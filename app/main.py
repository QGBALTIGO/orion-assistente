"""API Orion: oito fatias técnicas, integrações externas opt-in."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.catalog_api import router as catalog_router
from app.booking_api import router as bookings_router
from app.whatsapp_api import router as whatsapp_router
from app.integrations_api import router as integrations_router
from app.ops_api import router as ops_router
from app.landing_api import router as landing_router
from app.billing_api import router as billing_router
from app.metrics_api import router as metrics_router
from app.advanced_api import router as advanced_router

app = FastAPI(
    title="Orion Barbearias",
    description="Agenda SaaS multiempresa — desenvolvimento controlado",
    version="0.8.0",
)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness sem informação sobre usuários, banco ou segredos."""
    return {"status": "ok"}


app.include_router(bookings_router)
app.include_router(whatsapp_router)
app.include_router(integrations_router)
app.include_router(ops_router)
app.include_router(landing_router)
app.include_router(billing_router)
app.include_router(metrics_router)
app.include_router(advanced_router)
app.include_router(catalog_router)


@app.get("/panel", include_in_schema=False)
async def panel() -> FileResponse:
    """Somente HTML estático. Dados são acessados via Bearer token."""
    return FileResponse(Path(__file__).parent / "static" / "panel.html")
