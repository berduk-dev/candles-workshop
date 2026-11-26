"""Память: факты о пользователе. Везде фильтр по user.id текущей сессии — чужой факт нельзя
ни прочитать, ни удалить, ни увидеть в промпте."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError, InvalidInputError, NotFoundError
from app.models import User, UserMemory

MAX_FACTS = 50
MAX_LENGTH = 300


async def list_facts(session: AsyncSession, user: User) -> list[UserMemory]:
    return list(
        await session.scalars(select(UserMemory).where(UserMemory.user_id == user.id).order_by(UserMemory.id))
    )


async def add_fact(session: AsyncSession, user: User, fact: str) -> UserMemory:
    fact = " ".join(fact.split())
    if not fact or len(fact) > MAX_LENGTH:
        raise InvalidInputError(f"Факт должен быть не длиннее {MAX_LENGTH} символов.")
    existing = await session.scalar(
        select(UserMemory).where(UserMemory.user_id == user.id, func.lower(UserMemory.fact) == fact.lower())
    )
    if existing:
        return existing
    if len(await list_facts(session, user)) >= MAX_FACTS:
        raise DomainError("Память заполнена. Удалите ненужные факты на странице «Память».")
    row = UserMemory(user_id=user.id, fact=fact)
    session.add(row)
    await session.flush()
    return row


async def delete_fact(session: AsyncSession, user: User, fact_id: int) -> None:
    row = await session.scalar(
        select(UserMemory).where(UserMemory.id == fact_id, UserMemory.user_id == user.id)
    )
    if row is None:
        raise NotFoundError("Факт не найден.")
    await session.delete(row)
    await session.flush()
