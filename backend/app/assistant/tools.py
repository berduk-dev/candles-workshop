"""Инструменты ассистента. Каждый — тонкая обёртка над функцией сервисного слоя:
аргументы проверяет Pydantic, права — сам сервис (require), SQL здесь нет.

Описания для модели лежат в prompts/tools.yaml.
"""

import csv
import io
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant import actions, memory
from app.assistant.llm.base import LLMError, ToolSpec
from app.assistant.masking import Masker
from app.assistant.prompts import note, tool_texts
from app.clock import today
from app.config import get_settings
from app.errors import DomainError, InvalidInputError, NotFoundError
from app.formatting import fmt_qty
from app.models import BatchStatus, Currency, JobStatus, User
from app.schemas.common import ShortText, StrictModel
from app.schemas.inventory import BatchFilters
from app.schemas.production import JobQty
from app.services import analytics, batches, jobs, materials, recipes, suppliers
from app.services.fx import FxClient
from app.services.permissions import Perm, has_perm
from app.validation_messages import humanize

logger = logging.getLogger(__name__)


class DatasetUploader(Protocol):
    async def __call__(self, filename: str, content: bytes) -> str: ...


@dataclass
class ToolContext:
    """Всё, что нужно инструменту. Пользователь берётся из сессии, а не из аргументов модели."""

    session: AsyncSession
    user: User
    fx: FxClient
    masker: Masker
    conversation_id: int
    message_id: int | None
    upload_dataset: DatasetUploader
    # Побочные результаты для интерфейса: карточки действий, сохранённые факты.
    actions: list[Any] = field(default_factory=list)
    # Пояснения к результату («показаны похожие…»): добавляются в ответ инструмента.
    notes: list[str] = field(default_factory=list)
    dataset_texts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Tool:
    name: str
    label: str  # подпись под ответом
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], Awaitable[Any]]
    perm: Perm | None = None


TOOLS: dict[str, Tool] = {}


def tool(name: str, label: str, args: type[BaseModel], perm: Perm | None = None):
    def register(handler):
        TOOLS[name] = Tool(name, label, args, handler, perm)
        return handler

    return register


# ---------- аргументы ----------


class NoArgs(StrictModel):
    pass


class MaterialArgs(StrictModel):
    material: ShortText


class ListBatchesArgs(StrictModel):
    material: ShortText | None = None
    supplier: ShortText | None = None
    status: BatchStatus | None = None
    received_from: date | None = None
    received_to: date | None = None


class BatchArgs(StrictModel):
    batch_number: ShortText


class ListJobsArgs(StrictModel):
    status: JobStatus | None = None
    overdue: bool = False
    date_from: date | None = None
    date_to: date | None = None


class JobArgs(StrictModel):
    job_number: ShortText


class PeriodArgs(StrictModel):
    date_from: date
    date_to: date


class JobCostArgs(StrictModel):
    job_number: ShortText
    currency: Currency = Currency.RUB


class RecipeCostArgs(StrictModel):
    recipe: ShortText
    currency: Currency = Currency.RUB


class DatasetArgs(StrictModel):
    kind: Literal["consumption", "stock", "batches", "jobs", "deficit", "recipe_cost"]
    date_from: date | None = None
    date_to: date | None = None
    material: ShortText | None = None
    recipe: ShortText | None = None


class FactArgs(StrictModel):
    fact: ShortText = Field(max_length=memory.MAX_LENGTH)


class ProposeJobArgs(StrictModel):
    recipe: ShortText
    qty: JobQty
    planned_date: date


# ---------- чтение ----------


async def _material(ctx: ToolContext, query: str):
    found = await materials.find(ctx.session, ctx.user, query)
    if found:
        return found
    # Точно не нашли — пробуем с опечатками. О замене модель обязана сказать пользователю.
    similar = await materials.find_similar(ctx.session, ctx.user, query)
    if not similar:
        raise NotFoundError(f"Материал «{query}» не найден.")
    ctx.notes.append(note("similar_materials", query=query, names=", ".join(m.name for m in similar)))
    return similar


