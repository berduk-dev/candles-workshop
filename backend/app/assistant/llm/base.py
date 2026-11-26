"""Единый интерфейс к языковой модели. Остальной код ассистента знает только эти типы,
поэтому новый провайдер — это один класс с методом stream()."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

# ---------- что отправляем ----------


@dataclass(frozen=True)
class UserText:
    text: str


@dataclass(frozen=True)
class AssistantText:
    text: str


@dataclass(frozen=True)
class ToolCall:
    """Модель просит вызвать нашу функцию. Приходит событием и остаётся в истории шага."""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    output: str


@dataclass(frozen=True)
class CodeRun:
    """Код, выполненный в Code Interpreter. Тоже и событие, и часть истории."""

    code: str
    logs: str
    failed: bool
    container_id: str | None


HistoryItem = UserText | AssistantText | ToolCall | ToolResult | CodeRun


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class LLMRequest:
    instructions: str
    items: list[HistoryItem]
    tools: list[ToolSpec] = field(default_factory=list)
    code_interpreter: bool = False
    container_id: str | None = None


# ---------- что получаем ----------


@dataclass(frozen=True)
class ModelStarted:
    """Отвечает такая-то модель; fallback=True — запасная."""

    model: str
    fallback: bool


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class CodeStarted:
    pass


@dataclass(frozen=True)
class FileCreated:
    container_id: str
    file_id: str
    filename: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0  # часть входа, взятая из кэша промпта
    written_tokens: int = 0  # часть входа, впервые записанная в кэш
    reasoning_tokens: int = 0  # часть выхода, ушедшая на рассуждения


LLMEvent = ModelStarted | TextDelta | ToolCall | CodeStarted | CodeRun | FileCreated | Usage


class LLMProvider(Protocol):
    model: str

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]: ...


# ---------- ошибки ----------


class LLMError(Exception):
    """retryable=True — виноват сервис (429, 5xx, таймаут, обрыв): можно попробовать другую модель.
    retryable=False — виноват сам запрос (остальные 4xx): повторять бессмысленно."""

    def __init__(self, reason: str, *, retryable: bool, status: int | None = None, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable
        self.status = status
        self.detail = detail  # технический код ошибки; пользователю не показывается


class ContainerExpiredError(LLMError):
    """Контейнер Code Interpreter закрылся по неактивности: нужно создать новый."""

    def __init__(self) -> None:
        super().__init__("Среда для расчётов закрылась.", retryable=False)
