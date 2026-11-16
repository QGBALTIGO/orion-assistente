"""Synthetic intent/receipt service for the real group-add panel assets.

Production request schemas validate every input. No WhatsApp socket, provider,
invitation, message, real contact, database or participant mutation is used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.subscription_group_add_schemas import PreviewRequest, CreateBatch, MAX_BODY
from browser_subscription_chatbot import Handler as ChatbotHandler, ROOT, base, now
from scripts.plans_preview import Handler as PlansHandler

CONTRACT_PATH = ROOT / "tests/fixtures/subscription_group_add_contract.json"
GROUP_ADD_BASE = base.BASE + "/subscription-whatsapp/group-add"
FEATURE = "group_add.manage"
ITEM_STATES = ("pending", "running", "added", "already_member", "not_added", "unknown", "cancelled")


def contract():
    return json.loads(CONTRACT_PATH.read_text())


class Handler(ChatbotHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        url = urlsplit(self.path)
        if cookie and url.path in {"/", "/panel"}:
            query, sample = parse_qs(url.query), contract()
            with base._lock:
                session = base._sessions[cookie]
                session.update(
                    ga_calls=[], ga_batches={}, ga_requests={}, ga_previews={}, ga_creates=[],
                    ga_options=deepcopy(sample["options"]), ga_groups=deepcopy(sample["groups"]),
                    ga_revoked=False, ga_next=None, ga_outcome="completed", ga_recovery_failures=0,
                    ga_grant=query.get("fixtureGroupAdd") != ["0"], ga_plan=None,
                    ga_empty=query.get("fixtureEmpty") == ["1"], ga_effects=[],
                    company_status="paused" if query.get("fixtureInactive") == ["1"] else "active",
                )
                session["ga_options"]["generated_at"] = now()
                if query.get("fixtureDisconnected") == ["1"]:
                    session["ga_options"] = deepcopy(sample["options_disconnected"])
                    if query.get("fixtureLegacyAvailability") == ["1"]:
                        session["ga_options"]["devices"][0].update(available=True, blocked_reason=None)
        session = self.session()
        if session and url.path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**row, "workspace_profile": session["workspace_profile"],
                      "name": "Empresa sintética de assinaturas", "status": session["company_status"]} for row in value]
            return PlansHandler.body(self, value, status, mime, cookie)
        if session and url.path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": session["workspace_profile"]}
            features = {"account.manage", "business.setup"}
            if session["ga_grant"] and not session["ga_revoked"]:
                # Deliberately stale grant for non-owner/standard/paused cases:
                # the shipped shell must enforce those independent boundaries.
                features.add(FEATURE)
            plan["features"] = sorted(features)
            plan["permissions"] = {key: key in features for key in {*plan["permissions"], FEATURE}}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            session["ga_plan"] = plan
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    @staticmethod
    def allowed(session):
        return (session and session["workspace_profile"] == "subscription_services"
                and session["company_status"] == "active" and session["role"] == "owner"
                and session["ga_grant"] and not session["ga_revoked"])

    def error(self, code, message, status=409):
        return self.body({"detail": {"code": code, "message": message}}, status)

    @staticmethod
    def refresh_counts(value):
        members = value["members"]
        value["batch"]["total"] = len(members)
        value["batch"]["progress"] = {state: sum(m["status"] == state for m in members) for state in ITEM_STATES}
        return value

    def terminal(self, session, value):
        if value["batch"]["status"] not in {"pending", "running"}:
            return value
        outcome, stamp = session["ga_outcome"], now()
        value["batch"].update(status=outcome, started_at=value["batch"]["created_at"], finished_at=stamp,
            version=value["batch"]["version"] + 2,
            error_code="GROUP_ADD_OUTCOME_UNKNOWN" if outcome == "unknown" else None)
        for index, member in enumerate(value["members"]):
            state = "added"
            if outcome == "unknown":
                state = "unknown" if index == 0 else "cancelled"
            elif outcome == "partial" and index == len(value["members"]) - 1:
                state = "not_added"
            member.update(status=state, started_at=None if state == "cancelled" else value["batch"]["created_at"],
                finished_at=stamp, error_code={"unknown": "GROUP_ADD_OUTCOME_UNKNOWN",
                    "cancelled": "GROUP_ADD_STOPPED", "not_added": "GROUP_ADD_PROVIDER_REFUSED"}.get(state))
        return self.refresh_counts(value)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/group-add-report":
            return self.body({"calls": session["ga_calls"], "creates": session["ga_creates"],
                "batches": list(session["ga_batches"].values()), "effects": session["ga_effects"],
                "unexpected": session["unexpected"], "effective_plan": session["ga_plan"],
                "state": {"profile": session["workspace_profile"], "role": session["role"],
                    "active": session["company_status"] == "active", "granted": session["ga_grant"]}})
        if not path.startswith(GROUP_ADD_BASE):
            return super().do_GET()
        if session:
            session["ga_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", "Acesso indisponível.", 403)
        suffix = path[len(GROUP_ADD_BASE):]
        if suffix == "/options":
            return self.body(deepcopy(session["ga_options"]))
        if suffix == "/groups":
            identity = query.get("device_id", [""])[0]
            device = next((d for d in session["ga_options"]["devices"] if d["id"] == identity), None)
            if not device or device["status"] != "connected" or not device["available"]:
                return self.error("GROUP_ADD_DEVICE_UNAVAILABLE", "Conecte o dispositivo.")
            result = deepcopy(session["ga_groups"])
            result["device"] = deepcopy(device)
            if session["ga_empty"]:
                result.update(items=[], total=0)
            return self.body(result)
        if suffix.startswith("/requests/"):
            identity = session["ga_requests"].get(suffix.rsplit("/", 1)[-1])
            if session["ga_recovery_failures"]:
                session["ga_recovery_failures"] -= 1
                return self.error("TEMPORARY_FAILURE", "A consulta está temporariamente indisponível.", 503)
            value = session["ga_batches"].get(identity)
            if not value:
                return self.error("GROUP_ADD_NOT_FOUND", "Pedido não encontrado.", 404)
            return self.body({**deepcopy(self.terminal(session, value)), "replayed": True})
        if suffix.startswith("/batches/"):
            value = session["ga_batches"].get(suffix.rsplit("/", 1)[-1])
            if not value:
                return self.error("GROUP_ADD_NOT_FOUND", "Pedido não encontrado.", 404)
            return self.body(deepcopy(self.terminal(session, value)))
        return self.reject(session, "Unexpected group addition read")

    def make_preview(self, session, data):
        sample = contract()
        result = deepcopy(sample["preview_group" if data["source"] == "group" else "preview_contacts"])
        destination = next((g for g in session["ga_groups"]["items"] if g["id"] == data["destination_group_id"]), None)
        result["destination"] = None if destination is None else {
            "id": destination["id"], "name": destination["name"], "member_count": destination["member_count"],
            "is_admin": destination["can_add"], "blocked_reason": destination["blocked_reason"]}
        if destination is None:
            result["members"] = deepcopy(sample["preview_no_destination"]["members"])
        if data["source"] == "group":
            source = next((g for g in session["ga_groups"]["items"] if g["id"] == data["source_group_id"]), None)
            if source is None or source["id"] == data["destination_group_id"]:
                return None
            result["source_group"] = {k: source[k] for k in ("id", "name", "member_count")}
        result["filters"] = {key: data[key] for key in ("contact_group_id", "without_group", "search", "created_from", "created_to", "source_group_id")}
        if data["search"]:
            term = data["search"].casefold()
            result["members"] = [m for m in result["members"] if term in (m["name"] + " " + m["phone"]).casefold()]
        counts = {state: sum(m["status"] == state for m in result["members"]) for state in ("ready", "already_member", "blocked")}
        result["counts"] = {"total": len(result["members"]), **counts,
                            "unavailable": 1 if data["source"] == "group" else 0,
                            "duplicates": 0 if data["search"] else 1}
        result.update(generated_at=now(), preview_token=None, expires_at=None)
        if result["destination"] and result["destination"]["is_admin"] and counts["ready"]:
            result["preview_token"] = "synthetic-group-add-" + uuid4().hex + "x" * 100
            result["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
            session["ga_previews"][result["preview_token"]] = deepcopy(result)
        return result

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/group-add-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = data["mode"]
            if mode == "expire":
                for preview in session["ga_previews"].values():
                    preview["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            elif mode == "revoke":
                session["ga_revoked"] = True
            elif mode in {"completed", "partial", "unknown"}:
                session["ga_outcome"] = mode
            elif mode == "lost_batch":
                session["ga_next"], session["ga_recovery_failures"] = mode, 1
            else:
                return self.reject(session, "Unknown group addition fixture control")
            return self.body({"ok": True})
        if not path.startswith(GROUP_ADD_BASE):
            return self.reject(session, "Only the synthetic group addition intent may mutate")
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", "Acesso indisponível.", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF is required")
        if self.command != "POST" or path not in {GROUP_ADD_BASE + "/preview", GROUP_ADD_BASE + "/batches"}:
            return self.reject(session, "Unexpected group addition mutation")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError("Bounded payload required")
            raw = json.loads(self.rfile.read(size))
            schema = PreviewRequest if path.endswith("/preview") else CreateBatch
            data = schema.model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError):
            return self.error("GROUP_ADD_INVALID", "Confira a seleção e os filtros.", 422)
        session["ga_calls"].append({"method": "POST", "path": path, "data": deepcopy(data)})
        if path.endswith("/preview"):
            result = self.make_preview(session, data)
            return self.body(result) if result else self.error("GROUP_ADD_INVALID", "Escolha um grupo diferente do destino.", 422)
        key = data["idempotency_key"]
        if key in session["ga_requests"]:
            previous = next(v for v in session["ga_creates"] if v["idempotency_key"] == key)
            if previous != data:
                return self.error("GROUP_ADD_IDEMPOTENCY_CONFLICT", "A chave pertence a outro pedido.")
            return self.body({**deepcopy(session["ga_batches"][session["ga_requests"][key]]), "replayed": True})
        preview = session["ga_previews"].get(data["preview_token"])
        if not preview or datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc):
            return self.error("GROUP_ADD_PREVIEW_EXPIRED", "Carregue os contatos novamente.", 410)
        eligible = {m["phone"]: m for m in preview["members"] if m["status"] == "ready"}
        if not set(data["phones"]).issubset(eligible) or not preview["destination"]["is_admin"]:
            return self.error("GROUP_ADD_INVALID", "Seleção fora da prévia.", 422)
        value, identity = deepcopy(contract()["batch_created"]), str(uuid4())
        value["batch"].update(id=identity, idempotency_key=key, source=preview["source"], created_at=now(),
            device={k: preview["device"][k] for k in ("id", "name")},
            destination={k: preview["destination"][k] for k in ("id", "name")})
        value["members"] = [{"id": str(uuid4()), "ordinal": i + 1, "phone": phone,
            "name": eligible[phone]["name"], "status": "pending", "error_code": None,
            "started_at": None, "finished_at": None} for i, phone in enumerate(data["phones"])]
        self.refresh_counts(value)
        session["ga_batches"][identity] = value
        session["ga_requests"][key] = identity
        session["ga_creates"].append(deepcopy(data))
        if session["ga_next"] == "lost_batch":
            session["ga_next"] = None
            return self.error("TEMPORARY_FAILURE", "A resposta da confirmação se perdeu.", 503)
        return self.body(deepcopy(value))