async def _supplier_filter(ctx: ToolContext, query: str) -> str:
    """Поставщик для фильтра партий. Нет такого — ищем похожее название (опечатки)."""
    page = await batches.list_batches(ctx.session, ctx.user, BatchFilters(supplier=query), page_size=1)
    if page.total:
        return query
    similar = await suppliers.find_similar(ctx.session, query)
    if len(similar) == 1:
        ctx.notes.append(note("similar_supplier", query=query, name=similar[0].name))
        return similar[0].name
    if similar:
        names = ", ".join(s.name for s in similar)
        raise NotFoundError(note("several_suppliers", query=query, names=names))
    return query


@tool("list_materials", "Список материалов", NoArgs, Perm.VIEW_STOCK)
async def list_materials(ctx: ToolContext, args: NoArgs):
    return {"materials": await materials.list_with_stock(ctx.session, ctx.user)}


@tool("get_material_stock", "Остаток материала", MaterialArgs, Perm.VIEW_STOCK)
async def get_material_stock(ctx: ToolContext, args: MaterialArgs):
    result = []
    for material in (await _material(ctx, args.material))[:10]:
        page = await batches.list_batches(
            ctx.session,
            ctx.user,
            BatchFilters(material=material.code),
            sort="received_at",
            page_size=50,
        )
        result.append(
            {
                **material.model_dump(exclude={"id"}),
                "batches": [
                    {
                        "number": b.number,
                        "status": b.status_label,
                        "received_at": b.received_at,
                        "qty_on_hand": b.qty_on_hand,
                        "qty_reserved": b.qty_reserved,
                        "qty_free": b.qty_free,
                    }
                    for b in page.items
                    if b.qty_on_hand > 0 and b.material.code == material.code
                ],
            }
        )
    return {"materials": result}


@tool("list_batches", "Поиск партий", ListBatchesArgs, Perm.VIEW_STOCK)
async def list_batches(ctx: ToolContext, args: ListBatchesArgs):
    supplier = await _supplier_filter(ctx, args.supplier) if args.supplier else None
    page = await batches.list_batches(
        ctx.session,
        ctx.user,
        BatchFilters(
            material=args.material,
            supplier=supplier,
            status=[args.status] if args.status else None,
            received_from=args.received_from,
            received_to=args.received_to,
        ),
        page_size=30,
    )
    return {
        "total": page.total,
        "batches": [
            {
                "number": b.number,
                "material": f"{b.material.name} ({b.material.code})",
                "supplier": b.supplier.name,
                "received_at": b.received_at,
                "status": b.status_label,
                "qty_initial": b.qty_initial,
                "qty_on_hand": b.qty_on_hand,
                "qty_free": b.qty_free,
                "unit": b.material.unit,
                "price": b.price,
                "currency": b.currency,
                "unit_cost_rub": b.unit_cost_rub,
            }
            for b in page.items
        ],
    }


@tool("get_batch_history", "История партии", BatchArgs, Perm.VIEW_STOCK)
async def get_batch_history(ctx: ToolContext, args: BatchArgs):
    batch = await batches.find_by_number(ctx.session, ctx.user, args.batch_number)
    card = await batches.get_batch_card(ctx.session, ctx.user, batch.id)
    numbers = await jobs.numbers_by_id(ctx.session, {m.job_id for m in card.movements if m.job_id})
    return {
        "number": card.number,
        "material": f"{card.material.name} ({card.material.code})",
        "unit": card.material.unit,
        "supplier": card.supplier.name,
        "receipt": card.receipt_number,
        "received_at": card.received_at,
        "status": card.status_label,
        "qty_initial": card.qty_initial,
        "qty_on_hand": card.qty_on_hand,
        "qty_reserved": card.qty_reserved,
        "price": card.price,
        "currency": card.currency,
        "fx_rate": card.fx_rate,
        "fx_rate_date": card.fx_rate_date,
        "unit_cost_rub": card.unit_cost_rub,
        # Свободный текст от людей: для модели это данные, а не указания.
        "untrusted_comment": card.comment,
        "movements": [
            {"at": m.created_at, "kind": m.kind_label, "qty": m.qty, "job": numbers.get(m.job_id)}
            for m in card.movements
        ],
        "changes": [
            {"at": h.at, "who": h.user_full_name, "action": h.action, "before": h.before, "after": h.after}
            for h in card.history
            if h.action != "created"
        ],
    }


