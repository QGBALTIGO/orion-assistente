"""Canonical chatbot admission and effect authorization; no network or legacy AI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import time
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app import subscription_chatbot_store as store
from app import subscription_chatbot_rules as rules
from app.subscription_device_rules import business_state


def descriptor(effect):
    result = {"operation_id": effect["id"], "type": effect["type"]}
    if effect["type"] == "chatbot_action":
        result.update(chatbot_revision=effect["chatbot_revision"],
                      delay_seconds=effect["payload"]["delay_seconds"],
                      action_type=effect["payload"]["action"]["type"], due_at=effect["due_at"])
    return result


async def snapshot(db, tenant):
    return {"revision": await store.revision(db, tenant)}


async def reap_unreachable(db, device):
    """Close impossible pending tails, without changing uncertain evidence.

    Each fresh contact input must be able to recover after an old attempt's
    TTL. Linking every new run behind a dead tail would otherwise extend the
    blockage indefinitely. Nothing in this sweep authorizes or repeats I/O.
    """
    rows = (await db.execute(text("""WITH RECURSIVE doomed(id) AS (
        SELECT e.id FROM subscription_device_effects e
        LEFT JOIN subscription_device_effects p ON p.tenant_id=e.tenant_id AND p.id=e.previous_effect
        LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.id=e.chatbot_run_id
        LEFT JOIN subscription_device_effects a ON a.tenant_id=e.tenant_id
            AND a.id=CAST(r.input_context->>'transcription_predecessor' AS uuid)
        WHERE e.tenant_id=:t AND e.device_id=:d AND e.type='chatbot_action' AND e.status='pending'
          AND (e.expires_at<=clock_timestamp() OR p.status IN ('failed','not_sent')
            OR p.expires_at<=clock_timestamp() AND p.status<>'accepted'
            OR r.input_context->>'transcription_predecessor' IS NOT NULL AND
              (a.id IS NULL OR a.status IN ('failed','not_sent')
               OR a.expires_at<=clock_timestamp() AND a.status<>'accepted'))
        UNION
        SELECT child.id FROM subscription_device_effects child JOIN doomed parent ON child.previous_effect=parent.id
        WHERE child.tenant_id=:t AND child.device_id=:d AND child.type='chatbot_action' AND child.status='pending'
    ) UPDATE subscription_device_effects e SET status='not_sent',finished_at=clock_timestamp(),
        error_code='CHATBOT_PREDECESSOR_UNAVAILABLE' FROM doomed
        WHERE e.id=doomed.id AND e.tenant_id=:t RETURNING e.*"""),
        {"t": device["tenant_id"], "d": device["id"]})).mappings().all()
    # A run has one sequential tail; one terminal update per affected run is
    # sufficient after the recursive SQL has closed all dependent children.
    handled = set()
    for effect in rows:
        if effect["chatbot_run_id"] not in handled:
            await after_effect_finish(db, device, effect, "not_sent")
            handled.add(effect["chatbot_run_id"])
    return len(rows)


async def available_effects(db, device_id, event_id=None):
    values = {"d": device_id}
    event_filter = ""
    if event_id is not None:
        values["event"] = event_id
        event_filter = " AND e.event_id=:event"
    rows = (await db.execute(text("""SELECT e.* FROM subscription_device_effects e
        LEFT JOIN subscription_device_effects p ON p.tenant_id=e.tenant_id AND p.id=e.previous_effect
        LEFT JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.id=e.chatbot_run_id
        LEFT JOIN subscription_device_effects a ON a.tenant_id=e.tenant_id
            AND a.id=CAST(r.input_context->>'transcription_predecessor' AS uuid)
        WHERE e.device_id=:d AND e.status='pending' AND e.expires_at>clock_timestamp()
        AND (e.type<>'chatbot_action' OR e.previous_effect IS NULL OR p.status='accepted')
        AND (e.type<>'chatbot_action' OR r.input_context->>'transcription_predecessor' IS NULL OR a.status='accepted')""" + event_filter +
        " ORDER BY e.created_at,e.id LIMIT 32"), values)).mappings().all()
    return [descriptor(row) for row in rows]


async def _facts(db, device, run):
    row = (await db.execute(text("""SELECT t.name,a.email,b.configuration->>'contact_phone' AS contact_phone
        FROM tenants t JOIN panel_accounts a ON a.id=:a
        LEFT JOIN business_onboarding b ON b.tenant_id=t.id WHERE t.id=:t"""),
        {"t": device["tenant_id"], "a": device["owner_account_id"]})).mappings().one()
    context = run["input_context"]
    name = context.get("name") or run["phone"]
    contact = {"name": name, "phone_number": run["phone"], "my_name": row["name"],
               "my_email": row["email"], "my_contact_number": row["contact_phone"] or ""}
    occurred = datetime.fromisoformat(context["occurred_at"].replace("Z", "+00:00"))
    runtime = device.get("runtime") or {}
    own_phone = runtime.get("phone") if runtime.get("phone_verified") is True else ""
    http = {"appName": "Orion Agenda", "messageDateTime": int(occurred.timestamp()),
            "devicePhone": own_phone or "", "deviceName": device["name"], "senderName": name,
            "senderMessage": run["input_text"] or "", "senderPhone": run["phone"], "userAgent": "Orion-Chatbot/1"}
    return contact, http


def blocked_by_hours(device):
    return bool(device["settings"]["business_hours"]["block_replies_outside_hours"]
                and business_state(device["settings"]) != "open")


async def _ignore(db, run, code):
    await db.execute(text("""UPDATE subscription_chatbot_runs SET status='ignored',error_code=:code,
        updated_at=clock_timestamp(),finished_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
        {"t": run["tenant_id"], "id": run["id"], "code": code})
    if run.get("job_id"):
        await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',error_code=:code,
            finished_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
            {"t": run["tenant_id"], "id": run["job_id"], "code": code})


