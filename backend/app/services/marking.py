"""Отчёты в сервис маркировки (паттерн «outbox»).

1. complete() создаёт строку marking_reports в той же транзакции, что и завершение задания.
2. Воркер раз в несколько секунд находит отчёты, которым пора уйти, и вызывает deliver().
3. Неудача → следующая попытка через 5, 10, 20… секунд (не больше 10 минут), ключ идемпотентности тот же.
"""

import logging
import time
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clock import now
from app.errors import DomainError, NotFoundError
from app.models import Job, MarkingAttempt, MarkingReport, MarkingStatus, Recipe, User
from app.schemas.production import MarkingAttemptOut, MarkingOut
from app.services import audit
from app.services.permissions import Perm, require

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
BASE_DELAY = timedelta(seconds=5)
MAX_DELAY = timedelta(minutes=10)
# На время отправки отчёт «занят»: второй воркер его не возьмёт. Больше таймаута запроса с запасом.
LEASE = timedelta(seconds=30)
RETRYABLE_4XX = {408, 429}


class MarkingClient:
    def __init__(self, base_url: str, *, timeout: float, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport, trust_env=False
        )

    async def send(self, idempotency_key: str, payload: dict) -> httpx.Response:
        return await self._http.post("/reports", json=payload, headers={"Idempotency-Key": idempotency_key})

    async def aclose(self) -> None:
        await self._http.aclose()


def backoff(attempt: int) -> timedelta:
    """Пауза после попытки №attempt: 5 с, 10 с, 20 с… — каждый раз вдвое, но не больше 10 минут."""
    return min(BASE_DELAY * 2 ** (attempt - 1), MAX_DELAY)


async def create_report(session: AsyncSession, job: Job) -> MarkingReport:
    report = MarkingReport(
        job_id=job.id,
        idempotency_key=str(uuid.uuid4()),
        status=MarkingStatus.PENDING,
        attempts=0,
        next_attempt_at=now(),
    )
    session.add(report)
    await session.flush()
    return report


async def due_report_ids(session: AsyncSession) -> list[int]:
    rows = await session.scalars(
        select(MarkingReport.id)
        .where(MarkingReport.status == MarkingStatus.PENDING, MarkingReport.next_attempt_at <= now())
        .order_by(MarkingReport.next_attempt_at)
        .limit(50)
    )
    return list(rows)


async def deliver(sessions: async_sessionmaker[AsyncSession], report_id: int, client: MarkingClient) -> None:
    """Одна попытка отправки. Сам запрос идёт вне транзакции: блокировки в базе не ждут чужой сервис."""
    async with sessions() as session:
        report = await session.scalar(
            select(MarkingReport).where(MarkingReport.id == report_id).with_for_update()
        )
        if report is None or report.status != MarkingStatus.PENDING or report.next_attempt_at > now():
            return  # уже отправлен или его прямо сейчас отправляет другой воркер
        report.attempts += 1
        report.next_attempt_at = now() + LEASE
        attempt, key = report.attempts, report.idempotency_key
        job, recipe = (
            await session.execute(
                select(Job, Recipe).join(Recipe, Recipe.id == Job.recipe_id).where(Job.id == report.job_id)
            )
        ).one()
        payload = {
            "job_number": job.number,
            "product": recipe.name,
            "quantity": job.produced_qty,
            "completed_at": job.completed_at.isoformat(),
        }
        await session.commit()

    started = time.monotonic()
    http_status, error, external_id = None, None, None
    try:
        response = await client.send(key, payload)
        http_status = response.status_code
        if response.is_success:
            external_id = str(response.json().get("id"))
        else:
            error = f"Сервис ответил кодом {http_status}"
    except httpx.TimeoutException:
        error = "Сервис не ответил вовремя"
    except (httpx.HTTPError, ValueError) as exc:
        error = f"Ошибка связи: {type(exc).__name__}"
    duration_ms = int((time.monotonic() - started) * 1000)

    async with sessions() as session:
        report = await session.scalar(
            select(MarkingReport).where(MarkingReport.id == report_id).with_for_update()
        )
        session.add(
            MarkingAttempt(
                report_id=report_id,
                number=attempt,
                duration_ms=duration_ms,
                http_status=http_status,
                error=error,
            )
        )
        rejected = http_status is not None and 400 <= http_status < 500 and http_status not in RETRYABLE_4XX
        if error is None:
            report.status = MarkingStatus.SENT
            report.external_id = external_id
        elif rejected or attempt >= MAX_ATTEMPTS:
            report.status = MarkingStatus.FAILED
        else:
            report.next_attempt_at = now() + backoff(attempt)
        await session.commit()
    logger.info("Маркировка: отчёт %s, попытка %s — %s", report_id, attempt, error or "отправлен")


async def retry(session: AsyncSession, user: User, job_id: int) -> None:
    """Кнопка «Повторить» у руководителя: ключ идемпотентности прежний, поэтому дубля не будет."""
    require(user, Perm.MARKING_RETRY)
    report = await session.scalar(
        select(MarkingReport).where(MarkingReport.job_id == job_id).with_for_update()
    )
    if report is None:
        raise NotFoundError("У задания нет отчёта в сервис маркировки.")
    if report.status != MarkingStatus.FAILED:
        raise DomainError(f"Повтор не нужен: отчёт в статусе «{report.status.label}».")
    report.status = MarkingStatus.PENDING
    report.attempts = 0
    report.next_attempt_at = now()
    audit.record(session, user, entity="job", entity_id=job_id, action="marking_retry")
    await session.flush()


async def get_for_job(session: AsyncSession, job_id: int) -> MarkingOut | None:
    report = await session.scalar(select(MarkingReport).where(MarkingReport.job_id == job_id))
    if report is None:
        return None
    log = await session.scalars(
        select(MarkingAttempt).where(MarkingAttempt.report_id == report.id).order_by(MarkingAttempt.id)
    )
    return MarkingOut(
        status=report.status,
        status_label=report.status.label,
        attempts=report.attempts,
        next_attempt_at=report.next_attempt_at,
        log=[MarkingAttemptOut.model_validate(a, from_attributes=True) for a in log],
    )
