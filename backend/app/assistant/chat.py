"""Диалоги: сообщения, запуск ответа, остановка, сохранение прерванного ответа."""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.assistant.agent import Agent, AssistantDeps, Event, Turn, action_view, file_view
from app.assistant.llm.base import LLMError
from app.errors import DomainError, NotFoundError
from app.models import ChatFile, Conversation, Message, MessageStatus, PendingAction, User

logger = logging.getLogger(__name__)

PING_SECONDS = 15
_END = object()

# Ответы, которые прямо сейчас генерируются: диалог → задача. Один процесс uvicorn — один словарь.
_running: dict[int, asyncio.Task] = {}


# ---------- диалоги ----------


async def list_conversations(session: AsyncSession, user: User) -> list[Conversation]:
    return list(
        await session.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(Conversation.id.desc())
            .limit(100)
        )
    )


async def create(session: AsyncSession, user: User) -> Conversation:
    """Новый диалог. Если у пользователя уже есть пустой, возвращаем его: второй пустой не нужен."""
    # Блокировка пользователя: два одновременных запроса не создадут два пустых диалога.
    await session.execute(select(User.id).where(User.id == user.id).with_for_update(key_share=True))
    empty = await session.scalar(
        select(Conversation)
        .where(
            Conversation.user_id == user.id,
            ~exists().where(Message.conversation_id == Conversation.id),
        )
        .order_by(Conversation.id.desc())
        .limit(1)
    )
    if empty is not None:
        return empty
    conversation = Conversation(user_id=user.id, title="Новый диалог")
    session.add(conversation)
    await session.flush()
    return conversation


async def get_own(session: AsyncSession, user: User, conversation_id: int) -> Conversation:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id)
    )
    if conversation is None:
        raise NotFoundError("Диалог не найден.")
    return conversation


async def remove(session: AsyncSession, user: User, conversation_id: int) -> None:
    await get_own(session, user, conversation_id)
    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))


async def messages_view(session: AsyncSession, user: User, conversation_id: int) -> list[dict[str, Any]]:
    await get_own(session, user, conversation_id)
    messages = await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    files = list(
        await session.scalars(
            select(ChatFile).where(ChatFile.conversation_id == conversation_id).order_by(ChatFile.id)
        )
    )
    actions = list(
        await session.scalars(
            select(PendingAction)
            .where(PendingAction.conversation_id == conversation_id)
            .order_by(PendingAction.id)
        )
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "status": m.status.value,
            "model": m.model,
            "used_fallback": m.used_fallback,
            "meta": m.meta,
            "created_at": m.created_at.isoformat(),
            "files": [file_view(f) for f in files if f.message_id == m.id],
            "actions": [action_view(a) for a in actions if a.message_id == m.id],
        }
        for m in messages
    ]


async def add_note(session: AsyncSession, conversation_id: int, text: str) -> Message:
    """Итог действия попадает в диалог, чтобы ассистент знал, чем всё кончилось."""
    message = Message(conversation_id=conversation_id, role="assistant", content=text, meta={"note": True})
    session.add(message)
    await session.flush()
    return message


async def mark_stale_as_interrupted(sessions: async_sessionmaker[AsyncSession]) -> None:
    """При старте сервера: ответы, которые оборвались вместе с прошлым процессом."""
    async with sessions() as session:
        await session.execute(
            update(Message)
            .where(Message.status == MessageStatus.STREAMING)
            .values(status=MessageStatus.INTERRUPTED)
        )
        await session.commit()


# ---------- ответ ----------


async def start_answer(
    session: AsyncSession, user: User, conversation_id: int, text: str, file_ids: list[int]
) -> tuple[Message, Message]:
    conversation = await get_own(session, user, conversation_id)
    # Блокировка диалога: два одновременных вопроса не пройдут проверку вместе.
    await session.refresh(conversation, with_for_update=True)
    answering = select(Message.id).where(
        Message.conversation_id == conversation_id, Message.status == MessageStatus.STREAMING
    )
    if await session.scalar(answering.limit(1)):
        raise DomainError("Ассистент ещё отвечает на предыдущий вопрос.")
    if conversation.title == "Новый диалог":
        conversation.title = text[:60] + ("…" if len(text) > 60 else "")
    question = Message(conversation_id=conversation_id, role="user", content=text, meta={})
    answer = Message(
        conversation_id=conversation_id, role="assistant", status=MessageStatus.STREAMING, meta={}
    )
    session.add(question)
    await session.flush()
    session.add(answer)
    if file_ids:
        await session.execute(
            update(ChatFile)
            .where(
                ChatFile.id.in_(file_ids),
                ChatFile.conversation_id == conversation_id,
                ChatFile.user_id == user.id,
            )
            .values(message_id=question.id)
        )
    await session.flush()
    return question, answer


def launch(deps: AssistantDeps, user: User, conversation_id: int, answer_id: int) -> AsyncIterator[str]:
    """Запускает ответ отдельной задачей и возвращает поток SSE. Задачу можно остановить:
    кнопкой «Остановить» (stop) или закрытием вкладки (обрыв потока)."""
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(_produce(deps, user, conversation_id, answer_id, queue))
    _running[conversation_id] = task
    task.add_done_callback(lambda _: _running.pop(conversation_id, None))
    return _stream(task, queue, answer_id)


async def _stream(task: asyncio.Task, queue: asyncio.Queue, answer_id: int) -> AsyncIterator[str]:
    try:
        yield sse({"type": "start", "message_id": answer_id})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"  # чтобы прокси не закрыл молчащее соединение
                continue
            if event is _END:
                return
            yield sse(event)
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)


async def _produce(
    deps: AssistantDeps, user: User, conversation_id: int, answer_id: int, queue: asyncio.Queue
) -> None:
    turn = Turn()
    try:
        async for event in Agent(deps, user, conversation_id, answer_id, turn).run():
            queue.put_nowait(event)
        turn.status = MessageStatus.COMPLETE
    except asyncio.CancelledError:
        turn.status = MessageStatus.INTERRUPTED
    except LLMError as exc:
        turn.status, turn.error = MessageStatus.ERROR, exc.reason
    except DomainError as exc:
        turn.status, turn.error = MessageStatus.ERROR, exc.message
    except Exception:
        error_id = uuid.uuid4().hex[:8]
        logger.exception("Ошибка ассистента %s", error_id)
        turn.status, turn.error = MessageStatus.ERROR, f"Что-то пошло не так. Код ошибки: {error_id}."
    try:
        # shield: повторная отмена (кнопка «Остановить» и сразу закрытая вкладка) не прервёт запись в базу.
        saving = asyncio.ensure_future(_save(deps, answer_id, turn))
        await asyncio.shield(saving)
    finally:
        if turn.error:
            queue.put_nowait({"type": "error", "text": turn.error})
        queue.put_nowait({"type": "done", "status": turn.status.value})
        queue.put_nowait(_END)


async def _save(deps: AssistantDeps, answer_id: int, turn: Turn) -> None:
    async with deps.sessions() as session:
        await session.execute(
            update(Message)
            .where(Message.id == answer_id)
            .values(
                content=turn.text,
                status=turn.status,
                model=turn.model,
                used_fallback=turn.used_fallback,
                meta=turn.meta(),
            )
        )
        await session.commit()


def stop(conversation_id: int) -> bool:
    task = _running.get(conversation_id)
    if task is None:
        return False
    task.cancel()
    return True


def sse(event: Event) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
