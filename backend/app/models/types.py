from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import DateTime, Enum, Numeric, func
from sqlalchemy.orm import mapped_column

# Единые типы колонок: количества, деньги, курсы — только numeric, в Python — Decimal.
Qty = Annotated[Decimal, mapped_column(Numeric(14, 3))]
Money = Annotated[Decimal, mapped_column(Numeric(16, 4))]
Rate = Annotated[Decimal, mapped_column(Numeric(12, 6))]
CreatedAt = Annotated[
    datetime, mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
]


def str_enum(enum_cls, length: int = 20) -> Enum:
    """Строка + CHECK вместо нативного enum PostgreSQL: проще менять набор значений миграциями."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
        create_constraint=True,
    )
