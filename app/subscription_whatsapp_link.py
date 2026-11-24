"""Generate links and QR images inside Orion, without a third-party QR service."""
import asyncio
import base64
from io import BytesIO
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Response
import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.exceptions import DataOverflowError
from qrcode.image.svg import SvgPathImage

from app.catalog_api import Actor, authenticate
from app import subscription_chatbot_store as shared
from app.subscription_whatsapp_link_schemas import LinkRequest

BASE = "/api/v1/tenants/{tenant_id}/subscription-whatsapp/whatsapp-link"
FEATURE = "whatsapp_link.manage"
router = APIRouter(tags=["Link e QR do WhatsApp"])


def generate(payload: LinkRequest):
    url = "https://wa.me/" + payload.phone.removeprefix("+")
    if payload.message:
        url += "?text=" + quote(payload.message, safe="")
    result = {"phone": payload.phone, "url": url, "qr": None, "qr_notice": None}
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=5, border=4)
    qr.add_data(url, optimize=0)
    try:
        qr.make(fit=True)
    except (DataOverflowError, ValueError):
        # qrcode 8 can raise ValueError when automatic fitting reaches version
        # 41, as well as DataOverflowError for an explicitly bounded version.
        result["qr_notice"] = "O link está pronto. Reduza a mensagem para que o mesmo conteúdo caiba no QR Code."
        return result
    png = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(png, format="PNG")
    svg = qr.make_image(image_factory=SvgPathImage).to_string(encoding="unicode")
    result["qr"] = {"svg": svg, "png_base64": base64.b64encode(png.getvalue()).decode("ascii"),
                    "size": (qr.modules_count + 2 * qr.border) * qr.box_size}
    return result


@router.post(BASE + "/generate")
async def generate_link(tenant_id: UUID, payload: LinkRequest, response: Response,
                        actor: Actor = Depends(authenticate)):
    async with shared.owner_transaction(tenant_id, actor, feature=FEATURE):
        pass
    result = await asyncio.to_thread(generate, payload)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return result
