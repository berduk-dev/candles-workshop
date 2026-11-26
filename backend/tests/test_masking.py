import pytest

from app.assistant.masking import ALIAS_RE, Masker, StreamUnmasker

USERS = [
    (1, "Петров Иван Сергеевич"),
    (2, "Смирнова Анна Викторовна"),
    (3, "Кузнецов Олег Петрович"),
    (4, "Волкова Мария Андреевна"),
]
SUPPLIERS = [
    (1, "ООО «Восковой двор»"),
    (2, "Aroma Trade GmbH"),
    (3, "ООО «Стеклотара Плюс»"),
    (4, "Типография «Оттиск»"),
]


@pytest.fixture
def masker() -> Masker:
    return Masker(USERS, SUPPLIERS)


@pytest.mark.parametrize(
    "spelling",
    [
        "ООО Восковой двор",
        "ООО «Восковой двор»",
        'ООО "Восковой двор"',
        "Восковой двор, ООО",
        "Восковой двор",
        "восковой  двор",
        "«Восковой двор»",
        "ВОСКОВОЙ ДВОР",
    ],
)
def test_supplier_has_one_alias_in_any_spelling(masker, spelling):
    assert masker.mask_text(f"Поставка: {spelling}.") == "Поставка: SUP-001."


def test_supplier_in_other_cases(masker):
    assert masker.mask_text("Что пришло от Воскового двора?") == "Что пришло от SUP-001?"
    assert masker.mask_text("Мы платим Восковому двору") == "Мы платим SUP-001"
    assert masker.mask_text("Заказали в Оттиске этикетки") == "Заказали в SUP-004 этикетки"
    assert masker.mask_text("Aroma Trade прислала счёт") == "SUP-002 прислала счёт"


@pytest.mark.parametrize(
    "spelling",
    [
        "Петров Иван",
        "Петров Иван Сергеевич",
        "Иван Петров",
        "Иван Сергеевич Петров",
        "Иван Сергеевич",
        "Петрову И.",
        "Петрову И. С.",
        "Петрова И.С.",
        "И. Петров",
        "И.С. Петрову",
        "Петровым",
        "петров иван",
        "петрову",
    ],
)
def test_person_has_one_alias_in_any_spelling(masker, spelling):
    assert masker.mask_text(f"передал {spelling}") == "передал EMP-001"


def test_female_surname_in_cases(masker):
    assert masker.mask_text("Спроси у Смирновой Анны") == "Спроси у EMP-002"
    assert masker.mask_text("Задание Волковой") == "Задание EMP-004"


def test_ordinary_words_are_not_masked(masker):
    text = "Восковой слой, двор склада, кузнецы и волки, оттиск на этикетке, Иван-чай, парафин"
    assert masker.mask_text(text) == text


def test_several_names_in_one_text(masker):
    masked = masker.mask_text("Петров Иван принял партию от «Восковой двор, ООО», подписала Смирнова А. В.")
    assert masked == "EMP-001 принял партию от «SUP-001», подписала EMP-002" or (
        "EMP-001" in masked and "SUP-001" in masked and "EMP-002" in masked
    )
    assert not any(name in masked for name in ("Петров", "Восковой", "Смирнова"))


def test_two_suppliers_in_a_row(masker):
    masked = masker.mask_text("Восковой двор, ООО «Стеклотара Плюс»")
    assert masked == "SUP-001, SUP-003"


def test_namesake_with_other_first_name_is_not_our_employee(masker):
    masked = masker.mask_text("Звонил Петров Сергей")
    assert "Петров" not in masked and "EMP-001" not in masked
    assert masker.unmask_text(masked) == "Звонил Петров Сергей"


def test_same_surname_of_two_employees_is_not_guessed():
    masker = Masker([(1, "Петров Иван Сергеевич"), (2, "Петров Олег Ильич")], [])
    assert masker.mask_text("Петров Олег") == "EMP-002"
    masked = masker.mask_text("Спроси Петрова")
    assert ALIAS_RE.search(masked) and "EMP-001" not in masked and "EMP-002" not in masked


def test_unknown_company_is_masked_and_restored(masker):
    first = masker.mask_text("Новый поставщик ООО «Ромашка» и ИП Сидоров")
    assert "Ромашка" not in first and "Сидоров" not in first
    assert masker.unmask_text(first) == "Новый поставщик ООО «Ромашка» и ИП Сидоров"
    # Другое написание — тот же псевдоним.
    again = masker.mask_text("«Ромашка», ООО")
    assert again in first


def test_masking_twice_changes_nothing(masker):
    once = masker.mask_text("Петров Иван, ООО «Восковой двор», ООО «Ромашка»")
    assert masker.mask_text(once) == once


def test_round_trip_in_nested_data(masker):
    masked = masker.mask_obj({"supplier": "Восковой двор, ООО", "rows": [["Петрову И.", 5]], "n": None})
    assert masked == {"supplier": "SUP-001", "rows": [["EMP-001", 5]], "n": None}
    assert masker.unmask_obj(masked) == {
        "supplier": "ООО «Восковой двор»",
        "rows": [["Петров Иван Сергеевич", 5]],
        "n": None,
    }


@pytest.mark.parametrize("size", [1, 2, 3, 5, 100])
def test_stream_unmask_survives_split_codes(masker, size):
    text = "Партию SUP-001 принял EMP-001. EMP — не код, SUPER тоже, код SUP-999 неизвестен. Итог: SUP-002"
    stream = StreamUnmasker(masker)
    out = "".join(stream.feed(text[i : i + size]) for i in range(0, len(text), size)) + stream.flush()
    assert out == (
        "Партию ООО «Восковой двор» принял Петров Иван Сергеевич. EMP — не код, SUPER тоже, "
        "код SUP-999 неизвестен. Итог: Aroma Trade GmbH"
    )
