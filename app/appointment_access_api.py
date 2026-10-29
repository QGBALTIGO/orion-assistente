"""Public customer actions reuse the real booking engine and tenant RLS."""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import appointment_access as access
from app.appointment_errors import sanitized_database_errors
from app.appointment_lifecycle import customer_actor, customer_can_change, require_customer_deadline
from app.booking_service import cancel_appointment, reschedule_appointment, read_appointment, list_slots
from app.catalog_api import Actor, authenticate
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.management_api import management_session
from app.panel_auth import _same_origin
from app.public_origins import canonical_public_origin
from app.public_site_api import public_limit

router = APIRouter(tags=['Acompanhamento de agendamentos'])
PRIVATE_HEADERS = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
    'X-Content-Type-Options': 'nosniff', 'X-Robots-Tag': 'noindex, nofollow, noarchive',
    'Content-Security-Policy': "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


class CustomerAction(StrictModel):
    token: str = Field(max_length=80)
    action: Literal['view', 'slots', 'cancel', 'reschedule', 'review', 'accept_change', 'decline_change'] = 'view'
    expected_updated_at: AwareDatetime | None = None
    idempotency_key: UUID | None = None
    proposal_id: UUID | None = None
    day: date | None = None
    start_at: AwareDatetime | None = None
    rating: int | None = Field(default=None, ge=1, le=5, strict=True)
    comment: str | None = Field(default=None, max_length=1000)

    @field_validator('comment')
    @classmethod
    def comment_text(cls, value):
        if value and any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise ValueError('Comentário inválido')
        return value.strip() or None if value is not None else None


async def customer_view(db, identity):
    row = await read_appointment(db, identity.tenant_id, identity.appointment_id)
    tenant = identity.tenant_id
    now = (await db.execute(text('SELECT clock_timestamp()'))).scalar_one()
    shop = (await db.execute(text('''SELECT t.name,p.public_slug,p.public_enabled,p.address,p.city,p.state,
        p.whatsapp_phone,p.contact_phone,p.logo_url,p.theme_color FROM tenants t LEFT JOIN management_site_preferences p
        ON p.tenant_id=t.id WHERE t.id=:t'''), {'t': tenant})).mappings().one()
    completed_at = (await db.execute(text("""SELECT max(occurred_at) FROM appointment_history
        WHERE tenant_id=:t AND appointment_id=:id AND event='completed'
          AND details->>'source'='panel' AND details->'actor'->>'role' IN ('owner','editor','admin','support')"""),
        {'t':tenant,'id':identity.appointment_id})).scalar_one()
    actual_completion=completed_at is not None
    review = (await db.execute(text('''SELECT rating,comment FROM appointment_reviews
        WHERE tenant_id=:t AND appointment_id=:id'''), {'t': tenant, 'id': identity.appointment_id})).mappings().first()
    future = (await db.execute(text('''SELECT EXISTS(SELECT 1 FROM appointments WHERE tenant_id=:t
        AND customer_id=:customer AND id<>:id AND status='confirmed' AND start_at>clock_timestamp())'''),
        {'t': tenant, 'customer': identity.customer_id, 'id': identity.appointment_id})).scalar_one()
    booking_url = (canonical_public_origin()+'/s/'+shop['public_slug']
        if shop.get('public_slug') and shop['public_enabled'] else None)
    services=(await db.execute(text('''SELECT COALESCE(p.base_service_id,i.service_id) AS public_id,
        i.name_snapshot,r.days FROM appointment_items i LEFT JOIN service_packages p
        ON p.tenant_id=i.tenant_id AND p.synthetic_service_id=i.service_id AND p.kind='variant'
        LEFT JOIN appointment_return_intervals r ON r.tenant_id=i.tenant_id
          AND r.service_id=COALESCE(p.base_service_id,i.service_id)
        WHERE i.tenant_id=:t AND i.appointment_id=:id ORDER BY i.item_order'''),
        {'t':tenant,'id':identity.appointment_id})).mappings().all()
    suggestions=[]
    return_enabled=(await db.execute(text('SELECT return_invitation FROM appointment_communication_settings WHERE tenant_id=:t'),{'t':tenant})).scalar_one_or_none()
    if booking_url and row['status']=='completed' and not future:
        from urllib.parse import urlencode
        ids=list(dict.fromkeys(str(item['public_id']) for item in services))
        booking_url+='?'+urlencode({'servicos':','.join(ids),'profissional':str(row['professional_id'])})
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        for item in services:
            if item['days'] and actual_completion and return_enabled:
                target=(completed_at+timedelta(days=item['days'])).astimezone(ZoneInfo(row['timezone'])).date()
                suggestions.append({'service':item['name_snapshot'],'from_date':target.isoformat()})
    pending=(await db.execute(text("""SELECT id,new_start_at,expires_at FROM appointment_change_proposals
        WHERE tenant_id=:t AND appointment_id=:id AND appointment_version=:version
          AND status='pending' AND expires_at>clock_timestamp() ORDER BY created_at DESC LIMIT 1"""),
        {'t':tenant,'id':identity.appointment_id,'version':row['updated_at']})).mappings().first()
    import re
    from app.media_paths import is_media_url
    logo=shop.get('logo_url')
    if not is_media_url(logo) or not logo.startswith('/media/'+str(tenant)+'/'):logo=None
    brand=shop.get('theme_color')
    if not isinstance(brand,str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}',brand):brand='#2e7bf6'
    return {'logo_url':logo,'brand_color':brand,'company': shop['name'], 'customer': row['customer_name'], 'professional': row['professional_name'],
        'status': row['status'], 'protocol': row['protocol'], 'start_at': row['start_at'], 'end_at': row['end_at'],
        'timezone': row['timezone'], 'updated_at': row['updated_at'],
        'services': [i['name_snapshot'] for i in row['items']],
        'duration_min': sum(i['duration_snapshot_min'] for i in row['items']),
        'can_change': customer_can_change(row, now), 'pending_change':dict(pending) if pending and row['status']=='confirmed' else None,
        'can_review': row['status']=='completed' and actual_completion and review is None,
        'review': dict(review) if review else None, 'has_future_booking': future,
        'new_booking_url': booking_url, 'return_suggestions':suggestions, 'contact_phone': shop['whatsapp_phone'] or shop['contact_phone'],
        'address': ', '.join(str(shop[k]) for k in ('address','city','state') if shop.get(k))}


