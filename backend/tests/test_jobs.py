import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.clock import today
from app.db import SessionFactory
from app.errors import DomainError, InvalidInputError, PermissionDeniedError
from app.models import Batch, BatchStatus, Job, JobStatus, Role, StockMovement
from app.schemas.inventory import ReceiptCreate, ReceiptLineIn
from app.schemas.production import ActualItemIn, JobCompleteIn, JobIn, RecipeIn, RecipeItemIn
from app.services import analytics, batches, jobs, receipts, recipes
from tests.conftest import auth_headers

D = Decimal


async def add_batch(session, users, fx, code: str, qty: str, day: date, price="100", accept=True) -> int:
    receipt = await receipts.create_receipt(
        session,
        users[Role.STOREKEEPER],
        ReceiptCreate(
            supplier_name="Восковой двор",
            received_at=day,
            lines=[ReceiptLineIn(material_code=code, qty=D(qty), price=D(price), currency="RUB")],
        ),
        fx,
    )
    batch_id = receipt.batches[0].id
    if accept:
        await batches.change_status(session, users[Role.STOREKEEPER], batch_id, BatchStatus.ACCEPTED)
    await session.commit()
    return batch_id


@pytest.fixture
async def recipe(session, users, materials):
    """«Лаванда»: 0,2 кг воска и 1 стакан на свечу."""
    result = await recipes.save(
        session,
        users[Role.TECHNOLOGIST],
        RecipeIn(
            name="Свеча «Лаванда»",
            items=[
                RecipeItemIn(material_code="WAX-SOY", qty_per_unit=D("0.2")),
                RecipeItemIn(material_code="JAR-200", qty_per_unit=D("1")),
            ],
        ),
    )
    await session.commit()
    return result


async def planned_job(session, users, recipe, qty: int) -> Job:
    tech = users[Role.TECHNOLOGIST]
    job = await jobs.create_draft(
        session, tech, JobIn(recipe_id=recipe.id, qty=qty, planned_date=today() + timedelta(days=1))
    )
    await jobs.plan(session, tech, job.id)
    await session.commit()
    return job


async def batch_state(batch_id: int) -> tuple[Decimal, Decimal]:
    async with SessionFactory() as s:
        b = await s.get(Batch, batch_id)
        return b.qty_on_hand, b.qty_reserved


OLD, MID, NEW = date(2026, 9, 1), date(2026, 9, 10), date(2026, 9, 20)


async def test_fifo_takes_oldest_accepted_batches_first(session, users, materials, recipe, fx):
    # Создаём не по порядку дат: FIFO смотрит на дату поступления, а не на порядок записи.
    new = await add_batch(session, users, fx, "WAX-SOY", "10", NEW)
    old = await add_batch(session, users, fx, "WAX-SOY", "3", OLD)
    on_check = await add_batch(session, users, fx, "WAX-SOY", "50", OLD, accept=False)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)

    job = await planned_job(session, users, recipe, 20)  # нужно 4 кг воска

    assert await batch_state(old) == (D("3"), D("3"))
    assert await batch_state(new) == (D("10"), D("1"))
    assert (await batch_state(on_check))[1] == 0
    card = await jobs.get_card(session, users[Role.STOREKEEPER], job.id)
    assert {c.light for c in card.components} == {"green"}
    assert not card.has_deficit


async def test_shortage_gives_partial_reserve_deficit_and_blocks_start(session, users, materials, recipe, fx):
    wax = await add_batch(session, users, fx, "WAX-SOY", "1.5", OLD)
    job = await planned_job(session, users, recipe, 20)  # нужно 4 кг воска и 20 стаканов

    card = await jobs.get_card(session, users[Role.TECHNOLOGIST], job.id)
    lights = {c.material.code: (c.light, c.qty_reserved, c.qty_deficit) for c in card.components}
    assert lights["WAX-SOY"] == ("yellow", D("1.5"), D("2.5"))
    assert lights["JAR-200"] == ("red", D("0"), D("20"))

    with pytest.raises(DomainError) as error:
        await jobs.start(session, users[Role.TECHNOLOGIST], job.id)
    assert "2,5 кг — Воск соевый" in error.value.message
    await session.rollback()
    assert await batch_state(wax) == (D("1.5"), D("1.5"))


