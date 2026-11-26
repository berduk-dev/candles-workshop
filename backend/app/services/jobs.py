"""Производственные задания: жизненный цикл, резервы, светофор.

Черновик → Запланировано → В работе → Завершено; Отменено — только из первых двух.
Каждое изменение начинается с блокировки строки задания, поэтому два одновременных запроса
(двойной клик, кнопка и ассистент) выполняются по очереди.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import now, today
from app.errors import DomainError, InvalidInputError, NotFoundError
from app.formatting import fmt_date, fmt_qty
from app.models import (
    Batch,
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
from app.models.production import job_number_seq
from app.schemas.common import Page
from app.schemas.inventory import AuditEntryOut
from app.schemas.production import (
    ComponentOut,
    JobCardOut,
    JobCompleteIn,
    JobIn,
    JobOut,
    Light,
    PlanPreview,
    PlanPreviewItem,
    ReservationOut,
)
from app.services import audit, marking, recipes
from app.services.permissions import Perm, require
from app.services.recipes import material_out
from app.services.reservations import ZERO, allocate_fifo, free_qty, lock_batches
from app.services.sorting import order_by
from app.services.text import like_pattern, normalize_doc_number

# Незавершённые задания, которые могут опаздывать. Черновик ещё не план, поэтому сюда не входит.
OVERDUE_STATUSES = (JobStatus.PLANNED, JobStatus.IN_PROGRESS)
# Статусы сортируются по ходу работы, а не по алфавиту.
_STATUS_ORDER = case({s: i for i, s in enumerate(JobStatus)}, value=Job.status, else_=len(JobStatus))
JOB_SORT_COLUMNS = {
    "number": Job.number,
    "recipe": Recipe.name,
    "qty": Job.qty,
    "status": _STATUS_ORDER,
    "planned_date": Job.planned_date,
}
JOB_AUDIT_FIELDS = ["number", "recipe_id", "qty", "planned_date", "status", "produced_qty"]


# ---------- загрузка ----------


async def _lock_job(session: AsyncSession, job_id: int) -> Job:
    job = await session.scalar(
        select(Job).where(Job.id == job_id).with_for_update().execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError("Задание не найдено.")
    return job


def _expect(job: Job, *allowed: JobStatus, action: str) -> None:
    if job.status not in allowed:
        raise DomainError(f"Нельзя {action}: задание {job.number} в статусе «{job.status.label}».")


async def _items(session: AsyncSession, job_id: int) -> list[JobItem]:
    return list(
        await session.scalars(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.material_id))
    )


async def _reservations(session: AsyncSession, job_id: int) -> list[Reservation]:
    return list(await session.scalars(select(Reservation).where(Reservation.job_id == job_id)))


async def _materials(session: AsyncSession, ids: set[int]) -> dict[int, Material]:
    return {m.id: m for m in await session.scalars(select(Material).where(Material.id.in_(ids)))}


def _check_planned_date(planned_date: date) -> None:
    if planned_date < today():
        raise InvalidInputError(f"Дата {fmt_date(planned_date)} уже прошла.")


def _record(session: AsyncSession, user: User, job: Job, action: str, before: dict | None, **extra) -> None:
    audit.record(
        session,
        user,
        entity="job",
        entity_id=job.id,
        action=action,
        before=before,
        after={**audit.snapshot(job, JOB_AUDIT_FIELDS), **extra},
    )


# ---------- черновик ----------


async def create_draft(session: AsyncSession, user: User, data: JobIn) -> Job:
    require(user, Perm.JOB_MANAGE)
    _check_planned_date(data.planned_date)
    await recipes.load(session, data.recipe_id)
    number = await session.scalar(select(job_number_seq.next_value()))
    job = Job(
        number=f"З-{number:06d}",
        recipe_id=data.recipe_id,
        qty=data.qty,
        planned_date=data.planned_date,
        status=JobStatus.DRAFT,
        created_by=user.id,
    )
    session.add(job)
    await session.flush()
    _record(session, user, job, "created", None)
    return job


async def update_draft(session: AsyncSession, user: User, job_id: int, data: JobIn) -> Job:
    require(user, Perm.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.DRAFT, action="изменить")
    _check_planned_date(data.planned_date)
    await recipes.load(session, data.recipe_id)
    before = audit.snapshot(job, JOB_AUDIT_FIELDS)
    job.recipe_id, job.qty, job.planned_date = data.recipe_id, data.qty, data.planned_date
    await session.flush()
    _record(session, user, job, "updated", before)
    return job


# ---------- планирование и резервы ----------


async def _reserve_missing(session: AsyncSession, job: Job) -> dict[str, str]:
    """Добирает резерв до потребности. Возвращает {код материала: дефицит} — пусто, если всего хватило."""
    items = await _items(session, job.id)
    batches = await lock_batches(session, {i.material_id for i in items})
    existing = {r.batch_id: r for r in await _reservations(session, job.id)}
    by_batch = {b.id: b for b in batches}
    reserved: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in existing.values():
        reserved[by_batch[r.batch_id].material_id] += r.qty

    deficit: dict[int, Decimal] = {}
    for item in items:
        need = item.qty_required - reserved[item.material_id]
        for batch, take in allocate_fifo(batches, item.material_id, need):
            batch.qty_reserved += take
            if batch.id in existing:
                existing[batch.id].qty += take
            else:
                session.add(Reservation(job_id=job.id, batch_id=batch.id, qty=take))
            need -= take
        if need > 0:
            deficit[item.material_id] = need
    await session.flush()
    materials = await _materials(session, set(deficit))
    return {materials[mid].code: str(qty) for mid, qty in deficit.items()}


async def plan(session: AsyncSession, user: User, job_id: int) -> Job:
    """Черновик → Запланировано: потребность фиксируется по рецепту, материалы резервируются по FIFO.
    Нехватка не мешает запланировать — остаётся дефицит, с которым нельзя запустить."""
    require(user, Perm.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.DRAFT, action="запланировать")
    recipe = await recipes.load(session, job.recipe_id)
    before = audit.snapshot(job, JOB_AUDIT_FIELDS)
    for item in recipe.items:
        session.add(
            JobItem(job_id=job.id, material_id=item.material_id, qty_required=item.qty_per_unit * job.qty)
        )
    job.status = JobStatus.PLANNED
    await session.flush()
    deficit = await _reserve_missing(session, job)
    _record(session, user, job, "planned", before, deficit=deficit)
    return job


async def reserve_more(session: AsyncSession, user: User, job_id: int) -> Job:
    """Дозарезервировать: добрать недостающее после новых поступлений."""
    require(user, Perm.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.PLANNED, action="дозарезервировать")
    deficit = await _reserve_missing(session, job)
    _record(session, user, job, "reserved_more", None, deficit=deficit)
    return job


async def cancel(session: AsyncSession, user: User, job_id: int) -> Job:
    require(user, Perm.JOB_CANCEL)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.DRAFT, JobStatus.PLANNED, action="отменить")
    before = audit.snapshot(job, JOB_AUDIT_FIELDS)
    reservations = await _reservations(session, job.id)
    items = await _items(session, job.id)
    batches = {b.id: b for b in await lock_batches(session, {i.material_id for i in items})}
    for r in reservations:
        batches[r.batch_id].qty_reserved -= r.qty
        await session.delete(r)
    job.status = JobStatus.CANCELLED
    await session.flush()
    _record(session, user, job, "cancelled", before)
    return job


# ---------- запуск и завершение ----------


async def start(session: AsyncSession, user: User, job_id: int) -> Job:
    """Запланировано → В работе: резерв списывается со склада. С дефицитом запустить нельзя."""
    require(user, Perm.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.PLANNED, action="запустить")
    items = await _items(session, job.id)
    batches = {b.id: b for b in await lock_batches(session, {i.material_id for i in items})}
    reservations = await _reservations(session, job.id)

    reserved: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for r in reservations:
        reserved[batches[r.batch_id].material_id] += r.qty
    shortage = {
        i.material_id: i.qty_required - reserved[i.material_id]
        for i in items
        if reserved[i.material_id] < i.qty_required
    }
    if shortage:
        raise DomainError(f"Нельзя запустить {job.number}. {await _shortage_text(session, shortage)}")

    before = audit.snapshot(job, JOB_AUDIT_FIELDS)
    for r in reservations:
        batch = batches[r.batch_id]
        batch.qty_reserved -= r.qty
        batch.qty_on_hand -= r.qty
        session.add(
            StockMovement(
                batch_id=batch.id, kind=MovementKind.ISSUE, qty=-r.qty, job_id=job.id, user_id=user.id
            )
        )
        await session.delete(r)
    job.status = JobStatus.IN_PROGRESS
    job.started_at = now()
    await session.flush()
    _record(session, user, job, "started", before)
    return job


async def complete(session: AsyncSession, user: User, job_id: int, data: JobCompleteIn) -> Job:
    """В работе → Завершено. Перерасход досписывается по FIFO из свободного остатка,
    экономия возвращается в те партии, из которых выдавали (начиная с самой новой)."""
    require(user, Perm.JOB_MANAGE)
    job = await _lock_job(session, job_id)
    _expect(job, JobStatus.IN_PROGRESS, action="завершить")
    items = await _items(session, job.id)
    materials = await _materials(session, {i.material_id for i in items})
    actual = {a.material_code: a.qty for a in data.actual}
    if unknown := sorted(actual.keys() - {m.code for m in materials.values()}):
        raise InvalidInputError(f"Материалы не входят в задание: {', '.join(unknown)}.")

    batches = await lock_batches(session, materials)
    by_id = {b.id: b for b in batches}
    issued = await _issued_by_batch(session, job.id)
    shortage: dict[int, Decimal] = {}
    for item in items:
        item.qty_actual = actual.get(materials[item.material_id].code, item.qty_required)
        diff = item.qty_actual - item.qty_required  # при запуске выдана ровно потребность
        if diff > 0:
            for batch, take in allocate_fifo(batches, item.material_id, diff):
                batch.qty_on_hand -= take
                diff -= take
                _move(session, batch, MovementKind.OVERUSE, -take, job, user)
            if diff > 0:
                shortage[item.material_id] = diff
        elif diff < 0:
            to_return = -diff
            newest_first = sorted(
                (by_id[bid] for bid in issued if by_id[bid].material_id == item.material_id),
                key=lambda b: (b.received_at, b.id),
                reverse=True,
            )
            for batch in newest_first:
                back = min(issued[batch.id], to_return)
                if back > 0:
                    batch.qty_on_hand += back
                    to_return -= back
                    _move(session, batch, MovementKind.RETURN, back, job, user)
    if shortage:
        raise DomainError(f"Нельзя досписать перерасход. {await _shortage_text(session, shortage)}")

    before = audit.snapshot(job, JOB_AUDIT_FIELDS)
    job.status = JobStatus.COMPLETED
    job.produced_qty = data.produced_qty
    job.completed_at = now()
    await session.flush()
    _record(
        session,
        user,
        job,
        "completed",
        before,
        actual={materials[i.material_id].code: str(i.qty_actual) for i in items},
    )
    # Отчёт создаётся в этой же транзакции, а отправляется потом в фоне: завершение никого не ждёт.
    await marking.create_report(session, job)
    return job


def _move(
    session: AsyncSession, batch: Batch, kind: MovementKind, qty: Decimal, job: Job, user: User
) -> None:
    session.add(StockMovement(batch_id=batch.id, kind=kind, qty=qty, job_id=job.id, user_id=user.id))


async def _issued_by_batch(session: AsyncSession, job_id: int) -> dict[int, Decimal]:
    rows = await session.execute(
        select(StockMovement.batch_id, func.sum(-StockMovement.qty))
        .where(StockMovement.job_id == job_id, StockMovement.kind == MovementKind.ISSUE)
        .group_by(StockMovement.batch_id)
    )
    return dict(rows.tuples().all())


async def _shortage_text(session: AsyncSession, shortage: dict[int, Decimal]) -> str:
    """«Не хватает: 2,5 кг — Воск соевый; 10 шт — Этикетка.»"""
    materials = await _materials(session, set(shortage))
    parts = [f"{fmt_qty(qty, materials[mid].unit)} — {materials[mid].name}" for mid, qty in shortage.items()]
    return "Не хватает: " + "; ".join(parts) + "."


# ---------- просмотр ----------


async def preview_plan(session: AsyncSession, user: User, data: JobIn) -> PlanPreview:
    """Пробный расчёт без записи: что зарезервируется и чего не хватит, если запланировать сейчас."""
    require(user, Perm.JOB_MANAGE)
    _check_planned_date(data.planned_date)
    preview = await availability(session, user, data.recipe_id, data.qty)
    return preview.model_copy(update={"planned_date": data.planned_date})


async def availability(session: AsyncSession, user: User, recipe_id: int, qty: int) -> PlanPreview:
    """Хватит ли свободных материалов на qty изделий: по каждому — нужно, есть, не хватит.
    Только чтение, поэтому доступно всем, кто видит остатки."""
    require(user, Perm.VIEW_STOCK)
    data = JobIn(recipe_id=recipe_id, qty=qty, planned_date=today())
    recipe = recipes.to_recipe_out(await recipes.load(session, data.recipe_id))
    free = await free_by_material(session, {i.material.id for i in recipe.items})
    items = []
    for item in recipe.items:
        need = item.qty_per_unit * data.qty
        available = min(free.get(item.material.id, ZERO), need)
        items.append(
            PlanPreviewItem(
                material=item.material,
                qty_required=need,
                qty_available=available,
                qty_deficit=need - available,
            )
        )
    return PlanPreview(
        recipe=recipe,
        qty=data.qty,
        planned_date=data.planned_date,
        items=items,
        has_deficit=any(i.qty_deficit > 0 for i in items),
    )


async def free_by_material(session: AsyncSession, material_ids: set[int]) -> dict[int, Decimal]:
    result: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for batch in await session.scalars(select(Batch).where(Batch.material_id.in_(material_ids))):
        result[batch.material_id] += free_qty(batch)
    return result


async def reserved_by_material(session: AsyncSession, job_ids: set[int]) -> dict[tuple[int, int], Decimal]:
    """{(задание, материал): зарезервировано}"""
    rows = await session.execute(
        select(Reservation.job_id, Batch.material_id, func.sum(Reservation.qty))
        .join(Batch, Batch.id == Reservation.batch_id)
        .where(Reservation.job_id.in_(job_ids))
        .group_by(Reservation.job_id, Batch.material_id)
    )
    return {(job_id, material_id): qty for job_id, material_id, qty in rows}


async def jobs_with_deficit(session: AsyncSession, job_ids: set[int]) -> set[int]:
    reserved = await reserved_by_material(session, job_ids)
    items = await session.scalars(
        select(JobItem)
        .join(Job, Job.id == JobItem.job_id)
        .where(Job.id.in_(job_ids), Job.status == JobStatus.PLANNED)
    )
    return {i.job_id for i in items if reserved.get((i.job_id, i.material_id), ZERO) < i.qty_required}


def _to_job_out(job: Job, recipe: Recipe, has_deficit: bool) -> JobOut:
    return JobOut(
        id=job.id,
        number=job.number,
        recipe_id=recipe.id,
        recipe_name=recipe.name,
        qty=job.qty,
        planned_date=job.planned_date,
        status=job.status,
        status_label=job.status.label,
        produced_qty=job.produced_qty,
        has_deficit=has_deficit,
    )


async def list_jobs(
    session: AsyncSession,
    user: User,
    *,
    status: list[JobStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: str | None = None,
    overdue: bool = False,
    sort: str = "-planned_date",
    page: int = 1,
    page_size: int = 20,
) -> Page[JobOut]:
    """overdue — просроченные: плановая дата прошла, а задание запланировано или в работе."""
    require(user, Perm.VIEW_STOCK)
    page, page_size = max(page, 1), max(1, min(page_size, 100))
    query = select(Job, Recipe).join(Recipe, Recipe.id == Job.recipe_id)
    if status:
        query = query.where(Job.status.in_(status))
    if date_from:
        query = query.where(Job.planned_date >= date_from)
    if date_to:
        query = query.where(Job.planned_date <= date_to)
    if overdue:
        query = query.where(Job.status.in_(OVERDUE_STATUSES), Job.planned_date < today())
    if search:
        pattern = like_pattern(search)
        query = query.where(Job.number.ilike(pattern, escape="\\") | Recipe.name.ilike(pattern, escape="\\"))
    total = await session.scalar(select(func.count()).select_from(query.with_only_columns(Job.id).subquery()))
    rows = (
        await session.execute(
            query.order_by(*order_by(sort, JOB_SORT_COLUMNS, Job.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    deficit = await jobs_with_deficit(session, {job.id for job, _ in rows})
    return Page[JobOut](
        items=[_to_job_out(job, recipe, job.id in deficit) for job, recipe in rows],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


async def numbers_by_id(session: AsyncSession, job_ids: set[int]) -> dict[int, str]:
    rows = await session.execute(select(Job.id, Job.number).where(Job.id.in_(job_ids)))
    return dict(rows.tuples().all())


async def find_by_number(session: AsyncSession, user: User, number: str) -> Job:
    require(user, Perm.VIEW_STOCK)
    canonical = normalize_doc_number(number)
    job = await session.scalar(select(Job).where(Job.number == canonical))
    if job is None:
        raise NotFoundError(f"Задание {canonical} не найдено.")
    return job


def _light(required: Decimal, reserved: Decimal) -> Light:
    if reserved >= required:
        return "green"
    return "yellow" if reserved > 0 else "red"


async def get_card(session: AsyncSession, user: User, job_id: int) -> JobCardOut:
    require(user, Perm.VIEW_STOCK)
    row = (
        await session.execute(
            select(Job, Recipe).join(Recipe, Recipe.id == Job.recipe_id).where(Job.id == job_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Задание не найдено.")
    job, recipe = row
    items = await _items(session, job.id)
    if items:
        required = {i.material_id: (i.qty_required, i.qty_actual) for i in items}
    else:  # черновик: потребность считается по текущему рецепту
        loaded = await recipes.load(session, job.recipe_id)
        required = {i.material_id: (i.qty_per_unit * job.qty, None) for i in loaded.items}
    materials = await _materials(session, set(required))
    free = await free_by_material(session, set(required))
    reserved = await reserved_by_material(session, {job.id})
    issued_by_batch = await _issued_by_batch(session, job.id)
    batch_material = dict(
        (
            await session.execute(
                select(Batch.id, Batch.material_id).where(Batch.id.in_(issued_by_batch.keys()))
            )
        )
        .tuples()
        .all()
    )
    issued: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for batch_id, qty in issued_by_batch.items():
        issued[batch_material[batch_id]] += qty

    planned = job.status == JobStatus.PLANNED
    components = []
    for mid, (need, qty_actual) in required.items():
        in_reserve = reserved.get((job.id, mid), ZERO)
        components.append(
            ComponentOut(
                material=material_out(materials[mid]),
                qty_required=need,
                qty_reserved=in_reserve,
                qty_deficit=need - in_reserve if planned else ZERO,
                qty_free=free[mid],
                qty_issued=issued[mid],
                qty_actual=qty_actual,
                light=_light(need, in_reserve) if planned else None,
            )
        )
    reservation_rows = await session.execute(
        select(Reservation, Batch, Material)
        .join(Batch, Batch.id == Reservation.batch_id)
        .join(Material, Material.id == Batch.material_id)
        .where(Reservation.job_id == job.id)
        .order_by(Material.code, Batch.received_at, Batch.id)
    )
    return JobCardOut(
        **_to_job_out(job, recipe, any(c.qty_deficit > 0 for c in components)).model_dump(),
        components=sorted(components, key=lambda c: c.material.code),
        reservations=[
            ReservationOut(
                batch_id=b.id,
                batch_number=b.number,
                material_code=m.code,
                unit=m.unit,
                received_at=b.received_at,
                qty=r.qty,
            )
            for r, b, m in reservation_rows
        ],
        history=[
            AuditEntryOut.model_validate(h, from_attributes=True)
            for h in await audit.entries_for(session, "job", job.id)
        ],
        marking=await marking.get_for_job(session, job.id),
    )
