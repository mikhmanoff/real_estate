# alembic/env.py
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Загружаем .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Warning: python-dotenv not installed, using environment variables only")

# this is the Alembic Config object
config = context.config

# Получаем URL базы данных
# Пробуем разные варианты названия переменной
database_url = (
    os.getenv("DATABASE_URL") or 
    os.getenv("DB_URL") or
    os.getenv("POSTGRES_URL") or
    os.getenv("PG_URL")
)

# Если используешь asyncpg в коде, но alembic требует синхронный драйвер
if database_url:
    # postgresql+asyncpg:// -> postgresql://
    database_url = database_url.replace("+asyncpg", "").replace("+aiopg", "")
    config.set_main_option("sqlalchemy.url", database_url)
    print(f"[alembic] Using database: {database_url.split('@')[-1] if '@' in database_url else 'configured'}")
else:
    print("[alembic] ERROR: No DATABASE_URL found!")
    print("[alembic] Please set DATABASE_URL in .env file:")
    print("[alembic]   DATABASE_URL=postgresql://user:password@localhost:5432/dbname")
    sys.exit(1)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Импортируем модели ПОСЛЕ настройки пути
from database.models import Base

# Метаданные моделей для autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, 
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()