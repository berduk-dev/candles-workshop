"""Ассистент целиком: HTTP → агент → инструменты → база. Модель — заглушка со сценарием."""

import asyncio
import io
import json
import logging
from datetime import date, timedelta
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select

from app.assistant.llm.base import (
    CodeRun,
    ContainerExpiredError,
    FileCreated,
    LLMError,
    TextDelta,
    ToolCall,
    ToolResult,
)
from app.clock import now, today
from app.db import SessionFactory
from app.models import Batch, Job, JobStatus, Message, PendingAction, Role, User, UserContainer, UserMemory
from app.services import jobs
from tests.conftest import PASSWORD_HASH, auth_headers
from tests.test_jobs import add_batch, recipe  # noqa: F401

PROPOSE = "propose_create_and_plan_job"
FRIDAY = (today() + timedelta(days=3)).isoformat()


def call(tool: str, call_id: str = "c1", **args) -> ToolCall:
    return ToolCall(call_id, tool, json.dumps(args, ensure_ascii=False))


async def new_chat(client, headers) -> int:
    return (await client.post("/api/chat/conversations", headers=headers)).json()["id"]


async def ask(client, headers, chat_id: int, text: str, **extra) -> list[dict]:
    response = await client.post(
        f"/api/chat/conversations/{chat_id}/messages", json={"text": text, **extra}, headers=headers
    )
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


async def messages(client, headers, chat_id: int) -> list[dict]:
    return (await client.get(f"/api/chat/conversations/{chat_id}/messages", headers=headers)).json()


def tool_outputs(provider) -> list[dict]:
    return [json.loads(i.output) for r in provider.requests for i in r.items if isinstance(i, ToolResult)]


def everything_sent(provider) -> str:
    """Всё, что ушло в модель: инструкции, история, результаты инструментов."""
    return "\n".join(r.instructions + repr(r.items) + repr(r.tools) for r in provider.requests)


@pytest.fixture
async def stock(session, users, materials, fx):
    await add_batch(session, users, fx, "WAX-SOY", "10", date(2026, 9, 1), price="100")
    await add_batch(session, users, fx, "JAR-200", "100", date(2026, 9, 1), price="10")


# ---------- чат ----------


async def test_answer_is_streamed_and_saved_with_tools_and_model(client, users, stock, stub_llm):
    stub_llm(
        [
            [call("get_material_stock", material="соевого воска")],
            [TextDelta("Свободно "), TextDelta("10 кг.")],
        ]
    )
    headers = await auth_headers(client, "storekeeper")
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, "Сколько соевого воска?")

    assert [e["type"] for e in events] == [
        "start", "model", "tool_started", "tool", "model", "text", "text", "done",
    ]  # fmt: skip
    question, answer = await messages(client, headers, chat_id)
    assert question["content"] == "Сколько соевого воска?"
    assert answer["content"] == "Свободно 10 кг."
    assert answer["status"] == "complete"
    assert answer["model"] == "stub-primary"
    assert [t["name"] for t in answer["meta"]["tools"]] == ["get_material_stock"]


