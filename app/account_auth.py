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
import httpx
from uuid import UUID

from argon2 import PasswordHasher, exceptions as argon2_errors
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.panel_auth import _same_origin, _redis, _password_valid, panel_actor

router = APIRouter(prefix="/auth", tags=["Contas Orion"])


class EmailTransportError(Exception):
    """Provider failure without any message body or credential in logs."""
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


class OneTimeCode(EmailMixin):
    code: str = Field(pattern=r"^\d{6}$")


class ResetPassword(OneTimeCode):
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


def _test_mode():
    return os.getenv("PANEL_TEST_MODE")=="1"


def _test_email():
    return os.getenv("PANEL_TEST_EMAIL","").strip().lower()


def _allowed_recipient(email):
    # Resend shared onboarding@resend.dev sender is restricted to the owner's email.
    return not _test_mode() or (
        bool(_test_email()) and hmac.compare_digest(email.strip().lower(),_test_email())
    )


def mail_ready():
    transport=os.getenv("PANEL_EMAIL_TRANSPORT","smtp")
    provider_ready=(bool(os.getenv("PANEL_SMTP_PASSWORD")) and bool(os.getenv("PANEL_SMTP_FROM")))
    if transport=="smtp":
        provider_ready=provider_ready and bool(os.getenv("PANEL_SMTP_HOST")) and bool(os.getenv("PANEL_SMTP_USERNAME"))
    elif transport!="resend_https":
        provider_ready=False
    return bool(os.getenv("PANEL_EMAIL_ENABLED")=="1" and provider_ready and
                len(os.getenv("PANEL_OTP_SECRET",""))>=32 and
                os.getenv("PANEL_PUBLIC_URL","").startswith("https://") and
                (not _test_mode() or bool(_test_email())))


def _require_mail():
    if not mail_ready():raise HTTPException(503,"Envio de e-mails ainda não configurado")
    return _public_url()


def _send_email(recipient, purpose, secret, public_url):
    """SMTP TLS message; only the 6-digit code is revealed to the mailbox owner."""
    host=os.getenv("PANEL_SMTP_HOST","")
    port=int(os.getenv("PANEL_SMTP_PORT","465"))
    if port not in (465,587):raise ValueError("SMTP port not allowed")
    sender=os.environ["PANEL_SMTP_FROM"]
    username=os.getenv("PANEL_SMTP_USERNAME","")
    password=os.environ["PANEL_SMTP_PASSWORD"]
    if purpose=="verify":
        subject="Seu código de confirmação — Orion"
        intro="Para confirmar seu e-mail no Orion, digite o código abaixo."
        minutes=10
    else:
        subject="Seu código para redefinir a senha — Orion"
        intro="Se você solicitou uma nova senha no Orion, digite este código."
        minutes=10
    msg=EmailMessage()
    msg["From"]=sender;msg["To"]=recipient;msg["Subject"]=subject
    msg.set_content(
        intro+"\n\n"+secret+"\n\nO código expira em "+
        str(minutes)+" minutos e só pode ser usado uma vez. "+
        "Se não foi você, ignore este e-mail.\n\nEquipe Orion"
    )
    html=(
        '<!doctype html><html lang="pt-BR"><body style="background:#0e172a;padding:32px;'
        'font-family:Arial,sans-serif;color:#e8f6f3">'
        '<main style="margin:auto;max-width:430px;background:#12253c;border:1px solid #315568;'
        'border-radius:16px;padding:28px"><h1 style="color:#34d2b5">ORION</h1>'
        '<p>'+intro+'</p><div style="background:#ecfff9;color:#095b4e;border-radius:12px;'
        'text-align:center;font-size:34px;font-weight:bold;letter-spacing:9px;'
        'padding:18px 6px">'+secret+'</div><p>Válido por 10 minutos · uso único.</p>'
        '<p style="font-size:13px;color:#a9c9c2">Se não solicitou este código, ignore a mensagem.</p>'
        '</main></body></html>'
    )
    msg.add_alternative(html,subtype="html")
    if os.getenv("PANEL_EMAIL_TRANSPORT")=="resend_https":
        # Railway Free/Trial/Hobby blocks outbound SMTP; use HTTPS (port 443).
        # The existing PANEL_SMTP_PASSWORD holds the Resend sending API key.
        response=httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization":"Bearer "+password,"Content-Type":"application/json"},
            json={"from":sender,"to":[recipient],"subject":subject,
                  "text":msg.get_body(preferencelist=("plain",)).get_content(),
                  "html":html},
            timeout=12.0,
        )
        if response.status_code<200 or response.status_code>=300:
            raise EmailTransportError("Resend rejeitou o envio (HTTP "+
                                      str(response.status_code)+")")
        if not response.json().get("id"):
            raise EmailTransportError("Resend não confirmou o identificador do envio")
        return
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


