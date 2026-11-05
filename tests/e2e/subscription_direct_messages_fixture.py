"""Loopback direct-send ledger using the production input schemas.

All identity, contacts, files and results are synthetic. This fixture records
admission and cancellation, never invokes a provider or delivers a message.
"""
from __future__ import annotations

from base64 import b64decode
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.subscription_direct_schemas import ReviewDirectMessage, ConfirmDirectMessage, DirectAction
from app.subscription_direct_policy import url_button_plan
from app.subscription_bulk_store import message_preview
from app.subscription_templates_schemas import UploadAsset, media_filename
from browser_subscription_chatbot import Handler as ChatbotHandler, ROOT, base, now
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_direct_messages_contract.json").read_text())
DIRECT_BASE = base.BASE + "/subscription-whatsapp/direct-messages"
TEMPLATE_BASE = base.BASE + "/subscription-whatsapp/templates"
SEND_ID = CONTRACT["detail"]["send"]["send_id"]
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
MIMES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/jpeg", "webp": "image/jpeg",
    "mp3": "audio/mpeg", "ogg": "audio/mpeg", "wav": "audio/mpeg", "opus": "audio/mpeg",
    "mp4": "video/mp4", "pdf": "application/pdf", "txt": "text/plain; charset=utf-8",
    "csv": "text/plain; charset=utf-8", "apk": "application/vnd.android.package-archive",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def uid(value):
    return f"00000000-0000-4000-8000-{value:012}"


