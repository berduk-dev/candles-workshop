"""Отправка отчётов в настоящий мок маркировки (mocks/marking), запущенный внутри процесса, без сети."""

import importlib.util
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update

from app.clock import now
from app.db import SessionFactory
from app.errors import PermissionDeniedError
from app.models import Job, JobStatus, MarkingAttempt, MarkingReport, MarkingStatus, Recipe, Role
from app.services import marking
from app.services.marking import MarkingClient

MOCK_PATH = Path(__file__).resolve().parents[2] / "mocks" / "marking" / "app.py"


@pytest.fixture
def mock():
    spec = importlib.util.spec_from_file_location("marking_mock", MOCK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.FAIL_RATE = module.SLOW_RATE = 0.0
    return module


@pytest.fixture
async def report_id(session, users) -> int:
    recipe = Recipe(name="Свеча «Лаванда»", items=[])
    session.add(recipe)
    await session.flush()
    job = Job(
        number="З-000001",
        recipe_id=recipe.id,
        qty=10,
        produced_qty=10,
        planned_date=now().date(),
        status=JobStatus.COMPLETED,
        created_by=users[Role.TECHNOLOGIST].id,
        completed_at=now(),
    )
    session.add(job)
    await session.flush()
    report = await marking.create_report(session, job)
    await session.commit()
    return report.id


async def state(report_id: int) -> MarkingReport:
    async with SessionFactory() as s:
        return await s.get(MarkingReport, report_id)


async def attempts(report_id: int) -> list[MarkingAttempt]:
    async with SessionFactory() as s:
        return list(await s.scalars(select(MarkingAttempt).order_by(MarkingAttempt.id)))


async def make_due(report_id: int) -> None:
    async with SessionFactory() as s:
        await s.execute(
            update(MarkingReport)
            .where(MarkingReport.id == report_id)
            .values(next_attempt_at=now() - timedelta(seconds=1))
        )
        await s.commit()


class LostReply(httpx.AsyncBaseTransport):
    """Сервис принял отчёт, но ответ «потерялся»: клиент видит таймаут."""

    def __init__(self, app):
        self.inner = httpx.ASGITransport(app=app)
        self.lose = 1

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if self.lose:
            self.lose -= 1
            raise httpx.ReadTimeout("нет ответа", request=request)
        return response


async def test_resend_after_lost_reply_makes_no_duplicate(mock, report_id):
    client = MarkingClient("http://marking", timeout=5, transport=LostReply(mock.app))

    await marking.deliver(SessionFactory, report_id, client)
    assert (await state(report_id)).status == MarkingStatus.PENDING
    assert len(mock.reports) == 1  # сервис отчёт принял, но мы об этом не знаем

    await make_due(report_id)
    await marking.deliver(SessionFactory, report_id, client)

    report = await state(report_id)
    assert report.status == MarkingStatus.SENT
    assert len(mock.reports) == 1  # дубля нет
    log = await attempts(report_id)
    assert [(a.number, a.error) for a in log] == [(1, "Сервис не ответил вовремя"), (2, None)]


async def test_sent_report_is_not_sent_again(mock, report_id):
    client = MarkingClient("http://marking", timeout=5, transport=httpx.ASGITransport(app=mock.app))
    await marking.deliver(SessionFactory, report_id, client)
    await make_due(report_id)
    await marking.deliver(SessionFactory, report_id, client)
    assert len(await attempts(report_id)) == 1


async def test_500_is_retried_with_growing_pause(mock, report_id):
    mock.FAIL_RATE = 1.0
    client = MarkingClient("http://marking", timeout=5, transport=httpx.ASGITransport(app=mock.app))
    pauses = []
    for _ in range(3):
        await make_due(report_id)
        before = now()
        await marking.deliver(SessionFactory, report_id, client)
        pauses.append(round(((await state(report_id)).next_attempt_at - before).total_seconds()))
    assert pauses == [5, 10, 20]
    assert [a.http_status for a in await attempts(report_id)] == [500, 500, 500]
    # До назначенного времени повторная отправка ничего не делает.
    await marking.deliver(SessionFactory, report_id, client)
    assert len(await attempts(report_id)) == 3


async def test_gives_up_after_limit_and_manager_can_retry(mock, report_id, session, users, monkeypatch):
    monkeypatch.setattr(marking, "MAX_ATTEMPTS", 2)
    mock.FAIL_RATE = 1.0
    client = MarkingClient("http://marking", timeout=5, transport=httpx.ASGITransport(app=mock.app))
    for _ in range(2):
        await make_due(report_id)
        await marking.deliver(SessionFactory, report_id, client)
    failed = await state(report_id)
    assert failed.status == MarkingStatus.FAILED

    with pytest.raises(PermissionDeniedError):
        await marking.retry(session, users[Role.TECHNOLOGIST], failed.job_id)
    await marking.retry(session, users[Role.MANAGER], failed.job_id)
    await session.commit()
    mock.FAIL_RATE = 0.0
    await marking.deliver(SessionFactory, report_id, client)
    done = await state(report_id)
    assert done.status == MarkingStatus.SENT
    assert done.idempotency_key == failed.idempotency_key


def test_backoff_is_capped():
    assert marking.backoff(1) == timedelta(seconds=5)
    assert marking.backoff(20) == timedelta(minutes=10)
