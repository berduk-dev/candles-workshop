"""Эталонные числа: считаются по базе теми же сервисами, что и кнопки в интерфейсе."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import today
from app.config import get_settings
from app.models import Batch, Currency, Job, JobStatus, Receipt, Supplier, User
from app.services import analytics, batches, jobs, materials, recipes
from app.services.fx import FxClient


async def compute(session: AsyncSession, user: User, fx: FxClient) -> dict[str, Any]:
    soy = (await materials.find(session, user, "WAX-SOY"))[0]
    monday = today() - timedelta(days=today().weekday())
    deficit = await analytics.deficit(session, user, monday, monday + timedelta(days=6))
    sandal = next(t for t in deficit.totals if t.material.code == "FR-SAN")
    batch1 = await batches.find_by_number(session, user, "П-000001")
    job4 = await jobs.find_by_number(session, user, "З-000004")
    cost4 = await analytics.job_cost(session, user, job4.id, Currency.RUB, fx)
    job12 = await jobs.find_by_number(session, user, "З-000012")
    cost12 = await analytics.job_cost(session, user, job12.id, Currency.EUR, fx)
    lavender = await recipes.find_by_name(session, user, "Лаванда")
    recipe_cost = await analytics.recipe_cost(session, user, lavender.id, Currency.RUB, fx)
    wax = next(r for r in recipe_cost.rows if r.material.code == "WAX-SOY")
    vosk = await session.scalar(
        select(func.count(Batch.id))
        .join(Receipt, Receipt.id == Batch.receipt_id)
        .join(Supplier, Supplier.id == Receipt.supplier_id)
        .where(Supplier.norm_key == "восковой двор")
    )
    job10 = await jobs.find_by_number(session, user, "З-000010")
    unit_costs = {
        n: (await analytics.job_cost(session, user, j.id, Currency.RUB, fx)).per_unit
        for n, j in (("job10", job10), ("job12", job12))
    }
    lavender_soy = next(
        i.qty_per_unit
        for i in recipes.to_recipe_out(await recipes.load(session, lavender.id)).items
        if i.material.code == "WAX-SOY"
    )
    free = {m.code: m.qty_free for m in await materials.list_with_stock(session, user)}
    lavender_scent = (await materials.find(session, user, "FR-LAV"))[0]
    sandal_recipe = await recipes.find_by_name(session, user, "Сандал")
    short_300 = {
        i.material.code: 300 * i.qty_per_unit - free[i.material.code]
        for i in recipes.to_recipe_out(await recipes.load(session, sandal_recipe.id)).items
    }
    month_start = today().replace(day=1)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    tz = get_settings().tz
    produced = await session.scalar(
        select(func.coalesce(func.sum(Job.produced_qty), 0)).where(
            Job.completed_at >= datetime.combine(prev_start, time.min, tz),
            Job.completed_at < datetime.combine(month_start, time.min, tz),
        )
    )
    overdue = await session.scalars(
        select(Job.number).where(
            Job.status.in_([JobStatus.PLANNED, JobStatus.IN_PROGRESS]), Job.planned_date < today()
        )
    )
    return {
        "soy_on_hand": soy.qty_on_hand,
        "soy_free": soy.qty_free,
        "sandal_deficit": sandal.qty_deficit,
        "batch1_initial": batch1.qty_initial,
        "job4_total": cost4.total,
        "job4_unit": cost4.per_unit,
        "job12_total_eur": cost12.total,
        "lavender_unit": recipe_cost.per_unit,
        "lavender_unit_15": recipe_cost.per_unit + wax.cost_per_unit * Decimal("0.15"),
        "vosk_batches": Decimal(vosk),
        "soy_candles": Decimal(int(soy.qty_free / lavender_soy)),
        "job10_unit": unit_costs["job10"],
        "job12_unit": unit_costs["job12"],
        "lavender_scent": lavender_scent.qty_on_hand,
        "sandal_short_300": short_300["FR-SAN"],
        "bee_short_300": short_300["WAX-BEE"],
        "produced_prev_month": Decimal(produced),
        # Тексты, которые обязаны быть в ответе (expect_texts).
        "overdue_jobs": list(overdue),
    }
