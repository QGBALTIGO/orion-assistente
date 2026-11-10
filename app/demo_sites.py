"""Administrator-owned commercial previews, isolated from operational tenants.

The public capability is a random, expiring read-only URL. The only database
objects this module writes are commercial_demo_sites and commercial_demo_events.
No customer, appointment, channel, account membership or outbound job is created.
"""
import hashlib
import html
import ipaddress
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.account_auth import _storage
from app.admin_accounts import require_platform_owner
from app.database import get_session_factory


router = APIRouter(tags=["Demonstrações comerciais"])
STATIC = Path(__file__).parent / "static"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
MEDIA_PATTERN = re.compile(r"^/media/[0-9a-f-]{36}/[0-9a-f-]{36}\.webp$")
ASSET_PATTERN = re.compile(r"^[a-f0-9]{64}\.webp$")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, nosnippet",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data:; font-src 'self'; connect-src 'self'; "
        "frame-src https://www.google.com https://maps.google.com https://www.instagram.com; "
        "frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    ),
}
Status = Literal["draft", "published", "archived"]


def utcnow():
    return datetime.now(timezone.utc)


def public_url(value, *, media=False, allow_http=False):
    """Validate links without fetching them or permitting internal addresses."""
    if value is None:
        return None
    if media and MEDIA_PATTERN.fullmatch(value):
        return value
    if any(ord(char) < 33 for char in value) or "\\" in value:
        raise ValueError("URL pública inválida")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        scheme = {"http", "https"} if allow_http else {"https"}
        if (parsed.scheme not in scheme or parsed.username or parsed.password
                or parsed.port not in (None, 80 if parsed.scheme == "http" else 443)):
            raise ValueError("Informe uma URL pública HTTPS")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            ascii_host = host.encode("idna").decode("ascii").lower()
            if ("." not in ascii_host or not re.fullmatch(r"[a-z0-9.-]+", ascii_host)
                    or ascii_host.endswith((".localhost", ".local", ".internal", ".invalid"))):
                raise ValueError("Informe um domínio público")
        else:
            if not address.is_global:
                raise ValueError("Endereços internos não são permitidos")
    except (UnicodeError, ValueError):
        raise ValueError("URL pública inválida") from None
    return value


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DemoSite(DemoModel):
    title: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    theme_color: str = Field(default="#2e7bf6", pattern=r"^#[0-9a-fA-F]{6}$")
    logo_url: str | None = Field(default=None, max_length=2048)
    cover_url: str | None = Field(default=None, max_length=2048)
    address: str | None = Field(default=None, max_length=500)
    address_display: str | None = Field(default=None, max_length=700)
    address_number: str | None = Field(default=None, max_length=30)
    address_complement: str | None = Field(default=None, max_length=160)
    address_no_number: bool | None = None
    neighborhood: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=80)
    postal_code: str | None = Field(default=None, max_length=20)
    contact_phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    whatsapp_phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    instagram_url: str | None = Field(default=None, max_length=2048)
    facebook_url: str | None = Field(default=None, max_length=2048)
    tiktok_url: str | None = Field(default=None, max_length=2048)
    maps_url: str | None = Field(default=None, max_length=2048)
    external_url: str | None = Field(default=None, max_length=2048)
    show_prices: bool = True
    hide_orion_brand: Literal[False] = False
    amenity_wifi: bool | None = None
    amenity_parking: bool | None = None
    amenity_accessibility: bool | None = None
    amenity_children: bool | None = None

    @field_validator("logo_url", "cover_url")
    @classmethod
    def image_url(cls, value):
        return public_url(value, media=True)

    @field_validator("instagram_url", "facebook_url", "tiktok_url", "maps_url", "external_url")
    @classmethod
    def website_url(cls, value):
        return public_url(value)


