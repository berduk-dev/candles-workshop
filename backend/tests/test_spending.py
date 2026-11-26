"""«Сколько потратили» — два числа: закупки и расход в производство."""

import json
from datetime import date, timedelta
from decimal import Decimal

from app.assistant.llm.base import TextDelta, ToolCall, ToolResult
from app.clock import today
from app.models import Role
from app.schemas.production import ActualItemIn, JobCompleteIn
from app.services import analytics, jobs
from tests.conftest import auth_headers
from tests.test_assistant import ask, new_chat
from tests.test_jobs import add_batch, planned_job, recipe  # noqa: F401

D = Decimal


def first_tool_output(provider) -> dict:
    return next(json.loads(i.output) for r in provider.requests for i in r.items if isinstance(i, ToolResult))


async def test_spending_separates_purchases_from_consumption(session, users, materials, recipe, fx):  # noqa: F811
    day = today() - timedelta(days=3)
    await add_batch(session, users, fx, "WAX-SOY", "10", day, price="100")  # 1000 ₽
    await add_batch(session, users, fx, "WAX-SOY", "2", day, price="150", accept=False)  # на проверке: 300 ₽
    await add_batch(session, users, fx, "JAR-200", "100", day, price="10")  # 1000 ₽
    await add_batch(session, users, fx, "WAX-SOY", "50", date(2026, 1, 12), price="100")  # вне периода
    job = await planned_job(session, users, recipe, 20)  # 4 кг воска и 20 стаканов — старые партии первыми
    tech = users[Role.TECHNOLOGIST]
    await jobs.start(session, tech, job.id)
    await jobs.complete(
        session,
        tech,
        job.id,
        JobCompleteIn(produced_qty=20, actual=[ActualItemIn(material_code="WAX-SOY", qty=D("5"))]),
    )
    await session.commit()

    period = (today() - timedelta(days=30), today())
    result = await analytics.spending(session, users[Role.STOREKEEPER], *period)
    assert result.purchased.total_rub == D("2300.00")
    assert {r.material.code: r.cost_rub for r in result.purchased.by_material} == {
        "WAX-SOY": D("1300.00"),
        "JAR-200": D("1000.00"),
    }
    # Израсходовано 5 кг воска из старой партии по 100 ₽ и 20 стаканов по 10 ₽.
    assert result.consumed.total_rub == D("700.00")
    assert "закуплено" in result.purchased.meaning and "производство" in result.consumed.meaning
    assert "НЕЛЬЗЯ складывать" in result.warning

    only_wax = await analytics.spending(session, users[Role.STOREKEEPER], *period, [materials["WAX-SOY"].id])
    assert (only_wax.purchased.total_rub, only_wax.consumed.total_rub) == (D("1300.00"), D("500.00"))


async def test_spending_tool_answers_both_numbers(client, session, users, materials, fx, stub_llm):
    await add_batch(session, users, fx, "WAX-SOY", "10", today() - timedelta(days=2), price="100")
    await add_batch(session, users, fx, "JAR-200", "10", today() - timedelta(days=2), price="10")
    args = {"date_from": (today() - timedelta(days=30)).isoformat(), "date_to": today().isoformat()}
    provider = stub_llm(
        [[ToolCall("c1", "get_spending", json.dumps({**args, "material": "воск"}))], [TextDelta("Ответ.")]]
    )
    headers = await auth_headers(client, "manager")
    await ask(client, headers, await new_chat(client, headers), "Сколько потратили на воск?")
    output = next(
        json.loads(i.output) for r in provider.requests for i in r.items if isinstance(i, ToolResult)
    )
    assert output["purchased"]["total_rub"] == "1000"
    assert output["consumed"]["total_rub"] == "0"
    assert [r["material"]["code"] for r in output["purchased"]["by_material"]] == ["WAX-SOY"]


async def test_availability_tool_names_what_is_missing(
    client,
    session,
    users,
    materials,
    recipe,  # noqa: F811
    fx,
    stub_llm,
):
    """Кладовщик тоже может спросить «хватит ли»: это только чтение."""
    await add_batch(session, users, fx, "WAX-SOY", "1.5", date(2026, 9, 1))
    await add_batch(session, users, fx, "JAR-200", "100", date(2026, 9, 1))
    provider = stub_llm(
        [
            [ToolCall("c1", "check_availability", '{"recipe": "Лаванда", "qty": 20}')],
            [TextDelta("Не хватит.")],
        ]
    )
    headers = await auth_headers(client, "storekeeper")
    await ask(client, headers, await new_chat(client, headers), "Хватит на 20 свечей?")
    output = next(
        json.loads(i.output) for r in provider.requests for i in r.items if isinstance(i, ToolResult)
    )
    assert output["enough"] is False
    assert output["shortage"] == [{"material": "Воск соевый", "missing": "2,5 кг"}]
