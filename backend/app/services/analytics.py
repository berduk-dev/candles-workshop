"""Отчёты только на чтение: дефицит за период, себестоимость, расход. Ими пользуются и интерфейс,
и инструменты ассистента."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import today
from app.config import get_settings
from app.errors import DomainError, InvalidInputError
from app.formatting import q_money, q_rub
from app.models import (
    Batch,
    BatchStatus,
    Currency,
    Job,
    JobItem,
    JobStatus,
    Material,
    MovementKind,
    Recipe,
    Reservation,
    StockMovement,
    User,
)
from app.schemas.production import (
    ConsumptionRow,
    CostRow,
    DeficitOut,
    DeficitRow,
    DeficitTotal,
    JobCostOut,
    JobIn,
    RecipeCostOut,
    RecipeCostRow,
    SpendingOut,
    SpendingPart,
    SpendingRow,
)
from app.services import jobs, recipes
from app.services.fx import FxClient, FxQuote, get_rate
from app.services.permissions import Perm, require
from app.services.recipes import material_out
from app.services.reservations import ZERO, allocate_fifo

CONSUMPTION_KINDS = (MovementKind.ISSUE, MovementKind.OVERUSE, MovementKind.RETURN)


def _check_period(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise InvalidInputError("Начало периода позже конца.")
    if date_to - date_from > timedelta(days=800):
        raise InvalidInputError("Период слишком длинный: не больше двух лет.")


async def deficit(session: AsyncSession, user: User, date_from: date, date_to: date) -> DeficitOut:
    """Чего не хватает заданиям «Запланировано» с датой в периоде."""
    require(user, Perm.VIEW_STOCK)
    _check_period(date_from, date_to)
    rows = (
        await session.execute(
            select(Job, Recipe, JobItem, Material)
            .join(Recipe, Recipe.id == Job.recipe_id)
            .join(JobItem, JobItem.job_id == Job.id)
            .join(Material, Material.id == JobItem.material_id)
            .where(Job.status == JobStatus.PLANNED, Job.planned_date.between(date_from, date_to))
            .order_by(Job.planned_date, Job.id, Material.code)
        )
    ).all()
    job_ids = {job.id for job, *_ in rows}
    reserved = await jobs.reserved_by_material(session, job_ids)
    result: list[DeficitRow] = []
    totals: dict[int, Decimal] = defaultdict(lambda: ZERO)
    materials: dict[int, Material] = {}
    for job, recipe, item, material in rows:
        in_reserve = reserved.get((job.id, material.id), ZERO)
        missing = item.qty_required - in_reserve
        if missing <= 0:
            continue
        materials[material.id] = material
        totals[material.id] += missing
        result.append(
            DeficitRow(
                job_id=job.id,
                job_number=job.number,
                recipe_name=recipe.name,
                planned_date=job.planned_date,
                material=material_out(material),
                qty_required=item.qty_required,
                qty_reserved=in_reserve,
                qty_deficit=missing,
            )
        )
    free = await jobs.free_by_material(session, set(totals))
    return DeficitOut(
        date_from=date_from,
        date_to=date_to,
        planned_jobs=len(job_ids),
        rows=result,
        totals=[
            DeficitTotal(material=material_out(materials[mid]), qty_deficit=qty, qty_free=free[mid])
            for mid, qty in totals.items()
        ],
    )


# ---------- себестоимость ----------


async def job_cost(
    session: AsyncSession, user: User, job_id: int, currency: Currency, fx: FxClient
) -> JobCostOut:
    """Себестоимость по партиям: запущенные и завершённые — по фактическим движениям,
    запланированные — по резерву, черновик — по пробной раскладке FIFO."""
    require(user, Perm.JOB_COST_VIEW)
    row = (
        await session.execute(
            select(Job, Recipe).join(Recipe, Recipe.id == Job.recipe_id).where(Job.id == job_id)
        )
    ).one()
    job, recipe = row
    if job.status == JobStatus.CANCELLED:
        raise DomainError(f"Задание {job.number} отменено — себестоимости нет.")

    if job.status in (JobStatus.IN_PROGRESS, JobStatus.COMPLETED):
        basis = "фактический расход" if job.status == JobStatus.COMPLETED else "выдано в цех"
        used = (
            await session.execute(
                select(Batch, func.sum(-StockMovement.qty))
                .join(StockMovement, StockMovement.batch_id == Batch.id)
                .where(StockMovement.job_id == job.id)
                .group_by(Batch.id)
            )
        ).all()
        unpriced: dict[int, Decimal] = {}
    elif job.status == JobStatus.PLANNED:
        basis = "текущий резерв"
        used = (
            await session.execute(
                select(Batch, Reservation.qty)
                .join(Reservation, Reservation.batch_id == Batch.id)
                .where(Reservation.job_id == job.id)
            )
        ).all()
        card = await jobs.get_card(session, user, job.id)
        unpriced = {c.material.id: c.qty_deficit for c in card.components if c.qty_deficit > 0}
    else:
        basis = "оценка черновика по партиям, которые сейчас свободны"
        preview = await jobs.preview_plan(
            session,
            user,
            JobIn(recipe_id=job.recipe_id, qty=job.qty, planned_date=max(job.planned_date, today())),
        )
        batches = list(
            await session.scalars(
                select(Batch).where(Batch.material_id.in_([i.material.id for i in preview.items]))
            )
        )
        used = [pair for i in preview.items for pair in allocate_fifo(batches, i.material.id, i.qty_required)]
        unpriced = {i.material.id: i.qty_deficit for i in preview.items if i.qty_deficit > 0}

    quote = await get_rate(session, fx, currency, today())
    qty: dict[int, Decimal] = defaultdict(lambda: ZERO)
    cost: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for batch, amount in used:
        qty[batch.material_id] += amount
        cost[batch.material_id] += amount * batch.unit_cost_rub
    ids = set(qty) | set(unpriced)
    materials = {m.id: m for m in await session.scalars(select(Material).where(Material.id.in_(ids)))}
    total = sum(cost.values(), ZERO)
    # Завершённое задание делим на выпущенное количество, остальные — на плановое.
    units = job.produced_qty if job.produced_qty is not None else job.qty
    return JobCostOut(
        job_number=job.number,
        recipe_name=recipe.name,
        status_label=job.status.label,
        basis=basis,
        is_complete=not unpriced,
        units=units,
        currency=currency,
        fx_rate=quote.rate,
        fx_rate_date=quote.rate_date,
        total=q_rub(total / quote.rate),
        per_unit=q_rub(total / quote.rate / units) if units and not unpriced else None,
        rows=[
            CostRow(
                material=material_out(materials[mid]),
                qty=qty[mid],
                cost=q_rub(cost[mid] / quote.rate),
                qty_unpriced=unpriced.get(mid, ZERO),
            )
            for mid in sorted(ids, key=lambda i: materials[i].code)
        ],
    )


async def recipe_cost(
    session: AsyncSession, user: User, recipe_id: int, currency: Currency, fx: FxClient
) -> RecipeCostOut:
    """Себестоимость одного изделия: норма × средневзвешенная цена свободного остатка."""
    require(user, Perm.JOB_COST_VIEW)
    recipe = recipes.to_recipe_out(await recipes.load(session, recipe_id))
    quote: FxQuote = await get_rate(session, fx, currency, today())
    free_qty = Batch.qty_on_hand - Batch.qty_reserved
    averages = dict(
        (
            await session.execute(
                select(Batch.material_id, func.sum(free_qty * Batch.unit_cost_rub) / func.sum(free_qty))
                .where(Batch.status == BatchStatus.ACCEPTED, free_qty > 0)
                .group_by(Batch.material_id)
            )
        )
        .tuples()
        .all()
    )
    rows = []
    total = ZERO
    for item in recipe.items:
        avg = averages.get(item.material.id)
        cost = item.qty_per_unit * avg if avg is not None else None
        total += cost or ZERO
        rows.append(
            RecipeCostRow(
                material=item.material,
                qty_per_unit=item.qty_per_unit,
                avg_unit_cost=q_money(avg / quote.rate) if avg is not None else None,
                cost_per_unit=q_money(cost / quote.rate) if cost is not None else None,
            )
        )
    return RecipeCostOut(
        recipe_name=recipe.name,
        currency=currency,
        fx_rate=quote.rate,
        fx_rate_date=quote.rate_date,
        per_unit=q_money(total / quote.rate),
        is_complete=all(r.cost_per_unit is not None for r in rows),
        rows=rows,
    )


# ---------- расход ----------


async def spending(
    session: AsyncSession,
    user: User,
    date_from: date,
    date_to: date,
    material_ids: list[int] | None = None,
) -> SpendingOut:
    """Сколько денег ушло за период — два разных ответа: закупки и расход в производство.
    Закуплено — стоимость всех поступлений за период по цене и курсу из партий, включая партии
    на проверке и брак. Израсходовано — выдача в цех + перерасход − возврат экономии, по себестоимости
    партий. Складывать их нельзя: купленное частью ещё лежит на складе."""
    require(user, Perm.VIEW_STOCK)
    _check_period(date_from, date_to)
    query = (
        select(Material, func.sum(Batch.qty_initial), func.sum(Batch.qty_initial * Batch.unit_cost_rub))
        .join(Material, Material.id == Batch.material_id)
        .where(Batch.received_at.between(date_from, date_to))
        .group_by(Material.id)
        .order_by(Material.code)
    )
    if material_ids is not None:
        query = query.where(Material.id.in_(material_ids))
    bought = [
        SpendingRow(material=material_out(m), qty=qty, cost_rub=q_rub(cost))
        for m, qty, cost in await session.execute(query)
    ]
    used: dict[str, SpendingRow] = {}
    for row in await consumption(session, user, date_from, date_to, material_ids):
        current = used.get(row.material.code)
        used[row.material.code] = SpendingRow(
            material=row.material,
            qty=row.qty + (current.qty if current else ZERO),
            cost_rub=row.cost_rub + (current.cost_rub if current else ZERO),
        )
    consumed = sorted(used.values(), key=lambda r: r.material.code)
    return SpendingOut(
        date_from=date_from,
        date_to=date_to,
        purchased=SpendingPart(total_rub=sum((r.cost_rub for r in bought), ZERO), by_material=bought),
        consumed=SpendingPart(total_rub=sum((r.cost_rub for r in consumed), ZERO), by_material=consumed),
    )


async def consumption(
    session: AsyncSession,
    user: User,
    date_from: date,
    date_to: date,
    material_ids: list[int] | None = None,
) -> list[ConsumptionRow]:
    """Расход материалов в производство по дням (выдача + перерасход − возврат)."""
    require(user, Perm.VIEW_STOCK)
    _check_period(date_from, date_to)
    tz = get_settings().tz
    day = func.date(func.timezone(get_settings().timezone, StockMovement.created_at)).label("day")
    query = (
        select(
            day,
            Material,
            func.sum(-StockMovement.qty),
            func.sum(-StockMovement.qty * Batch.unit_cost_rub),
        )
        .join(Batch, Batch.id == StockMovement.batch_id)
        .join(Material, Material.id == Batch.material_id)
        .where(
            StockMovement.kind.in_(CONSUMPTION_KINDS),
            StockMovement.created_at >= datetime.combine(date_from, time.min, tz),
            StockMovement.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tz),
        )
        .group_by(day, Material.id)
        .order_by(day, Material.code)
    )
    if material_ids is not None:
        query = query.where(Material.id.in_(material_ids))
    return [
        ConsumptionRow(day=d, material=material_out(m), qty=qty, cost_rub=q_rub(cost))
        for d, m, qty, cost in await session.execute(query)
    ]
