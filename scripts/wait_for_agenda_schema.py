"""Read-only worker deployment barrier for the additive agenda history column.

Railway may start the API migration and the worker deployment concurrently.
This process can only inspect PostgreSQL metadata as app_runtime. It never runs
Alembic, reads customer rows, or changes schema, data, roles or permissions.
"""
import asyncio
from dataclasses import dataclass, field
import json
import os
import time

import asyncpg
from sqlalchemy.engine import make_url

WAIT_SECONDS = 90.0
POLL_SECONDS = 1.0
ATTEMPT_SECONDS = 3.0
SAFE_ERRORS = frozenset({
    "invalid_runtime_configuration", "runtime_role_required", "runtime_authentication_failed",
    "runtime_catalog_unavailable", "agenda_schema_timeout", "agenda_schema_check_failed",
})

SCHEMA_QUERY = """
    SELECT current_user AS runtime_role, EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
        JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
        WHERE n.nspname='public' AND c.relname='appointment_history' AND c.relkind='r'
          AND a.attname='details' AND a.attnum>0 AND NOT a.attisdropped AND a.attnotnull
          AND tn.nspname='pg_catalog' AND t.typname='jsonb'
    ) AS ready
"""


class Stop(Exception):
    """Only finite codes from SAFE_ERRORS are emitted by the command."""


@dataclass(frozen=True)
class Settings:
    dsn: str = field(repr=False)

    @classmethod
    def from_environment(cls):
        try:
            raw = os.getenv("APP_DATABASE_URL", "")
            if not raw or raw != raw.strip() or len(raw) > 8192:
                raise ValueError()
            if raw.startswith("postgres://"):
                raw = "postgresql://" + raw[len("postgres://"):]
            url = make_url(raw)
            if url.drivername not in {"postgresql", "postgresql+asyncpg"}:
                raise ValueError()
            if not url.host or not url.database or any(char.isspace() or char in "/,\\\x00" for char in url.host):
                raise ValueError()
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError()
            if url.username != "app_runtime":
                raise Stop("runtime_role_required")
            # Preserve optional TLS settings; reject identity/startup overrides
            # and caller-controlled timeouts. The worker's API URL is the only
            # configuration input, never the migrator or owner URL.
            query = dict(url.query)
            if not set(query) <= {"ssl", "sslmode", "sslrootcert", "sslcert", "sslkey", "sslpassword"}:
                raise ValueError()
            if any(not isinstance(value, str) for value in query.values()):
                raise ValueError()
            if "ssl" in query:
                if "sslmode" in query:
                    raise ValueError()
                query["sslmode"] = query.pop("ssl")
            if query.get("sslmode", "prefer") not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
                raise ValueError()
            return cls(url.set(drivername="postgresql", query=query).render_as_string(hide_password=False))
        except Stop:
            raise
        except Exception:
            raise Stop("invalid_runtime_configuration") from None


async def probe_schema(settings, timeout):
    connection = None
    try:
        milliseconds = str(max(1, int(timeout * 1000)))
        connection = await asyncpg.connect(
            settings.dsn, timeout=timeout, command_timeout=timeout,
            server_settings={"default_transaction_read_only": "on", "search_path": "pg_catalog",
                             "statement_timeout": milliseconds, "lock_timeout": milliseconds,
                             "application_name": "orion-agenda-schema-wait"},
        )
        row = await connection.fetchrow(SCHEMA_QUERY)
        if row["runtime_role"] != "app_runtime":
            raise Stop("runtime_role_required")
        return bool(row["ready"])
    finally:
        if connection is not None:
            # No transaction/data writes exist to flush. Termination is immediate
            # and remains bounded even after a network timeout or cancellation.
            connection.terminate()


async def wait_for_schema(settings, *, clock=None, sleep=None, probe=None):
    clock, sleep, probe = clock or time.monotonic, sleep or asyncio.sleep, probe or probe_schema
    deadline = clock() + WAIT_SECONDS
    while (remaining := deadline - clock()) > 0:
        budget = min(ATTEMPT_SECONDS, remaining)
        try:
            # One budget covers both connecting and reading the catalog, rather
            # than allowing each operation its own three-second extension.
            async with asyncio.timeout(budget):
                if await probe(settings, budget):
                    return {"ok": True, "status": "agenda_schema_ready"}
        except (asyncpg.InvalidAuthorizationSpecificationError, asyncpg.InvalidPasswordError,
                asyncpg.InvalidCatalogNameError):
            raise Stop("runtime_authentication_failed") from None
        except asyncpg.InsufficientPrivilegeError:
            raise Stop("runtime_catalog_unavailable") from None
        except (OSError, TimeoutError, asyncpg.PostgresError):
            pass  # A database restarting during deployment may become ready.
        if (remaining := deadline - clock()) > 0:
            await sleep(min(POLL_SECONDS, remaining))
    raise Stop("agenda_schema_timeout")


def main():
    try:
        result = asyncio.run(wait_for_schema(Settings.from_environment()))
    except Exception as error:
        code = str(error) if isinstance(error, Stop) and str(error) in SAFE_ERRORS else "agenda_schema_check_failed"
        print(json.dumps({"ok": False, "error": code}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
