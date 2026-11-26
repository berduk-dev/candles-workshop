import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import InvalidInputError
from app.models import Supplier
from app.services.morph import similar_text

# Организационно-правовые формы не входят в «ядро» названия.
LEGAL_FORMS = frozenset({"ооо", "оао", "зао", "пао", "ао", "ип", "нко", "llc", "ltd", "inc", "gmbh"})
_PUNCT = re.compile(r"[«»\"'“”„.,;:()\[\]]")


def supplier_norm_key(name: str) -> str:
    """«ООО «Восковой двор»», «Восковой двор, ООО», «восковой  двор» → «восковой двор»."""
    text = _PUNCT.sub(" ", name.lower().replace("ё", "е"))
    return " ".join(token for token in text.split() if token not in LEGAL_FORMS)


async def get_or_create(session: AsyncSession, name: str) -> Supplier:
    clean_name = " ".join(name.split())
    key = supplier_norm_key(clean_name)
    if not key:
        raise InvalidInputError("Укажите название поставщика.")
    # Вставка с ON CONFLICT: два одновременных поступления от нового поставщика не создадут дубль.
    await session.execute(
        insert(Supplier)
        .values(name=clean_name, norm_key=key)
        .on_conflict_do_nothing(index_elements=["norm_key"])
    )
    return (await session.scalars(select(Supplier).where(Supplier.norm_key == key))).one()


async def find_similar(session: AsyncSession, query: str) -> list[Supplier]:
    """Запасной поиск с опечатками: «Васковый двар» → «Восковой двор»."""
    key = supplier_norm_key(query)
    return [s for s in await list_suppliers(session) if key and similar_text(key, s.norm_key)]


async def list_suppliers(session: AsyncSession) -> list[Supplier]:
    return list(await session.scalars(select(Supplier).order_by(Supplier.name)))
