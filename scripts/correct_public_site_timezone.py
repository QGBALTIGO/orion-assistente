"""Reviewed timezone correction, preserving every persisted appointment byte.

Call correct_timezone inside a caller-owned transaction, after a verified backup
and migration to 0038. It neither changes appointment instants nor emits messages
or calendar jobs. The caller must commit only after checking the returned receipt.
There is deliberately no environment-driven automatic production execution.
"""
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text


def appointment_digest(db,tenant):
    return db.execute(text("""SELECT count(*),md5(COALESCE(
        string_agg(md5(to_jsonb(a)::text),'' ORDER BY id),''))
        FROM appointments a WHERE tenant_id=:tenant"""),{'tenant':tenant}).one()


def correct_timezone(db,*,tenant:UUID,slug:str,expected_zone:str,target_zone:str,now=None):
    if not isinstance(tenant,UUID) or not db.in_transaction():
        raise ValueError('A verified tenant and caller-owned transaction are required')
    ZoneInfo(expected_zone);ZoneInfo(target_zone)
    stamp=now or datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('An aware review instant is required')
    # Same order as the engine: professional scope, then publication/source
    # rows. This drains current bookings before changing their local clock.
    professionals=db.execute(text('SELECT id FROM professionals WHERE tenant_id=:tenant ORDER BY id'),{'tenant':tenant}).scalars().all()
    for professional in professionals:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                   {'scope':str(tenant)+':'+str(professional)})
    site=db.execute(text("""SELECT t.timezone,p.city,p.state FROM tenants t
        JOIN management_site_preferences p ON p.tenant_id=t.id
        JOIN public_site_slugs s ON s.tenant_id=t.id
        WHERE t.id=:tenant AND s.slug=:slug AND s.kind='canonical'
        FOR UPDATE OF p,t"""),{'tenant':tenant,'slug':slug}).mappings().one_or_none()
    if site is None or site['timezone'] not in {expected_zone,target_zone}:
        raise ValueError('Timezone correction scope changed')
    if site['state']!='MG' or str(site['city']).strip().casefold()!='montes claros' or target_zone!='America/Sao_Paulo':
        raise ValueError('This reviewed correction is only for Montes Claros, MG')
    before=appointment_digest(db,tenant)
    if site['timezone']==target_zone:
        return {'changed':False,'appointments':before[0],'appointments_digest':before[1],'reviews_added':0,
                'policy':'preserve_instants','from_timezone':expected_zone,'to_timezone':target_zone}
    reviewed=db.execute(text("""INSERT INTO public_site_timezone_reviews
        (tenant_id,appointment_id,from_timezone,to_timezone,original_start_at)
        SELECT tenant_id,id,:old,:new,start_at FROM appointments
        WHERE tenant_id=:tenant AND status IN ('confirmed','hold') AND start_at>=:stamp
        ON CONFLICT DO NOTHING RETURNING appointment_id"""),
        {'tenant':tenant,'old':expected_zone,'new':target_zone,'stamp':stamp}).scalars().all()
    db.execute(text('UPDATE tenants SET timezone=:zone WHERE id=:tenant AND timezone=:old'),
               {'tenant':tenant,'zone':target_zone,'old':expected_zone})
    after=appointment_digest(db,tenant)
    if after!=before:
        raise RuntimeError('Appointment preservation check failed; rollback required')
    return {'changed':True,'appointments':before[0],'appointments_digest':before[1],'reviews_added':len(reviewed),
            'policy':'preserve_instants','from_timezone':expected_zone,'to_timezone':target_zone}
