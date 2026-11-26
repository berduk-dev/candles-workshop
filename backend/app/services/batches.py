import asyncio
import io
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError, InvalidInputError, NotFoundError
from app.models import Batch, BatchStatus, Material, Receipt, StockMovement, Supplier, User
from app.schemas.common import Page
from app.schemas.inventory import (
    BATCH_SORT_FIELDS,
    AuditEntryOut,
    BatchCardOut,
    BatchFilters,
    BatchOut,
    MaterialOut,
    MovementOut,
    SupplierOut,
)
from app.services import audit
from app.services.permissions import Perm, require
from app.services.sorting import order_by
from app.services.text import like_pattern, normalize_doc_number

# Поля партии, которые попадают в журнал изменений.
BATCH_AUDIT_FIELDS = [
    "number",
    "material_id",
    "qty_initial",
    "qty_on_hand",
    "qty_reserved",
    "price",
    "currency",
    "fx_rate",
    "fx_rate_date",
    "unit_cost_rub",
    "status",
    "comment",
]

MAX_PAGE_SIZE = 100

# Свободный остаток партии: только у принятых, у остальных 0.
qty_free_expr = case((Batch.status == BatchStatus.ACCEPTED, Batch.qty_on_hand - Batch.qty_reserved), else_=0)

_SORT_COLUMNS = {
    "number": Batch.number,
    "received_at": Batch.received_at,
    "material": Material.name,
    "supplier": Supplier.name,
    "qty_initial": Batch.qty_initial,
    "qty_on_hand": Batch.qty_on_hand,
    "qty_free": qty_free_expr,
    "price": Batch.price,
    "currency": Batch.currency,
    "unit_cost_rub": Batch.unit_cost_rub,
    "status": Batch.status,
}
# Числовые колонки с фильтром «от — до».
RANGE_FIELDS = ("qty_initial", "qty_on_hand", "qty_free", "price", "unit_cost_rub")
assert set(_SORT_COLUMNS) == set(BATCH_SORT_FIELDS)


def batch_query() -> Select:
    return (
        select(Batch, Material, Receipt, Supplier)
        .join(Material, Material.id == Batch.material_id)
        .join(Receipt, Receipt.id == Batch.receipt_id)
        .join(Supplier, Supplier.id == Receipt.supplier_id)
    )


def to_batch_out(batch: Batch, material: Material, receipt: Receipt, supplier: Supplier) -> BatchOut:
    free = batch.qty_on_hand - batch.qty_reserved if batch.status == BatchStatus.ACCEPTED else Decimal("0")
    return BatchOut(
        id=batch.id,
        number=batch.number,
        receipt_id=receipt.id,
        receipt_number=receipt.number,
        material=MaterialOut(id=material.id, code=material.code, name=material.name, unit=material.unit),
        supplier=SupplierOut(id=supplier.id, name=supplier.name),
        received_at=batch.received_at,
        qty_initial=batch.qty_initial,
        qty_on_hand=batch.qty_on_hand,
        qty_reserved=batch.qty_reserved,
        qty_free=free,
        price=batch.price,
        currency=batch.currency,
        fx_rate=batch.fx_rate,
        fx_rate_date=batch.fx_rate_date,
        unit_cost_rub=batch.unit_cost_rub,
        status=batch.status,
        status_label=batch.status.label,
        comment=batch.comment,
    )


def _apply_filters(query: Select, filters: BatchFilters) -> Select:
    if filters.number:
        query = query.where(Batch.number.ilike(like_pattern(filters.number), escape="\\"))
    if filters.material:
        pattern = like_pattern(filters.material)
        query = query.where(
            or_(Material.code.ilike(pattern, escape="\\"), Material.name.ilike(pattern, escape="\\"))
        )
    if filters.supplier:
        query = query.where(Supplier.name.ilike(like_pattern(filters.supplier), escape="\\"))
    if filters.status:
        query = query.where(Batch.status.in_(filters.status))
    if filters.currency:
        query = query.where(Batch.currency.in_(filters.currency))
    if filters.received_from:
        query = query.where(Batch.received_at >= filters.received_from)
    if filters.received_to:
        query = query.where(Batch.received_at <= filters.received_to)
    for field in RANGE_FIELDS:
        if (low := getattr(filters, f"{field}_min")) is not None:
            query = query.where(_SORT_COLUMNS[field] >= low)
        if (high := getattr(filters, f"{field}_max")) is not None:
            query = query.where(_SORT_COLUMNS[field] <= high)
    return query


def _order_by(sort: str):
    return order_by(sort, _SORT_COLUMNS, Batch.id)


async def list_batches(
    session: AsyncSession,
    user: User,
    filters: BatchFilters,
    *,
    sort: str = "-received_at",
    page: int = 1,
    page_size: int = 20,
) -> Page[BatchOut]:
    require(user, Perm.VIEW_STOCK)
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise InvalidInputError(f"Страница начинается с 1, на странице от 1 до {MAX_PAGE_SIZE} строк.")
    query = _apply_filters(batch_query(), filters)
    total = await session.scalar(
        select(func.count()).select_from(query.with_only_columns(Batch.id).subquery())
    )
    rows = await session.execute(
        query.order_by(*_order_by(sort)).offset((page - 1) * page_size).limit(page_size)
    )
    return Page[BatchOut](
        items=[to_batch_out(*row) for row in rows], total=total or 0, page=page, page_size=page_size
    )