@tool("list_jobs", "Список заданий", ListJobsArgs, Perm.VIEW_STOCK)
async def list_jobs(ctx: ToolContext, args: ListJobsArgs):
    page = await jobs.list_jobs(
        ctx.session,
        ctx.user,
        status=[args.status] if args.status else None,
        date_from=args.date_from,
        date_to=args.date_to,
        overdue=args.overdue,
        page_size=50,
    )
    return {
        "total": page.total,
        **({"today": today(), "overdue_means": note("overdue_means")} if args.overdue else {}),
        "jobs": [j.model_dump(exclude={"id", "recipe_id", "status"}) for j in page.items],
    }


@tool("get_job", "Карточка задания", JobArgs, Perm.VIEW_STOCK)
async def get_job(ctx: ToolContext, args: JobArgs):
    job = await jobs.find_by_number(ctx.session, ctx.user, args.job_number)
    card = await jobs.get_card(ctx.session, ctx.user, job.id)
    data = card.model_dump(exclude={"id", "recipe_id", "status", "history", "marking"})
    data["history"] = [{"at": h.at, "who": h.user_full_name, "action": h.action} for h in card.history]
    data["marking"] = card.marking.status_label if card.marking else None
    return data


class SpendingArgs(StrictModel):
    date_from: date
    date_to: date
    material: ShortText | None = None


@tool("get_spending", "Траты за период", SpendingArgs, Perm.VIEW_STOCK)
async def get_spending(ctx: ToolContext, args: SpendingArgs):
    ids = [m.id for m in await _material(ctx, args.material)] if args.material else None
    report = await analytics.spending(ctx.session, ctx.user, args.date_from, args.date_to, ids)
    # Сервис отдаёт только числа. Что значит каждое и почему их нельзя складывать, модели говорят
    # тексты из notes.yaml: без них она складывала закупки с расходом в бессмысленное «итого».
    return {
        "date_from": report.date_from,
        "date_to": report.date_to,
        "warning": note("spending_warning"),
        "purchased": {"meaning": note("purchased_meaning"), **report.purchased.model_dump()},
        "consumed": {"meaning": note("consumed_meaning"), **report.consumed.model_dump()},
    }


class AvailabilityArgs(StrictModel):
    recipe: ShortText
    qty: JobQty


@tool("check_availability", "Хватит ли материалов", AvailabilityArgs, Perm.VIEW_STOCK)
async def check_availability(ctx: ToolContext, args: AvailabilityArgs):
    recipe = await recipes.find_by_name(ctx.session, ctx.user, args.recipe)
    preview = await jobs.availability(ctx.session, ctx.user, recipe.id, args.qty)
    short = [i for i in preview.items if i.qty_deficit > 0]
    return {
        "enough": not short,
        "shortage": [
            {"material": i.material.name, "missing": fmt_qty(i.qty_deficit, i.material.unit)} for i in short
        ],
        "items": [i.model_dump(exclude={"material"}) | {"material": i.material.name} for i in preview.items],
    }


@tool("get_deficit", "Дефицит за период", PeriodArgs, Perm.VIEW_STOCK)
async def get_deficit(ctx: ToolContext, args: PeriodArgs):
    return await analytics.deficit(ctx.session, ctx.user, args.date_from, args.date_to)


@tool("get_job_cost", "Себестоимость задания", JobCostArgs, Perm.JOB_COST_VIEW)
async def get_job_cost(ctx: ToolContext, args: JobCostArgs):
    job = await jobs.find_by_number(ctx.session, ctx.user, args.job_number)
    cost = await analytics.job_cost(ctx.session, ctx.user, job.id, args.currency, ctx.fx)
    missing = [
        f"{r.material.name}: {fmt_qty(r.qty_unpriced, r.material.unit)}" for r in cost.rows if r.qty_unpriced
    ]
    if not missing:
        return cost
    return {"warning": note("cost_incomplete", missing="; ".join(missing)), **cost.model_dump()}


@tool("list_recipes", "Рецепты", NoArgs, Perm.VIEW_STOCK)
async def list_recipes(ctx: ToolContext, args: NoArgs):
    return {"recipes": await recipes.list_recipes(ctx.session, ctx.user)}


@tool("get_recipe_cost", "Себестоимость свечи", RecipeCostArgs, Perm.JOB_COST_VIEW)
async def get_recipe_cost(ctx: ToolContext, args: RecipeCostArgs):
    recipe = await recipes.find_by_name(ctx.session, ctx.user, args.recipe)
    cost = await analytics.recipe_cost(ctx.session, ctx.user, recipe.id, args.currency, ctx.fx)
    return {**cost.model_dump(), "note": note("money")}


