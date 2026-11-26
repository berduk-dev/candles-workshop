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
