"""Explicit entitlements for isolated UI fixtures, never an application fallback.

Only the standard library and Orion's dependency-free catalogue are imported:
browser-only CI intentionally has no database or API runtime installed.
"""
from __future__ import annotations

import hashlib
import json

from app.plan_catalog import ALL_FEATURES, CODES, PLAN_FEATURES, RECOVERY_FEATURES


def synthetic_plan_state(tenant_id, code="elite"):
    """Model an indefinite synthetic manual grant using the real feature matrix."""
    plan = CODES[code] if code is not None else None  # Unknown codes never gain a fallback.
    features = sorted((PLAN_FEATURES[code] if plan else frozenset()) | RECOVERY_FEATURES)
    state = {
        "code": code, "name": plan["name"] if plan else "Sem plano ativo",
        "source": "manual" if plan else "none", "status": "active" if plan else "no_access",
        "active": plan is not None, "valid_until": None, "version": 1, "platform_workspace": False,
        "features": features, "permissions": {key: key in features for key in sorted(ALL_FEATURES)},
        "limits": {"max_professionals": plan["max_professionals"] if plan else None, "active_professionals": 0,
                   "excess_professionals": 0},
        "manual_assignment": {"plan_code": code, "started_at": "2020-01-01T00:00:00Z",
                              "expires_at": None, "active": True} if plan else None,
        "commercial_subscription": None, "trial": None,
    }
    state["revision"] = hashlib.sha256(json.dumps(
        {**state, "tenant_id": str(tenant_id)}, sort_keys=True, default=str,
        separators=(",", ":")).encode()).hexdigest()
    return state
