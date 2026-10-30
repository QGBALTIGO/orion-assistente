"""API Orion: oito fatias técnicas, integrações externas opt-in."""
from pathlib import Path
from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from app.catalog_api import router as catalog_router, list_services
from app.booking_api import router as bookings_router
from app.whatsapp_api import router as whatsapp_router
from app.integrations_api import router as integrations_router
from app.ops_api import router as ops_router
from app.landing_api import router as landing_router
from app.sales_requests_api import router as sales_requests_router
from app.billing_api import router as billing_router
from app.cakto_api import router as cakto_router
from app.plan_management import router as plan_management_router
from app.orion_direct_checkout import router as direct_checkout_router
from app.metrics_api import router as metrics_router
from app.advanced_api import router as advanced_router
from app.qr_api import router as qr_router
from app.panel_auth import router as panel_auth_router
from app.account_auth import router as account_auth_router
from app.account_settings import router as account_settings_router
from app.admin_accounts import router as admin_accounts_router
from app.maintenance_access import router as maintenance_router, admin_router as maintenance_admin_router
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
from app.orion_pwa_api import router as orion_pwa_router
from app.plan_routing import include_plan_router, enforce_endpoint_plan

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
                  methods=["GET"], tags=["Operação interna"], dependencies=[Depends(enforce_endpoint_plan)])
include_plan_router(app, bookings_router)
include_plan_router(app, onboarding_router)
include_plan_router(app, commercial_router)
include_plan_router(app, whatsapp_router)
include_plan_router(app, integrations_router)
include_plan_router(app, ops_router)
include_plan_router(app, landing_router)
include_plan_router(app, sales_requests_router)
include_plan_router(app, billing_router)
include_plan_router(app, cakto_router)
include_plan_router(app, plan_management_router)
include_plan_router(app, direct_checkout_router)
include_plan_router(app, metrics_router)
include_plan_router(app, advanced_router)
include_plan_router(app, qr_router)
include_plan_router(app, panel_auth_router)
include_plan_router(app, account_auth_router)
include_plan_router(app, account_settings_router)
# Register the specific maintenance start route before the generic account-action route.
include_plan_router(app, maintenance_admin_router)
include_plan_router(app, maintenance_router)
include_plan_router(app, admin_accounts_router)
include_plan_router(app, groq_router)
include_plan_router(app, groq_audio_router)
include_plan_router(app, qr_ai_router)
include_plan_router(app, business_profile_router)
include_plan_router(app, account_business_router)
include_plan_router(app, management_router)
include_plan_router(app, management_blocks_router)
include_plan_router(app, management_ai_router)
include_plan_router(app, management_team_router)
include_plan_router(app, invitation_router)
include_plan_router(app, management_preferences_router)
from app.appointment_access_api import router as appointment_access_router
include_plan_router(app, appointment_access_router)
from app.appointment_communications_api import router as appointment_communications_router
include_plan_router(app, appointment_communications_router)
include_plan_router(app, public_site_router)
include_plan_router(app, service_packages_router)
include_plan_router(app, qr_channels_router)
include_plan_router(app, automation_router)
include_plan_router(app, qr_channel_audio_router)
include_plan_router(app, media_router)
include_plan_router(app, inbox_router)
include_plan_router(app, inbox_control_router)
include_plan_router(app, inbox_conversations_router)
include_plan_router(app, communication_router)
include_plan_router(app, workflow_drafts_router)
include_plan_router(app, orion_pwa_router)
# Specific management paths must precede the legacy two-segment catalog path.
include_plan_router(app, catalog_router)


@app.get("/brand/{asset}", include_in_schema=False)
async def branding(asset: str) -> FileResponse:
    allowed = {
        "orion-brand-dark.svg": "image/svg+xml",
        "orion-brand-light.svg": "image/svg+xml",
        "orion-symbol.svg": "image/svg+xml",
        "orion-email-logo.png": "image/png",
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
               "orion-plans.css": "text/css", "orion-plans.js": "text/javascript",
               "orion-fields.css": "text/css", "orion-fields.js": "text/javascript",
               "orion-suite-live.js": "text/javascript",
               "orion-admin-center.js": "text/javascript",
               "orion-communication.js": "text/javascript",
               "orion-appointment-communications.js": "text/javascript",
               "orion-pwa.js": "text/javascript",
               "orion-auth.css": "text/css",
               "orion-invite.css": "text/css", "orion-invite.js": "text/javascript",
               "orion-onboarding.css": "text/css", "orion-onboarding.js": "text/javascript",
               "orion-channels.css": "text/css", "orion-channels.js": "text/javascript",
               "orion-connect.js": "text/javascript",
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
