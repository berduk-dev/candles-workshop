"""Мок сервиса курсов ЦБ.

Курсы детерминированные (одна и та же дата — всегда один и тот же курс), поэтому демо-данные
и тесты воспроизводимы. В выходные, праздники и будущие даты курса нет — ответ 404,
как у настоящего ЦБ, который не публикует курс на нерабочие дни.
"""

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Мок курсов ЦБ")

# Нерабочие праздничные дни (месяц, день) — без переносов, для мока достаточно.
HOLIDAYS = {(1, d) for d in range(1, 9)} | {(2, 23), (3, 8), (5, 1), (5, 9), (6, 12), (11, 4)}

BASE_RATES = {"USD": 92.0, "EUR": 99.5}
PHASE = {"USD": 0.0, "EUR": 1.3}


def has_rate(day: date) -> bool:
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    return day <= today and day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS


def rate_for(currency: str, day: date) -> Decimal:
    n = day.toordinal()
    # Медленный тренд + недельные колебания ±1.5 руб.
    value = (
        BASE_RATES[currency]
        + 3.0 * math.sin(n / 60 + PHASE[currency])
        + 1.5 * math.sin(n / 7 + PHASE[currency])
    )
    return Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/rates/{day}")
def rates(day: date) -> dict:
    if not has_rate(day):
        raise HTTPException(status_code=404, detail="На эту дату курс не устанавливался")
    return {"date": day.isoformat(), "base": "RUB", "rates": {c: str(rate_for(c, day)) for c in BASE_RATES}}