# ---------- данные для Code Interpreter ----------


@tool("prepare_dataset", "Данные для расчёта", DatasetArgs)
async def prepare_dataset(ctx: ToolContext, args: DatasetArgs):
    """CSV собирается теми же сервисами (значит, с правами пользователя) и обезличивается."""
    date_to = args.date_to or today()
    date_from = args.date_from or date_to - timedelta(days=90)
    rows = await _dataset_rows(ctx, args, date_from, date_to)
    if not rows:
        return {"rows": 0, "message": note("dataset_empty")}
    rows = [{k: ctx.masker.mask_obj(_plain(v)) for k, v in row.items()} for row in rows]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    content = out.getvalue()
    ctx.dataset_texts.append(content)
    path = await ctx.upload_dataset(f"{args.kind}.csv", content.encode("utf-8"))
    result = {"path": path, "rows": len(rows), "columns": list(rows[0]), "sample": rows[:3]}
    if args.kind in ("batches", "recipe_cost"):  # цены с четырьмя знаками
        result["note"] = note("money")
    return result


async def _dataset_rows(
    ctx: ToolContext, args: DatasetArgs, date_from: date, date_to: date
) -> list[dict[str, Any]]:
    s, user = ctx.session, ctx.user
    if args.kind == "consumption":
        ids = [m.id for m in await _material(ctx, args.material)] if args.material else None
        return [
            {
                "date": r.day,
                "week_start": r.day - timedelta(days=r.day.weekday()),  # понедельник недели
                "material_code": r.material.code,
                "material": r.material.name,
                "unit": r.material.unit,
                "qty": r.qty,
                "cost_rub": r.cost_rub,
            }
            for r in await analytics.consumption(s, user, date_from, date_to, ids)
        ]
    if args.kind == "stock":
        return [m.model_dump(exclude={"id"}) for m in await materials.list_with_stock(s, user)]
    if args.kind == "batches":
        filters = BatchFilters(
            material=args.material,
            received_from=args.date_from,
            received_to=args.date_to,
        )
        rows: list[dict[str, Any]] = []
        for page_no in range(1, 21):  # не больше 2000 партий
            page = await batches.list_batches(
                s, user, filters, sort="received_at", page=page_no, page_size=100
            )
            rows += [
                {
                    "number": b.number,
                    "received_at": b.received_at,
                    "material_code": b.material.code,
                    "material": b.material.name,
                    "unit": b.material.unit,
                    "supplier": b.supplier.name,
                    "status": b.status_label,
                    "qty_initial": b.qty_initial,
                    "qty_on_hand": b.qty_on_hand,
                    "qty_free": b.qty_free,
                    "price": b.price,
                    "currency": b.currency,
                    "unit_cost_rub": b.unit_cost_rub,
                }
                for b in page.items
            ]
            if len(rows) >= page.total:
                break
        return rows
    if args.kind == "jobs":
        page = await jobs.list_jobs(s, user, date_from=args.date_from, date_to=args.date_to, page_size=100)
        return [j.model_dump(exclude={"id", "recipe_id", "status"}) for j in page.items]
    if args.kind == "deficit":
        if not args.date_to:
            date_from, date_to = args.date_from or today(), today() + timedelta(days=30)
        report = await analytics.deficit(s, user, date_from, date_to)
        return [
            {
                "job": r.job_number,
                "recipe": r.recipe_name,
                "planned_date": r.planned_date,
                "material": r.material.name,
                "unit": r.material.unit,
                "required": r.qty_required,
                "reserved": r.qty_reserved,
                "deficit": r.qty_deficit,
            }
            for r in report.rows
        ]
    if not args.recipe:
        raise InvalidInputError("Для recipe_cost укажите рецепт.")
    recipe = await recipes.find_by_name(s, user, args.recipe)
    cost = await analytics.recipe_cost(s, user, recipe.id, Currency.RUB, ctx.fx)
    return [
        {
            "material_code": r.material.code,
            "material": r.material.name,
            "unit": r.material.unit,
            "qty_per_unit": r.qty_per_unit,
            "avg_unit_cost_rub": r.avg_unit_cost,
            "cost_per_candle_rub": r.cost_per_unit,
        }
        for r in cost.rows
    ]


# ---------- память и действия ----------


