from sqlalchemy import func, select

from app.models import Batch, Currency, Job, JobStatus, Material, Recipe, StockMovement, Supplier
from app.seed.demo import seed
from app.services import jobs


async def test_demo_data_is_complete_and_consistent(session, fx):
    assert await seed(session, fx) is True
    assert await seed(session, fx) is False  # второй раз ничего не делает

    assert await session.scalar(select(func.count(Material.id))) >= 10
    assert await session.scalar(select(func.count(Recipe.id))) == 3
    currencies = set(await session.scalars(select(Batch.currency).distinct()))
    assert currencies == set(Currency)
    # Три написания «Воскового двора» — один поставщик.
    names = list(await session.scalars(select(Supplier.name)))
    assert len([n for n in names if "Восков" in n]) == 1

    planned = set(await session.scalars(select(Job.id).where(Job.status == JobStatus.PLANNED)))
    assert len(await jobs.jobs_with_deficit(session, planned)) == 1

    # Остаток каждой партии равен сумме её движений.
    moved = dict(
        (
            await session.execute(
                select(StockMovement.batch_id, func.sum(StockMovement.qty)).group_by(StockMovement.batch_id)
            )
        )
        .tuples()
        .all()
    )
    for batch in await session.scalars(select(Batch)):
        assert moved[batch.id] == batch.qty_on_hand, batch.number
