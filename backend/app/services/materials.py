from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError, NotFoundError
from app.models import Batch, BatchStatus, Material, User
from app.schemas.inventory import MaterialCreate, MaterialOut, MaterialStockOut
from app.services import audit
from app.services.morph import same_text, similar_text
from app.services.permissions import Perm, require
from app.services.text import like_pattern

ZERO = Decimal("0")


def _accepted(column):
    return func.coalesce(func.sum(case((Batch.status == BatchStatus.ACCEPTED, column), else_=0)), 0)


def _stock_subquery():
    """Остатки по материалу. В остаток и свободный остаток идут только принятые партии;
    партии «На проверке» считаются отдельно, «Брак» не считается вовсе."""
    return (
        select(
            Batch.material_id.label("material_id"),
            _accepted(Batch.qty_on_hand).label("on_hand"),
            _accepted(Batch.qty_reserved).label("reserved"),
            func.coalesce(
                func.sum(case((Batch.status == BatchStatus.ON_CHECK, Batch.qty_on_hand), else_=0)), 0
            ).label("on_check"),
        )
        .group_by(Batch.material_id)
        .subquery()
    )


def _to_stock_out(material: Material, on_hand, reserved, on_check) -> MaterialStockOut:
    on_hand, reserved, on_check = (Decimal(v or ZERO) for v in (on_hand, reserved, on_check))
    return MaterialStockOut(
        id=material.id,
        code=material.code,
        name=material.name,
        unit=material.unit,
        qty_on_hand=on_hand,
        qty_reserved=reserved,
        qty_free=on_hand - reserved,
        qty_on_check=on_check,
    )


async def list_with_stock(
    session: AsyncSession, user: User, search: str | None = None
) -> list[MaterialStockOut]:
    require(user, Perm.VIEW_STOCK)
    stock = _stock_subquery()
    query = select(Material, stock.c.on_hand, stock.c.reserved, stock.c.on_check).outerjoin(
        stock, stock.c.material_id == Material.id
    )
    if search:
        pattern = like_pattern(search)
        query = query.where(
            or_(Material.code.ilike(pattern, escape="\\"), Material.name.ilike(pattern, escape="\\"))
        )
    rows = await session.execute(query.order_by(Material.code))
    return [_to_stock_out(*row) for row in rows]


async def find(session: AsyncSession, user: User, query: str) -> list[MaterialStockOut]:
    """Поиск по-человечески: точный код или название в любой форме и порядке слов."""
    everything = await list_with_stock(session, user)
    code = query.strip().upper()
    if exact := [m for m in everything if m.code == code]:
        return exact
    return [m for m in everything if same_text(query, f"{m.name} {m.code}")]


async def find_similar(session: AsyncSession, user: User, query: str) -> list[MaterialStockOut]:
    """Запасной поиск с опечатками, когда find() ничего не нашёл: «соевго воска» → «Воск соевый»."""
    return [m for m in await list_with_stock(session, user) if similar_text(query, m.name)]


async def get_stock(session: AsyncSession, user: User, material_id: int) -> MaterialStockOut:
    require(user, Perm.VIEW_STOCK)
    stock = _stock_subquery()
    row = (
        await session.execute(
            select(Material, stock.c.on_hand, stock.c.reserved, stock.c.on_check)
            .outerjoin(stock, stock.c.material_id == Material.id)
            .where(Material.id == material_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Материал не найден.")
    return _to_stock_out(*row)


async def create(session: AsyncSession, user: User, data: MaterialCreate) -> MaterialOut:
    require(user, Perm.MATERIAL_EDIT)
    if await session.scalar(select(Material.id).where(Material.code == data.code)):
        raise DomainError(f"Материал с кодом {data.code} уже есть.")
    material = Material(code=data.code, name=data.name, unit=data.unit)
    session.add(material)
    await session.flush()
    audit.record(
        session,
        user,
        entity="material",
        entity_id=material.id,
        action="created",
        after=audit.snapshot(material, ["code", "name", "unit"]),
    )
    return MaterialOut(id=material.id, code=material.code, name=material.name, unit=material.unit)