async def _load_one(session: AsyncSession, condition) -> BatchOut:
    row = (await session.execute(batch_query().where(condition))).first()
    if row is None:
        raise NotFoundError("Партия не найдена.")
    return to_batch_out(*row)


async def get_batch(session: AsyncSession, user: User, batch_id: int) -> BatchOut:
    require(user, Perm.VIEW_STOCK)
    return await _load_one(session, Batch.id == batch_id)


async def find_by_number(session: AsyncSession, user: User, number: str) -> BatchOut:
    require(user, Perm.VIEW_STOCK)
    canonical = normalize_doc_number(number)
    row = (await session.execute(batch_query().where(Batch.number == canonical))).first()
    if row is None:
        raise NotFoundError(f"Партия {canonical} не найдена.")
    return to_batch_out(*row)


async def get_batch_card(session: AsyncSession, user: User, batch_id: int) -> BatchCardOut:
    batch = await get_batch(session, user, batch_id)
    movements = await session.scalars(
        select(StockMovement).where(StockMovement.batch_id == batch_id).order_by(StockMovement.id)
    )
    history = await audit.entries_for(session, "batch", batch_id)
    return BatchCardOut(
        **batch.model_dump(),
        movements=[
            MovementOut(
                id=m.id,
                kind=m.kind,
                kind_label=m.kind.label,
                qty=m.qty,
                job_id=m.job_id,
                created_at=m.created_at,
            )
            for m in movements
        ],
        history=[AuditEntryOut.model_validate(h, from_attributes=True) for h in history],
    )


async def change_status(
    session: AsyncSession, user: User, batch_id: int, new_status: BatchStatus
) -> BatchOut:
    """«На проверке» → «Принята» или «Брак». Оба конечных статуса окончательные."""
    require(user, Perm.BATCH_STATUS)
    if new_status == BatchStatus.ON_CHECK:
        raise InvalidInputError("Вернуть партию на проверку нельзя.")
    # Блокировка строки: два кладовщика не поменяют статус одновременно.
    batch = await session.scalar(select(Batch).where(Batch.id == batch_id).with_for_update())
    if batch is None:
        raise NotFoundError("Партия не найдена.")
    if batch.status != BatchStatus.ON_CHECK:
        raise DomainError(
            f"Партия {batch.number} уже в статусе «{batch.status.label}». "
            "Статус меняется только у партий «На проверке»."
        )
    before = audit.snapshot(batch, ["status"])
    batch.status = new_status
    audit.record(
        session,
        user,
        entity="batch",
        entity_id=batch.id,
        action="status_changed",
        before=before,
        after=audit.snapshot(batch, ["status"]),
    )
    await session.flush()
    return await _load_one(session, Batch.id == batch_id)


EXPORT_LIMIT = 10_000
EXPORT_COLUMNS = [
    ("Номер", lambda b: b.number),
    ("Код", lambda b: b.material.code),
    ("Материал", lambda b: b.material.name),
    ("Ед.", lambda b: b.material.unit),
    ("Поставщик", lambda b: b.supplier.name),
    ("Дата поступления", lambda b: b.received_at),
    ("Поступило", lambda b: b.qty_initial),
    ("Остаток", lambda b: b.qty_on_hand),
    ("В резерве", lambda b: b.qty_reserved),
    ("Свободно", lambda b: b.qty_free),
    ("Цена", lambda b: b.price),
    ("Валюта", lambda b: b.currency.value),
    ("Курс", lambda b: b.fx_rate),
    ("Себестоимость, ₽", lambda b: b.unit_cost_rub),
    ("Статус", lambda b: b.status_label),
    ("Комментарий", lambda b: b.comment),
]
FORMULA_PREFIXES = ("=", "+", "-", "@")


async def export_xlsx(
    session: AsyncSession, user: User, filters: BatchFilters, *, sort: str = "-received_at"
) -> bytes:
    """Реестр с теми же фильтрами и сортировкой, что на экране. Числа и даты — настоящие числа и даты."""
    require(user, Perm.VIEW_STOCK)
    query = _apply_filters(batch_query(), filters).order_by(*_order_by(sort)).limit(EXPORT_LIMIT)
    rows = [to_batch_out(*row) for row in await session.execute(query)]
    return await asyncio.to_thread(_build_xlsx, rows)


def _build_xlsx(rows: list[BatchOut]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Партии"
    sheet.append([title for title, _ in EXPORT_COLUMNS])
    for batch in rows:
        sheet.append([get(batch) for _, get in EXPORT_COLUMNS])
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if cell.is_date:
                cell.number_format = "DD.MM.YYYY"
            elif isinstance(cell.value, Decimal):
                cell.number_format = "#,##0.###"
            elif isinstance(cell.value, str) and cell.value.startswith(FORMULA_PREFIXES):
                cell.data_type = "s"  # текст от людей не должен стать формулой Excel
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        sheet.column_dimensions[cell.column_letter].width = max(12, len(cell.value) + 4)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
