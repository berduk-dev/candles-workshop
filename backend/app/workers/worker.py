"""Фоновый воркер: `arq app.workers.worker.WorkerSettings`."""

import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import get_settings
from app.db import SessionFactory, engine
from app.services import marking
from app.services.marking import MarkingClient


async def startup(ctx: dict) -> None:
    settings = get_settings()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ctx["marking"] = MarkingClient(settings.marking_base_url, timeout=settings.marking_timeout_seconds)


async def shutdown(ctx: dict) -> None:
    await ctx["marking"].aclose()
    await engine.dispose()


async def send_marking_report(ctx: dict, report_id: int) -> None:
    await marking.deliver(SessionFactory, report_id, ctx["marking"])


async def dispatch_marking(ctx: dict) -> None:
    """Каждые 5 секунд ставит в очередь отчёты, которым пора уйти. Источник правды — таблица в базе,
    поэтому при падении Redis или воркера отчёты не теряются."""
    async with SessionFactory() as session:
        ids = await marking.due_report_ids(session)
    for report_id in ids:
        # Один и тот же отчёт не встанет в очередь дважды.
        await ctx["redis"].enqueue_job("send_marking_report", report_id, _job_id=f"marking:{report_id}")


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    functions = [send_marking_report]  # noqa: RUF012
    cron_jobs = [cron(dispatch_marking, second=set(range(0, 60, 5)))]  # noqa: RUF012
    on_startup = startup
    on_shutdown = shutdown
    keep_result = 0  # иначе arq не даст поставить повтор с тем же _job_id
    max_tries = 1  # повторами управляет marking.deliver, а не arq
