"""Brief factual receipt of a verified completed operation, never active state.

This is not an operation queue or a retry record. No hold, token, payment claim,
model text or latch authorizes a receipt. Callers explicitly mark a terminal
result only after the actual backend returned it. A later correction can then
be answered truthfully without restoring a completed booking or executing it.
"""
import json
import math
import re
from time import time
from uuid import UUID

from app import qr_intake_revision as revision
from app import qr_channel_scope
from app.qr_presentation import fits_message

TTL = 300
TERMINAL = frozenset({"confirmed", "rescheduled", "cancelled", "commerce_confirmed", "commerce_cancelled"})
ID_PATTERN = r"[a-zA-Z0-9._:-]{1,160}"
PHONE_PATTERN = r"55[1-9][0-9]{9,10}"


def key(tenant, phone):
    if not isinstance(tenant, UUID) or not isinstance(phone, str) or not re.fullmatch(PHONE_PATTERN, phone):
        raise ValueError("Authenticated tenant and contact required")
    return f"orion:qr:operation-receipt:{qr_channel_scope.tenant_scope(tenant)}:{phone}"


def _factual_result(result):
    return (isinstance(result, dict) and result.get("step") in TERMINAL and
        isinstance(result.get("answer"), str) and bool(result["answer"].strip()) and fits_message(result["answer"]))


async def save(redis, tenant, phone, message_id, result, *, verified_operation=False, preview=False):
    """Persist the verified result even when a later ingress revoked its reply.

    The private marker is a caller-owned assertion, not part of any HTTP input.
    In particular, a generic `cancelled` reply or a write-started latch is not
    enough. Pick factual fields explicitly so tokens/private state cannot leak.
    """
    if (preview or verified_operation is not True or not _factual_result(result) or
            not isinstance(message_id, str) or not re.fullmatch(ID_PATTERN, message_id)):
        return False
    receipt = {"tenant": str(tenant), "phone": phone, "message_id": message_id,
        "step": result["step"], "answer": result["answer"], "saved_at": time()}
    identity = qr_channel_scope.current()
    if identity is not None:
        receipt["channel_id"] = str(identity.channel_id)
    protocol = result.get("protocol")
    if isinstance(protocol, str) and re.fullmatch(r"[a-zA-Z0-9._:-]{1,80}", protocol):
        receipt["protocol"] = protocol
    await redis.set(key(tenant, phone), json.dumps(receipt, ensure_ascii=True), ex=TTL)
    return True


def _read(raw, tenant, phone):
    try:
        qr_channel_scope.tenant_scope(tenant)
        identity = qr_channel_scope.current()
        value = json.loads(raw)
        age = time() - value["saved_at"] if type(value.get("saved_at")) in {int, float} else math.inf
        if (not _factual_result(value) or value.get("tenant") != str(tenant) or value.get("phone") != phone or
                value.get("channel_id") != (str(identity.channel_id) if identity else None) or
                not isinstance(value.get("message_id"), str) or not re.fullmatch(ID_PATTERN, value["message_id"]) or
                not math.isfinite(age) or not 0 <= age < TTL):
            return None
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _answer(receipt):
    body = receipt["answer"]
    explanation = ("Esta operação foi concluída. A alteração da sua nova mensagem ainda não foi aplicada. "
        "Se quiser mudar algo, me diga o que deseja revisar; vou conferir as opções antes de uma nova confirmação.")
    complete = body + "\n\n" + explanation
    if fits_message(complete):
        return complete
    short = "Operação concluída; sua nova alteração ainda não foi aplicada.\n\n" + body
    return short if fits_message(short) else body  # Preserve the complete verified facts.


async def pending(redis, tenant, phone, require_summary_review, *, preview=False):
    """Present known completion once delivery is uncertain, without a new turn.

    A false signal comes from the authenticated bridge after an accepted,
    current reply. It acknowledges this short-lived receipt and normal handling
    resumes. A true signal can repeatedly show facts; it never executes a plan.
    """
    if preview:
        return None
    target = key(tenant, phone)
    await revision.assert_current()
    if require_summary_review is False:
        await redis.delete(target)
        return None
    if require_summary_review is not True:
        raise ValueError("Explicit authenticated delivery signal required")
    raw = await redis.get(target)
    receipt = _read(raw, tenant, phone) if raw else None
    await revision.assert_current()
    if receipt is None:
        return None
    return {"answer": _answer(receipt), "step": "completed_operation_review"}