class DemoProfessional(DemoModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    specialty: str | None = Field(default=None, max_length=250)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    image_url: str | None = Field(default=None, max_length=2048)

    @field_validator("image_url")
    @classmethod
    def image(cls, value):
        return public_url(value, media=True)


class DemoService(DemoModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=120)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    image_url: str | None = Field(default=None, max_length=2048)
    duration_min: int | None = Field(default=None, strict=True, ge=1, le=1440)
    price_cents: int | None = Field(default=None, strict=True, ge=0, le=100000000)
    price_mode: Literal["fixed", "from"] = "fixed"
    catalog_kind: Literal["base", "combo"] = "base"
    featured: bool = False
    sort_order: int = Field(default=0, strict=True, ge=0, le=10000)
    professional_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("image_url")
    @classmethod
    def image(cls, value):
        return public_url(value, media=True)


class DemoInterval(DemoModel):
    open: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    close: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def ordered(self):
        if self.open >= self.close:
            raise ValueError("O horário final deve ser posterior ao inicial")
        return self


class DemoDay(DemoModel):
    weekday: int = Field(strict=True, ge=0, le=6)
    # None means unknown; [] means explicitly closed. Never fill missing days.
    intervals: list[DemoInterval] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def no_overlaps(self):
        intervals = sorted(self.intervals or [], key=lambda item: item.open)
        if any(left.close > right.open for left, right in zip(intervals, intervals[1:])):
            raise ValueError("Os intervalos do expediente não podem se sobrepor")
        return self


class DemoCatalogue(DemoModel):
    site: DemoSite
    services: list[DemoService] = Field(default_factory=list, max_length=100)
    professionals: list[DemoProfessional] = Field(default_factory=list, max_length=50)
    opening_hours: list[DemoDay] = Field(default_factory=list, max_length=7)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Fuso horário inválido") from None
        return value

    @model_validator(mode="after")
    def consistent_catalogue(self):
        professionals = {person.id for person in self.professionals}
        if len(professionals) != len(self.professionals):
            raise ValueError("Profissionais duplicados")
        if len({service.id for service in self.services}) != len(self.services):
            raise ValueError("Serviços duplicados")
        if len({day.weekday for day in self.opening_hours}) != len(self.opening_hours):
            raise ValueError("Dias de expediente duplicados")
        for service in self.services:
            if (len(set(service.professional_ids)) != len(service.professional_ids)
                    or not set(service.professional_ids).issubset(professionals)):
                raise ValueError("Vínculo de profissional inválido")
        if len(self.model_dump_json().encode("utf-8")) > 200000:
            raise ValueError("O catálogo da demonstração excede o limite")
        return self


class DemoDraft(DemoModel):
    source_id: str = Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]+$")
    catalogue: DemoCatalogue
    source_urls: list[str] = Field(default_factory=list, max_length=30)
    review_notes: str = Field(default="", max_length=4000)
    pending_fields: list[str] = Field(default_factory=list, max_length=40)
    reviewed_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None

    @field_validator("source_urls")
    @classmethod
    def valid_sources(cls, values):
        if any(len(value) > 2048 for value in values):
            raise ValueError("URL de origem muito longa")
        return list(dict.fromkeys(public_url(value, allow_http=True) for value in values))

    @field_validator("pending_fields")
    @classmethod
    def valid_pending(cls, values):
        if any(not value.strip() or len(value) > 250 for value in values):
            raise ValueError("Pendência inválida")
        return list(dict.fromkeys(value.strip() for value in values))


class DemoCreate(DemoDraft):
    # Importing a bundle never publishes it as a side effect.
    status: Literal["draft"] = "draft"


class DemoUpdate(DemoDraft):
    expected_version: int = Field(strict=True, ge=1)
    status: Status


class DemoStatus(DemoModel):
    expected_version: int = Field(strict=True, ge=1)
    status: Status


def _fingerprint(payload):
    value = payload.model_dump(mode="json")
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _expiry(value, *, publishing=False):
    now = utcnow()
    expiry = value or now + timedelta(days=30)
    if expiry > now + timedelta(days=366):
        raise HTTPException(422, "A validade máxima da demonstração é de um ano")
    if publishing and expiry <= now:
        raise HTTPException(422, "Renove a validade antes de disponibilizar a demonstração")
    return expiry


def _admin_item(row, *, full=True):
    result = {key: row[key] for key in (
        "id", "source_id", "status", "expires_at", "version", "pending_fields",
        "reviewed_at", "created_at", "updated_at",
    )}
    result["title"] = row.get("title") or row["catalogue"]["site"]["title"]
    result["public_url"] = "/demo/" + row["token"]
    if full:
        result.update({key: row[key] for key in ("catalogue", "source_urls", "review_notes")})
    return jsonable_encoder(result)


async def _event(db, demo_id, actor, action, version):
    await db.execute(text("""INSERT INTO commercial_demo_events
        (demo_id,actor_id,action,version) VALUES(:demo,:actor,:action,:version)"""),
        {"demo": demo_id, "actor": actor, "action": action, "version": version})


