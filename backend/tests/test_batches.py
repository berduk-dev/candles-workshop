import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.errors import NotFoundError
from app.models import BatchStatus, Role
from app.schemas.inventory import ReceiptCreate, ReceiptLineIn
from app.services import batches, receipts
from app.services import materials as materials_service
from tests.conftest import auth_headers


async def make_receipt(session, user, fx, lines, *, supplier="Восковой двор", day=date(2026, 10, 9)):
    receipt = await receipts.create_receipt(
        session,
        user,
        ReceiptCreate(
            supplier_name=supplier,
            received_at=day,
            lines=[
                ReceiptLineIn(material_code=code, qty=Decimal(qty), price=Decimal("10"), currency="RUB")
                for code, qty in lines
            ],
        ),
        fx,
    )
    await session.commit()
    return receipt


async def test_status_flow_on_check_to_accepted_is_final(client, session, users, materials, fx):
    receipt = await make_receipt(session, users[Role.STOREKEEPER], fx, [("WAX-SOY", "10")])
    batch_id = receipt.batches[0].id
    headers = await auth_headers(client, "storekeeper")

    accepted = await client.post(
        f"/api/batches/{batch_id}/status", json={"status": "accepted"}, headers=headers
    )
    assert accepted.status_code == 200
    assert accepted.json()["status_label"] == "Принята"

    again = await client.post(f"/api/batches/{batch_id}/status", json={"status": "rejected"}, headers=headers)
    assert again.status_code == 409
    assert again.json()["detail"] == (
        "Партия П-000001 уже в статусе «Принята». Статус меняется только у партий «На проверке»."
    )

    card = (await client.get(f"/api/batches/{batch_id}", headers=headers)).json()
    status_change = card["history"][-1]
    assert status_change["action"] == "status_changed"
    assert status_change["before"] == {"status": "on_check"}
    assert status_change["after"] == {"status": "accepted"}


async def test_technologist_cannot_change_status(client, session, users, materials, fx):
    receipt = await make_receipt(session, users[Role.STOREKEEPER], fx, [("WAX-SOY", "10")])
    headers = await auth_headers(client, "technologist")

    response = await client.post(
        f"/api/batches/{receipt.batches[0].id}/status", json={"status": "accepted"}, headers=headers
    )
    assert response.status_code == 403


async def test_stock_counts_only_accepted_batches(session, users, materials, fx):
    keeper = users[Role.STOREKEEPER]
    receipt = await make_receipt(
        session, keeper, fx, [("WAX-SOY", "10"), ("WAX-SOY", "5"), ("WAX-SOY", "2.5")]
    )
    accepted, rejected, _on_check = receipt.batches
    await batches.change_status(session, keeper, accepted.id, BatchStatus.ACCEPTED)
    await batches.change_status(session, keeper, rejected.id, BatchStatus.REJECTED)
    await session.commit()

    stock = await materials_service.get_stock(session, keeper, accepted.material.id)
    assert stock.qty_on_hand == Decimal("10")
    assert stock.qty_free == Decimal("10")
    assert stock.qty_on_check == Decimal("2.5")


async def test_registry_filters_sorting_and_pagination(client, session, users, materials, fx):
    keeper = users[Role.STOREKEEPER]
    await make_receipt(session, keeper, fx, [("WAX-SOY", "10"), ("FR-LAV", "2")], day=date(2026, 10, 1))
    await make_receipt(
        session, keeper, fx, [("WAX-SOY", "30")], supplier="Aroma Trade", day=date(2026, 10, 5)
    )
    await make_receipt(session, keeper, fx, [("JAR-200", "100")], day=date(2026, 10, 7))
    headers = await auth_headers(client, "technologist")

    page = (
        await client.get("/api/batches", params={"page_size": 2, "sort": "-received_at"}, headers=headers)
    ).json()
    assert page["total"] == 4
    assert [b["number"] for b in page["items"]] == ["П-000004", "П-000003"]

    page2 = (await client.get("/api/batches", params={"page_size": 2, "page": 2}, headers=headers)).json()
    assert [b["number"] for b in page2["items"]] == ["П-000002", "П-000001"]

    wax = (
        await client.get("/api/batches", params={"material": "соев", "sort": "qty_initial"}, headers=headers)
    ).json()
    assert [Decimal(b["qty_initial"]) for b in wax["items"]] == [Decimal("10"), Decimal("30")]

    aroma = (await client.get("/api/batches", params={"supplier": "aroma"}, headers=headers)).json()
    assert [b["number"] for b in aroma["items"]] == ["П-000003"]

    big = (await client.get("/api/batches", params={"qty_initial_min": "30"}, headers=headers)).json()
    assert [b["number"] for b in big["items"]] == ["П-000004", "П-000003"]
    two = await client.get(
        "/api/batches", params={"status": ["on_check", "accepted"], "currency": "RUB"}, headers=headers
    )
    assert two.json()["total"] == 4

    bad = await client.get("/api/batches", params={"sort": "password"}, headers=headers)
    assert bad.status_code == 422
    assert bad.json()["detail"] == "Нельзя сортировать по полю «password»."


async def test_like_wildcards_in_filter_are_literal(client, session, users, materials, fx):
    await make_receipt(session, users[Role.STOREKEEPER], fx, [("WAX-SOY", "10")])
    headers = await auth_headers(client, "storekeeper")

    response = await client.get("/api/batches", params={"number": "%"}, headers=headers)
    assert response.json()["total"] == 0


@pytest.mark.parametrize("typed", ["П-000001", "p-1", "П-1", " п-000001 "])
async def test_find_batch_by_number_tolerates_typing(session, users, materials, fx, typed):
    await make_receipt(session, users[Role.STOREKEEPER], fx, [("WAX-SOY", "10")])
    found = await batches.find_by_number(session, users[Role.TECHNOLOGIST], typed)
    assert found.number == "П-000001"


async def test_find_missing_batch_says_which(session, users, materials):
    with pytest.raises(NotFoundError, match="Партия П-000077 не найдена"):
        await batches.find_by_number(session, users[Role.MANAGER], "П-77")


async def test_excel_export_respects_filters_and_keeps_numbers(client, session, users, materials, fx):
    keeper = users[Role.STOREKEEPER]
    await make_receipt(session, keeper, fx, [("WAX-SOY", "10.5"), ("FR-LAV", "2")], day=date(2026, 10, 1))
    headers = await auth_headers(client, "technologist")

    response = await client.get("/api/batches/export.xlsx", params={"material": "соев"}, headers=headers)
    assert response.status_code == 200
    rows = list(load_workbook(io.BytesIO(response.content)).active.iter_rows(values_only=True))
    assert len(rows) == 2  # заголовок и одна партия
    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["Код"] == "WAX-SOY"
    assert row["Остаток"] == 10.5 and isinstance(row["Остаток"], float)
    assert row["Дата поступления"].date() == date(2026, 10, 1)


async def test_documents_are_found_by_number_for_links(client, session, users, materials, fx):
    await make_receipt(session, users[Role.STOREKEEPER], fx, [("WAX-SOY", "10")])
    headers = await auth_headers(client, "storekeeper")
    found = await client.get("/api/batches/by-number/п-1", headers=headers)
    assert found.json() == {"id": 1}
    missing = await client.get("/api/batches/by-number/П-000099", headers=headers)
    assert (missing.status_code, missing.json()["detail"]) == (404, "Партия П-000099 не найдена.")
    assert (await client.get("/api/jobs/by-number/З-000099", headers=headers)).status_code == 404
