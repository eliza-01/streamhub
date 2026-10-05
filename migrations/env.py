from __future__ import annotations

from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, inspect, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL", "").strip()
if not database_url:
    raise RuntimeError("DATABASE_URL is required for migrations")
sync_database_url = database_url.replace("mysql+asyncmy://", "mysql+pymysql://", 1)
config.set_main_option("sqlalchemy.url", sync_database_url.replace("%", "%%"))
target_metadata = None


def _ensure_alembic_version_capacity(connection) -> None:
    if connection.dialect.name != "mysql":
        return
    inspector = inspect(connection)
    if not inspector.has_table("alembic_version"):
        return
    version_column = next(
        (column for column in inspector.get_columns("alembic_version") if column["name"] == "version_num"),
        None,
    )
    if version_column is None:
        return
    length = getattr(version_column["type"], "length", None)
    if length is not None and length >= 128:
        return
    connection.exec_driver_sql(
        "ALTER TABLE alembic_version MODIFY COLUMN version_num VARCHAR(128) NOT NULL"
    )
    connection.commit()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _ensure_alembic_version_capacity(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
