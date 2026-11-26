from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

# Явные имена ограничений: Alembic генерирует стабильные миграции, а ошибки БД читаемы.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# hide_parameters: в текст ошибок (а значит, и в логи) не попадают значения из запросов — там бывают ФИО.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True, hide_parameters=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Сессия на запрос. Фиксирует транзакцию вызывающий код (роутер), а не сервис:
    так несколько сервисных вызовов можно объединить в одну транзакцию."""
    async with SessionFactory() as session:
        yield session
