"""Курсы валют из мок-сервиса ЦБ.

Правило: курс берётся на дату поступления; если на эту дату курса нет (выходной, праздник),
берётся последний предыдущий. Полученные курсы кэшируются в таблице fx_rates.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ExternalServiceError
from app.formatting import fmt_date
from app.models import Currency, FxRate

# Дольше 10 дней подряд без курса не бывает даже на новогодних праздниках.
MAX_LOOKBACK_DAYS = 10


@dataclass(frozen=True)
class FxQuote:
    currency: Currency
    rate: Decimal
    rate_date: date


class FxClient:
    """HTTP-клиент мок-сервиса курсов. `transport` подменяется в тестах — тесты не ходят в сеть."""

    def __init__(
        self, base_url: str, *, timeout: float = 5.0, transport: httpx.AsyncBaseTransport | None = None
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, transport=transport, trust_env=False
        )

    async def fetch_day(self, day: date) -> dict[Currency, Decimal] | None:
        """Курсы на день или None, если в этот день курса нет."""
        response = await self._http.get(f"/rates/{day.isoformat()}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        rates = response.json()["rates"]
        known = {c.value for c in Currency}
        return {Currency(code): Decimal(str(value)) for code, value in rates.items() if code in known}

    async def aclose(self) -> None:
        await self._http.aclose()


async def get_rate(session: AsyncSession, fx: FxClient, currency: Currency, on_date: date) -> FxQuote:
    if currency == Currency.RUB:
        return FxQuote(currency, Decimal(1), on_date)

    for offset in range(MAX_LOOKBACK_DAYS + 1):
        day = on_date - timedelta(days=offset)
        cached = await session.get(FxRate, (day, currency))
        if cached is not None:
            return FxQuote(currency, cached.rate, day)

        try:
            rates = await fx.fetch_day(day)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise ExternalServiceError(
                f"Не удалось получить курс {currency.value} на {fmt_date(on_date)}: "
                "сервис курсов недоступен. Попробуйте позже."
            ) from exc
        if rates is None:
            continue

        # Кэшируем все валюты дня; гонка двух запросов безопасна благодаря ON CONFLICT.
        await session.execute(
            insert(FxRate)
            .values([{"rate_date": day, "currency": code, "rate": rate} for code, rate in rates.items()])
            .on_conflict_do_nothing()
        )
        if currency not in rates:
            raise ExternalServiceError(f"Сервис курсов не вернул курс {currency.value} на {fmt_date(day)}.")
        return FxQuote(currency, rates[currency], day)

    raise ExternalServiceError(
        f"Нет курса {currency.value} ни на {fmt_date(on_date)}, ни за {MAX_LOOKBACK_DAYS} предыдущих дней."
    )
