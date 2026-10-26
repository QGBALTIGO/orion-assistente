"""API Orion: oito fatias técnicas, integrações externas opt-in."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from app.catalog_api import router as catalog_router, list_services
from app.booking_api import router as bookings_router
from app.whatsapp_api import router as whatsapp_router
from app.integrations_api import router as integrations_router
from app.ops_api import router as ops_router
from app.landing_api import router as landing_router
from app.sales_requests_api import router as sales_requests_router
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
from app.business_profile_api import router as business_profile_router, account_router as account_business_router
from app.commercial_api import router as commercial_router
from app.management_api import router as management_router
from app.management_blocks import router as management_blocks_router
from app.management_ai import router as management_ai_router
from app.management_team import router as management_team_router, invitation_router
from app.management_preferences import router as management_preferences_router
from app.public_site_api import router as public_site_router
from app.service_packages_api import router as service_packages_router
from app.onboarding_api import router as onboarding_router
from app.qr_channels import router as qr_channels_router
from app.automation_api import router as automation_router
from app.qr_channel_audio import router as qr_channel_audio_router
from app.media_api import router as media_router
from app.inbox_api import router as inbox_router
from app.inbox_control import router as inbox_control_router
from app.inbox_conversations import router as inbox_conversations_router
from app.communication_api import router as communication_router
from app.workflow_drafts import router as workflow_drafts_router

app = FastAPI(
    title="Orion Barbearias",
    description="Agenda SaaS multiempresa — desenvolvimento controlado",
    version="0.8.0",
)


@app.on_event("startup")
async def startup_provider_probe():
    await probe_groq_provider()


@app.on_event("startup")
async def startup_panel_session_store():
    from app.panel_auth import start_session_store
    await start_session_store(app)


@app.on_event("shutdown")
async def shutdown_panel_session_store():
    from app.panel_auth import close_session_store
    await close_session_store(app)


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    """Liveness sem informação sobre usuários, banco ou segredos."""
    return {"status": "ok"}


app.add_api_route("/api/v1/tenants/{tenant_id}/services", list_services,
                  methods=["GET"], tags=["Operação interna"])
app.include_router(bookings_router)
app.include_router(onboarding_router)
app.include_router(commercial_router)
app.include_router(whatsapp_router)
app.include_router(integrations_router)
app.include_router(ops_router)
app.include_router(landing_router)
app.include_router(sales_requests_router)
app.include_router(billing_router)
app.include_router(metrics_router)
app.include_router(advanced_router)
app.include_router(qr_router)
app.include_router(panel_auth_router)
app.include_router(account_auth_router)
app.include_router(groq_router)
app.include_router(groq_audio_router)
app.include_router(qr_ai_router)
app.include_router(business_profile_router)
app.include_router(account_business_router)
app.include_router(management_router)
app.include_router(management_blocks_router)
app.include_router(management_ai_router)
app.include_router(management_team_router)
app.include_router(invitation_router)
app.include_router(management_preferences_router)
app.include_router(public_site_router)
app.include_router(service_packages_router)
app.include_router(qr_channels_router)
app.include_router(automation_router)
app.include_router(qr_channel_audio_router)
app.include_router(media_router)
app.include_router(inbox_router)
app.include_router(inbox_control_router)
app.include_router(inbox_conversations_router)
app.include_router(communication_router)
app.include_router(workflow_drafts_router)
# Specific management paths must precede the legacy two-segment catalog path.
app.include_router(catalog_router)


@app.get("/brand/{asset}", include_in_schema=False)
async def branding(asset: str) -> FileResponse:
    allowed = {
        "orion-brand-dark.svg": "image/svg+xml",
        "orion-brand-light.svg": "image/svg+xml",
        "orion-symbol.svg": "image/svg+xml",
        "figtree-latin.woff2": "font/woff2",
        "outfit-latin.woff2": "font/woff2",
    }
    if asset not in allowed:
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(Path(__file__).parent / "static" / asset,
                        media_type=allowed[asset],
                        headers={"Cache-Control":"public, max-age=3600",
                                 "X-Content-Type-Options":"nosniff"})


@app.get("/panel-assets/{asset}", include_in_schema=False)
async def panel_asset(asset: str) -> FileResponse:
    """Only the public suite assets; never expose arbitrary application files."""
    allowed = {"orion-suite.css": "text/css", "orion-suite.js": "text/javascript",
               "orion-suite-live.js": "text/javascript",
               "orion-communication.js": "text/javascript",
               "orion-auth.css": "text/css",
               "orion-invite.css": "text/css", "orion-invite.js": "text/javascript",
               "orion-onboarding.css": "text/css", "orion-onboarding.js": "text/javascript",
               "orion-channels.css": "text/css", "orion-channels.js": "text/javascript",
               "orion-automations.js": "text/javascript",
               "orion-inbox.js": "text/javascript", "orion-conversations.js": "text/javascript", "orion-flows.js": "text/javascript", "orion-inbox.css": "text/css"}
    if asset not in allowed:
        from fastapi import HTTPException
        raise HTTPException(404)
    return FileResponse(
        Path(__file__).parent / "static" / ("orion-suite.js" if asset == "orion-suite-live.js" else asset),
        media_type=allowed[asset],
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/panel", include_in_schema=False)
async def panel() -> FileResponse:
    """Painel HTML; dados protegidos por sessão HttpOnly com CSRF."""
    return FileResponse(
        Path(__file__).parent / "static" / "panel.html",
        headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
    )


@app.get("/convite", include_in_schema=False)
async def invitation_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "orion-invite.html",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
