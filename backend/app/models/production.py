from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Sequence, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import JobStatus, MarkingStatus
from app.models.inventory import Material
from app.models.types import CreatedAt, Qty, str_enum

job_number_seq = Sequence("job_number_seq", metadata=Base.metadata)


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[CreatedAt]

    items: Mapped[list["RecipeItem"]] = relationship(
        lazy="raise", order_by="RecipeItem.id", cascade="all, delete-orphan"
    )


class RecipeItem(Base):
    """Норма расхода материала на 1 изделие. Точность та же, что у количеств (3 знака):
    потребность = норма × количество всегда точная, без округлений."""

    __tablename__ = "recipe_items"
    __table_args__ = (
        UniqueConstraint("recipe_id", "material_id"),
        CheckConstraint("qty_per_unit > 0", name="qty_per_unit_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    qty_per_unit: Mapped[Qty]

    material: Mapped[Material] = relationship(lazy="raise")


class Job(Base):
    """Производственное задание. Жизненный цикл — в services/jobs.py."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("produced_qty >= 0", name="produced_qty_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    qty: Mapped[int]  # изделия — целые штуки
    planned_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[JobStatus] = mapped_column(str_enum(JobStatus), default=JobStatus.DRAFT)
    produced_qty: Mapped[int | None]
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recipe: Mapped[Recipe] = relationship(lazy="raise")


class JobItem(Base):
    """Потребность задания в материале. Снимок рецепта на момент планирования: правка рецепта
    не меняет уже запланированные задания. qty_actual — фактический расход при завершении."""

    __tablename__ = "job_items"
    __table_args__ = (UniqueConstraint("job_id", "material_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    qty_required: Mapped[Qty]
    qty_actual: Mapped[Qty | None]

    material: Mapped[Material] = relationship(lazy="raise")


class Reservation(Base):
    """Резерв партии под задание. Живёт, пока задание «Запланировано»: при запуске превращается
    в движение «Выдача в цех», при отмене удаляется."""

    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("job_id", "batch_id"),
        CheckConstraint("qty > 0", name="qty_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    qty: Mapped[Qty]


class MarkingReport(Base):
    """Отчёт в сервис маркировки (outbox): создаётся в одной транзакции с завершением задания,
    отправляется фоновым воркером. Ключ идемпотентности один на все попытки."""

    __tablename__ = "marking_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), unique=True)
    status: Mapped[MarkingStatus] = mapped_column(str_enum(MarkingStatus), default=MarkingStatus.PENDING)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[CreatedAt]


class MarkingAttempt(Base):
    """Журнал попыток отправки."""

    __tablename__ = "marking_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("marking_reports.id"), index=True)
    number: Mapped[int]
    at: Mapped[CreatedAt]
    duration_ms: Mapped[int]
    http_status: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
