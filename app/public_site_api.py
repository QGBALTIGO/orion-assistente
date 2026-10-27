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
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import AwareDatetime, Field, StrictBool, StrictInt, field_validator
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.booking_service import book_appointment, confirm_hold, verified_slot_grid
from app.catalog_api import Actor, authenticate, audit
from app.database import get_session_factory, tenant_transaction
from app.management_api import management_session
from app.management_schemas import CleanModel, normalize_phone
from app.panel_auth import _redis, _same_origin
from app.service_packages_api import selection_contract, ensure_selection_service, digest
from app.tenant_access import resolve_tenant_access
from app.public_site_rendering import project_catalogue_visuals, public_html, published_booking_labels
from app.site_address import address_fields, address_display, read_address_fields

router=APIRouter(tags=["Site público"])
UTC=timezone.utc
STATIC=Path(__file__).parent/'static'
PUBLIC_FIELDS=('title','description','slug','theme_color','logo_url','cover_url','address','city','state','postal_code',
               'address_number','address_complement','neighborhood','address_no_number',
               'contact_phone','whatsapp_phone','instagram_url','maps_url','facebook_url','tiktok_url','external_url',
               'hide_orion_brand','show_prices',
               'amenity_wifi','amenity_parking','amenity_accessibility','amenity_children')
HEADERS={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
         "Content-Security-Policy":"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data:; font-src 'self'; connect-src 'self'; frame-src https://www.google.com https://maps.google.com; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"}
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
    restore_previous: StrictBool=False


class PublicBooking(CleanModel):
    quote: str=Field(min_length=40,max_length=8000)
    start_at: AwareDatetime
    customer_name: str=Field(min_length=1,max_length=120)
    customer_phone: str=Field(min_length=10,max_length=40)
    # Optional at the API boundary to preserve existing integrations;
    # the new public booking form collects it as a required field.
    customer_birth_date: date | None=None
    confirmed: StrictBool

    @field_validator('customer_phone')
    @classmethod
    def valid_phone(cls,value):
        value=normalize_phone(value)
        if not value:raise ValueError('Informe DDD e telefone')
        return value

    @field_validator('customer_birth_date')
    @classmethod
    def valid_customer_birth_date(cls,value):
        if value is not None and not date(1900,1,1)<=value<=datetime.now(UTC).date():
            raise ValueError('Data de nascimento inválida')
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


