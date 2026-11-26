"""Перевод ошибок валидации Pydantic в текст для человека.

Используется и для запросов API, и (позже) для аргументов инструментов ассистента:
и те и другие — внешний ввод.
"""

from collections.abc import Iterable, Mapping
from typing import Any

_MESSAGES: dict[str, str] = {
    "missing": "обязательное поле",
    "extra_forbidden": "лишнее поле",
    "greater_than": "должно быть больше {gt}",
    "greater_than_equal": "должно быть не меньше {ge}",
    "less_than": "должно быть меньше {lt}",
    "less_than_equal": "должно быть не больше {le}",
    "decimal_parsing": "должно быть числом",
    "decimal_type": "должно быть числом",
    "decimal_max_places": "не больше {decimal_places} знаков после запятой",
    "decimal_max_digits": "слишком большое число",
    "decimal_whole_digits": "слишком большое число",
    "finite_number": "должно быть конечным числом",
    "int_parsing": "должно быть целым числом",
    "int_type": "должно быть целым числом",
    "bool_parsing": "должно быть да или нет",
    "string_type": "должно быть строкой",
    "string_too_short": "не может быть пустым",
    "string_too_long": "не длиннее {max_length} символов",
    "string_pattern_mismatch": "недопустимые символы",
    "enum": "допустимые значения: {expected}",
    "literal_error": "допустимые значения: {expected}",
    "date_parsing": "нужна дата в формате ГГГГ-ММ-ДД",
    "date_from_datetime_parsing": "нужна дата в формате ГГГГ-ММ-ДД",
    "date_type": "нужна дата в формате ГГГГ-ММ-ДД",
    "too_short": "нужно хотя бы {min_length}",
    "too_long": "не больше {max_length}",
    "list_type": "должен быть список",
    "model_attributes_type": "должен быть объект",
    "dict_type": "должен быть объект",
    "json_invalid": "некорректный JSON",
}

_FIELDS: dict[str, str] = {
    "qty": "количество",
    "price": "цена",
    "currency": "валюта",
    "material_code": "код материала",
    "supplier_name": "поставщик",
    "supplier_doc": "номер накладной",
    "received_at": "дата поступления",
    "lines": "строки поступления",
    "comment": "комментарий",
    "login": "логин",
    "password": "пароль",
    "status": "статус",
    "code": "код",
    "name": "название",
    "unit": "единица измерения",
    "page": "страница",
    "page_size": "размер страницы",
}

_SKIP = {"body", "query", "path"}


def _location(loc: Iterable[Any]) -> str:
    parts: list[str] = []
    for item in loc:
        if item in _SKIP:
            continue
        if isinstance(item, int):
            parts.append(f"№{item + 1}")
        else:
            parts.append(_FIELDS.get(str(item), str(item)))
    return " → ".join(parts)


def _message(error: Mapping[str, Any]) -> str:
    template = _MESSAGES.get(error.get("type", ""))
    if template is None:
        return str(error.get("msg", "некорректное значение"))
    try:
        return template.format(**(error.get("ctx") or {}))
    except (KeyError, IndexError):
        return template


def humanize(errors: Iterable[Mapping[str, Any]]) -> str:
    lines = []
    for error in errors:
        location = _location(error.get("loc", ()))
        message = _message(error)
        lines.append(f"{location}: {message}" if location else message)
    return "Проверьте данные: " + "; ".join(lines) + "."
