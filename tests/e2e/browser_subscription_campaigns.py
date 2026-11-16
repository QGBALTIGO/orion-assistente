"""Campaigns with shipped assets, actual HTTP wrapper and a loopback ledger.

All contacts, groups, campaign operations and scheduler projections are synthetic.
No provider, database, network target or real recipient is used. Viewport tests
do not claim physical-device or virtual-keyboard coverage.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

from playwright.sync_api import expect
import pytest

from app.subscription_campaign_schemas import (
    ArchiveGroup, CampaignAction, ConfirmCampaign, CreateCampaign, CreateGroup,
    GroupPermissions, ReviewCampaign, UpdateCampaign, UpdateGroup,
)
from app.subscription_campaign_policy import start_at
from browser_subscription_chatbot import (
    Handler as ChatbotHandler, ROOT, SIZES, assert_layout, base, browser, navigation, now,
)  # noqa: F401 - official Chrome and WebKit fixture.
from browser_subscription_chatbot_simulator import setup_page
from scripts.plans_preview import Handler as PlansHandler

CONTRACT = json.loads((ROOT / "tests/fixtures/subscription_campaigns_contract.json").read_text())
CAMPAIGNS_BASE = base.BASE + "/subscription-whatsapp/campaigns"
CAMPAIGN_ID = CONTRACT["detail_response"]["campaign"]["id"]
GROUP_ID = CONTRACT["group_response"]["group"]["id"]
ARTIFACTS = ROOT / "test-artifacts" / "subscription-campaigns"


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def page_result(items, query=None):
    query = query or {}
    page = max(1, int(query.get("page", ["1"])[0]))
    return {"items": deepcopy(items[(page - 1) * 30:page * 30]), "total": len(items),
            "page": page, "page_size": 30, "pages": max(1, (len(items) + 29) // 30),
            "generated_at": now(), "timezone": "America/Sao_Paulo"}


class Handler(ChatbotHandler):
    @staticmethod
    def public_operation(operation):
        value = deepcopy(operation)
        if value["status"] != "prepared":
            value["preview"] = None
        return value

    def body(self, value, status=200, mime="application/json", cookie=None):
        path = urlsplit(self.path).path
        if cookie and path in {"/", "/panel"}:
            query = parse_qs(urlsplit(self.path).query)
            with base._lock:
                session = base._sessions[cookie]
                session.update(cp_calls=[], cp_requests={}, cp_confirmations=[], cp_effects=[],
                    cp_next=None, cp_revoked=False, cp_recipients={},
                    cp_options=deepcopy(CONTRACT["options_response"]),
                    cp_campaigns={} if query.get("fixtureEmpty") == ["1"] else {
                        CAMPAIGN_ID: deepcopy(CONTRACT["detail_response"]["campaign"])},
                    cp_groups={GROUP_ID: deepcopy(CONTRACT["group_response"])})
        session = self.session()
        if session and path.endswith("/management/permissions") and isinstance(value, dict):
            plan = deepcopy(value["effective_plan"])
            profile = session["workspace_profile"]
            plan["workspace_profile"] = profile
            features = ["account.manage", "business.setup", "devices.manage"]
            if profile == "subscription_services" and not session["chatbot_readonly"]:
                features.extend(["chatbot.manage", "templates.manage", "sent_messages.manage", "diagnostics.manage"])
                if not session["cp_revoked"]:
                    features.append("campaigns.manage")
            plan["features"] = features if profile == "subscription_services" else plan["features"]
            plan["permissions"] = {key: key in features if profile == "subscription_services" else
                (False if key == "campaigns.manage" else enabled) for key, enabled in
                {**plan["permissions"], **{key: False for key in features}, "campaigns.manage": False}.items()}
            plan["revision"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        try:
            return super().body(value, status, mime, cookie)
        except (BrokenPipeError, ConnectionResetError):
            return None

    def allowed(self, session):
        return (session and session["workspace_profile"] == "subscription_services"
                and not session["chatbot_readonly"] and not session["cp_revoked"])

    def error(self, code, status=409, rejection=None):
        detail = {"code": code, "message": "A situação sintética mudou. Consulte o mesmo pedido."}
        if rejection is not None:
            detail["rejection"] = rejection
        return self.body({"detail": detail}, status)

    @staticmethod
    def summaries(session):
        return [deepcopy(value["group"]) for value in session["cp_groups"].values()
                if not value["group"]["archived"]]

    @staticmethod
    def project(session, campaign):
        value = deepcopy(campaign)
        value["devices"] = [{"id": d["id"], "name": d["name"]} for d in session["cp_options"]["devices"]
                            if d["id"] in value["device_ids"]]
        value["template_name"] = next((t["name"] for t in session["cp_options"]["templates"]
                                       if t["id"] == value["template_id"]), None)
        return value

    def do_GET(self):
        url, session = urlsplit(self.path), self.session()
        path, query = url.path, parse_qs(url.query)
        if session and path == "/__fixture/campaigns-report":
            return self.body({"calls": session["cp_calls"], "operations": list(session["cp_requests"].values()),
                "campaigns": list(session["cp_campaigns"].values()), "groups": list(session["cp_groups"].values()),
                "confirmations": session["cp_confirmations"], "effects": session["cp_effects"],
                "unexpected": session["unexpected"]})
        if session and path == base.BASE + "/subscription-whatsapp/sent-messages" and session["workspace_profile"] == "subscription_services":
            return self.body({"items": [], "counts": {"today": 0, "last_30_days": 0, "scope": "message_attempts"}})
        if not path.startswith(CAMPAIGNS_BASE):
            return super().do_GET()
        if session:
            session["cp_calls"].append({"method": "GET", "path": path, "query": query})
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        suffix = path[len(CAMPAIGNS_BASE):]
        if suffix == "/options":
            return self.body({**deepcopy(session["cp_options"]), "groups": self.summaries(session)})
        if suffix == "":
            items = [self.project(session, c) for c in session["cp_campaigns"].values() if not c["archived"]
                     and (not query.get("status") or c["status"] == query["status"][0])
                     and (not query.get("q") or query["q"][0].casefold() in c["title"].casefold())]
            return self.body(page_result(items, query))
        if suffix == "/groups":
            items = self.summaries(session)
            return self.body({"items": items, "total": len(items)})
        if suffix.startswith("/requests/"):
            saved = session["cp_requests"].get(suffix.rsplit("/", 1)[-1])
            if saved is None:
                return self.error("CAMPAIGN_NOT_FOUND", 404)
            operation = deepcopy(saved["operation"])
            operation["replayed"] = True
            if operation["status"] == "prepared" and datetime.fromisoformat(operation["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                operation["status"] = "expired"
            return self.body(self.public_operation(operation))
        if suffix.startswith("/groups/"):
            value = session["cp_groups"].get(suffix.split("/")[2])
            return self.body(deepcopy(value)) if value else self.error("CAMPAIGN_NOT_FOUND", 404)
        identity = suffix.split("/")[1]
        campaign = session["cp_campaigns"].get(identity)
        if campaign is None:
            return self.error("CAMPAIGN_NOT_FOUND", 404)
        if suffix.endswith("/recipients"):
            rows = session["cp_recipients"].get(identity, [])
            rows = [row for row in rows if (not query.get("status") or row["status"] == query["status"][0])
                    and (not query.get("q") or query["q"][0].casefold() in
                         (row["name"] + " " + row["phone"] + " " + (row["message"] or "")).casefold())]
            return self.body(page_result(rows, query))
        return self.body({"campaign": self.project(session, campaign), "progress": deepcopy(campaign["progress"]),
            "groups": [g for g in self.summaries(session) if g["id"] in campaign["group_ids"]],
            "recipients": page_result(session["cp_recipients"].get(identity, []))})

    def mutate(self):
        path, session = urlsplit(self.path).path, self.session()
        if session and path == "/__fixture/campaigns-control":
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            mode = data["mode"]
            if mode == "revoke":
                session["cp_revoked"] = True
            elif mode == "seed_progress":
                c = session["cp_campaigns"][CAMPAIGN_ID]
                c.update(status="running", status_label="Em andamento", actions=["pause", "archive"],
                         progress={"total": 5, "processed": 4, "sent": 1, "pending": 1, "failed": 1,
                                   "uncertain": 1, "cancelled": 1, "percent": 80})
                rows = deepcopy(list(CONTRACT["recipient_examples"].values()))
                rows[0]["message"] = "<img src=x onerror=alert(1)> Texto sintético"
                rows[2]["parts"] = [
                    {"index": 0, "effect_id": str(uuid4()), "type": "image", "status": "accepted",
                     "error_code": None, "finished_at": now()},
                    {"index": 1, "effect_id": str(uuid4()), "type": "text", "status": "not_sent",
                     "error_code": "TRANSPORT_UNAVAILABLE_BEFORE_SEND", "finished_at": now()},
                ]
                session["cp_recipients"][CAMPAIGN_ID] = rows
            elif mode == "many_campaigns":
                for index in range(64):
                    c = deepcopy(CONTRACT["detail_response"]["campaign"])
                    c.update(id=str(uuid4()), title=f"Página sintética {index:02}")
                    session["cp_campaigns"][c["id"]] = c
            elif mode == "expire_review":
                for saved in session["cp_requests"].values():
                    if saved["operation"]["status"] == "prepared":
                        value = stamp(datetime.now(timezone.utc) - timedelta(seconds=1))
                        saved["operation"]["expires_at"] = value
                        saved["operation"]["preview"]["expires_at"] = value
            elif mode in {"lost_create", "lost_review", "lost_confirm", "lost_action", "missing_create",
                          "stale_review", "after_admission_review", "marker_confirm"}:
                session["cp_next"] = mode
            else:
                return self.reject(session, "Unknown synthetic campaign control")
            return self.body({"ok": True})
        if not path.startswith(CAMPAIGNS_BASE):
            return super().mutate()
        if not self.allowed(session):
            return self.error("WORKSPACE_FEATURE_UNAVAILABLE", 403)
        if self.headers.get("X-Orion-CSRF") != session["csrf"]:
            return self.reject(session, "Synthetic CSRF required")
        suffix = path[len(CAMPAIGNS_BASE):]
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2_097_152:
                raise ValueError("Bounded body required")
            raw = json.loads(self.rfile.read(size))
            session["cp_calls"].append({"method": self.command, "path": path, "data": deepcopy(raw)})
            if suffix == "" and self.command == "POST":
                model, kind = CreateCampaign, "create"
            elif suffix == "/groups" and self.command == "POST":
                model, kind = CreateGroup, "group_create"
            elif suffix.startswith("/groups/"):
                model, kind = ((GroupPermissions, "group_permissions") if suffix.endswith("/permissions") else
                               (ArchiveGroup, "group_archive") if self.command == "DELETE" else (UpdateGroup, "group_update"))
            elif suffix.endswith("/review"):
                model, kind = ReviewCampaign, "review"
            elif suffix.endswith("/confirm"):
                model, kind = ConfirmCampaign, "review"
            elif suffix.endswith("/actions"):
                model, kind = CampaignAction, "action"
            elif self.command == "PATCH":
                model, kind = UpdateCampaign, "update"
            else:
                return self.reject(session, "Unexpected campaign mutation")
            data = model.model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError, KeyError):
            return self.body({"detail": {"code": "INVALID_INPUT", "message": "Dados sintéticos inválidos."}}, 422)
        key = data["idempotency_key"]
        prior = session["cp_requests"].get(key)
        confirmation = suffix.endswith("/confirm")
        if confirmation:
            if not prior or prior["operation"]["kind"] != "review" or prior["operation"]["campaign_id"] != suffix.split("/")[1]:
                return self.error("CAMPAIGN_NOT_FOUND", 404)
            op = prior["operation"]
            if op["preview"]["review_version"] != data["review_version"]:
                return self.error("CAMPAIGN_CONFLICT")
            if op["status"] != "succeeded":
                if datetime.fromisoformat(op["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    return self.error("CAMPAIGN_REVIEW_EXPIRED", 410)
                c = session["cp_campaigns"][op["campaign_id"]]
                c.update(version=c["version"] + 1, status="scheduled", status_label="Agendada",
                         actions=["pause", "edit", "archive"], progress=deepcopy(CONTRACT["scheduled_detail_response"]["progress"]))
                first = deepcopy(CONTRACT["recipient_examples"]["pending"])
                second = {**deepcopy(first), "id": str(uuid4()), "name": "Cliente sintético B", "phone": "+5567999999912"}
                cancelled = {**deepcopy(CONTRACT["recipient_examples"]["cancelled"]), "name": "Cliente sintético C", "phone": "+5567999999913"}
                session["cp_recipients"][c["id"]] = [first, second, cancelled]
                op.update(status="succeeded", result={"campaign": self.project(session, c), "progress": c["progress"]})
                session["cp_confirmations"].append({"key": key, "campaign_id": c["id"]})
            else:
                op = {**deepcopy(op), "replayed": True}
            if session["cp_next"] == "marker_confirm":
                session["cp_next"] = None
                return self.error("CAMPAIGN_CONFLICT", rejection={"reason": "stale_version", "idempotency_key": key,
                    "kind": "review", "campaign_id": op["campaign_id"], "group_id": None, "expected_version": 1})
            return self.respond_operation(session, op, "confirm", 202)
        if prior:
            if prior["path"] != path or prior["data"] != data:
                return self.error("CAMPAIGN_CONFLICT")
            return self.body({**self.public_operation(prior["operation"]), "replayed": True}, 201 if kind in {"create", "group_create", "review"} else 200)
        target = (session["cp_groups"].get(suffix.split("/")[2], {}).get("group") if kind.startswith("group_") and kind != "group_create"
                  else session["cp_campaigns"].get(suffix.split("/")[1]) if kind != "create" and not kind.startswith("group_") else None)
        if "expected_version" in data:
            if target is None:
                return self.error("CAMPAIGN_NOT_FOUND", 404)
            if session["cp_next"] == "stale_review" and kind == "review":
                session["cp_next"] = None
                target["version"] += 1
            if data["expected_version"] != target["version"]:
                proof = {"reason": "stale_version", "idempotency_key": key, "kind": kind,
                    "campaign_id": None if kind.startswith("group_") else target["id"],
                    "group_id": target["id"] if kind.startswith("group_") else None,
                    "expected_version": data["expected_version"]} if data["expected_version"] < target["version"] else None
                return self.error("CAMPAIGN_CONFLICT", rejection=proof)
        if kind == "create" and session["cp_next"] == "missing_create":
            session["cp_next"] = None
            return self.error("SYNTHETIC_LOST_RESPONSE", 503)
        op = {"request_id": str(uuid4()), "idempotency_key": key, "kind": kind, "status": "succeeded",
              "campaign_id": None, "group_id": None, "result": None, "preview": None,
              "replayed": False, "created_at": now(), "expires_at": None}
        if kind == "create":
            c = {**deepcopy(CONTRACT["detail_response"]["campaign"]), **data["campaign"], "id": str(uuid4()), "created_at": now(), "updated_at": now(), "start_at": stamp(start_at(data["campaign"]))}
            session["cp_campaigns"][c["id"]] = c
            op.update(campaign_id=c["id"], result={"campaign": self.project(session, c)})
        elif kind == "update":
            target.update(**data["campaign"], version=target["version"] + 1, updated_at=now(), start_at=stamp(start_at(data["campaign"])))
            op.update(campaign_id=target["id"], result={"campaign": self.project(session, target)})
        elif kind == "review":
            preview = deepcopy(CONTRACT["review_response"]["preview"])
            c = self.project(session, target)
            preview.update(campaign=c, review_version=sha256(str(uuid4()).encode()).hexdigest(),
                           expires_at=stamp(datetime.now(timezone.utc) + timedelta(seconds=120)),
                           groups=[g for g in self.summaries(session) if g["id"] in c["group_ids"]], devices=c["devices"])
            preview["schedule"].update({key: c[key] for key in ["start_at", "start_date", "start_time", "timezone", "window_start", "window_end", "weekdays", "interval_seconds"]})
            template = next((t for t in session["cp_options"]["templates"] if t["id"] == c["template_id"]), None)
            preview["message_preview"].update(type=template["kind"] if template else "text", text=c["text"],
                template_name=template["name"] if template else None,
                parts=[{"type": template["kind"] if template else "text", "text": c["text"]}])
            op.update(campaign_id=c["id"], status="prepared", preview=preview, expires_at=preview["expires_at"])
        elif kind == "action":
            verb = data["action"]
            status = {"pause": "paused", "resume": "scheduled", "edit": "draft", "archive": "cancelled"}[verb]
            target.update(version=target["version"] + 1, status=status, status_label=CONTRACT["status_examples"][status]["status_label"],
                          actions=deepcopy(CONTRACT["status_examples"][status]["actions"]))
            if verb == "archive":
                target["archived"] = True
            op.update(campaign_id=target["id"], result={"campaign": self.project(session, target)})
        elif kind == "group_create":
            group = {"id": str(uuid4()), "name": data["name"], "version": 1, "member_count": len(data["members"]), "blocked_count": 0, "archived": False}
            value = {"group": group, "members": [{**m, "phone": "+" + m["phone"], "blocked": False, "permission_version": 0, "blocked_reason": None} for m in data["members"]]}
            session["cp_groups"][group["id"]] = value
            op.update(group_id=group["id"], result=deepcopy(value))
        else:
            value = session["cp_groups"][target["id"]]
            if kind == "group_archive":
                target["archived"] = True
            elif kind == "group_update":
                target["name"] = data["name"]
                old = {m["phone"]: m for m in value["members"]}
                value["members"] = [{**old.get("+" + m["phone"], {"blocked": False, "permission_version": 0, "blocked_reason": None}),
                                     "name": m["name"], "phone": "+" + m["phone"]} for m in data["members"]]
            elif kind == "group_permissions":
                for contact in data["contacts"]:
                    member = next((m for m in value["members"] if m["phone"] == "+" + contact["phone"]), None)
                    if member is None or member["permission_version"] != contact["version"]:
                        return self.error("CAMPAIGN_PERMISSION_CONFLICT")
                    member.update(blocked=False, permission_version=member["permission_version"] + 1, blocked_reason=None)
            target.update(version=target["version"] + 1, member_count=len(value["members"]), blocked_count=sum(m["blocked"] for m in value["members"]))
            op.update(group_id=target["id"], result=deepcopy(value))
        session["cp_requests"][key] = {"path": path, "data": deepcopy(data), "operation": deepcopy(op)}
        if session["cp_next"] == "after_admission_review" and kind == "review":
            session["cp_next"] = None
            return self.error("CAMPAIGN_CONFLICT")
        return self.respond_operation(session, op, kind, 201 if kind in {"create", "group_create", "review"} else 200)

    def respond_operation(self, session, operation, kind, status):
        if session["cp_next"] == "lost_" + kind:
            session["cp_next"] = None
            return self.error("SYNTHETIC_LOST_RESPONSE", 503)
        return self.body(self.public_operation(operation), status)


@pytest.fixture(scope="module")
def fixture_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def action(page, name):
    return page.locator('[data-cp-action="' + name + '"]')


def field(page, name):
    return page.locator('[data-cp-field="' + name + '"]')


def report(page, fixture_url):
    value = page.request.get(fixture_url + "/__fixture/campaigns-report").json()
    assert not value["unexpected"], value["unexpected"]
    assert not value["effects"], "A public page never executes the synthetic scheduler or sends"
    return value


def control(page, fixture_url, mode):
    response = page.request.post(fixture_url + "/__fixture/campaigns-control", data={"mode": mode})
    assert response.ok, response.text()


def writes(value):
    return [call for call in value["calls"] if call["method"] != "GET"]


def open_campaigns(page, fixture_url, extra=""):
    page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=campanhas" + extra)
    expect(page.locator("#osSubscriptionCampaigns")).to_be_visible()
    expect(action(page, "new")).to_be_enabled()


def review(page, identity=CAMPAIGN_ID):
    page.locator('[data-cp-action="review"][data-cp-id="' + identity + '"]').click()
    expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "review")


def fill_campaign(page, template=False):
    action(page, "new").click()
    expect(field(page, "title")).to_be_visible()
    assert page.locator("[name=device_ids]:checked,[name=group_ids]:checked").count() == 0
    for name in ("title", "start_date", "start_time", "window_start", "window_end", "interval_seconds"):
        field(page, name).fill(str(CONTRACT["campaign_input"][name]))
    action(page, "devices-all").click()
    action(page, "groups-all").click()
    if template:
        field(page, "message_type").select_option("template")
        expect(field(page, "template_id").locator("option")).to_have_count(9)
        field(page, "template_id").select_option(CONTRACT["options_response"]["templates"][1]["id"])
    else:
        field(page, "text").fill(CONTRACT["campaign_input"]["text"])


def assert_recovery(page, key):
    saved = page.evaluate("history.state.orionCampaignRecovery")
    assert set(saved) == {"tenant_id", "account_scope", "idempotency_key", "kind", "campaign_id", "group_id", "action"}
    assert saved["idempotency_key"] == key
    serialized = json.dumps(saved)
    assert all(secret not in serialized for secret in ("556799", "Cliente sintético", "text", "members", "evidence"))
    storage = page.evaluate("({local:{...localStorage},session:{...sessionStorage}})")
    assert all("Campaign" not in name and "campaign" not in name for values in storage.values() for name in values)


def reload_same_session(page):
    page.evaluate("history.replaceState(history.state, '', '/panel?aba=campanhas')")
    page.reload()
    expect(page.locator("#osSubscriptionCampaigns")).to_be_visible()


def assert_review_geometry(page, size, label):
    width, height = size
    dialog = page.locator("[data-cp-dialog]")
    body = dialog.locator(".os-cp-dialog-body")
    assert_layout(page, width)
    boxes = dialog.evaluate("""node => {
      const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height};};
      return {dialog:rect(node),body:rect(node.querySelector('.os-cp-dialog-body')),footer:rect(node.querySelector('.os-cp-dialog-footer'))};
    }""")
    assert boxes["dialog"]["top"] >= -1 and boxes["dialog"]["bottom"] <= height + 1, boxes
    assert boxes["body"]["height"] >= 200, boxes
    assert boxes["footer"]["bottom"] <= height + 1, boxes
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    body.evaluate("node=>{node.scrollTop=0}")
    page.screenshot(path=str(ARTIFACTS / (label + "-review-schedule.png")), full_page=True, animations="disabled")
    for selector in [".os-cp-review-counts", ".os-cp-facts", "[data-cp-preview]", ".os-cp-attestation"]:
        target = dialog.locator(selector)
        target.scroll_into_view_if_needed()
        metrics = target.evaluate("""node=>{const t=node.getBoundingClientRect(),b=node.closest('.os-cp-dialog-body').getBoundingClientRect();return {top:t.top,bottom:t.bottom,bodyTop:b.top,bodyBottom:b.bottom,height:t.height,bodyHeight:b.height};}""")
        assert metrics["height"] > 0 and metrics["bottom"] > metrics["bodyTop"] and metrics["top"] < metrics["bodyBottom"], metrics
        if metrics["height"] <= metrics["bodyHeight"] - 1:
            assert metrics["top"] >= metrics["bodyTop"] - 1 and metrics["bottom"] <= metrics["bodyBottom"] + 1, metrics
        if selector == "[data-cp-preview]":
            page.screenshot(path=str(ARTIFACTS / (label + "-review-content.png")), full_page=True, animations="disabled")
    expect(dialog.locator('[data-cp-action="confirm"]')).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / (label + "-review-confirmation.png")), full_page=True, animations="disabled")


@pytest.mark.parametrize("size", SIZES, ids=lambda size: f"{size[0]}x{size[1]}")
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_campaign_list_complete_editor_review_confirmation_and_true_progress(browser, fixture_url, size, theme):
    context, page, external, errors = setup_page(browser, fixture_url, size, theme)
    label = f"{browser[0]}-{size[0]}x{size[1]}-{theme}"
    try:
        open_campaigns(page, fixture_url)
        assert not writes(report(page, fixture_url))
        navigation(page)
        expect(page.locator('#osNav [data-os-route="campanhas"]')).to_be_visible()
        if page.locator("#osMenu").is_visible() and page.locator("#osMenu").get_attribute("aria-expanded") == "true":
            page.locator("#osCollapse").click()
            expect(page.locator("#osSidebar")).not_to_be_visible()
        page.evaluate("document.fonts.ready")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-list.png")), full_page=True, animations="disabled")
        fill_campaign(page, template=True)
        for name in ["start_date", "start_time", "timezone", "window_start", "window_end", "interval_seconds"]:
            expect(field(page, name)).to_have_value(str(CONTRACT["campaign_input"][name]))
        page.locator('[data-cp-days="weekend"]').click()
        assert page.locator("[name=weekdays]:checked").count() == 2
        page.locator('[data-cp-days="weekdays"]').click()
        assert page.locator("[name=weekdays]:checked").count() == 5
        assert not writes(report(page, fixture_url))
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-editor.png")), full_page=True, animations="disabled")
        action(page, "save").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        saved = report(page, fixture_url)
        created = writes(saved)[0]
        assert set(created["data"]["campaign"]) == set(CONTRACT["campaign_input"])
        assert created["data"]["campaign"]["message_type"] == "template" and created["data"]["campaign"]["text"] is None
        identity = saved["operations"][0]["operation"]["campaign_id"]
        assert not saved["confirmations"]
        action(page, "acknowledge").click()
        expect(page.locator("[data-cp-recipient-empty]")).to_be_visible()
        review(page, identity)
        assert not report(page, fixture_url)["confirmations"]
        expect(page.locator("[data-cp-confirm-consent]")).not_to_be_checked()
        expect(action(page, "confirm")).to_be_disabled()
        assert_review_geometry(page, size, label)
        action(page, "cancel-review").click()
        expect(page.locator("[data-cp-dialog]")).to_have_count(0)
        assert not report(page, fixture_url)["confirmations"]
        review(page, identity)
        page.locator("[data-cp-confirm-consent]").check()
        action(page, "confirm").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        admitted = report(page, fixture_url)
        assert len(admitted["confirmations"]) == 1
        confirmation = [c for c in writes(admitted) if c["path"].endswith("/confirm")][0]
        assert set(confirmation["data"]) == {"idempotency_key", "review_version", "confirmation", "consent_confirmed"}
        assert confirmation["data"]["confirmation"] == "AGENDAR" and confirmation["data"]["consent_confirmed"] is True
        assert_recovery(page, confirmation["data"]["idempotency_key"])
        action(page, "acknowledge").click()
        expect(page.locator(".os-cp-summary")).to_contain_text("Agendada")
        control(page, fixture_url, "seed_progress")
        action(page, "back").click()
        page.locator('[data-cp-row="' + CAMPAIGN_ID + '"] [data-cp-action="detail"]').click()
        expect(page.locator('[data-cp-metric="uncertain"] strong')).to_have_text("1")
        expect(page.locator('[data-cp-metric="failed"] strong')).to_have_text("1")
        for status in ("sent", "pending", "failed", "uncertain", "cancelled"):
            page.locator('[data-cp-recipient-tab="' + status + '"]').click()
            expect(page.locator("[data-cp-recipient]")).to_have_count(1)
            if status == "sent":
                expect(page.locator("[data-cp-recipient]")).to_contain_text("<img src=x")
                assert page.locator("[data-cp-recipient] img").count() == 0
            if status == "failed":
                page.locator("[data-cp-recipient] summary").click()
                expect(page.locator("[data-cp-recipient] details")).to_contain_text("Imagem · Aceito pelo transporte")
                expect(page.locator("[data-cp-recipient] details")).to_contain_text("Texto · Não enviado")
                expect(page.locator("[data-cp-recipient] td").last).to_contain_text("Falhou")
        tabs = page.locator("[data-cp-recipient-tab]").evaluate_all("""nodes => nodes.map(node => {
          const text = document.createRange(); text.selectNodeContents(node);
          const box = node.getBoundingClientRect(), parent = node.parentElement.getBoundingClientRect();
          return {label:node.textContent,lines:text.getClientRects().length,height:box.height,
            left:box.left,right:box.right,parentLeft:parent.left,parentRight:parent.right};
        })""")
        assert len(tabs) == 5
        assert all(tab["lines"] == 1 and tab["height"] >= 42 for tab in tabs), tabs
        assert all(tab["left"] >= tab["parentLeft"] - 1 and tab["right"] <= tab["parentRight"] + 1 for tab in tabs), tabs
        assert len(report(page, fixture_url)["confirmations"]) == 1
        assert_layout(page, size[0])
        page.screenshot(path=str(ARTIFACTS / (label + "-progress.png")), full_page=True, animations="disabled")
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_explicit_pause_resume_edit_archive_and_contact_authorization(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        review(page)
        page.locator("[data-cp-confirm-consent]").check()
        action(page, "confirm").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        action(page, "acknowledge").click()
        for verb, label in [("pause", "Pausada"), ("resume", "Agendada"), ("edit", None)]:
            before = len(writes(report(page, fixture_url)))
            action(page, verb).click()
            expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "action")
            assert len(writes(report(page, fixture_url))) == before
            action(page, "commit-action").click()
            expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
            action(page, "acknowledge").click()
            if label:
                expect(page.locator(".os-cp-summary")).to_contain_text(label)
        expect(field(page, "title")).to_be_visible()
        field(page, "title").fill("Campanha revisada sintética")
        action(page, "save").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        action(page, "acknowledge").click()
        action(page, "archive").click()
        action(page, "commit-action").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        action(page, "acknowledge").click()
        action(page, "back").click()
        action(page, "groups").click()
        action(page, "new-group").click()
        field(page, "group_name").fill("Grupo manual sintético")
        field(page, "members").fill("Pessoa sintética A;+55 (67) 99999-9917\nPessoa sintética B;+5567999999918")
        action(page, "save-group").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        created = [op["operation"] for op in report(page, fixture_url)["operations"] if op["operation"]["kind"] == "group_create"][0]
        action(page, "acknowledge").click()
        page.locator('[data-cp-group="' + GROUP_ID + '"] [data-cp-action="group"]').click()
        page.locator("[data-cp-blocked]").check()
        action(page, "permissions").click()
        expect(action(page, "commit-permissions")).to_be_disabled()
        field(page, "evidence").fill("Nova autorização sintética recebida do contato.")
        page.locator("[data-cp-permission-consent]").check()
        action(page, "commit-permissions").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        data = report(page, fixture_url)
        call = next(c for c in writes(data) if c["path"].endswith("/permissions"))
        assert call["data"]["contacts"] == [{"phone": "+5567999999913", "version": 2}]
        assert call["data"]["confirmation"] == "AUTORIZAR"
        assert len(data["confirmations"]) == 1
        assert_recovery(page, call["data"]["idempotency_key"])
        action(page, "acknowledge").click()
        page.locator('[data-cp-group="' + created["group_id"] + '"] [data-cp-action="archive-group"]').click()
        action(page, "commit-group-archive").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        assert len(report(page, fixture_url)["confirmations"]) == 1
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("phase", ["create", "review", "confirm", "action"])
def test_lost_response_reload_recovers_existing_identity_without_replaying(browser, fixture_url, phase):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        if phase == "create":
            fill_campaign(page)
            control(page, fixture_url, "lost_create")
            action(page, "save").click()
        elif phase == "review":
            control(page, fixture_url, "lost_review")
            action(page, "review").click()
        elif phase == "confirm":
            review(page)
            control(page, fixture_url, "lost_confirm")
            page.locator("[data-cp-confirm-consent]").check()
            action(page, "confirm").click()
        else:
            action(page, "archive").click()
            expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "action")
            control(page, fixture_url, "lost_action")
            action(page, "commit-action").click()
        expect(page.locator("[data-cp-feedback]")).to_contain_text("Não foi possível confirmar")
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "pending")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(ARTIFACTS / f"{browser[0]}-recovery-{phase}-pending.png"), full_page=True, animations="disabled")
        before = writes(report(page, fixture_url))
        key = before[-1]["data"]["idempotency_key"]
        assert_recovery(page, key)
        reload_same_session(page)
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "review" if phase == "review" else "result")
        after = report(page, fixture_url)
        assert writes(after) == before
        assert any(c["path"].endswith("/requests/" + key) for c in after["calls"])
        assert len(after["confirmations"]) == (1 if phase == "confirm" else 0)
        if phase == "review":
            expect(page.locator("[data-cp-confirm-consent]")).not_to_be_checked()
        page.screenshot(path=str(ARTIFACTS / f"{browser[0]}-recovery-{phase}-resolved.png"), full_page=True, animations="disabled")
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_missing_response_keeps_same_key_and_stale_proof_releases_only_rejected_review(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        fill_campaign(page)
        control(page, fixture_url, "missing_create")
        action(page, "save").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "pending")
        page.locator('[data-cp-dialog] [data-cp-action="recover"]').click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "missing")
        before = writes(report(page, fixture_url))
        assert len(before) == 1
        action(page, "retry").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        assert writes(report(page, fixture_url)) == [before[0], before[0]]
        action(page, "acknowledge").click()
        control(page, fixture_url, "stale_review")
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/review")) as response:
            action(page, "review").click()
        body = response.value.json()
        assert body["detail"]["rejection"]["reason"] == "stale_version"
        expect(page.locator("[data-cp-dialog]")).to_have_count(0)
        expect(page.locator("[data-cp-feedback]")).to_contain_text("pedido não foi registrado")
        assert not page.evaluate("Boolean(history.state.orionCampaignRecovery)")
        assert len(writes(report(page, fixture_url))) == 3
        action(page, "review").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "review")
        data = report(page, fixture_url)
        assert writes(data)[-1]["data"]["idempotency_key"] != writes(data)[-2]["data"]["idempotency_key"]
        assert not data["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize("mode", ["after_admission_review", "marker_confirm"])
def test_generic_post_admission_or_confirm_conflict_preserves_recovery(browser, fixture_url, mode):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        if mode == "marker_confirm":
            review(page)
            page.locator("[data-cp-confirm-consent]").check()
        control(page, fixture_url, mode)
        suffix = "/confirm" if mode == "marker_confirm" else "/review"
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/" + CAMPAIGN_ID + suffix)) as submitted:
            action(page, "confirm" if mode == "marker_confirm" else "review").click()
        assert submitted.value.status == 409
        assert submitted.value.json()["detail"]["code"] == "CAMPAIGN_CONFLICT"
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "pending")
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("aria-busy", "false")
        before = writes(report(page, fixture_url))
        assert len(before) == (2 if mode == "marker_confirm" else 1)
        assert_recovery(page, before[-1]["data"]["idempotency_key"])
        page.locator('[data-cp-dialog] [data-cp-action="recover"]').click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result" if mode == "marker_confirm" else "review")
        assert writes(report(page, fixture_url)) == before
        assert len(report(page, fixture_url)["confirmations"]) == (1 if mode == "marker_confirm" else 0)
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_filters_expiry_revocation_empty_and_other_niche_do_not_admit(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        control(page, fixture_url, "many_campaigns")
        action(page, "refresh").click()
        expect(page.locator("[data-cp-row]")).to_have_count(30)
        page.locator('[data-cp-pagination="list"] [data-cp-action="next"]').click()
        expect(page.locator('[data-cp-pagination="list"]')).to_contain_text("página 2")
        field(page, "q").fill("Página sintética 63")
        action(page, "filter").click()
        expect(page.locator("[data-cp-row]")).to_have_count(1)
        assert "Página" not in page.url
        action(page, "reset").click()
        expect(page.locator("[data-cp-row]")).to_have_count(30)
        review(page)
        control(page, fixture_url, "expire_review")
        reload_same_session(page)
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        expect(page.locator("[data-cp-dialog]")).to_contain_text("expirou")
        action(page, "acknowledge").click()
        # Acknowledge reads the detail and then its recipient page. Finish
        # those reads before making a distinct revocation/refresh scenario;
        # otherwise an in-flight read may correctly remove this route first.
        expect(page.locator(".os-cp-summary")).to_be_visible()
        expect(page.locator("[data-cp-recipient-empty]")).to_be_visible()
        expect(page.locator("#osSubscriptionCampaigns")).to_have_attribute("aria-busy", "false")
        before_revocation = writes(report(page, fixture_url))
        control(page, fixture_url, "revoke")
        with page.expect_response(lambda response: response.status == 403 and CAMPAIGNS_BASE in response.url):
            action(page, "refresh").click()
        expect(page.locator('#osNav [data-os-route="campanhas"]')).to_have_count(0)
        expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", "dashboard")
        expect(page.locator("#osSubscriptionDashboard")).to_have_attribute("aria-busy", "false")
        revoked = report(page, fixture_url)
        assert writes(revoked) == before_revocation
        assert not revoked["confirmations"] and not revoked["effects"]
        for extra in ["&fixtureProfile=standard", "&fixtureReadonly=1"]:
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/v1/account/business-identity"):
                page.goto(fixture_url + "/panel?fixtureRole=owner&fixturePlan=elite&aba=campanhas" + extra)
            fallback = "plano" if "standard" in extra else "dashboard"
            expect(page.locator("#page-suite")).to_have_attribute("data-os-current-route", fallback)
            if fallback == "dashboard":
                expect(page.locator("#osSubscriptionDashboard")).to_have_attribute("aria-busy", "false")
            expect(page.locator('#osNav [data-os-route="campanhas"]')).to_have_count(0)
            assert report(page, fixture_url)["calls"] == []
        open_campaigns(page, fixture_url, "&fixtureEmpty=1")
        expect(page.locator("[data-cp-empty]")).to_be_visible()
        assert not writes(report(page, fixture_url))
        assert not external, external
        assert not errors, errors
    finally:
        context.close()


def test_invalid_local_time_can_be_corrected_explicitly_with_the_same_request_key(browser, fixture_url):
    context, page, external, errors = setup_page(browser, fixture_url)
    try:
        open_campaigns(page, fixture_url)
        fill_campaign(page)
        field(page, "timezone").select_option("America/New_York")
        field(page, "start_date").fill("2030-03-10")
        field(page, "start_time").fill("02:30")
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/campaigns")) as response:
            action(page, "save").click()
        assert response.value.status == 422
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "pending")
        before = report(page, fixture_url)
        assert not before["operations"] and not before["confirmations"]
        key = writes(before)[0]["data"]["idempotency_key"]
        action(page, "correct").click()
        expect(field(page, "start_time")).to_have_value("02:30")
        assert_recovery(page, key)
        assert len(writes(report(page, fixture_url))) == 1
        field(page, "start_time").fill("03:30")
        action(page, "save").click()
        expect(page.locator("[data-cp-dialog]")).to_have_attribute("data-cp-view", "result")
        after = report(page, fixture_url)
        assert len(writes(after)) == 2 and writes(after)[1]["data"]["idempotency_key"] == key
        assert len(after["operations"]) == 1 and not after["confirmations"]
        assert not external, external
        assert not errors, errors
    finally:
        context.close()
