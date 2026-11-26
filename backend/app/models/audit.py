from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.types import CreatedAt


class AuditLog(Base):
    """Журнал изменений: кто (ФИО снимком на момент действия), когда, что было, что стало."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    at: Mapped[CreatedAt]
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user_full_name: Mapped[str] = mapped_column(String(150))
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int]
    action: Mapped[str] = mapped_column(String(40))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
