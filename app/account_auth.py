"""Persistent Orion accounts: email proof, password recovery and privileged owner claim.

Only app_support accesses these tables; public registration NEVER creates an admin.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import re
import secrets
from uuid import UUID

from argon2 import PasswordHasher, exceptions as argon2_errors
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.panel_auth import _same_origin, _redis, _password_valid, panel_actor
from app.account_business import reject_multiple_businesses
from app import email_outbox, email_transport

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
    password: str = Field(min_length=8, max_length=128)

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
    password: str = Field(min_length=8, max_length=128)


class ClaimOwner(BaseModel):
    legacy_password: str = Field(min_length=1, max_length=200)


def _storage():
    from app.catalog_api import support_session_factory
    return support_session_factory()


def _public_url():
    try:
        return email_transport.public_origin()
    except ValueError:
        raise HTTPException(503,"Endereço público para e-mails não configurado") from None


def _test_mode():
    return os.getenv("PANEL_TEST_MODE")=="1"


def _test_email():
    return os.getenv("PANEL_TEST_EMAIL","").strip().lower()


def _allowed_recipient(email):
    return email_transport.allowed_recipient(email)


def mail_ready():
    return email_transport.configured()


def _require_mail():
    if not mail_ready():raise HTTPException(503,"Envio de e-mails ainda não configurado")
    return _public_url()


async def _mail_request_quota(email):
    cache = _redis()
    try:
        return await email_outbox.request_quota(cache, email)
    finally:
        await cache.aclose()


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
    # Same lock order for issue and consume: account, then token. Serialization
    # prevents parallel issuance leaving multiple active codes behind.
    await session.execute(text("SELECT id FROM panel_accounts WHERE id=:id FOR UPDATE"), {"id": account_id})
    for _ in range(16):
        code = f"{secrets.randbelow(1_000_000):06d}"
        digest = _digest_code(email, purpose, code)
        used = (await session.execute(text("SELECT 1 FROM panel_email_tokens WHERE token_digest=:digest"),
                                      {"digest": digest})).scalar_one_or_none()
        if used is None:
            break
    else:
        raise HTTPException(503, "Não foi possível gerar o código. Tente mais tarde")
    expiry = datetime.now(timezone.utc) + timedelta(minutes=10)
    await session.execute(text("""
        UPDATE panel_email_tokens SET used_at=now()
        WHERE account_id=:id AND purpose=:purpose AND used_at IS NULL
    """),{"id":account_id,"purpose":purpose})
    token_id = (await session.execute(text("""
        INSERT INTO panel_email_tokens(account_id,token_digest,purpose,expires_at)
        VALUES (:id,:digest,:purpose,:expiry) RETURNING id
    """),{"id":account_id,"digest":digest,
          "purpose":purpose,"expiry":expiry})).scalar_one()
    await email_outbox.enqueue(session, kind=purpose, recipient=email,
        dedupe_key="token:" + str(token_id), account_id=account_id,
        token_id=token_id, expires_at=expiry, code=code)
    return code


async def _consume_code(session,email,purpose,code):
    await session.execute(text("SELECT id FROM panel_accounts WHERE email=:email FOR UPDATE"), {"email": email})
    row=(await session.execute(text("""
        SELECT t.id,t.account_id,t.token_digest,t.attempts,t.expires_at
        FROM panel_email_tokens t
        JOIN panel_accounts a ON a.id=t.account_id
        WHERE a.email=:email AND t.purpose=:purpose AND t.used_at IS NULL
          AND t.expires_at>clock_timestamp()
        ORDER BY t.created_at DESC LIMIT 1 FOR UPDATE OF t
    """),{"email":email,"purpose":purpose})).mappings().first()
    if not row or row["expires_at"] <= datetime.now(timezone.utc):return None
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
    return {"registration":os.getenv("PANEL_REGISTRATION_ENABLED")=="1" and mail_ready(),
            "passwordRecovery":mail_ready(),
            "testMode":_test_mode()}


@router.post("/register",status_code=201)
async def register(payload:Register,request:Request):
    _same_origin(request)
    if os.getenv("PANEL_REGISTRATION_ENABLED")!="1":
        raise HTTPException(503,"Cadastro ainda não habilitado")
    _require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403, "Cadastro disponível somente para o e-mail de teste")
    await _throttle(request,"register",payload.email,count=3,window=3600)
    if not await _mail_request_quota(payload.email):
        raise HTTPException(429, "Aguarde antes de solicitar outro código para este e-mail")
    async with _storage()() as session:
        try:
            async with session.begin():
                prior = (await session.execute(text("""SELECT id,password_hash,registration_pending
                    FROM panel_accounts WHERE email=:email FOR UPDATE"""), {"email":payload.email})).mappings().first()
                if prior:
                    try:
                        resume = prior['registration_pending'] and _hasher.verify(prior['password_hash'],payload.password)
                    except (argon2_errors.VerificationError,argon2_errors.InvalidHashError):
                        resume = False
                    if not resume:
                        raise HTTPException(409, "Já existe uma conta com este e-mail. Entre ou recupere a senha.")
                    # A deliberate repeat resumes the same pending account. It
                    # never replaces its password/name or creates another email.
                else:
                    account_id = (await session.execute(text("""
                        INSERT INTO panel_accounts(name,email,password_hash,role,registration_pending)
                        VALUES (:name,:email,:password,'member',true) RETURNING id
                    """),{"name":payload.name,"email":payload.email,
                          "password":_hasher.hash(payload.password)})).scalar_one()
                    await _issue_token(session,account_id,"verify",payload.email)
        except IntegrityError:
            raise HTTPException(409,"Já existe uma conta com este e-mail. Entre ou recupere a senha.") from None
    return {"ok":True,"verificationRequired":True,"codeRequested":not bool(prior),
            "message":"Confirme seu e-mail para concluir o cadastro. O código pode levar alguns instantes para chegar."}


@router.post("/verify")
async def verify(payload:OneTimeCode,request:Request):
    _same_origin(request)
    _require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"Validação disponível somente para o e-mail de teste")
    await _throttle(request,"verify",payload.email,count=8,window=900)
    account_id=None
    completed=False
    async with _storage()() as session:
        async with session.begin():
            account_id=await _consume_code(session,payload.email,"verify",payload.code)
            if account_id:
                completed = (await session.execute(text("SELECT registration_pending FROM panel_accounts WHERE id=:id"),
                                                   {"id":account_id})).scalar_one()
                await session.execute(text("""
                    UPDATE panel_accounts SET verified_at=coalesce(verified_at,now()),registration_pending=false
                    WHERE id=:id
                """),{"id":account_id})
    if not account_id:raise HTTPException(400,"Código inválido, expirado ou sem tentativas disponíveis")
    return {"ok":True,"registrationCompleted":completed,
            "message":"Conta criada e e-mail confirmado. Entre para continuar." if completed else "E-mail confirmado. Agora você pode entrar."}


@router.post("/verify/resend")
async def resend_verify(payload:RecoveryStart,request:Request):
    _same_origin(request)
    _require_mail()
    if not _allowed_recipient(payload.email):
        raise HTTPException(403,"Reenvio disponível somente para o e-mail de teste")
    await _throttle(request,"resend",payload.email,count=3,window=3600)
    if not await _mail_request_quota(payload.email):
        return {"ok":True,"message":"Se a conta aguarda confirmação, um novo código será enviado."}
    async with _storage()() as session:
        async with session.begin():
            account=(await session.execute(text("""
                SELECT id FROM panel_accounts
                WHERE email=:email AND verified_at IS NULL FOR UPDATE
            """),{"email":payload.email})).mappings().first()
            if account:
                await _issue_token(session,account["id"],"verify",payload.email)
    return {"ok":True,"message":"Se a conta aguarda confirmação, um novo código será enviado."}


@router.post("/password/forgot")
async def forgot(payload:RecoveryStart,request:Request):
    _same_origin(request)
    _require_mail()
    await _throttle(request,"forgot",payload.email,count=3,window=3600)
    if not await _mail_request_quota(payload.email) or not _allowed_recipient(payload.email):
        return {"ok":True,"message":"Se esse e-mail estiver cadastrado, enviaremos um código."}
    async with _storage()() as session:
        async with session.begin():
            account=(await session.execute(text("""
                SELECT id FROM panel_accounts WHERE email=:email FOR UPDATE
            """),{"email":payload.email})).mappings().first()
            if account:
                await _issue_token(session,account["id"],"reset",payload.email)
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
                version = (await session.execute(text("""
                    UPDATE panel_accounts SET password_hash=:hashed,
                    verified_at=coalesce(verified_at,now()),registration_pending=false,
                    auth_version=auth_version+1 WHERE id=:id RETURNING auth_version
                """),{"hashed":_hasher.hash(payload.password),"id":account_id})).scalar_one()
                await session.execute(text("""
                    UPDATE panel_email_tokens SET used_at=now()
                    WHERE account_id=:id AND used_at IS NULL
                """),{"id":account_id})
                await email_outbox.enqueue(session, kind="password_changed", recipient=payload.email,
                    dedupe_key="password-changed:" + str(account_id) + ":" + str(version), account_id=account_id)
    if not account_id:raise HTTPException(400,"Código inválido, expirado ou sem tentativas disponíveis")
    return {"ok":True,"message":"Senha atualizada. Entre novamente."}


async def authenticate_account(email,password):
    if "@" not in email or len(email)>254:return None
    async with _storage()() as session:
        row=(await session.execute(text("""
            SELECT id,name,email,password_hash,role,auth_version,verified_at,registration_pending
            FROM panel_accounts WHERE email=:email
              AND COALESCE(to_jsonb(panel_accounts)->>'auth_provider','password')='password'
        """),{"email":email.strip().lower()})).mappings().first()
    try:
        accepted=_hasher.verify(row["password_hash"] if row else _FAKE,password)
    except (argon2_errors.VerificationError,argon2_errors.InvalidHashError):
        accepted=False
    if not row or not accepted:return None
    if row['registration_pending']:
        raise HTTPException(403, {"code":"email_confirmation_required",
            "message":"Confirme seu e-mail para concluir o cadastro.","email":row['email']})
    return {"id":str(row["id"]),"name":row["name"],"email":row["email"],
            "role":row["role"],"version":row["auth_version"]}


async def current_account(account_id,version):
    async with _storage()() as session:
        row=(await session.execute(text("""
            SELECT role,auth_version,verified_at,name,email,registration_pending FROM panel_accounts WHERE id=:id
              AND COALESCE(to_jsonb(panel_accounts)->>'auth_provider','password')='password'
        """),{"id":UUID(account_id)})).mappings().first()
    if not row or row["auth_version"]!=version or row.get('registration_pending',False):
        raise HTTPException(401,"Sua sessão expirou. Entre novamente")
    return row


_CURRENT_ACCOUNT_SCOPE_SQL = text("""
            SELECT a.role,a.auth_version,a.verified_at,a.name,a.email,a.registration_pending,m.role AS membership_role,
              (SELECT count(*) FROM tenant_memberships all_memberships
               WHERE all_memberships.account_id=a.id AND all_memberships.active) AS active_business_count
            FROM panel_accounts a LEFT JOIN tenant_memberships m
              ON m.account_id=a.id AND m.tenant_id=:tenant AND m.active
            WHERE a.id=:id AND COALESCE(to_jsonb(a)->>'auth_provider','password')='password'
        """)


async def current_account_and_membership(account_id, version, tenant_id):
    """One current, read-only snapshot; no authority survives this request."""
    # Borrow the same support pool, with no new engine or connection allowance.
    # This SELECT needs no explicit transaction. SQLAlchemy restores the default
    # isolation on return, including failures; ordinary writes remain atomic.
    async with _storage().kw["bind"].connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        row = (await connection.execute(_CURRENT_ACCOUNT_SCOPE_SQL,
            {"id": UUID(account_id), "tenant": tenant_id})).mappings().first()
    if not row or row["auth_version"] != version or row.get('registration_pending',False):
        raise HTTPException(401, "Sua sessão expirou. Entre novamente")
    if row.get("active_business_count", 0) > 1:
        reject_multiple_businesses()
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


class ChangeOwnPassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)


@router.post("/account/password")
async def change_own_password(payload: ChangeOwnPassword, request: Request,
                              data: dict = Depends(panel_actor)):
    """Self-service change for verified password accounts only.

    The version increment invalidates every existing session, including the
    current one. Legacy environment-admin credentials cannot be changed here.
    """
    if not data.get("account_id") or data.get("auth_provider") == "legacy_admin":
        raise HTTPException(403, "Esta conta não utiliza senha pessoal")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(422, "As novas senhas não coincidem")
    if hmac.compare_digest(payload.current_password, payload.new_password):
        raise HTTPException(422, "Escolha uma senha diferente da atual")
    await _throttle(request, "change-password", str(data["account_id"]), count=5, window=900)
    async with _storage()() as session:
        async with session.begin():
            row = (await session.execute(text("""
                SELECT id,password_hash,email,auth_version,registration_pending
                FROM panel_accounts WHERE id=:id
                AND COALESCE(to_jsonb(panel_accounts)->>'auth_provider','password')='password'
                FOR UPDATE
            """), {"id": UUID(str(data["account_id"]))})).mappings().first()
            if not row or row["registration_pending"]:
                raise HTTPException(403, "Conta indisponível para alteração de senha")
            try:
                valid = _hasher.verify(row["password_hash"], payload.current_password)
            except (argon2_errors.VerificationError, argon2_errors.InvalidHashError):
                valid = False
            if not valid:
                raise HTTPException(403, "Senha atual incorreta")
            await session.execute(text("""
                UPDATE panel_accounts SET password_hash=:hash,auth_version=auth_version+1
                WHERE id=:id
            """), {"id": row["id"], "hash": _hasher.hash(payload.new_password)})
            if mail_ready() and row["email"]:
                await email_outbox.enqueue(session, kind="password_changed",
                    recipient=row["email"],
                    dedupe_key="password-changed:" + str(row["id"]) + ":" + str(row["auth_version"] + 1),
                    account_id=row["id"])
    return {"ok": True, "requires_login": True,
            "message": "Senha alterada com sucesso. Entre novamente."}