async def test_reserve_more_after_new_receipt(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "1.5", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)
    await add_batch(session, users, fx, "WAX-SOY", "10", NEW)

    await jobs.reserve_more(session, users[Role.TECHNOLOGIST], job.id)
    await session.commit()

    card = await jobs.get_card(session, users[Role.TECHNOLOGIST], job.id)
    assert not card.has_deficit
    assert [r.qty for r in card.reservations if r.material_code == "WAX-SOY"] == [D("1.5"), D("2.5")]
    # В журнале — только изменения, а не всё задание целиком.
    entry = card.history[-1]
    assert (entry.action, entry.before, entry.after) == (
        "reserved_more",
        {"deficit": {"WAX-SOY": "2.500"}, "added": None},
        {"deficit": {}, "added": {"WAX-SOY": "2.500"}},
    )
    with pytest.raises(DomainError, match="всё уже в резерве"):
        await jobs.reserve_more(session, users[Role.TECHNOLOGIST], job.id)


async def test_reserve_more_without_free_stock_is_refused_and_not_logged(
    session, users, materials, recipe, fx
):
    await add_batch(session, users, fx, "WAX-SOY", "1.5", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)
    history_before = len((await jobs.get_card(session, users[Role.TECHNOLOGIST], job.id)).history)

    with pytest.raises(DomainError, match=r"Дозарезервировать нечего.*Не хватает: 2,5 кг — Воск соевый"):
        await jobs.reserve_more(session, users[Role.TECHNOLOGIST], job.id)
    assert len((await jobs.get_card(session, users[Role.TECHNOLOGIST], job.id)).history) == history_before


