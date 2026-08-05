"""
Alembic environment — async variant (the app's engine is asyncpg).

Invoked two ways:
  - programmatically at startup: db.init_db() -> command.upgrade(...), with
    sqlalchemy.url set in code (see db._run_migrations);
  - via the CLI from the repo root (alembic.ini points here), where the
    URL falls back to the DATABASE_URL env var.
"""

import asyncio
import sys
from os import getenv
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make the package importable when run via the CLI from the repo root
# (src layout — same reason pyproject sets mypy_path/src).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nba_projection_bot.db import Base  # noqa: E402

load_dotenv()

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", getenv("DATABASE_URL", ""))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing (alembic upgrade --sql)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    # asyncio.run needs a thread without a running loop — db.init_db
    # guarantees that by invoking alembic inside asyncio.to_thread.
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
