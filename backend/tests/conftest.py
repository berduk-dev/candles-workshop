"""Тесты идут на настоящем PostgreSQL (сервис db-test из docker compose, порт 5433).

Схема создаётся теми же миграциями Alembic, что и в проде. Перед каждым тестом таблицы
очищаются через TRUNCATE (а не откат транзакции): тестам конкурентности нужны настоящие коммиты.
Сеть запрещена pytest-socket'ом — разрешён только localhost для базы.
"""

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://candles:candles@localhost:5433/candles_test"
)
# До импорта приложения: движок БД создаётся при импорте app.db.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["STORAGE_DIR"] = tempfile.mkdtemp()
os.environ["OPENAI_API_KEY"] = ""
os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long!!"

import bcrypt  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from alembic import command  # noqa: E402
from app.api.chat import get_assistant  # noqa: E402
from app.assistant.agent import AssistantDeps  # noqa: E402
from app.assistant.llm.router import ModelRouter  # noqa: E402
from app.assistant.llm.stub import StubContainers, StubProvider  # noqa: E402
from app.db import Base, SessionFactory, engine  # noqa: E402
from app.deps import get_fx  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Material, Role, User  # noqa: E402
from app.services.fx import FxClient  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "secret"
# Низкая стоимость bcrypt — только чтобы тесты не тратили секунды на хэширование.
PASSWORD_HASH = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture(scope="session", autouse=True)
async def migrated_database() -> AsyncIterator[None]:
    admin = create_async_engine(TEST_DATABASE_URL)
    async with admin.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await admin.dispose()

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    config.attributes["configure_logging"] = False
    # env.py запускает свой event loop, поэтому миграции — в отдельном потоке.
    await asyncio.to_thread(command.upgrade, config, "head")
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(migrated_database) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    sequences = [s.name for s in Base.metadata._sequences.values()]
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        for name in sequences:
            await conn.execute(text(f"ALTER SEQUENCE {name} RESTART"))


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as s:
        yield s


@pytest.fixture
async def users(session: AsyncSession) -> dict[Role, User]:
    created = {
        Role.STOREKEEPER: User(login="storekeeper", full_name="Петров Иван Сергеевич", role=Role.STOREKEEPER),
        Role.TECHNOLOGIST: User(
            login="technologist", full_name="Смирнова Анна Викторовна", role=Role.TECHNOLOGIST
        ),
        Role.MANAGER: User(login="manager", full_name="Кузнецов Олег Петрович", role=Role.MANAGER),
    }
    for user in created.values():
        user.password_hash = PASSWORD_HASH
        session.add(user)
    await session.commit()
    return created


@pytest.fixture
async def materials(session: AsyncSession) -> dict[str, Material]:
    created = {
        "WAX-SOY": Material(code="WAX-SOY", name="Воск соевый", unit="кг"),
        "FR-LAV": Material(code="FR-LAV", name="Отдушка «Лаванда»", unit="кг"),
        "JAR-200": Material(code="JAR-200", name="Стакан стеклянный 200 мл", unit="шт"),
    }
    session.add_all(created.values())
    await session.commit()
    return created


class FakeFx:
    """Подмена мок-сервиса курсов: курс зависит от даты, в выходные курса нет. Считает запросы."""

    def __init__(self) -> None:
        self.calls: list[date] = []
        self.down = False

    @staticmethod
    def rate(currency: str, day: date) -> Decimal:
        base = {"USD": Decimal("90"), "EUR": Decimal("100")}[currency]
        return base + Decimal(day.day) / 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("fx is down", request=request)
        day = date.fromisoformat(request.url.path.rsplit("/", 1)[1])
        self.calls.append(day)
        if day.weekday() >= 5:
            return httpx.Response(404, json={"detail": "нет курса"})
        rates = {c: str(self.rate(c, day)) for c in ("USD", "EUR")}
        return httpx.Response(200, content=json.dumps({"date": day.isoformat(), "rates": rates}))


@pytest.fixture
def fake_fx() -> FakeFx:
    return FakeFx()


@pytest.fixture
def fx(fake_fx: FakeFx) -> FxClient:
    return FxClient("http://fx.test", transport=httpx.MockTransport(fake_fx.handler))


@pytest.fixture
async def client(fx: FxClient) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[get_fx] = lambda: fx
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def containers() -> StubContainers:
    return StubContainers()


@pytest.fixture
def stub_llm(fx: FxClient, containers: StubContainers):
    """stub_llm([шаг1, шаг2]) — подменяет модель сценарием и возвращает заглушку с записанными запросами."""

    def install(steps, fallback_steps=None) -> StubProvider:
        providers = [StubProvider(steps, "stub-primary")]
        if fallback_steps is not None:
            providers.append(StubProvider(fallback_steps, "stub-fallback"))
        deps = AssistantDeps(ModelRouter(providers, call_timeout=5), containers, SessionFactory, fx)
        app.dependency_overrides[get_assistant] = lambda: deps
        return providers[0]

    return install


async def auth_headers(client: httpx.AsyncClient, login: str) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"login": login, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.cookies.clear()  # каждый запрос в тестах явно говорит, от чьего имени он идёт
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