async def test_two_parallel_plans_never_reserve_the_same_stock(session, users, materials, recipe, fx):
    """Воска хватает ровно на одно задание. Второе планируется, пока первое ещё не зафиксировано."""
    wax = await add_batch(session, users, fx, "WAX-SOY", "4", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    tech = users[Role.TECHNOLOGIST]
    first, second = [
        (await jobs.create_draft(session, tech, JobIn(recipe_id=recipe.id, qty=20, planned_date=today()))).id
        for _ in range(2)
    ]
    await session.commit()

    async def plan_second() -> None:
        async with SessionFactory() as s:
            await jobs.plan(s, tech, second)
            await s.commit()

    async with SessionFactory() as s:
        await jobs.plan(s, tech, first)  # строки партий заблокированы, транзакция открыта
        waiting = asyncio.create_task(plan_second())
        await asyncio.sleep(0.3)
        assert not waiting.done()  # второй запрос ждёт
        await s.commit()
    await waiting

    assert await batch_state(wax) == (D("4"), D("4"))
    reserved = await jobs.reserved_by_material(session, {first, second})
    wax_id = materials["WAX-SOY"].id
    assert reserved[(first, wax_id)] == D("4")
    assert (second, wax_id) not in reserved  # второму не досталось ничего


async def test_same_job_planned_twice_in_parallel_reserves_once(session, users, materials, recipe, fx):
    wax = await add_batch(session, users, fx, "WAX-SOY", "100", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    tech = users[Role.TECHNOLOGIST]
    job = await jobs.create_draft(session, tech, JobIn(recipe_id=recipe.id, qty=20, planned_date=today()))
    await session.commit()

    async def plan_in_own_session() -> None:
        async with SessionFactory() as s:
            await jobs.plan(s, tech, job.id)
            await s.commit()

    results = await asyncio.gather(*(plan_in_own_session() for _ in range(2)), return_exceptions=True)

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1
    assert "в статусе «Запланировано»" in errors[0].message
    assert await batch_state(wax) == (D("100"), D("4"))


async def test_start_writes_off_reserve(session, users, materials, recipe, fx):
    wax = await add_batch(session, users, fx, "WAX-SOY", "10", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)

    await jobs.start(session, users[Role.TECHNOLOGIST], job.id)
    await session.commit()

    assert await batch_state(wax) == (D("6"), D("0"))
    card = await jobs.get_card(session, users[Role.TECHNOLOGIST], job.id)
    assert card.status == JobStatus.IN_PROGRESS
    assert card.reservations == []


async def test_overuse_is_written_off_and_saving_returns_to_batches(session, users, materials, recipe, fx):
    old = await add_batch(session, users, fx, "WAX-SOY", "3", OLD)
    new = await add_batch(session, users, fx, "WAX-SOY", "10", NEW)
    jars = await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)  # 4 кг: 3 из старой, 1 из новой
    tech = users[Role.TECHNOLOGIST]
    await jobs.start(session, tech, job.id)

    await jobs.complete(
        session,
        tech,
        job.id,
        JobCompleteIn(
            produced_qty=19,
            actual=[
                ActualItemIn(material_code="WAX-SOY", qty=D("2.5")),  # сэкономили 1,5 кг
                ActualItemIn(material_code="JAR-200", qty=D("22")),  # разбили 2 стакана
            ],
        ),
    )
    await session.commit()

    # Экономия 1,5: сначала в новую партию (1 кг), остальное в старую (0,5 кг).
    assert await batch_state(new) == (D("10"), D("0"))
    assert await batch_state(old) == (D("0.5"), D("0"))
    assert await batch_state(jars) == (D("78"), D("0"))
    # Журнал движений сходится с остатком у каждой партии.
    for batch_id in (old, new, jars):
        moved = await session.scalar(
            select(func.sum(StockMovement.qty)).where(StockMovement.batch_id == batch_id)
        )
        assert moved == (await batch_state(batch_id))[0]


async def test_overuse_without_stock_is_rejected_with_text(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "4", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)
    tech = users[Role.TECHNOLOGIST]
    await jobs.start(session, tech, job.id)
    await session.commit()

    with pytest.raises(DomainError, match="Не хватает: 2,5 кг — Воск соевый"):
        await jobs.complete(
            session,
            tech,
            job.id,
            JobCompleteIn(produced_qty=20, actual=[ActualItemIn(material_code="WAX-SOY", qty=D("6.5"))]),
        )


async def test_cancel_releases_reserve_but_only_before_start(session, users, materials, recipe, fx):
    wax = await add_batch(session, users, fx, "WAX-SOY", "10", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    tech = users[Role.TECHNOLOGIST]
    first = await planned_job(session, users, recipe, 20)
    await jobs.cancel(session, tech, first.id)
    await session.commit()
    assert await batch_state(wax) == (D("10"), D("0"))

    second = await planned_job(session, users, recipe, 20)
    await jobs.start(session, tech, second.id)
    await session.commit()
    with pytest.raises(DomainError, match="Нельзя отменить: задание З-000002 в статусе «В работе»"):
        await jobs.cancel(session, tech, second.id)


async def test_draft_is_editable_and_planned_is_not(session, users, materials, recipe, fx):
    tech = users[Role.TECHNOLOGIST]
    data = JobIn(recipe_id=recipe.id, qty=5, planned_date=today())
    job = await jobs.create_draft(session, tech, data)
    await jobs.update_draft(session, tech, job.id, data.model_copy(update={"qty": 7}))
    assert (await session.get(Job, job.id)).qty == 7
    await jobs.plan(session, tech, job.id)
    with pytest.raises(DomainError, match="Нельзя изменить"):
        await jobs.update_draft(session, tech, job.id, data)


async def test_recipe_change_does_not_touch_planned_job(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "100", OLD)
    job = await planned_job(session, users, recipe, 10)
    await recipes.save(
        session,
        users[Role.MANAGER],
        RecipeIn(name=recipe.name, items=[RecipeItemIn(material_code="WAX-SOY", qty_per_unit=D("5"))]),
        recipe.id,
    )
    await session.commit()
    card = await jobs.get_card(session, users[Role.MANAGER], job.id)
    assert {c.material.code: c.qty_required for c in card.components} == {
        "WAX-SOY": D("2"),
        "JAR-200": D("10"),
    }


async def test_storekeeper_cannot_manage_jobs(client, session, users, materials, recipe):
    with pytest.raises(PermissionDeniedError):
        await jobs.create_draft(
            session, users[Role.STOREKEEPER], JobIn(recipe_id=recipe.id, qty=1, planned_date=today())
        )
    headers = await auth_headers(client, "storekeeper")
    response = await client.post(
        "/api/jobs",
        json={"recipe_id": recipe.id, "qty": 1, "planned_date": today().isoformat()},
        headers=headers,
    )
    assert response.status_code == 403


async def test_past_date_is_rejected(session, users, recipe):
    with pytest.raises(InvalidInputError, match="уже прошла"):
        await jobs.create_draft(
            session,
            users[Role.TECHNOLOGIST],
            JobIn(recipe_id=recipe.id, qty=1, planned_date=today() - timedelta(days=1)),
        )


async def test_deficit_report_for_period(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "1.5", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    await planned_job(session, users, recipe, 20)
    viewer = users[Role.STOREKEEPER]

    report = await analytics.deficit(session, viewer, today(), today() + timedelta(days=7))
    assert [(t.material.code, t.qty_deficit) for t in report.totals] == [("WAX-SOY", D("2.5"))]

    later = await analytics.deficit(
        session, viewer, today() + timedelta(days=30), today() + timedelta(days=40)
    )
    assert later.rows == [] and later.planned_jobs == 0


async def test_job_cost_uses_actual_batches(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "3", OLD, price="100")
    await add_batch(session, users, fx, "WAX-SOY", "10", NEW, price="200")
    await add_batch(session, users, fx, "JAR-200", "100", OLD, price="10")
    job = await planned_job(session, users, recipe, 20)
    tech = users[Role.TECHNOLOGIST]

    planned = await analytics.job_cost(session, tech, job.id, "RUB", fx)
    # 3 кг × 100 + 1 кг × 200 + 20 стаканов × 10 = 700
    assert planned.total == D("700.00")
    assert planned.per_unit == D("35.00")

    await jobs.start(session, tech, job.id)
    await jobs.complete(
        session,
        tech,
        job.id,
        JobCompleteIn(produced_qty=20, actual=[ActualItemIn(material_code="WAX-SOY", qty=D("5"))]),
    )
    done = await analytics.job_cost(session, tech, job.id, "RUB", fx)
    assert done.total == D("900.00")  # перерасход 1 кг × 200

    with pytest.raises(PermissionDeniedError):
        await analytics.job_cost(session, users[Role.STOREKEEPER], job.id, "RUB", fx)


async def test_job_cost_with_deficit_is_marked_incomplete(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "1.5", OLD, price="100")
    await add_batch(session, users, fx, "JAR-200", "100", OLD, price="10")
    job = await planned_job(session, users, recipe, 20)
    cost = await analytics.job_cost(session, users[Role.MANAGER], job.id, "RUB", fx)
    assert not cost.is_complete
    assert cost.per_unit is None
    assert cost.total == D("350.00")


async def test_recipe_cost_is_weighted_average(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "1", OLD, price="100")
    await add_batch(session, users, fx, "WAX-SOY", "3", NEW, price="200")
    await add_batch(session, users, fx, "JAR-200", "10", OLD, price="10")
    cost = await analytics.recipe_cost(session, users[Role.MANAGER], recipe.id, "RUB", fx)
    assert cost.per_unit == D("45.0000")  # 0,2 × 175 + 10


async def test_consumption_counts_issue_overuse_and_return(session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "10", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    job = await planned_job(session, users, recipe, 20)
    tech = users[Role.TECHNOLOGIST]
    await jobs.start(session, tech, job.id)
    await jobs.complete(
        session,
        tech,
        job.id,
        JobCompleteIn(produced_qty=20, actual=[ActualItemIn(material_code="WAX-SOY", qty=D("3.5"))]),
    )
    await session.commit()
    rows = await analytics.consumption(
        session, users[Role.STOREKEEPER], today(), today(), [materials["WAX-SOY"].id]
    )
    assert [(r.day, r.qty, r.cost_rub) for r in rows] == [(today(), D("3.5"), D("350.00"))]


async def test_job_lifecycle_over_api_with_audit(client, session, users, materials, recipe, fx):
    await add_batch(session, users, fx, "WAX-SOY", "10", OLD)
    await add_batch(session, users, fx, "JAR-200", "100", OLD)
    headers = await auth_headers(client, "technologist")
    body = {"recipe_id": recipe.id, "qty": 10, "planned_date": today().isoformat()}

    job = (await client.post("/api/jobs", json=body, headers=headers)).json()
    assert (job["number"], job["status_label"]) == ("З-000001", "Черновик")
    for step, label in [("plan", "Запланировано"), ("start", "В работе")]:
        job = (await client.post(f"/api/jobs/{job['id']}/{step}", headers=headers)).json()
        assert job["status_label"] == label
    job = (
        await client.post(f"/api/jobs/{job['id']}/complete", json={"produced_qty": 10}, headers=headers)
    ).json()
    assert job["status_label"] == "Завершено"
    assert job["marking"]["status"] == "pending"
    assert [h["action"] for h in job["history"]] == ["created", "planned", "started", "completed"]
    assert job["history"][1]["user_full_name"] == "Смирнова Анна Викторовна"
    assert job["history"][2]["before"] == {"status": "planned"}
    assert job["history"][2]["after"] == {"status": "in_progress"}
