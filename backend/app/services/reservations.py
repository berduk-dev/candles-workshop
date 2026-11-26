"""Резервирование по FIFO.

Защита от двойного резерва — блокировка строк партий (SELECT ... FOR UPDATE): второй запрос ждёт,
пока первый зафиксирует транзакцию, и видит уже уменьшенный остаток. Строки всегда блокируются
в одном порядке (материал, дата, id), поэтому взаимной блокировки не бывает.
"""

from collections.abc import Iterable, Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Batch, BatchStatus

ZERO = Decimal("0")


async def lock_batches(session: AsyncSession, material_ids: Iterable[int]) -> list[Batch]:
    """Блокирует партии материалов до конца транзакции. Порядок = порядок FIFO."""
    rows = await session.scalars(
        select(Batch)
        .where(Batch.material_id.in_(set(material_ids)))
        .order_by(Batch.material_id, Batch.received_at, Batch.id)
        .with_for_update()
        # Если партия уже загружена в сессию, перечитать её: за время ожидания остаток мог измениться.
        .execution_options(populate_existing=True)
    )
    return list(rows)


def free_qty(batch: Batch) -> Decimal:
    """Свободный остаток есть только у принятых партий."""
    return batch.qty_on_hand - batch.qty_reserved if batch.status == BatchStatus.ACCEPTED else ZERO


def allocate_fifo(batches: Sequence[Batch], material_id: int, need: Decimal) -> list[tuple[Batch, Decimal]]:
    """Раскладывает потребность по партиям, начиная с самой старой. Если не хватает — берёт сколько есть."""
    result: list[tuple[Batch, Decimal]] = []
    for batch in sorted(batches, key=lambda b: (b.received_at, b.id)):
        if need <= 0:
            break
        take = min(free_qty(batch), need) if batch.material_id == material_id else ZERO
        if take > 0:
            result.append((batch, take))
            need -= take
    return result
