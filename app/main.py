"""API inicial. Nenhum endpoint de tenant é público na FATIA 0."""
from fastapi import FastAPI

app = FastAPI(
    title="Orion Barbearias",
    description="Fundação técnica — FATIA 0",
    version="0.0.1",
)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness sem informação sobre usuários, banco ou segredos."""
    return {"status": "ok"}
