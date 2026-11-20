"""Read-only, platform-owner-only agenda layout experiment.

No business mutation endpoints are exposed here. The active panel and its
assets remain unchanged for ordinary accounts. Controlled by a kill switch.
"""
import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.admin_accounts import require_platform_owner

router = APIRouter(include_in_schema=False)
_ROOT = Path(__file__).resolve().parent / "static"
_ASSETS = {
    "agenda-preview.css": ("orion-agenda-preview.css", "text/css"),
    "agenda-preview.js": ("orion-agenda-preview.js", "text/javascript"),
}
_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def _require_preview_enabled() -> None:
    if os.getenv("ORION_AGENDA_ADMIN_PREVIEW_ENABLED", "0") != "1":
        raise HTTPException(404, "Página não encontrada")


@router.get("/painel/agenda-teste")
async def agenda_preview(_admin: UUID = Depends(require_platform_owner)) -> FileResponse:
    _require_preview_enabled()
    return FileResponse(
        _ROOT / "orion-agenda-preview.html",
        media_type="text/html",
        headers=_HEADERS,
    )


@router.get("/painel/agenda-teste/{asset}")
async def agenda_preview_asset(
    asset: str, _admin: UUID = Depends(require_platform_owner)
) -> FileResponse:
    _require_preview_enabled()
    item = _ASSETS.get(asset)
    if item is None:
        raise HTTPException(404, "Arquivo não encontrado")
    name, media_type = item
    return FileResponse(_ROOT / name, media_type=media_type, headers=_HEADERS)
