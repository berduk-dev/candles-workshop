from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import Role
from app.models.types import CreatedAt, str_enum


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[Role] = mapped_column(str_enum(Role))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[CreatedAt]
