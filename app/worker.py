"""Worker Arq mínimo. Jobs de negócio só serão adicionados em fatias futuras."""
import os

from arq.connections import RedisSettings


async def infrastructure_ping(ctx: dict) -> str:
    """Job inofensivo para validar a fila sem dados pessoais."""
    return "ok"


class WorkerSettings:
    functions = [infrastructure_ping]
    redis_settings = RedisSettings.from_dsn(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
