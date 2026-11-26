"""Промпты лежат отдельными файлами рядом; здесь только их загрузка."""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DIR = Path(__file__).parent


@lru_cache
def load_text(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


@lru_cache
def tool_texts() -> dict[str, Any]:
    return yaml.safe_load(load_text("tools.yaml"))


@lru_cache
def note_texts() -> dict[str, str]:
    return yaml.safe_load(load_text("notes.yaml"))


def note(key: str, /, **values: Any) -> str:
    """Короткий текст для модели из notes.yaml: пояснение к результату инструмента или пометка в истории.
    «/» делает key только позиционным: среди подстановок может быть и своя {key}, и {name}."""
    return note_texts()[key].format(**values)
