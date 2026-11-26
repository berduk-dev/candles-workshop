"""OpenAI Responses API. Единственное место, которое знает про SDK и формат OpenAI."""

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI

from app.assistant.llm.base import (
    AssistantText,
    CodeRun,
    CodeStarted,
    ContainerExpiredError,
    FileCreated,
    HistoryItem,
    LLMError,
    LLMEvent,
    LLMRequest,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
    UserText,
)

RETRYABLE_ERROR_CODES = {"server_error", "rate_limit_exceeded", "overloaded", "vector_store_timeout"}

# Соединение живёт между вопросами: новое (TCP + TLS) стоит около полсекунды, а по умолчанию httpx закрывает
# простаивающее уже через 5 секунд.
LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=300)
# Проверка живости на уровне TCP: молча оборванное соединение (NAT, VPN) ядро закроет само за минуту, и httpx
# уберёт его из пула. Набор опций зависит от ОС.
_TCP_PROBES = {"TCP_KEEPIDLE": 30, "TCP_KEEPALIVE": 30, "TCP_KEEPINTVL": 10, "TCP_KEEPCNT": 3}
SOCKET_OPTIONS = [
    (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),
    *(
        (socket.IPPROTO_TCP, getattr(socket, name), value)
        for name, value in _TCP_PROBES.items()
        if hasattr(socket, name)
    ),
]


def make_client(api_key: str) -> AsyncOpenAI:
    """Таймауты на каждый вызов; повторы SDK выключены — ими управляет ModelRouter."""
    timeout = httpx.Timeout(connect=5, read=90, write=30, pool=5)
    # trust_env=False: системный ALL_PROXY не должен влиять на приложение.
    transport = httpx.AsyncHTTPTransport(limits=LIMITS, socket_options=SOCKET_OPTIONS, trust_env=False)
    return AsyncOpenAI(
        api_key=api_key,
        max_retries=0,
        timeout=timeout,
        http_client=httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False),
    )


def translate_error(exc: Exception) -> LLMError:
    """Исключение SDK → наша ошибка с решением «повторять или нет»."""
    if isinstance(exc, openai.APITimeoutError | httpx.TimeoutException):
        return LLMError("Модель не ответила вовремя.", retryable=True)
    if isinstance(exc, openai.APIStatusError):
        status, detail = exc.status_code, str(exc.code or "")
        if "container" in str(exc.message).lower() and "expired" in str(exc.message).lower():
            return ContainerExpiredError()
        if status == 429 or status >= 500:
            return LLMError("Модель перегружена.", retryable=True, status=status, detail=detail)
        if status in (401, 403):
            return LLMError(
                "Ассистент не настроен: ключ API не принят.", retryable=False, status=status, detail=detail
            )
        return LLMError("Модель не смогла обработать запрос.", retryable=False, status=status, detail=detail)
    if isinstance(exc, openai.APIConnectionError | httpx.TransportError):
        return LLMError("Нет связи с моделью.", retryable=True)
    # Ошибка, пришедшая внутри потока: статуса HTTP уже нет, остаётся код.
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    return LLMError("Ошибка модели.", retryable=True, detail=code)


def _to_input(item: HistoryItem) -> dict[str, Any]:
    match item:
        case UserText(text):
            return {"role": "user", "content": text}
        case AssistantText(text):
            return {"role": "assistant", "content": text}
        case ToolCall(call_id, name, arguments):
            return {"type": "function_call", "call_id": call_id, "name": name, "arguments": arguments}
        case ToolResult(call_id, output):
            return {"type": "function_call_output", "call_id": call_id, "output": output}
        case CodeRun(code, logs, _, _):
            # История передаётся явно и не зависит от хранилища OpenAI, поэтому её поймёт и запасная модель.
            return {"role": "assistant", "content": f"[Выполнен код]\n{code}\n[Вывод]\n{logs[:4000]}"}


