"""Loopback bulk ledger using the shipped request schemas, with no transport.

Only synthetic contacts, devices, templates and batches are stored in memory.
The real panel and API adapter talk to this HTTP fixture during browser tests.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.subscription_bulk_schemas import AudienceRequest, ReviewBulk, ConfirmBulk, BulkAction
from browser_subscription_chatbot import Handler as ChatbotHandler, ROOT, base, now
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_bulk_messages_contract.json").read_text())
BULK_BASE = base.BASE + "/subscription-whatsapp/bulk-messages"
BATCH_ID = CONTRACT["detail"]["batch"]["batch_id"]
KINDS = ("text", "image", "video", "audio", "document", "sticker", "ptv", "location")
LABELS = {"pending": "Esperando", "running": "Em processamento", "sent": "Enviado",
          "failed": "Falhou", "uncertain": "Resultado incerto", "cancelled": "Cancelado"}


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
    detail["batch"]["progress"] = {"total": total, "processed": processed,
        "pending": counts["pending"] + counts["running"],
        **{key: counts[key] for key in ("sent", "failed", "uncertain", "cancelled")},
        "percent": round(processed / total * 100, 2) if total else 0}


def selection(session, value):
    contacts = {row["key"]: row for row in session["bm_contacts"]}
    chosen = list(contacts) if value["all_contacts"] else value["contact_keys"]
    combined = chosen + [key for group in value["group_ids"] for key in session["bm_members"].get(group, [])]
    keys = list(dict.fromkeys(combined))
    if len(keys) > 500:
        raise ValueError("At most500")
    rows = [deepcopy(contacts[key]) for key in keys]
    blocked = sum(not row["available"] for row in rows)
    return {"items": rows, "total": len(rows), "eligible": len(rows) - blocked, "blocked": blocked,
            "duplicates": len(combined) - len(keys), "timezone": session["bm_options"]["timezone"], "generated_at": now()}


def preview_row(session, row, ordinal):
    contact = next((value for value in session["bm_contacts"] if value["key"] == row.get("contact_key")), None)
    template = next((value for value in session["bm_options"]["templates"] if value["id"] == row.get("template_id")), None)
    device = next(value for value in session["bm_options"]["devices"] if value["id"] == row["device_id"])
    kind = template["kind"] if template else "text"
    text = row["text"] if not template else "Conteúdo sintético " + kind
    part = {"type": kind, "text": text if kind in {"text", "image", "video", "document"} else None}
    if kind == "document":
        part["file_name"] = "Exemplo.pdf"
    elif kind == "location":
        part.update(latitude=-15.6, longitude=-56.1)
    followup = "Complemento sintético" if template and kind != "text" else None
    available = contact is None or contact["available"]
    digits = row["contact_key"][6:] if row["source"] == "catalog" else row["phone"]
    return {"row_key": row["row_key"], "ordinal": ordinal, "source": row["source"],
        "phone_e164": "+" + digits, "name": contact["name"] if contact else "",
        "device": {key: device.get(key) for key in ("id", "name", "phone_e164")},
        "message_type": row["message_type"],
        "template": {key: template[key] for key in ("id", "name", "kind")} if template else None,
        "message_preview": {"type": kind, "template_name": template["name"] if template else None,
            "text": text if kind == "text" else None, "caption": text if kind in {"image", "video", "document"} else None,
            "followup": followup, "parts": [part] + ([{"type": "text", "text": followup}] if followup else [])},
        "available": available, "blocked_reason": None if available else contact["blocked_reason"],
        "error_code": None if available else "BULK_OPTED_OUT"}


class Handler(ChatbotHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                session = base._sessions[cookie]
                options = deepcopy(CONTRACT["options"])
                options["devices"].append({**deepcopy(options["devices"][0]), "id": uid(30), "name": "Dispositivo secundário"})
                options["templates"] = [{"id": uid(100 + index), "name": "Template sintético " + kind,
                    "kind": kind, "available": True, "blocked_reason": None, "revision": 1} for index, kind in enumerate(KINDS)]
                contacts = deepcopy(CONTRACT["contacts"]["items"])
                contacts.extend([{**deepcopy(contacts[0]), "key": "phone:5567999999903", "phone_e164": "+5567999999903", "name": "Contato complementar"},
                    {**deepcopy(contacts[0]), "key": "phone:5567999999904", "phone_e164": "+5567999999904", "name": "Contato com restrição", "available": False, "blocked_reason": "O contato retirou a autorização."}])
                options["groups"] = [{"id": uid(200), "name": "Clientes sintéticos", "version": 1, "member_count": 2, "blocked_count": 0, "archived": False},
                    {"id": uid(201), "name": "Grupo em comum", "version": 1, "member_count": 2, "blocked_count": 1, "archived": False}]
                session.update(bm_calls=[], bm_requests={}, bm_confirmations=[], bm_effects=[], bm_next=None,
                    bm_revoked=False, bm_options=options, bm_contacts=contacts,
                    bm_batches={} if query.get("fixtureEmpty") == ["1"] else {BATCH_ID: deepcopy(CONTRACT["detail"])},
                    bm_members={uid(200): [row["key"] for row in contacts[:2]], uid(201): [row["key"] for row in contacts[1:]]})
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            profile = session["workspace_profile"]
            plan["workspace_profile"] = profile
            features = ["account.manage", "business.setup", "devices.manage"]
            if profile == "subscription_services" and not session["chatbot_readonly"]:
                features.extend(["chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage", "campaigns.manage", "scheduled_messages.manage"])
                if not session["bm_revoked"]:
                    features.append("bulk_messages.manage")
            plan["features"] = features if profile == "subscription_services" else plan["features"]
            plan["permissions"] = {key: key in features if profile == "subscription_services" else
                (False if key == "bulk_messages.manage" else enabled) for key, enabled in
                {**plan["permissions"], **{key: False for key in features}, "bulk_messages.manage": False}.items()}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    @staticmethod
    def allowed(session):
        return session and session["workspace_profile"] == "subscription_services" and not session["chatbot_readonly"] and not session["bm_revoked"]

    def error(self, code, status=409, rejection=None):
        detail = {"code": code, "message": "A situação sintética mudou. Consulte o mesmo pedido."}
        if rejection is not None:
            detail["rejection"] = rejection
        return self.body({"detail": detail}, status)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/bulk-report":
            return self.body({"calls": session["bm_calls"], "operations": list(session["bm_requests"].values()),
                "batches": list(session["bm_batches"].values()), "confirmations": session["bm_confirmations"],
                "effects": session["bm_effects"], "unexpected": session["unexpected"]})
        if not path.startswith(BULK_BASE):
            return super().do_GET()
        if session:
            session["bm_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(BULK_BASE):]
        if suffix == "/options":
            return self.body(deepcopy(session["bm_options"]))
        if suffix == "/contacts":
            needle = query.get("q", [""])[0].casefold()
            return self.body(page_result([row for row in session["bm_contacts"] if needle in (row["name"] + " " + row["phone_e164"]).casefold()], query))
        if suffix.startswith("/requests/"):
            prior = session["bm_requests"].get(suffix.rsplit("/", 1)[-1])
            return self.body({**public_operation(prior["operation"]), "replayed": True}) if prior else self.error("BULK_NOT_FOUND", 404)
        if suffix in {"", "/"}:
            needle = query.get("q", [""])[0].casefold()
            rows = [row for value in session["bm_batches"].values() for row in value["recipients"]["items"]]
            filtered = [row for row in rows if needle in (row["name"] + " " + row["phone_e164"] + " " + row["device"]["name"]).casefold()]
            sent = sum(row["status"] == "sent" for row in rows)
            active = [value["batch"] for value in session["bm_batches"].values() if value["batch"]["status"] in {"pending", "running"}]
            return self.body({**page_result(filtered, query), "counts": {"total": sent, "last_30_days": sent, "today": sent},
                "active_batches": deepcopy(active[:30]), "active_batches_total": len(active)})
        target = session["bm_batches"].get(suffix.split("/")[1])
        if target is None:
            return self.error("BULK_NOT_FOUND", 404)
        if suffix.endswith("/recipients"):
            needle, status = query.get("q", [""])[0].casefold(), query.get("status", [""])[0]
            return self.body(page_result([row for row in target["recipients"]["items"] if
                (not status or row["status"] == status) and needle in (row["name"] + " " + row["phone_e164"]).casefold()], query))
        return self.body({"batch": deepcopy(target["batch"]), "recipients": page_result(target["recipients"]["items"])})

    def control(self, session, mode):
        if mode == "revoke":
            session["bm_revoked"] = True
        elif mode == "expire_review":
            for entry in session["bm_requests"].values():
                if entry["operation"]["status"] == "prepared":
                    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
                    entry["operation"]["expires_at"] = expired
                    entry["operation"]["preview"]["expires_at"] = expired
        elif mode == "many":
            detail = session["bm_batches"][BATCH_ID]
            detail["recipients"]["items"] = [{**deepcopy(CONTRACT["history"]["items"][0]), "id": str(uuid4()),
                "row_key": str(uuid4()), "ordinal": index + 1, "name": f"Página sintética {index:02}"} for index in range(63)]
            update_progress(detail)
        elif mode == "progress":
            detail = session["bm_batches"][BATCH_ID]
            detail["batch"].update(status="running", status_label="Em processamento")
            detail["recipients"]["items"] = [{**deepcopy(CONTRACT["history"]["items"][0]), "id": str(uuid4()),
                "row_key": str(uuid4()), "ordinal": index + 1, "name": "Contato sintético " + status,
                "status": status, "status_label": LABELS[status], "sent_at": now() if status == "sent" else None,
                "error_code": "BULK_OUTCOME_UNCERTAIN" if status == "uncertain" else None,
                "error_message": "O resultado não pôde ser confirmado." if status == "uncertain" else None,
                "parts": [{"id": str(uuid4()), "type": "text", "status": "uncertain", "error_code": "BULK_OUTCOME_UNCERTAIN"}] if status == "uncertain" else []}
                for index, status in enumerate(LABELS)]
            update_progress(detail)
        elif mode in {"lost_review", "lost_confirm", "lost_action", "missing_review", "invalid_review", "stale_action", "generic_action", "marker_confirm"}:
            session["bm_next"] = mode
        else:
            return self.reject(session, "Unknown synthetic bulk control")
        return self.body({"ok": True})

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/bulk-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            return self.control(session, data["mode"])
        if not path.startswith(BULK_BASE):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(BULK_BASE):]
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.command != "POST" or not 0 < size <= 2_097_152:
                raise ValueError("Bounded POST required")
            raw = json.loads(self.rfile.read(size))
            session["bm_calls"].append({"method": self.command, "path": path, "data": deepcopy(raw)})
            model = {"/audience": AudienceRequest, "/review": ReviewBulk, "/confirm": ConfirmBulk}.get(suffix) or (BulkAction if suffix.endswith("/actions") else None)
            if model is None:
                return self.reject(session, "Unexpected bulk mutation")
            data = model.model_validate(raw).model_dump(mode="json", exclude_none=True)
            if model is AudienceRequest:
                return self.body(selection(session, data))
        except (ValueError, TypeError, KeyError):
            return self.error("INVALID_INPUT", 422)
        key, prior = data["idempotency_key"], session["bm_requests"].get(data["idempotency_key"])
        if model is ConfirmBulk:
            if prior is None:
                return self.error("BULK_NOT_FOUND", 404)
            op = prior["operation"]
            if op["status"] == "succeeded":
                return self.body({**public_operation(op), "replayed": True})
            if op["kind"] != "review" or op["preview"]["review_version"] != data["review_version"]:
                return self.error("BULK_CONFLICT")
            if datetime.fromisoformat(op["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return self.error("BULK_REVIEW_EXPIRED", 410)
            if not op["preview"]["eligible"]:
                return self.error("BULK_NO_ELIGIBLE", 422)
            identity, rows = str(uuid4()), []
            for row in op["preview"]["items"]:
                value = deepcopy(row)
                value.pop("available")
                value.pop("blocked_reason")
                value.update(id=str(uuid4()), batch_id=identity, status="pending" if row["available"] else "cancelled",
                    status_label="Esperando" if row["available"] else "Cancelado", error_message=row["blocked_reason"],
                    parts=[], sent_at=None, created_at=now())
                rows.append(value)
            batch = {**deepcopy(CONTRACT["detail"]["batch"]), "batch_id": identity, "created_at": now(),
                "confirmed_at": now(), "interval_seconds": op["preview"]["interval_seconds"]}
            detail = {"batch": batch, "recipients": {**page_result(rows), "items": rows}}
            update_progress(detail)
            session["bm_batches"][identity] = detail
            op.update(status="succeeded", batch_id=identity, result={"batch": deepcopy(batch)})
            session["bm_confirmations"].append({"idempotency_key": key, "batch_id": identity})
            return self.respond_operation(session, op, "confirm")
        if prior:
            if prior["path"] != path or prior["data"] != data:
                return self.error("BULK_IDEMPOTENCY_CONFLICT")
            return self.body({**public_operation(prior["operation"]), "replayed": True})
        op = {"request_id": str(uuid4()), "idempotency_key": key, "kind": "review", "status": "prepared",
            "batch_id": None, "replayed": False, "created_at": now(), "expires_at": None, "preview": None, "result": None}
        if model is ReviewBulk:
            if session["bm_next"] in {"missing_review", "invalid_review"}:
                mode, session["bm_next"] = session["bm_next"], None
                return self.error("SYNTHETIC_REJECTED", 422 if mode == "invalid_review" else 503)
            rows = [preview_row(session, row, index + 1) for index, row in enumerate(data["batch"]["items"])]
            counts = Counter(row["phone_e164"] for row in rows)
            blocked = sum(not row["available"] for row in rows)
            op["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat()
            op["preview"] = {"review_version": sha256(str(uuid4()).encode()).hexdigest(), "expires_at": op["expires_at"],
                "interval_seconds": data["batch"]["interval_seconds"], "timezone": session["bm_options"]["timezone"],
                "total": len(rows), "eligible": len(rows) - blocked, "blocked": blocked, "duplicates": len(rows) - len(counts),
                "duplicate_destinations": sum(value > 1 for value in counts.values()), "unique_destinations": len(counts), "items": rows}
        else:
            target = session["bm_batches"].get(suffix.split("/")[1])
            if target is None:
                return self.error("BULK_NOT_FOUND", 404)
            if session["bm_next"] == "stale_action":
                target["batch"]["version"] += 1
                session["bm_next"] = None
            if session["bm_next"] == "generic_action":
                session["bm_next"] = None
                return self.error("BULK_CONFLICT")
            version = target["batch"]["version"]
            if data["expected_version"] != version:
                proof = {"reason": "stale_version", "idempotency_key": key, "kind": "cancel", "batch_id": target["batch"]["batch_id"],
                    "expected_version": data["expected_version"]} if data["expected_version"] < version else None
                return self.error("BULK_CONFLICT", rejection=proof)
            for row in target["recipients"]["items"]:
                if row["status"] == "pending":
                    row.update(status="cancelled", status_label="Cancelado")
            target["batch"].update(version=version + 1, status="uncertain" if any(row["status"] == "uncertain" for row in target["recipients"]["items"]) else "cancelled", actions=[], finished_at=now())
            target["batch"]["status_label"] = LABELS[target["batch"]["status"]]
            update_progress(target)
            op.update(kind="cancel", status="succeeded", batch_id=target["batch"]["batch_id"], result={"batch": deepcopy(target["batch"])})
        session["bm_requests"][key] = {"path": path, "data": deepcopy(data), "operation": op}
        return self.respond_operation(session, op, "action" if model is BulkAction else "review")

    def respond_operation(self, session, operation, phase):
        if session["bm_next"] == "lost_" + phase:
            session["bm_next"] = None
            return self.error("SYNTHETIC_LOST_RESPONSE", 503)
        if session["bm_next"] == "marker_confirm" and phase == "confirm":
            session["bm_next"] = None
            return self.error("BULK_CONFLICT", rejection={"reason": "stale_version", "idempotency_key": operation["idempotency_key"],
                "kind": "cancel", "batch_id": operation["batch_id"], "expected_version": 1})
        return self.body(public_operation(operation))
