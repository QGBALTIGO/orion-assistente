"""Persistent Orion accounts: email proof, password recovery and privileged owner claim.

Only app_support accesses these tables; public registration NEVER creates an admin.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import ssl
from uuid import UUID

from argon2 import PasswordHasher, exceptions as argon2_errors
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.panel_auth import _same_origin, _redis, _password_valid, panel_actor

router = APIRouter(prefix="/auth", tags=["Contas Orion"])
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
# A fake hash makes missing-account timing less distinguishable.
_FAKE = _hasher.hash("not-a-real-orion-password-value")


class EmailMixin(BaseModel):
    email: str = Field(min_length=5, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v):
        value=v.strip().lower()
        if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value):
            raise ValueError("Informe um e-mail válido")
        return value


class Register(EmailMixin):
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("name")
    @classmethod
    def normalize_name(cls,v):
        value=" ".join(v.split())
        if len(value)<2 or len(value)>120:raise ValueError("Nome inválido")
        return value


class RecoveryStart(EmailMixin):
    pass


class OneTimeToken(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class ResetPassword(OneTimeToken):
    password: str = Field(min_length=12, max_length=128)


class ClaimOwner(BaseModel):
    legacy_password: str = Field(min_length=1, max_length=200)


def _storage():
    from app.catalog_api import support_session_factory
    return support_session_factory()


def _public_url():
    value=os.getenv("PANEL_PUBLIC_URL","").strip().rstrip("/")
    parsed=urlparse(value)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise HTTPException(503,"Endereço público para e-mails não configurado")
    return value


def mail_ready():
    return bool(os.getenv("PANEL_EMAIL_ENABLED")=="1" and
                os.getenv("PANEL_SMTP_HOST") and os.getenv("PANEL_SMTP_USERNAME") and
                os.getenv("PANEL_SMTP_PASSWORD") and os.getenv("PANEL_SMTP_FROM") and
                os.getenv("PANEL_PUBLIC_URL","").startswith("https://"))


def _require_mail():
    if not mail_ready():raise HTTPException(503,"Envio de e-mails ainda não configurado")
    return _public_url()


def _send_email(recipient, purpose, secret, public_url):
    host=os.environ["PANEL_SMTP_HOST"]
    port=int(os.getenv("PANEL_SMTP_PORT","465"))
    if port not in (465,587):raise ValueError("SMTP port not allowed")
    sender=os.environ["PANEL_SMTP_FROM"]
    username=os.environ["PANEL_SMTP_USERNAME"]
    password=os.environ["PANEL_SMTP_PASSWORD"]
    if purpose=="verify":
        subject="Confirme seu e-mail no Orion"
        intro="Para confirmar sua conta, abra este link (válido por 24 horas):"
        url=public_url+"/panel?verify="+secret
    else:
        subject="Redefinição de senha do Orion"
        intro="Se você solicitou a troca de senha, abra este link (válido por 20 minutos):"
        url=public_url+"/panel?reset="+secret
    msg=EmailMessage()
    msg["From"]=sender;msg["To"]=recipient;msg["Subject"]=subject
    msg.set_content(intro+"\n\n"+url+"\n\nSe não foi você, ignore esta mensagem.")
    if port==465:
        with smtplib.SMTP_SSL(host,port,timeout=12,context=ssl.create_default_context()) as smtp:
            smtp.login(username,password);smtp.send_message(msg)
    else:
        with smtplib.SMTP(host,port,timeout=12) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username,password);smtp.send_message(msg)


async def _throttle(request, category, subject, count=5, window=900):
    ip=request.client.host if request.client else "unknown"
    digest=hashlib.sha256((ip+"|"+subject.lower()).encode()).hexdigest()
    key="orion:authlimit:"+category+":"+digest
    cache=_redis()
    try:
        value=int(await cache.incr(key))
        if value==1:await cache.expire(key,window)
        if value>count:raise HTTPException(429,"Muitas tentativas. Aguarde alguns minutos")
    finally:await cache.aclose()


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def _issue_token(session, account_id, purpose):
    raw=secrets.token_urlsafe(32)
    duration=timedelta(hours=24) if purpose=="verify" else timedelta(minutes=20)
    await session.execute(text("""
        UPDATE panel_email_tokens SET used_at=now()
        WHERE account_id=:id AND purpose=:purpose AND used_at IS NULL
    """),{"id":account_id,"purpose":purpose})
    await session.execute(text("""
        INSERT INTO panel_email_tokens(account_id,token_digest,purpose,expires_at)
        VALUES (:id,:digest,:purpose,:expiry)
    """),{"id":account_id,"digest":_digest(raw),"purpose":purpose,
          "expiry":datetime.now(timezone.utc)+duration})
    return raw


@router.get("/features")
async def features():
    return {"registration":mail_ready() and os.getenv("PANEL_REGISTRATION_ENABLED")=="1",
            "passwordRecovery":mail_ready()}


@router.post("/register",status_code=202)
async def register(payload:Register,request:Request):
    _same_origin(request)
    url=_require_mail()
    if os.getenv("PANEL_REGISTRATION_ENABLED")!="1":
        raise HTTPException(503,"Cadastro ainda não habilitado")
    await _throttle(request,"register",payload.email,count=3,window=3600)
    token=None
    async with _storage()() as session:
        async with session.begin():
            row=(await session.execute(text("""
                SELECT id,verified_at FROM panel_accounts WHERE email=:email FOR UPDATE
            """),{"email":payload.email})).mappings().first()
            if row and row["verified_at"] is not None:
                return {"ok":True,"message":"Se necessário, verifique sua caixa de entrada."}
            if row:
                account_id=row["id"]
                # Never replace a pending user's password using an unauthenticated request.
            else:
                try:
                    account_id=(await session.execute(text("""
                        INSERT INTO panel_accounts(name,email,password_hash,role)
                        VALUES (:name,:email,:password,'member') RETURNING id
                    """),{"name":payload.name,"email":payload.email,
                          "password":_hasher.hash(payload.password)})).scalar_one()
                except IntegrityError:
                    raise HTTPException(409,"Tente novamente em instantes") from None
            token=await _issue_token(session,account_id,"verify")
    try:await asyncio.to_thread(_send_email,payload.email,"verify",token,url)
    except (OSError,smtplib.SMTPException,ValueError,TimeoutError):
        raise HTTPException(503,"Não foi possível enviar o e-mail. Tente novamente mais tarde") from None
    return {"ok":True,"message":"Confira seu e-mail para confirmar a conta."}


@router.post("/verify")
async def verify(payload:OneTimeToken,request:Request):
    _same_origin(request)
    async with _storage()() as session:
        async with session.begin():
            row=(await session.execute(text("""
                SELECT id,account_id FROM panel_email_tokens
                WHERE token_digest=:digest AND purpose='verify' AND used_at IS NULL
                  AND expires_at>now()
                FOR UPDATE
            """),{"digest":_digest(payload.token)})).mappings().first()
            if not row:raise HTTPException(400,"Link inválido ou expirado")
            await session.execute(text("UPDATE panel_accounts SET verified_at=coalesce(verified_at,now()) WHERE id=:id"),
                                  {"id":row["account_id"]})
            await session.execute(text("UPDATE panel_email_tokens SET used_at=now() WHERE id=:id"),
                                  {"id":row["id"]})
    return {"ok":True,"message":"E-mail confirmado. Agora você pode entrar."}


@router.post("/password/forgot")
async def forgot(payload:RecoveryStart,request:Request):
    _same_origin(request)
    url=_require_mail()
    await _throttle(request,"forgot",payload.email,count=3,window=3600)
    token=None
    async with _storage()() as session:
        async with session.begin():
            account=(await session.execute(text("""
                SELECT id FROM panel_accounts WHERE email=:email AND verified_at IS NOT NULL
            """),{"email":payload.email})).mappings().first()
            if account:token=await _issue_token(session,account["id"],"reset")
    if token:
        try:await asyncio.to_thread(_send_email,payload.email,"reset",token,url)
        except (OSError,smtplib.SMTPException,ValueError,TimeoutError):
            # Non-enumerating response. Monitoring must alert on SMTP failures.
            pass
    return {"ok":True,"message":"Se esse e-mail estiver cadastrado, enviaremos um link."}


@router.post("/password/reset")
async def reset(payload:ResetPassword,request:Request):
    _same_origin(request)
    await _throttle(request,"reset","token",count=8,window=900)
    async with _storage()() as session:
        async with session.begin():
            row=(await session.execute(text("""
                SELECT id,account_id FROM panel_email_tokens
                WHERE token_digest=:digest AND purpose='reset' AND used_at IS NULL
                  AND expires_at>now() FOR UPDATE
            """),{"digest":_digest(payload.token)})).mappings().first()
            if not row:raise HTTPException(400,"Link inválido ou expirado")
            await session.execute(text("""
                UPDATE panel_accounts SET password_hash=:hashed,
                auth_version=auth_version+1 WHERE id=:id
            """),{"hashed":_hasher.hash(payload.password),"id":row["account_id"]})
            await session.execute(text("""
                UPDATE panel_email_tokens SET used_at=now()
                WHERE account_id=:id AND used_at IS NULL
            """),{"id":row["account_id"]})
    return {"ok":True,"message":"Senha atualizada. Entre novamente."}


async def authenticate_account(email,password):
    if "@" not in email or len(email)>254:return None
    async with _storage()() as session:
        row=(await session.execute(text("""
            SELECT id,name,email,password_hash,role,auth_version,verified_at
            FROM panel_accounts WHERE email=:email
        """),{"email":email.strip().lower()})).mappings().first()
    try:
        accepted=_hasher.verify(row["password_hash"] if row else _FAKE,password)
    except (argon2_errors.VerificationError,argon2_errors.InvalidHashError):
        accepted=False
    if not row or not accepted or row["verified_at"] is None:return None
    return {"id":str(row["id"]),"name":row["name"],"email":row["email"],
            "role":row["role"],"version":row["auth_version"]}


async def current_account(account_id,version):
    async with _storage()() as session:
        row=(await session.execute(text("""
            SELECT role,auth_version,verified_at,name,email FROM panel_accounts WHERE id=:id
        """),{"id":UUID(account_id)})).mappings().first()
    if not row or row["verified_at"] is None or row["auth_version"]!=version:
        raise HTTPException(401,"Sua sessão expirou. Entre novamente")
    return row


@router.post("/admin/claim")
async def claim_owner(payload:ClaimOwner,request:Request,data:dict=Depends(panel_actor)):
    if not data.get("account_id"):raise HTTPException(403,"Entre com sua conta verificada")
    owner=os.getenv("PANEL_OWNER_EMAIL","").strip().lower()
    if not owner:raise HTTPException(503,"Identidade proprietária não configurada")
    await _throttle(request,"claim",data["account_id"],count=5,window=1800)
    if not _password_valid(payload.legacy_password):
        raise HTTPException(403,"Autorização administrativa recusada")
    async with _storage()() as session:
        async with session.begin():
            row=(await session.execute(text("""
                SELECT email,verified_at FROM panel_accounts WHERE id=:id FOR UPDATE
            """),{"id":UUID(data["account_id"])})).mappings().first()
            if not row or row["verified_at"] is None or not hmac.compare_digest(row["email"],owner):
                raise HTTPException(403,"Conta sem autorização administrativa")
            await session.execute(text("""
                UPDATE panel_accounts SET role='admin',auth_version=auth_version+1 WHERE id=:id
            """),{"id":UUID(data["account_id"])})
    return {"ok":True,"reloginRequired":True}
