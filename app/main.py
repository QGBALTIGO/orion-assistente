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
from app.qr_api import router as qr_router
from app.panel_auth import router as panel_auth_router
from app.account_auth import router as account_auth_router
from app.groq_api import router as groq_router
from app.groq_audio import router as groq_audio_router
from app.groq_probe import probe_groq_provider
from app.qr_ai import router as qr_ai_router
from app.business_profile_api import router as business_profile_router
from app.commercial_api import router as commercial_router

app = FastAPI(
    title="Orion Barbearias",
    description="Agenda SaaS multiempresa — desenvolvimento controlado",
    version="0.8.0",
)


@app.on_event("startup")
async def startup_provider_probe():
    await probe_groq_provider()


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
app.include_router(qr_router)
app.include_router(panel_auth_router)
app.include_router(account_auth_router)
app.include_router(catalog_router)
app.include_router(groq_router)
app.include_router(groq_audio_router)
app.include_router(qr_ai_router)
app.include_router(business_profile_router)
app.include_router(commercial_router)


@app.get("/brand/{asset}", include_in_schema=False)
async def branding(asset: str) -> FileResponse:
    if asset not in {"orion-brand-dark.svg","orion-brand-light.svg","orion-symbol.svg"}:
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(Path(__file__).parent / "static" / asset,
                        media_type="image/svg+xml",
                        headers={"Cache-Control":"public, max-age=3600",
                                 "X-Content-Type-Options":"nosniff"})


@app.get("/panel", include_in_schema=False)
async def panel() -> FileResponse:
    """Painel HTML; dados protegidos por sessão HttpOnly com CSRF."""
    return FileResponse(
        Path(__file__).parent / "static" / "panel.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )
