"""Опечатки в названиях и просроченные задания — два случая, на которых ассистент ошибался в эвалах."""

import json
from datetime import date, timedelta

from app.assistant.llm.base import TextDelta, ToolCall, ToolResult
from app.assistant.masking import Masker
from app.clock import today
from app.models import Job, Role
from app.schemas.production import JobIn
from app.services import jobs
from app.services import materials as materials_service
from app.services.morph import similar, similar_text
from tests.conftest import auth_headers
from tests.test_assistant import ask, new_chat, stock  # noqa: F401
from tests.test_jobs import add_batch, recipe  # noqa: F401
from tests.test_masking import SUPPLIERS, USERS


def test_similar_forgives_small_typos_only():
    assert similar("двара", frozenset({"двор"}))
    assert similar("Васкового", frozenset({"восковой"}))
    assert not similar("двери", frozenset({"двор"}))  # две ошибки в коротком слове — уже другое слово
    assert not similar("2000", frozenset({"3000"}))  # в числах опечаток не бывает
    assert similar_text("соевго воска", "Воск соевый")
    assert not similar_text("кокосовый воск", "Воск соевый")


def test_supplier_with_typo_is_masked():
    masker = Masker(USERS, SUPPLIERS)
    assert masker.mask_text("Сколько партий от Васкового двара?") == "Сколько партий от SUP-001?"
    assert masker.mask_text("Счёт от ООО «Стиклотара Плюс»") == "Счёт от SUP-003"


def test_typos_do_not_hide_ordinary_words():
    masker = Masker(USERS, SUPPLIERS)
    text = "Восковые свечи во дворе, плюс стеклотара отдельно, оттиск на этикетке"
    assert masker.mask_text(text) == text


async def test_material_with_typo_is_found_as_similar(session, users, materials):
    user = users[Role.STOREKEEPER]
    assert await materials_service.find(session, user, "соевго воска") == []
    found = await materials_service.find_similar(session, user, "соевго воска")
    assert [m.code for m in found] == ["WAX-SOY"]


def tool_outputs(provider) -> list[dict]:
    return [json.loads(i.output) for r in provider.requests for i in r.items if isinstance(i, ToolResult)]


async def test_supplier_typo_in_tool_finds_batches_and_says_so(client, users, stock, stub_llm):  # noqa: F811
    provider = stub_llm(
        [[ToolCall("c1", "list_batches", '{"supplier": "Васковый двар"}')], [TextDelta("Две партии.")]]
    )
    headers = await auth_headers(client, "manager")
    await ask(client, headers, await new_chat(client, headers), "Партии от Васкового двара?")
    output = tool_outputs(provider)[0]
    assert output["total"] == 2
    assert "показаны партии похожего: SUP-001" in output["note"]  # имя для модели уже скрыто


async def test_overdue_means_past_date_and_not_finished(client, session, users, materials, recipe, fx):  # noqa: F811
    await add_batch(session, users, fx, "WAX-SOY", "100", date(2026, 9, 1))
    await add_batch(session, users, fx, "JAR-200", "100", date(2026, 9, 1))
    tech = users[Role.TECHNOLOGIST]
    created = {}
    for name in ("draft", "planned", "running", "future"):
        job = await jobs.create_draft(session, tech, JobIn(recipe_id=recipe.id, qty=1, planned_date=today()))
        created[name] = job
    for name in ("planned", "running", "future"):
        await jobs.plan(session, tech, created[name].id)
    await jobs.start(session, tech, created["running"].id)
    for name in ("draft", "planned", "running"):
        created[name].planned_date = today() - timedelta(days=2)
    created["future"].planned_date = today() + timedelta(days=2)
    await session.commit()

    page = await jobs.list_jobs(session, users[Role.STOREKEEPER], overdue=True)
    assert {j.number for j in page.items} == {created["planned"].number, created["running"].number}

    headers = await auth_headers(client, "storekeeper")
    response = await client.get("/api/jobs", params={"overdue": "true"}, headers=headers)
    assert response.json()["total"] == 2
    assert await session.get(Job, created["draft"].id)  # черновик в просроченные не попал
