"""Owner-published catalogue and consent-based public bookings.

An opt-in slug resolves authority on the server. Public output is a whitelist;
customer, account and tenant identities never leave the booking boundary.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.booking_service import book_appointment, check_slot, confirm_hold
from app.catalog_api import Actor, authenticate, audit
from app.database import get_session_factory, tenant_transaction
from app.management_api import management_session
from app.management_schemas import CleanModel, normalize_phone
from app.panel_auth import _redis, _same_origin
from app.service_packages_api import selection_contract, ensure_selection_service
from app.tenant_access import resolve_tenant_access

router=APIRouter(tags=["Site público"])
UTC=timezone.utc
STATIC=Path(__file__).parent/'static'
PUBLIC_FIELDS=('title','description','slug','theme_color','logo_url','cover_url','address','city','state','postal_code',
               'contact_phone','whatsapp_phone','instagram_url','maps_url','facebook_url','tiktok_url','external_url',
               'hide_orion_brand','show_prices')
HEADERS={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
         "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"}
_booking_capacity=WeakKeyDictionary()


async def booking_capacity():
    # Each confirmation briefly needs a publication guard and engine connection.
    # Bound nested transactions below the runtime pool's 15-connection capacity
    # so a burst cannot consume every connection with guards waiting on engines.
    loop=asyncio.get_running_loop()
    gate=_booking_capacity.setdefault(loop,asyncio.Semaphore(6))
    try:
        async with asyncio.timeout(1):await gate.acquire()
    except TimeoutError:
        raise HTTPException(503,'A agenda está recebendo muitas solicitações. Tente novamente em instantes',headers={'Retry-After':'2'}) from None
    try:yield
    finally:gate.release()


class Publication(CleanModel):
    expected_version: StrictInt=Field(ge=1)
    enabled: StrictBool


class PublicBooking(CleanModel):
    quote: str=Field(min_length=40,max_length=8000)
    start_at: AwareDatetime
    customer_name: str=Field(min_length=1,max_length=120)
    customer_phone: str=Field(min_length=10,max_length=40)
    confirmed: StrictBool

    @field_validator('customer_phone')
    @classmethod
    def valid_phone(cls,value):
        value=normalize_phone(value)
        if not value:raise ValueError('Informe DDD e telefone')
        return value

    @field_validator('confirmed')
    @classmethod
    def explicit_consent(cls,value):
        if value is not True:raise ValueError('Confirme expressamente o resumo do agendamento')
        return value


def checked_slug(slug):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',slug) or not 3<=len(slug)<=80:
        raise HTTPException(404,'Página indisponível')
    return slug


def signing_key():
    raw=os.getenv('PUBLIC_SITE_SIGNING_KEY') or os.getenv('SUPPORT_API_TOKEN','')
    if len(raw)<32:raise HTTPException(503,'Agendamento temporariamente indisponível')
    return hmac.new(raw.encode(),b'orion-public-quotes:v1',hashlib.sha256).digest()


def issue_quote(slug,professional,day,selection,now=None):
    stamp=int((now or datetime.now(UTC)).timestamp())
    value={'v':1,'slug':slug,'professional':str(professional),'service':str(selection['base_id']),
           'addons':[str(x) for x in selection['addon_ids']], 'day':day.isoformat(),
           'fingerprint':selection['fingerprint'],'expires':stamp+600,'nonce':secrets.token_hex(16)}
    raw=base64.urlsafe_b64encode(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).rstrip(b'=')
    signature=base64.urlsafe_b64encode(hmac.new(signing_key(),raw,hashlib.sha256).digest()).rstrip(b'=')
    return (raw+b'.'+signature).decode()


def read_quote(token,slug,now=None):
    try:
        raw,signature=token.encode().split(b'.')
        expected=base64.urlsafe_b64encode(hmac.new(signing_key(),raw,hashlib.sha256).digest()).rstrip(b'=')
        if not hmac.compare_digest(expected,signature):raise ValueError()
        value=json.loads(base64.urlsafe_b64decode(raw+b'='*((-len(raw))%4)))
        stamp=int((now or datetime.now(UTC)).timestamp())
        if value['v']!=1 or value['slug']!=slug or not re.fullmatch(r'[0-9a-f]{32}',value['nonce']):raise ValueError()
        if not isinstance(value['expires'],int) or value['expires']<=stamp or value['expires']>stamp+600:
            raise HTTPException(409,'O resumo expirou. Selecione novamente o horário')
        value['service']=UUID(value['service']);value['professional']=UUID(value['professional'])
        value['addons']=[UUID(x) for x in value['addons']];value['day']=date.fromisoformat(value['day'])
        return value
    except HTTPException:raise
    except (ValueError,TypeError,KeyError,UnicodeError,json.JSONDecodeError):
        raise HTTPException(422,'Resumo inválido. Consulte os horários novamente') from None


async def public_limit(request,slug,category,limit=120,window=300,subject=None):
    # Honor only the server's peer identity, never a client-supplied forwarding header.
    peer=subject if subject is not None else (request.client.host if request.client else 'unknown')
    key='orion:public-limit:'+category+':'+hashlib.sha256((slug+'|'+peer).encode()).hexdigest()
    cache=_redis()
    try:
        value=await cache.eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return n",1,key,window)
        if int(value)>limit:raise HTTPException(429,'Muitas tentativas. Aguarde alguns minutos',headers={'Retry-After':str(window)})
    except (RedisError,OSError):
        raise HTTPException(503,'Agendamento temporariamente indisponível') from None
    finally:await cache.aclose()


async def resolve_public(slug):
    checked_slug(slug)
    async with get_session_factory()() as db:
        tenant=(await db.execute(text('SELECT public.resolve_published_site(:slug)'),{'slug':slug})).scalar_one_or_none()
    if tenant is None:raise HTTPException(404,'Página indisponível')
    return tenant


async def published_guard(db,tenant,slug):
    row=(await db.execute(text("""SELECT p.*,t.timezone,t.status FROM management_site_preferences p
        JOIN tenants t ON t.id=p.tenant_id WHERE p.tenant_id=:tenant AND p.public_slug=:slug
        AND p.public_enabled AND t.status='active' FOR SHARE OF p"""),{'tenant':tenant,'slug':slug})).mappings().one_or_none()
    if row is None:raise HTTPException(404,'Página indisponível')
    return dict(row)


async def record_public_booking(db,tenant,appointment_id,zone,confirmed_at=None):
    stamp=confirmed_at or datetime.now(UTC)
    inserted=(await db.execute(text("""INSERT INTO public_booking_receipts (tenant_id,appointment_id,created_at)
        VALUES (:tenant,:id,:stamp) ON CONFLICT DO NOTHING RETURNING appointment_id"""),
        {'tenant':tenant,'id':appointment_id,'stamp':stamp})).scalar_one_or_none()
    if inserted:
        day=stamp.astimezone(ZoneInfo(zone)).date()
        await db.execute(text("""INSERT INTO public_site_daily_metrics (tenant_id,day,bookings)
            VALUES (:tenant,:day,1) ON CONFLICT (tenant_id,day)
            DO UPDATE SET bookings=public_site_daily_metrics.bookings+1"""),{'tenant':tenant,'day':day})


async def confirmed_public_replay(db,tenant,quote,payload):
    # Recover an already committed confirmation even if the editable catalogue
    # changed after the first response was lost. The signed nonce, professional,
    # selected instant and normalized contact must still match this operation.
    row=(await db.execute(text("""SELECT a.id,a.professional_id,a.start_at,a.end_at,a.status,a.protocol,
        a.total_cents,a.updated_at,c.whatsapp_e164,p.name AS professional,t.timezone
        FROM appointments a JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
        JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
        JOIN tenants t ON t.id=a.tenant_id
        WHERE a.tenant_id=:tenant AND a.idempotency_key=:key FOR SHARE OF a"""),
        {'tenant':tenant,'key':'public:'+quote['nonce']})).mappings().one_or_none()
    if row is None:return None
    if (row['professional_id']!=quote['professional'] or row['start_at']!=payload.start_at
        or not hmac.compare_digest(row['whatsapp_e164'] or '',payload.customer_phone)):
        raise HTTPException(409,'Este resumo já foi utilizado em outro agendamento')
    if row['status']=='hold':return None
    if row['status']!='confirmed':raise HTTPException(409,'Este agendamento já foi alterado. Entre em contato com a barbearia')
    items=(await db.execute(text('SELECT name_snapshot,catalog_snapshot FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=:id ORDER BY item_order'),
                            {'tenant':tenant,'id':row['id']})).mappings().all()
    services=[]
    for item in items:
        snapshot=item['catalog_snapshot'] or {}
        if snapshot.get('kind')=='variant':services.extend(x['name'] for x in snapshot.get('items',[]))
        else:services.append(item['name_snapshot'])
    await record_public_booking(db,tenant,row['id'],row['timezone'],row['updated_at'])
    return {'status':row['status'],'protocol':row['protocol'],'start_at':row['start_at'],'end_at':row['end_at'],
            'price_cents':row['total_cents'],'timezone':row['timezone'],'professional':row['professional'],'services':services}


async def catalogue(db,tenant,*,preview=False):
    site=(await db.execute(text('SELECT * FROM management_site_preferences WHERE tenant_id=:tenant'),{'tenant':tenant})).mappings().one_or_none()
    shop=(await db.execute(text('SELECT name,timezone,booking_horizon_days FROM tenants WHERE id=:tenant'),{'tenant':tenant})).mappings().one()
    source = site if preview else ((site or {}).get('public_snapshot') or {})
    preference={key:source.get(key) for key in PUBLIC_FIELDS} if source else {key:None for key in PUBLIC_FIELDS}
    preference.update(title=preference['title'] or shop['name'],theme_color=preference['theme_color'] or '#2e7bf6',
                      show_prices=True if preference['show_prices'] is None else preference['show_prices'])
    professionals=(await db.execute(text("SELECT id,name,specialty,color,image_url FROM professionals WHERE tenant_id=:tenant AND active AND accepts_appointments ORDER BY name,id LIMIT 200"),{'tenant':tenant})).mappings().all()
    services=(await db.execute(text("""SELECT s.id,s.name,s.description,s.category,s.color,s.image_url,s.sort_order,s.duration_min,s.price_cents,s.catalog_kind,
        COALESCE(p.featured,false) AS featured,p.id AS package_id
        FROM services s LEFT JOIN service_packages p ON p.tenant_id=s.tenant_id AND p.synthetic_service_id=s.id AND p.kind='combo'
        WHERE s.tenant_id=:tenant AND s.active AND s.catalog_kind IN ('base','combo')
          AND EXISTS (SELECT 1 FROM professional_services ps JOIN professionals pr ON pr.tenant_id=ps.tenant_id AND pr.id=ps.professional_id
            WHERE ps.tenant_id=s.tenant_id AND ps.service_id=s.id AND pr.active AND pr.accepts_appointments)
        ORDER BY COALESCE(p.featured,false) DESC,s.sort_order,s.name,s.id LIMIT 250"""),{'tenant':tenant})).mappings().all()
    links=(await db.execute(text("""SELECT ps.service_id,ps.professional_id FROM professional_services ps
        JOIN professionals p ON p.tenant_id=ps.tenant_id AND p.id=ps.professional_id
        JOIN services s ON s.tenant_id=ps.tenant_id AND s.id=ps.service_id
        WHERE ps.tenant_id=:tenant AND p.active AND p.accepts_appointments AND s.active AND s.catalog_kind<>'variant'"""),{'tenant':tenant})).mappings().all()
    addons=(await db.execute(text("""SELECT a.base_service_id,a.addon_service_id,a.add_duration,a.sort_order,s.name,s.price_cents,s.duration_min
        FROM service_addons a JOIN services s ON s.tenant_id=a.tenant_id AND s.id=a.addon_service_id
        WHERE a.tenant_id=:tenant AND s.active ORDER BY a.base_service_id,a.sort_order"""),{'tenant':tenant})).mappings().all()
    public_services=[]
    for row in services:
        item={key:value for key,value in row.items() if key!='package_id'}
        item['professional_ids']=[x['professional_id'] for x in links if x['service_id']==row['id']]
        item['addons']=[{**{key:value for key,value in x.items() if key!='base_service_id'},
                         'professional_ids':[link['professional_id'] for link in links if link['service_id']==x['addon_service_id']]}
                        for x in addons if x['base_service_id']==row['id']]
        if row['package_id']:
            item['components']=[dict(x) for x in (await db.execute(text("""SELECT s.name,s.duration_min,i.item_order
                FROM service_package_items i JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
                WHERE i.tenant_id=:tenant AND i.package_id=:package ORDER BY i.item_order"""),
                {'tenant':tenant,'package':row['package_id']})).mappings().all()]
        public_services.append(item)
    return {'site':preference,'services':public_services,'professionals':[dict(x) for x in professionals],
            'timezone':shop['timezone'],'booking_horizon_days':shop['booking_horizon_days'],
            'today':datetime.now(ZoneInfo(shop['timezone'])).date().isoformat(),'preview':preview,
            'published':bool(site and site['public_enabled']), 'booking_enabled':not preview and bool(site and site['public_enabled'])}


@router.get('/api/v1/public/sites/{slug}')
async def public_catalogue(slug:str,request:Request):
    await public_limit(request,checked_slug(slug),'catalogue')
    tenant=await resolve_public(slug)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await published_guard(db,tenant,slug)
            return await catalogue(db,tenant)


@router.get('/api/v1/tenants/{tenant_id}/management/site/preview')
async def preview_catalogue(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    async with management_session(tenant_id,actor) as db:return await catalogue(db,tenant_id,preview=True)


@router.post('/api/v1/tenants/{tenant_id}/management/site/publication')
async def publish_site(tenant_id:UUID,payload:Publication,actor:Actor=Depends(authenticate)):
    access=await resolve_tenant_access(actor,tenant_id,'publish')
    if access.role!='owner':raise HTTPException(403,'Somente o proprietário pode publicar ou retirar este site')
    if payload.enabled:
        signing_key()
        cache=_redis()
        try:await cache.ping()
        except (RedisError,OSError):raise HTTPException(503,'Agendamento temporariamente indisponível') from None
        finally:await cache.aclose()
    try:
      async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant_id):
            existing=(await db.execute(text('SELECT * FROM management_site_preferences WHERE tenant_id=:tenant FOR UPDATE'),{'tenant':tenant_id})).mappings().one_or_none()
            if not existing or existing['version']!=payload.expected_version:raise HTTPException(409,'As preferências mudaram. Salve e atualize antes de publicar')
            if payload.enabled:
                if not existing['slug'] or not existing['title'].strip():raise HTTPException(422,'Defina o nome público e o endereço do site antes de publicar')
                content=await catalogue(db,tenant_id,preview=True)
                hours=(await db.execute(text("""SELECT 1 FROM working_hours h JOIN professionals p ON p.tenant_id=h.tenant_id AND p.id=h.professional_id
                    WHERE h.tenant_id=:tenant AND p.active AND p.accepts_appointments LIMIT 1"""),{'tenant':tenant_id})).scalar_one_or_none()
                if not content['services'] or not hours:raise HTTPException(422,'Cadastre serviços, profissionais e horários para abrir o agendamento')
            version=(await db.execute(text("""UPDATE management_site_preferences SET public_enabled=:enabled,
                public_enabled_at=CASE WHEN :enabled THEN now() ELSE public_enabled_at END,
                public_slug=CASE WHEN :enabled THEN :slug ELSE public_slug END,
                public_snapshot=CASE WHEN :enabled THEN CAST(:snapshot AS jsonb) ELSE public_snapshot END,
                version=version+1,updated_at=now() WHERE tenant_id=:tenant RETURNING version"""),
                {'tenant':tenant_id,'enabled':payload.enabled,'slug':existing['slug'],
                 'snapshot':json.dumps({key:existing[key] for key in PUBLIC_FIELDS})})).scalar_one()
            await audit(db,tenant_id,'site_published' if payload.enabled else 'site_unpublished','management_site_preferences',tenant_id)
            return {'published':payload.enabled,'public_enabled':payload.enabled,'version':version,
                    'public_url':'/s/'+existing['slug'] if payload.enabled else None,'preview_url':f'/app/site-preview/{tenant_id}',
                    'has_unpublished_changes':False}
    except IntegrityError:
        raise HTTPException(409,'Este endereço público já está em uso. Escolha outro endereço e salve') from None


@router.get('/api/v1/public/sites/{slug}/availability')
async def public_availability(slug:str,request:Request,service_id:UUID,professional_id:UUID,day:date,
                              addon_ids:list[UUID]=Query(default=[])):
    await public_limit(request,checked_slug(slug),'availability',60,300)
    tenant=await resolve_public(slug)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            await published_guard(db,tenant,slug)
            selection=await selection_contract(db,tenant,service_id,addon_ids,professional_id)
            zone=ZoneInfo(selection['shop']['timezone']);now=datetime.now(UTC);local_now=now.astimezone(zone)
            if day<local_now.date() or day>local_now.date()+timedelta(days=selection['shop']['booking_horizon_days']):
                raise HTTPException(422,'Data fora do período disponível')
            slots=[]
            for minute in range(0,1440,selection['shop']['slot_step_minutes']):
                naive=datetime.combine(day,time.min)+timedelta(minutes=minute)
                start=naive.replace(tzinfo=zone).astimezone(UTC).astimezone(zone)
                if start.replace(tzinfo=None)!=naive:continue
                try:
                    await check_slot(db,tenant,professional_id,selection['shop'],[selection['aggregate']],start,now)
                    slots.append(start.isoformat())
                except HTTPException as exc:
                    if exc.status_code not in {409,422}:raise
            quote=issue_quote(slug,professional_id,day,selection,now) if slots else None
            return {'slots':slots,'timezone':selection['shop']['timezone'],'price_cents':selection['aggregate']['price_cents'],
                    'duration_min':selection['aggregate']['duration_min'],'quote':quote,'expires_in':600 if quote else None,
                    'services':[x['name'] for x in selection['items']]}


@router.post('/api/v1/public/sites/{slug}/appointments',status_code=201)
async def public_book(slug:str,payload:PublicBooking,request:Request,_capacity=Depends(booking_capacity)):
    _same_origin(request)
    checked_slug(slug)
    quote=read_quote(payload.quote,slug)
    await public_limit(request,slug,'booking-ip',12,3600)
    await public_limit(request,slug,'booking-contact',4,3600,subject=payload.customer_phone)
    tenant=await resolve_public(slug)
    hold=None
    try:
        # Prepare a managed variant in its own transaction so the unchanged
        # engine can read it. No customer or appointment exists at this stage.
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                await published_guard(db,tenant,slug)
                replay=await confirmed_public_replay(db,tenant,quote,payload)
                if replay is not None:return replay
                selection=await selection_contract(db,tenant,quote['service'],quote['addons'],quote['professional'])
                if not hmac.compare_digest(selection['fingerprint'],quote['fingerprint']):raise HTTPException(409,'Serviços ou preços mudaram. Confira um novo resumo')
                engine_service=await ensure_selection_service(db,tenant,selection)
        # Hold the publication and source contract stable while the engine
        # performs its own locked hold/confirmation transactions.
        async with get_session_factory()() as guard:
            async with tenant_transaction(guard,tenant):
                await published_guard(guard,tenant,slug)
                selection=await selection_contract(guard,tenant,quote['service'],quote['addons'],quote['professional'],lock=True)
                if not hmac.compare_digest(selection['fingerprint'],quote['fingerprint']):raise HTTPException(409,'Serviços ou preços mudaram. Confira um novo resumo')
                zone=ZoneInfo(selection['shop']['timezone'])
                if payload.start_at.astimezone(zone).date()!=quote['day']:raise HTTPException(422,'O horário não pertence ao dia escolhido')
                async with get_session_factory()() as engine:
                    hold=await book_appointment(engine,tenant,quote['professional'],[engine_service],payload.customer_phone,
                                                payload.customer_name,payload.start_at,'public:'+quote['nonce'],source='public_site')
                    result=await confirm_hold(engine,tenant,hold['id'],customer_phone=payload.customer_phone,source='public_site')
                await record_public_booking(guard,tenant,result['id'],selection['shop']['timezone'])
                return {'status':result['status'],'protocol':result['protocol'],'start_at':result['start_at'],'end_at':result['end_at'],
                        'price_cents':result['total_cents'],'timezone':selection['shop']['timezone'],
                        'professional':selection['shop']['professional_name'],'services':[x['name'] for x in selection['items']]}
    except (HTTPException,IntegrityError) as exc:
        # A hold left by a failed confirmation expires through the engine's
        # normal five-minute lease. Never race a parallel confirmed replay with
        # a read-then-cancel cleanup in this anonymous endpoint.
        if isinstance(exc,IntegrityError):raise HTTPException(409,'O horário acabou de ser reservado. Escolha outro horário') from None
        raise


@router.get('/api/v1/tenants/{tenant_id}/management/site/metrics')
async def site_metrics(tenant_id:UUID,days:int=Query(default=30,ge=1,le=366),actor:Actor=Depends(authenticate)):
    async with management_session(tenant_id,actor) as db:
        zone=(await db.execute(text('SELECT timezone FROM tenants WHERE id=:tenant'),{'tenant':tenant_id})).scalar_one()
        end=datetime.now(ZoneInfo(zone)).date();start=end-timedelta(days=days-1)
        rows=(await db.execute(text('SELECT day,visits,bookings FROM public_site_daily_metrics WHERE tenant_id=:tenant AND day BETWEEN :start AND :end ORDER BY day'),
                              {'tenant':tenant_id,'start':start,'end':end})).mappings().all()
        by_day={x['day']:x for x in rows};daily=[]
        for offset in range(days):
            day=start+timedelta(days=offset);row=by_day.get(day,{'visits':0,'bookings':0})
            daily.append({'day':day,'visits':row['visits'],'bookings':row['bookings']})
        visits=sum(x['visits'] for x in daily);bookings=sum(x['bookings'] for x in daily)
        start_at=datetime.combine(start,time.min,tzinfo=ZoneInfo(zone));end_at=datetime.combine(end+timedelta(days=1),time.min,tzinfo=ZoneInfo(zone))
        clients=(await db.execute(text("""SELECT count(DISTINCT a.customer_id) FROM public_booking_receipts r
            JOIN appointments a ON a.tenant_id=r.tenant_id AND a.id=r.appointment_id
            WHERE r.tenant_id=:tenant AND r.created_at>=:start AND r.created_at<:end"""),
            {'tenant':tenant_id,'start':start_at,'end':end_at})).scalar_one()
        return {'start_date':start,'end_date':end,'timezone':zone,'visits':visits,'site_bookings':bookings,
                'clients_booked':clients,'conversion_percent':round(bookings/visits*100,2) if visits else 0,'daily':daily,
                'definition':'Aberturas da página e agendamentos confirmados pelo site público; prévias não entram na contagem.'}


@router.get('/s/{slug}',include_in_schema=False)
async def public_page(slug:str,request:Request):
    await public_limit(request,checked_slug(slug),'page')
    tenant=await resolve_public(slug)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            site=await published_guard(db,tenant,slug)
            await db.execute(text("""INSERT INTO public_site_daily_metrics (tenant_id,day,visits)
                VALUES (:tenant,CAST(now() AT TIME ZONE :zone AS date),1)
                ON CONFLICT (tenant_id,day) DO UPDATE SET visits=public_site_daily_metrics.visits+1"""),{'tenant':tenant,'zone':site['timezone']})
    return FileResponse(STATIC/'orion-public.html',headers=HEADERS)


@router.get('/app/site-preview/{tenant_id}',include_in_schema=False)
async def preview_page(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    await resolve_tenant_access(actor,tenant_id,'read')
    return FileResponse(STATIC/'orion-public.html',headers={**HEADERS,'X-Robots-Tag':'noindex, nofollow'})


@router.get('/public-assets/{asset}',include_in_schema=False)
async def public_asset(asset:str):
    allowed={'orion-public.css':'text/css','orion-public.js':'text/javascript'}
    if asset not in allowed:raise HTTPException(404)
    return FileResponse(STATIC/asset,media_type=allowed[asset],headers={'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'})
