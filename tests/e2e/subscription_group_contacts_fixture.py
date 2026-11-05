"""Synthetic group membership and local import ledger for real panel assets.

The production input schema validates imports. No database, socket, provider,
message, real contact or group membership is created by this loopback fixture.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.subscription_group_contacts_schemas import ImportContacts, MAX_BODY
from browser_subscription_chatbot import Handler as ChatbotHandler, ROOT, base, now
from scripts.plans_preview import Handler as PlansHandler

CONTRACT_PATH = ROOT / "tests/fixtures/subscription_group_contacts_contract.json"
GROUP_CONTACTS_BASE = base.BASE + "/subscription-whatsapp/group-contacts"
FEATURE = "group_contacts.manage"


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
                    gc_calls=[], gc_requests={}, gc_previews={}, gc_imports=[], gc_effects=[],
                    gc_options=deepcopy(sample["options"]), gc_groups=deepcopy(sample["groups"]),
                    gc_members=deepcopy(sample["members"]), gc_next=None, gc_revoked=False,
                    gc_recovery_failures=0,
                    gc_grant=query.get("fixtureGroupContacts") != ["0"], gc_plan=None,
                    gc_empty=query.get("fixtureEmpty") == ["1"],
                    company_status="paused" if query.get("fixtureInactive") == ["1"] else "active",
                )
                session["gc_local_members"] = {
                    group["id"]: {sample["members"]["members"][0]["phone"]: "Nome local preservado",
                                  "+5567999999909": "Contato local fora do grupo"}
                    for group in sample["options"]["groups"]
                }
                session["gc_options"]["generated_at"] = now()
                for group in session["gc_options"]["groups"]:
                    group["member_count"] = len(session["gc_local_members"][group["id"]])
                if query.get("fixtureDisconnected") == ["1"]:
                    for device in session["gc_options"]["devices"]:
                        device.update(available=False, status="disconnected", blocked_reason="Conecte o dispositivo para consultar grupos.")
        session = self.session()
        if session and url.path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**row, "workspace_profile": session["workspace_profile"],
                      "name": "Empresa sintética de assinaturas", "status": session["company_status"]} for row in value]
            return PlansHandler.body(self, value, status, mime, cookie)
        if session and url.path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": session["workspace_profile"]}
            features = {"account.manage", "business.setup"}
            if session["gc_grant"] and not session["gc_revoked"]:
                # Intentionally stale for standard/inactive/reader cases: the
                # shell must enforce its own independent context boundary.
                features.add(FEATURE)
            plan["features"] = sorted(features)
            plan["permissions"] = {key: key in features for key in {*plan["permissions"], FEATURE}}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            session["gc_plan"] = plan
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    @staticmethod
    def allowed(session):
        return (session and session["workspace_profile"] == "subscription_services"
                and session["company_status"] == "active" and session["role"] == "owner"
                and session["gc_grant"] and not session["gc_revoked"])

    def error(self, code, message, status=409):
        return self.body({"detail": {"code": code, "message": message}}, status)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/group-contacts-report":
            return self.body({
                "calls": session["gc_calls"], "imports": session["gc_imports"],
                "operations": list(session["gc_requests"].values()),
                "groups": session["gc_options"]["groups"], "local_members": session["gc_local_members"],
                "effects": session["gc_effects"], "unexpected": session["unexpected"],
                "effective_plan": session["gc_plan"],
                "state": {"profile": session["workspace_profile"], "role": session["role"],
                          "active": session["company_status"] == "active", "granted": session["gc_grant"]},
            })
        if not path.startswith(GROUP_CONTACTS_BASE):
            return super().do_GET()
        if session:
            session["gc_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", "Acesso indisponível.", 403)
        suffix = path[len(GROUP_CONTACTS_BASE):]
        if suffix == "/options":
            return self.body(deepcopy(session["gc_options"]))
        if suffix.startswith("/requests/"):
            prior = session["gc_requests"].get(suffix.rsplit("/", 1)[-1])
            if prior:
                if session["gc_recovery_failures"]:
                    session["gc_recovery_failures"] -= 1
                    return self.error("TEMPORARY_FAILURE", "A consulta do resultado está indisponível.", 503)
                return self.body({**deepcopy(prior["operation"]), "replayed": True})
            return self.error("GROUP_CONTACTS_NOT_FOUND", "Salvamento não encontrado.", 404)
        device_id = query.get("device_id", [""])[0]
        device = next((item for item in session["gc_options"]["devices"] if item["id"] == device_id), None)
        if not device or not device["available"]:
            return self.error("GROUP_CONTACTS_DEVICE_UNAVAILABLE", "Dispositivo indisponível.")
        if suffix == "/groups":
            result = deepcopy(session["gc_groups"])
            result["device"] = deepcopy(device)
            result["generated_at"] = now()
            if session["gc_empty"]:
                result["items"] = []
            return self.body(result)
        if suffix == "/members":
            group_id = query.get("group_id", [""])[0]
            group = next((item for item in session["gc_groups"]["items"] if item["id"] == group_id), None)
            if group is None:
                return self.error("GROUP_CONTACTS_NOT_FOUND", "Grupo não encontrado.", 404)
            result = deepcopy(session["gc_members"])
            result["device"], result["group"] = deepcopy(device), deepcopy(group)
            if group != session["gc_groups"]["items"][0]:
                result.update(members=[], total=0, unavailable_count=0, duplicate_count=0)
                result["group"]["member_count"] = 0
            token = "synthetic-preview-" + uuid4().hex + "x" * 128
            result.update(preview_token=token, expires_at=(datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat())
            session["gc_previews"][token] = deepcopy(result)
            return self.body(result)
        return self.reject(session, "Unexpected group contact read")

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/group-contacts-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = data["mode"]
            if mode == "expire":
                for preview in session["gc_previews"].values():
                    preview["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            elif mode == "revoke":
                session["gc_revoked"] = True
            elif mode == "lost_import":
                session["gc_next"] = mode
                session["gc_recovery_failures"] = 1
            else:
                return self.reject(session, "Unknown group contact fixture control")
            return self.body({"ok": True})
        if not path.startswith(GROUP_CONTACTS_BASE):
            return self.reject(session, "Only the synthetic local group import may mutate")
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", "Acesso indisponível.", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF is required")
        if self.command != "POST" or path != GROUP_CONTACTS_BASE + "/import":
            return self.reject(session, "Unexpected group contact mutation")
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError("Bounded payload required")
            raw = json.loads(self.rfile.read(size))
            data = ImportContacts.model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError):
            return self.error("GROUP_CONTACTS_INVALID", "Confira a seleção.", 422)
        session["gc_calls"].append({"method": "POST", "path": path, "data": deepcopy(data)})
        key, prior = data["idempotency_key"], session["gc_requests"].get(data["idempotency_key"])
        if prior:
            if prior["data"] != data:
                return self.error("GROUP_CONTACTS_IDEMPOTENCY_CONFLICT", "A chave pertence a outro pedido.")
            return self.body({**deepcopy(prior["operation"]), "replayed": True})
        preview = session["gc_previews"].get(data["preview_token"])
        if not preview or datetime.fromisoformat(preview["expires_at"]) <= datetime.now(timezone.utc):
            return self.error("GROUP_CONTACTS_PREVIEW_EXPIRED", "Carregue novamente os membros.", 410)
        members = {member["phone"]: member for member in preview["members"]}
        if any(phone not in members for phone in data["phones"]):
            return self.error("GROUP_CONTACTS_INVALID", "Seleção fora da prévia.", 422)
        target = data["target_group_id"]
        if target is None:
            target = str(uuid4())
            group = {"id": target, "name": data["new_group_name"], "version": 1,
                     "member_count": 0, "blocked_count": 0, "archived": False}
            session["gc_options"]["groups"].append(group)
            session["gc_local_members"][target] = {}
            kind = "group_create"
        else:
            group = next((item for item in session["gc_options"]["groups"] if item["id"] == target), None)
            if group is None or group["version"] != data["expected_version"]:
                return self.error("GROUP_CONTACTS_CONFLICT", "O grupo mudou. Atualize e tente novamente.")
            group["version"] += 1
            kind = "group_update"
        local = session["gc_local_members"][target]
        existing = sum(phone in local for phone in data["phones"])
        for phone in data["phones"]:
            local.setdefault(phone, members[phone]["name"] or phone)
        group["member_count"] = len(local)
        result = {"origin": "group_contacts", "group": deepcopy(group), "selected_count": len(data["phones"]),
                  "added_count": len(data["phones"]) - existing, "existing_count": existing, "restored_count": 0}
        operation = {"request_id": str(uuid4()), "idempotency_key": key, "kind": kind,
                     "status": "succeeded", "group_id": target, "campaign_id": None,
                     "result": result, "preview": None, "replayed": False, "created_at": now(), "expires_at": None}
        session["gc_requests"][key] = {"data": deepcopy(data), "operation": deepcopy(operation)}
        session["gc_imports"].append(deepcopy(result))
        if session["gc_next"] == "lost_import":
            session["gc_next"] = None
            return self.error("TEMPORARY_FAILURE", "A resposta do salvamento se perdeu.", 503)
        return self.body(operation)
