"""Install a single scoped, audited fallback using the ordinary rule validators."""
import asyncio
import json
from time import monotonic
from uuid import UUID

from sqlalchemy import text

from app.catalog_api import Actor, audit, support_session_factory
from app.subscription_chatbot import _write_rule, validate_definition
from app.subscription_chatbot import rules_engine, schemas, store

RULE_ID = UUID("95bc1a3b-45c3-497a-bb75-9e1e81f968fa")
ACTION_ID = UUID("e107a2c9-414a-4555-b368-2747c40d8104")
REPLY = (
    "Oi! Aqui é a BaltigoFlix. Não consegui identificar sua solicitação. "
    "Pode contar em poucas palavras se precisa de ajuda com acesso, renovação ou outra dúvida?"
)

TARGET = """
SELECT t.id AS tenant_id,d.id AS device_id,d.owner_account_id
FROM tenants t
JOIN subscription_devices d ON d.tenant_id=t.id AND d.removed_at IS NULL
JOIN tenant_memberships m ON m.tenant_id=t.id AND m.account_id=d.owner_account_id
WHERE lower(btrim(t.name))='baltigoflix' AND t.status='active' AND m.active AND m.role='owner'
  AND d.status='connected'
LIMIT 2 FOR UPDATE OF t,d,m
"""


def _log(state, **fields):
    print("orion_baltigoflix_fallback " + json.dumps({"state": state, **fields}), flush=True)


def fallback_definition(device):
    rule = schemas.RuleDefinition.model_validate({
        "device_id": device, "enabled": True, "match_mode": "equal", "keyword": "*****",
        "response": {"kind": "sequence", "actions": [
            {"id": ACTION_ID, "type": "text", "text": REPLY, "delay_seconds": 1}
        ]},
    })
    return rule.model_dump(mode="python")


async def _apply():
    async with support_session_factory()() as db:
        async with db.begin():
            targets = (await db.execute(text(TARGET))).mappings().all()
            if len(targets) != 1:
                return "tenant_or_device_not_unique", {"matches": len(targets)}
            target = targets[0]
            tenant, device = target["tenant_id"], target["device_id"]
            await store.workspace(db, tenant)
            await store.writable(db, tenant)
            current = await store.revision(db, tenant)
            graph = await store.load_graph(db, tenant)
            eligible = lambda r: r["enabled"] and (r["device_id"] is None or r["device_id"] == device)
            fallbacks = [r for r in graph[0].values() if eligible(r) and r["keyword"] in rules_engine.WILDCARDS]
            if fallbacks:
                ours = any(r["id"] == RULE_ID for r in fallbacks)
                return ("already_applied" if ours else "fallback_already_configured"), {"revision": current}
            if current != 41:
                return "configuration_changed", {"revision": current}
            await store.check_revision(db, tenant, current)
            pending = (await db.execute(text("""
                SELECT count(*) FROM subscription_device_effects WHERE tenant_id=:t AND device_id=:d
                AND type='chatbot_action' AND status IN ('pending','running') AND expires_at>clock_timestamp()
            """), {"t": tenant, "d": device})).scalar_one()
            if pending:
                return "active_replies", {"count": pending}
            actor = Actor("support", tenant_id=tenant, account_id=target["owner_account_id"], maintenance=True)
            definition = fallback_definition(device)
            definition["response"] = json.loads(store.encode(definition["response"]))
            await validate_definition(db, tenant, actor, RULE_ID, definition, graph=graph)
            await _write_rule(db, tenant, actor, RULE_ID, definition)
            revision = await store.bump_revision(db, tenant)
            await audit(db, tenant, "chatbot_fallback_installed_by_support", "subscription_chatbot_rules", RULE_ID)
            graph_after = await store.load_graph(db, tenant)
            rule = graph_after[0][str(RULE_ID)]
            assert eligible(rule) and rule["keyword"] == "*****"
            assert set(graph_after[0]) - {str(RULE_ID)} == set(graph[0])
        return "applied", {"revision": revision, "previous_revision": current, "cooldown_minutes": 60}


async def repair():
    try:
        state, evidence = await _apply()
        _log(state, **evidence)
        if state in {"applied", "already_applied"}:
            await _watch()
    except Exception as exc:
        origin = getattr(exc, "orig", None)
        _log("error", kind=type(exc).__name__, sqlstate=getattr(origin, "sqlstate", None),
             http_status=getattr(exc, "status_code", None))


async def _watch():
    statement = text("""
        SELECT r.status AS run, e.status AS response, e.attempts,
               e.provider_id IS NOT NULL AS provider_id_present
        FROM subscription_chatbot_rules rule
        JOIN subscription_chatbot_runs r ON r.tenant_id=rule.tenant_id AND r.rule_id=rule.id
        LEFT JOIN subscription_device_effects e ON e.tenant_id=r.tenant_id AND e.chatbot_run_id=r.id
        WHERE rule.id=:rule AND r.created_at>=now()-interval '10 minutes'
        ORDER BY r.created_at DESC LIMIT 8
    """)
    deadline = monotonic() + 120
    previous = None
    while monotonic() < deadline:
        async with support_session_factory()() as db:
            async with db.begin():
                await db.execute(text("SET TRANSACTION READ ONLY"))
                rows = [dict(row) for row in (await db.execute(statement, {"rule": RULE_ID})).mappings().all()]
        if rows and rows != previous:
            _log("live_observation", results=rows)
            previous = rows
        if any(row["run"] == "succeeded" and row["response"] == "accepted" and row["provider_id_present"]
               for row in rows):
            _log("live_provider_accepted")
            return
        await asyncio.sleep(5)
    _log("observation_closed")
