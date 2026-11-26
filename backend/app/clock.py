from datetime import date, datetime

from app.config import get_settings


def now() -> datetime:
    return datetime.now(get_settings().tz)


def today() -> date:
    """«Сегодня» в часовом поясе мастерской, а не сервера."""
    return now().date()
