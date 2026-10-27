"""Public landing and an exact allowlist of its reviewed local assets."""
from html import escape
import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from app.public_origins import canonical_public_origin

router=APIRouter()
# Fixed facade host for metadata only; no user-supplied arbitrary origin is accepted.
RAILWAY_ORIGIN = "https://orion-assistente-production.up.railway.app"
VERCEL_ORIGIN = "https://orion-painel-iota.vercel.app"
LANDING_DIR = Path(__file__).resolve().parents[1] / "landing"
ASSETS = {
    "orion-landing.css": ("assets/orion-landing.css", "text/css"),
    "orion-landing.js": ("assets/orion-landing.js", "text/javascript"),
    "negocio-barbearia.webp": ("assets/negocio-barbearia.webp", "image/webp"),
    "negocio-estetica.webp": ("assets/negocio-estetica.webp", "image/webp"),
    "negocio-odontologia.webp": ("assets/negocio-odontologia.webp", "image/webp"),
    "negocio-restaurante.webp": ("assets/negocio-restaurante.webp", "image/webp"),
    "negocio-fitness.webp": ("assets/negocio-fitness.webp", "image/webp"),
    "rotina-negocio.mp4": ("assets/rotina-negocio.mp4", "video/mp4"),
    "rotina-poster.webp": ("assets/rotina-poster.webp", "image/webp"),
    "orion-social.png": ("assets/orion-social.png", "image/png"),
}


def sales_contact_link():
    phone = os.getenv("SALES_WHATSAPP_E164", "").strip()
    if not re.fullmatch(r"\+55[1-9][0-9]{9,10}", phone):
        return ""
    url = "https://wa.me/" + phone[1:] + "?text=" + quote("Olá! Quero conhecer o Orion para o meu negócio.", safe="")
    return ('<a class="sales-contact-link" href="' + escape(url, quote=True) + '" '
            'rel="noopener noreferrer" target="_blank">Falar pelo WhatsApp</a>')


@router.get("/",response_class=HTMLResponse,include_in_schema=False)
async def landing(request: Request):
    content = (LANDING_DIR / "index.html").read_text(encoding="utf-8")
    if os.getenv("PANEL_PUBLIC_URL", "").strip():
        try:
            origin = canonical_public_origin()
        except ValueError:
            raise HTTPException(503, "Página temporariamente indisponível") from None
    else:
        # Vercel sets this fixed marker before proxying the homepage.
        origin = VERCEL_ORIGIN if request.headers.get("x-orion-facade") == "vercel-iota" else RAILWAY_ORIGIN
    for part in ("/landing-assets/orion-social.png", "/"):
        content = content.replace(f'content="{RAILWAY_ORIGIN}{part}"', f'content="{escape(origin, quote=True)}{part}"')
    content = content.replace(f'rel="canonical" href="{RAILWAY_ORIGIN}/"',
                              f'rel="canonical" href="{escape(origin, quote=True)}/"')
    return HTMLResponse(content.replace("__SALES_CONTACT_LINK__", sales_contact_link()),
        headers={"Cache-Control":"public, max-age=60","X-Content-Type-Options":"nosniff"})


@router.get("/landing-assets/{asset}", include_in_schema=False)
async def landing_asset(asset: str):
    approved = ASSETS.get(asset)
    if approved is None:
        raise HTTPException(404)
    relative, media_type = approved
    path = LANDING_DIR / relative
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type=media_type,
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
