"""Loopback-only guidance fixture using the shipped shell and device read model.

There is no database, provider, bridge, ASR or writable synthetic operation.
Stale guidance grants in the standard/inactive cases deliberately exercise the
shell's independent workspace and active-company boundaries.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from browser_subscription_devices import Handler as DeviceHandler, PROFILE
from scripts.plans_preview import Handler as PlansHandler, base

GUIDANCE_FEATURE = "whatsapp_guidance.view"


class Handler(DeviceHandler):
    def body(self, value, status=200, mime="application/json", cookie=None):
        url = urlsplit(self.path)
        if cookie and url.path in {"/", "/panel"}:
            query = parse_qs(url.query)
            with base._lock:
                base._sessions[cookie].update(
                    guidance_calls=[],
                    guidance_grant=query.get("fixtureGuidance") != ["0"],
                    company_status="paused" if query.get("fixtureInactive") == ["1"] else "active",
                )
        session = self.session()
        if session and url.path == "/api/v1/account/businesses" and isinstance(value, list):
            value = [{**row, "workspace_profile": session["workspace_profile"],
                      "name": "Empresa sintética de assinaturas",
                      "status": session["company_status"]} for row in value]
            return PlansHandler.body(self, value, status, mime, cookie)
        if session and url.path.endswith("/management/permissions") and isinstance(value, dict):
            plan = {**value["effective_plan"], "workspace_profile": session["workspace_profile"]}
            if session["workspace_profile"] == PROFILE:
                features = {"account.manage", "business.setup"}
                if plan["active"]:
                    features.add("devices.manage")
            else:
                features = set(plan["features"])
            if session["guidance_grant"]:
                features.add(GUIDANCE_FEATURE)
            else:
                features.discard(GUIDANCE_FEATURE)
            plan["features"] = sorted(features)
            plan["permissions"] = {key: key in features for key in
                                   {*plan["permissions"], GUIDANCE_FEATURE, "devices.manage"}}
            plan["revision"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            # DeviceHandler's response projection is intentionally bypassed: the
            # test needs this explicit policy state, not its device-only grant.
            return PlansHandler.body(self, {"effective_plan": plan}, status, mime, cookie)
        return super().body(value, status, mime, cookie)

    def do_GET(self):
        path = urlsplit(self.path).path
        session = self.session()
        if session and path == "/__fixture/guidance-report":
            return self.body({
                "calls": session["guidance_calls"], "unexpected": session["unexpected"],
                "state": {"profile": session["workspace_profile"],
                          "active": session["company_status"] == "active",
                          "feature_granted": session["guidance_grant"],
                          "role": session["role"]},
            })
        if session and path.startswith(("/api/", "/auth/")):
            session["guidance_calls"].append({"method": "GET", "path": path})
        return super().do_GET()

    def mutate(self):
        session = self.session()
        if session:
            session["guidance_calls"].append({"method": self.command, "path": urlsplit(self.path).path})
        return self.reject(session, "Guidance is read-only; no mutation is permitted")