def _digest_code(email, purpose, code):
    # HMAC avoids exposing a six-digit code by brute-forcing DB dumps.
    secret=os.environ["PANEL_OTP_SECRET"].encode("utf-8")
    return hmac.new(secret,(purpose+":"+email+":"+code).encode(),hashlib.sha256).hexdigest()


async def _issue_token(session, account_id, purpose, email):
    code=f"{secrets.randbelow(1_000_000):06d}"
    duration=timedelta(minutes=10)
    await session.execute(text("""
        UPDATE panel_email_tokens SET used_at=now()
        WHERE account_id=:id AND purpose=:purpose AND used_at IS NULL
    """),{"id":account_id,"purpose":purpose})
    await session.execute(text("""
        INSERT INTO panel_email_tokens(account_id,token_digest,purpose,expires_at)
        VALUES (:id,:digest,:purpose,:expiry)
    """),{"id":account_id,"digest":_digest_code(email,purpose,code),
          "purpose":purpose,"expiry":datetime.now(timezone.utc)+duration})
    return code


async def _consume_code(session,email,purpose,code):
    row=(await session.execute(text("""
        SELECT t.id,t.account_id,t.token_digest,t.attempts
        FROM panel_email_tokens t
        JOIN panel_accounts a ON a.id=t.account_id
        WHERE a.email=:email AND t.purpose=:purpose AND t.used_at IS NULL
          AND t.expires_at>now()
        ORDER BY t.created_at DESC LIMIT 1 FOR UPDATE OF t
    """),{"email":email,"purpose":purpose})).mappings().first()
    if not row:return None
    accepted=hmac.compare_digest(row["token_digest"],_digest_code(email,purpose,code))
    if not accepted:
        await session.execute(text("""
            UPDATE panel_email_tokens SET attempts=attempts+1,
              used_at=CASE WHEN attempts+1>=5 THEN now() ELSE used_at END
            WHERE id=:id
        """),{"id":row["id"]})
        return None
    await session.execute(text(
        "UPDATE panel_email_tokens SET used_at=now() WHERE id=:id"
    ),{"id":row["id"]})
    return row["account_id"]


@router.get("/features")
async def features():
    return {"registration":mail_ready() and (
                os.getenv("PANEL_REGISTRATION_ENABLED")=="1" or _test_mode()),
            "passwordRecovery":mail_ready(),
            "testMode":_test_mode()}


@router.post("/register",status_code=202)
async def register(payload:Register,request:Request):
    _same_origin(request)
    url=_require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"O cadastro está limitado ao e-mail autorizado nos testes")
    if os.getenv("PANEL_REGISTRATION_ENABLED")!="1" and not _test_mode():
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
            token=await _issue_token(session,account_id,"verify",payload.email)
    try:await asyncio.to_thread(_send_email,payload.email,"verify",token,url)
    except (OSError,smtplib.SMTPException,httpx.HTTPError,EmailTransportError,ValueError,TimeoutError):
        raise HTTPException(503,"Não foi possível enviar o e-mail. Tente novamente mais tarde") from None
    return {"ok":True,"message":"Digite o código de 6 dígitos enviado ao seu e-mail."}


