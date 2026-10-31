"""Short public appointment links secured by WhatsApp-verified one-time codes.

A protocol/alias is only a public LOOKUP identifier. Neither a short GET nor
an OTP request returns any booking facts, tenant IDs or phone numbers.
The customer/professional must initiate a WhatsApp message from the registered
phone. The trusted QR bridge supplies the actual sender PN (never the browser).
Only after a matching inbound PN and a correct 5-minute code may the browser
receive an in-memory customer capability, or a professional-scoped session.

No arbitrary outbound WhatsApp send endpoint is exposed by this router.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from pydantic import AwareDatetime, Field
from redis.exceptions import RedisError
from sqlalchemy import text

from app import appointment_access as access
from app.appointment_errors import sanitized_database_errors
from app.catalog_api import support_session_factory
from app.catalog_schemas import StrictModel
from app.database import get_session_factory, tenant_transaction
from app.panel_auth import _redis, _same_origin
from app.public_origins import canonical_public_origin
from app.public_site_api import public_limit
from app.booking_service import (
    read_appointment, list_slots, cancel_appointment, reschedule_appointment)
from app.appointment_lifecycle import LifecycleActor
from app.plan_permissions import has_feature

router = APIRouter(tags=["Links curtos de agendamentos"])
CODE = re.compile(r"^AG-[A-HJ-NP-Z2-9]{6}(?:-[A-F0-9]{8})?$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{22}$")
OTP = re.compile(r"^[0-9]{6}$")
INBOUND = re.compile(r"^ORION VERIFICAR (C|P) (AG-[A-HJ-NP-Z2-9]{6}(?:-[A-F0-9]{8})?) ([A-Za-z0-9_-]{22})$",re.I)
TTL = 300
SESSION_TTL = 24 * 3600
CHALLENGE_COOKIE = "__Host-orion_appointment_challenge"
SESSION_COOKIE = "__Host-orion_appointment_session"
GENERIC = "Não foi possível validar. Confira o WhatsApp cadastrado e solicite um novo código pelo link."
CSP = {"Cache-Control":"no-store, max-age=0","Pragma":"no-cache",
    "Referrer-Policy":"no-referrer","X-Content-Type-Options":"nosniff",
    "X-Robots-Tag":"noindex, nofollow, noarchive",
    "Content-Security-Policy":"default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}


class Start(StrictModel):
    code: str = Field(min_length=9, max_length=24)
    role: Literal["customer","professional"] = "customer"


class Verify(Start):
    challenge: str = Field(min_length=22, max_length=22)
    otp: str = Field(min_length=6,max_length=6)


class OpenDirect(Start):
    proof: str = Field(min_length=27, max_length=27, pattern=r"^[A-Za-z0-9_-]{27}$")


class Action(Start):
    proof: str | None = Field(default=None, min_length=27, max_length=27,
                              pattern=r"^[A-Za-z0-9_-]{27}$")
    action: Literal["view","slots","cancel","reschedule"] = "view"
    day: date | None = None
    start_at: AwareDatetime | None = None
    expected_updated_at: AwareDatetime | None = None
    idempotency_key: UUID | None = None


def active():
    return access.short_links_enabled() and os.getenv(
        "ORION_CENTRAL_CONFIRMATION_REPLY_ENABLED") == "1"


def one_click_active():
    """The OTP endpoints are retired as soon as direct-link proof is configured."""
    return len(os.getenv("ORION_DIRECT_APPOINTMENT_KEY", "")) >= 43


def normalized(code):
    if not isinstance(code,str) or not CODE.fullmatch(code.upper()):
        raise HTTPException(404,"Link indisponível")
    return code.upper()


def scoped_redis_key(kind, token):
    return "orion:short:" + kind + ":" + hashlib.sha256(token.encode()).hexdigest()


def digest_code(challenge, code):
    secret=os.getenv("INTEGRATION_FERNET_KEY","")
    if len(secret)<32:
        raise RuntimeError("otp_secret_missing")
    return hmac.new(secret.encode(),(challenge+"|"+code).encode(),hashlib.sha256).hexdigest()


async def limited(request,code,kind,number=8,seconds=300):
    await public_limit(request,"orion-short:"+code,kind,number,seconds)
    await public_limit(request,"orion-short-global",kind+"-all",200,60)


def validate_origin(request):
    _same_origin(request)
    origin=request.headers.get("origin")
    site=request.headers.get("sec-fetch-site")
    # A cross-site POST cannot use a session cookie. Browsers without Origin
    # (e.g. some WKWebView versions) must at least claim same-origin fetch.
    if not origin and site not in ("same-origin","none"):
        raise HTTPException(403,"Origem não autorizada")


async def with_redis(fn):
    client=_redis()
    try:
        return await fn(client)
    except (RedisError,OSError):
        raise HTTPException(503,"Verificação temporariamente indisponível") from None
    finally:
        await client.aclose()


def set_cookie(response,name,raw,max_age):
    response.set_cookie(name,raw,max_age=max_age,secure=True,
                        httponly=True,samesite="lax",path="/")


@router.get("/{code}", include_in_schema=False)
async def short_customer_page(code:str):
    if not active():raise HTTPException(404,"Link indisponível")
    normalized(code)
    return FileResponse(Path(__file__).parent/"static"/"orion-appointment.html",headers=CSP)


@router.get("/p/{code}",include_in_schema=False)
async def short_professional_page(code:str):
    if not active():raise HTTPException(404,"Link indisponível")
    normalized(code)
    return FileResponse(Path(__file__).parent/"static"/"orion-appointment.html",headers=CSP)


@router.post("/api/short-appointment/start")
async def start(payload:Start,request:Request):
    if not active() or one_click_active():raise HTTPException(404,"Verificação por código descontinuada")
    validate_origin(request)
    code=normalized(payload.code)
    await limited(request,code,"otp-start",6,300)
    from app.appointment_confirmation import central_admin_phone
    phone=await central_admin_phone()
    if not isinstance(phone,str) or not re.fullmatch(r"(?:55[1-9][0-9]{9,10}|5959[0-9]{8})",phone):
        raise HTTPException(503,"Verificação por WhatsApp indisponível")
    challenge=secrets.token_urlsafe(16)
    browser_secret=secrets.token_urlsafe(32)
    state={"code":code,"role":payload.role,
           "browser":hashlib.sha256(browser_secret.encode()).hexdigest(),
           "state":"waiting"}
    async def store(cache):
        return await cache.set(scoped_redis_key("challenge",challenge),
                               json.dumps(state,separators=(",",":")),ex=TTL,nx=True)
    if not await with_redis(store):
        raise HTTPException(503,"Solicite um novo código")
    letter="P" if payload.role=="professional" else "C"
    text_message="ORION VERIFICAR "+letter+" "+code+" "+challenge
    response=JSONResponse({"status":"requested","challenge":challenge,
      "whatsapp_url":"https://wa.me/"+phone+"?text="+quote(text_message,safe="")},headers=CSP)
    set_cookie(response,CHALLENGE_COOKIE,browser_secret,TTL)
    return response


async def inbound_otp_from_verified_phone(body,phone):
    """Call ONLY from the authenticated, rate-limited central QR ingress."""
    if not active() or one_click_active():return None
    match=INBOUND.fullmatch(body.strip()) if isinstance(body,str) and len(body)<180 else None
    if not match:return None
    letter,code,challenge=match.groups()
    code=code.upper()
    role="professional" if letter.upper()=="P" else "customer"
    generic={"state":"unrecognized","answer":GENERIC}
    key=scoped_redis_key("challenge",challenge)
    async def lookup(cache):return await cache.get(key)
    try:
        packed=await with_redis(lookup)
        initial=json.loads(packed) if packed else {}
        if initial.get("state")!="waiting" or initial.get("code")!=code or initial.get("role")!=role:
            return generic
        if not isinstance(phone,str) or not re.fullmatch(r"(?:55[1-9][0-9]{9,10}|5959[0-9]{8})",phone):
            return generic
        async with support_session_factory()() as db:
            row=(await db.execute(text("""
                SELECT tenant_id,appointment_id,customer_id,professional_id
                FROM public.orion_resolve_short_appointment(:code,:phone,:role)
            """),{"code":code,"phone":"+"+phone,"role":role})).mappings().first()
        if row is None:return generic
        if not await has_feature_for_tenant(row["tenant_id"]):return generic
        pin=f"{secrets.randbelow(1_000_000):06d}"
        state={**initial,"state":"verified_whatsapp",
               "otp_hash":digest_code(challenge,pin),
               "tenant":str(row["tenant_id"]),"appointment":str(row["appointment_id"]),
               "customer":str(row["customer_id"]),"professional":str(row["professional_id"]),
               "phone_hash":hashlib.sha256(("+"+phone).encode()).hexdigest()}
        # Atomic compare-and-replace: QR retries cannot create two different OTPs.
        lua="""local previous=redis.call('GET',KEYS[1])
               if not previous or previous~=ARGV[1] then return 0 end
               redis.call('SET',KEYS[1],ARGV[2],'KEEPTTL');return 1"""
        async def save(cache):
            return await cache.eval(lua,1,key,packed,json.dumps(state,separators=(",",":")))
        if not await with_redis(save):return generic
        return {"state":"confirmed",
                "answer":f"🔐 *Código de acesso Orion:* *{pin}*\n\n"
                         "Digite esse código na página do agendamento.\n"
                         "_Ele expira em 5 minutos. Nunca compartilhe o código._"}
    except Exception:
        return {"state":"unavailable","answer":None}


async def has_feature_for_tenant(tenant):
    from app.plan_permissions import tenant_has_feature
    return await tenant_has_feature(tenant,"notifications.basic")


async def direct_state(code, role, proof):
    """Authorize on every request using the per-recipient fragment proof.

    The support-only database resolver returns internal facts but does not
    grant access. Never return its data unless the constant-time HMAC check
    succeeds; public protocols are enumerable and are NOT authentication.
    """
    if not isinstance(proof, str) or not re.fullmatch(r"[A-Za-z0-9_-]{27}", proof):
        return None
    async with support_session_factory()() as db:
        row = (await db.execute(text("""
            SELECT tenant_id,appointment_id,customer_id,professional_id,
                customer_phone,professional_phone,customer_version,professional_active
            FROM public.orion_resolve_direct_appointment(:code)
        """), {"code":code})).mappings().first()
    if not row or (role == "professional" and not row["professional_active"]):
        return None
    if not await has_feature_for_tenant(row["tenant_id"]):
        return None
    identity = row["professional_id"] if role == "professional" else row["customer_id"]
    phone = row["professional_phone"] if role == "professional" else row["customer_phone"]
    if not phone:
        return None
    expected = access.direct_link_proof(code, role, row["tenant_id"],
        row["appointment_id"], identity, phone,
        row["customer_version"] if role == "customer" else None)
    if not hmac.compare_digest(proof, expected):
        return None
    return {"code":code,"role":role,"tenant":str(row["tenant_id"]),
        "appointment":str(row["appointment_id"]),
        "customer":str(row["customer_id"]),"professional":str(row["professional_id"]),
        "phone_hash":hashlib.sha256(phone.encode()).hexdigest()}


async def verified_session(request,code,role):
    raw=request.cookies.get(SESSION_COOKIE,"")
    if len(raw)!=43:return None
    async def load(cache):return await cache.get(scoped_redis_key("session",raw))
    packed=await with_redis(load)
    try:
        session=json.loads(packed) if packed else None
        if not session or session["code"]!=code or session["role"]!=role:
            return None
        tenant=UUID(session["tenant"]);appointment=UUID(session["appointment"])
        expected=UUID(session[("professional" if role=="professional" else "customer")])
    except (TypeError,ValueError,KeyError):
        return None
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            if not await has_feature(db,tenant,"notifications.basic"):
                return None
            row=(await db.execute(text("""
                SELECT a.customer_id,a.professional_id,a.status,
                  c.whatsapp_e164 AS customer_phone,c.active AS customer_active,
                  c.deleted_at AS deleted_at,
                  p.phone_e164 AS professional_phone,p.active AS professional_active,
                  a.customer_access_blocked
                FROM appointments a
                JOIN customers c ON c.tenant_id=a.tenant_id AND c.id=a.customer_id
                JOIN professionals p ON p.tenant_id=a.tenant_id AND p.id=a.professional_id
                JOIN tenants t ON t.id=a.tenant_id AND t.status='active'
                WHERE a.tenant_id=:t AND a.id=:a
            """),{"t":tenant,"a":appointment})).mappings().first()
            if not row or not row["customer_active"] or row["deleted_at"] or row["customer_access_blocked"]:
                return None
            if row[role+"_id"]!=expected:return None
            if row["status"]=="hold":return None
            contact=row["professional_phone" if role=="professional" else "customer_phone"]
            if not isinstance(contact,str) or (
                role=="professional" and not row["professional_active"]):
                return None
            if not hmac.compare_digest(hashlib.sha256(contact.encode()).hexdigest(),
                                       session["phone_hash"]):return None
            return session
    return None


async def session_response(session,role):
    tenant=UUID(session["tenant"]);appointment=UUID(session["appointment"])
    async with get_session_factory()() as db:
        async with tenant_transaction(db,tenant):
            if role=="customer":
                url=await access.issue(db,tenant,appointment)
                return {"authorized":True,"token":url.split("/a#",1)[1]}
            row=await read_appointment(db,tenant,appointment)
            now=(await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            company=(await db.execute(text("""
                SELECT t.name,p.theme_color,p.logo_url
                FROM tenants t LEFT JOIN management_site_preferences p ON p.tenant_id=t.id
                WHERE t.id=:t
            """),{"t":tenant})).mappings().one()
            from app.media_paths import is_media_url
            logo=company["logo_url"]
            if not is_media_url(logo) or not logo.startswith("/media/"+str(tenant)+"/"):
                logo=None
            color=company["theme_color"]
            if not isinstance(color,str) or not re.fullmatch(r"#[0-9a-fA-F]{6}",color):
                color="#2e7bf6"
            change=row["status"]=="confirmed" and row["start_at"]>now
            return {"authorized":True,"appointment":{
                "company":company["name"],"logo_url":logo,"brand_color":color,
                "customer":row["customer_name"],"professional":row["professional_name"],
                "services":[item["name_snapshot"] for item in row["items"]],
                "start_at":row["start_at"],"end_at":row["end_at"],
                "duration_min":sum(x["duration_snapshot_min"] for x in row["items"]),
                "timezone":row["timezone"],"protocol":row["protocol"],
                "status":row["status"],"updated_at":row["updated_at"],
                "can_change":change,"can_review":False,"review":None,
                "has_future_booking":False,"return_suggestions":[],"contact_phone":None,
                "central_whatsapp":False,"pending_change":None,
                "professional_view":True}}


@router.post("/api/short-appointment/session")
@sanitized_database_errors
async def session(payload:Start,request:Request):
    if not active():raise HTTPException(404,"Link indisponível")
    validate_origin(request)
    code=normalized(payload.code)
    await limited(request,code,"otp-session",40,300)
    state=await verified_session(request,code,payload.role)
    if not state:
        return JSONResponse({"authorized":False},headers=CSP,status_code=401)
    return JSONResponse(jsonable_encoder(await session_response(state,payload.role)),headers=CSP)


@router.post("/api/short-appointment/open")
@sanitized_database_errors
async def open_direct(payload:OpenDirect, request:Request):
    if not active():raise HTTPException(404,"Link indisponível")
    validate_origin(request)
    code=normalized(payload.code)
    await limited(request,code,"direct-open",30,300)
    state=await direct_state(code,payload.role,payload.proof)
    if not state:raise HTTPException(404,"Link indisponível ou expirado. Solicite um novo link.")
    return JSONResponse(jsonable_encoder(await session_response(state,payload.role)),headers=CSP)


@router.post("/api/short-appointment/verify")
@sanitized_database_errors
async def verify(payload:Verify,request:Request):
    if not active() or one_click_active():raise HTTPException(404,"Verificação por código descontinuada")
    validate_origin(request)
    code=normalized(payload.code)
    await limited(request,code,"otp-verify",6,300)
    if not CHALLENGE.fullmatch(payload.challenge) or not OTP.fullmatch(payload.otp):
        raise HTTPException(400,GENERIC)
    # Lock retries to one challenge across ALL IPs and devices. An attacker
    # cannot bypass the six-try limit by distributing guesses across IPs.
    attempts_key=scoped_redis_key("otp-attempts",payload.challenge)
    lua="""local n=redis.call('INCR',KEYS[1])
             if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end
             return n"""
    async def count_attempt(cache):
        return await cache.eval(lua,1,attempts_key,TTL)
    if int(await with_redis(count_attempt))>6:
        raise HTTPException(429,GENERIC,headers={"Retry-After":"300"})
    browser=request.cookies.get(CHALLENGE_COOKIE,"")
    if len(browser)!=43:raise HTTPException(400,GENERIC)
    key=scoped_redis_key("challenge",payload.challenge)
    async def load(cache):return await cache.get(key)
    packed=await with_redis(load)
    try:
        state=json.loads(packed) if packed else {}
        if (state.get("state")!="verified_whatsapp" or state.get("code")!=code
             or state.get("role")!=payload.role
             or not hmac.compare_digest(state["browser"],hashlib.sha256(browser.encode()).hexdigest())):
            raise HTTPException(400,GENERIC)
    except (KeyError,TypeError,ValueError):
        raise HTTPException(400,GENERIC) from None
    if not hmac.compare_digest(state["otp_hash"],digest_code(payload.challenge,payload.otp)):
        raise HTTPException(400,GENERIC)
    async def consume(cache):
        script="""local current=redis.call('GET',KEYS[1])
                  if not current or current~=ARGV[1] then return 0 end
                  redis.call('DEL',KEYS[1]);return 1"""
        return await cache.eval(script,1,key,packed)
    if not await with_redis(consume):
        raise HTTPException(400,GENERIC)
    session_secret=secrets.token_urlsafe(32)
    session_data={k:state[k] for k in ("code","role","tenant","appointment",
                                        "customer","professional","phone_hash")}
    async def save_session(cache):
        return await cache.set(scoped_redis_key("session",session_secret),
              json.dumps(session_data,separators=(",",":")),ex=SESSION_TTL,nx=True)
    if not await with_redis(save_session):
        raise HTTPException(503,"Tente novamente")
    content=await session_response(session_data,payload.role)
    response=JSONResponse(jsonable_encoder(content),headers=CSP)
    set_cookie(response,SESSION_COOKIE,session_secret,SESSION_TTL)
    response.delete_cookie(CHALLENGE_COOKIE,path="/",secure=True,httponly=True,samesite="lax")
    return response


@router.post("/api/short-appointment/professional")
@sanitized_database_errors
async def professional_action(payload:Action,request:Request):
    if not active():raise HTTPException(404,"Link indisponível")
    validate_origin(request)
    code=normalized(payload.code)
    if payload.role!="professional":raise HTTPException(403,GENERIC)
    await limited(request,code,"professional-actions",30,300)
    session_data=(await direct_state(code,"professional",payload.proof)
        if payload.proof else await verified_session(request,code,"professional"))
    if not session_data:raise HTTPException(401,GENERIC)
    tenant=UUID(session_data["tenant"]);appointment=UUID(session_data["appointment"])
    if payload.action=="view":
        return JSONResponse(jsonable_encoder((await session_response(session_data,"professional"))["appointment"]),headers=CSP)
    async with get_session_factory()() as db:
        if payload.action=="slots":
            if not payload.day:raise HTTPException(422,"Escolha uma data")
            async with tenant_transaction(db,tenant):
                row=await read_appointment(db,tenant,appointment)
                if row["professional_id"]!=UUID(session_data["professional"]):
                    raise HTTPException(403,GENERIC)
            return JSONResponse(jsonable_encoder(
                await list_slots(db,tenant,row["professional_id"],
                   [x["service_id"] for x in row["items"]],payload.day,appointment_id=appointment)),headers=CSP)
        if payload.expected_updated_at is None or payload.idempotency_key is None:
            raise HTTPException(422,"Atualize os detalhes antes de alterar")
        actor=LifecycleActor(role="professional",source="whatsapp_qr")
        if payload.action=="cancel":
            await cancel_appointment(db,tenant,appointment,
                expected_updated_at=payload.expected_updated_at,
                idempotency_key=str(payload.idempotency_key),
                future_only=True,requested_by="professional",context=actor,
                reason="Cancelado pelo profissional após verificação do WhatsApp")
        elif payload.action=="reschedule":
            if not payload.start_at:raise HTTPException(422,"Escolha o horário")
            await reschedule_appointment(db,tenant,appointment,payload.start_at,
                expected_updated_at=payload.expected_updated_at,
                idempotency_key=str(payload.idempotency_key),context=actor)
        else:
            raise HTTPException(422,"Ação inválida")
    return JSONResponse(jsonable_encoder(
       (await session_response(session_data,"professional"))["appointment"]),headers=CSP)
