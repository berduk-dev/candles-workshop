"""Цикл «модель ↔ инструменты» для одного вопроса пользователя.

Агент ничего не знает про HTTP и SSE: он отдаёт события, а chat.py передаёт их в браузер и сохраняет.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant import actions as actions_service
from app.assistant import container, files, memory, tools
from app.assistant.llm.base import (
    AssistantText,
    CodeRun,
    CodeStarted,
    ContainerExpiredError,
    FileCreated,
    HistoryItem,
    LLMError,
    LLMRequest,
    ModelStarted,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
    UserText,
)
from app.assistant.llm.router import ModelRouter
from app.assistant.masking import Masker, StreamUnmasker
from app.assistant.prompts import load_text, note
from app.clock import today
from app.formatting import fmt_date
from app.models import ChatFile, Message, MessageStatus, PendingAction, User
from app.services.fx import FxClient
from app.services.permissions import PERMISSION_TEXT, has_perm, who_can

logger = logging.getLogger(__name__)

MAX_STEPS = 8
HISTORY_MESSAGES = 20
HISTORY_CHARS = 4000
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
# Код что-то сохраняет на диск — только тогда есть смысл искать созданные им файлы.
SAVES_FILE = re.compile(r"savefig|to_csv|to_excel|\.save\(|open\([^)]*['\"]w")
# Часы контейнера и наши могут расходиться: допуск при отсечении старых файлов, секунды.
FILE_CLOCK_SKEW = 30
SHOWN_EXTENSIONS = {".png", ".jpg", ".jpeg", ".csv", ".xlsx"}
TOO_MANY_STEPS = (
    "Не получилось ответить: запрос требует слишком много шагов. Попробуйте разбить его на части."
)
CODE_FAILED = "Расчёт выполнился с ошибкой — подробности в разделе с кодом."

Event = dict[str, Any]


@dataclass(frozen=True)
class AssistantDeps:
    router: ModelRouter
    containers: container.Containers
    sessions: async_sessionmaker[AsyncSession]
    fx: FxClient


@dataclass
class Turn:
    """Накопленный ответ: из него собирается сообщение в базе, в том числе прерванное."""

    text: str = ""
    model: str | None = None
    used_fallback: bool = False
    tools: list[dict[str, Any]] = field(default_factory=list)
    code: list[dict[str, Any]] = field(default_factory=list)
    legend: dict[str, str] = field(default_factory=dict)
    usage: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            # *_ms — куда ушло время ответа: ожидание модели, инструменты (включая загрузку в контейнер)
            ("input", "output", "cached", "written", "reasoning", "steps", "llm_ms", "tool_ms", "files_ms"),
            0,
        )
    )
    # Контейнеры Code Interpreter, заведённые ради этого ответа: каждый — отдельная платная сессия.
    containers: set[str] = field(default_factory=set)
    error: str | None = None
    status: MessageStatus = MessageStatus.STREAMING

    def meta(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "code": self.code,
            "legend": self.legend,
            "usage": {**self.usage, "containers": len(self.containers)},
            "error": self.error,
        }


class Agent:
    def __init__(self, deps: AssistantDeps, user: User, conversation_id: int, message_id: int, turn: Turn):
        self.deps = deps
        self.user = user
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.turn = turn
        self.created: list[FileCreated] = []
        self.code_container: str | None = None
        self.known_container: str | None = None
        self.dataset_texts: list[str] = []
        self.started_at = time.time()

    async def run(self) -> AsyncIterator[Event]:
        async with self.deps.sessions() as session:
            self.masker = await Masker.load(session)
            items = await self._history(session)
            self.known_container = await container.current(session, self.user.id)
            container_id = self.known_container
            if await container.uploads(session, self.conversation_id):
                container_id = await container.ensure(
                    session, self.user.id, self.conversation_id, self.deps.containers
                )
                self._track(container_id)
            instructions = await self._instructions(session)
            await self.masker.save(session)
            await session.commit()

        unmasker = StreamUnmasker(self.masker)
        retried_container = False
        for _ in range(MAX_STEPS):
            request = LLMRequest(
                instructions=instructions,
                items=items,
                tools=tools.specs_for(self.user),
                code_interpreter=True,
                container_id=container_id,
            )
            text, calls, step_items = "", [], []
            step_started = time.monotonic()
            try:
                async for event in self.deps.router.stream(request):
                    match event:
                        case ModelStarted(model, fallback):
                            self.turn.model, self.turn.used_fallback = model, fallback
                            yield {"type": "model", "model": model, "fallback": fallback}
                        case TextDelta(delta):
                            text += delta
                            if shown := unmasker.feed(delta):
                                self.turn.text += shown
                                yield {"type": "text", "delta": shown}
                        case ToolCall():
                            calls.append(event)
                        case CodeStarted():
                            yield {"type": "code_started"}
                        case CodeRun():
                            step_items.append(event)
                            self.code_container = event.container_id or self.code_container
                            entry = {"code": event.code, "logs": event.logs[:5000], "failed": event.failed}
                            self.turn.code.append(entry)
                            yield {"type": "code", **entry}
                        case FileCreated():
                            self.created.append(event)
                        case Usage():
                            self.turn.usage["input"] += event.input_tokens
                            self.turn.usage["output"] += event.output_tokens
                            self.turn.usage["cached"] += event.cached_tokens
                            self.turn.usage["written"] += event.written_tokens
                            self.turn.usage["reasoning"] += event.reasoning_tokens
                            self.turn.usage["steps"] += 1
            except ContainerExpiredError:
                # Контейнер закрылся раньше, чем мы ждали: заводим новый и повторяем шаг один раз.
                if retried_container or text or calls:
                    raise
                retried_container = True
                container_id = await self._renew_container()
                self._track(container_id)
                continue
            self.turn.usage["llm_ms"] += int((time.monotonic() - step_started) * 1000)
            if rest := unmasker.flush():
                self.turn.text += rest
                yield {"type": "text", "delta": rest}
            if self.code_container and self.code_container != container_id:
                container_id = self.code_container
                self._track(container_id)
                await self._remember_container(container_id)

            if not calls:
                started = time.monotonic()
                async for event in self._collect_files():
                    yield event
                self.turn.usage["files_ms"] += int((time.monotonic() - started) * 1000)
                return
            if text:
                step_items.append(AssistantText(text))
                if not self.turn.text.endswith("\n"):
                    self.turn.text += "\n\n"
                    yield {"type": "text", "delta": "\n\n"}
            items += step_items
            items += calls
            for call in calls:
                async for event in self._call_tool(call, items):
                    yield event
            container_id = await self._current_container() or container_id
            self._track(container_id)
        raise LLMError(TOO_MANY_STEPS, retryable=False)

    # ---------- инструменты ----------

    async def _call_tool(self, call: ToolCall, items: list[HistoryItem]) -> AsyncIterator[Event]:
        spec = tools.TOOLS.get(call.name)
        label = spec.label if spec else call.name
        yield {"type": "tool_started", "name": call.name, "label": label}
        # Отдельная короткая сессия на вызов: соединение с базой не висит, пока модель думает.
        async with self.deps.sessions() as session:
            ctx = tools.ToolContext(
                session=session,
                user=self.user,
                fx=self.deps.fx,
                masker=self.masker,
                conversation_id=self.conversation_id,
                message_id=self.message_id,
                upload_dataset=self._upload_dataset,
                dataset_texts=self.dataset_texts,
            )
            started = time.monotonic()
            outcome = await tools.execute(call.name, call.arguments, ctx)
            self.turn.usage["tool_ms"] += int((time.monotonic() - started) * 1000)
        items.append(ToolResult(call.call_id, outcome.output))
        entry = {
            "name": call.name,
            "label": label,
            "ok": outcome.ok,
            "args": self.masker.unmask_text(call.arguments),
            "result": self.masker.unmask_text(outcome.output)[:8000],
        }
        self.turn.tools.append(entry)
        yield {"type": "tool", **entry}
        for action in ctx.actions:
            yield {"type": "action", "action": action_view(action)}

    async def _upload_dataset(self, name: str, content: bytes) -> str:
        async with self.deps.sessions() as session:
            path = await container.put_dataset(
                session, self.user.id, self.conversation_id, self.deps.containers, name, content
            )
            await session.commit()
            return path

    def _track(self, container_id: str | None) -> None:
        if container_id and container_id != self.known_container:
            self.turn.containers.add(container_id)

    async def _current_container(self) -> str | None:
        async with self.deps.sessions() as session:
            return await container.current(session, self.user.id)

    async def _remember_container(self, container_id: str) -> None:
        async with self.deps.sessions() as session:
            await container.remember(session, self.user.id, container_id)
            await session.commit()

    async def _renew_container(self) -> str | None:
        async with self.deps.sessions() as session:
            await container.forget(session, self.user.id)
            await session.commit()
            return await container.ensure(session, self.user.id, self.conversation_id, self.deps.containers)

    # ---------- файлы, созданные кодом ----------

    async def _collect_files(self) -> AsyncIterator[Event]:
        """Забирает файлы из контейнера к себе: контейнер скоро закроется, а файл должен скачиваться."""
        if not self.turn.code:
            return
        if any(c["failed"] for c in self.turn.code) and not any(not c["failed"] for c in self.turn.code):
            yield {"type": "notice", "text": CODE_FAILED}
        found = list({f.file_id: f for f in self.created}.values())
        try:
            if (
                not found
                and self.code_container
                and any(SAVES_FILE.search(c["code"]) for c in self.turn.code)
            ):
                # Модель забыла сослаться на файл — берём то, что код создал за этот ответ. Контейнер
                # общий для всех диалогов пользователя, поэтому старые файлы отсекаем по времени.
                found = [
                    FileCreated(self.code_container, f.file_id, Path(f.path).name)
                    for f in await self.deps.containers.list_files(self.code_container)
                    if f.from_code and f.created_at >= self.started_at - FILE_CLOCK_SKEW
                ]
            # Кроме сохранённого файла, появляется автокопия графика с именем cfile_… — это дубль.
            found = [f for f in found if not f.filename.startswith("cfile_")] or found
            async with self.deps.sessions() as session:
                known = set(
                    await session.scalars(
                        select(ChatFile.container_file_id).where(
                            ChatFile.conversation_id == self.conversation_id
                        )
                    )
                )
                wanted = [
                    item
                    for item in found
                    if item.file_id not in known and Path(item.filename).suffix.lower() in SHOWN_EXTENSIONS
                ]
                # Файлы скачиваются одновременно: каждый запрос к контейнеру — около секунды.
                contents = await asyncio.gather(
                    *(self.deps.containers.download(i.container_id, i.file_id) for i in wanted)
                )
                for item, content in zip(wanted, contents, strict=True):
                    file = await files.save(
                        session,
                        user_id=self.user.id,
                        conversation_id=self.conversation_id,
                        message_id=self.message_id,
                        source="generated",
                        filename=self.masker.unmask_text(item.filename),
                        content=files.unmask_generated(item.filename, content, self.masker),
                        container_file_id=item.file_id,
                    )
                    await session.commit()
                    yield {"type": "file", "file": file_view(file)}
        except LLMError as exc:
            logger.warning("Файлы контейнера не получены: %s", exc.reason)
            yield {"type": "notice", "text": "Файл создан, но скачать его не удалось. Попросите ещё раз."}
        if any(Path(f.filename).suffix.lower() in {".png", ".jpg", ".jpeg"} for f in found):
            codes = [c["code"] + c["logs"] for c in self.turn.code]
            self.turn.legend = self.masker.legend(*codes, *self.dataset_texts)
            if self.turn.legend:
                yield {"type": "legend", "legend": self.turn.legend}

    # ---------- промпт ----------

    async def _history(self, session: AsyncSession) -> list[HistoryItem]:
        """Последние сообщения диалога. Имена заменяются кодами здесь, перед самой отправкой."""
        rows = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == self.conversation_id, Message.id != self.message_id)
                .order_by(Message.id.desc())
                .limit(HISTORY_MESSAGES)
            )
        )
        items: list[HistoryItem] = []
        for message in reversed(rows):
            text = self.masker.mask_text(message.content)[:HISTORY_CHARS]
            if message.status == MessageStatus.INTERRUPTED:
                text += "\n" + note("interrupted")
            if text.strip():
                items.append(UserText(text) if message.role == "user" else AssistantText(text))
        return items

    async def _instructions(self, session: AsyncSession) -> str:
        day = today()
        week_start = day - timedelta(days=day.weekday())
        facts = await memory.list_facts(session, self.user)
        uploaded = await container.uploads(session, self.conversation_id)
        return load_text("system.md").format(
            today=fmt_date(day),
            weekday=WEEKDAYS[day.weekday()],
            week_start=fmt_date(week_start),
            week_end=fmt_date(week_start + timedelta(days=6)),
            user_alias=f"EMP-{self.user.id:03d}",
            role=self.user.role.label,
            permissions="; ".join(t for p, t in PERMISSION_TEXT.items() if has_perm(self.user, p)),
            forbidden="; ".join(
                f"{t} (может {who_can(p)})" for p, t in PERMISSION_TEXT.items() if not has_perm(self.user, p)
            )
            or "ограничений нет",
            memory="\n".join(f"- {self.masker.mask_text(f.fact)}" for f in facts) or note("no_facts"),
            files="\n".join(f"- {f.filename}: {f.container_path}" for f in uploaded) or note("no_files"),
        )


def action_view(action: PendingAction) -> dict[str, Any]:
    expired = action.status == "pending" and action.expires_at < actions_service.now()
    return {
        "id": action.id,
        "message_id": action.message_id,
        "tool": action.tool,
        "preview": action.preview,
        "status": "expired" if expired else action.status.value,
        "result": action.result,
    }


def file_view(file: ChatFile) -> dict[str, Any]:
    return {
        "id": file.id,
        "filename": file.filename,
        "mime": file.mime,
        "size": file.size,
        "source": file.source,
        "url": f"/api/chat/files/{file.id}",
    }
