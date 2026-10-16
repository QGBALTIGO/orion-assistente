"""Bootstrap controlado de PostgreSQL Railway para ambiente de homologação.

Executar somente no serviço isolado 'orion-bootstrap' com DATABASE_URL admin.
Nunca importar no servidor API, nem passar admin credentials para API/worker.

Idempotente quanto a roles e migrations (Alembic controla revisões).
"""
import os
from urllib.parse import quote

import psycopg
from psycopg import sql
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


ROLES = ("app_runtime", "app_migrator", "app_support")
ROLE_ENV = {
    "app_runtime": "APP_RUNTIME_PASSWORD",
    "app_migrator": "APP_MIGRATOR_PASSWORD",
    "app_support": "APP_SUPPORT_PASSWORD",
}


def required(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"Configuração ausente: {name}")
    return value


def create_roles() -> str:
    admin_url = required("DATABASE_URL")
    if not admin_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL precisa ser PostgreSQL")
    passwords = {role: required(ROLE_ENV[role]) for role in ROLES}
    if len(set(passwords.values())) != len(ROLES):
        raise RuntimeError("Senhas dos três papéis precisam ser distintas")
    if any(len(pw) < 32 for pw in passwords.values()):
        raise RuntimeError("Senhas dos papéis precisam de 32 caracteres ou mais")

    with psycopg.connect(admin_url, autocommit=True, connect_timeout=15) as conn:
        dbname = conn.execute("SELECT current_database()").fetchone()[0]
        for role in ROLES:
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
            ).fetchone()
            if exists:
                conn.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}").format(
                                sql.Identifier(role), sql.Literal(passwords[role])
                            )
                )
            else:
                conn.execute(
                    sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}").format(
                                sql.Identifier(role), sql.Literal(passwords[role])
                            )
                )
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO app_runtime, app_migrator, app_support").format(
                sql.Identifier(dbname)
            )
        )
        conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO app_migrator").format(
                sql.Identifier(dbname)
            )
        )
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(
            "GRANT USAGE ON SCHEMA public TO app_runtime, app_migrator, app_support"
        )
        conn.execute("GRANT CREATE ON SCHEMA public TO app_migrator")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE app_migrator IN SCHEMA public "
            "REVOKE ALL ON TABLES FROM PUBLIC"
        )
    print("Papéis runtime, migrator e support provisionados; atributos RLS seguros.")
    return dbname


def make_connection_url(role: str, password: str) -> str:
    admin_url = make_url(required("DATABASE_URL").replace("postgres://", "postgresql://", 1))
    return str(admin_url.set(
        drivername="postgresql",
        username=role,
        password=password,
    ).render_as_string(hide_password=False))


def migrate() -> None:
    migrator_url = make_connection_url("app_migrator", required("APP_MIGRATOR_PASSWORD"))
    os.environ["MIGRATION_DATABASE_URL"] = migrator_url
    # Config inclui projeto e migrations na imagem; não registrar senhas.
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    print("Alembic upgrade head executado no PostgreSQL Railway.")


def verify() -> None:
    runtime_url = make_connection_url("app_runtime", required("APP_RUNTIME_PASSWORD"))
    with psycopg.connect(runtime_url, autocommit=True, connect_timeout=15) as conn:
        role = conn.execute("SELECT current_user").fetchone()[0]
        if role != "app_runtime":
            raise RuntimeError("Papel incorreto na conexão runtime")
        count = conn.execute("SELECT count(*) FROM tenants").fetchone()[0]
        if count != 0:
            raise RuntimeError("RLS falhou: runtime sem tenant acessou tenants")
    with psycopg.connect(
        make_connection_url("app_support", required("APP_SUPPORT_PASSWORD")),
        autocommit=True,
        connect_timeout=15,
    ) as conn:
        if conn.execute("SELECT current_user").fetchone()[0] != "app_support":
            raise RuntimeError("Papel incorreto na conexão support")
        conn.execute("SELECT count(*) FROM tenants")
    print("RLS fail-closed e credenciais de suporte verificados.")


if __name__ == "__main__":
    create_roles()
    migrate()
    verify()
    print("Bootstrap de homologação completo.")
