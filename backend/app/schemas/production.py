from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.models import Currency, JobStatus, MarkingStatus
from app.schemas.common import NonNegativeQty, PositiveQty, ShortText, StrictModel
from app.schemas.inventory import AuditEntryOut, MaterialCode, MaterialOut

JobQty = Annotated[int, Field(gt=0, le=100_000)]

# ---------- вход ----------


class RecipeItemIn(StrictModel):
    material_code: MaterialCode
    qty_per_unit: PositiveQty


class RecipeIn(StrictModel):
    name: ShortText
    items: list[RecipeItemIn] = Field(min_length=1, max_length=50)

    @field_validator("items")
    @classmethod
    def no_duplicates(cls, items: list[RecipeItemIn]) -> list[RecipeItemIn]:
        codes = [item.material_code for item in items]
        if len(codes) != len(set(codes)):
            raise ValueError("материал указан в рецепте дважды")
        return items


class JobIn(StrictModel):
    recipe_id: int
    qty: JobQty
    planned_date: date


class ActualItemIn(StrictModel):
    material_code: MaterialCode
    qty: NonNegativeQty


class JobCompleteIn(StrictModel):
    produced_qty: Annotated[int, Field(ge=0, le=100_000)]
    # Материалы, которых нет в списке, считаются израсходованными ровно по выдаче.
    actual: list[ActualItemIn] = Field(default_factory=list, max_length=50)


# ---------- выход ----------


class RecipeItemOut(BaseModel):
    material: MaterialOut
    qty_per_unit: Decimal


class RecipeOut(BaseModel):
    id: int
    name: str
    items: list[RecipeItemOut]


Light = Literal["green", "yellow", "red"]


class ComponentOut(BaseModel):
    """Строка «светофора»: сколько нужно, сколько в резерве, чего не хватает."""

    material: MaterialOut
    qty_required: Decimal
    qty_reserved: Decimal
    qty_deficit: Decimal
    qty_free: Decimal = Field(description="Свободный остаток на складе сейчас")
    qty_issued: Decimal
    qty_actual: Decimal | None
    light: Light | None = Field(description="Только для запланированных заданий")


class ReservationOut(BaseModel):
    batch_id: int
    batch_number: str
    material_code: str
    unit: str
    received_at: date
    qty: Decimal


class JobOut(BaseModel):
    id: int
    number: str
    recipe_id: int
    recipe_name: str
    qty: int
    planned_date: date
    status: JobStatus
    status_label: str
    produced_qty: int | None
    has_deficit: bool


class MarkingAttemptOut(BaseModel):
    number: int
    at: datetime
    duration_ms: int
    http_status: int | None
    error: str | None


class MarkingOut(BaseModel):
    status: MarkingStatus
    status_label: str
    attempts: int
    next_attempt_at: datetime
    log: list[MarkingAttemptOut]


class JobCardOut(JobOut):
    components: list[ComponentOut]
    reservations: list[ReservationOut]
    history: list[AuditEntryOut]
    marking: MarkingOut | None


class PlanPreviewItem(BaseModel):
    material: MaterialOut
    qty_required: Decimal
    qty_available: Decimal
    qty_deficit: Decimal


class PlanPreview(BaseModel):
    recipe: RecipeOut
    qty: int
    planned_date: date
    items: list[PlanPreviewItem]
    has_deficit: bool


class DeficitRow(BaseModel):
    job_id: int
    job_number: str
    recipe_name: str
    planned_date: date
    material: MaterialOut
    qty_required: Decimal
    qty_reserved: Decimal
    qty_deficit: Decimal


class DeficitTotal(BaseModel):
    material: MaterialOut
    qty_deficit: Decimal
    qty_free: Decimal


class DeficitOut(BaseModel):
    date_from: date
    date_to: date
    planned_jobs: int
    rows: list[DeficitRow]
    totals: list[DeficitTotal]


class CostRow(BaseModel):
    material: MaterialOut
    qty: Decimal
    cost: Decimal
    qty_unpriced: Decimal = Decimal("0")


class JobCostOut(BaseModel):
    job_number: str
    recipe_name: str
    status_label: str
    basis: str = Field(description="По чему посчитано: факт, резерв или оценка")
    is_complete: bool = Field(description="False, если часть материалов не оценена из-за дефицита")
    units: int
    currency: Currency
    fx_rate: Decimal
    fx_rate_date: date
    total: Decimal
    per_unit: Decimal | None
    rows: list[CostRow]


class RecipeCostRow(BaseModel):
    material: MaterialOut
    qty_per_unit: Decimal
    avg_unit_cost: Decimal | None
    cost_per_unit: Decimal | None


class RecipeCostOut(BaseModel):
    recipe_name: str
    currency: Currency
    fx_rate: Decimal
    fx_rate_date: date
    per_unit: Decimal
    is_complete: bool
    rows: list[RecipeCostRow]


class SpendingRow(BaseModel):
    material: MaterialOut
    qty: Decimal
    cost_rub: Decimal


class SpendingPart(BaseModel):
    meaning: str = Field(description="Что именно посчитано — это нужно сказать пользователю")
    total_rub: Decimal
    by_material: list[SpendingRow]


class SpendingOut(BaseModel):
    """«Сколько потратили» — это два разных числа: что купили и что израсходовали."""

    date_from: date
    date_to: date
    warning: str = Field(description="Почему два числа нельзя складывать")
    purchased: SpendingPart
    consumed: SpendingPart


class ConsumptionRow(BaseModel):
    day: date
    material: MaterialOut
    qty: Decimal
    cost_rub: Decimal
