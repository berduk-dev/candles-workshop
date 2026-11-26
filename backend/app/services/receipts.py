from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clock import today
from app.errors import InvalidInputError, NotFoundError
from app.formatting import fmt_date, q_money
from app.models import Batch, BatchStatus, Currency, Material, MovementKind, Receipt, StockMovement, User
from app.models.inventory import batch_number_seq, receipt_number_seq
from app.schemas.inventory import ReceiptCreate, ReceiptOut, SupplierOut
from app.services import audit, suppliers
from app.services.batches import BATCH_AUDIT_FIELDS, batch_query, to_batch_out
from app.services.fx import FxClient, FxQuote, get_rate
from app.services.permissions import Perm, require


async def create_receipt(session: AsyncSession, user: User, data: ReceiptCreate, fx: FxClient) -> ReceiptOut:
    """Оформляет поступление: каждая строка становится партией «На проверке».

    Себестоимость единицы в рублях = цена × курс на дату поступления (в выходной — последний
    предыдущий курс). Курс фиксируется в партии и больше не пересчитывается.
    """
    require(user, Perm.RECEIPT_CREATE)
    if data.received_at > today():
        raise InvalidInputError(f"Дата поступления {fmt_date(data.received_at)} ещё не наступила.")

    codes = {line.material_code for line in data.lines}
    materials = {m.code: m for m in await session.scalars(select(Material).where(Material.code.in_(codes)))}
    missing = sorted(codes - materials.keys())
    if missing:
        raise NotFoundError(f"Не найдены материалы с кодами: {', '.join(missing)}.")

    supplier = await suppliers.get_or_create(session, data.supplier_name)
    receipt_no = await session.scalar(select(receipt_number_seq.next_value()))
    receipt = Receipt(
        number=f"ПТ-{receipt_no:06d}",
        supplier_id=supplier.id,
        received_at=data.received_at,
        supplier_doc=data.supplier_doc,
        created_by=user.id,
    )
    session.add(receipt)
    await session.flush()

    # Курс каждой валюты запрашиваем один раз на всё поступление.
    quotes: dict[Currency, FxQuote] = {}
    for line in data.lines:
        if line.currency not in quotes:
            quotes[line.currency] = await get_rate(session, fx, line.currency, data.received_at)
        quote = quotes[line.currency]
        batch_no = await session.scalar(select(batch_number_seq.next_value()))
        batch = Batch(
            number=f"П-{batch_no:06d}",
            receipt_id=receipt.id,
            material_id=materials[line.material_code].id,
            received_at=data.received_at,
            qty_initial=line.qty,
            qty_on_hand=line.qty,
            qty_reserved=0,
            price=line.price,
            currency=line.currency,
            fx_rate=quote.rate,
            fx_rate_date=quote.rate_date,
            unit_cost_rub=q_money(line.price * quote.rate),
            status=BatchStatus.ON_CHECK,
            comment=line.comment or None,
        )
        session.add(batch)
        await session.flush()
        session.add(
            StockMovement(batch_id=batch.id, kind=MovementKind.RECEIPT, qty=line.qty, user_id=user.id)
        )
        audit.record(
            session,
            user,
            entity="batch",
            entity_id=batch.id,
            action="created",
            after=audit.snapshot(batch, BATCH_AUDIT_FIELDS),
        )

    audit.record(
        session,
        user,
        entity="receipt",
        entity_id=receipt.id,
        action="created",
        after={
            "number": receipt.number,
            "supplier_id": supplier.id,
            "received_at": receipt.received_at.isoformat(),
            "lines": len(data.lines),
        },
    )
    await session.flush()
    return await get_receipt(session, user, receipt.id)


async def get_receipt(session: AsyncSession, user: User, receipt_id: int) -> ReceiptOut:
    require(user, Perm.VIEW_STOCK)
    rows = (
        await session.execute(batch_query().where(Batch.receipt_id == receipt_id).order_by(Batch.id))
    ).all()
    if not rows:
        raise NotFoundError("Поступление не найдено.")
    _, _, receipt, supplier = rows[0]
    return ReceiptOut(
        id=receipt.id,
        number=receipt.number,
        supplier=SupplierOut(id=supplier.id, name=supplier.name),
        received_at=receipt.received_at,
        supplier_doc=receipt.supplier_doc,
        batches=[to_batch_out(*row) for row in rows],
    )