def page_result(items, query=None):
    page = max(1, int((query or {}).get("page", ["1"])[0]))
    return {"items": deepcopy(items[(page - 1) * 30:page * 30]), "total": len(items),
        "page": page, "page_size": 30, "pages": max(1, (len(items) + 29) // 30),
        "timezone": "America/Cuiaba", "generated_at": now()}


def public_operation(value):
    value = deepcopy(value)
    if value["status"] == "prepared" and datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        value["status"] = "expired"
    if value["status"] != "prepared":
        value["preview"] = None
    return value


def update_progress(detail):
    counts = Counter(row["status"] for row in detail["recipients"]["items"])
    total = len(detail["recipients"]["items"])
    processed = total - counts["pending"] - counts["running"]
    detail["send"]["progress"] = {"total": total, "processed": processed,
        "pending": counts["pending"] + counts["running"],
        **{key: counts[key] for key in ("sent", "failed", "uncertain", "cancelled")},
        "percent": round(processed / total * 100, 2) if total else 0}


def preview(session, message):
    device = next(item for item in session["dm_options"]["devices"] if item["id"] == message["device_id"])
    template = next((item for item in session["dm_options"]["templates"] if item["id"] == message.get("template_id")), None)
    asset = session["dm_assets"].get(message.get("asset_id"))
    kind = template["kind"] if template else asset["kind"] if asset else "text"
    body = message.get("text", message.get("caption", "Conteúdo sintético " + kind))
    warnings = []
    if message["kind"] == "media":
        assert asset and message["file_name"] == asset["file_name"]
        if kind == "audio":
            if body:
                warnings.append("AUDIO_CAPTION_OMITTED")
            body = ""
        else:
            flattened = re.sub(r"[\r\n]+", " ", body)
            if flattened != body:
                warnings.append("CAPTION_LINES_NORMALIZED")
            body = flattened
    part = {"type": kind}
    if kind == "text":
        part["text"] = body
    if kind in {"image", "video", "document"}:
        part["caption"] = body
    if kind == "document":
        part["file_name"] = message.get("file_name", "Exemplo.pdf")
    if kind == "location":
        part.update(latitude=-15.6, longitude=-56.1)
    followup = "Complemento sintético" if template and kind != "text" else None
    content = {"type": kind, "template_name": template["name"] if template else None,
        "text": body if kind == "text" else None, "caption": body if kind in {"image", "video", "document"} else None,
        "followup": followup, "parts": [part] + ([{"type": "text", "text": followup}] if followup else [])}
    if message["kind"] == "url_button":
        kind = "url_button"
        content = message_preview(url_button_plan(message), None)
    recipients = message["recipients"]
    digits = [recipients["phone"]] if recipients["mode"] == "phone" else [key[6:] for key in recipients["contact_keys"]]
    rows = []
    for ordinal, phone in enumerate(digits, 1):
        contact = next((item for item in session["dm_contacts"] if item["key"] == "phone:" + phone), None) if recipients["mode"] == "contacts" else None
        # This loopback fixture can render a validated CTA review, but has no
        # current real bridge capability proof. It never permits its send.
        available = kind != "url_button" and (contact is None or contact["available"])
        blocked_reason = ("Prévia sintética sem transporte habilitado." if kind == "url_button" else
                          None if available else contact["blocked_reason"])
        rows.append({"row_key": str(uuid4()), "ordinal": ordinal, "source": "catalog" if contact else "manual",
            "phone_e164": "+" + phone, "name": contact["name"] if contact else "",
            "device": {key: device.get(key) for key in ("id", "name", "phone_e164")},
            "message_type": message["kind"], "template": deepcopy(template), "message_preview": deepcopy(content),
            "available": available, "blocked_reason": blocked_reason,
            "error_code": None if available else "DIRECT_DEVICE_UNAVAILABLE" if kind == "url_button" else "DIRECT_OPTED_OUT"})
    blocked = sum(not row["available"] for row in rows)
    return {"review_version": sha256(str(uuid4()).encode()).hexdigest(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat(),
        "kind": message["kind"], "timezone": session["dm_options"]["timezone"],
        "total": len(rows), "eligible": len(rows) - blocked, "blocked": blocked, "items": rows,
        "warnings": warnings, "save_template": {"name": message["save_template"]["name"], "kind": kind} if message.get("save_template") else None}


class Handler(ChatbotHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            with base._lock:
                session = base._sessions[cookie]
                options = deepcopy(CONTRACT["options"])
                options["generated_at"] = now()
                options["devices"].append({**deepcopy(options["devices"][0]), "id": uid(30), "name": "Dispositivo secundário"})
                options["templates"] = [{"id": uid(100 + index), "name": "Template " + kind,
                    "kind": kind, "available": True, "blocked_reason": None, "revision": 1} for index, kind in enumerate(KINDS)]
                contacts = [{**deepcopy(CONTRACT["contacts"]["items"][0]),
                    "key": "phone:" + str(5567999999901 + index), "phone_e164": "+" + str(5567999999901 + index),
                    "name": "Contato sintético " + f"{index + 1:02}"} for index in range(35)]
                contacts[-1].update(available=False, blocked_reason="O contato retirou a autorização.")
                session.update(dm_calls=[], dm_requests={}, dm_confirmations=[], dm_effects=[], dm_next=None,
                    dm_revoked=False, dm_options=options, dm_contacts=contacts, dm_uploads={}, dm_assets={},
                    dm_saved_templates=[], dm_sends={SEND_ID: deepcopy(CONTRACT["detail"])})
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            profile = session["workspace_profile"]
            plan["workspace_profile"] = profile
            features = ["account.manage", "business.setup", "devices.manage"]
            if profile == "subscription_services" and not session["chatbot_readonly"]:
                features.extend(["chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage", "campaigns.manage", "scheduled_messages.manage", "bulk_messages.manage"])
                if not session["dm_revoked"]:
                    features.append("direct_messages.manage")
            plan["features"] = features if profile == "subscription_services" else plan["features"]
            plan["permissions"] = {key: key in features if profile == "subscription_services" else
                (False if key == "direct_messages.manage" else enabled) for key, enabled in
                {**plan["permissions"], **{key: False for key in features}, "direct_messages.manage": False}.items()}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    @staticmethod
    def allowed(session):
        return session and session["workspace_profile"] == "subscription_services" and not session["chatbot_readonly"] and not session["dm_revoked"]

    def error(self, code, status=409, rejection=None):
        detail = {"code": code, "message": "A situação sintética mudou. Consulte o mesmo pedido."}
        if rejection is not None:
            detail["rejection"] = rejection
        return self.body({"detail": detail}, status)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/direct-report":
            return self.body({"calls": session["dm_calls"], "operations": list(session["dm_requests"].values()),
                "sends": list(session["dm_sends"].values()), "confirmations": session["dm_confirmations"],
                "saved_templates": session["dm_saved_templates"], "uploads": list(session["dm_uploads"].values()),
                "effects": session["dm_effects"], "unexpected": session["unexpected"]})
        if not path.startswith(DIRECT_BASE):
            return super().do_GET()
        if session:
            session["dm_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(DIRECT_BASE):]
        if suffix == "/options":
            return self.body(deepcopy(session["dm_options"]))
        if suffix == "/contacts":
            term = query.get("q", [""])[0].casefold()
            return self.body(page_result([row for row in session["dm_contacts"] if term in (row["name"] + " " + row["phone_e164"]).casefold()], query))
        if suffix.startswith("/requests/"):
            prior = session["dm_requests"].get(suffix.rsplit("/", 1)[-1])
            return self.body({**public_operation(prior["operation"]), "replayed": True}) if prior else self.error("DIRECT_NOT_FOUND", 404)
        detail = session["dm_sends"].get(suffix[1:])
        if detail is None:
            return self.error("DIRECT_NOT_FOUND", 404)
        return self.body({"send": deepcopy(detail["send"]), "recipients": page_result(detail["recipients"]["items"])})

    def control(self, session, mode):
        if mode == "revoke":
            session["dm_revoked"] = True
        elif mode == "expire_review":
            for entry in session["dm_requests"].values():
                if entry["operation"]["status"] == "prepared":
                    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                    entry["operation"]["expires_at"] = expired
                    entry["operation"]["preview"]["expires_at"] = expired
        elif mode == "progress":
            detail = session["dm_sends"][SEND_ID]
            detail["send"].update(status="running", status_label="Em processamento")
            detail["recipients"]["items"] = [{**deepcopy(CONTRACT["detail"]["recipients"]["items"][0]),
                "id": str(uuid4()), "row_key": str(uuid4()), "ordinal": index + 1,
                "phone_e164": "+" + str(5567999999901 + index), "name": "Contato sintético " + status,
                "status": status, "error_message": "O resultado não pôde ser confirmado." if status == "uncertain" else None}
                for index, status in enumerate(("pending", "running", "sent", "failed", "uncertain", "cancelled"))]
            update_progress(detail)
        elif mode in {"lost_review", "lost_confirm", "lost_action", "missing_review", "stale_action", "generic_action", "malformed_review"}:
            session["dm_next"] = mode
        else:
            return self.reject(session, "Unknown direct control")
        return self.body({"ok": True})

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/direct-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            return self.control(session, data["mode"])
        if not (path.startswith(DIRECT_BASE) or path == TEMPLATE_BASE + "/assets"):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(DIRECT_BASE):]
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.command != "POST" or not 0 < size <= 14_000_000:
                raise ValueError("Bounded POST required")
            raw = json.loads(self.rfile.read(size))
            session["dm_calls"].append({"method": self.command, "path": path, "data": deepcopy(raw)})
            model = UploadAsset if path == TEMPLATE_BASE + "/assets" else {"/review": ReviewDirectMessage, "/confirm": ConfirmDirectMessage}.get(suffix) or (DirectAction if suffix.endswith("/actions") else None)
            if model is None:
                return self.reject(session, "Unexpected direct mutation")
            data = model.model_validate(raw).model_dump(mode="json", exclude_none=True)
        except (ValueError, TypeError, KeyError):
            return self.error("INVALID_INPUT", 422)
        if model is UploadAsset:
            key = data["request_id"]
            prior = session["dm_uploads"].get(key)
            if prior:
                return self.body(prior["result"]) if prior["data"] == data else self.error("TEMPLATE_REQUEST_CONFLICT")
            mime = MIMES[data["filename"].rsplit(".", 1)[-1].lower()]
            filename = media_filename(data["filename"], mime)
            result = {**deepcopy(CONTRACT["upload_response"]), "request_id": key,
                "chatbot_revision": data["expected_chatbot_revision"], "file_name": filename}
            result["asset"].update(id=str(uuid4()), kind=data["kind"], mime=mime, file_name=filename,
                size=len(b64decode(data["content_base64"])))
            session["dm_assets"][result["asset"]["id"]] = deepcopy(result["asset"])
            session["dm_uploads"][key] = {"data": data, "result": deepcopy(result)}
            return self.body(result)
        key, prior = data["idempotency_key"], session["dm_requests"].get(data["idempotency_key"])
        if model is ConfirmDirectMessage:
            if prior is None:
                return self.error("DIRECT_NOT_FOUND", 404)
            op = prior["operation"]
            if op["status"] == "succeeded":
                return self.body({**public_operation(op), "replayed": True})
            if op["preview"]["review_version"] != data["review_version"]:
                return self.error("DIRECT_CONFLICT")
            if public_operation(op)["status"] == "expired":
                return self.error("DIRECT_REVIEW_EXPIRED", 410)
            if not op["preview"]["eligible"]:
                return self.error("DIRECT_NO_ELIGIBLE", 422)
            identity, rows = str(uuid4()), []
            for row in op["preview"]["items"]:
                value = deepcopy(row)
                value.pop("available")
                value.pop("blocked_reason")
                value.update(id=str(uuid4()), send_id=identity, status="pending" if row["available"] else "cancelled",
                    status_label="Na fila" if row["available"] else "Cancelado", error_message=row["blocked_reason"],
                    parts=[], sent_at=None, created_at=now())
                rows.append(value)
            send = {**deepcopy(CONTRACT["detail"]["send"]), "send_id": identity, "created_at": now(), "confirmed_at": now()}
            detail = {"send": send, "recipients": page_result(rows)}
            update_progress(detail)
            session["dm_sends"][identity] = detail
            saved = {"id": str(uuid4()), **op["preview"]["save_template"], "revision": session["dm_options"]["chatbot_revision"] + 1} if op["preview"]["save_template"] else None
            if saved:
                session["dm_saved_templates"].append(deepcopy(saved))
            op.update(status="succeeded", send_id=identity, result={"send": deepcopy(send), "saved_template": saved})
            session["dm_confirmations"].append({"idempotency_key": key, "send_id": identity})
            return self.respond_operation(session, op, "confirm")
        if prior:
            if prior["path"] != path or prior["data"] != data:
                return self.error("DIRECT_IDEMPOTENCY_CONFLICT")
            return self.body({**public_operation(prior["operation"]), "replayed": True})
        op = {"request_id": str(uuid4()), "idempotency_key": key, "kind": "review", "status": "prepared",
            "send_id": None, "replayed": False, "created_at": now(), "expires_at": None, "preview": None, "result": None}
        if model is ReviewDirectMessage:
            if session["dm_next"] == "missing_review":
                session["dm_next"] = None
                return self.error("SYNTHETIC_REJECTED", 503)
            op["preview"] = preview(session, data["message"])
            op["expires_at"] = op["preview"]["expires_at"]
        else:
            target = session["dm_sends"].get(suffix.split("/")[1])
            if target is None:
                return self.error("DIRECT_NOT_FOUND", 404)
            if session["dm_next"] == "stale_action":
                target["send"]["version"] += 1
                session["dm_next"] = None
            if session["dm_next"] == "generic_action":
                session["dm_next"] = None
                return self.error("DIRECT_CONFLICT")
            version = target["send"]["version"]
            if data["expected_version"] != version:
                proof = {"reason": "stale_version", "idempotency_key": key, "kind": "cancel",
                    "send_id": target["send"]["send_id"], "expected_version": data["expected_version"]} if data["expected_version"] < version else None
                return self.error("DIRECT_CONFLICT", rejection=proof)
            for row in target["recipients"]["items"]:
                if row["status"] == "pending":
                    row.update(status="cancelled", status_label="Cancelado")
            target["send"].update(version=version + 1, status="uncertain" if any(row["status"] == "uncertain" for row in target["recipients"]["items"]) else "cancelled", actions=[], finished_at=now())
            update_progress(target)
            op.update(kind="cancel", status="succeeded", send_id=target["send"]["send_id"], result={"send": deepcopy(target["send"]), "saved_template": None})
        session["dm_requests"][key] = {"path": path, "data": deepcopy(data), "operation": op}
        return self.respond_operation(session, op, "action" if model is DirectAction else "review")

    def respond_operation(self, session, operation, phase):
        if session["dm_next"] == "lost_" + phase:
            session["dm_next"] = None
            return self.error("SYNTHETIC_LOST_RESPONSE", 503)
        result = public_operation(operation)
        if session["dm_next"] == "malformed_review" and phase == "review":
            session["dm_next"] = None
            result["idempotency_key"] = str(uuid4())
        return self.body(result)
