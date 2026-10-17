"""Migrations exclusivamente pelo usuário app_migrator, nunca pela API."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)


def migration_url() -> str:
    url = os.environ.get("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL é obrigatória para migrations")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("MIGRATION_DATABASE_URL deve usar psycopg/PostgreSQL")
    return url


config.set_main_option("sqlalchemy.url", migration_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # ONE-TIME Railway migration. A database owner can assume the restricted
        # migrator role for Alembic; the DDL still executes as app_migrator.
        # Never relax the checked current_user and never enable for regular API.
        if os.getenv("ORION_MIGRATION_SWITCH_ROLE") == "1":
            if connection.exec_driver_sql("SELECT current_user").scalar_one() != "app_migrator":
                connection.exec_driver_sql("SET ROLE app_migrator")
                connection.commit()
        if connection.exec_driver_sql("SELECT current_user").scalar_one() != "app_migrator":
            raise RuntimeError("Migrations exigem exclusivamente o usuário app_migrator")
        # SQLAlchemy 2.x: até SELECT inicia transação implícita.
        # Sem COMMIT aqui, o context.begin_transaction() do Alembic detecta
        # uma transação externa e o fechamento da conexão desfaz todo o DDL.
        connection.commit()
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
