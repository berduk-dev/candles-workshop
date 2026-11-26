"""Контейнер Code Interpreter: один на пользователя, создаётся только когда нужен.

Каждый новый контейнер — отдельная платная сессия, а пользователь часто начинает новый диалог.
Поэтому контейнер общий для всех диалогов одного человека: чужих данных в нём нет, а свои
он и так видит. Файлы, загруженные в диалог, кладутся в контейнер заново, если он сменился.
"""

from datetime import timedelta
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant import files
from app.assistant.llm.base import ContainerExpiredError
from app.assistant.llm.openai_provider import ContainerFile
from app.clock import now
from app.models import ChatFile, UserContainer

# OpenAI закрывает контейнер после 20 минут простоя — берём с запасом.
IDLE_LIMIT = timedelta(minutes=15)


class Containers(Protocol):
    async def create(self, name: str) -> str: ...
    async def upload(self, container_id: str, filename: str, content: bytes) -> str: ...
    async def download(self, container_id: str, file_id: str) -> bytes: ...
    async def list_files(self, container_id: str) -> list[ContainerFile]: ...


async def current(session: AsyncSession, user_id: int) -> str | None:
    """Живой контейнер пользователя или None, если его нет или он скоро закроется сам."""
    row = await session.get(UserContainer, user_id, populate_existing=True)
    return row.container_id if row and now() - row.used_at < IDLE_LIMIT else None


async def remember(session: AsyncSession, user_id: int, container_id: str) -> None:
    """Записывает контейнер пользователя и продлевает ему жизнь."""
    await session.execute(
        insert(UserContainer)
        .values(user_id=user_id, container_id=container_id, used_at=now())
        .on_conflict_do_update(
            index_elements=["user_id"], set_={"container_id": container_id, "used_at": now()}
        )
    )


async def forget(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(UserContainer).where(UserContainer.user_id == user_id))


async def uploads(session: AsyncSession, conversation_id: int) -> list[ChatFile]:
    return list(
        await session.scalars(
            select(ChatFile)
            .where(ChatFile.conversation_id == conversation_id, ChatFile.source == "upload")
            .order_by(ChatFile.id)
        )
    )


async def ensure(session: AsyncSession, user_id: int, conversation_id: int, containers: Containers) -> str:
    """Возвращает id живого контейнера, где уже лежат все загруженные в этот диалог файлы."""
    container_id = await current(session, user_id) or await containers.create(f"user-{user_id}")
    await remember(session, user_id, container_id)
    for file in await uploads(session, conversation_id):
        if file.container_id != container_id:
            file.container_path = await containers.upload(
                container_id, file.filename, files.read_content(file)
            )
            file.container_id = container_id
    await session.commit()
    return container_id


async def put_dataset(
    session: AsyncSession,
    user_id: int,
    conversation_id: int,
    containers: Containers,
    name: str,
    content: bytes,
) -> str:
    container_id = await ensure(session, user_id, conversation_id, containers)
    try:
        return await containers.upload(container_id, name, content)
    except ContainerExpiredError:  # закрылся раньше срока: создаём новый, один раз
        await forget(session, user_id)
        container_id = await ensure(session, user_id, conversation_id, containers)
        return await containers.upload(container_id, name, content)
