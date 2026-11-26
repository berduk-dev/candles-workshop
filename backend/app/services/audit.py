"""Журнал изменений. Запись делается в той же транзакции, что и само изменение:
если изменение откатилось, записи в журнале тоже не будет."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.services.permissions import Perm, require


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def snapshot(obj: Any, fields: list[str]) -> dict[str, Any]:
    return {name: _json_safe(getattr(obj, name)) for name in fields}


def record(
    session: AsyncSession,
    user: User,
    *,
    entity: str,
    entity_id: int,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Пишет в журнал только изменившиеся поля: «что было» → «что стало»."""
    if before is not None and after is not None:
        changed = [key for key in after if before.get(key) != after.get(key)]
        before = {key: before.get(key) for key in changed}
        after = {key: after[key] for key in changed}
    session.add(
        AuditLog(
            user_id=user.id,
            user_full_name=user.full_name,
            entity=entity,
            entity_id=entity_id,
            action=action,
            before=before,
            after=after,
        )
    )


async def list_entries(
    session: AsyncSession,
    user: User,
    *,
    entity: str | None = None,
    entity_id: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AuditLog], int]:
    require(user, Perm.AUDIT_VIEW)
    query = select(AuditLog)
    if entity:
        query = query.where(AuditLog.entity == entity)
    if entity_id is not None:
        query = query.where(AuditLog.entity_id == entity_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(AuditLog.at.desc(), AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows), total or 0


async def entries_for(session: AsyncSession, entity: str, entity_id: int) -> list[AuditLog]:
    """История конкретного объекта — для карточек (видна всем, кто видит сам объект)."""
    rows = await session.scalars(
        select(AuditLog)
        .where(AuditLog.entity == entity, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.at, AuditLog.id)
    )
    return list(rows)
