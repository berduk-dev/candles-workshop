"""Материалы, поставщики, поступления, партии. Роутеры тонкие: разбор запроса → сервис → commit."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.deps import CurrentUser, FxDep, SessionDep
from app.schemas.common import IdOut, Page
from app.schemas.inventory import (
    BatchCardOut,
    BatchOut,
    BatchQuery,
    BatchStatusChange,
    MaterialCreate,
    MaterialOut,
    MaterialStockOut,
    ReceiptCreate,
    ReceiptOut,
    SupplierOut,
)
from app.services import batches, materials, receipts, suppliers

router = APIRouter(tags=["Склад"])


@router.get("/materials", response_model=list[MaterialStockOut], summary="Материалы с остатками")
async def list_materials(
    session: SessionDep, user: CurrentUser, search: str | None = None, sort: str = "code"
):
    return await materials.list_with_stock(session, user, search, sort)


@router.post("/materials", response_model=MaterialOut, status_code=201, summary="Завести материал")
async def create_material(data: MaterialCreate, session: SessionDep, user: CurrentUser):
    result = await materials.create(session, user, data)
    await session.commit()
    return result


@router.get("/materials/{material_id}", response_model=MaterialStockOut, summary="Остаток материала")
async def get_material(material_id: int, session: SessionDep, user: CurrentUser):
    return await materials.get_stock(session, user, material_id)


@router.get("/suppliers", response_model=list[SupplierOut], summary="Поставщики")
async def list_suppliers(session: SessionDep, user: CurrentUser):
    return [SupplierOut(id=s.id, name=s.name) for s in await suppliers.list_suppliers(session)]


@router.post("/receipts", response_model=ReceiptOut, status_code=201, summary="Оформить поступление")
async def create_receipt(data: ReceiptCreate, session: SessionDep, user: CurrentUser, fx: FxDep):
    result = await receipts.create_receipt(session, user, data, fx)
    await session.commit()
    return result


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut, summary="Поступление")
async def get_receipt(receipt_id: int, session: SessionDep, user: CurrentUser):
    return await receipts.get_receipt(session, user, receipt_id)


# Параметры адреса (?material=воск&qty_free_min=5&sort=-price), описанные одной моделью.
BatchQueryDep = Annotated[BatchQuery, Query()]


@router.get("/batches", response_model=Page[BatchOut], summary="Реестр партий")
async def list_batches(session: SessionDep, user: CurrentUser, query: BatchQueryDep):
    return await batches.list_batches(
        session, user, query, sort=query.sort, page=query.page, page_size=query.page_size
    )


@router.get("/batches/export.xlsx", summary="Реестр партий в Excel (с теми же фильтрами)")
async def export_batches(session: SessionDep, user: CurrentUser, query: BatchQueryDep) -> Response:
    content = await batches.export_xlsx(session, user, query, sort=query.sort)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="batches.xlsx"'},
    )


@router.get("/batches/by-number/{number}", response_model=IdOut, summary="Найти партию по номеру")
async def find_batch(number: str, session: SessionDep, user: CurrentUser):
    """Номер (П-000019, п-19) → id. Нужен ссылкам из ответов ассистента."""
    return IdOut(id=(await batches.find_by_number(session, user, number)).id)


@router.get("/batches/{batch_id}", response_model=BatchCardOut, summary="Карточка партии")
async def get_batch(batch_id: int, session: SessionDep, user: CurrentUser):
    return await batches.get_batch_card(session, user, batch_id)


@router.post(
    "/batches/{batch_id}/status", response_model=BatchOut, summary="Принять партию или отметить брак"
)
async def change_batch_status(batch_id: int, data: BatchStatusChange, session: SessionDep, user: CurrentUser):
    result = await batches.change_status(session, user, batch_id, data.status)
    await session.commit()
    return result