@router.post("/verify")
async def verify(payload:OneTimeCode,request:Request):
    _same_origin(request)
    _require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"Validação disponível somente para o e-mail de teste")
    await _throttle(request,"verify",payload.email,count=8,window=900)
    account_id=None
    async with _storage()() as session:
        async with session.begin():
            account_id=await _consume_code(session,payload.email,"verify",payload.code)
            if account_id:
                await session.execute(text("""
                    UPDATE panel_accounts SET verified_at=coalesce(verified_at,now())
                    WHERE id=:id
                """),{"id":account_id})
    if not account_id:raise HTTPException(400,"Código inválido, expirado ou sem tentativas disponíveis")
    return {"ok":True,"message":"E-mail confirmado. Agora você pode entrar."}


@router.post("/verify/resend")
async def resend_verify(payload:RecoveryStart,request:Request):
    _same_origin(request)
    url=_require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"Reenvio disponível somente para o e-mail de teste")
    if os.getenv("PANEL_REGISTRATION_ENABLED")!="1" and not _test_mode():
        raise HTTPException(503,"Cadastros não habilitados")
    await _throttle(request,"resend",payload.email,count=3,window=3600)
    token=None
    async with _storage()() as session:
        async with session.begin():
            account=(await session.execute(text("""
                SELECT id FROM panel_accounts
                WHERE email=:email AND verified_at IS NULL FOR UPDATE
            """),{"email":payload.email})).mappings().first()
            if account:
                token=await _issue_token(session,account["id"],"verify",payload.email)
    if token:
        try:await asyncio.to_thread(_send_email,payload.email,"verify",token,url)
        except (OSError,smtplib.SMTPException,httpx.HTTPError,EmailTransportError,ValueError,TimeoutError):
            raise HTTPException(503,"Não foi possível reenviar. Tente mais tarde") from None
    return {"ok":True,"message":"Se a conta aguarda confirmação, um novo código será enviado."}


@router.post("/password/forgot")
async def forgot(payload:RecoveryStart,request:Request):
    _same_origin(request)
    url=_require_mail()
    await _throttle(request,"forgot",payload.email,count=3,window=3600)
    if not _allowed_recipient(payload.email):
        return {"ok":True,"message":"Se esse e-mail estiver cadastrado, enviaremos um código."}
    token=None
    async with _storage()() as session:
        async with session.begin():
            account=(await session.execute(text("""
                SELECT id FROM panel_accounts WHERE email=:email AND verified_at IS NOT NULL
            """),{"email":payload.email})).mappings().first()
            if account:
                token=await _issue_token(session,account["id"],"reset",payload.email)
    if token:
        try:await asyncio.to_thread(_send_email,payload.email,"reset",token,url)
        except (OSError,smtplib.SMTPException,httpx.HTTPError,EmailTransportError,ValueError,TimeoutError):
            # Always give the same response whether or not this e-mail exists.
            pass
    return {"ok":True,"message":"Se esse e-mail estiver cadastrado, enviaremos um código."}


@router.post("/password/reset")
async def reset(payload:ResetPassword,request:Request):
    _same_origin(request)
    _require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"Recuperação disponível somente para o e-mail de teste")
    await _throttle(request,"reset",payload.email,count=8,window=900)
    account_id=None
    async with _storage()() as session:
        async with session.begin():
            account_id=await _consume_code(session,payload.email,"reset",payload.code)
            if account_id:
                await session.execute(text("""
                    UPDATE panel_accounts SET password_hash=:hashed,
                    auth_version=auth_version+1 WHERE id=:id
                """),{"hashed":_hasher.hash(payload.password),"id":account_id})
                await session.execute(text("""
                    UPDATE panel_email_tokens SET used_at=now()
                    WHERE account_id=:id AND used_at IS NULL
                """),{"id":account_id})
    if not account_id:raise HTTPException(400,"Código inválido, expirado ou sem tentativas disponíveis")
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
