from datetime import date
from decimal import Decimal

import pytest

from app.errors import ExternalServiceError
from app.models import Currency
from app.services.fx import get_rate

FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)
SUNDAY = date(2026, 10, 11)


async def test_rub_rate_is_one_without_request(session, fx, fake_fx):
    quote = await get_rate(session, fx, Currency.RUB, SATURDAY)
    assert quote.rate == Decimal(1)
    assert fake_fx.calls == []


async def test_weekday_rate_is_taken_for_that_day(session, fx, fake_fx):
    quote = await get_rate(session, fx, Currency.USD, FRIDAY)
    assert quote.rate_date == FRIDAY
    assert quote.rate == fake_fx.rate("USD", FRIDAY)


@pytest.mark.parametrize("weekend_day", [SATURDAY, SUNDAY])
async def test_weekend_takes_last_previous_rate(session, fx, fake_fx, weekend_day):
    quote = await get_rate(session, fx, Currency.EUR, weekend_day)
    assert quote.rate_date == FRIDAY
    assert quote.rate == fake_fx.rate("EUR", FRIDAY)


async def test_rate_is_cached_after_first_request(session, fx, fake_fx):
    await get_rate(session, fx, Currency.USD, FRIDAY)
    await session.commit()
    fake_fx.calls.clear()

    # Другая валюта того же дня тоже уже в кэше: сервис отдаёт все курсы дня разом.
    quote = await get_rate(session, fx, Currency.EUR, FRIDAY)
    assert quote.rate == fake_fx.rate("EUR", FRIDAY)
    assert fake_fx.calls == []


async def test_service_down_gives_human_message(session, fx, fake_fx):
    fake_fx.down = True
    with pytest.raises(ExternalServiceError, match="сервис курсов недоступен"):
        await get_rate(session, fx, Currency.USD, FRIDAY)
