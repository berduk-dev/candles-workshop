def like_pattern(value: str) -> str:
    """Подстрока для ILIKE с экранированием % и _: пользовательский ввод не становится шаблоном."""
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Номера документов пишутся кириллицей («П-000123», «З-000045»), но люди и модель часто набирают
# похожие латинские буквы или цифру 3 вместо «З». Приводим к каноническому виду перед поиском.
_LOOKALIKES = str.maketrans({"P": "П", "T": "Т", "3": "З"})


def normalize_doc_number(value: str) -> str:
    """«p-12» → «П-000012», «пт-7» → «ПТ-000007». Строки не в формате «префикс-номер» не трогаем."""
    value = value.strip().upper().replace(" ", "")
    prefix, sep, rest = value.partition("-")
    if not sep or not prefix or not rest.isdigit():
        return value
    return f"{prefix.translate(_LOOKALIKES)}-{int(rest):06d}"
