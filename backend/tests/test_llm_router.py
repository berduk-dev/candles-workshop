"""Переход на запасную модель. Проверяется через настоящий SDK OpenAI с подменённым транспортом (без сети)."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.assistant.llm.base import LLMError, LLMRequest, ModelStarted, TextDelta, Usage, UserText
from app.assistant.llm.openai_provider import OpenAIProvider
from app.assistant.llm.router import ModelRouter

REQUEST = LLMRequest(instructions="", items=[UserText("привет")])


def ok_stream(text: str) -> httpx.Response:
    events = [
        {"type": "response.output_text.delta", "delta": text, "item_id": "m", "output_index": 0},
    ]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def error(status: int) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": "x", "code": "some_code"}})


def broken_midway() -> httpx.Response:
    async def body():
        event = {"type": "response.output_text.delta", "delta": "Нач"}
        yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
        raise httpx.ReadError("обрыв")

    return httpx.Response(200, content=body(), headers={"content-type": "text/event-stream"})


def make_router(primary, calls: list[str], fallback=lambda: ok_stream("запасная")) -> ModelRouter:
    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        return primary() if model == "main" else fallback()

    client = AsyncOpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return ModelRouter([OpenAIProvider(client, "main"), OpenAIProvider(client, "spare")], call_timeout=5)


def timeout():
    raise httpx.ReadTimeout("долго")


def no_connection():
    raise httpx.ConnectError("нет связи")


async def collect(router: ModelRouter):
    return [e async for e in router.stream(REQUEST)]


def stream_of(*events: dict) -> httpx.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


async def test_working_notes_are_not_shown_as_answer():
    def message(item_id: str, phase: str) -> dict:
        item = {"id": item_id, "type": "message", "role": "assistant", "phase": phase, "content": []}
        return {"type": "response.output_item.added", "output_index": 0, "item": item}

    def delta(item_id: str, text: str) -> dict:
        return {"type": "response.output_text.delta", "item_id": item_id, "delta": text}

    reply = stream_of(
        message("a", "commentary"),
        delta("a", "Надо бы посчитать"),
        message("b", "final_answer"),
        delta("b", "Ответ"),
    )
    events = await collect(make_router(lambda: reply, []))
    assert events == [ModelStarted("main", False), TextDelta("Ответ")]


async def test_primary_answers_when_healthy():
    calls: list[str] = []
    events = await collect(make_router(lambda: ok_stream("основная"), calls))
    assert calls == ["main"]
    assert events == [ModelStarted("main", False), TextDelta("основная")]


@pytest.mark.parametrize(
    "failure",
    [
        lambda: error(429),
        lambda: error(500),
        lambda: error(503),
        timeout,
        no_connection,
    ],
    ids=["429", "500", "503", "timeout", "no-connection"],
)
async def test_falls_back_when_service_is_at_fault(failure):
    calls: list[str] = []
    events = await collect(make_router(failure, calls))
    assert calls == ["main", "spare"]
    assert events == [ModelStarted("spare", True), TextDelta("запасная")]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_bad_request_is_not_repeated(status):
    calls: list[str] = []
    with pytest.raises(LLMError) as exc:
        await collect(make_router(lambda: error(status), calls))
    assert calls == ["main"]
    assert not exc.value.retryable


async def test_hung_model_hits_call_deadline():
    class Hung:
        model = "main"

        async def stream(self, request):
            await asyncio.sleep(60)
            yield TextDelta("никогда")

    class Spare:
        model = "spare"

        async def stream(self, request):
            yield TextDelta("запасная")

    router = ModelRouter([Hung(), Spare()], call_timeout=0.1)
    assert await collect(router) == [ModelStarted("spare", True), TextDelta("запасная")]


class Slow:
    """Молчит, потом говорит, потом снова молчит."""

    def __init__(self, model: str, before: float, after: float = 0):
        self.model, self.before, self.after = model, before, after

    async def stream(self, request):
        await asyncio.sleep(self.before)
        yield TextDelta("раз")
        await asyncio.sleep(self.after)
        yield TextDelta("два")


async def test_silent_model_is_replaced_before_the_call_deadline():
    router = ModelRouter(
        [Slow("main", before=60), Slow("spare", before=0)], call_timeout=30, first_event_timeout=0.05
    )
    async with asyncio.timeout(5):
        events = await collect(router)
    assert events == [ModelStarted("spare", True), TextDelta("раз"), TextDelta("два")]


async def test_first_event_timeout_does_not_cut_started_answer_or_last_model():
    started = ModelRouter(
        [Slow("main", before=0, after=0.2), Slow("spare", before=0)], call_timeout=5, first_event_timeout=0.05
    )
    assert (await collect(started))[0] == ModelStarted("main", False)
    # У последней модели замены нет: лучше подождать её дольше, чем отказать.
    last = ModelRouter([Slow("only", before=0.2)], call_timeout=5, first_event_timeout=0.05)
    assert await collect(last) == [ModelStarted("only", False), TextDelta("раз"), TextDelta("два")]


@pytest.mark.parametrize(("tier", "sent"), [("fast", "fast"), ("", None)])
async def test_service_tier_is_sent_only_when_set(tier, sent):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return ok_stream("да")

    client = AsyncOpenAI(
        api_key="test",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await collect(ModelRouter([OpenAIProvider(client, "main", service_tier=tier)], call_timeout=5))
    assert bodies[0].get("service_tier") == sent


async def test_usage_tells_cache_and_reasoning():
    usage = {
        "input_tokens": 4000,
        "input_tokens_details": {"cached_tokens": 3000, "cache_write_tokens": 900},
        "output_tokens": 120,
        "output_tokens_details": {"reasoning_tokens": 80},
        "total_tokens": 4120,
    }
    response = {"id": "r", "object": "response", "output": [], "usage": usage}
    reply = stream_of({"type": "response.completed", "sequence_number": 1, "response": response})
    events = await collect(make_router(lambda: reply, []))
    assert events == [
        Usage(4000, 120, cached_tokens=3000, written_tokens=900, reasoning_tokens=80),
    ]


async def test_answer_is_never_glued_from_two_models():
    calls: list[str] = []
    router = make_router(broken_midway, calls)
    events = []
    with pytest.raises(LLMError, match="оборвалась посреди ответа"):
        async for event in router.stream(REQUEST):
            events.append(event)
    assert calls == ["main"]
    assert TextDelta("Нач") in events


async def test_both_models_down_gives_human_message():
    calls: list[str] = []
    with pytest.raises(LLMError, match="ни основная, ни запасная"):
        await collect(make_router(lambda: error(503), calls, fallback=lambda: error(503)))
    assert calls == ["main", "spare"]
