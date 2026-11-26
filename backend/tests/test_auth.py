from decimal import Decimal

from app.formatting import fmt_number, fmt_qty
from tests.conftest import PASSWORD


async def test_login_sets_httponly_cookie_and_returns_permissions(client, users):
    response = await client.post("/api/auth/login", json={"login": "Manager", "password": PASSWORD})

    assert response.status_code == 200
    assert "httponly" in response.headers["set-cookie"].lower()
    body = response.json()
    assert body["role_label"] == "Руководитель"
    assert "audit_view" in body["permissions"]

    me = await client.get("/api/auth/me")
    assert me.json()["full_name"] == "Кузнецов Олег Петрович"


async def test_wrong_password_and_unknown_login_look_the_same(client, users):
    wrong = await client.post("/api/auth/login", json={"login": "manager", "password": "nope"})
    unknown = await client.post("/api/auth/login", json={"login": "ghost", "password": "nope"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Неверный логин или пароль."


async def test_requests_without_session_are_rejected_politely(client, users):
    response = await client.get("/api/batches")
    assert response.status_code == 401
    assert response.json()["detail"] == "Войдите в систему."


async def test_storekeeper_cannot_read_audit_log(client, users):
    await client.post("/api/auth/login", json={"login": "storekeeper", "password": PASSWORD})
    response = await client.get("/api/audit")
    assert response.status_code == 403
    assert response.json()["detail"] == "Недостаточно прав: смотреть журнал изменений может Руководитель."


def test_numbers_are_formatted_for_people():
    assert fmt_qty(Decimal("2.500"), "кг") == "2,5 кг"
    assert fmt_number(Decimal("3.000")) == "3"
    assert fmt_number(Decimal("100")) == "100"
    assert fmt_number(Decimal("1234567.5")) == "1234567,5"
    assert fmt_number(Decimal("-0.125")) == "-0,125"
