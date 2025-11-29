# database/connection.py
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.utils import env


def get_database_url() -> str:
    """Формирует URL подключения к PostgreSQL из переменных окружения."""
    return (
        f"postgresql+asyncpg://"
        f"{env('DB_USER', 'postgres')}:{env('DB_PASSWORD', 'postgres')}"
        f"@{env('DB_HOST', 'localhost')}:{env('DB_PORT', '5432')}"
        f"/{env('DB_NAME', 'tg_realty')}"
    )


# Async engine
engine = create_async_engine(
    get_database_url(),
    echo=env("DB_ECHO", "false").lower() == "true",
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Контекстный менеджер для работы с сессией."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    """Создаёт таблицы (для разработки). В проде лучше использовать alembic."""
    from database.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Закрывает пул соединений."""
    await engine.dispose()