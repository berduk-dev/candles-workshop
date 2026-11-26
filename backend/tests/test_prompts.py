"""Тексты для модели лежат файлами в prompts/. Проверяем, что код и файлы не разошлись:
опечатка в ключе иначе всплыла бы только в живом диалоге."""

import re
from pathlib import Path

from app.assistant import tools
from app.assistant.prompts import note, note_texts, tool_texts

APP = Path(__file__).parent.parent / "app"


def test_code_and_notes_file_use_the_same_keys():
    used = {
        key
        for path in APP.rglob("*.py")
        for key in re.findall(r'\bnote\("(\w+)"', path.read_text(encoding="utf-8"))
    }
    assert used == set(note_texts())


def test_every_tool_has_a_description_for_the_model():
    assert set(tools.TOOLS) == set(tool_texts())


def test_note_substitutes_values_even_named_like_its_own_argument():
    text = note("similar_supplier", query="Васковый двар", name="SUP-001")
    assert text == "Поставщика «Васковый двар» нет; показаны партии похожего: SUP-001."
