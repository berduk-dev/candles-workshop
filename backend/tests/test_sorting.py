"""Сортировка кликом по заголовку в каждом списке и период в журнале изменений."""

from datetime import date, timedelta

from sqlalchemy import update

from app.clock import today
from app.models import AuditLog, Role
from app.schemas.production import JobIn
from app.services import jobs
from tests.conftest import auth_headers
from tests.test_jobs import add_batch, recipe  # noqa: F401


async def make_jobs(session, users, recipe) -> None:  # noqa: F811
    tech = users[Role.TECHNOLOGIST]
    for qty, days in ((30, 5), (10, 1), (20, 3)):
        await jobs.create_draft(
            session, tech, JobIn(recipe_id=recipe.id, qty=qty, planned_date=today() + timedelta(days=days))
        )
    await session.commit()


async def test_jobs_sort_by_any_column(client, session, users, materials, recipe, fx):  # noqa: F811
    await add_batch(session, users, fx, "WAX-SOY", "100", date(2026, 9, 1))
    await add_batch(session, users, fx, "JAR-200", "100", date(2026, 9, 1))
    await make_jobs(session, users, recipe)
    await jobs.plan(session, users[Role.TECHNOLOGIST], 2)  # З-000002 — запланировано, остальные черновики
    await session.commit()
    headers = await auth_headers(client, "storekeeper")

    async def numbers(sort: str) -> list[str]:
        page = (await client.get("/api/jobs", params={"sort": sort}, headers=headers)).json()
        return [j["number"] for j in page["items"]]

    assert await numbers("qty") == ["З-000002", "З-000003", "З-000001"]
    assert await numbers("-qty") == ["З-000001", "З-000003", "З-000002"]
    assert await numbers("planned_date") == ["З-000002", "З-000003", "З-000001"]
    # Статусы — по ходу работы: черновики раньше запланированных.
    assert (await numbers("status"))[-1] == "З-000002"
    bad = await client.get("/api/jobs", params={"sort": "password"}, headers=headers)
    assert (bad.status_code, bad.json()["detail"]) == (422, "Нельзя сортировать по полю «password».")


async def test_materials_sort_by_free_stock(client, session, users, materials, fx):
    await add_batch(session, users, fx, "WAX-SOY", "5", date(2026, 9, 1))
    await add_batch(session, users, fx, "JAR-200", "100", date(2026, 9, 1))
    headers = await auth_headers(client, "storekeeper")

    rows = (await client.get("/api/materials", params={"sort": "-qty_free"}, headers=headers)).json()
    assert [m["code"] for m in rows] == ["JAR-200", "WAX-SOY", "FR-LAV"]
    rows = (await client.get("/api/materials", params={"sort": "material"}, headers=headers)).json()
    assert [m["name"] for m in rows] == sorted(m["name"] for m in rows)


async def test_audit_period_and_sort(client, session, users, materials, recipe, fx):  # noqa: F811
    await make_jobs(session, users, recipe)
    # Первую запись журнала «сдвигаем» в прошлый месяц.
    first = await session.execute(
        update(AuditLog).where(AuditLog.id == 1).values(at=AuditLog.at - timedelta(days=40))
    )
    assert first.rowcount == 1
    await session.commit()
    headers = await auth_headers(client, "manager")
    month_start = today().replace(day=1)

    page = (
        await client.get("/api/audit", params={"date_from": month_start.isoformat()}, headers=headers)
    ).json()
    assert 1 not in [e["id"] for e in page["items"]]
    oldest_first = (await client.get("/api/audit", params={"sort": "at"}, headers=headers)).json()
    assert oldest_first["items"][0]["id"] == 1
