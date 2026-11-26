from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import ActionStatus, MessageStatus
from app.models.types import CreatedAt, str_enum


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[CreatedAt]


class UserContainer(Base):
    """Контейнер Code Interpreter пользователя. Один на человека, а не на каждый диалог:
    каждый новый контейнер — отдельная платная сессия. Живёт ограниченное время, поэтому
    помним, когда им пользовались."""

    __tablename__ = "user_containers"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    container_id: Mapped[str] = mapped_column(String(100))
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """Сообщение диалога. Текст хранится с настоящими именами (для показа пользователю);
    псевдонимы подставляются заново при каждой сборке промпта."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus), default=MessageStatus.COMPLETE)
    model: Mapped[str | None] = mapped_column(String(100))
    used_fallback: Mapped[bool] = mapped_column(default=False)
    # Вызванные инструменты, выполненный код, текст ошибки — всё, что показывается под ответом.
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]


class PendingAction(Base):
    """Действие, которое предложил ассистент. Выполняется только после «Подтвердить»."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String(50))
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ActionStatus] = mapped_column(str_enum(ActionStatus), default=ActionStatus.PENDING)
    result: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[CreatedAt]


class UserMemory(Base):
    """Факт, который ассистент запомнил о пользователе. Виден только ему."""

    __tablename__ = "user_memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    fact: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[CreatedAt]


class ChatFile(Base):
    """Файл диалога: загружен пользователем (уже с псевдонимами) или создан Code Interpreter."""

    __tablename__ = "chat_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(20))  # upload | generated
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    storage_name: Mapped[str] = mapped_column(String(100), unique=True)
    # Где файл лежит в контейнере (для загрузок) или его id там (для созданных файлов).
    container_path: Mapped[str | None] = mapped_column(String(300))
    # В какой контейнер положен файл: контейнер сменился — файл надо положить заново.
    container_id: Mapped[str | None] = mapped_column(String(100))
    container_file_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[CreatedAt]


class Pseudonym(Base):
    """Псевдонимы имён, которых нет в справочниках (например, новый поставщик из загруженного файла).
    Сотрудники и поставщики из базы получают псевдоним по id и сюда не попадают."""

    __tablename__ = "pseudonyms"

    alias: Mapped[str] = mapped_column(String(30), primary_key=True)
    original: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[CreatedAt]
