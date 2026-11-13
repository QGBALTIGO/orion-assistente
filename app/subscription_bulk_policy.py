"""Pure frozen-message and result projections for immediate bulk batches."""
from app import subscription_campaign_policy as campaigns
from app.subscription_chatbot_schemas import clean_text

TERMINAL = frozenset({"completed", "failed", "uncertain", "cancelled"})
LABELS = {"pending": "Na fila", "running": "Enviando", "completed": "Concluído",
          "failed": "Falhou", "uncertain": "Resultado incerto", "cancelled": "Cancelado"}
RECIPIENT_LABELS = {"pending": "Esperando", "running": "Enviando", "sent": "Enviada",
                    "failed": "Falhou", "uncertain": "Resultado incerto", "cancelled": "Cancelada"}
CANCELLATION_CODES = frozenset({"BULK_CANCELLED", "BULK_OPTED_OUT", "BULK_SOURCE_REMOVED",
    "BULK_REVOKED", "BULK_CONTEXT_CHANGED", "BULK_TEMPLATE_CHANGED", "BULK_SELF_CONTACT"})
progress = campaigns.progress


def render_text(value, facts=None):
    # The observed plain-text field offers no variable substitution. Braces,
    # JSON and WhatsApp markup are literal text, including {name}.
    return clean_text(value, maximum=1000)


def frozen_plan(item, template, facts):
    if item["message_type"] == "text":
        return [{"type": "text", "text": render_text(item["text"], facts)}]
    return campaigns.frozen_plan(item, template, facts)


def recipient_state(recipient, parts, now):
    state, code = campaigns.recipient_state(recipient, parts, now)
    if code == "CAMPAIGN_EFFECT_EXPIRED":
        code = "BULK_EFFECT_EXPIRED"
    if state == "failed" and code in CANCELLATION_CODES and not any(
            part.get("status") == "accepted" for part in parts):
        return "cancelled", code
    return state, code


def duplicate_counts(phones):
    seen, duplicate_phones, duplicates = set(), set(), 0
    for phone in phones:
        if phone in seen:
            duplicates += 1
            duplicate_phones.add(phone)
        seen.add(phone)
    return {"duplicates": duplicates, "duplicate_destinations": len(duplicate_phones),
            "unique_destinations": len(seen)}
