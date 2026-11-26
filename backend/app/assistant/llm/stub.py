"""Заглушки модели и контейнеров для тестов и E2E: никакой сети, ответы по сценарию."""

import re
import time
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import timedelta

from app.assistant.llm.base import (
    LLMError,
    LLMEvent,
    LLMRequest,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
    UserText,
)
from app.assistant.llm.openai_provider import ContainerFile
from app.clock import today

Step = list[LLMEvent | LLMError]


class StubProvider:
    """Отдаёт заранее заданные шаги по очереди и запоминает все запросы, которые получил."""

    def __init__(self, steps: Iterable[Step] | Callable[[LLMRequest], Step], model: str = "stub"):
        self.model = model
        self.requests: list[LLMRequest] = []
        self._steps = steps if callable(steps) else iter(list(steps))

    async def stream(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        # Копия: агент дописывает историю в тот же список, а тестам нужен снимок.
        self.requests.append(
            LLMRequest(
                request.instructions,
                list(request.items),
                request.tools,
                request.code_interpreter,
                request.container_id,
            )
        )
        step = self._steps(request) if callable(self._steps) else next(self._steps)
        for event in step:
            if isinstance(event, LLMError):
                raise event
            yield event


class StubContainers:
    def __init__(self) -> None:
        self.files: dict[str, dict[str, bytes]] = {}
        self.generated: dict[str, bytes] = {}
        self.created_at: dict[str, float] = {}  # для имитации старых файлов в общем контейнере
        self.listed = 0  # сколько раз просили список файлов

    async def create(self, name: str) -> str:
        container_id = f"cntr_{len(self.files) + 1}"
        self.files[container_id] = {}
        return container_id

    async def upload(self, container_id: str, filename: str, content: bytes) -> str:
        self.files[container_id][filename] = content
        return f"/mnt/data/{filename}"

    async def download(self, container_id: str, file_id: str) -> bytes:
        return self.generated[file_id]

    async def list_files(self, container_id: str) -> list[ContainerFile]:
        self.listed += 1
        return [
            ContainerFile(file_id, f"/mnt/data/{file_id}", True, self.created_at.get(file_id, time.time()))
            for file_id in self.generated
        ]


def demo_script(request: LLMRequest) -> Step:
    """Сценарий для E2E (LLM_STUB=1): «запланируй N свечей «Рецепт»» → предложение задания."""
    last = request.items[-1]
    if isinstance(last, ToolResult):
        return [TextDelta("Подготовил задание. "), TextDelta("Проверьте и подтвердите."), Usage(1, 1)]
    text = last.text if isinstance(last, UserText) else ""
    if match := re.search(r"(\d+) свеч\w* «(\w+)»", text):
        planned = (today() + timedelta(days=3)).isoformat()
        args = f'{{"recipe": "{match[2]}", "qty": {match[1]}, "planned_date": "{planned}"}}'
        return [ToolCall("call_1", "propose_create_and_plan_job", args)]
    return [TextDelta("Это ответ заглушки."), Usage(1, 1)]
