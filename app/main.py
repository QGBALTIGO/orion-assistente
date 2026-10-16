"""API interna de operação da FATIA 1 (somente pessoal autorizado)."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.catalog_api import router as catalog_router

app = FastAPI(
    title="Orion Barbearias",
    description="Catálogo interno — FATIA 1",
    version="0.1.0",
)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness sem informação sobre usuários, banco ou segredos."""
    return {"status": "ok"}


app.include_router(catalog_router)


@app.get("/panel", include_in_schema=False)
async def panel() -> FileResponse:
    """Somente HTML estático. Dados são acessados via Bearer token."""
    return FileResponse(Path(__file__).parent / "static" / "panel.html")
