"""Pure plans and context tickets for a simulation, never transport effects.

The production matcher/compiler receives copies with an explicit test policy.
Tickets stay in the local conversation; they authorize no WhatsApp operation.
"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode
from uuid import uuid4

from app import subscription_chatbot_rules as rules
from app.subscription_chatbot_test_schemas import TOKEN_CHARS
from app.subscription_template_documents import DOCUMENT_MIMES


CONTEXT_TTL = 1800
NO_MATCH = "Nenhuma resposta correspondente ao seu texto para este dispositivo"
UNAVAILABLE = "Esta resposta não pôde ser simulada. Confira as referências, tags e arquivos no Chatbot."
MEDIA_TYPES = {
    "image": ("image/jpeg", 5_000_000),
    "sticker": ("image/webp", 500_000),
    "video": ("video/mp4", 16_000_000),
    "ptv": ("video/mp4", 16_000_000),
    "audio": ("audio/mpeg", 16_000_000),
    "document": ("text/plain; charset=utf-8", 10_000_000),
}
DOMAIN = b"orion:subscription-chatbot-test:v1"
SCOPE_KEYS = ("tenant_id", "actor_id", "device_id", "generation", "device_revision", "chatbot_revision", "context_id")


class InvalidContext(ValueError):
    pass


class ContextUnavailable(RuntimeError):
    pass


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value):
    raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if _b64(raw) != value:
        raise ValueError("Noncanonical token")
    return raw


def _key():
    try:
        raw = base64.b64decode(os.environ["INTEGRATION_FERNET_KEY"], altchars=b"-_", validate=True)
        if len(raw) != 32:
            raise ValueError()
        return hmac.new(raw, DOMAIN, hashlib.sha256).digest()
    except (KeyError, ValueError, TypeError):
        raise ContextUnavailable() from None


def context_scope(tenant, actor_id, device, revision, context_id):
    return {"tenant_id": str(tenant), "actor_id": str(actor_id), "device_id": str(device["id"]),
        "generation": str(device["generation"]), "device_revision": device["revision"],
        "chatbot_revision": revision, "context_id": str(context_id)}


def issue_ticket(scope, purpose, **references):
    now = int(time.time())
    payload = {"v": 1, "purpose": purpose, "issued_at": now, "expires_at": now + CONTEXT_TTL,
               **{key: scope[key] for key in SCOPE_KEYS}, **references}
    encoded = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    signature = _b64(hmac.new(_key(), encoded.encode("ascii"), hashlib.sha256).digest())
    token = encoded + "." + signature
    if len(token) > TOKEN_CHARS:
        raise ContextUnavailable()
    return token, datetime.fromtimestamp(payload["expires_at"], timezone.utc).isoformat()


def read_ticket(token, purpose, *, expected=None):
    key = _key()
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= TOKEN_CHARS:
            raise ValueError()
        encoded, signature = token.split(".")
        if not hmac.compare_digest(_unb64(signature), hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest()):
            raise ValueError()
        payload = json.loads(_unb64(encoded))
        now = int(time.time())
        if (not isinstance(payload, dict) or payload.get("v") != 1 or payload.get("purpose") != purpose
                or type(payload.get("issued_at")) is not int or type(payload.get("expires_at")) is not int
                or payload["expires_at"] != payload["issued_at"] + CONTEXT_TTL
                or payload["issued_at"] > now + 30 or payload["expires_at"] <= now
                or any(payload.get(key) != value for key, value in (expected or {}).items())):
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise InvalidContext() from None


def eligible(rule, device_id):
    return bool(rule and not rule.get("removed_at") and
                (rule.get("device_id") is None or str(rule["device_id"]) == str(device_id)))


def simulation_graph(graph):
    copied_rules, copied_templates = deepcopy(graph[0]), deepcopy(graph[1])
    for identity, rule in copied_rules.items():
        rule["enabled"] = True
        if rule["response"]["kind"] == "list":
            # The compiler copies a list without evaluating its options. This
            # private marker identifies that list after nested expansion.
            rule["response"]["_test_rule_id"] = str(identity)
    return copied_rules, copied_templates


def keyword_catalog(graph, device_id):
    return [{"rule_id": str(row["id"]), "keyword": row["keyword"], "match_mode": row["match_mode"],
             "enabled": row["enabled"]}
            for row in sorted(graph[0].values(), key=lambda row: row["creation_order"], reverse=True)
            if eligible(row, device_id)]


def asset_reference(asset):
    kind = asset["kind"]
    expected = MEDIA_TYPES.get(kind)
    if (not expected or asset["mime"] not in (DOCUMENT_MIMES if kind == "document" else {expected[0]}) or not 1 <= asset["size"] <= expected[1]
            or asset.get("thumbnail") and (asset["thumbnail"]["mime"] != "image/jpeg"
                or not 1 <= asset["thumbnail"]["size"] <= 100_000)):
        raise ValueError("Unsupported normalized media")
    return {"id": str(asset["id"]), **{key: asset[key] for key in
        ("kind", "mime", "size", "sha256", "width", "height", "duration_ms", "thumbnail")}}


def compile_messages(root, graph, scope, facts, http_context, base, *, selected_action=None):
    copied_rules, copied_templates = simulation_graph(graph)
    plan, _ = rules.compile_plan(str(root["id"]), copied_rules, copied_templates, facts, http_context,
                                 selected_action=deepcopy(selected_action))
    messages = []
    for item in plan:
        action = item["action"]
        kind = action["type"]
        message = {"id": str(uuid4()), "delay_seconds": item["delay_seconds"]}
        if kind == "text":
            message.update(type="text", text=action["text"])
        elif kind == "list":
            source = graph[0].get(action["_test_rule_id"])
            if not eligible(source, scope["device_id"]):
                raise InvalidContext()
            ticket, expiry = issue_ticket(scope, "menu", rule_id=str(source["id"]), rule_revision=source["revision"])
            message.update(type="list", **{key: action[key] for key in ("title", "description", "footer", "button_text")},
                selection_token=ticket, expires_at=expiry,
                sections=[{"id": str(section["id"]), "title": section["title"],
                    "rows": [{"id": str(row["id"]), "title": row["title"], "description": row["description"]}
                             for row in section["rows"]]} for section in action["sections"]])
        elif kind in MEDIA_TYPES:
            asset = action["asset"]
            reference = asset_reference(asset)
            ticket, _ = issue_ticket(scope, "media", rule_id=str(root["id"]), rule_revision=root["revision"], asset=reference)
            path = base + "/assets/" + str(asset["id"]) + "/content?"
            thumbnail = asset.get("thumbnail")
            value = {key: reference[key] for key in ("id", "kind", "mime", "size", "width", "height", "duration_ms")}
            value.update(file_name=action.get("file_name") or asset["file_name"],
                content_url=path + urlencode({"ticket": ticket, "variant": "original"}),
                thumbnail_url=path + urlencode({"ticket": ticket, "variant": "thumbnail"}) if thumbnail else None,
                thumbnail_mime=thumbnail["mime"] if thumbnail else None,
                thumbnail_size=thumbnail["size"] if thumbnail else None)
            message.update(type="media", kind=kind, caption=action.get("caption") or "", asset=value)
        elif kind == "location":
            message.update(type="location", latitude=action["latitude"], longitude=action["longitude"])
        elif kind == "http":
            message.update(type="external_request", **{key: action[key] for key in ("url", "method", "as_file", "custom_template")},
                executed=False, text="Etapa de URL não executada na simulação. O retorno do servidor não foi consultado.")
        elif kind == "carousel":
            message.update(type="carousel", body=action["body"],
                cards=[{key: card[key] for key in ("title", "body", "button_text")} | {"image_status": "not_requested"}
                       for card in action["cards"]],
                notice="As imagens externas não são consultadas na simulação. Os botões deste carrossel não possuem resposta cadastrada.")
        else:
            raise ValueError("Unknown action")
        messages.append(message)
    return messages


def text_message(value, *, unavailable=False):
    return {"id": str(uuid4()), "type": "unavailable" if unavailable else "text", "text": value,
            "delay_seconds": 0, **({"code": "CHATBOT_TEST_UNAVAILABLE"} if unavailable else {})}
