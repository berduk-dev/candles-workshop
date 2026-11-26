from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.formatting import q_money
from app.models import AuditLog, StockMovement, Supplier
from tests.conftest import auth_headers

FRIDAY = date(2026, 10, 9)
SATURDAY = date(2026, 10, 10)


def receipt_payload(**overrides) -> dict:
    payload = {
        "supplier_name": "ООО «Восковой двор»",
        "received_at": FRIDAY.isoformat(),
        "lines": [
            {"material_code": "WAX-SOY", "qty": "120", "price": "310", "currency": "RUB"},
            {"material_code": "FR-LAV", "qty": "8.5", "price": "48.20", "currency": "EUR"},
            {"material_code": "JAR-200", "qty": "1500", "price": "0.42", "currency": "USD"},
        ],
    }
    payload.update(overrides)
    return payload


async def test_receipt_lines_become_batches_with_rub_cost(client, users, materials, fake_fx):
    headers = await auth_headers(client, "storekeeper")
    response = await client.post("/api/receipts", json=receipt_payload(), headers=headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["number"] == "ПТ-000001"
    batches = {b["material"]["code"]: b for b in body["batches"]}
    assert [b["number"] for b in body["batches"]] == ["П-000001", "П-000002", "П-000003"]
    assert all(b["status"] == "on_check" for b in body["batches"])

    assert Decimal(batches["WAX-SOY"]["unit_cost_rub"]) == Decimal("310")
    eur = fake_fx.rate("EUR", FRIDAY)
    assert Decimal(batches["FR-LAV"]["fx_rate"]) == eur
    assert Decimal(batches["FR-LAV"]["unit_cost_rub"]) == q_money(Decimal("48.20") * eur)
    usd = fake_fx.rate("USD", FRIDAY)
    assert Decimal(batches["JAR-200"]["unit_cost_rub"]) == q_money(Decimal("0.42") * usd)
    # Курс по каждой валюте запрошен один раз на всё поступление.
    assert fake_fx.calls == [FRIDAY]


async def test_weekend_receipt_uses_friday_rate(client, users, materials, fake_fx):
    headers = await auth_headers(client, "storekeeper")
    response = await client.post(
        "/api/receipts", json=receipt_payload(received_at=SATURDAY.isoformat()), headers=headers
    )

    assert response.status_code == 201, response.text
    usd_batch = next(b for b in response.json()["batches"] if b["currency"] == "USD")
    assert usd_batch["fx_rate_date"] == FRIDAY.isoformat()
    assert Decimal(usd_batch["fx_rate"]) == fake_fx.rate("USD", FRIDAY)


async def test_receipt_writes_movements_and_audit(client, users, materials, session):
    headers = await auth_headers(client, "storekeeper")
    await client.post("/api/receipts", json=receipt_payload(), headers=headers)

    assert await session.scalar(select(func.count(StockMovement.id))) == 3
    entries = list(await session.scalars(select(AuditLog).order_by(AuditLog.id)))
    assert [(e.entity, e.action) for e in entries] == [("batch", "created")] * 3 + [("receipt", "created")]
    assert entries[0].user_full_name == "Петров Иван Сергеевич"
    assert entries[0].before is None
    assert entries[0].after["status"] == "on_check"


async def test_technologist_cannot_create_receipt(client, users, materials):
    headers = await auth_headers(client, "technologist")
    response = await client.post("/api/receipts", json=receipt_payload(), headers=headers)

    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "Недостаточно прав: оформлять поступления может Кладовщик или Руководитель."
    )


async def test_future_date_is_rejected(client, users, materials):
    headers = await auth_headers(client, "storekeeper")
    response = await client.post(
        "/api/receipts", json=receipt_payload(received_at="2099-01-01"), headers=headers
    )

    assert response.status_code == 422
    assert "ещё не наступила" in response.json()["detail"]


async def test_unknown_material_is_reported_by_code(client, users, materials):
    headers = await auth_headers(client, "storekeeper")
    payload = receipt_payload(
        lines=[{"material_code": "nope-1", "qty": "1", "price": "1", "currency": "RUB"}]
    )
    response = await client.post("/api/receipts", json=payload, headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Не найдены материалы с кодами: NOPE-1."


async def test_invalid_quantity_gives_readable_error(client, users, materials):
    headers = await auth_headers(client, "storekeeper")
    payload = receipt_payload(
        lines=[{"material_code": "WAX-SOY", "qty": "0", "price": "1", "currency": "RUB"}]
    )
    response = await client.post("/api/receipts", json=payload, headers=headers)

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Проверьте данные: строки поступления → №1 → количество: должно быть больше 0."
    )


async def test_supplier_spellings_map_to_one_supplier(client, users, materials, session):
    headers = await auth_headers(client, "storekeeper")
    for name in ["ООО «Восковой двор»", "Восковой двор, ООО", "восковой  ДВОР"]:
        response = await client.post(
            "/api/receipts", json=receipt_payload(supplier_name=name), headers=headers
        )
        assert response.status_code == 201, response.text

    assert await session.scalar(select(func.count(Supplier.id))) == 1
