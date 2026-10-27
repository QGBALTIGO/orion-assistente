"""Read-only owner notices and explicit acknowledgement, never appointment edits."""
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text


async def operational_review(db,tenant,site):
    zone=(await db.execute(text('SELECT timezone FROM tenants WHERE id=:tenant'),{'tenant':tenant})).scalar_one()
    notices=[]
    if site.get('state')=='MG' and datetime.now(ZoneInfo(zone)).utcoffset()!=datetime.now(ZoneInfo('America/Sao_Paulo')).utcoffset():
        notices.append({'code':'timezone_location_mismatch','message':'O fuso configurado difere do horário de Minas Gerais. Confira os horários antes de divulgar a página.'})
    address=str(site.get('address') or '').strip()
    if not address or not any(c.isdigit() for c in address):
        notices.append({'code':'address_needs_review','message':'Confira rua, número ou indicação sem número e o destino exato do mapa. O endereço não foi completado automaticamente.'})
    digits=lambda v:''.join(c for c in str(v or '') if c.isdigit())
    phone,whatsapp=digits(site.get('contact_phone')),digits(site.get('whatsapp_phone'))
    if phone and whatsapp and phone!=whatsapp:
        notices.append({'code':'contact_numbers_differ','message':'Os números de ligação e WhatsApp são diferentes. Confirme com o proprietário se ambos são canais oficiais.'})
    reviews=(await db.execute(text('''SELECT r.appointment_id,r.from_timezone,r.to_timezone,r.original_start_at,
        r.created_at,r.acknowledged_at,a.protocol,a.status FROM public_site_timezone_reviews r
        JOIN appointments a ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
        WHERE r.tenant_id=:tenant AND r.acknowledged_at IS NULL ORDER BY r.original_start_at,r.appointment_id LIMIT 100'''),
        {'tenant':tenant})).mappings().all()
    affected=[]
    for row in reviews:
        affected.append({**dict(row),
            'previous_local_start':row['original_start_at'].astimezone(ZoneInfo(row['from_timezone'])).isoformat(),
            'current_local_start':row['original_start_at'].astimezone(ZoneInfo(row['to_timezone'])).isoformat()})
    count=(await db.execute(text('SELECT count(*) FROM public_site_timezone_reviews WHERE tenant_id=:tenant AND acknowledged_at IS NULL'),
                           {'tenant':tenant})).scalar_one()
    return {'timezone':zone,'notices':notices,'timezone_review_count':count,'timezone_reviews':affected,
            'timezone_policy':'Os instantes gravados das reservas foram preservados. A revisão apenas registra ciência da diferença de horário local.'}
