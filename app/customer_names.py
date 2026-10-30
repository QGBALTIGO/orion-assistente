"""Names supplied by people or WhatsApp metadata, never inferred from messages.

WhatsApp profile names are display labels, not verified civil identities. A
profile may fill an unnamed customer; it cannot replace an existing useful name.
Callers must authenticate and resolve the tenant before opening the transaction.
"""
import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import Field
from sqlalchemy import text

from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.customer_identity import resolve_customer


PHONE_RE = re.compile(r"^[1-9][0-9]{7,14}$")
CUSTOMER_PHONE_RE = re.compile(r"^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$")
PLACEHOLDER_NAMES = frozenset({
    "cliente whatsapp", "cliente do whatsapp", "contato whatsapp", "contato do whatsapp", "whatsapp",
    "cliente", "contato", "sem nome", "nome nao informado", "desconhecido",
    "unknown", "whatsapp customer", "whatsapp contact",
})


class WhatsAppProfile(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    source: Literal["contact_name", "verified_name", "push_name"]


def contact_phone(value) -> str | None:
    """Accept an explicit international PN; never convert a JID/LID to a phone."""
    if not isinstance(value, str):
        return None
    digits = value.removeprefix("+")
    return digits if PHONE_RE.fullmatch(digits) else None


def useful_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    has_emoji = any(unicodedata.category(char) in {"So", "Sk"} for char in name)
    if (not 1 <= len(name) <= 120 or not (has_emoji or any(char.isalpha() for char in name))
            or any(unicodedata.category(char).startswith("C") and not (char == "\u200d" and has_emoji)
                   for char in name)
            or re.search(r"@(?:lid|s\.whatsapp\.net|g\.us)\b", name, re.IGNORECASE)):
        return None
    key = "".join(char for char in unicodedata.normalize("NFKD", name.casefold())
                  if not unicodedata.combining(char))
    return None if key in PLACEHOLDER_NAMES else name


def display_customer(row, whatsapp_name=None):
    """Add presentation fields without rewriting the stored editable name."""
    result = dict(row)
    known = useful_name(result.get("name"))
    whatsapp = useful_name(whatsapp_name)
    phone = result.get("phone", result.get("whatsapp_e164"))
    result["display_name"] = known or whatsapp or phone or None
    result["whatsapp_name"] = whatsapp
    return result


async def merge_whatsapp_name(db, tenant: UUID, phone: str, name: str, *, create=True) -> bool:
    """Idempotent enrichment under RLS; serialized with manual customer edits.

No unknown-name customer is created and no useful name, inactive flag, deleted
record or other customer field is replaced. The version advances only on a real
rename, so an editor holding the old version cannot silently overwrite it.
"""
    pn, clean = contact_phone(phone), useful_name(name)
    if pn is None or not CUSTOMER_PHONE_RE.fullmatch(pn) or clean is None:
        return False
    parameters = {"tenant": tenant, "phone": "+" + pn, "name": clean}
    current, created = await resolve_customer(db, tenant, "+" + pn, clean, create=create, lock=True)
    if created:
        return True
    if current is None or current["deleted_at"] is not None or useful_name(current["name"]):
        return False
    await db.execute(text("""
        UPDATE customers SET name=:name,version=version+1,updated_at=now()
        WHERE tenant_id=:tenant AND id=:id AND name=:previous AND deleted_at IS NULL
    """), {**parameters, "id": current["id"], "previous": current["name"]})
    return True


async def remember_whatsapp_name(tenant: UUID, phone: str, name: str) -> bool:
    if contact_phone(phone) is None or useful_name(name) is None:
        return False
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            return await merge_whatsapp_name(db, tenant, phone, name)
