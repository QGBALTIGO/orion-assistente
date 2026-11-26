"""Self-service credentials for the existing environment-bound administrator.

The environment digest remains the identity binding used by the worker.
A personal password, once present, is the only password accepted by the API.
No credentials are created by a deployment. All changes require reauthentication.
"""
import asyncio
import hmac
import json
from uuid import UUID

from argon2 import PasswordHasher, exceptions as argon2_errors
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import legacy_admin_identity as identity
from app.catalog_api import support_session_factory

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
_SELECT = """
SELECT a.id,a.name,a.auth_version,a.legacy_username,a.legacy_credential_digest,
       a.role
FROM public.panel_accounts a
WHERE a.auth_provider='legacy_admin' AND a.role='admin'
  AND a.email IS NULL AND a.password_hash IS NULL
"""

def _bound(row):
    if not row or not identity.enabled():
        return False
    username, digest = identity.configuration_binding()
    return row["legacy_username"] == username and hmac.compare_digest(row["legacy_credential_digest"], digest)

def _check(row, password, environment_check):
    if row and row["personal_hash"] is not None:
        if not _bound(row):
            return False
        try:
            return _hasher.verify(row["personal_hash"], password)
        except (argon2_errors.VerificationError, argon2_errors.InvalidHashError):
            return False
    # An already provisioned native admin must never fall back to the
    # support role when its identity binding is broken or disabled.
    if row and not _bound(row):
        return False
    return environment_check(password)

async def _row(db, *, exclusive=False):
    suffix = " FOR UPDATE OF a" if exclusive else " FOR SHARE OF a"
    row = (await db.execute(text(_SELECT+suffix))).mappings().one_or_none()
    if row is None:
        return None
    # A second statement AFTER taking the account lock is essential:
    # READ COMMITTED can otherwise pair a refreshed auth_version with
    # an older joined password row while a concurrent rotation commits.
    personal_hash = (await db.execute(text(
        "SELECT password_hash FROM public.orion_legacy_admin_passwords WHERE account_id=:id"
    ), {"id":row["id"]})).scalar_one_or_none()
    return {**row, "personal_hash":personal_hash}

async def authenticate(password, environment_check):
    """Return (accepted, account) with a consistent credential generation."""
    try:
        async with support_session_factory()() as db:
            async with db.begin():
                row = await _row(db)
                accepted = await asyncio.to_thread(_check, row, password, environment_check)
                if not accepted:
                    return False, None
                if row is None:
                    return True, None  # Preserve the unprovisioned support login.
                return True, {"id":str(row["id"]), "name":row["name"], "email":None,
                              "role":"admin", "version":row["auth_version"],
                              "auth_provider":"legacy_admin"}
    except (SQLAlchemyError, ValueError, RuntimeError, OSError):
        raise HTTPException(503,"Acesso administrativo temporariamente indisponível") from None

async def verify_authorization(password, environment_check):
    """The existing owner-claim flow must not accept a retired password."""
    accepted, _ = await authenticate(password, environment_check)
    return accepted

async def parse_passwords(request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Envie os dados no formato JSON")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>8192:
            raise HTTPException(413, "Solicitação muito grande")
    try:
        data=json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Dados de senha inválidos") from None
    if not isinstance(data,dict) or set(data)!={"current_password","new_password","confirm_password"}:
        raise HTTPException(422, "Preencha os três campos de senha")
    current,new,confirm=(data[k] for k in ("current_password","new_password","confirm_password"))
    if not all(isinstance(v,str) for v in (current,new,confirm)):
        raise HTTPException(422, "Dados de senha inválidos")
    if not 1<=len(current)<=200 or not 8<=len(new)<=128 or not 8<=len(confirm)<=128:
        raise HTTPException(422, "A nova senha deve ter entre 8 e 128 caracteres")
    try:
        current_bytes,new_bytes,confirm_bytes=(v.encode("utf-8") for v in (current,new,confirm))
    except UnicodeError:
        raise HTTPException(422, "Caracteres de senha inválidos") from None
    if not hmac.compare_digest(new_bytes,confirm_bytes):
        raise HTTPException(422, "As novas senhas não coincidem")
    if hmac.compare_digest(current_bytes,new_bytes):
        raise HTTPException(422, "Escolha uma senha diferente da atual")
    return current,new,confirm

async def _change(request,data,environment_check):
    if data.get("role")!="admin" or data.get("maintenance") or not data.get("account_id"):
        raise HTTPException(403,"Operação exclusiva do administrador autenticado")
    current,new,confirm=await parse_passwords(request)
    from app.account_auth import _throttle, ChangeOwnPassword, change_own_password
    if data.get("auth_provider")!="legacy_admin":
        if len(current)>128:
            raise HTTPException(422,"Senha atual inválida")
        return await change_own_password(ChangeOwnPassword(current_password=current,new_password=new,
                                          confirm_password=confirm),request,data)
    await _throttle(request,"admin-personal-password",str(data["account_id"]),count=5,window=900)
    async with support_session_factory()() as db:
        async with db.begin():
            row=await _row(db,exclusive=True)
            if not _bound(row) or str(row["id"])!=str(data["account_id"]) or row["auth_version"]!=data.get("auth_version"):
                raise HTTPException(401,"Sessão expirada. Entre novamente.")
            if not await asyncio.to_thread(_check,row,current,environment_check):
                raise HTTPException(403,"Senha atual incorreta")
            hashed=await asyncio.to_thread(_hasher.hash,new)
            await db.execute(text("""
                INSERT INTO public.orion_legacy_admin_passwords(account_id,password_hash)
                VALUES(:id,:hash)
                ON CONFLICT(account_id) DO UPDATE SET password_hash=EXCLUDED.password_hash,
                    updated_at=now()
            """),{"id":row["id"],"hash":hashed})
            result=await db.execute(text("""
                UPDATE public.panel_accounts SET auth_version=auth_version+1
                WHERE id=:id AND role='admin' AND auth_provider='legacy_admin' AND auth_version=:version
            """),{"id":row["id"],"version":row["auth_version"]})
            if result.rowcount!=1:
                raise HTTPException(409,"A sessão mudou. Entre novamente.")
    return {"ok":True,"requires_login":True,"message":"Senha alterada. Entre novamente."}


async def change(request,data,environment_check):
    try:
        return await _change(request,data,environment_check)
    except (SQLAlchemyError, ValueError, RuntimeError, OSError):
        raise HTTPException(503,"Não foi possível confirmar a alteração da senha") from None