async def test_fallback_model_is_marked_under_answer(client, users, stub_llm):
    stub_llm([[LLMError("429", retryable=True, status=429)]], fallback_steps=[[TextDelta("Я запасная.")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    await ask(client, headers, chat_id, "Привет")
    answer = (await messages(client, headers, chat_id))[-1]
    assert (answer["model"], answer["used_fallback"]) == ("stub-fallback", True)


class SlowProvider:
    """Выдаёт начало ответа и зависает — как длинная генерация."""

    model = "slow"

    def __init__(self):
        self.started = asyncio.Event()

    async def stream(self, request):
        yield TextDelta("Начало ответа")
        self.started.set()
        await asyncio.sleep(60)
        yield TextDelta(" конец")


async def test_stop_button_saves_partial_answer_as_interrupted(client, users, stub_llm):
    from app.api.chat import get_assistant
    from app.assistant.llm.router import ModelRouter
    from app.main import app

    stub_llm([])
    deps = app.dependency_overrides[get_assistant]()
    provider = SlowProvider()
    slow = type(deps)(ModelRouter([provider], call_timeout=60), deps.containers, deps.sessions, deps.fx)
    app.dependency_overrides[get_assistant] = lambda: slow

    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    asking = asyncio.create_task(ask(client, headers, chat_id, "Расскажи подробно"))
    await provider.started.wait()

    busy = await client.post(
        f"/api/chat/conversations/{chat_id}/messages", json={"text": "ещё"}, headers=headers
    )
    assert busy.status_code == 409

    stopped = await client.post(f"/api/chat/conversations/{chat_id}/stop", headers=headers)
    assert stopped.json() == {"stopped": True}
    events = await asking
    assert events[-1] == {"type": "done", "status": "interrupted"}
    answer = (await messages(client, headers, chat_id))[-1]
    assert (answer["content"], answer["status"]) == ("Начало ответа", "interrupted")


async def test_disconnect_saves_partial_answer_as_interrupted(client, session, users, stub_llm):
    from app.api.chat import get_assistant
    from app.assistant import chat as chat_service
    from app.assistant.llm.router import ModelRouter
    from app.main import app

    stub_llm([])
    deps = app.dependency_overrides[get_assistant]()
    provider = SlowProvider()
    slow = type(deps)(ModelRouter([provider], call_timeout=60), deps.containers, deps.sessions, deps.fx)
    user = users[Role.MANAGER]
    conversation = await chat_service.create(session, user)
    _, answer = await chat_service.start_answer(session, user, conversation.id, "Вопрос", [])
    await session.commit()

    stream = chat_service.launch(slow, user, conversation.id, answer.id)
    async for chunk in stream:
        if "Начало" in chunk:
            break
    await stream.aclose()  # браузер закрыл соединение

    async with SessionFactory() as s:
        saved = await s.get(Message, answer.id)
    assert (saved.content, saved.status) == ("Начало ответа", "interrupted")


async def test_model_error_becomes_human_text(client, users, stub_llm):
    stub_llm([[LLMError("Модель не смогла обработать запрос.", retryable=False, status=400)]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, "Привет")
    assert {"type": "error", "text": "Модель не смогла обработать запрос."} in events
    answer = (await messages(client, headers, chat_id))[-1]
    assert answer["status"] == "error"


async def test_second_empty_chat_is_not_created(client, users, stub_llm):
    stub_llm([[TextDelta("ок")]])
    headers = await auth_headers(client, "manager")
    first = await new_chat(client, headers)
    assert await new_chat(client, headers) == first
    # У другого пользователя пустой диалог свой.
    assert await new_chat(client, await auth_headers(client, "technologist")) != first
    # После вопроса диалог уже не пустой — можно начать новый.
    await ask(client, headers, first, "Привет")
    assert await new_chat(client, headers) != first


async def test_other_users_chat_is_invisible(client, users, stub_llm):
    stub_llm([[TextDelta("ок")]])
    mine = await auth_headers(client, "manager")
    chat_id = await new_chat(client, mine)
    other = await auth_headers(client, "technologist")
    response = await client.get(f"/api/chat/conversations/{chat_id}/messages", headers=other)
    assert response.status_code == 404
    response = await client.post(
        f"/api/chat/conversations/{chat_id}/messages", json={"text": "привет"}, headers=other
    )
    assert response.status_code == 404


# ---------- инструменты: аргументы и права ----------


@pytest.mark.parametrize(
    ("tool", "raw", "expected"),
    [
        ("get_material_stock", "не json", "не корректный JSON"),
        ("get_material_stock", "{}", "обязательное поле"),
        ("get_material_stock", '{"material": "воск", "sql": "DROP TABLE"}', "лишнее поле"),
        ("get_material_stock", '{"material": "золото"}', "Материал «золото» не найден"),
        (PROPOSE, '{"recipe": "Лаванда", "qty": -5, "planned_date": "2030-01-01"}', "больше 0"),
        (PROPOSE, '{"recipe": "Лаванда", "qty": 5, "planned_date": "2020-01-01"}', "уже прошла"),
        (PROPOSE, '{"recipe": "Роза", "qty": 5, "planned_date": "2030-01-01"}', "не найден"),
        ("get_deficit", '{"date_from": "2026-12-31", "date_to": "2026-01-01"}', "позже конца"),
        ("drop_tables", "{}", "не существует"),
    ],
)  # fmt: skip
async def test_tool_arguments_are_validated(client, users, stock, recipe, stub_llm, tool, raw, expected):  # noqa: F811
    provider = stub_llm([[ToolCall("c1", tool, raw)], [TextDelta("Не получилось.")]])
    headers = await auth_headers(client, "manager")
    await ask(client, headers, await new_chat(client, headers), "вопрос")
    assert expected in tool_outputs(provider)[0]["error"]


async def test_role_rights_apply_through_assistant(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    """Кладовщику инструмент не показан, но модель всё равно его вызвала — сервис отказывает."""
    provider = stub_llm(
        [
            [call("propose_create_and_plan_job", recipe="Лаванда", qty=5, planned_date=FRIDAY)],
            [call("get_job_cost", job_number="З-1")],
            [call("prepare_dataset", kind="recipe_cost", recipe="Лаванда")],
            [TextDelta("Нельзя.")],
        ]
    )
    job = await jobs.create_draft(session, users[Role.MANAGER], _job_in(recipe))
    await session.commit()
    headers = await auth_headers(client, "storekeeper")
    await ask(client, headers, await new_chat(client, headers), "Запланируй 5 свечей")

    offered = {t.name for t in provider.requests[0].tools}
    assert "propose_create_and_plan_job" not in offered and "get_job_cost" not in offered
    assert "get_material_stock" in offered
    errors = [o["error"] for o in tool_outputs(provider)[-3:]]
    assert all("Недостаточно прав" in e for e in errors), errors
    assert await session.scalar(select(func.count(PendingAction.id))) == 0
    await session.refresh(job)
    assert job.status == JobStatus.DRAFT


def _job_in(recipe, qty=5):  # noqa: F811
    from app.schemas.production import JobIn

    return JobIn(recipe_id=recipe.id, qty=qty, planned_date=today())


# ---------- действия ----------


async def propose(client, headers, stub_llm, qty=20) -> tuple[int, dict, object]:
    provider = stub_llm(
        [
            [call("propose_create_and_plan_job", recipe="лаванда", qty=qty, planned_date=FRIDAY)],
            [TextDelta("Подтвердите, пожалуйста.")],
        ]
    )
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, f"Запланируй {qty} свечей «Лаванда» на пятницу")
    return chat_id, next(e["action"] for e in events if e["type"] == "action"), provider


async def test_action_changes_nothing_until_confirmed(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    headers = await auth_headers(client, "technologist")
    chat_id, action, provider = await propose(client, headers, stub_llm)

    assert "Свеча «Лаванда», 20 шт" in action["preview"]["title"]
    assert action["preview"]["components"][0]["required"] in ("4 кг", "20 шт")
    assert "ЖДЁТ подтверждения" in tool_outputs(provider)[0]["status"]
    assert await session.scalar(select(func.count(Job.id))) == 0
    assert await session.scalar(select(func.sum(Batch.qty_reserved))) == 0

    confirmed = await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers)
    assert confirmed.json()["status"] == "confirmed"
    assert "З-000001 создано и запланировано" in confirmed.json()["result"]
    job = (await session.scalars(select(Job))).one()
    assert (job.qty, job.status, job.planned_date.isoformat()) == (20, JobStatus.PLANNED, FRIDAY)
    assert await session.scalar(select(func.sum(Batch.qty_reserved))) == Decimal("24")
    # Итог виден в диалоге.
    assert "З-000001" in (await messages(client, headers, chat_id))[-1]["content"]


async def test_double_confirm_creates_one_job(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    headers = await auth_headers(client, "technologist")
    _, action, _ = await propose(client, headers, stub_llm)
    responses = await asyncio.gather(
        *(client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers) for _ in range(2))
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert await session.scalar(select(func.count(Job.id))) == 1


async def test_action_of_another_user_cannot_be_confirmed(client, users, stock, recipe, stub_llm):  # noqa: F811
    headers = await auth_headers(client, "technologist")
    _, action, _ = await propose(client, headers, stub_llm)
    other = await auth_headers(client, "manager")
    response = await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=other)
    assert response.status_code == 404


async def test_rights_are_checked_again_on_confirm(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    headers = await auth_headers(client, "technologist")
    _, action, _ = await propose(client, headers, stub_llm)
    user = await session.get(User, users[Role.TECHNOLOGIST].id, populate_existing=True)
    user.role = Role.STOREKEEPER  # пока карточка висела, человека перевели на склад
    await session.commit()
    response = (await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers)).json()
    assert response["status"] == "failed" and "Недостаточно прав" in response["result"]
    assert await session.scalar(select(func.count(Job.id))) == 0


async def test_expired_and_rejected_actions_do_nothing(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    headers = await auth_headers(client, "technologist")
    _, action, _ = await propose(client, headers, stub_llm)
    row = await session.get(PendingAction, action["id"])
    row.expires_at = now() - timedelta(minutes=1)
    await session.commit()
    expired = (await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers)).json()
    assert expired["status"] == "expired"

    _, action, _ = await propose(client, headers, stub_llm)
    rejected = (await client.post(f"/api/chat/actions/{action['id']}/reject", headers=headers)).json()
    assert rejected["status"] == "rejected"
    again = await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers)
    assert again.status_code == 409
    assert await session.scalar(select(func.count(Job.id))) == 0


async def test_cancel_job_needs_confirmation_too(client, session, users, stock, recipe, stub_llm):  # noqa: F811
    job = await jobs.create_draft(session, users[Role.TECHNOLOGIST], _job_in(recipe))
    await session.commit()
    stub_llm([[call("propose_cancel_job", job_number="з-1")], [TextDelta("Подтвердите.")]])
    headers = await auth_headers(client, "technologist")
    events = await ask(client, headers, await new_chat(client, headers), "Отмени З-1")
    action = next(e["action"] for e in events if e["type"] == "action")
    await session.refresh(job)
    assert job.status == JobStatus.DRAFT
    await client.post(f"/api/chat/actions/{action['id']}/confirm", headers=headers)
    await session.refresh(job)
    assert job.status == JobStatus.CANCELLED


# ---------- персональные данные ----------


async def test_names_never_reach_model_and_come_back_to_user(client, users, stock, stub_llm, caplog):
    caplog.set_level(logging.DEBUG)
    provider = stub_llm(
        [
            [call("get_batch_history", batch_number="П-1")],
            [call("list_batches", "c2", supplier="SUP-001")],
            [TextDelta("Партию от SUP"), TextDelta("-001 принял EMP-00"), TextDelta("1.")],
        ]
    )
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, "Что пришло от «Восковой двор, ООО» и что сделал Петров И.?")

    sent = everything_sent(provider)
    for secret in ("Восков", "Петров", "Кузнецов", "Смирнова"):
        assert secret not in sent
    assert "SUP-001" in sent and "EMP-001" in sent
    # Псевдоним из аргументов модели развернулся в имя до обращения к сервису.
    assert tool_outputs(provider)[-1]["total"] == 2

    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert text == "Партию от Восковой двор принял Петров Иван Сергеевич."
    assert (await messages(client, headers, chat_id))[-1]["content"] == text
    for secret in ("Восков", "Петров"):
        assert secret not in caplog.text


async def test_second_question_masks_history(client, users, stock, stub_llm):
    provider = stub_llm([[TextDelta("Это SUP-001.")], [TextDelta("Да.")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    await ask(client, headers, chat_id, "Кто поставщик воска?")
    await ask(client, headers, chat_id, "Точно?")
    sent = everything_sent(provider)
    assert "Восков" not in sent
    assert "Это SUP-001." in sent


# ---------- память ----------


async def test_memory_is_isolated_between_users(client, session, users, stub_llm):
    session.add(
        User(
            login="technologist2",
            full_name="Волкова Мария Андреевна",
            role=Role.TECHNOLOGIST,
            password_hash=PASSWORD_HASH,
        )
    )
    await session.commit()
    first = await auth_headers(client, "technologist")
    stub_llm([[call("save_user_fact", fact="Работает в синем цехе")], [TextDelta("Запомнил.")]])
    await ask(client, first, await new_chat(client, first), "Запомни: я из синего цеха")

    # Новый диалог того же пользователя: факт в промпте.
    provider = stub_llm([[TextDelta("ок")]])
    await ask(client, first, await new_chat(client, first), "Привет")
    assert "Работает в синем цехе" in provider.requests[0].instructions

    # Другой пользователь с той же ролью: чужого факта нет ни в промпте, ни в списке.
    second = await auth_headers(client, "technologist2")
    provider = stub_llm([[TextDelta("ок")]])
    await ask(client, second, await new_chat(client, second), "Привет")
    assert "синем" not in everything_sent(provider)
    assert (await client.get("/api/chat/memory", headers=second)).json() == []

    # Чужой факт нельзя удалить, свой — можно.
    fact = (await client.get("/api/chat/memory", headers=first)).json()[0]
    assert (await client.delete(f"/api/chat/memory/{fact['id']}", headers=second)).status_code == 404
    assert (await client.delete(f"/api/chat/memory/{fact['id']}", headers=first)).status_code == 204
    assert await session.scalar(select(func.count(UserMemory.id))) == 0


# ---------- Code Interpreter и файлы ----------


async def test_dataset_goes_to_container_masked_and_files_come_back(
    client, users, stock, stub_llm, containers
):
    containers.generated = {"f1": b"\x89PNG", "f2": b"supplier\nSUP-001\n"}
    stub_llm(
        [
            [call("prepare_dataset", kind="batches")],
            [
                CodeRun("print(1)", "1", False, "cntr_1"),
                TextDelta("Готово."),
                FileCreated("cntr_1", "f1", "chart.png"),
                FileCreated("cntr_1", "f2", "report.csv"),
            ],
        ]
    )
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, "Построй график")

    dataset = containers.files["cntr_1"]["batches.csv"].decode()
    assert "SUP-001" in dataset and "Восков" not in dataset
    assert "WAX-SOY" in dataset
    answer = (await messages(client, headers, chat_id))[-1]
    assert answer["meta"]["code"] == [{"code": "print(1)", "logs": "1", "failed": False}]
    assert answer["meta"]["usage"]["containers"] == 1
    assert [f["filename"] for f in answer["files"]] == ["chart.png", "report.csv"]
    # В таблице, которую создал код, имена возвращены; для картинки есть расшифровка кодов.
    report = await client.get(answer["files"][1]["url"], headers=headers)
    assert "Восковой двор" in report.content.decode("utf-8-sig")
    assert answer["meta"]["legend"]["SUP-001"] == "Восковой двор"
    assert any(e["type"] == "file" for e in events)

    other = await auth_headers(client, "technologist")
    assert (await client.get(answer["files"][0]["url"], headers=other)).status_code == 404


async def test_code_error_does_not_break_chat(client, users, stub_llm):
    stub_llm([[CodeRun("1/0", "Traceback (most recent call last)", True, "cntr_1"), TextDelta("Не вышло.")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    events = await ask(client, headers, chat_id, "Посчитай")
    assert any(e["type"] == "notice" for e in events)
    answer = (await messages(client, headers, chat_id))[-1]
    assert answer["status"] == "complete" and answer["meta"]["code"][0]["failed"]


async def test_expired_container_is_recreated(client, users, stock, stub_llm, containers):
    provider = stub_llm(
        [
            [call("prepare_dataset", kind="stock")],
            [ContainerExpiredError()],
            [TextDelta("Готово.")],
        ]
    )
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    await ask(client, headers, chat_id, "Посчитай")
    assert [r.container_id for r in provider.requests] == [None, "cntr_1", "cntr_2"]
    assert (await messages(client, headers, chat_id))[-1]["status"] == "complete"


async def test_dead_container_is_not_reused_for_plain_question(
    client, session, users, stock, stub_llm, containers
):
    """Диалог, где давно считали: обычный вопрос не должен ни ловить ошибку, ни заводить новый контейнер."""
    provider = stub_llm([[call("get_material_stock", material="воск")], [TextDelta("Свободно 10 кг.")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    user_id = users[Role.MANAGER].id
    session.add(UserContainer(user_id=user_id, container_id="cntr_dead", used_at=now() - timedelta(hours=1)))
    await session.commit()

    await ask(client, headers, chat_id, "Сколько воска?")

    assert [r.container_id for r in provider.requests] == [None, None]
    assert containers.files == {}
    answer = (await messages(client, headers, chat_id))[-1]
    assert answer["meta"]["usage"]["containers"] == 0


async def test_container_is_shared_by_users_dialogs_but_not_between_users(
    client, users, stock, stub_llm, containers
):
    """Новый диалог того же человека продолжает его контейнер; у другого человека — свой."""
    script = [[call("prepare_dataset", kind="stock")], [TextDelta("Готово.")]]
    provider = stub_llm(script * 3)
    manager = await auth_headers(client, "manager")
    for _ in range(2):
        await ask(client, manager, await new_chat(client, manager), "Посчитай")
    technologist = await auth_headers(client, "technologist")
    await ask(client, technologist, await new_chat(client, technologist), "Посчитай")

    assert list(containers.files) == ["cntr_1", "cntr_2"]  # два человека — два контейнера, не три
    assert [r.container_id for r in provider.requests] == [None, "cntr_1", "cntr_1", "cntr_1", None, "cntr_2"]
    chats = (await client.get("/api/chat/conversations", headers=manager)).json()
    second = (await messages(client, manager, chats[0]["id"]))[-1]
    assert second["meta"]["usage"]["containers"] == 0  # платный контейнер в этом ответе не заводили


async def test_uploads_follow_user_to_a_new_container(client, session, users, stub_llm, containers):
    stub_llm([[TextDelta("ок")], [TextDelta("ок")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    file = (await upload(client, headers, chat_id, "data.csv", b"a;b\n1;2\n")).json()
    await ask(client, headers, chat_id, "Разбери файл", file_ids=[file["id"]])
    # Контейнер закрылся по простою — при следующем вопросе файл кладётся в новый.
    row = await session.get(UserContainer, users[Role.MANAGER].id)
    row.used_at = now() - timedelta(hours=1)
    await session.commit()
    await ask(client, headers, chat_id, "Ещё раз")
    assert [list(files) for files in containers.files.values()] == [["data.csv"], ["data.csv"]]


async def upload(client, headers, chat_id, name, content):
    return await client.post(
        f"/api/chat/conversations/{chat_id}/files", files={"file": (name, content)}, headers=headers
    )


async def test_uploaded_files_are_masked_before_container(client, users, stock, stub_llm, containers):
    provider = stub_llm([[TextDelta("ок")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    csv_text = "Поставщик;Принял;Сумма\nВосковой двор, ООО;Петров И.;100\nООО «Ромашка»;Анна;5\n"
    csv_file = (await upload(client, headers, chat_id, "Петров.csv", csv_text.encode("cp1251"))).json()
    book = Workbook()
    book.active.append(["ООО «Восковой двор»", 12.5])
    buffer = io.BytesIO()
    book.save(buffer)
    xlsx_file = (await upload(client, headers, chat_id, "data.xlsx", buffer.getvalue())).json()

    await ask(client, headers, chat_id, "Разбери файлы", file_ids=[csv_file["id"], xlsx_file["id"]])

    stored = containers.files["cntr_1"]
    text = stored[csv_file["filename"]].decode()
    assert "SUP-001;EMP-001;100" in text
    assert not any(secret in text for secret in ("Восков", "Петров", "Ромашка"))
    sheet = load_workbook(io.BytesIO(stored["data.xlsx"])).active
    assert [c.value for c in sheet[1]] == ["SUP-001", 12.5]
    assert "/mnt/data/data.xlsx" in provider.requests[0].instructions
    assert "Петров" not in everything_sent(provider)
    question = (await messages(client, headers, chat_id))[0]
    assert len(question["files"]) == 2


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("virus.exe", b"x", "только файлы CSV и XLSX"),
        ("empty.csv", b"", "Файл пустой"),
        ("broken.xlsx", b"not a zip", "повреждён"),
        ("big.csv", b"a" * (5 * 1024 * 1024 + 1), "больше 5 МБ"),
    ],
)
async def test_bad_uploads_are_rejected_politely(client, users, stub_llm, name, content, message):
    stub_llm([])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    response = await upload(client, headers, chat_id, name, content)
    assert response.status_code == 422
    assert message in response.json()["detail"]


async def test_old_files_of_shared_container_are_not_attached(client, users, stock, stub_llm, containers):
    """Модель не сослалась на файл: берём созданное за этот ответ, а не старый график другого диалога."""
    containers.generated = {"old.png": b"\x89PNG old", "new.png": b"\x89PNG new"}
    containers.created_at = {"old.png": 0.0}  # создан давно, в другом диалоге
    stub_llm([[CodeRun("plt.savefig('/mnt/data/chart.png')", "", False, "cntr_1"), TextDelta("Готово.")]])
    headers = await auth_headers(client, "manager")
    chat_id = await new_chat(client, headers)
    await ask(client, headers, chat_id, "Построй график")
    answer = (await messages(client, headers, chat_id))[-1]
    assert [f["filename"] for f in answer["files"]] == ["new.png"]
    assert containers.listed == 1


async def test_pure_calculation_does_not_look_for_files(client, users, stub_llm, containers):
    stub_llm([[CodeRun("print(59.16 * 1.15)", "68.03", False, "cntr_1"), TextDelta("68,03 ₽")]])
    headers = await auth_headers(client, "manager")
    await ask(client, headers, await new_chat(client, headers), "Посчитай")
    assert containers.listed == 0


def test_reading_a_file_is_not_saving_one():
    from app.assistant.agent import SAVES_FILE

    assert not SAVES_FILE.search("df = pd.read_csv('/mnt/data/abc-stock.csv')\nprint(df.sum())")
    assert SAVES_FILE.search("plt.savefig('/mnt/data/chart.png')")
    assert SAVES_FILE.search("df.to_excel('/mnt/data/report.xlsx')")
