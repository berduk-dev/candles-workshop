"""Сортировка списков по параметру из адреса: «price» — по возрастанию, «-price» — по убыванию.

Поле сверяется со списком разрешённых колонок: текст из адреса никогда не попадает в SQL как есть.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import ColumnElement

from app.config import get_settings
from app.errors import InvalidInputError


def order_by(sort: str, columns: dict[str, ColumnElement], tiebreak: ColumnElement) -> list:
    descending = sort.startswith("-")
    field = sort.lstrip("-")
    column = columns.get(field)
    if column is None:
        raise InvalidInputError(f"Нельзя сортировать по полю «{field}».")
    # Второй ключ — id: при равных значениях порядок строк не прыгает между страницами.
    if descending:
        return [column.desc().nulls_last(), tiebreak.desc()]
    return [column.asc().nulls_last(), tiebreak.asc()]


def day_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """Границы дней по часовому поясу мастерской: «по 30.11» включает весь день 30.11."""
    tz = get_settings().tz
    start = datetime.combine(date_from, time.min, tz) if date_from else None
    end = datetime.combine(date_to + timedelta(days=1), time.min, tz) if date_to else None
    return start, end
