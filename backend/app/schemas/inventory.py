from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.models import BatchStatus, Currency, MovementKind
from app.schemas.common import Comment, PositiveQty, Price, ShortText, StrictModel

MaterialCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, min_length=1, max_length=30, pattern=r"^[A-Za-z0-9_-]+$"
    ),
]

# ---------- вход ----------


class MaterialCreate(StrictModel):
    code: MaterialCode
    name: ShortText
    unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]


class ReceiptLineIn(StrictModel):
    material_code: MaterialCode
    qty: PositiveQty
    price: Price = Field(description="Цена за единицу в валюте строки")
    currency: Currency
    comment: Comment | None = None


class ReceiptCreate(StrictModel):
    supplier_name: ShortText
    received_at: date
    supplier_doc: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=100)


class BatchStatusChange(StrictModel):
    status: Literal[BatchStatus.ACCEPTED, BatchStatus.REJECTED]


BATCH_SORT_FIELDS = (
    "number",
    "received_at",
    "material",
    "supplier",
    "qty_initial",
    "qty_on_hand",
    "qty_free",
    "price",
    "currency",
    "unit_cost_rub",
    "status",
)


class BatchFilters(BaseModel):
    """Фильтры реестра партий — по одному на колонку."""

    number: str | None = None
    material: str | None = None
    supplier: str | None = None
    status: list[BatchStatus] | None = None
    currency: list[Currency] | None = None
    received_from: date | None = None
    received_to: date | None = None
    qty_initial_min: Decimal | None = None
    qty_initial_max: Decimal | None = None
    qty_on_hand_min: Decimal | None = None
    qty_on_hand_max: Decimal | None = None
    qty_free_min: Decimal | None = None
    qty_free_max: Decimal | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    unit_cost_rub_min: Decimal | None = None
    unit_cost_rub_max: Decimal | None = None


class BatchQuery(BatchFilters):
    sort: str = "-received_at"
    page: int = 1
    page_size: int = 20


# ---------- выход ----------


class MaterialOut(BaseModel):
    id: int
    code: str
    name: str
    unit: str


class MaterialStockOut(MaterialOut):
    qty_on_hand: Decimal
    qty_reserved: Decimal
    qty_free: Decimal
    qty_on_check: Decimal


class SupplierOut(BaseModel):
    id: int
    name: str


class BatchOut(BaseModel):
    id: int
    number: str
    receipt_id: int
    receipt_number: str
    material: MaterialOut
    supplier: SupplierOut
    received_at: date
    qty_initial: Decimal
    qty_on_hand: Decimal
    qty_reserved: Decimal
    qty_free: Decimal
    price: Decimal
    currency: Currency
    fx_rate: Decimal
    fx_rate_date: date
    unit_cost_rub: Decimal
    status: BatchStatus
    status_label: str
    comment: str | None


class MovementOut(BaseModel):
    id: int
    kind: MovementKind
    kind_label: str
    qty: Decimal
    job_id: int | None
    created_at: datetime


class AuditEntryOut(BaseModel):
    id: int
    at: datetime
    user_full_name: str
    entity: str
    entity_id: int
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class BatchCardOut(BatchOut):
    movements: list[MovementOut]
    history: list[AuditEntryOut]


class ReceiptOut(BaseModel):
    id: int
    number: str
    supplier: SupplierOut
    received_at: date
    supplier_doc: str | None
    batches: list[BatchOut]
