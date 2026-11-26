"""Начальные формы русских слов (pymorphy3): «воскового» → «восковой». Нужны для поиска материалов
по-человечески и для маскирования имён в любом падеже."""

import re
from functools import lru_cache

import pymorphy3

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*")
_PERSON_TAGS = {"Surn", "Name", "Patr"}


@lru_cache
def _analyzer() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def _norm(word: str) -> str:
    return word.lower().replace("ё", "е")


@lru_cache(maxsize=50_000)
def lemmas(word: str) -> frozenset[str]:
    """Все возможные начальные формы слова (и само слово)."""
    return frozenset(_norm(p.normal_form) for p in _analyzer().parse(word)) | {_norm(word)}


@lru_cache(maxsize=50_000)
def person_lemmas(word: str) -> frozenset[str]:
    """Начальные формы слова как фамилии, имени или отчества. Пусто, если оно на них не похоже."""
    return frozenset(_norm(p.normal_form) for p in _analyzer().parse(word) if _PERSON_TAGS & p.tag.grammemes)


@lru_cache(maxsize=50_000)
def is_only_surname(word: str) -> bool:
    """У слова нет других значений, кроме фамилии: «петрову» — да, «кузнецы» — нет.
    Poss — притяжательное прилагательное, у словаря так размечены многие фамилии на «-ов»."""
    return all({"Surn", "Poss"} & p.tag.grammemes for p in _analyzer().parse(word))


@lru_cache(maxsize=50_000)
def is_first_name(word: str) -> bool:
    best = _analyzer().parse(word)
    return bool(best) and any("Name" in p.tag.grammemes and p.score > 0.1 for p in best)


@lru_cache(maxsize=200_000)
def _distance(a: str, b: str) -> int:
    """Расстояние Левенштейна: сколько букв вставить, удалить или заменить, чтобы из a получить b."""
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def _typo_limit(word: str) -> int:
    """Сколько опечаток прощаем: в коротких словах и числах — ни одной, до 6 букв — одну, дальше — две."""
    if len(word) < 4 or any(c.isdigit() for c in word):
        return 0
    return 1 if len(word) <= 6 else 2


def similar(word: str, forms: frozenset[str]) -> bool:
    """Слово совпадает с одной из форм по начальной форме или отличается опечаткой:
    «двара» ~ «двор», «Васкового» ~ «восковой»."""
    mine = lemmas(word)
    if mine & forms:
        return True
    return any(
        abs(len(a) - len(b)) <= 2 and _distance(a, b) <= min(_typo_limit(a), _typo_limit(b))
        for a in mine
        for b in forms
    )


def similar_text(query: str, text: str) -> bool:
    """Как same_text, но прощает опечатки. Для запасного поиска, когда точный ничего не нашёл."""
    text_words = [lemmas(w) for w in WORD_RE.findall(text)]
    query_words = WORD_RE.findall(query)
    return bool(query_words) and all(any(similar(q, t) for t in text_words) for q in query_words)


def same_text(query: str, text: str) -> bool:
    """Все слова запроса есть в тексте с точностью до формы: «соевого воска» ~ «Воск соевый»."""
    text_words = [lemmas(w) for w in WORD_RE.findall(text)]
    query_words = WORD_RE.findall(query)
    return bool(query_words) and all(any(lemmas(q) & t for t in text_words) for q in query_words)
