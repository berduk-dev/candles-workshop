"""Сами машинные проверки эвалов тоже надо проверять: ошибка в них прячет ошибку ассистента."""

from decimal import Decimal

import pytest

from app.config import get_settings
from evals.checks import LONG_MONEY_RE, has_number, numbers, ungrounded
from evals.run import percentile, token_cost


def test_numbers_understands_russian_format_and_skips_codes_and_dates():
    text = "Партия П-000012 от 24.11.2026: 1 234,50 кг, SUP-001, 12 шт, 18:30"
    assert numbers(text) == [Decimal("1234.50"), Decimal("12")]
    assert numbers('{"total": "9724.16", "qty": "-10.8"}') == [Decimal("9724.16"), Decimal("10.8")]


def test_rounding_is_allowed_but_wrong_number_is_not():
    assert has_number("Себестоимость 194,48 ₽", Decimal("194.4832"))
    assert has_number("около 194 ₽", Decimal("194.4832"))
    assert not has_number("Себестоимость 195,48 ₽", Decimal("194.4832"))


def test_invented_numbers_are_found():
    sources = ['{"qty_free": "82.1"}']
    assert ungrounded("Свободно 82,1 кг, 2 партии", sources) == []
    assert ungrounded("Свободно 82,1 кг и ещё 37 кг в пути", sources) == [Decimal("37")]
    assert ungrounded("За октябрь 2026 и в ноябре 2026 года: 82,1 кг", sources) == []
    assert ungrounded("Март: 2026 шт.", sources) == [Decimal("2026")]
    assert ungrounded("Выдача 5.10 и 12.10, неделя 23–29 ноября 2026: 82,1 кг", sources) == []


@pytest.mark.parametrize("text", ["на **8,8737 ₽**", "201.2791 руб.", "1,005 €"])
def test_money_longer_than_kopecks_is_found(text):
    assert LONG_MONEY_RE.search(text)


@pytest.mark.parametrize("text", ["8,87 ₽", "0,125 кг по 310 ₽/кг", "9 724,16 ₽", "курс 92,1234"])
def test_normal_money_is_fine(text):
    assert not LONG_MONEY_RE.search(text)


def test_percentile_is_a_real_answer_not_an_average():
    assert percentile([1, 2, 3, 100], 0.95) == 100
    assert percentile([3, 1, 2], 0.5) == 2


@pytest.mark.parametrize(("tier", "expected"), [("", 0.00024), ("fast", 0.00048)])
def test_token_cost_counts_cache_and_tier(monkeypatch, tier, expected):
    monkeypatch.setattr(get_settings(), "llm_service_tier", tier)
    # 1000 обычных + 1000 из кэша (×0,1) + 800 записано (×1,25) по $0,10 за млн и 60 на выходе по $0,50.
    s = {"input": 2800, "cached": 1000, "written": 800, "output": 60}
    assert token_cost("gpt-6-luna", s) == pytest.approx(expected)
    assert token_cost("unknown-model", s) is None
