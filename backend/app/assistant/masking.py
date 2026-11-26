"""Маскирование персональных данных.

Сотрудники и поставщики заменяются кодами (EMP-003, SUP-001) до отправки в LLM и возвращаются в ответе.
Код — это id записи в базе, поэтому он один и тот же при любом написании и в любом диалоге.
Слова сравниваются по начальной форме, так что падежи и порядок слов не мешают:
«ООО Восковой двор», «Восковой двор, ООО», «Восковому двору» → SUP-001;
«Петров Иван», «Петрову И.», «И. С. Петров» → EMP-001.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Pseudonym, Supplier, User
from app.services.morph import WORD_RE, is_first_name, is_only_surname, lemmas, person_lemmas, similar
from app.services.suppliers import LEGAL_FORMS, supplier_norm_key

ALIAS_RE = re.compile(r"(?:SUP|EMP)-(?:X[0-9A-F]{6}|\d{3,})")
# Хвост текста, который может оказаться началом кода: при стриминге его придерживаем.
_ALIAS_TAIL_RE = re.compile(r"(?:S|SU|SUP|E|EM|EMP|(?:SUP|EMP)-(?:X[0-9A-F]{0,6}|\d*))$")

_GAP_RE = re.compile(r"[\s«»\"'“”„.,]{0,4}")
_OPEN, _CLOSE = "«\"'“„", "»\"'”“"
_FORMS = "|".join(sorted((f for f in LEGAL_FORMS), key=len, reverse=True))
_NAME = r"[A-ZА-ЯЁ][\wА-Яа-яЁё-]*\.?"
# Организации, которых нет в базе: «ООО «Ромашка»», «ИП Сидоров А. В.», «Ромашка, ООО».
_UNKNOWN_ORG_RE = re.compile(
    rf"\b(?i:{_FORMS})\s+(?:[«\"“„][^»\"”“\n]{{2,80}}[»\"”“]|{_NAME}(?:\s+{_NAME}){{0,2}})"
    rf"|[«\"“„][^»\"”“\n]{{2,80}}[»\"”“],?\s+(?i:{_FORMS})\b"
    rf"|{_NAME}(?:\s+{_NAME}){{0,2}},\s*(?i:{_FORMS})\b"
)


@dataclass(frozen=True)
class _Word:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class _Person:
    alias: str
    surname: frozenset[str]
    name: frozenset[str]
    patronymic: frozenset[str]
    initials: tuple[str, str]


def _person(user_id: int, full_name: str) -> _Person:
    surname, name, patronymic = ([*full_name.split(), "", ""])[:3]

    def forms(word: str) -> frozenset[str]:
        return (person_lemmas(word) or lemmas(word)) if word else frozenset()

    return _Person(
        alias=f"EMP-{user_id:03d}",
        surname=forms(surname),
        name=forms(name),
        patronymic=forms(patronymic),
        initials=(name[:1].upper(), patronymic[:1].upper()),
    )


class Masker:
    """Двусторонняя замена имён. Один объект — на один запрос пользователя."""

    def __init__(
        self,
        users: list[tuple[int, str]],
        suppliers: list[tuple[int, str]],
        pseudonyms: dict[str, str] | None = None,
    ):
        self._names: dict[str, str] = dict(pseudonyms or {})
        self._people: list[_Person] = []
        # первая лемма названия → [(леммы всех слов, псевдоним, нужна ли заглавная буква)]
        self._suppliers: dict[str, list[tuple[list[frozenset[str]], str, bool]]] = {}
        self._new: dict[str, str] = {}
        # Названия из нескольких слов: их узнаём и с опечатками («Васковый двар»).
        self._multiword: list[tuple[list[frozenset[str]], str, bool]] = []
        for user_id, full_name in users:
            person = _person(user_id, full_name)
            self._people.append(person)
            self._names[person.alias] = full_name
        for supplier_id, name in suppliers:
            alias = f"SUP-{supplier_id:03d}"
            self._names[alias] = name
            self._add_supplier(supplier_norm_key(name), alias)
            # Собственное имя в кавычках встречается и отдельно: «Типография «Оттиск»» → «Оттиск».
            if quoted := re.search(r"[«\"“„]([^»\"”“]+)[»\"”“]", name):
                self._add_supplier(supplier_norm_key(quoted.group(1)), alias)

    def _add_supplier(self, key: str, alias: str) -> None:
        words = [lemmas(w) for w in WORD_RE.findall(key)]
        if not words:
            return
        for first in words[0]:
            self._suppliers.setdefault(first, []).append((words, alias, len(words) == 1))
        if len(words) > 1:
            self._multiword.append((words, alias, False))

    @classmethod
    async def load(cls, session: AsyncSession) -> "Masker":
        users = (await session.execute(select(User.id, User.full_name))).tuples().all()
        suppliers = (await session.execute(select(Supplier.id, Supplier.name))).tuples().all()
        pseudonyms = (await session.execute(select(Pseudonym.alias, Pseudonym.original))).tuples().all()
        return cls(list(users), list(suppliers), dict(pseudonyms))

    async def save(self, session: AsyncSession) -> None:
        """Сохраняет псевдонимы новых имён, чтобы раскрыть их и в следующих сообщениях."""
        if self._new:
            await session.execute(
                insert(Pseudonym)
                .values([{"alias": a, "original": o[:300]} for a, o in self._new.items()])
                .on_conflict_do_nothing()
            )
            self._new.clear()

    # ---------- имена → коды ----------

    def mask_text(self, text: str) -> str:
        words = [_Word(m.group(), m.start(), m.end()) for m in WORD_RE.finditer(text)]
        result, position, i = [], 0, 0
        while i < len(words):
            found = self._match_supplier(text, words, i) or self._match_person(text, words, i)
            if found is None:
                i += 1
                continue
            alias, start, end = found
            result += [text[position:start], alias]
            position = end
            while i < len(words) and words[i].start < end:
                i += 1
        result.append(text[position:])
        return _UNKNOWN_ORG_RE.sub(self._mask_unknown_org, "".join(result))

    def mask_obj(self, value: Any) -> Any:
        return _map_strings(value, self.mask_text)

    def _mask_unknown_org(self, match: re.Match) -> str:
        if ALIAS_RE.search(match.group()):
            return match.group()
        return self._external("SUP", supplier_norm_key(match.group()), match.group())

    def _external(self, prefix: str, key: str, original: str) -> str:
        """Псевдоним для имени не из базы: зависит только от самого имени, поэтому стабилен."""
        secret = get_settings().jwt_secret
        alias = f"{prefix}-X{hashlib.sha256(f'{secret}:{key}'.encode()).hexdigest()[:6].upper()}"
        if alias not in self._names:
            self._names[alias] = self._new[alias] = original
        return alias

    def _match_supplier(self, text: str, words: list[_Word], i: int) -> tuple[str, int, int] | None:
        best: tuple[str, int] | None = None
        exact = [c for first in lemmas(words[i].text) for c in self._suppliers.get(first, ())]
        for pattern, alias, needs_capital in [*exact, *self._multiword]:
            if i + len(pattern) > len(words) or (best and len(pattern) <= best[1]):
                continue
            if needs_capital and not words[i].text[0].isupper():
                continue
            # Одно слово — только точное совпадение формы, иначе спутаем с обычными словами.
            fits = similar if len(pattern) > 1 else (lambda word, forms: bool(lemmas(word) & forms))
            if all(
                fits(words[i + k].text, pattern[k])
                and (k == 0 or _GAP_RE.fullmatch(text, words[i + k - 1].end, words[i + k].start))
                for k in range(len(pattern))
            ):
                best = (alias, len(pattern))
        if best is None:
            return None
        alias, count = best
        start, end = self._with_legal_form(text, words, i, i + count - 1)
        return alias, start, end

    @staticmethod
    def _with_legal_form(text: str, words: list[_Word], first: int, last: int) -> tuple[int, int]:
        """Расширяет найденное название на кавычки и «ООО» слева или справа."""
        start, end = words[first].start, words[last].end
        if start and text[start - 1] in _OPEN:
            start -= 1
            if end < len(text) and text[end] in _CLOSE:
                end += 1

        def is_form(index: int) -> bool:
            return 0 <= index < len(words) and words[index].text.lower() in LEGAL_FORMS

        if is_form(first - 1) and _GAP_RE.fullmatch(text, words[first - 1].end, start):
            start = words[first - 1].start
        elif is_form(last + 1) and _GAP_RE.fullmatch(text, end, words[last + 1].start):
            # «Восковой двор, ООО «Другая фирма»»: это ООО относится уже к следующему названию.
            after = text[words[last + 1].end :].lstrip()
            if not after[:1] or after[0] not in _OPEN:
                end = words[last + 1].end
        return start, end

    def _match_person(self, text: str, words: list[_Word], i: int) -> tuple[str, int, int] | None:
        def near(a: int, b: int) -> bool:
            return b < len(words) and bool(re.fullmatch(r"\.?\s*", text[words[a].end : words[b].start]))

        def is_initial(index: int, letter: str) -> bool:
            return index < len(words) and len(words[index].text) == 1 and words[index].text.upper() == letter

        def end_of(index: int) -> int:
            """Конец слова; у инициала — вместе с точкой."""
            end = words[index].end
            return end + 1 if len(words[index].text) == 1 and text[end : end + 1] == "." else end

        forms = person_lemmas(words[i].text)
        start = words[i].start
        for p in self._people:
            second, third = p.initials
            # Фамилия Имя [Отчество] / Фамилия И. [О.]
            if (
                forms & p.surname
                and near(i, i + 1)
                and (person_lemmas(words[i + 1].text) & p.name or is_initial(i + 1, second))
            ):
                last = i + 1
                if near(last, last + 1) and (
                    person_lemmas(words[last + 1].text) & p.patronymic or is_initial(last + 1, third)
                ):
                    last += 1
                return p.alias, start, end_of(last)
            # Имя [Отчество] [Фамилия] / И. [О.] Фамилия — одно имя без продолжения не маскируем
            if forms & p.name or is_initial(i, second):
                last = i
                if near(last, last + 1) and (
                    person_lemmas(words[last + 1].text) & p.patronymic or is_initial(last + 1, third)
                ):
                    last += 1
                if near(last, last + 1) and person_lemmas(words[last + 1].text) & p.surname:
                    return p.alias, start, words[last + 1].end
                if last > i and len(words[i].text) > 1 and len(words[last].text) > 1:
                    return p.alias, start, words[last].end
        return self._match_surname(words, i, near)

    def _match_surname(
        self, words: list[_Word], i: int, near: Callable[[int, int], bool]
    ) -> tuple[str, int, int] | None:
        word = words[i]
        people = [p for p in self._people if person_lemmas(word.text) & p.surname]
        if not people or not (word.text[0].isupper() or is_only_surname(word.text)):
            return None
        following = words[i + 1] if near(i, i + 1) else None
        if following and following.text[0].isupper() and is_first_name(following.text):
            # Однофамилец с другим именем — это не наш сотрудник, но имя всё равно скрываем.
            original = f"{word.text} {following.text}"
            key = " ".join(min(person_lemmas(w)) for w in original.split() if person_lemmas(w))
            return self._external("EMP", key, original), word.start, following.end
        if len(people) == 1:
            return people[0].alias, word.start, word.end
        # Два сотрудника с одной фамилией: угадывать нельзя, даём общий псевдоним.
        return self._external("EMP", min(people[0].surname), word.text), word.start, word.end

    # ---------- коды → имена ----------

    def unmask_text(self, text: str) -> str:
        return ALIAS_RE.sub(lambda m: self._names.get(m.group(), m.group()), text)

    def unmask_obj(self, value: Any) -> Any:
        return _map_strings(value, self.unmask_text)

    def legend(self, *texts: str) -> dict[str, str]:
        """Расшифровка кодов, встретившихся в текстах (для графиков: на картинке имя не заменить)."""
        found = {m.group() for text in texts for m in ALIAS_RE.finditer(text)}
        return {alias: self._names[alias] for alias in sorted(found) if alias in self._names}


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {key: _map_strings(item, fn) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, fn) for item in value]
    return value


class StreamUnmasker:
    """Обратная замена в потоке: код может прийти разрезанным («SUP-0» + «01»), поэтому хвост,
    похожий на начало кода, придерживается до следующего куска."""

    def __init__(self, masker: Masker):
        self._masker = masker
        self._tail = ""

    def feed(self, chunk: str) -> str:
        text = self._tail + chunk
        match = _ALIAS_TAIL_RE.search(text)
        self._tail = match.group() if match else ""
        return self._masker.unmask_text(text[: len(text) - len(self._tail)])

    def flush(self) -> str:
        text, self._tail = self._tail, ""
        return self._masker.unmask_text(text)
