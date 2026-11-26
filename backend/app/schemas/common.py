from decimal import Decimal
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Входные типы. Количества и деньги — Decimal с ограничением точности, как в БД.
PositiveQty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
NonNegativeQty = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]
Price = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]

T = TypeVar("T")


class StrictModel(BaseModel):
    """Входные модели: лишние поля — ошибка, а не тихое игнорирование."""

    model_config = ConfigDict(extra="forbid")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
