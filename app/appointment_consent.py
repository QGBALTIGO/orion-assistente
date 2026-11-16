"""Explicit permission for one appointment; never grants marketing permission.

Callers supply a trusted staff identity or a verified inbound customer identity.
The public booking form only prepares the customer's WhatsApp statement. A
stored grant is insert-only so a confirmation replay cannot undo a withdrawal.
"""
import hashlib
import json
import unicodedata
from uuid import UUID

from sqlalchemy import text

from app.appointment_message_templates import compose_prefill

PURPOSE_VERSION = 1
STAFF_ROLES = frozenset({'admin', 'support', 'owner', 'editor', 'professional'})


def explicit_customer_statement(body, protocol):
    """Accept the complete opted-in prefill, not a quoted/negated fragment."""
    if not isinstance(body, str) or len(body) > 1500:
        return False
    expected = compose_prefill(protocol, appointment_messages_opt_in=True)

    def normalized(value):
        return ' '.join(unicodedata.normalize('NFC', value).split()).casefold()

    return normalized(body) == normalized(expected)


def staff_consent_actor(context):
    """An API boolean is insufficient without the adapter's staff identity."""
    if (context is None or context.source != 'panel' or
            context.role not in STAFF_ROLES or
            not isinstance(context.account_id, UUID)):
        return None
    source = 'professional_portal' if context.role == 'professional' else 'panel'
    return source, {'role': context.role, 'account_id': str(context.account_id)}


async def grant_scoped_consent(db, tenant, appointment_id, *, source, actor,
                               evidence, customer_phone=None):
    """Record a new explicit grant for the current booking/contact only."""
    if source not in {'panel', 'professional_portal', 'customer_whatsapp'}:
        raise ValueError('invalid_appointment_consent_source')
    if not isinstance(actor, dict) or not isinstance(evidence, str) or not evidence:
        raise ValueError('invalid_appointment_consent_evidence')
    if source == 'customer_whatsapp':
        if actor.get('role') != 'customer' or not customer_phone:
            raise ValueError('verified_customer_consent_required')
    else:
        try:
            UUID(str(actor.get('account_id')))
        except (ValueError, TypeError):
            raise ValueError('authenticated_staff_consent_required') from None
        if actor.get('role') not in STAFF_ROLES:
            raise ValueError('authenticated_staff_consent_required')
    current = (await db.execute(text('''
        SELECT a.customer_id,c.whatsapp_e164 FROM appointments a
        JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE a.tenant_id=:tenant AND a.id=:appointment AND a.status='confirmed'
          AND c.active AND c.deleted_at IS NULL
    '''), {'tenant': tenant, 'appointment': appointment_id})).mappings().first()
    if not current or not current['whatsapp_e164']:
        return False
    phone = current['whatsapp_e164']
    if source == 'customer_whatsapp' and (
            customer_phone != phone or
            str(actor.get('customer_id')) != str(current['customer_id'])):
        return False
    # Share the same lock as explicit contact withdrawals. Neither a racing
    # booking nor an old confirmation receipt can overwrite a revoked row.
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                     {'scope': str(tenant) + ':automation-consent:' + phone})
    inserted = (await db.execute(text('''
        INSERT INTO appointment_message_consents
          (tenant_id,appointment_id,customer_id,contact_e164,operational_opt_in,
           review_opt_in,source,actor,purpose_version,evidence_hash)
        SELECT a.tenant_id,a.id,c.id,c.whatsapp_e164,true,true,:source,
               CAST(:actor AS jsonb),:purpose,:evidence
        FROM appointments a JOIN customers c
          ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        WHERE a.tenant_id=:tenant AND a.id=:appointment AND a.status='confirmed'
          AND c.id=:customer AND c.whatsapp_e164=:phone
          AND c.active AND c.deleted_at IS NULL
        ON CONFLICT (tenant_id,appointment_id) DO NOTHING
        RETURNING appointment_id
    '''), {'tenant': tenant, 'appointment': appointment_id,
           'customer': current['customer_id'], 'phone': phone, 'source': source,
           'actor': json.dumps(actor, sort_keys=True, default=str),
           'purpose': PURPOSE_VERSION,
           'evidence': hashlib.sha256(evidence.encode()).hexdigest()})).scalar_one_or_none()
    return inserted is not None


async def revoke_scoped_consents(db, tenant, phone, scope):
    """Apply a current explicit withdrawal without changing booking facts."""
    if scope not in {'all', 'marketing', 'birthday'}:
        raise ValueError('invalid_appointment_consent_scope')
    if scope == 'birthday':
        return 0
    phone = '+' + phone.removeprefix('+')
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                     {'scope': str(tenant) + ':automation-consent:' + phone})
    result = await db.execute(text('''
        UPDATE appointment_message_consents SET
          operational_opt_in=CASE WHEN :scope='all' THEN false ELSE operational_opt_in END,
          review_opt_in=false,
          updated_at=GREATEST(clock_timestamp(),updated_at+interval '1 microsecond')
        WHERE tenant_id=:tenant AND contact_e164=:phone
          AND (review_opt_in OR (:scope='all' AND operational_opt_in))
    '''), {'tenant': tenant, 'phone': phone, 'scope': scope})
    # Pending notices should show the withdrawal immediately. An attempt already
    # committed as sending is still guarded again before transport authorization.
    await db.execute(text('''
        UPDATE appointment_notifications n SET status='cancelled',
          last_error='customer_opt_out',updated_at=clock_timestamp()
        FROM customers c
        WHERE n.tenant_id=:tenant AND n.recipient_kind='customer'
          AND n.recipient_id=c.id AND c.tenant_id=n.tenant_id
          AND c.whatsapp_e164=:phone AND n.status='pending'
          AND (:scope='all' OR n.event='review')
    '''), {'tenant': tenant, 'phone': phone, 'scope': scope})
    return result.rowcount
