"""Loopback schedule ledger using production request schemas and time policy.

The fixture serves shipped Orion assets through its real HTTP wrapper. It has
only synthetic contacts and records, and never invokes a database or transport.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.subscription_schedule_policy import estimate_completion, start_at
from app.subscription_schedule_schemas import (
    AudienceRequest, ConfirmPending, ConfirmSchedule, EditSchedule,
    ReviewPending, ReviewSchedule, ScheduleAction,
)
from browser_subscription_chatbot import Handler as ChatbotHandler, ROOT, base, now
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_scheduled_messages_contract.json").read_text())
SCHEDULES_BASE = base.BASE + "/subscription-whatsapp/scheduled-messages"
SCHEDULE_ID = CONTRACT["detail_response"]["schedule"]["schedule_id"]
RUN_ID = CONTRACT["detail_response"]["schedule"]["run_id"]


def stamp(value):
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def parsed(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def page_result(items, query=None):
    page = max(1, int((query or {}).get("page", ["1"])[0]))
    return {"items": deepcopy(items[(page - 1) * 30:page * 30]), "total": len(items),
            "page": page, "page_size": 30, "pages": max(1, (len(items) + 29) // 30),
            "timezone": "America/Sao_Paulo", "generated_at": now()}


def counts(items):
    return {"all": len(items), **{key: sum(row["bucket"] == key for row in items)
            for key in ("active", "past_due", "history")},
            **{key: sum(row["active_filter"] == key for row in items)
            for key in ("attention", "queued", "processing", "upcoming")}}


def selected(session, definition):
    direct = [row["key"] for row in session["sc_contacts"]] if definition["all_contacts"] else definition["contact_keys"]
    grouped = [key for group in definition["group_ids"] for key in session["sc_members"].get(group, [])]
    unique = list(dict.fromkeys([*direct, *grouped]))
    contacts = {row["key"]: row for row in session["sc_contacts"]}
    blocked = sum(not contacts[key]["available"] for key in unique)
    return unique, {"total": len(unique), "direct": len(direct), "from_groups": len(grouped),
                    "duplicates": len(direct) + len(grouped) - len(unique),
                    "blocked": blocked, "eligible": len(unique) - blocked}


def audience(session, definition):
    _, value = selected(session, definition)
    zone = session["sc_options"]["timezone"]
    start = start_at({**definition, "timezone": zone}) if definition.get("start_date") else None
    value.update(timezone=zone, generated_at=now(), start_at=stamp(start),
                 estimated_end_at=stamp(estimate_completion(start, value["total"], definition.get("interval_seconds", 60))))
    return value


def message(session, definition):
    template = next((row for row in session["sc_options"]["templates"] if row["id"] == definition["template_id"]), None)
    kind = template["kind"] if template else "text"
    return {"type": kind, "text": definition["text"], "caption": None, "followup": None,
            "template_name": template["name"] if template else None,
            "parts": [{"type": kind, "text": definition["text"]}]}


class Handler(ChatbotHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                session = base._sessions[cookie]
                contacts = deepcopy(CONTRACT["contacts_response"]["items"])
                groups = CONTRACT["options_response"]["groups"]
                session.update(sc_calls=[], sc_requests={}, sc_confirmations=[], sc_releases=[], sc_effects=[],
                    sc_next=None, sc_revoked=False, sc_options=deepcopy(CONTRACT["options_response"]),
                    sc_contacts=contacts, sc_runs={} if query.get("fixtureEmpty") == ["1"] else {
                        RUN_ID: deepcopy(CONTRACT["detail_response"])},
                    sc_members={groups[0]["id"]: [row["key"] for row in contacts[:2]], groups[1]["id"]: [contacts[1]["key"]]})
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            profile = session["workspace_profile"]
            plan["workspace_profile"] = profile
            features = ["account.manage", "business.setup", "devices.manage"]
            if profile == "subscription_services" and not session["chatbot_readonly"]:
                features.extend(["chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage", "campaigns.manage"])
                if not session["sc_revoked"]:
                    features.append("scheduled_messages.manage")
            plan["features"] = features if profile == "subscription_services" else plan["features"]
            plan["permissions"] = {key: key in features if profile == "subscription_services" else
                (False if key == "scheduled_messages.manage" else enabled) for key, enabled in
                {**plan["permissions"], **{key: False for key in features}, "scheduled_messages.manage": False}.items()}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    @staticmethod
    def allowed(session):
        return session and session["workspace_profile"] == "subscription_services" and not session["chatbot_readonly"] and not session["sc_revoked"]

    def error(self, code, status=409, rejection=None):
        detail = {"code": code, "message": "A situação sintética mudou. Consulte o mesmo pedido."}
        if rejection is not None:
            detail["rejection"] = rejection
        return self.body({"detail": detail}, status)

    @staticmethod
    def public_operation(value):
        value = deepcopy(value)
        if value["status"] == "prepared" and parsed(value["expires_at"]) <= datetime.now(timezone.utc):
            value["status"] = "expired"
        if value["status"] != "prepared":
            value["preview"] = None
        return value

    @staticmethod
    def find_run(session, identity, run=None):
        return next((value for key, value in session["sc_runs"].items()
                     if value["schedule"]["schedule_id"] == identity and (run is None or key == run)), None)

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/schedules-report":
            return self.body({"calls": session["sc_calls"], "operations": list(session["sc_requests"].values()),
                "runs": list(session["sc_runs"].values()), "confirmations": session["sc_confirmations"],
                "releases": session["sc_releases"], "effects": session["sc_effects"], "unexpected": session["unexpected"]})
        if session and session["workspace_profile"] == "subscription_services" and not session["chatbot_readonly"]:
            if path == base.BASE + "/subscription-whatsapp/sent-messages":
                return self.body({"items": [], "counts": {"today": 0, "last_30_days": 0, "scope": "message_attempts"}})
            if path == base.BASE + "/subscription-whatsapp/campaigns":
                return self.body({"items": [], "total": 0})
        if not path.startswith(SCHEDULES_BASE):
            return super().do_GET()
        if session:
            session["sc_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(SCHEDULES_BASE):]
        if suffix == "/options":
            return self.body(deepcopy(session["sc_options"]))
        if suffix == "/contacts":
            needle = query.get("q", [""])[0].casefold()
            return self.body(page_result([row for row in session["sc_contacts"]
                if needle in (row["name"] + " " + row["phone_e164"]).casefold()], query))
        if suffix.startswith("/requests/"):
            saved = session["sc_requests"].get(suffix.rsplit("/", 1)[-1])
            return self.body({**self.public_operation(saved["operation"]), "replayed": True}) if saved else self.error("SCHEDULE_NOT_FOUND", 404)
        rows = [value["schedule"] for value in session["sc_runs"].values()]
        if suffix in {"", "/"}:
            tab, status, needle = (query.get(key, [default])[0] for key, default in (("tab", "active"), ("status", ""), ("q", "")))
            matching = [row for row in rows if needle.casefold() in row["title"].casefold()]
            filtered = [row for row in matching if (tab == "all" or row["bucket"] == tab)
                        and (not status or row["active_filter"] == status)]
            return self.body({**page_result(filtered, query), "counts": counts(matching)})
        identity = suffix.split("/")[1]
        if suffix.endswith("/runs"):
            return self.body(page_result([row for row in rows if row["schedule_id"] == identity], query))
        detail = self.find_run(session, identity, query.get("run_id", [None])[0])
        if not detail:
            return self.error("SCHEDULE_NOT_FOUND", 404)
        if suffix.endswith("/recipients"):
            needle, status = query.get("q", [""])[0].casefold(), query.get("status", [""])[0]
            return self.body(page_result([row for row in detail["recipients"]["items"]
                if (not status or row["status"] == status) and needle in (row["name"] + " " + row["phone"]).casefold()], query))
        return self.body(deepcopy(detail))

    def control(self, session, mode):
        if mode == "revoke":
            session["sc_revoked"] = True
        elif mode == "expire_review":
            for entry in session["sc_requests"].values():
                if entry["operation"]["status"] == "prepared":
                    expired = stamp(datetime.now(timezone.utc) - timedelta(seconds=1))
                    entry["operation"]["expires_at"] = expired
                    entry["operation"]["preview"]["expires_at"] = expired
        elif mode == "many":
            for index in range(62):
                detail = deepcopy(CONTRACT["detail_response"])
                detail["schedule"].update(schedule_id=str(uuid4()), run_id=str(uuid4()), title=f"Página sintética {index:02}")
                session["sc_runs"][detail["schedule"]["run_id"]] = detail
        elif mode == "due":
            for status in ("pending", "running", "uncertain"):
                detail = deepcopy(CONTRACT["detail_response"])
                detail["schedule"].update(schedule_id=str(uuid4()), run_id=str(uuid4()),
                    title="Ocorrência sintética " + status, scheduled_at="2020-01-10T17:00:00Z", status=status,
                    bucket="past_due" if status == "pending" else "history" if status == "uncertain" else "active",
                    active_filter=None if status != "running" else "processing", status_label=status)
                session["sc_runs"][detail["schedule"]["run_id"]] = detail
        elif mode == "progress":
            detail = session["sc_runs"][RUN_ID]
            detail["schedule"].update(status="running", status_label="Enviando", active_filter="processing", actions=["cancel_run", "cancel_series"],
                progress={"total": 6, "processed": 4, "sent": 1, "pending": 1, "failed": 1, "uncertain": 1, "cancelled": 1, "percent": 66.67})
            rows = []
            for index, status in enumerate(("pending", "running", "sent", "failed", "uncertain", "cancelled"), 1):
                row = deepcopy(CONTRACT["detail_response"]["recipients"]["items"][0])
                row.update(id=str(uuid4()), ordinal=index, name="Contato sintético " + status,
                    status=status, status_label=status, error_code="SYNTHETIC_UNCERTAIN" if status == "uncertain" else None,
                    parts=[{"id": str(uuid4()), "type": "text", "status": "uncertain", "error_code": "SYNTHETIC_UNCERTAIN"}] if status == "uncertain" else [])
                rows.append(row)
            detail["recipients"] = page_result(rows)
        elif mode in {"lost_review", "lost_confirm", "lost_action", "lost_pending_review", "lost_pending_confirm",
                      "missing_review", "invalid_review", "stale_review", "after_admission_review", "marker_confirm"}:
            session["sc_next"] = mode
        else:
            return self.reject(session, "Unknown synthetic schedules control")
        return self.body({"ok": True})

    def version_error(self, item, kind, data):
        run_version = data.get("expected_run_version")
        if data["expected_version"] == item["version"] and (run_version is None or run_version == item["run_version"]):
            return None
        old = data["expected_version"] <= item["version"] and (run_version is None or run_version <= item["run_version"])
        proof = {"reason": "stale_version", "idempotency_key": data["idempotency_key"], "kind": kind,
                 "schedule_id": item["schedule_id"], "run_id": None if kind == "cancel_series" else item["run_id"],
                 "expected_version": data["expected_version"], "expected_run_version": run_version} if old else None
        return {"code": "SCHEDULE_CONFLICT", "rejection": proof}

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/schedules-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            return self.control(session, data["mode"])
        if not path.startswith(SCHEDULES_BASE):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(SCHEDULES_BASE):]
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.command != "POST" or not 0 < size <= 2_097_152:
                raise ValueError("Bounded POST required")
            raw = json.loads(self.rfile.read(size))
            session["sc_calls"].append({"method": self.command, "path": path, "data": deepcopy(raw)})
            model = ({"/audience": AudienceRequest, "/review": ReviewSchedule, "/confirm": ConfirmSchedule,
                      "/pending/review": ReviewPending, "/pending/confirm": ConfirmPending}.get(suffix)
                     or (EditSchedule if suffix.endswith("/review") else ScheduleAction if suffix.endswith("/actions") else None))
            if model is None:
                return self.reject(session, "Unexpected schedule mutation")
            data = model.model_validate(raw).model_dump(mode="json")
            if model is AudienceRequest:
                return self.body(audience(session, data))
        except (ValueError, TypeError, KeyError):
            return self.error("INVALID_INPUT", 422)
        key, prior = data["idempotency_key"], session["sc_requests"].get(data["idempotency_key"])
        if suffix in {"/confirm", "/pending/confirm"}:
            if not prior:
                return self.error("SCHEDULE_NOT_FOUND", 404)
            op = prior["operation"]
            pending = suffix == "/pending/confirm"
            if (op["kind"] == "pending_review") != pending or op["preview"]["review_version"] != data["review_version"]:
                return self.error("SCHEDULE_CONFLICT")
            if op["status"] == "succeeded":
                return self.body({**self.public_operation(op), "replayed": True})
            if parsed(op["expires_at"]) <= datetime.now(timezone.utc):
                return self.error("SCHEDULE_REVIEW_EXPIRED", 410)
            if pending:
                released = [{"schedule_id": row["schedule_id"], "run_id": row["run_id"]} for row in op["preview"]["runs"]]
                if not released:
                    return self.error("SCHEDULE_NO_ELIGIBLE", 422)
                for row in released:
                    detail = session["sc_runs"][row["run_id"]]
                    detail["released"] = True
                    detail["schedule"].update(run_version=detail["schedule"]["run_version"] + 1, bucket="active", active_filter="attention", status_label="Necessita de atenção")
                session["sc_releases"].append(deepcopy(released))
                op.update(status="succeeded", result={"released": released, "total": len(released)})
            else:
                definition = prior["data"]["schedule"]
                old = self.find_run(session, op["schedule_id"], op["run_id"]) if op["kind"] == "review_edit" else None
                if old:
                    conflict = self.version_error(old["schedule"], "review_edit", prior["data"])
                    if conflict:
                        return self.error("SCHEDULE_CONFLICT")
                item = deepcopy(CONTRACT["detail_response"]["schedule"])
                a = audience(session, definition)
                item.update(schedule_id=old["schedule"]["schedule_id"] if old else str(uuid4()),
                    run_id=old["schedule"]["run_id"] if old else str(uuid4()), title=definition["title"],
                    version=old["schedule"]["version"] + 1 if old else 1, run_version=old["schedule"]["run_version"] + 1 if old else 1,
                    scheduled_at=a["start_at"], estimated_end_at=a["estimated_end_at"], local_date=definition["start_date"],
                    local_time=definition["start_time"], interval_seconds=definition["interval_seconds"],
                    repeat_every_30_days=definition["repeat_every_30_days"],
                    progress={"total": a["total"], "processed": 0, "sent": 0, "pending": a["total"], "failed": 0, "uncertain": 0, "cancelled": 0, "percent": 0})
                selected_keys, _ = selected(session, definition)
                rows = []
                for index, contact_key in enumerate(selected_keys, 1):
                    contact = next(row for row in session["sc_contacts"] if row["key"] == contact_key)
                    rows.append({"id": str(uuid4()), "ordinal": index, "name": contact["name"], "phone": contact["phone_e164"],
                                 "status": "pending", "status_label": "Pendente", "error_code": None, "parts": []})
                session["sc_runs"][item["run_id"]] = {"schedule": item, "definition": {**deepcopy(definition), "timezone": a["timezone"]},
                    "message_preview": message(session, definition), "groups": [deepcopy(group) for group in session["sc_options"]["groups"] if group["id"] in definition["group_ids"]], "recipients": page_result(rows)}
                op.update(status="succeeded", schedule_id=item["schedule_id"], run_id=item["run_id"], result={"schedule": deepcopy(item)})
                session["sc_confirmations"].append({"idempotency_key": key, "schedule_id": item["schedule_id"], "run_id": item["run_id"]})
            return self.respond_operation(session, op, "pending_confirm" if pending else "confirm")
        if prior:
            if prior["path"] != path or prior["data"] != data:
                return self.error("SCHEDULE_IDEMPOTENCY_CONFLICT")
            return self.body({**self.public_operation(prior["operation"]), "replayed": True})
        op = {"request_id": str(uuid4()), "idempotency_key": key, "kind": "review_create", "status": "prepared",
              "schedule_id": None, "run_id": None, "replayed": False, "created_at": now(), "expires_at": None,
              "preview": None, "result": None}
        if model is ReviewPending:
            rows = [deepcopy(value["schedule"]) for value in session["sc_runs"].values()
                    if not value.get("released") and value["schedule"]["status"] == "pending"
                    and value["schedule"]["series_status"] == "active" and value["schedule"]["scheduled_at"]
                    and parsed(value["schedule"]["scheduled_at"]) <= datetime.now(timezone.utc)]
            op.update(kind="pending_review", preview={"total": len(rows), "runs": rows})
        else:
            target = self.find_run(session, suffix.split("/")[1], data.get("run_id")) if model in {EditSchedule, ScheduleAction} else None
            kind = data["action"] if model is ScheduleAction else "review_edit" if model is EditSchedule else "review_create"
            if model in {EditSchedule, ScheduleAction}:
                if target is None:
                    return self.error("SCHEDULE_NOT_FOUND", 404)
                if session["sc_next"] == "stale_review" and model is EditSchedule:
                    target["schedule"]["version"] += 1
                    session["sc_next"] = None
                conflict = self.version_error(target["schedule"], kind, data)
                if conflict:
                    return self.error(**conflict)
                op.update(schedule_id=target["schedule"]["schedule_id"], run_id=target["schedule"]["run_id"] if kind != "cancel_series" else None)
            op["kind"] = kind
            if model is ScheduleAction:
                selected_runs = [value for value in session["sc_runs"].values() if value["schedule"]["schedule_id"] == op["schedule_id"]] if kind == "cancel_series" else [target]
                for value in selected_runs:
                    row = value["schedule"]
                    row["version"] += 1
                    if kind == "cancel_series":
                        row["series_status"] = "cancelled"
                    if row["status"] == "pending":
                        row.update(status="cancelled", status_label="Cancelada", bucket="history", active_filter=None, actions=[], run_version=row["run_version"] + 1)
                op.update(status="succeeded", result={"schedule_id": op["schedule_id"], "cancelled": True} if kind == "cancel_series" else {"schedule": deepcopy(target["schedule"])})
            else:
                if session["sc_next"] in {"missing_review", "invalid_review"}:
                    mode, session["sc_next"] = session["sc_next"], None
                    return self.error("SYNTHETIC_REJECTED", 422 if mode == "invalid_review" else 503)
                definition = data["schedule"]
                try:
                    a = audience(session, definition)
                except ValueError:
                    return self.error("INVALID_LOCAL_TIME", 422)
                op["preview"] = {"title": definition["title"], "definition": {**deepcopy(definition), "timezone": a["timezone"]},
                    **{key: a[key] for key in ("total", "eligible", "blocked", "duplicates", "direct", "from_groups")},
                    "device": deepcopy(next(row for row in session["sc_options"]["devices"] if row["id"] == definition["device_id"])),
                    "groups": [deepcopy(group) for group in session["sc_options"]["groups"] if group["id"] in definition["group_ids"]],
                    "message_preview": message(session, definition), "schedule": {"start_at": a["start_at"], "estimated_end_at": a["estimated_end_at"],
                        "timezone": a["timezone"], "repeat_every_30_days": definition["repeat_every_30_days"]}}
        if op["status"] == "prepared":
            op["expires_at"] = stamp(datetime.now(timezone.utc) + timedelta(seconds=120))
            op["preview"].update(review_version=sha256(str(uuid4()).encode()).hexdigest(), expires_at=op["expires_at"])
        session["sc_requests"][key] = {"path": path, "data": deepcopy(data), "operation": deepcopy(op)}
        if session["sc_next"] == "after_admission_review" and op["kind"].startswith("review_"):
            session["sc_next"] = None
            return self.error("SCHEDULE_CONFLICT")
        return self.respond_operation(session, op, "pending_review" if op["kind"] == "pending_review" else "action" if model is ScheduleAction else "review")

    def respond_operation(self, session, operation, phase):
        if session["sc_next"] == "lost_" + phase:
            session["sc_next"] = None
            return self.error("SYNTHETIC_LOST_RESPONSE", 503)
        if session["sc_next"] == "marker_confirm" and phase == "confirm":
            session["sc_next"] = None
            return self.error("SCHEDULE_CONFLICT", rejection={"reason": "stale_version", "idempotency_key": operation["idempotency_key"],
                "kind": "review_edit", "schedule_id": operation["schedule_id"], "run_id": operation["run_id"], "expected_version": 1, "expected_run_version": 1})
        return self.body(self.public_operation(operation))
