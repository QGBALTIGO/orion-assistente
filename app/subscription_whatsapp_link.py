"""No-send WhatsApp click-to-chat links and local PNG QR generation.

The caller is the active subscription-business owner. No WhatsApp session,
message, contact store, third-party QR service or click tracking is involved.
"""
import base64
from io import BytesIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.encoders import jsonable_encoder
import qrcode
from qrcode.constants import ERROR_CORRECT_M

from app.catalog_api import Actor, authenticate
from app import subscription_chatbot_store as shared
from app.subscription_whatsapp_link_schemas import LinkRequest

router = APIRouter(tags=["Link e QR WhatsApp de assinaturas"])
BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/whatsapp-link"
FEATURE = "whatsapp_link.manage"


def generate(payload: LinkRequest):
    url = "https://wa.me/" + payload.phone[1:]
    if payload.message:
        url += "?text=" + quote(payload.message, safe="")
    # High density QR content is deliberately rejected rather than silently
    # truncating the greeting or relying on an external shortening service.
    if len(url.encode("utf-8")) > 1500:
        raise HTTPException(422, {"code": "WHATSAPP_LINK_TOO_LONG",
            "message": "Reduza a mensagem para gerar um QR legível."})
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                       box_size=5, border=4)
    qr.add_data(url, optimize=0)
    qr.make(fit=True)
    png = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(png, format="PNG")
    data = png.getvalue()
    if len(data) > 150_000:
        raise HTTPException(422, {"code": "WHATSAPP_LINK_TOO_LONG",
            "message": "A mensagem não cabe em um QR seguro."})
    return {"url": url, "image_base64": base64.b64encode(data).decode("ascii"),
            "format": "png", "mime_type": "image/png"}


@router.post(BASE + "/generate")
async def generate_link(tenant_id: UUID, payload: LinkRequest,
                        response: Response, actor: Actor = Depends(authenticate)):
    async with shared.owner_transaction(tenant_id, actor, feature=FEATURE):
        result = generate(payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return jsonable_encoder(result)
