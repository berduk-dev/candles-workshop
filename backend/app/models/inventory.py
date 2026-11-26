from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Sequence,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import BatchStatus, Currency, MovementKind
from app.models.types import CreatedAt, Money, Qty, Rate, str_enum

# Автономера документов. Отдельные последовательности: номер не зависит от id и не переиспользуется.
receipt_number_seq = Sequence("receipt_number_seq", metadata=Base.metadata)
batch_number_seq = Sequence("batch_number_seq", metadata=Base.metadata)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Нормализованное имя без ОПФ, кавычек и регистра: «ООО Восковой двор» == «Восковой двор, ООО».
    norm_key: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[CreatedAt]


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[CreatedAt]


class Receipt(Base):
    """Поступление от поставщика. Каждая строка поступления — отдельная партия."""

    __tablename__ = "receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    received_at: Mapped[date] = mapped_column(Date)
    supplier_doc: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[CreatedAt]

    supplier: Mapped[Supplier] = relationship(lazy="raise")
    batches: Mapped[list["Batch"]] = relationship(back_populates="receipt", lazy="raise")


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = (
        CheckConstraint("qty_initial > 0", name="qty_initial_positive"),
        CheckConstraint("qty_on_hand >= 0", name="qty_on_hand_non_negative"),
        # Последний рубеж против двойного резерва: в резерве не может быть больше, чем лежит.
        CheckConstraint("qty_reserved >= 0 AND qty_reserved <= qty_on_hand", name="qty_reserved_in_range"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("fx_rate > 0", name="fx_rate_positive"),
        # Индекс под FIFO-выборку: принятые партии материала по дате поступления.
        Index("ix_batches_fifo", "material_id", "status", "received_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("receipts.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"))
    received_at: Mapped[date] = mapped_column(Date)

    qty_initial: Mapped[Qty]
    qty_on_hand: Mapped[Qty]
    qty_reserved: Mapped[Qty] = mapped_column(default=Decimal("0"))

    price: Mapped[Money]
    currency: Mapped[Currency] = mapped_column(str_enum(Currency, 3))
    fx_rate: Mapped[Rate]
    fx_rate_date: Mapped[date] = mapped_column(Date)
    unit_cost_rub: Mapped[Money]

    status: Mapped[BatchStatus] = mapped_column(str_enum(BatchStatus), default=BatchStatus.ON_CHECK)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[CreatedAt]

    receipt: Mapped[Receipt] = relationship(back_populates="batches", lazy="raise")
    material: Mapped[Material] = relationship(lazy="raise")


class StockMovement(Base):
    """Журнал движений по партии — из него собирается история партии."""

    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    kind: Mapped[MovementKind] = mapped_column(str_enum(MovementKind))
    qty: Mapped[Qty]
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[CreatedAt]


class FxRate(Base):
    """Кэш курсов ЦБ из мок-сервиса: один раз полученный курс больше не запрашиваем."""

    __tablename__ = "fx_rates"

    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[Currency] = mapped_column(str_enum(Currency, 3), primary_key=True)
    rate: Mapped[Rate]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