async def _input(db, device, event_id, data, fence, *, origin=None, forced_rule=None, job=None):
    current = await store.revision(db, device["tenant_id"])
    if data.get("chatbot_revision") != current:
        return None
    origin = origin or data.get("origin", "contact")
    value = data.get("text") or ""
    if origin == "owner_command":
        if not value.startswith("!") or not value[1:].strip():
            return None
        if data.get("message_id") and (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND device_id=:d AND provider_id=:message LIMIT 1"""),
            {"t": device["tenant_id"], "d": device["id"], "message": data["message_id"]})).scalar_one_or_none():
            return None
        value = value[1:]
    selection = data.get("selection_id")
    kind = "test" if forced_rule else "selection" if selection else data["kind"]
    if kind == "text" and not value.strip():
        return None
    status = "ready"
    if kind == "audio":
        config = device["settings"]["transcription"]
        if not config["enabled"] or not config["reply_enabled"]:
            return None
        transcribe = (await db.execute(text("""SELECT id FROM subscription_device_effects
            WHERE tenant_id=:t AND device_id=:d AND event_id=:event AND type='transcribe' AND status='pending'"""),
            {"t": device["tenant_id"], "d": device["id"], "event": event_id})).scalar_one_or_none()
        if not transcribe:
            return None
        status, value = "waiting_audio", None
    context = {"name": data.get("name"), "occurred_at": data["occurred_at"],
               "selection_id": selection, "quoted_message_id": data.get("quoted_message_id")}
    result = (await db.execute(text("""INSERT INTO subscription_chatbot_runs
        (tenant_id,device_id,generation,source_event_id,chatbot_revision,settings_revision,actor_account_id,
         ingress_order,input_fence,phone,origin,input_kind,input_text,input_context,rule_id,rule_revision,job_id,status)
        VALUES(:t,:d,:g,:event,:chatrev,:devrev,:actor,:ordering,:fence,:phone,:origin,:kind,:value,
               CAST(:context AS jsonb),:rule,:rule_revision,:job,:status)
        ON CONFLICT(tenant_id,source_event_id) DO NOTHING RETURNING *"""),
        {"t": device["tenant_id"], "d": device["id"], "g": device["generation"], "event": event_id,
         "chatrev": current, "devrev": device["revision"], "actor": device["owner_account_id"],
         "ordering": data.get("ingress_order") or time.time_ns() // 1000, "fence": fence,
         "phone": data["phone"], "origin": origin, "kind": kind, "value": value,
         "context": store.encode(context), "rule": forced_rule["id"] if forced_rule else None,
         "rule_revision": forced_rule["revision"] if forced_rule else None,
         "job": job["id"] if job else None, "status": status})).mappings().one_or_none()
    return dict(result) if result else None


async def record_event(db, device, transport, event_id, data, fence):
    if data["kind"] not in {"text", "audio"} or data.get("chatbot_revision") is None or not data.get("phone"):
        return
    await store.workspace(db, device["tenant_id"])
    await reap_unreachable(db, device)
    run = await _input(db, device, event_id, data, fence)
    if run:
        await drain_contact(db, device, run["phone"])


async def _selected(db, device, run):
    context = run["input_context"]
    raw = context.get("selection_id")
    quoted = context.get("quoted_message_id")
    if not isinstance(raw, str) or not raw.startswith("ocb:") or len(raw) > 160 or not quoted:
        return None
    row = (await db.execute(text("""SELECT s.*,r.rule_id,r.rule_revision,e.provider_id,e.status AS effect_status
        FROM subscription_chatbot_selections s
        JOIN subscription_chatbot_runs r ON r.tenant_id=s.tenant_id AND r.id=s.run_id
        JOIN subscription_device_effects e ON e.tenant_id=s.tenant_id AND e.id=s.outgoing_effect_id
        WHERE s.tenant_id=:t AND s.device_id=:d AND s.generation=:g AND s.phone=:phone
          AND s.token_hash=:hash AND s.chatbot_revision=:revision AND s.expires_at>clock_timestamp()
          AND s.consumed_event_id IS NULL AND e.status='accepted' AND e.provider_id=:quoted
        FOR UPDATE OF s"""), {"t": device["tenant_id"], "d": device["id"], "g": device["generation"],
            "phone": run["phone"], "hash": hashlib.sha256(raw.encode()).hexdigest(),
            "revision": run["chatbot_revision"], "quoted": quoted})).mappings().one_or_none()
    if not row:
        return None
    await db.execute(text("UPDATE subscription_chatbot_selections SET consumed_event_id=:event WHERE id=:id"),
                     {"event": run["source_event_id"], "id": row["id"]})
    return dict(row)


async def _reserve_window(db, device, run, rule):
    if rule["keyword"] not in rules.WILDCARDS or rule["keyword"] == "****" or run["origin"] == "test_response":
        return True
    old = (await db.execute(text("""SELECT * FROM subscription_chatbot_cooldowns WHERE
        tenant_id=:t AND device_id=:d AND rule_id=:rule AND phone=:phone FOR UPDATE"""),
        {"t": device["tenant_id"], "d": device["id"], "rule": rule["id"], "phone": run["phone"]})).mappings().one_or_none()
    now = datetime.now(timezone.utc)
    zone = device["settings"]["timezone"]
    if not rules.window_available(rule["keyword"], old, now, zone):
        return False
    until, local_day = rules.window_end(rule["keyword"], now, zone)
    await db.execute(text("""INSERT INTO subscription_chatbot_cooldowns
        (tenant_id,device_id,rule_id,phone,next_allowed_at,local_day,run_id)
        VALUES(:t,:d,:rule,:phone,:until,:day,:run)
        ON CONFLICT(tenant_id,device_id,rule_id,phone) DO UPDATE SET
        reserved_at=clock_timestamp(),next_allowed_at=EXCLUDED.next_allowed_at,
        local_day=EXCLUDED.local_day,run_id=EXCLUDED.run_id"""),
        {"t": device["tenant_id"], "d": device["id"], "rule": rule["id"], "phone": run["phone"],
         "until": until, "day": local_day, "run": run["id"]})
    return True


async def put_effect(db, device, run, item, key, *, previous=None):
    identity = uuid4()
    action = deepcopy(item["action"])
    selections = []
    if action["type"] == "list":
        for section in action["sections"]:
            section.pop("id", None)
            for row in section["rows"]:
                selection = "ocb:" + secrets.token_urlsafe(24)
                selected_action = row.pop("action")
                row["id"] = selection
                selections.append((selection, {"kind": "row", "definition": selected_action}))
    elif action["type"] == "carousel":
        for index, card in enumerate(action["cards"]):
            selection = "ocb:" + secrets.token_urlsafe(24)
            card["selection_id"] = selection
            # The observed card has no configurable URL/action. Record a
            # verified click without inventing an executable target.
            selections.append((selection, {"kind": "carousel", "card_index": index}))
    payload = {"phone": run["phone"], "delay_seconds": item["delay_seconds"], "action": action}
    result = (await db.execute(text("""INSERT INTO subscription_device_effects
        (id,tenant_id,device_id,generation,settings_revision,event_id,effect_key,type,purpose,payload,
         previous_effect,chatbot_revision,chatbot_run_id,due_at)
        VALUES(:id,:t,:d,:g,:r,:event,:key,'chatbot_action','chatbot_reply',CAST(:payload AS jsonb),
               :previous,:chatrev,:run,clock_timestamp()+(CAST(:delay AS double precision)*interval '1 second'))
        ON CONFLICT(device_id,event_id,effect_key) DO NOTHING RETURNING *"""),
        {"id": identity, "t": device["tenant_id"], "d": device["id"], "g": device["generation"],
         "r": device["revision"], "event": run["source_event_id"], "key": key, "payload": store.encode(payload),
         "previous": previous, "chatrev": run["chatbot_revision"], "run": run["id"], "delay": item["delay_seconds"]})).mappings().one_or_none()
    if result is None:
        return dict((await db.execute(text("SELECT * FROM subscription_device_effects WHERE device_id=:d AND event_id=:event AND effect_key=:key"),
            {"d": device["id"], "event": run["source_event_id"], "key": key})).mappings().one())
    for token, selected in selections:
        await db.execute(text("""INSERT INTO subscription_chatbot_selections
            (tenant_id,device_id,generation,chatbot_revision,run_id,outgoing_effect_id,phone,token_hash,action)
            VALUES(:t,:d,:g,:revision,:run,:effect,:phone,:token,CAST(:action AS jsonb))"""),
            {"t": device["tenant_id"], "d": device["id"], "g": device["generation"],
             "revision": run["chatbot_revision"], "run": run["id"], "effect": result["id"],
             "phone": run["phone"], "token": hashlib.sha256(token.encode()).hexdigest(), "action": store.encode(selected)})
    return dict(result)


async def _plan(db, device, run, rule, graph, selected_action=None):
    contacts, http = await _facts(db, device, run)
    try:
        plan, dependencies = rules.compile_plan(rule["id"], graph[0], graph[1], contacts, http,
            selected_action=selected_action, allow_inactive_root=run["origin"] == "test_response")
    except ValueError:
        await _ignore(db, run, "CHATBOT_INVALID_GRAPH")
        return False
    run.update(rule_id=rule["id"], rule_revision=rule["revision"])
    transcript = run["input_context"].get("transcription_predecessor")
    transcript = UUID(transcript) if transcript else None
    previous = None
    # Contact ordering spans complete rule sequences, not just admission.
    previous = (await db.execute(text("""SELECT e.id FROM subscription_device_effects e
            JOIN subscription_chatbot_runs r ON r.tenant_id=e.tenant_id AND r.id=e.chatbot_run_id
            WHERE e.tenant_id=:t AND e.device_id=:d AND r.phone=:phone AND r.ingress_order<:ordering
              AND e.type='chatbot_action' AND e.expires_at>clock_timestamp()
              AND e.status IN ('pending','running','uncertain','accepted')
              AND e.chatbot_revision=:revision
              AND NOT EXISTS(SELECT 1 FROM subscription_device_effects child
                  WHERE child.tenant_id=e.tenant_id AND child.chatbot_run_id=e.chatbot_run_id
                    AND child.previous_effect=e.id)
            ORDER BY r.ingress_order DESC,e.created_at DESC,e.id DESC LIMIT 1"""),
            {"t": device["tenant_id"], "d": device["id"], "phone": run["phone"],
             "ordering": run["ingress_order"], "revision": run["chatbot_revision"]})).scalar_one_or_none()
    previous = previous or transcript
    for index, item in enumerate(plan):
        effect = await put_effect(db, device, run, item, f"chatbot:{run['id']}:{index}", previous=previous)
        previous = effect["id"]
    await db.execute(text("""UPDATE subscription_chatbot_runs SET rule_id=:rule,rule_revision=:rule_revision,
        plan=CAST(:plan AS jsonb),status='planned',updated_at=clock_timestamp()
        WHERE tenant_id=:t AND id=:id"""),
        {"t": device["tenant_id"], "id": run["id"], "rule": rule["id"], "rule_revision": rule["revision"],
         "plan": store.encode(plan)})
    if run.get("job_id"):
        await db.execute(text("UPDATE subscription_chatbot_jobs SET status='running',started_at=clock_timestamp() WHERE id=:id"), {"id": run["job_id"]})
    return True


async def drain_contact(db, device, phone):
    queue = (await db.execute(text("""SELECT * FROM subscription_chatbot_runs
        WHERE tenant_id=:t AND device_id=:d AND phone=:phone AND status IN ('waiting_audio','ready','waiting_order')
        ORDER BY ingress_order,id FOR UPDATE"""),
        {"t": device["tenant_id"], "d": device["id"], "phone": phone})).mappings().all()
    if not queue:
        return
    current = await store.revision(db, device["tenant_id"])
    graph = await store.load_graph(db, device["tenant_id"])
    for original in queue:
        run = dict(original)
        if (run["chatbot_revision"] != current or run["settings_revision"] != device["revision"]
                or run["generation"] != device["generation"] or run["actor_account_id"] != device["owner_account_id"]):
            await _ignore(db, run, "CHATBOT_EFFECT_REVOKED")
            continue
        if run["status"] == "waiting_audio":
            effect = (await db.execute(text("""SELECT status,expires_at FROM subscription_device_effects
                WHERE tenant_id=:t AND event_id=:event AND type='transcribe'"""),
                {"t": device["tenant_id"], "event": run["source_event_id"]})).mappings().one_or_none()
            if effect and effect["status"] in {"pending", "running"} and effect["expires_at"] > datetime.now(timezone.utc):
                break
            await _ignore(db, run, "CHATBOT_AUDIO_UNAVAILABLE")
            continue
        if blocked_by_hours(device):
            await _ignore(db, run, "CHATBOT_OUTSIDE_HOURS")
            continue
        selected_action = None
        if run["input_kind"] == "selection":
            selection = await _selected(db, device, run)
            if not selection or selection["action"]["kind"] != "row":
                await _ignore(db, run, "CHATBOT_SELECTION_UNAVAILABLE")
                continue
            rule = graph[0].get(str(selection["rule_id"]))
            if not rule or not rule["enabled"] or rule["revision"] != selection["rule_revision"]:
                await _ignore(db, run, "CHATBOT_EFFECT_REVOKED")
                continue
            selected_action = selection["action"]["definition"]
        elif run["origin"] == "test_response":
            rule = graph[0].get(str(run["rule_id"]))
            if not rule or rule["revision"] != run["rule_revision"]:
                await _ignore(db, run, "CHATBOT_EFFECT_REVOKED")
                continue
        else:
            matches = rules.candidates(graph[0].values(), run["input_text"] or "", device["id"])
            rule = matches[0] if matches else None
            if rule is None:
                await _ignore(db, run, "CHATBOT_NO_MATCH")
                continue
            if not await _reserve_window(db, device, run, rule):
                await _ignore(db, run, "CHATBOT_REPLY_WINDOW")
                continue
        await _plan(db, device, run, rule, graph, selected_action)


async def complete_transcript(db, device, event_id, transcript, predecessor=None):
    run = (await db.execute(text("""SELECT * FROM subscription_chatbot_runs WHERE tenant_id=:t
        AND device_id=:d AND source_event_id=:event AND status='waiting_audio' FOR UPDATE"""),
        {"t": device["tenant_id"], "d": device["id"], "event": event_id})).mappings().one_or_none()
    if not run:
        return
    context = dict(run["input_context"])
    context["transcription_predecessor"] = str(predecessor) if predecessor else None
    await db.execute(text("""UPDATE subscription_chatbot_runs SET input_text=:value,input_context=CAST(:context AS jsonb),
        status='ready',updated_at=clock_timestamp() WHERE tenant_id=:t AND id=:id"""),
        {"t": device["tenant_id"], "id": run["id"], "value": transcript, "context": store.encode(context)})
    await drain_contact(db, device, run["phone"])


async def create_test(db, tenant, actor, rule, payload, job):
    device, transport = await store.check_device(db, tenant, payload.device_id, actor_id=actor.account_id, live=True)
    if device["status"] != "connected":
        raise store.fail("CHATBOT_CAPABILITY_UNAVAILABLE", 409, "Conecte o dispositivo antes de enviar o teste.")
    if rule["device_id"] is not None and rule["device_id"] != device["id"]:
        raise store.fail("CHATBOT_INVALID_GRAPH", 422, "A resposta pertence a outro dispositivo.")
    now = datetime.now(timezone.utc)
    data = {"phone": payload.phone, "text": rule["keyword"], "name": "Contato de teste", "kind": "text",
            "origin": "test_response", "occurred_at": now.isoformat(), "chatbot_revision": payload.expected_chatbot_revision,
            "ingress_order": time.time_ns() // 1000}
    event_id = (await db.execute(text("""INSERT INTO subscription_device_events
        (tenant_id,device_id,generation,provider_event_id,kind,occurred_at,payload)
        VALUES(:t,:d,:g,:provider,'text',:at,CAST(:payload AS jsonb)) RETURNING id"""),
        {"t": tenant, "d": device["id"], "g": device["generation"], "provider": "chatbot-test:"+str(job["id"]),
         "at": now, "payload": store.encode(data)})).scalar_one()
    run = await _input(db, device, event_id, data, transport["fence"], origin="test_response", forced_rule=rule, job=job)
    await drain_contact(db, device, run["phone"])


async def policy_allows(db, device, effect):
    if effect["type"] != "chatbot_action":
        return False
    await store.workspace(db, device["tenant_id"])
    if effect["chatbot_revision"] != await store.revision(db, device["tenant_id"]) or blocked_by_hours(device):
        return False
    run = (await db.execute(text("SELECT * FROM subscription_chatbot_runs WHERE tenant_id=:t AND id=:id"),
                           {"t": device["tenant_id"], "id": effect["chatbot_run_id"]})).mappings().one_or_none()
    if (not run or run["actor_account_id"] != device["owner_account_id"] or run["generation"] != device["generation"]
            or run["status"] not in {"planned", "succeeded"}):
        return False
    rule = (await db.execute(text("SELECT * FROM subscription_chatbot_rules WHERE tenant_id=:t AND id=:id AND removed_at IS NULL"),
                            {"t": device["tenant_id"], "id": run["rule_id"]})).mappings().one_or_none()
    return bool(rule and rule["revision"] == run["rule_revision"]
                and (rule["enabled"] or run["origin"] == "test_response")
                and (rule["device_id"] is None or rule["device_id"] == device["id"]))


async def authorize_effect(db, device, effect, owner, fence, payload):
    denied = {"authorized": False, "operation_id": str(effect["id"])}
    if payload.chatbot_revision != effect["chatbot_revision"]:
        return denied
    recheck = payload.recheck
    if recheck:
        if effect["status"] != "running" or effect["claimed_owner"] != owner or effect["claimed_fence"] != fence:
            return denied
    elif effect["status"] != "pending":
        return denied
    from app.subscription_sent_messages import message_authority
    if not await message_authority(db, device, effect):
        return denied
    if (effect["settings_revision"] != device["revision"] or effect["generation"] != device["generation"]
            or effect["expires_at"] <= datetime.now(timezone.utc) or not await policy_allows(db, device, effect)):
        if not recheck:
            await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
                finished_at=clock_timestamp(),error_code='CHATBOT_EFFECT_REVOKED' WHERE id=:id"""), {"id": effect["id"]})
        return denied
    if not recheck:
        now = datetime.now(timezone.utc)
        if effect["due_at"] > now:
            return denied | {"retry_after_ms": max(1, int((effect["due_at"]-now).total_seconds()*1000))}
        if effect["previous_effect"]:
            previous = (await db.execute(text("SELECT status,finished_at FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
                                        {"t": device["tenant_id"], "id": effect["previous_effect"]})).mappings().one_or_none()
            if not previous or previous["status"] != "accepted":
                return denied
            if previous["finished_at"]:
                due = previous["finished_at"] + timedelta(seconds=effect["payload"]["delay_seconds"])
                if due > now:
                    return denied | {"retry_after_ms": max(1, int((due-now).total_seconds()*1000))}
        transcript = (await db.execute(text("""SELECT input_context->>'transcription_predecessor'
            FROM subscription_chatbot_runs WHERE tenant_id=:t AND id=:id"""),
            {"t": device["tenant_id"], "id": effect["chatbot_run_id"]})).scalar_one_or_none()
        if transcript:
            accepted = (await db.execute(text("SELECT status FROM subscription_device_effects WHERE tenant_id=:t AND id=:id"),
                {"t": device["tenant_id"], "id": UUID(transcript)})).scalar_one_or_none()
            if accepted != "accepted":
                return denied
        await db.execute(text("""UPDATE subscription_device_effects SET status='running',attempts=attempts+1,
            authorized_at=clock_timestamp(),claimed_owner=:owner,claimed_fence=:fence WHERE id=:id"""),
            {"id": effect["id"], "owner": owner, "fence": fence})
    run = (await db.execute(text("SELECT rule_id,rule_revision FROM subscription_chatbot_runs WHERE tenant_id=:t AND id=:id"),
                           {"t": device["tenant_id"], "id": effect["chatbot_run_id"]})).mappings().one()
    return jsonable_encoder({"authorized": True, "operation_id": effect["id"], "type": "chatbot_action",
        "device_id": device["id"], "generation": device["generation"], "settings_revision": device["revision"],
        "chatbot_revision": effect["chatbot_revision"], "rule_id": run["rule_id"], "rule_revision": run["rule_revision"],
        "payload": effect["payload"]})


async def after_effect_finish(db, device, effect, state):
    if effect["type"] == "transcribe" and state in {"failed", "uncertain", "not_sent"}:
        run = (await db.execute(text("""SELECT * FROM subscription_chatbot_runs WHERE tenant_id=:t
            AND source_event_id=:event AND status='waiting_audio'"""),
            {"t": device["tenant_id"], "event": effect["event_id"]})).mappings().one_or_none()
        if run:
            await _ignore(db, run, "CHATBOT_AUDIO_UNAVAILABLE")
            await drain_contact(db, device, run["phone"])
    if state == "accepted":
        children = (await db.execute(text("""UPDATE subscription_device_effects SET
            due_at=clock_timestamp()+(CAST(payload->>'delay_seconds' AS double precision)*interval '1 second')
            WHERE tenant_id=:t AND previous_effect=:previous AND type='chatbot_action' AND status='pending'
            RETURNING *"""), {"t": device["tenant_id"], "previous": effect["id"]})).mappings().all()
    else:
        children = []
    if effect["type"] != "chatbot_action":
        return [descriptor(child) for child in children]
    run = (await db.execute(text("SELECT * FROM subscription_chatbot_runs WHERE tenant_id=:t AND id=:id FOR UPDATE"),
                           {"t": device["tenant_id"], "id": effect["chatbot_run_id"]})).mappings().one()
    if state in {"failed", "not_sent"}:
        await db.execute(text("""UPDATE subscription_device_effects SET status='not_sent',
            error_code='chatbot_predecessor_failed',finished_at=clock_timestamp()
            WHERE tenant_id=:t AND chatbot_run_id=:run AND status='pending'"""),
            {"t": device["tenant_id"], "run": run["id"]})
    stats = (await db.execute(text("""SELECT count(*) FILTER(WHERE status<>'accepted') AS unfinished,
        count(*) FILTER(WHERE status IN ('failed','not_sent','uncertain')) AS failed,
        count(*) FILTER(WHERE status='accepted' AND payload->'action'->>'type'<>'http') AS accepted_messages
        FROM subscription_device_effects WHERE tenant_id=:t AND chatbot_run_id=:run"""),
        {"t": device["tenant_id"], "run": run["id"]})).mappings().one()
    if stats["unfinished"] == 0:
        await db.execute(text("UPDATE subscription_chatbot_runs SET status='succeeded',finished_at=clock_timestamp() WHERE id=:id"), {"id": run["id"]})
        if run["job_id"]:
            await store.complete_job(db, run["job_id"], {"accepted_messages": stats["accepted_messages"]})
    elif stats["failed"]:
        if state != "uncertain":
            await db.execute(text("UPDATE subscription_chatbot_runs SET status='failed',finished_at=clock_timestamp() WHERE id=:id"), {"id": run["id"]})
        if run["job_id"]:
            await db.execute(text("""UPDATE subscription_chatbot_jobs SET status='failed',error_code='CHATBOT_DELIVERY_UNCONFIRMED',
                result=CAST(:result AS jsonb),finished_at=clock_timestamp() WHERE id=:id"""),
                {"id": run["job_id"], "result": store.encode({"accepted_messages": stats["accepted_messages"]})})
    return [descriptor(child) for child in children]