@router.get('/a', include_in_schema=False)
async def customer_page():
    access.require_enabled()
    return FileResponse(Path(__file__).parent/'static'/'orion-appointment.html', headers=PRIVATE_HEADERS)


@router.get('/appointment-assets/{asset}', include_in_schema=False)
async def appointment_asset(asset: str):
    access.require_enabled()
    types = {'orion-appointment.js':'text/javascript', 'orion-appointment.css':'text/css'}
    if asset not in types:
        raise HTTPException(404)
    return FileResponse(Path(__file__).parent/'static'/asset, media_type=types[asset], headers=PRIVATE_HEADERS)


@router.post('/api/appointment-access')
@sanitized_database_errors
async def customer_action(payload: CustomerAction, request: Request):
    access.require_enabled()
    _same_origin(request)
    await public_limit(request, 'appointment-access', 'access-ip', 120, 300)
    tenant, digest = access.parse_token(payload.token)
    access.require_tenant(tenant)
    # Tokens aren't included in rate-limit keys or any HTTP URL.
    await public_limit(request, str(tenant), 'access-token', 60, 300, subject=digest)
    async with get_session_factory()() as db:
        async with tenant_transaction(db, tenant):
            identity = await access.verify(db, tenant, digest)
    with access.bind(identity):
        if payload.action in {'cancel', 'reschedule', 'accept_change'}:
            if payload.expected_updated_at is None or payload.idempotency_key is None:
                raise HTTPException(422, 'Atualize o agendamento antes de confirmar a alteração')
            async with get_session_factory()() as db:
                kwargs = {'context':customer_actor(identity.customer_id, 'public_site'),
                    'expected_updated_at':payload.expected_updated_at, 'idempotency_key':str(payload.idempotency_key)}
                try:
                    if payload.action == 'cancel':
                        await cancel_appointment(db, tenant, identity.appointment_id, **kwargs)
                    else:
                        if payload.action=='accept_change' and payload.proposal_id is None:
                            raise HTTPException(422,'Escolha a proposta atual')
                        if payload.start_at is None and payload.action!='accept_change':
                            raise HTTPException(422, 'Escolha o novo horário')
                        await reschedule_appointment(db, tenant, identity.appointment_id, payload.start_at or datetime.now(timezone.utc),
                            proposal_id=payload.proposal_id if payload.action=='accept_change' else None, **kwargs)
                except IntegrityError:
                    raise HTTPException(409, 'O horário acabou de ser ocupado. Escolha outro horário.') from None
        if payload.action == 'slots':
            if payload.day is None:
                raise HTTPException(422, 'Escolha a data')
            async with get_session_factory()() as db:
                async with tenant_transaction(db, tenant):
                    row = await read_appointment(db, tenant, identity.appointment_id)
                    await require_customer_deadline(db, row, customer_actor(identity.customer_id, 'public_site'))
            async with get_session_factory()() as db:
                result = await list_slots(db, tenant, row['professional_id'],
                    [i['service_id'] for i in row['items']], payload.day, appointment_id=identity.appointment_id)
            return JSONResponse(jsonable_encoder(result), headers=PRIVATE_HEADERS)
        async with get_session_factory()() as db:
            async with tenant_transaction(db, tenant):
                if payload.action=='decline_change':
                    if payload.proposal_id is None:raise HTTPException(422,'Escolha a proposta atual')
                    declined=(await db.execute(text("""UPDATE appointment_change_proposals SET status='declined'
                        WHERE tenant_id=:t AND appointment_id=:id AND id=:proposal AND status='pending'
                          AND expires_at>clock_timestamp() RETURNING id"""),
                        {'t':tenant,'id':identity.appointment_id,'proposal':payload.proposal_id})).scalar_one_or_none()
                    if declined:
                        from app.appointment_notifications import enqueue_event
                        await enqueue_event(db,tenant,identity.appointment_id,'reschedule_declined',
                            {'requested_by':'customer','source':'public_site','previous':{'proposal_id':str(declined)}})
                if payload.action == 'review':
                    if payload.rating is None:
                        raise HTTPException(422, 'Escolha de 1 a 5 estrelas')
                    # Match the engine lock order: professional then appointment.
                    from app.booking_service import acquire_professional_lock
                    pro = (await db.execute(text('SELECT professional_id FROM appointments WHERE tenant_id=:t AND id=:id'),
                        {'t':tenant,'id':identity.appointment_id})).scalar_one()
                    await acquire_professional_lock(db, tenant, pro)
                    row = (await db.execute(text('SELECT status,professional_id FROM appointments WHERE tenant_id=:t AND id=:id FOR UPDATE'),
                        {'t':tenant,'id':identity.appointment_id})).mappings().one()
                    completed_by_team=(await db.execute(text("""SELECT EXISTS(SELECT 1 FROM appointment_history
                        WHERE tenant_id=:t AND appointment_id=:id AND event='completed' AND details->>'source'='panel'
                        AND details->'actor'->>'role' IN ('owner','editor','admin','support'))"""),
                        {'t':tenant,'id':identity.appointment_id})).scalar_one()
                    if row['status'] != 'completed' or not completed_by_team:
                        raise HTTPException(409, 'A avaliação fica disponível após a conclusão do atendimento')
                    inserted = (await db.execute(text('''INSERT INTO appointment_reviews
                        (tenant_id,appointment_id,customer_id,professional_id,rating,comment)
                        VALUES(:t,:id,:customer,:pro,:rating,:comment) ON CONFLICT DO NOTHING RETURNING appointment_id'''),
                        {'t':tenant,'id':identity.appointment_id,'customer':identity.customer_id,
                         'pro':row['professional_id'],'rating':payload.rating,'comment':payload.comment})).scalar_one_or_none()
                    if inserted is None:
                        raise HTTPException(409, 'Sua avaliação já foi recebida. Obrigado!')
                result = await customer_view(db, identity)
    return JSONResponse(jsonable_encoder(result), headers=PRIVATE_HEADERS)