@tool("save_user_fact", "Запомнил факт", FactArgs)
async def save_user_fact(ctx: ToolContext, args: FactArgs):
    row = await memory.add_fact(ctx.session, ctx.user, args.fact)
    return {"saved": row.fact}


@tool("propose_create_and_plan_job", "Предложение задания", ProposeJobArgs, Perm.JOB_MANAGE)
async def propose_create_and_plan_job(ctx: ToolContext, args: ProposeJobArgs):
    action = await actions.propose_create_job(
        ctx.session,
        ctx.user,
        conversation_id=ctx.conversation_id,
        message_id=ctx.message_id,
        recipe_name=args.recipe,
        data_qty=args.qty,
        planned_date=args.planned_date,
    )
    return _proposed(ctx, action)


@tool("propose_cancel_job", "Предложение отмены", JobArgs, Perm.JOB_CANCEL)
async def propose_cancel_job(ctx: ToolContext, args: JobArgs):
    action = await actions.propose_cancel_job(
        ctx.session,
        ctx.user,
        conversation_id=ctx.conversation_id,
        message_id=ctx.message_id,
        job_number=args.job_number,
    )
    return _proposed(ctx, action)


def _proposed(ctx: ToolContext, action) -> dict[str, Any]:
    ctx.actions.append(action)
    return {"status": note("action_proposed"), "preview": action.preview}


# ---------- выполнение ----------


@dataclass
class ToolOutcome:
    output: str  # JSON для модели, уже с псевдонимами
    ok: bool
    summary: str  # что показать пользователю


def _plain(value: Any) -> Any:
    """Приводит к типам JSON. Decimal — строкой без лишних нулей: «12.5», а не 12.500 и не float."""
    if isinstance(value, BaseModel):
        return _plain(value.model_dump())
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(get_settings().tz).strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _error(masker: Masker, message: str) -> ToolOutcome:
    output = json.dumps({"error": masker.mask_text(message)}, ensure_ascii=False)
    return ToolOutcome(output, False, message)


async def execute(name: str, raw_args: str, ctx: ToolContext) -> ToolOutcome:
    """Выполняет вызов инструмента. Любая ошибка возвращается модели текстом и не роняет чат."""
    spec = TOOLS.get(name)
    if spec is None:
        return _error(ctx.masker, f"Инструмента «{name}» не существует.")
    try:
        decoded = json.loads(raw_args or "{}")
        # Коды из аргументов превращаются обратно в имена до обращения к сервисам.
        args = spec.args.model_validate(ctx.masker.unmask_obj(decoded))
        result = await spec.handler(ctx, args)
        await ctx.session.commit()
    except json.JSONDecodeError:
        return _error(ctx.masker, "Аргументы — не корректный JSON.")
    except ValidationError as exc:
        return _error(ctx.masker, f"Неверные аргументы: {humanize(exc.errors())}")
    except DomainError as exc:
        await ctx.session.rollback()
        return _error(ctx.masker, exc.message)
    except LLMError as exc:  # не удалось положить файл в контейнер
        await ctx.session.rollback()
        return _error(ctx.masker, f"Среда для расчётов недоступна. {exc.reason}")
    except Exception:
        await ctx.session.rollback()
        error_id = uuid.uuid4().hex[:8]
        # Аргументы не логируем: в них могут быть имена.
        logger.exception("Инструмент %s: ошибка %s", name, error_id)
        return _error(ctx.masker, f"Внутренняя ошибка инструмента. Код ошибки: {error_id}.")
    plain = _plain(result)
    if ctx.notes and isinstance(plain, dict):
        plain = {"note": " ".join(ctx.notes), **plain}
    output = json.dumps(ctx.masker.mask_obj(plain), ensure_ascii=False)
    return ToolOutcome(output, True, spec.label)


def specs_for(user: User) -> list[ToolSpec]:
    """Инструменты, доступные роли. Это подсказка модели, а не защита: права всё равно проверит сервис."""
    texts = tool_texts()
    result = []
    for spec in TOOLS.values():
        if spec.perm and not has_perm(user, spec.perm):
            continue
        schema = spec.args.model_json_schema()
        for param, text in (texts[spec.name].get("params") or {}).items():
            schema["properties"][param]["description"] = text
        result.append(ToolSpec(spec.name, texts[spec.name]["description"].strip(), schema))
    return result