class OpenAIProvider:
    def __init__(self, client: AsyncOpenAI, model: str, service_tier: str = "", reasoning_effort: str = ""):
        self._client = client
        self.model = model
        self._service_tier = service_tier
        self._reasoning_effort = reasoning_effort

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "strict": False,
            }
            for t in request.tools
        ]
        params: dict[str, Any] = {"service_tier": self._service_tier} if self._service_tier else {}
        if self._reasoning_effort:
            params["reasoning"] = {"effort": self._reasoning_effort}
        if request.code_interpreter:
            # Контейнер создаётся, только если модель действительно решит запустить код.
            container = request.container_id or {"type": "auto"}
            tools.append({"type": "code_interpreter", "container": container})
            params["include"] = ["code_interpreter_call.outputs"]
        try:
            stream = await self._client.responses.create(
                model=self.model,
                instructions=request.instructions,
                input=[_to_input(i) for i in request.items],
                tools=tools,
                store=False,
                stream=True,
                **params,
            )
            # Модель может перед ответом писать рабочие заметки (phase="commentary"): пользователю они
            # не нужны. Показываем только сам ответ; заметки — лишь если ничего другого не пришло.
            notes: set[str] = set()
            note_text, answered = "", False
            async with stream:
                async for event in stream:
                    if event.type == "response.output_item.added":
                        if getattr(event.item, "phase", None) == "commentary":
                            notes.add(event.item.id)
                        answered |= event.item.type == "function_call"
                    elif event.type == "response.output_text.delta":
                        if event.item_id in notes:
                            note_text += event.delta
                            continue
                        answered = True
                    elif event.type == "response.completed" and note_text and not answered:
                        yield TextDelta(note_text)
                    for item in self._convert(event):
                        yield item
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            raise translate_error(exc) from exc

    @staticmethod
    def _convert(event: Any) -> list[LLMEvent]:
        match event.type:
            case "response.output_text.delta":
                return [TextDelta(event.delta)]
            case "response.code_interpreter_call.in_progress":
                return [CodeStarted()]
            case "response.output_item.done":
                return _convert_item(event.item)
            case "response.completed":
                return [_usage(event.response.usage)] if event.response.usage else []
            case "response.failed":
                error = event.response.error
                code = error.code if error else "unknown"
                raise LLMError(
                    "Модель вернула ошибку.", retryable=code in RETRYABLE_ERROR_CODES, detail=str(code)
                )
            case "error":
                if "container" in str(event.message).lower() and "expired" in str(event.message).lower():
                    raise ContainerExpiredError()
                raise LLMError(
                    "Модель вернула ошибку.",
                    retryable=event.code in RETRYABLE_ERROR_CODES,
                    detail=str(event.code),
                )
            case "response.incomplete":
                raise LLMError("Ответ получился слишком длинным и был оборван.", retryable=False)
        return []


def _usage(usage: Any) -> Usage:
    read, out = usage.input_tokens_details, usage.output_tokens_details
    return Usage(
        usage.input_tokens,
        usage.output_tokens,
        cached_tokens=read.cached_tokens if read else 0,
        written_tokens=getattr(read, "cache_write_tokens", 0) or 0,
        reasoning_tokens=out.reasoning_tokens if out else 0,
    )


def _convert_item(item: Any) -> list[LLMEvent]:
    if item.type == "function_call":
        return [ToolCall(item.call_id, item.name, item.arguments)]
    if item.type == "code_interpreter_call":
        logs = "\n".join(o.logs for o in item.outputs or [] if o.type == "logs")
        failed = item.status == "failed" or "Traceback (most recent call last)" in logs
        return [CodeRun(item.code or "", logs, failed, item.container_id)]
    if item.type == "message":
        return [
            FileCreated(a.container_id, a.file_id, os.path.basename(a.filename or a.file_id))
            for part in item.content
            for a in getattr(part, "annotations", None) or []
            if a.type == "container_file_citation"
        ]
    return []


@dataclass(frozen=True)
class ContainerFile:
    file_id: str
    path: str
    from_code: bool
    created_at: float = 0.0  # время создания (unix), чтобы отличить файлы этого ответа


class OpenAIContainers:
    """Контейнеры Code Interpreter: создать, положить файл, забрать файл."""

    def __init__(self, client: AsyncOpenAI):
        self._client = client

    async def create(self, name: str) -> str:
        try:
            return (await self._client.containers.create(name=name)).id
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            raise translate_error(exc) from exc

    async def upload(self, container_id: str, filename: str, content: bytes) -> str:
        """Возвращает путь файла внутри контейнера (OpenAI добавляет к имени свой префикс)."""
        try:
            created = await self._client.containers.files.create(container_id, file=(filename, content))
            return created.path
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            raise _container_error(exc) from exc

    async def download(self, container_id: str, file_id: str) -> bytes:
        try:
            response = await self._client.containers.files.content.retrieve(
                file_id, container_id=container_id
            )
            return await response.aread()
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            raise _container_error(exc) from exc

    async def list_files(self, container_id: str) -> list[ContainerFile]:
        try:
            page = await self._client.containers.files.list(container_id)
            return [
                ContainerFile(f.id, f.path, f.source == "assistant", float(f.created_at or 0))
                for f in page.data
            ]
        except (openai.OpenAIError, httpx.HTTPError) as exc:
            raise _container_error(exc) from exc


def _container_error(exc: Exception) -> LLMError:
    if isinstance(exc, openai.NotFoundError):
        return ContainerExpiredError()
    return translate_error(exc)
