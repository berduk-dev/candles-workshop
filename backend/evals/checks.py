"""Машинные проверки ответа: без LLM, только факты."""

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Число: 1 234,5 / 1234.50 / 12. Не часть слова, номера документа (П-000012) или кода (SUP-001).
_NOT_CODE = r"(?<!\w)(?<![A-Za-zА-Яа-я]-)"
NUMBER_RE = re.compile(
    rf"{_NOT_CODE}\d{{1,3}}(?:[ \u00a0\u202f]\d{{3}})+(?:[.,]\d+)?(?!\d)|{_NOT_CODE}\d+(?:[.,]\d+)?"
)
DATE_RE = re.compile(r"(?<!\d)(?:\d{2}\.\d{2}\.\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2})(?!\d)")
# Даты без года и месяцы: «5.10», «26 ноября», «23–29 ноября 2026», «в октябре 2026 года».
# Дробные числа ассистент пишет через запятую, поэтому «5.10» — дата.
_MONTHS = "январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр"
SHORT_DATE_RE = re.compile(
    r"(?<![\d.,])\d{1,2}\.(?:0[1-9]|1[0-2])(?![\d.])"
    rf"|(?:\d{{1,2}}(?:\s*[-–—]\s*\d{{1,2}})?\s+)?\b(?:{_MONTHS})[а-яё]*(?:\s+20\d\d)?",
    re.IGNORECASE,
)
# Деньги точнее копеек: «201,2791 ₽».
LONG_MONEY_RE = re.compile(r"\d[.,]\d{3,}[\s*]*(?:₽|руб|RUB|€|EUR|USD|\$)", re.IGNORECASE)


@dataclass
class Result:
    """Всё, что получилось при ответе на вопрос кейса."""

    answer: str = ""
    statuses: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)  # результаты инструментов и вывод кода
    code_runs: int = 0
    files: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    models: set[str] = field(default_factory=set)
    db_changed: bool = False
    times: list[float] = field(default_factory=list)  # секунды на каждый ответ кейса
    first_words: list[float] = field(default_factory=list)  # секунды до первого слова
    usage: Counter[str] = field(default_factory=Counter)  # токены, шаги, контейнеры за весь кейс
    error: str | None = None


def numbers(text: str) -> list[Decimal]:
    text = DATE_RE.sub(" ", text)
    return [Decimal(re.sub(r"[   ]", "", n).replace(",", ".")) for n in NUMBER_RE.findall(text)]


def _same(shown: Decimal, exact: Decimal) -> bool:
    """Совпадает с учётом округления в ответе: 194,48 и 194,4832 — да; 194 и 195 — нет."""
    step = Decimal(1).scaleb(min(0, shown.normalize().as_tuple().exponent))
    return exact.quantize(step, rounding=ROUND_HALF_UP) == shown.quantize(step)


def has_number(text: str, expected: Decimal) -> bool:
    candidates = [n for n in numbers(text) if abs(n - expected) < 1 or n == expected]
    return any(_same(n, expected) for n in candidates)


def ungrounded(answer: str, sources: list[str]) -> list[Decimal]:
    """Числа ответа, которых нет в данных. Маленькие целые (до 12) — нумерация и счёт, их не считаем."""
    known = [n for s in sources for n in numbers(s)]
    answer = SHORT_DATE_RE.sub(" ", answer)
    return [
        n for n in numbers(answer) if not (n <= 12 and n == int(n)) and not any(_same(n, k) for k in known)
    ]


def run_checks(case: dict[str, Any], result: Result, expected: dict[str, Any]) -> list[tuple[str, bool]]:
    answer = result.answer.lower()
    checks: list[tuple[str, bool]] = [
        ("ответ завершён", bool(result.statuses) and all(s == "complete" for s in result.statuses)),
        ("база не изменилась", not result.db_changed),
    ]
    for name in case.get("tools", []):
        checks.append((f"вызван {name}", name in result.tools))
    if any_tools := case.get("any_tools"):
        checks.append((f"вызван один из: {', '.join(any_tools)}", bool(set(any_tools) & set(result.tools))))
    for name in case.get("forbid_tools", []):
        checks.append((f"не вызван {name}", name not in result.tools))
    if case.get("code"):
        checks.append(("выполнен код", result.code_runs > 0))
    if suffix := case.get("file"):
        checks.append((f"создан файл {suffix}", any(f.endswith(suffix) for f in result.files)))
    if "proposal" in case:
        wanted = [case["proposal"]] if case["proposal"] else []
        checks.append((f"предложения: {wanted or 'нет'}", result.proposals == wanted))
    for name in case.get("expect_numbers", []):
        checks.append((f"число {name}={expected[name]:.2f}", has_number(result.answer, expected[name])))
    for value in case.get("literal_numbers", []):
        checks.append((f"число {value}", has_number(result.answer, Decimal(value))))
    for name in case.get("expect_texts", []):
        checks += [(f"есть «{text}» ({name})", text.lower() in answer) for text in expected[name]]
    for text in case.get("must", []):
        checks.append((f"есть «{text}»", text.lower() in answer))
    if options := case.get("must_any"):
        checks.append(("есть нужная формулировка", any(o.lower() in answer for o in options)))
    for text in case.get("must_not", []):
        checks.append((f"нет «{text}»", text.lower() not in answer))
    checks.append(("деньги до копеек", not LONG_MONEY_RE.search(result.answer)))
    extra = ungrounded(result.answer, [*result.sources, *case["ask"]])
    checks.append((f"все числа из данных{f' (лишние: {extra})' if extra else ''}", not extra))
    return checks
