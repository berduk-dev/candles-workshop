from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Все настройки — только из переменных окружения (или .env при локальном запуске)."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://candles:candles@localhost:5432/candles"

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_ttl_minutes: int = 12 * 60
    cookie_secure: bool = False

    fx_base_url: str = "http://localhost:8081"
    fx_timeout_seconds: float = 5.0

    redis_url: str = "redis://localhost:6379"
    marking_base_url: str = "http://localhost:8082"
    marking_timeout_seconds: float = 5.0

    # Ассистент. Без ключа учётная часть работает, ассистент отвечает понятной ошибкой.
    openai_api_key: str = ""
    llm_primary_model: str = "gpt-6-luna"
    llm_fallback_model: str = "gpt-5.4-mini"
    # Пусто — обычный тариф, «fast» — быстрый (токены вдвое дороже). Выключен: на эвалах он быстрее
    # на 0,7–1,4 с, но чаще ошибается в выборе инструмента (замеры — в evals/README.md).
    llm_service_tier: str = ""
    # Сколько модель «думает» перед ответом: none, low, medium, high. Пусто — как решит модель.
    llm_reasoning_effort: str = ""
    llm_call_timeout_seconds: float = 180.0
    llm_first_event_timeout_seconds: float = 20.0  # тишина дольше — переходим на запасную модель
    llm_stub: bool = False  # заглушка вместо модели — для E2E-тестов
    storage_dir: str = "storage"

    # Часовой пояс мастерской: от него считаются «сегодня», «эта неделя», «пятница».
    timezone: str = "Europe/Moscow"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


@lru_cache
def get_settings() -> Settings:
    return Settings()