@router.post('/api/v1/tenants/{tenant_id}/appointments/{appointment_id}/revoke-links')
async def revoke_links(tenant_id: UUID, appointment_id: UUID, actor: Actor=Depends(authenticate)):
    access.require_tenant(tenant_id)
    async with management_session(tenant_id, actor, write=True) as db:
        await read_appointment(db, tenant_id, appointment_id)
        await db.execute(text('''UPDATE appointment_access SET revoked_at=clock_timestamp()
            WHERE tenant_id=:t AND appointment_id=:id AND revoked_at IS NULL'''), {'t':tenant_id,'id':appointment_id})
        await db.execute(text('UPDATE appointments SET customer_access_blocked=true WHERE tenant_id=:t AND id=:id'),{'t':tenant_id,'id':appointment_id})
        from app.catalog_api import audit
        await audit(db, tenant_id, 'revoke_appointment_links', 'appointments', appointment_id)
    return {'revoked':True}


@router.get('/api/v1/tenants/{tenant_id}/appointment-reviews')
async def private_reviews(tenant_id: UUID, actor: Actor=Depends(authenticate)):
    access.require_tenant(tenant_id)
    async with management_session(tenant_id, actor) as db:
        items = (await db.execute(text('''SELECT r.rating,r.comment,r.created_at,a.protocol,p.name AS professional
            FROM appointment_reviews r JOIN appointments a ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
            JOIN professionals p ON p.tenant_id=r.tenant_id AND p.id=r.professional_id
            WHERE r.tenant_id=:t ORDER BY r.created_at DESC LIMIT 100'''), {'t':tenant_id})).mappings().all()
    return JSONResponse(jsonable_encoder({'items':[dict(r) for r in items]}), headers=PRIVATE_HEADERS)