def issue_quote(slug,professional,day,selection,now=None,*,publication_version=None):
    stamp=int((now or datetime.now(UTC)).timestamp())
    multi='service_ids' in selection
    value={'v':2 if multi else 1,'slug':slug,'professional':str(professional),
           'day':day.isoformat(),'fingerprint':selection['fingerprint'],
           'expires':stamp+600,'nonce':secrets.token_hex(16)}
    if publication_version is not None:value['publication_version']=publication_version
    if multi:
        value['services']=[str(x) for x in selection['service_ids']]
    else:
        value['service']=str(selection['base_id'])
        value['addons']=[str(x) for x in selection['addon_ids']]
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
        if value['v'] not in (1,2) or value['slug']!=slug or not re.fullmatch(r'[0-9a-f]{32}',value['nonce']):raise ValueError()
        if 'publication_version' in value and (type(value['publication_version']) is not int or value['publication_version']<1):raise ValueError()
        if not isinstance(value['expires'],int) or value['expires']<=stamp or value['expires']>stamp+600:
            raise HTTPException(409,'O resumo expirou. Selecione novamente o horário')
        if not isinstance(value['fingerprint'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['fingerprint']):
            raise ValueError()
        value['professional']=UUID(value['professional']);value['day']=date.fromisoformat(value['day'])
        if value['v']==1:
            value['service']=UUID(value['service'])
            value['addons']=[UUID(x) for x in value['addons']]
        else:
            ids=value['services']
            if not isinstance(ids,list) or not 2<=len(ids)<=6 or len(set(ids))!=len(ids):
                raise ValueError()
            value['services']=[UUID(x) for x in ids]
            if len(set(value['services']))!=len(value['services']):
                raise ValueError()
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
        JOIN tenants t ON t.id=p.tenant_id WHERE p.tenant_id=:tenant
        AND EXISTS (SELECT 1 FROM public_site_slugs r WHERE r.tenant_id=p.tenant_id
                    AND r.slug=:slug AND r.kind IN ('canonical','alias'))
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


async def store_public_birth_date(db,tenant,appointment_id,birth_date):
    """Fill a missing birthday after confirmation; never rewrite existing PII.

    Only the booked customer's tenant-scoped row may be updated.
    Public requests do not get access to the customer's existing details.
    """
    if birth_date is None:
        return
    await db.execute(text("""
        UPDATE customers AS c
        SET birth_date=:birth_date,version=c.version+1,updated_at=now()
        WHERE c.tenant_id=:tenant AND c.birth_date IS NULL
          AND c.id=(SELECT a.customer_id FROM appointments a
                    WHERE a.tenant_id=:tenant AND a.id=:appointment_id)
    """),{'tenant':tenant,'appointment_id':appointment_id,'birth_date':birth_date})


async def confirmed_public_replay(db,tenant,quote,payload):
    # Recover an already committed confirmation even if the editable catalogue
    # changed after the first response was lost. The signed nonce, professional,
    # selected instant and normalized contact must still match this operation.
    row=(await db.execute(text("""SELECT a.id,a.professional_id,a.start_at,a.end_at,a.status,a.protocol,
        a.total_cents,a.updated_at,a.created_at,c.whatsapp_e164,p.name AS professional,t.timezone
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
    items=(await db.execute(text('SELECT name_snapshot,price_mode_snapshot,catalog_snapshot FROM appointment_items WHERE tenant_id=:tenant AND appointment_id=:id ORDER BY item_order'),
                            {'tenant':tenant,'id':row['id']})).mappings().all()
    services=[]
    for item in items:
        snapshot=item['catalog_snapshot'] or {}
        if snapshot.get('kind')=='variant':services.extend(x['name'] for x in snapshot.get('items',[]))
        else:services.append(item['name_snapshot'])
    # Replays keep the visual contract in force when this booking was created,
    # even after a later publication or an unpublished operational rename.
    if 'publication_version' in quote:
        snapshot=(await db.execute(text("""SELECT snapshot FROM public_site_publications
            WHERE tenant_id=:tenant AND version=:version AND action IN ('publish','restore')"""),
            {'tenant':tenant,'version':quote['publication_version']})).scalar_one_or_none()
    else:
        snapshot=(await db.execute(text("""SELECT snapshot FROM public_site_publications
            WHERE tenant_id=:tenant AND action IN ('publish','restore') AND published_at<=:stamp
            ORDER BY version DESC LIMIT 1"""),{'tenant':tenant,'stamp':row['created_at']})).scalar_one_or_none()
    ids=quote['services'] if quote['v']==2 else [quote['service'],*quote['addons']]
    labels=published_booking_labels({'public_snapshot':snapshot},quote['professional'],[{'id':item} for item in ids])
    await store_public_birth_date(db,tenant,row['id'],payload.customer_birth_date)
    await record_public_booking(db,tenant,row['id'],row['timezone'],row['updated_at'])
    return {'status':row['status'],'protocol':row['protocol'],'start_at':row['start_at'],'end_at':row['end_at'],
            'price_cents':row['total_cents'],
            'price_mode':'from' if any(x['price_mode_snapshot']=='from' for x in items) else 'fixed',
            'timezone':row['timezone'],**(labels or {'professional':row['professional'],'services':services})}


def public_opening_hours(entries):
    """Actual weekly open intervals across active/bookable professionals.

    Subtract each professional's registered working_breaks from that
    professional's shift *before* combining staff availability. This means a
    lunch closure appears only when no available professional covers it.
    A break never affects an unrelated professional or another company.
    Time-off and one-off closures remain checked by the booking engine.
    """
    shifts = {}
    for index, entry in enumerate(entries):
        try:
            weekday = int(entry["weekday"])
            start = str(entry["start_time"])[:5]
            end = str(entry["end_time"])[:5]
        except (KeyError, TypeError, ValueError):
            continue
        if weekday not in range(7) or not (len(start) == len(end) == 5 and start < end):
            continue

        # An outer-joined working hour appears once for every configured break.
        # Coalesce those rows before subtracting pauses; otherwise each copy
        # would independently recreate the full unbroken shift.
        hour_id = entry.get("working_hour_id")
        key = hour_id if hour_id is not None else ("single", index)
        shift = shifts.setdefault(key, {"weekday": weekday,
                                        "start": start, "end": end, "breaks": set()})
        break_start, break_end = entry.get("break_start"), entry.get("break_end")
        if break_start is not None and break_end is not None:
            begin, finish = str(break_start)[:5], str(break_end)[:5]
            if len(begin) == len(finish) == 5 and begin < finish:
                shift["breaks"].add((begin, finish))

    per_day = [[] for _ in range(7)]
    for shift in shifts.values():
        cursor = shift["start"]
        finish = shift["end"]
        for begin, end in sorted(shift["breaks"]):
            begin = max(begin, shift["start"])
            end = min(end, finish)
            if begin >= end:
                continue
            if cursor < begin:
                per_day[shift["weekday"]].append((cursor, begin))
            cursor = max(cursor, end)
        if cursor < finish:
            per_day[shift["weekday"]].append((cursor, finish))

    days = []
    for weekday, intervals in enumerate(per_day):
        merged = []
        for begin, finish in sorted(intervals):
            if merged and begin <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], finish))
            else:
                merged.append((begin, finish))
        days.append({"weekday": weekday,
                     "intervals": [{"open": begin, "close": finish}
                                   for begin, finish in merged]})
    return days


async def catalogue(db,tenant,*,preview=False):
    site=(await db.execute(text('SELECT * FROM management_site_preferences WHERE tenant_id=:tenant'),{'tenant':tenant})).mappings().one_or_none()
    shop=(await db.execute(text('SELECT name,timezone,booking_horizon_days FROM tenants WHERE id=:tenant'),{'tenant':tenant})).mappings().one()
    source = site if preview else ((site or {}).get('public_snapshot') or {})
    if preview:
        source = {**(source or {}), **await read_address_fields(db, tenant)}
    preference={key:source.get(key) for key in PUBLIC_FIELDS} if source else {key:None for key in PUBLIC_FIELDS}
    preference.update(address_fields(preference))
    preference['address_display'] = address_display(preference)
    preference.update(title=preference['title'] or shop['name'],theme_color=preference['theme_color'] or '#2e7bf6',
                      show_prices=True if preference['show_prices'] is None else preference['show_prices'])
    for key in ('amenity_wifi','amenity_parking','amenity_accessibility','amenity_children'):
        preference[key] = preference[key] is True
    professionals=(await db.execute(text("SELECT id,name,specialty,color,image_url FROM professionals WHERE tenant_id=:tenant AND active AND accepts_appointments ORDER BY name,id LIMIT 200"),{'tenant':tenant})).mappings().all()
    working_rows=(await db.execute(text("""
        SELECT h.id AS working_hour_id,h.weekday,h.start_time,h.end_time,
               b.start_time AS break_start,b.end_time AS break_end
        FROM working_hours h
        JOIN professionals p
          ON p.tenant_id=h.tenant_id AND p.id=h.professional_id
        LEFT JOIN working_breaks b
          ON b.tenant_id=h.tenant_id AND b.working_hour_id=h.id
        WHERE h.tenant_id=:tenant AND p.active AND p.accepts_appointments
        ORDER BY h.weekday,h.start_time,h.end_time,b.start_time
    """), {'tenant':tenant})).mappings().all()
    public_hours=public_opening_hours(working_rows)
    services=(await db.execute(text("""SELECT s.id,s.name,s.description,s.category,s.color,s.image_url,s.sort_order,s.duration_min,s.price_cents,s.price_mode,s.catalog_kind,
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
    addons=(await db.execute(text("""SELECT a.base_service_id,a.addon_service_id,a.add_duration,a.sort_order,s.name,s.price_cents,s.price_mode,s.duration_min
        FROM service_addons a JOIN services s ON s.tenant_id=a.tenant_id AND s.id=a.addon_service_id
        WHERE a.tenant_id=:tenant AND s.active ORDER BY a.base_service_id,a.sort_order,a.addon_service_id"""),{'tenant':tenant})).mappings().all()
    # One bounded query for all combinations, instead of one query per combo.
    package_ids=[row['package_id'] for row in services if row['package_id']]
    components=(await db.execute(text("""SELECT i.package_id,i.service_id,s.name,s.duration_min,i.item_order
        FROM service_package_items i JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
        WHERE i.tenant_id=:tenant AND i.package_id=ANY(CAST(:packages AS uuid[]))
        ORDER BY i.package_id,i.item_order"""),{'tenant':tenant,'packages':package_ids})).mappings().all() if package_ids else []
    by_package={}
    for component in components:
        by_package.setdefault(component['package_id'],[]).append({key:value for key,value in component.items() if key!='package_id'})
    by_service={};by_addon={}
    for link in links:by_service.setdefault(link['service_id'],[]).append(link['professional_id'])
    for addon in addons:by_addon.setdefault(addon['base_service_id'],[]).append(addon)
    public_services=[]
    for row in services:
        item={key:value for key,value in row.items() if key!='package_id'}
        item['professional_ids']=by_service.get(row['id'],[])
        item['addons']=[{**{key:value for key,value in x.items() if key!='base_service_id'},
                         'professional_ids':by_service.get(x['addon_service_id'],[])}
                        for x in by_addon.get(row['id'],[])]
        if row['package_id']:
            item['components']=by_package.get(row['package_id'],[])
        public_services.append(item)
    result={'site':preference,'services':public_services,'professionals':[dict(x) for x in professionals],
            'opening_hours':public_hours,'timezone':shop['timezone'],'booking_horizon_days':shop['booking_horizon_days'],
            'today':datetime.now(ZoneInfo(shop['timezone'])).date().isoformat(),'preview':preview,
            'published':bool(site and site['public_enabled']), 'booking_enabled':not preview and bool(site and site['public_enabled'])}
    return result if preview else project_catalogue_visuals(result,(site or {}).get('public_snapshot'))


def check_published_selection(site,professional,services,addons=(),components=None):
    """A guessed ID cannot book an entity absent from the published visual version."""
    saved=(site.get('public_snapshot') or {}).get('_catalogue')
    if not isinstance(saved,dict):return
    people={str(item['id']) for item in saved.get('professionals',[])}
    available={str(item['id']):item for item in saved.get('services',[])}
    if str(professional) not in people or any(str(item) not in available for item in services):
        raise HTTPException(422,'Seleção indisponível no site publicado. Consulte os serviços novamente')
    if addons:
        allowed={str(item['addon_service_id']) for item in available[str(services[0])].get('addons',[])}
        if any(str(item) not in allowed for item in addons):
            raise HTTPException(422,'Adicional indisponível no site publicado')
    if components is not None:
        for service in services:
            expected=[str(part['service_id']) for part in available[str(service)].get('components',[])]
            actual=[str(part['service_id']) for part in components
                    if str(part.get('combo_id',services[0]))==str(service)]
            if actual!=expected:
                raise HTTPException(409,'A composição do combo mudou e aguarda publicação. Selecione outro serviço')


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
    from app.site_publication import apply_publication
    return await apply_publication(tenant_id,payload,actor)


async def multi_service_contract(db,tenant,service_ids,professional,*,lock=False):
    """Quote complete public services as one native multi-item booking.

    No persistent combo is created. Each selected service adds its full
    duration, prep/cleanup and current server-side price. A signed fingerprint
    also binds the underlying composition of every registered combo.
    """
    from app.booking_service import load_bundle
    if not 2<=len(service_ids)<=6 or len(set(service_ids))!=len(service_ids):
        raise HTTPException(422,'Selecione de 2 a 6 serviços diferentes')
    suffix=' FOR SHARE' if lock else ''
    rows=(await db.execute(text("""SELECT id,catalog_kind FROM services
        WHERE tenant_id=:tenant AND id=ANY(CAST(:ids AS uuid[])) ORDER BY id"""+suffix),
        {'tenant':tenant,'ids':service_ids})).mappings().all()
    kinds={r['id']:r['catalog_kind'] for r in rows}
    if len(kinds)!=len(service_ids) or any(kinds.get(x) not in ('base','combo') for x in service_ids):
        raise HTTPException(422,'Serviço indisponível para agendamento')
    shop,items=await load_bundle(db,tenant,professional,service_ids)
    if lock:
        await db.execute(text("""SELECT id FROM professional_services
            WHERE tenant_id=:tenant AND professional_id=:professional
            AND service_id=ANY(CAST(:ids AS uuid[])) ORDER BY id FOR SHARE"""),
            {'tenant':tenant,'professional':professional,'ids':service_ids})
    components=(await db.execute(text("""SELECT p.synthetic_service_id AS combo_id,p.version,
        i.service_id,i.item_order,s.name,s.price_cents,s.price_mode,s.duration_min
        FROM service_packages p
        JOIN service_package_items i ON i.tenant_id=p.tenant_id AND i.package_id=p.id
        JOIN services s ON s.tenant_id=i.tenant_id AND s.id=i.service_id
        WHERE p.tenant_id=:tenant AND p.kind='combo'
          AND p.synthetic_service_id=ANY(CAST(:ids AS uuid[]))
        ORDER BY p.synthetic_service_id,i.item_order"""),
        {'tenant':tenant,'ids':service_ids})).mappings().all()
    by_combo={}
    for entry in components:
        by_combo.setdefault(entry['combo_id'],[]).append(entry['service_id'])
    used=set()
    for service_id in service_ids:
        included=by_combo.get(service_id,[]) if kinds[service_id]=='combo' else [service_id]
        if not included:
            raise HTTPException(422,'Este combo precisa ser atualizado antes do agendamento')
        for component in included:
            if component in used:
                raise HTTPException(422,'Um serviço já está incluído em outro combo selecionado')
            used.add(component)
    aggregate={'id':service_ids[0],'name':'Atendimento personalizado',
        'price_cents':sum(x['price_cents'] for x in items),
        'price_mode':'from' if any(x['price_mode']=='from' for x in items) else 'fixed',
        **{key:sum(x[key] for x in items) for key in ('duration_min','prep_min','cleanup_min')}}
    if (aggregate['duration_min']>1440 or aggregate['prep_min']>240 or
            aggregate['cleanup_min']>240 or aggregate['price_cents']>100_000_000):
        raise HTTPException(422,'Esta combinação ultrapassa os limites da agenda')
    return {'service_ids':service_ids,'items':items,'aggregate':aggregate,'shop':shop,'components':[dict(x) for x in components],
            'fingerprint':digest({'items':items,'shop':shop,'components':[dict(x) for x in components],
                                  'kinds':[(str(x),kinds[x]) for x in service_ids]})}


@router.get('/api/v1/public/sites/{slug}/availability')
async def public_availability(slug:str,request:Request,professional_id:UUID,day:date,
                              service_id:UUID|None=None,addon_ids:list[UUID]=Query(default=[]),
                              service_ids:list[UUID]=Query(default=[])):
    await public_limit(request,checked_slug(slug),'availability',60,300)
    tenant=await resolve_public(slug)
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            published=await published_guard(db,tenant,slug)
            check_published_selection(published,professional_id,service_ids or [service_id],addon_ids)
            if service_ids:
                if service_id is not None or addon_ids:
                    raise HTTPException(422,'Escolha serviços completos ou um serviço com adicionais')
                selection=await multi_service_contract(db,tenant,service_ids,professional_id)
            else:
                if service_id is None:raise HTTPException(422,'Selecione um serviço')
                selection=await selection_contract(db,tenant,service_id,addon_ids,professional_id)
            check_published_selection(published,professional_id,service_ids or [service_id],addon_ids,selection['components'])
            zone=ZoneInfo(selection['shop']['timezone']);now=datetime.now(UTC);local_now=now.astimezone(zone)
            if day<local_now.date() or day>local_now.date()+timedelta(days=selection['shop']['booking_horizon_days']):
                raise HTTPException(422,'Data fora do período disponível')
            slots,availability_reason=await verified_slot_grid(
                db,tenant,professional_id,selection['shop'],[selection['aggregate']],
                day,now,strict_errors=True)
            quote=issue_quote(slug,professional_id,day,selection,now,publication_version=published['public_version']) if slots else None
            return {'slots':slots,'availability_reason':availability_reason,'timezone':selection['shop']['timezone'],'price_cents':selection['aggregate']['price_cents'],
                    'price_mode':selection['aggregate']['price_mode'],
                    'duration_min':selection['aggregate']['duration_min'],'quote':quote,'expires_in':600 if quote else None,
                    'services':(published_booking_labels(published,professional_id,selection['items']) or
                                {'services':[x['name'] for x in selection['items']]})['services']}


@router.post('/api/v1/public/sites/{slug}/appointments',status_code=201)
async def public_book(slug:str,payload:PublicBooking,request:Request,_capacity=Depends(booking_capacity)):
    _same_origin(request)
    checked_slug(slug)
    quote=read_quote(payload.quote,slug)
    await public_limit(request,slug,'booking-ip',12,3600)
    await public_limit(request,slug,'booking-contact',4,3600,subject=payload.customer_phone)
    tenant=await resolve_public(slug)
    # Aliases belong to one establishment: old links must not multiply quotas.
    await public_limit(request,'tenant:'+str(tenant),'booking-ip',12,3600)
    await public_limit(request,'tenant:'+str(tenant),'booking-contact',4,3600,subject=payload.customer_phone)
    hold=None
    try:
        # Prepare a managed variant in its own transaction so the unchanged
        # engine can read it. No customer or appointment exists at this stage.
        async with get_session_factory()() as db:
            async with tenant_transaction(db,tenant):
                published=await published_guard(db,tenant,slug)
                replay=await confirmed_public_replay(db,tenant,quote,payload)
                if replay is not None:return replay
                check_published_selection(published,quote['professional'],quote['services'] if quote['v']==2 else [quote['service']],quote.get('addons',[]))
                selection=(await multi_service_contract(db,tenant,quote['services'],quote['professional'])
                           if quote['v']==2 else
                           await selection_contract(db,tenant,quote['service'],quote['addons'],quote['professional']))
                check_published_selection(published,quote['professional'],quote['services'] if quote['v']==2 else [quote['service']],quote.get('addons',[]),selection['components'])
                if not hmac.compare_digest(selection['fingerprint'],quote['fingerprint']):raise HTTPException(409,'Serviços ou preços mudaram. Confira um novo resumo')
                engine_services=(selection['service_ids'] if quote['v']==2
                                 else [await ensure_selection_service(db,tenant,selection)])
        # Hold the publication and source contract stable while the engine
        # performs its own locked hold/confirmation transactions.
        async with get_session_factory()() as guard:
            async with tenant_transaction(guard,tenant):
                published=await published_guard(guard,tenant,slug)
                check_published_selection(published,quote['professional'],quote['services'] if quote['v']==2 else [quote['service']],quote.get('addons',[]))
                selection=(await multi_service_contract(guard,tenant,quote['services'],quote['professional'],lock=True)
                           if quote['v']==2 else
                           await selection_contract(guard,tenant,quote['service'],quote['addons'],quote['professional'],lock=True))
                check_published_selection(published,quote['professional'],quote['services'] if quote['v']==2 else [quote['service']],quote.get('addons',[]),selection['components'])
                if not hmac.compare_digest(selection['fingerprint'],quote['fingerprint']):raise HTTPException(409,'Serviços ou preços mudaram. Confira um novo resumo')
                zone=ZoneInfo(selection['shop']['timezone'])
                if payload.start_at.astimezone(zone).date()!=quote['day']:raise HTTPException(422,'O horário não pertence ao dia escolhido')
                async with get_session_factory()() as engine:
                    hold=await book_appointment(engine,tenant,quote['professional'],engine_services,payload.customer_phone,
                                                payload.customer_name,payload.start_at,'public:'+quote['nonce'],source='public_site',
                                                nonblocking_professional_lock=True)
                    result=await confirm_hold(engine,tenant,hold['id'],customer_phone=payload.customer_phone,source='public_site',
                                              nonblocking_professional_lock=True)
                await store_public_birth_date(guard,tenant,result['id'],payload.customer_birth_date)
                await record_public_booking(guard,tenant,result['id'],selection['shop']['timezone'])
                visual_site=published
                if quote.get('publication_version',published['public_version'])!=published['public_version']:
                    previous=(await guard.execute(text("""SELECT snapshot FROM public_site_publications
                        WHERE tenant_id=:tenant AND version=:version AND action IN ('publish','restore')"""),
                        {'tenant':tenant,'version':quote['publication_version']})).scalar_one_or_none()
                    if previous is not None:visual_site={'public_snapshot':previous}
                labels=published_booking_labels(visual_site,quote['professional'],selection['items']) or {
                    'professional':selection['shop']['professional_name'],'services':[x['name'] for x in selection['items']]}
                return {'status':result['status'],'protocol':result['protocol'],'start_at':result['start_at'],'end_at':result['end_at'],
                        'price_cents':result['total_cents'],
                        'price_mode':result['price_mode'],'timezone':selection['shop']['timezone'],
                        **labels}
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
            if site['public_slug']!=slug:
                return RedirectResponse('/s/'+checked_slug(site['public_slug']),status_code=308,headers=HEADERS)
            try:
                rendered,schema_hash=public_html((STATIC/'orion-public.html').read_text(encoding='utf-8'),
                                                site['public_snapshot'] or {},site['public_slug'])
            except ValueError:
                raise HTTPException(503,'Página temporariamente indisponível') from None
            await db.execute(text("""INSERT INTO public_site_daily_metrics (tenant_id,day,visits)
                VALUES (:tenant,CAST(now() AT TIME ZONE :zone AS date),1)
                ON CONFLICT (tenant_id,day) DO UPDATE SET visits=public_site_daily_metrics.visits+1"""),{'tenant':tenant,'zone':site['timezone']})
    headers={**HEADERS,'Content-Security-Policy':HEADERS['Content-Security-Policy'].replace(
        "script-src 'self'", "script-src 'self' 'sha256-"+schema_hash+"'")}
    return HTMLResponse(rendered,headers=headers)


@router.get('/app/site-preview/{tenant_id}',include_in_schema=False)
async def preview_page(tenant_id:UUID,actor:Actor=Depends(authenticate)):
    await resolve_tenant_access(actor,tenant_id,'read')
    return FileResponse(STATIC/'orion-public.html',headers={**HEADERS,'X-Robots-Tag':'noindex, nofollow'})


@router.get('/public-assets/{asset}',include_in_schema=False)
async def public_asset(asset:str):
    allowed={'orion-public.css':'text/css','orion-public.js':'text/javascript',
             'orion-public-theme.js':'text/javascript'}
    if asset not in allowed:raise HTTPException(404)
    return FileResponse(STATIC/asset,media_type=allowed[asset],headers={'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'})
