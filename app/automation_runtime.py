"""Durable, bounded automations using existing domain records and consents.

An attempt is committed before external I/O. Unknown delivery is never retried
automatically. Disabled code does not touch the new schema or any transport.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from app.automation_policy import AUDIENCE_SQL, MARKETING_EVENTS, OPERATIONAL_EVENTS, delivery_reason, render_template, schedule_for, send_window
from app.automation_policy import schedule_explanation,consent_version
from app.automation_templates import compose
from app.communication_style import presentation_metadata
from app.catalog_api import support_session_factory
from app.database import get_session_factory, tenant_transaction


def execution_enabled():
    return os.getenv("MARKETING_EXECUTION_ENABLED", "0") == "1"


def reconciliation_enabled():
    return os.getenv("MARKETING_RECONCILIATION_ENABLED", "0") == "1"


def execution_authorized(settings):
    return bool(settings and settings.get("enabled") and settings.get("owner_active")
                and settings.get("tenant_status") == "active" and settings.get("preferences_approved")
                and not settings.get("vacancy_requested"))


# Both consumers refer to one predicate; no separate approval implementation.
execution_approved = execution_authorized


def _notice_identity(tenant, phone, identity):
    """Validate a server identity without granting assistant/domain authority."""
    from app.qr_channels import ChannelIdentity, normalize_sender
    if (os.getenv("QR_CHANNELS_ENABLED", "0") != "1" or not isinstance(identity, ChannelIdentity)
            or identity.tenant_id != tenant or identity.provider != "baileys" or identity.allowed_sender is not True
            or type(identity.fence) is not int or not 1 <= identity.fence <= 2**63-1):
        raise HTTPException(409, "Autorização do canal mudou")
    extra={}
    try:
        sender = normalize_sender(phone)
    except ValueError:
        from app import qr_legacy
        sender=phone.removeprefix('+') if isinstance(phone,str) else ''
        # Only authenticated_channel can supply the HMAC-verified self binding.
        # Keep ordinary contacts and channel allowlist validation unchanged.
        if (not identity.legacy_transport or not identity.central_adopted or not qr_legacy.enabled()
            or sender!=identity.verified_self_phone or sender!=os.getenv('APPOINTMENT_COMMUNICATIONS_PILOT_PHONE','')
            or not re.fullmatch(r'55[1-9][0-9][2-9][0-9]{7}',sender)
            or tenant!=qr_legacy.configured_tenant() or not qr_legacy.is_alias(tenant,identity.channel_id)
            or not isinstance(identity.preserved_session_fingerprint,str)
            or not re.fullmatch(r'[a-f0-9]{64}',identity.preserved_session_fingerprint)
            or sender!=qr_legacy.verified_self_phone({'tenant_id':tenant,'id':identity.channel_id,
                'request_fingerprint':identity.preserved_session_fingerprint})):
            raise HTTPException(403, "Remetente não autorizado neste canal") from None
        extra={'session_proof':identity.preserved_session_fingerprint}
    return {"tenant": tenant, "channel": identity.channel_id, "account": identity.owner_account_id,
            "owner": identity.lease_owner, "fence": identity.fence, "sender": sender,**extra}


async def _notice_guard(db, values):
    function='orion_preserved_notice_guard' if 'session_proof' in values else 'automation_notice_guard'
    proof=',CAST(:session_proof AS text)' if 'session_proof' in values else ''
    if os.getenv("QR_CHANNELS_ENABLED", "0") != "1" or (await db.execute(text("SELECT public."+function+"""(
        CAST(:tenant AS uuid),CAST(:channel AS uuid),CAST(:account AS uuid),
        CAST(:owner AS uuid),CAST(:fence AS bigint),CAST(:sender AS text)"""+proof+")"), values)).scalar_one() is not True:
        raise HTTPException(409, "Autorização do canal mudou")


async def interaction(tenant, phone, *, human=None, channel_identity=None):
    if not execution_enabled():
        return
    notice = _notice_identity(tenant, phone, channel_identity) if channel_identity is not None else None
    if not phone.startswith("+"):
        phone = "+" + phone
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            if notice is not None:
                await _notice_guard(db, notice)
            await db.execute(text("""INSERT INTO automation_contact_state(tenant_id,contact_e164,human_paused)
              VALUES(:t,:phone,COALESCE(:human,false)) ON CONFLICT(tenant_id,contact_e164) DO UPDATE
              SET revision=automation_contact_state.revision+1,last_interaction_at=now(),
                  human_paused=COALESCE(:human,automation_contact_state.human_paused)"""),
              {"t": tenant, "phone": phone, "human": human})
            await db.execute(text("""UPDATE automation_jobs SET status='cancelled',last_error='conversation_changed',updated_at=now()
              WHERE tenant_id=:t AND contact_e164=:phone AND status='pending'"""), {"t": tenant, "phone": phone})
            if notice is not None:
                # Membership/channel/tenant SHARE locks stay on this same
                # transaction. Expiry while waiting/writing rolls it all back.
                await _notice_guard(db, notice)


async def settings_for(db, tenant, *, lock=False):
    row = (await db.execute(text("""SELECT x.*,t.timezone,t.name AS business_name,t.status AS tenant_status,
      public.automation_owner_active(x.tenant_id,x.approved_by,x.approved_auth_version) AS owner_active,
      p.version AS preferences_version,coalesce(p.version=x.reviewed_preferences_version,false) AS preferences_approved,
      coalesce((p.preferences->'automations'->>'vacancy_requested')='true',false) AS vacancy_requested,
      coalesce(p.preferences->'automations'->'delivery_policy','{}'::jsonb) AS delivery_policy
      FROM automation_execution x JOIN tenants t ON t.id=x.tenant_id
      LEFT JOIN management_preferences p ON p.tenant_id=x.tenant_id WHERE x.tenant_id=:t""" +
      (" FOR UPDATE OF x" if lock else "")), {"t": tenant})).mappings().one_or_none()
    return dict(row) if row else None


async def contact_facts(db, tenant, phone):
    consent = (await db.execute(text("SELECT * FROM contact_consents WHERE tenant_id=:t AND contact_e164=:phone"),
        {"t": tenant, "phone": phone})).mappings().one_or_none()
    state = (await db.execute(text("SELECT * FROM automation_contact_state WHERE tenant_id=:t AND contact_e164=:phone"),
        {"t": tenant, "phone": phone})).mappings().one_or_none()
    handoff = (await db.execute(text("""SELECT 1 FROM human_handoffs WHERE tenant_id=:t AND contact_e164=:phone
      AND status='active' AND expires_at>now() LIMIT 1"""), {"t": tenant, "phone": phone})).first()
    from app.inbox_control import enabled as inbox_enabled
    inbox_handoff = False
    if inbox_enabled():
        inbox_handoff = (await db.execute(text("""SELECT 1 FROM inbox_controls WHERE tenant_id=:t
            AND contact_e164=:phone AND mode<>'ai' LIMIT 1"""), {"t": tenant, "phone": phone})).first()
    if handoff or inbox_handoff:
        state = {**dict(state or {}), "human_paused": True, "revision": state["revision"] if state else 0}
    return dict(consent) if consent else None, dict(state) if state else None


async def queue_notification(db, tenant, settings, preference, customer, event, event_key, facts, labels, *, campaign_id=None):
    if not execution_enabled() or not execution_authorized(settings):
        return False
    if event not in OPERATIONAL_EVENTS | MARKETING_EVENTS:
        return False
    now = datetime.now(timezone.utc)
    rules = (preference.get("preferences") or {}).get("automations", {})
    rule = next((r for r in rules.get("rules", []) if r["event"] == event and r.get("requested_enabled")), None)
    if event == "campaign":
        template = facts.get("message")
    elif rule:
        template = rule["template"]
    else:
        return False
    consent, contact = await contact_facts(db, tenant, customer["whatsapp_e164"])
    required = 'birthday_opt_in' if event.startswith('birthday_') else "operational_opt_in" if event in OPERATIONAL_EVENTS else "marketing_opt_in"
    scheduled,stop = schedule_explanation(event,facts,now,settings['timezone'],policy=rules.get('delivery_policy'),
        recovery_days=rules.get('recovery_days',60),approved_at=settings.get('approved_at') if event!='campaign' else None)
    if stop=='before_activation':return False
    if scheduled is None:
        if stop=='quiet_window_expired':await pending_notice(db,tenant,customer['whatsapp_e164'],event,event_key,facts,stop)
        return False
    if contact and contact.get('human_paused'):
        await pending_notice(db,tenant,customer['whatsapp_e164'],event,event_key,facts,'human_paused')
        return False
    if not consent or not consent.get(required):return False
    try:
        if 'style' not in settings:
            from app.communication_style import style_for
            settings={**settings,'style':await style_for(db,tenant)}
        prepared=compose(event,rule or {'template':template},labels,
            business=(preference.get('preferences') or {}).get('business',{}),style=settings.get('style'),public_origin=os.getenv('PANEL_PUBLIC_URL'))
        body=prepared.body
    except (ValueError, KeyError):
        await pending_notice(db,tenant,customer['whatsapp_e164'],event,event_key,facts,'missing_or_unapproved_fact')
        return False  # Missing facts remain missing; no fabricated link/benefit.
    result = await db.execute(text("""INSERT INTO automation_jobs(tenant_id,contact_e164,event,event_key,campaign_id,campaign_version,customer_id,customer_version,channel_id,
      appointment_id,appointment_version,coupon_id,coupon_version,trigger_at,settings_version,preferences_version,consent_version,interaction_revision,body,due_at,expires_at,presentation)
      VALUES(:t,:phone,:event,:key,:campaign,:campaign_version,:customer,:customer_version,:channel,:appointment,:appointment_version,:coupon,:coupon_version,:trigger,:settings,:preferences,:consent,:interaction,:body,:due,:expiry,CAST(:presentation AS jsonb))
      ON CONFLICT(tenant_id,event_key,contact_e164) DO NOTHING RETURNING id"""),
      {"t": tenant, "phone": customer["whatsapp_e164"], "event": event, "key": event_key,
       "campaign": campaign_id, "campaign_version": facts.get("campaign_version") if campaign_id else None,
       "customer": customer["id"], "customer_version": customer["version"],
       "channel": settings["channel_id"], "appointment": facts.get("appointment_id"), "appointment_version": facts.get("updated_at"),
       "coupon": facts.get("coupon_id"), "coupon_version": facts.get("coupon_version"),
       "trigger": facts.get("occurred_at"),
       "settings": settings["version"], "preferences": preference["version"], "consent": consent_version(consent,event),
       "interaction": contact["revision"] if contact else 0, "body": body, "due": scheduled.due_at, "expiry": scheduled.expires_at,
       'presentation':json.dumps(presentation_metadata(prepared,settings))})
    return result.scalar_one_or_none() is not None


def base_labels(settings, customer, preferences):
    business = (preferences.get("preferences") or {}).get("business", {})
    labels = {"cliente": customer["name"], "empresa": settings["business_name"]}
    if business.get("google_review_url"):
        labels["link_review"] = business["google_review_url"]
    return labels


async def pending_notice(db,tenant,phone,event,event_key,facts,reason):
    if event not in OPERATIONAL_EVENTS:return False
    await db.execute(text('''INSERT INTO communication_pending(tenant_id,contact_e164,event,event_key,reason,appointment_id,appointment_version)
      VALUES(:t,:phone,:event,:key,:reason,:appointment,:version) ON CONFLICT(tenant_id,contact_e164,event_key,reason) DO NOTHING'''),
      {'t':tenant,'phone':phone,'event':event,'key':event_key,'reason':reason,
       'appointment':facts.get('appointment_id'),'version':facts.get('updated_at')})
    return True


def appointment_context(settings,customer,preference,appointment):
    labels=base_labels(settings,customer,preference)
    local=appointment['start_at'].astimezone(ZoneInfo(settings['timezone']))
    labels.update(data=local.strftime('%d/%m/%Y'),horario=local.strftime('%Hh%M'),profissional=appointment['professional'],
        servico=appointment['services'] or '',protocolo=appointment['protocol'],
        valor=f"R$ {appointment['total_cents']//100},{appointment['total_cents']%100:02d}")
    return labels


async def plan_tenant(tenant):
    """One bounded page per company. Domain history is the source of triggers."""
    if not execution_enabled():
        return 0
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            settings = await settings_for(db, tenant, lock=True)
            if not execution_authorized(settings):
                return 0
            preference = (await db.execute(text("SELECT preferences,version FROM management_preferences WHERE tenant_id=:t"), {"t": tenant})).mappings().one_or_none()
            if not preference:
                return 0
            from app.communication_style import style_for
            settings['style']=await style_for(db,tenant)
            customers = (await db.execute(text("""SELECT c.* FROM customers c JOIN contact_consents k
              ON k.tenant_id=c.tenant_id AND k.contact_e164=c.whatsapp_e164
              WHERE c.tenant_id=:t AND c.active AND c.deleted_at IS NULL AND (k.marketing_opt_in OR k.operational_opt_in OR k.birthday_opt_in)
              ORDER BY c.id LIMIT 40 OFFSET :cursor"""), {"t": tenant, "cursor": settings["scan_cursor"]})).mappings().all()
            queued = 0
            for customer in customers:
                labels = base_labels(settings, customer, preference)
                public = (await db.execute(text("SELECT public_slug FROM management_site_preferences WHERE tenant_id=:t AND public_enabled"), {"t": tenant})).scalar_one_or_none()
                origin = os.getenv("PANEL_PUBLIC_URL", "").rstrip("/")
                if public and origin.startswith("https://"):
                    labels["link_agendar"] = origin + "/s/" + public
                appointments = (await db.execute(text("""SELECT a.*,p.name AS professional,
                  (SELECT string_agg(i.name_snapshot,', ' ORDER BY i.item_order) FROM appointment_items i
                    WHERE i.tenant_id=a.tenant_id AND i.appointment_id=a.id) AS services,
                  (SELECT max(h.occurred_at) FROM appointment_history h WHERE h.tenant_id=a.tenant_id
                    AND h.appointment_id=a.id AND h.event=a.status) AS occurred_at
                  FROM appointments a JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                  WHERE a.tenant_id=:t AND a.customer_id=:customer AND a.updated_at>now()-interval '100 days'
                  ORDER BY a.updated_at DESC,a.id LIMIT 40"""), {"t": tenant, "customer": customer["id"]})).mappings().all()
                future = bool((await db.execute(text("""SELECT 1 FROM appointments WHERE tenant_id=:t AND customer_id=:customer
                  AND status='confirmed' AND start_at>now() LIMIT 1"""), {"t": tenant, "customer": customer["id"]})).first())
                for a in appointments:
                    local = a["start_at"].astimezone(ZoneInfo(settings["timezone"]))
                    facts = {**dict(a), "appointment_id": a["id"], "has_future_booking": future}
                    known = {**appointment_context(settings,customer,preference,a),**labels}
                    for event in ("booking_confirmed", "rescheduled", "cancelled", "reminder_24h", "reminder_2h", "reminder_1h", "after_service", "cancelled_7d"):
                        if event == "rescheduled":
                            occurred = (await db.execute(text("SELECT max(occurred_at) FROM appointment_history WHERE tenant_id=:t AND appointment_id=:id AND event='rescheduled'"), {"t": tenant, "id": a["id"]})).scalar_one_or_none()
                            event_facts = {**facts, "occurred_at": occurred}
                        else:
                            event_facts = facts
                        origin = event_facts.get("occurred_at") if event in {"booking_confirmed", "rescheduled", "cancelled", "after_service", "cancelled_7d"} else a["updated_at"]
                        key = event + ":" + str(a["id"]) + ":" + (origin.isoformat() if origin else "none")
                        queued += await queue_notification(db, tenant, settings, preference, customer, event, key, event_facts, known)
                completed_at = (await db.execute(text("""SELECT max(h.occurred_at) FROM appointment_history h
                  JOIN appointments a ON a.tenant_id=h.tenant_id AND a.id=h.appointment_id
                  WHERE a.tenant_id=:t AND a.customer_id=:customer AND a.status='completed' AND h.event='completed'"""),
                  {"t": tenant, "customer": customer["id"]})).scalar_one_or_none()
                for event in ("inactive_60d", "birthday_7d", "birthday_2d"):
                    facts = {"occurred_at": completed_at, "birth_date": customer["birth_date"],
                        'birth_month':customer.get('birth_month'),'birth_day':customer.get('birth_day'),"has_future_booking": future}
                    period = (completed_at.isoformat() if completed_at else "none") if event == "inactive_60d" else datetime.now(timezone.utc).astimezone(ZoneInfo(settings["timezone"])).strftime("%Y")
                    key = event + ":" + str(customer["id"]) + ":" + period
                    queued += await queue_notification(db, tenant, settings, preference, customer, event, key, facts, labels)
                coupons = (await db.execute(text("""SELECT id,version,code,issued_at,expires_at FROM loyalty_coupons
                  WHERE tenant_id=:t AND customer_id=:customer AND status='available' AND expires_at>now()
                  ORDER BY issued_at DESC,id LIMIT 10"""), {"t": tenant, "customer": customer["id"]})).mappings().all()
                for coupon in coupons:
                    queued += await queue_notification(db, tenant, settings, preference, customer, "loyalty_reward",
                      "loyalty_reward:" + str(coupon["id"]), {"occurred_at": coupon["issued_at"], "coupon_id": coupon["id"],
                      "coupon_version": coupon["version"]}, {**labels, "cupom": coupon["code"]})
            await db.execute(text("UPDATE automation_execution SET scan_cursor=:cursor,last_scanned_at=now() WHERE tenant_id=:t"),
                {"t": tenant, "cursor": settings["scan_cursor"] + len(customers) if len(customers) == 40 else 0})
            return queued


async def campaign_audience_reason(db, tenant, customer, campaign):
    audience = (campaign or {}).get("audience")
    if audience not in AUDIENCE_SQL:
        return "campaign_paused"
    future = "EXISTS(SELECT 1 FROM appointments f WHERE f.tenant_id=c.tenant_id AND f.customer_id=c.id AND f.status='confirmed' AND f.start_at>now())" if audience == "inactive_customers" else "false"
    row = (await db.execute(text("SELECT (" + AUDIENCE_SQL[audience] + ") AS eligible," + future + " AS has_future "
        "FROM customers c JOIN tenants t ON t.id=c.tenant_id WHERE c.tenant_id=:t AND c.id=:customer AND c.active AND c.deleted_at IS NULL"),
        {"t": tenant, "customer": customer})).mappings().one_or_none()
    if not row or not row["eligible"]:
        return "new_booking" if row and row["has_future"] else "audience_changed"
    return None


async def job_reason(db, tenant, job, settings):
    if 'body' in job:
        from app.automation_templates import verified_presentation
        if not verified_presentation(job):return 'presentation_changed'
    preference = (await db.execute(text("SELECT version FROM management_preferences WHERE tenant_id=:t"), {"t": tenant})).scalar_one_or_none()
    consent, contact = await contact_facts(db, tenant, job["contact_e164"])
    appointment = None
    campaign = None
    if job["appointment_id"]:
        appointment = (await db.execute(text("SELECT status,updated_at,start_at FROM appointments WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": job["appointment_id"]})).mappings().one_or_none()
    if job["campaign_id"]:
        campaign = (await db.execute(text("SELECT status,version,audience FROM campaigns WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": job["campaign_id"]})).mappings().one_or_none()
    if not settings or not settings.get("owner_active") or settings.get("tenant_status") != "active":
        return "approval_revoked"
    if job.get('presentation'):
        from app.communication_style import style_for
        if job['presentation'].get('source_timezone')!=settings['timezone']:return 'configuration_changed'
        style=await style_for(db,tenant)
        if job['presentation'].get('profile_revision')!=style.get('_published_profile_id'):return 'configuration_changed'
    if settings.get("vacancy_requested"):
        return "waitlist_contract_missing"
    if not settings.get("preferences_approved"):
        return "configuration_changed"
    customer = (await db.execute(text("SELECT id,version FROM customers WHERE tenant_id=:t AND whatsapp_e164=:phone AND active AND deleted_at IS NULL"), {"t": tenant, "phone": job["contact_e164"]})).mappings().one_or_none()
    if not customer or customer["id"] != job["customer_id"] or customer["version"] != job["customer_version"]:
        return "customer_unavailable"
    if job["campaign_id"]:
        reason = await campaign_audience_reason(db, tenant, customer["id"], campaign)
        if reason:
            return reason
    if job.get("coupon_id"):
        coupon = (await db.execute(text("SELECT status,version,expires_at FROM loyalty_coupons WHERE tenant_id=:t AND id=:id"),
          {"t": tenant, "id": job["coupon_id"]})).mappings().one_or_none()
        if not coupon or coupon["status"] != "available" or coupon["version"] != job["coupon_version"] or coupon["expires_at"] <= datetime.now(timezone.utc):
            return "coupon_changed"
    if job["event"] in {"inactive_60d", "cancelled_7d", "birthday_7d", "birthday_2d"}:
        future = (await db.execute(text("""SELECT 1 FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
          WHERE a.tenant_id=:t AND c.whatsapp_e164=:phone AND a.status='confirmed' AND a.start_at>now() LIMIT 1"""), {"t": tenant, "phone": job["contact_e164"]})).first()
        if future:
            return "new_booking"
    if job["event"] in {"inactive_60d", "cancelled_7d"}:
        if not job.get("trigger_at"):
            return "trigger_changed"
        completed = (await db.execute(text("""SELECT max(h.occurred_at) FROM appointment_history h
            JOIN appointments a ON a.tenant_id=h.tenant_id AND a.id=h.appointment_id
            WHERE a.tenant_id=:t AND a.customer_id=:customer AND a.status='completed' AND h.event='completed'"""),
            {"t": tenant, "customer": job["customer_id"]})).scalar_one_or_none()
        if (job["event"] == "inactive_60d" and completed != job.get("trigger_at")) or (
                job["event"] == "cancelled_7d" and completed and completed > job.get("trigger_at")):
            return "new_activity"
    return delivery_reason(job, consent, contact, settings, now=datetime.now(timezone.utc), preferences_version=preference,
                           appointment=appointment, campaign=campaign)


async def process_automation_job(ctx, tenant_id, job_id):
    if not execution_enabled():
        return "disabled"
    tenant, identity, token = UUID(tenant_id), UUID(job_id), uuid4()
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            settings = await settings_for(db, tenant, lock=True)
            row = (await db.execute(text("SELECT * FROM automation_jobs WHERE tenant_id=:t AND id=:id AND status='pending' FOR UPDATE SKIP LOCKED"), {"t": tenant, "id": identity})).mappings().one_or_none()
            if not row:
                return "already_claimed"
            job = dict(row)
            reason = await job_reason(db, tenant, job, settings)
            if reason:
                if reason=='conversation_changed' and job.get('event') in OPERATIONAL_EVENTS:
                    _,contact=await contact_facts(db,tenant,job['contact_e164'])
                    if contact and contact.get('human_paused'):
                        await pending_notice(db,tenant,job['contact_e164'],job['event'],job['event_key'],
                            {'appointment_id':job.get('appointment_id'),'updated_at':job.get('appointment_version')},'human_paused')
                if reason != "not_due":
                    await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error=:reason,updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity, "reason": reason})
                return reason
            now = datetime.now(timezone.utc)
            window = send_window(now, settings["timezone"],settings.get('delivery_policy'),job.get('event'))
            if window > now:
                if window >= job["expires_at"]:
                    await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='quiet_window_expired',updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity})
                else:
                    await db.execute(text("UPDATE automation_jobs SET due_at=:due WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity, "due": window})
                return "quiet_hours"
            count = (await db.execute(text("""SELECT count(*) FROM automation_jobs WHERE tenant_id=:t AND attempted_at IS NOT NULL
              AND (attempted_at AT TIME ZONE :zone)::date=(now() AT TIME ZONE :zone)::date"""), {"t": tenant, "zone": settings["timezone"]})).scalar_one()
            if count >= settings["daily_limit"]:
                tomorrow = (now.astimezone(ZoneInfo(settings["timezone"])) + timedelta(days=1)).replace(
                    hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
                if tomorrow >= job["expires_at"]:
                    await db.execute(text("UPDATE automation_jobs SET status='cancelled',last_error='daily_limit_expired',updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity})
                else:
                    await db.execute(text("UPDATE automation_jobs SET due_at=:due WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity, "due": tomorrow})
                return "daily_limit"
            await db.execute(text("UPDATE automation_jobs SET status='sending',claim_token=:token,attempted_at=now(),updated_at=now() WHERE tenant_id=:t AND id=:id"), {"t": tenant, "id": identity, "token": token})
    # A crash from this point leaves an uncertain attempt, never a blind retry.
    result, external, error = "uncertain", None, "delivery_uncertain"
    try:
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                current = await settings_for(db, tenant)
                stop = await job_reason(db, tenant, job, current)
                if stop=='conversation_changed' and job.get('event') in OPERATIONAL_EVENTS:
                    _,contact=await contact_facts(db,tenant,job['contact_e164'])
                    if contact and contact.get('human_paused'):
                        await pending_notice(db,tenant,job['contact_e164'],job['event'],job['event_key'],
                            {'appointment_id':job.get('appointment_id'),'updated_at':job.get('appointment_version')},'human_paused')
        if not execution_enabled():
            stop = "execution_paused"
        if stop:
            result, error = "cancelled", stop
        else:
            sender = ctx.get("automation_sender")
            if sender is None:
                from app.automation_transport import send_automation
                sender = send_automation
            external = await sender(tenant, job["channel_id"], job["contact_e164"], job["body"], str(identity))
            if external != str(identity):
                raise ValueError("unverified_delivery")
            result, error = "sent", None
    except Exception:
        # No exception/provider response is persisted; it can contain credentials.
        external = None
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await db.execute(text("""UPDATE automation_jobs SET status=:status,external_delivery_id=:external,last_error=:error,updated_at=now()
              WHERE tenant_id=:t AND id=:id AND claim_token=:token AND status='sending'"""),
              {"t": tenant, "id": identity, "token": token, "status": result, "external": external, "error": error})
    return result


async def scan_automations(ctx):
    running = execution_enabled()
    if not running and not reconciliation_enabled():
        return "disabled"
    async with support_session_factory()() as db:
        tenants = (await db.execute(text("SELECT tenant_id FROM automation_execution ORDER BY last_scanned_at NULLS FIRST,tenant_id LIMIT 50"))).scalars().all()
    total = 0
    for tenant in tenants:
        if running:
            total += await plan_tenant(tenant)
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                # An interrupted attempt must be reconciled, never resent.
                await db.execute(text("UPDATE automation_jobs SET status='uncertain',last_error='worker_interrupted',updated_at=now() WHERE tenant_id=:t AND status='sending' AND attempted_at<now()-interval '2 minutes'"), {"t": tenant})
                jobs = (await db.execute(text("SELECT id,due_at FROM automation_jobs WHERE tenant_id=:t AND status='pending' AND due_at<=now() ORDER BY due_at,id LIMIT 20"), {"t": tenant})).mappings().all()
                reconciliation = (await db.execute(text("""SELECT id FROM automation_jobs WHERE tenant_id=:t AND status IN ('sent','uncertain')
                  AND delivery_status!='read' AND attempted_at>now()-interval '7 days'
                  AND (last_reconciled_at IS NULL OR last_reconciled_at<now()-interval '5 minutes')
                  ORDER BY last_reconciled_at NULLS FIRST,id LIMIT 20"""), {"t": tenant})).scalars().all()
                await db.execute(text("UPDATE automation_execution SET last_scanned_at=now() WHERE tenant_id=:t"), {"t": tenant})
        for identity in reconciliation:
            await reconcile_automation_job(ctx, str(tenant), str(identity))
        for item in jobs if running else []:
            await ctx["redis"].enqueue_job("process_automation_job", str(tenant), str(item["id"]),
                _job_id="automation:" + str(item["id"]) + ":" + item["due_at"].isoformat())
    return total


async def reconcile_automation_job(ctx, tenant_id, job_id):
    """Read the durable bridge receipt; this operation never sends a message."""
    if not execution_enabled() and not reconciliation_enabled():
        return "disabled"
    tenant, identity = UUID(tenant_id), UUID(job_id)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            row = (await db.execute(text("SELECT channel_id,status FROM automation_jobs WHERE tenant_id=:t AND id=:id AND status IN ('sent','uncertain')"), {"t": tenant, "id": identity})).mappings().one_or_none()
            if not row:
                return "unavailable"
    accepted, delivery = False, "unknown"
    try:
        reader = ctx.get("automation_inspector")
        if reader is None:
            from app.automation_transport import inspect_automation
            reader = inspect_automation
        record = await reader(tenant, row["channel_id"], str(identity))
        accepted = record.get("operation_id") == str(identity) and record.get("accepted") is True and record.get("state") == "accepted"
        if accepted and record.get("delivery") in {"delivered", "read"}:
            delivery = record["delivery"]
    except Exception:
        pass
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            await db.execute(text("""UPDATE automation_jobs SET last_reconciled_at=now(),updated_at=now(),
              status=CASE WHEN :accepted THEN 'sent' ELSE status END,
              external_delivery_id=CASE WHEN :accepted THEN :external ELSE external_delivery_id END,
              delivery_status=CASE WHEN delivery_status='read' THEN 'read' WHEN :delivery='read' THEN 'read'
                WHEN delivery_status='delivered' OR :delivery='delivered' THEN 'delivered' ELSE 'unknown' END,
              last_error=CASE WHEN :accepted THEN NULL ELSE last_error END
              WHERE tenant_id=:t AND id=:id AND status IN ('sent','uncertain')"""),
              {"t": tenant, "id": identity, "external": str(identity), "accepted": accepted, "delivery": delivery})
    return delivery if accepted else "uncertain"
