"""Числа и даты в сообщениях для людей: «2,5 кг», «12.10.2026»."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.0001")
RUB_STEP = Decimal("0.01")


def q_qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def q_rub(value: Decimal) -> Decimal:
    return value.quantize(RUB_STEP, rounding=ROUND_HALF_UP)


def fmt_number(value: Decimal) -> str:
    """12.500 → «12,5», 3.000 → «3»: без лишних нулей, с запятой."""
    text = f"{value.normalize():f}"  # normalize убирает хвостовые нули, :f — без записи вида 1E+2
    return text.replace(".", ",")


def fmt_qty(value: Decimal, unit: str) -> str:
    return f"{fmt_number(value)} {unit}"


def fmt_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")