async def _find(db, demo_id, *, lock=False):
    row = (await db.execute(text("SELECT * FROM commercial_demo_sites WHERE id=:id" + (" FOR UPDATE" if lock else "")),
                            {"id": demo_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Demonstração não encontrada")
    return dict(row)


@router.get("/api/v1/admin/demos")
async def list_demos(response: Response, admin: UUID = Depends(require_platform_owner),
                     limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    response.headers.update(HEADERS)
    async with _storage()() as db:
        total = (await db.execute(text("SELECT count(*) FROM commercial_demo_sites"))).scalar_one()
        rows = (await db.execute(text("""SELECT id,source_id,token,status,expires_at,version,
            pending_fields,reviewed_at,created_at,updated_at,catalogue->'site'->>'title' AS title
            FROM commercial_demo_sites ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset"""),
            {"limit": limit, "offset": offset})).mappings().all()
    return {"items": [_admin_item(row, full=False) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/api/v1/admin/demos/{demo_id}")
async def get_demo(demo_id: UUID, response: Response, admin: UUID = Depends(require_platform_owner)):
    response.headers.update(HEADERS)
    async with _storage()() as db:
        return _admin_item(await _find(db, demo_id))


@router.post("/api/v1/admin/demos", status_code=201)
async def create_demo(payload: DemoCreate, response: Response, admin: UUID = Depends(require_platform_owner)):
    response.headers.update(HEADERS)
    digest = _fingerprint(payload)
    values = payload.model_dump(mode="json", exclude={"expires_at"})
    expiry = _expiry(payload.expires_at)
    if expiry <= utcnow():
        raise HTTPException(422, "Informe uma validade futura")
    params = {"id": uuid4(), "token": secrets.token_urlsafe(32), "actor": admin,
              "fingerprint": digest, "expires": expiry, "reviewed": payload.reviewed_at,
              "source": payload.source_id, "notes": payload.review_notes,
              **{key: json.dumps(values[key], ensure_ascii=False) for key in ("catalogue", "source_urls", "pending_fields")}}
    async with _storage()() as db:
        async with db.begin():
            row = (await db.execute(text("""INSERT INTO commercial_demo_sites
                (id,source_id,token,catalogue,source_urls,review_notes,pending_fields,reviewed_at,
                 expires_at,status,initial_fingerprint,created_by,updated_by)
                VALUES(:id,:source,:token,CAST(:catalogue AS jsonb),CAST(:source_urls AS jsonb),
                       :notes,CAST(:pending_fields AS jsonb),:reviewed,:expires,'draft',:fingerprint,:actor,:actor)
                ON CONFLICT(source_id) DO NOTHING RETURNING *"""), params)).mappings().first()
            replayed = row is None
            if replayed:
                row = (await db.execute(text("SELECT * FROM commercial_demo_sites WHERE source_id=:source FOR SHARE"),
                                        {"source": payload.source_id})).mappings().one()
                if row["initial_fingerprint"] != digest:
                    raise HTTPException(409, "Esta origem já possui uma demonstração. Abra o cadastro para revisar as alterações")
                response.status_code = 200
            else:
                await _event(db, row["id"], admin, "created", row["version"])
    return {"item": _admin_item(row), "replayed": replayed}


@router.put("/api/v1/admin/demos/{demo_id}")
async def update_demo(demo_id: UUID, payload: DemoUpdate, response: Response,
                      admin: UUID = Depends(require_platform_owner)):
    response.headers.update(HEADERS)
    async with _storage()() as db:
        async with db.begin():
            current = await _find(db, demo_id, lock=True)
            if payload.expected_version != current["version"]:
                raise HTTPException(409, "A demonstração mudou. Atualize antes de salvar")
            if payload.source_id != current["source_id"]:
                raise HTTPException(409, "A origem da demonstração não pode ser alterada")
            expiry = _expiry(payload.expires_at or current["expires_at"], publishing=payload.status == "published")
            values = payload.model_dump(mode="json")
            row = (await db.execute(text("""UPDATE commercial_demo_sites
                SET catalogue=CAST(:catalogue AS jsonb),source_urls=CAST(:source_urls AS jsonb),
                    review_notes=:notes,pending_fields=CAST(:pending_fields AS jsonb),reviewed_at=:reviewed,
                    expires_at=:expires,status=:status,updated_by=:actor,updated_at=clock_timestamp(),version=version+1
                WHERE id=:id AND version=:version RETURNING *"""),
                {"id": demo_id, "version": current["version"], "actor": admin, "expires": expiry,
                 "notes": payload.review_notes, "reviewed": payload.reviewed_at, "status": payload.status,
                 **{key: json.dumps(values[key], ensure_ascii=False) for key in ("catalogue", "source_urls", "pending_fields")}})).mappings().one()
            await _event(db, demo_id, admin, "updated", row["version"])
    return {"item": _admin_item(row), "replayed": False}


@router.patch("/api/v1/admin/demos/{demo_id}/status")
async def change_demo_status(demo_id: UUID, payload: DemoStatus, response: Response,
                             admin: UUID = Depends(require_platform_owner)):
    response.headers.update(HEADERS)
    async with _storage()() as db:
        async with db.begin():
            current = await _find(db, demo_id, lock=True)
            if payload.expected_version != current["version"]:
                raise HTTPException(409, "A demonstração mudou. Atualize antes de alterar o estado")
            _expiry(current["expires_at"], publishing=payload.status == "published")
            if payload.status == current["status"]:
                return {"item": _admin_item(current), "replayed": True}
            row = (await db.execute(text("""UPDATE commercial_demo_sites SET status=:status,
                updated_by=:actor,updated_at=clock_timestamp(),version=version+1
                WHERE id=:id AND version=:version RETURNING *"""),
                {"id": demo_id, "version": current["version"], "status": payload.status, "actor": admin})).mappings().one()
            await _event(db, demo_id, admin, payload.status, row["version"])
    return {"item": _admin_item(row), "replayed": False}


async def _published_demo(token):
    if not TOKEN_PATTERN.fullmatch(token):
        raise HTTPException(404, "Demonstração indisponível")
    # app_runtime has no table access. The function exposes only a live snapshot.
    async with get_session_factory()() as db:
        record = (await db.execute(text("SELECT public.read_commercial_demo(:token)"), {"token": token})).scalar_one_or_none()
    if not record or record.get("status") != "published":
        raise HTTPException(404, "Demonstração indisponível")
    try:
        expiry = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if expiry.tzinfo is None or expiry <= utcnow():
            raise ValueError("expired")
        catalogue = DemoCatalogue.model_validate(record["catalogue"]).model_dump(mode="json")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(404, "Demonstração indisponível") from None
    # Revalidate the stored public whitelist; source/review/account data can
    # never be returned by spreading an administrative record into the output.
    zone = ZoneInfo(catalogue["timezone"]) if catalogue["timezone"] else timezone.utc
    for service in catalogue["services"]:
        service["addons"] = []
    return {**catalogue, "demo": True, "preview": True, "published": True,
            "booking_enabled": False, "expires_at": expiry.isoformat(),
            "today": utcnow().astimezone(zone).date().isoformat(), "booking_horizon_days": 30}


@router.get("/api/v1/public/demos/{token}")
async def public_demo(token: str, response: Response):
    response.headers.update(HEADERS)
    return await _published_demo(token)


@router.get("/demo-assets/{asset}", include_in_schema=False)
async def demo_asset(asset: str):
    # Only immutable WebP files bundled with this release are addressable.
    # This is deliberately not an upload, proxy or arbitrary-file endpoint.
    if not ASSET_PATTERN.fullmatch(asset):
        raise HTTPException(404, "Imagem indisponível")
    directory = (STATIC / "demo-assets").resolve()
    path = directory / asset
    if path.resolve().parent != directory or not path.is_file():
        raise HTTPException(404, "Imagem indisponível")
    return FileResponse(path, media_type="image/webp", headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "X-Robots-Tag": "noindex, noimageindex",
    })


@router.get("/demo/{token}", include_in_schema=False)
async def demo_page(token: str):
    catalogue = await _published_demo(token)
    document = (STATIC / "orion-public.html").read_text(encoding="utf-8")
    title = html.escape("Demonstração • " + catalogue["site"]["title"] + " | Orion")
    document = re.sub(r"<title>.*?</title>", lambda match: "<title>" + title + "</title>", document, count=1, flags=re.S)
    document = document.replace("<head>", '<head>\n  <meta name="robots" content="noindex,nofollow,nosnippet">', 1)
    document = re.sub(r'<meta name="description" content="[^"]*">',
                      '<meta name="description" content="Demonstração personalizada do Orion. Simulações sem reservas reais.">',
                      document, count=1)
    return HTMLResponse(document, headers=HEADERS)
