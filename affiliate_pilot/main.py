from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import time as wall_time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

from .engine import DEMO, Engine
from .card_style import ImageStyle
from .offers import card_png
from .analytics import Analytics
from .niches import NICHES, PUBLIC_NICHES, SORTS, search_niche
from .offers import caption, formatted_html, local_day_start, next_slot, render_card, validate_length, validate_template
from .phrase import configured, generate_phrase, initial_phrase, phrase_suggestions
from .promotech import PromoTech
from .providers import ProviderError, Shopee, Telegram
from .store import AutomationConflict, DEAL_TEMPLATE_ID, ManualSendConflict, Store, destination_identity
from .whatsapp_transport import OrionWhatsAppTransport


HERE = Path(__file__).resolve().parent
BRAND = HERE.parent / "app" / "static"
COOKIE = "orion_affiliates"
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$|^default$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    username: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=1, max_length=180)


class ShopeeKeys(Input):
    app_id: str = Field(pattern=r"^[0-9]{1,20}$")
    key: SecretStr = Field(min_length=10, max_length=300)


class TelegramKeys(Input):
    token: SecretStr = Field(min_length=30, max_length=160)
    dedicated: Literal[True]

class GroqKeys(Input):
    key: SecretStr = Field(min_length=32, max_length=130)


class Destination(Input):
    name: Name
    mode: Literal["telegram", "whatsapp_group", "whatsapp_channel"]
    address: str = Field(max_length=180)
    test: bool = False


class OfferInput(Input):
    url: str = Field(max_length=8192)
    note: str = Field(default="", max_length=300)
    allow_manual: bool = False


class OfferEdit(Input):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)] | None = None
    price: Decimal | None = Field(default=None, gt=0, le=100000000, max_digits=12, decimal_places=2)
    image: str | None = Field(default=None, max_length=2048)
    affiliate_url: str | None = Field(default=None, max_length=2048)


class ManualSend(Input):
    template: Identifier = "default"
    destinations: list[Identifier] = Field(min_length=1, max_length=100)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")


class ProductFlag(Input):
    favorite: bool = False
    blocked: bool = False


class Template(Input):
    name: Name
    brand: str = Field(default="", max_length=32)
    layout: Literal["simple", "discount", "coupon"] = "simple"
    media: Literal["card", "text"] = "card"
    text: str = Field(min_length=1, max_length=1900)
    style: Literal["plain", "whatsapp"] = "plain"
    image_style: ImageStyle = Field(default_factory=ImageStyle)


class PhraseEdit(Input):
    text: str = Field(max_length=155)


class Plan(Input):
    name: Name
    keyword: str = Field(default="", max_length=80)
    niches: list[Annotated[str, Field(max_length=30)]] = Field(default_factory=list, max_length=len(NICHES))
    sort: int = Field(default=2, ge=1, le=5)
    min_sales: int | None = Field(default=None, ge=0, le=1000000)
    min_discount: int | None = Field(default=None, ge=0, le=99)
    blocked: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]] = Field(
        default_factory=list, max_length=20,
    )
    template_id: Identifier = "default"
    destinations: list[Identifier] = Field(min_length=1, max_length=5)
    days: list[Annotated[int, Field(ge=0, le=6)]] = Field(min_length=1, max_length=7)
    opens: str = Field(pattern=r"^\d{2}:\d{2}$")
    closes: str = Field(pattern=r"^\d{2}:\d{2}$")
    timezone: str = Field(default="America/Campo_Grande", max_length=70)
    interval: int = Field(ge=10, le=1440)
    daily: int = Field(ge=1, le=48)
    repeat_hours: int = Field(default=48, ge=1, le=720)
    min_price: float | None = Field(default=None, ge=0, le=100000)
    max_price: float | None = Field(default=None, ge=0, le=100000)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_commission: float | None = Field(default=None, ge=0, le=100)
    mode: Literal["review", "automatic"] = "review"


class Toggle(Input):
    active: bool


class QueueInput(Input):
    destination: Identifier
    template: Identifier = "default"
    due: int | None = None


class JobAction(Input):
    action: Literal["approve", "cancel"]
    confirm_test: bool = False


def create_app(store=None, *, sending=None, origin=None, start_worker=True):
    origin = origin or os.getenv("AFFILIATE_PUBLIC_ORIGIN", "http://127.0.0.1:8067")
    wa_token = os.getenv("AFFILIATE_WA_TOKEN")
    wa_port = os.getenv("AFFILIATE_WA_PORT")
    wa_owner = os.getenv("AFFILIATE_BOOTSTRAP_USER", "piloto")
    bridge_key = os.getenv("ORION_AFFILIATE_WORKSPACE_KEY")
    bridge = Fernet(bridge_key.encode("ascii")) if bridge_key else None
    if sending is None:
        sending = os.getenv("AFFILIATE_ENABLE_TEST_SEND") == "1"

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store if store is not None else Store(
            Path(os.environ.get("AFFILIATE_DATABASE", "/data/affiliates.sqlite3")),
            os.environ["AFFILIATE_MASTER_KEY"],
        )
        app.state.engine = Engine(app.state.store, sending, PromoTech.configured(),
                                  manual_transport=OrionWhatsAppTransport(app.state.store))
        app.state.analytics = Analytics(app.state.store)
        if token := os.getenv("AFFILIATE_BOOTSTRAP_PASSWORD"):
            app.state.store.bootstrap(os.getenv("AFFILIATE_BOOTSTRAP_USER", "piloto"), token,
                                      os.getenv("AFFILIATE_COMPANY_NAME", "GeekHunter"))
        app.state.store.ensure_deal_templates()
        app.state.running_task = asyncio.create_task(app.state.engine.run()) if start_worker else None
        try:
            yield
        finally:
            await app.state.analytics.close()
            if app.state.running_task:
                app.state.running_task.cancel()
                await asyncio.gather(app.state.running_task, return_exceptions=True)

    app = FastAPI(title="Orion Afiliados", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    render_slots = asyncio.Semaphore(2)
    app.mount("/assets", StaticFiles(directory=HERE / "static", check_dir=False), name="affiliate-assets")

    @app.middleware("http")
    async def security(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("origin") != origin:
            return JSONResponse({"detail": "A solicitação não veio deste painel."}, status_code=403)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
                                       "style-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
        })
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"detail": "Confira os campos preenchidos. Há um valor fora do formato permitido."}, 422)

    @app.exception_handler(ProviderError)
    async def provider_error(request, exc):
        return JSONResponse({"detail": exc.message, "code": exc.code}, 429 if exc.code == "rate" else 422)

    @app.exception_handler(AutomationConflict)
    async def automation_conflict(request, exc):
        return JSONResponse({"detail": str(exc), "code": "AUTOMATION_CHANGED"}, status_code=409)

    def current(request: Request):
        if signed := request.headers.get("X-Orion-Affiliate-Access"):
            if bridge is None or len(signed) > 2048:
                raise HTTPException(401, "Acesso do Orion indisponível")
            try:
                claim = json.loads(bridge.decrypt(signed.encode("ascii"), ttl=30))
                tenant, name = claim["tenant"], claim["name"]
                if (not re.fullmatch("[a-f0-9]{32}", tenant) or not isinstance(name, str)
                        or not 1 <= len(name) <= 160 or claim["method"] != request.method
                        or claim["path"] != request.url.path):
                    raise ValueError
            except (InvalidToken, ValueError, KeyError, TypeError):
                raise HTTPException(401, "Acesso do Orion inválido ou expirado") from None
            return app.state.store.orion_workspace(tenant, name)
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 90 or not (user := app.state.store.user(token)):
            raise HTTPException(401, "Entre com o acesso do piloto.")
        return user

    User = Depends(current)

    async def whatsapp(user, path, method="GET", **params):
        if user["username"].startswith("orion-") and method == "GET" and path in {"status", "groups", "channel"}:
            return await app.state.engine.manual_transport.control(user["id"], path, **params)
        if not wa_token or not wa_port or user["username"] != wa_owner:
            raise HTTPException(503, "O conector WhatsApp dedicado está indisponível para esta conta.")
        try:
            async with httpx.AsyncClient(timeout=27 if path == "channel" else 18, trust_env=False) as client:
                response = await client.request(method, f"http://127.0.0.1:{int(wa_port)}/{path}",
                                                headers={"Authorization": f"Bearer {wa_token}"}, params=params)
            data = response.json()
            if response.is_success:
                return data
            if data.get("code") == "admin":
                raise HTTPException(422, "A conta conectada não é administradora deste grupo.")
            if data.get("code") == "channel_admin":
                raise HTTPException(422, "O WhatsApp não confirmou a conta conectada como administradora do canal.")
            if data.get("code") == "channel_not_found":
                raise HTTPException(422, "O WhatsApp não retornou um canal ativo para este link.")
        except (httpx.HTTPError, ValueError):
            pass
        raise HTTPException(503, "Não foi possível consultar o WhatsApp. Confira se a conta está vinculada.")

    def required(company, kind, ident):
        value = app.state.store.get(company, kind, ident)
        if value is None:
            raise HTTPException(404, "Registro não encontrado nesta conta.")
        return value

    def preview_token(company, offer, template):
        value = json.dumps([company, offer, template], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(value.encode()).hexdigest()

    def batch_response(batch, created=False):
        return {"batch_id": batch["batch_id"], "created": created, "jobs": batch["jobs"]}

    def rule_response(company, rule):
        try:
            day_start = local_day_start(int(time.time()), rule.get("timezone") or "America/Campo_Grande")
        except (ValueError, ZoneInfoNotFoundError):
            day_start = local_day_start(int(time.time()), "America/Campo_Grande")
        result = {**rule, "runtime": app.state.engine.automation_rule_runtime(company, rule),
                  "activity": app.state.store.automation_activity(company, rule, day_start)}
        result.pop("automation_authorization", None)
        return result

    @app.get("/")
    def index():
        return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/design/{name}")
    def design(name: str):
        if name not in {"orion-suite.css", "orion-auth.css", "orion-subscription-dashboard.css"}:
            raise HTTPException(404)
        return FileResponse(BRAND / name, media_type="text/css")

    @app.get("/brand/{name}")
    def brand(name: str):
        if name not in {"orion-brand-light.svg", "orion-brand-dark.svg", "figtree-latin.woff2", "outfit-latin.woff2"}:
            raise HTTPException(404)
        return FileResponse(BRAND / name)

    @app.get("/health")
    def health():
        engine = app.state.engine
        return {"status": "ok", "worker": engine.last_tick > int(time.time()) - 300,
                "external_sends_enabled": sending, "promotech_configured": bool(engine.promotech),
                "database_schema": 1, "orion_workspace_configured": bridge is not None}

    @app.post("/api/login")
    def login(payload: Login, request: Request, response: Response):
        if not app.state.store.login_allowed(f"{request.client.host}:{payload.username.casefold()}"):
            raise HTTPException(429, "Muitas tentativas. Aguarde para entrar.")
        token = app.state.store.authenticate(payload.username.strip().lower(), payload.password.get_secret_value())
        if not token:
            raise HTTPException(401, "Usuário ou senha inválidos.")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=origin.startswith("https:"), max_age=28800)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, user=User):
        app.state.store.logout(request.cookies[COOKIE])
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/state")
    async def state(user=User):
        from .marketplaces import retailer_options
        company = user["id"]
        result = {"company": user["name"], "sending": sending, "jobs": app.state.store.history(company),
                  "niches": [{"id": ident, **data} for ident, data in PUBLIC_NICHES.items()],
                  "sorts": SORTS, "phrase_ai": configured() or bool(app.state.store.credentials(company, "groq")[0]),
                  "deal_template": DEAL_TEMPLATE_ID,
                  "retailers": retailer_options(),
                  "promotech_automation": bool(app.state.engine.promotech and
                       app.state.engine.promotech.binding["company"] == company),
                  "whatsapp_link_available": bool(wa_token and wa_port and user["username"] == wa_owner)}
        result["image_templates_v1"] = True
        preferences=app.state.store.get(company,"preferences","templates") or {}
        preferred=preferences.get("default")
        if isinstance(preferred,str) and app.state.store.get(company,"template",preferred):
            result["deal_template"]=preferred
        # Logos are only needed in the template editor and actual renderer,
        # not in the history response (which can contain 120 job snapshots).
        for job in result["jobs"]:
            saved = (job.get("doc") or {}).get("template")
            if isinstance(saved, dict) and isinstance(saved.get("image_style"), dict):
                saved["image_style"] = {k:v for k,v in saved["image_style"].items() if k!="logo"}
        for kind in ("connection", "destination", "template", "offer", "rule", "decision", "product_flag"):
            result[kind] = app.state.store.list(company, kind, limit=40)
        if not any(t["id"]==result["deal_template"] for t in result["template"]):
            preferred=app.state.store.get(company,"template",result["deal_template"])
            if preferred:
                result["template"]=result["template"][:39]+[preferred]
        telegram = next((connection for connection in result["connection"] if connection["id"] == "telegram"), {})
        wa_version = None
        wa_connected = None
        if any(d["verified"] and d["mode"].startswith("whatsapp_") for d in result["destination"]):
            try:
                wa_status = await whatsapp(user, "status")
                wa_version = wa_status.get("version")
                if str(wa_version or "").startswith("orion:"):
                    wa_connected = wa_status.get("available") is True
            except (HTTPException, ProviderError):
                pass
        for destination in result["destination"]:
            if destination["mode"] == "telegram" and destination["connection_version"] != telegram.get("version"):
                destination.update(verified=False, issue="Valide o destino com a conta Telegram atual.")
            if (destination["verified"] and destination["mode"].startswith("whatsapp_")
                    and destination.get("connection_version") != wa_version):
                destination.update(verified=False, issue="Confirme a administração com o WhatsApp vinculado.")
        result["automation_destinations"] = []
        for destination in result["destination"]:
            eligible, reason = app.state.engine.automation_destination_capability(company, destination, None)
            if destination["mode"].startswith("whatsapp_") and wa_connected is False:
                eligible, reason = False, "Reconecte seu WhatsApp para retomar os envios deste destino."
            result["automation_destinations"].append({"id": destination["id"], "name": destination.get("name") or "Destino",
                "mode": destination["mode"], "eligible": eligible, "reason": reason})
        result["rule"] = [rule_response(company, rule) for rule in result["rule"]]
        capabilities = {entry["id"]: entry for entry in result["automation_destinations"]}
        for rule in result["rule"]:
            if not app.state.store.orion_tenant(company):
                continue
            blocked = next((capabilities.get(ident) for ident in rule.get("destinations", [])
                            if not capabilities.get(ident, {}).get("eligible")), None)
            if blocked:
                rule["runtime"].update(status="blocked" if rule.get("active") else "paused",
                                       can_activate=False, reason=blocked["reason"])
        return result

    @app.get("/api/analytics")
    async def analytics(period: Literal["today", "7d", "30d", "month"] = "month",
                        timezone: str = "America/Campo_Grande", user=User):
        try:
            if len(timezone) > 64:
                raise ValueError
            ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise HTTPException(422, "Selecione um fuso horário válido para o relatório.") from None
        return await app.state.analytics.report(user["id"], period=period, timezone=timezone)

    @app.get("/api/whatsapp/status")
    @app.get("/api/connection/whatsapp")
    async def whatsapp_status(user=User):
        data = await whatsapp(user, "status")
        return {name: data[name] for name in ("status", "account", "image", "expires") if name in data}

    @app.post("/api/connection/whatsapp")
    async def whatsapp_start(payload: Toggle, user=User):
        if not payload.active:
            raise HTTPException(422, "Confirme o vínculo de uma conta dedicada.")
        data = await whatsapp(user, "link", "POST")
        app.state.store.audit(user["id"], "whatsapp:link_requested")
        return {"status": data["status"]}

    @app.delete("/api/connection/whatsapp")
    async def whatsapp_disconnect(user=User):
        result = await whatsapp(user, "link", "DELETE")
        for row in app.state.store.list(user["id"], "destination"):
            if row["mode"].startswith("whatsapp_") and row["verified"]:
                app.state.store.update_destination(user["id"], row,
                    {"verified": False, "issue": "Conta WhatsApp desvinculada."}, "whatsapp:destination_unlinked")
        app.state.store.audit(user["id"], "whatsapp:unlink")
        return result

    @app.get("/api/whatsapp/groups")
    @app.get("/api/connection/whatsapp/groups")
    async def whatsapp_groups(user=User):
        data = await whatsapp(user, "groups")
        return {"groups": data["groups"], "truncated": bool(data.get("truncated", False))}

    @app.put("/api/platforms/shopee")
    async def shopee_save(payload: ShopeeKeys, user=User):
        key = payload.key.get_secret_value()
        client = Shopee(payload.app_id, key)
        offers = await client.search(None, 4)
        if not offers:
            raise ProviderError("access", "A Shopee respondeu, mas não liberou ofertas para validar esta conta.")
        await client.affiliate_link(offers[0], "orionverify")
        app.state.store.save_connection(user["id"], "shopee", {"app_id": payload.app_id, "key": key}, {
            "status": "connected", "account": "••••" + payload.app_id[-4:], "verified": int(time.time()),
            "capabilities": ["catálogo", "link de afiliado"],
        })
        return {"status": "connected"}

    @app.delete("/api/connection/{name}")
    def disconnect(name: Literal["shopee", "telegram"], user=User):
        app.state.store.revoke(user["id"], name)
        return {"ok": True}

    @app.put("/api/platforms/groq")
    async def groq_save(payload: GroqKeys, user=User):
        key = payload.key.get_secret_value().strip()
        if not re.fullmatch(r"gsk_[A-Za-z0-9]{28,125}", key):
            raise HTTPException(422, "Confira o formato da chave da Groq.")
        _, result = await generate_phrase({"title": "Garrafa para água"}, key)
        if result != "groq":
            raise HTTPException(422, "A Groq não confirmou a geração de texto com esta chave.")
        app.state.store.save_connection(user["id"], "groq", {"key": key},
            {"status": "connected", "verified": int(time.time())})
        return {"status": "connected"}

    @app.delete("/api/platforms/groq")
    def groq_disconnect(user=User):
        app.state.store.revoke(user["id"], "groq")
        return {"ok": True}

    @app.put("/api/connection/telegram")
    async def telegram_save(payload: TelegramKeys, user=User):
        token = payload.token.get_secret_value().strip()
        if not re.fullmatch(r"\d{5,17}:[A-Za-z0-9_-]{25,120}", token):
            raise HTTPException(422, "Token de bot Telegram inválido.")
        client = Telegram(token)
        info = await client.call("getMe")
        if not info.get("is_bot") or (await client.call("getWebhookInfo")).get("url"):
            raise HTTPException(422, "Use um bot dedicado ao piloto e sem webhook vinculado.")
        code = "orion_" + secrets.token_urlsafe(20)
        app.state.store.save_connection(user["id"], "telegram", {
            "token": token, "bot_id": info["id"], "code": code, "expires": int(time.time()) + 900,
        }, {"status": "pairing", "username": info["username"],
            "pair_url": f"https://t.me/{info['username']}?start={code}"})
        return {"status": "pairing"}

    @app.post("/api/connection/telegram/verify")
    async def telegram_verify(user=User):
        client, credentials, version = app.state.engine.telegram(user["id"])
        if credentials.get("operator"):
            return {"status": "connected"}
        if credentials["expires"] < time.time():
            raise HTTPException(422, "O vínculo expirou. Reconecte o bot para gerar um novo link.")
        if (await client.call("getWebhookInfo")).get("url"):
            raise HTTPException(422, "O bot já está vinculado a outro serviço.")
        updates = await client.call("getUpdates", limit=100, timeout=0, allowed_updates=["message"])
        match = next((update["message"] for update in updates if update.get("message", {}).get("text") ==
                      f"/start {credentials['code']}"
                      and update["message"].get("chat", {}).get("type") == "private"
                      and update["message"].get("date", 0) >= credentials["expires"] - 900), None)
        if not match:
            raise HTTPException(422, "Abra o link do bot e pressione Iniciar. Depois confirme aqui.")
        if app.state.store.credentials(user["id"], "telegram")[1] != version:
            raise HTTPException(409, "A conta foi alterada. Faça o vínculo novamente.")
        info = await client.call("getMe")
        app.state.store.save_connection(user["id"], "telegram", {
            "token": credentials["token"], "bot_id": credentials["bot_id"], "operator": match["from"]["id"],
        }, {"status": "connected", "username": info["username"], "verified": int(time.time())})
        return {"status": "connected"}

    @app.put("/api/destinations/{ident}")
    async def destination_save(ident: Identifier, payload: Destination, user=User):
        company = user["id"]
        address = payload.address.strip()
        if payload.mode == "telegram":
            verifier, credentials, version = app.state.engine.telegram(company)
            if not credentials.get("operator"):
                raise HTTPException(422, "Confirme sua conta no Telegram antes de cadastrar o destino.")
            if not re.fullmatch(r"-\d{4,19}|@[A-Za-z0-9_]{5,32}", address):
                raise HTTPException(422, "Use o identificador numérico do grupo ou canal, ou seu @usuário público.")
            checked = await verifier.verify_destination(
                int(address) if address.startswith("-") else address, credentials["bot_id"], credentials["operator"])
            doc = {"name": payload.name, "mode": payload.mode, "test": payload.test, **checked,
                   "verified": True, "connection_version": version}
            for old in app.state.store.list(company, "destination"):
                if old.get("chat_id") == doc["chat_id"] and old["id"] != ident:
                    raise HTTPException(409, "Esse destino já está cadastrado.")
        elif payload.mode == "whatsapp_group" and address and (payload.test or user["username"].startswith("orion-")):
            if not re.fullmatch(r"[1-9]\d{5,21}(?:-\d{5,18})?@g\.us", address):
                raise HTTPException(422, "Escolha um grupo retornado pela conta conectada.")
            checked = await whatsapp(user, "groups", address=address)
            entry = checked["groups"][0]
            if any(row.get("address") == address and row["id"] != ident
                   for row in app.state.store.list(company, "destination")):
                raise HTTPException(409, "Esse grupo já está cadastrado.")
            doc = {"name": payload.name, "mode": payload.mode, "address": entry["address"],
                   "title": entry["title"], "test": False, "verified": True, "checked_at": int(time.time()),
                   "connection_version": checked["version"], "issue": "Administração verificada."}
        else:
            doc = {"name": payload.name, "mode": payload.mode, "address": address,
                   "verified": False, "test": False, "issue": "Publicação assistida. Administração não verificada."}
        row = app.state.store.put(company, "destination", ident, doc)
        app.state.store.audit(company, "destination:save")
        return row

    @app.post("/api/destinations/{ident}/verify-channel")
    async def verify_whatsapp_channel(ident: Identifier, user=User):
        company = user["id"]
        destination = required(company, "destination", ident)
        if destination["mode"] != "whatsapp_channel":
            raise HTTPException(422, "Selecione um canal do WhatsApp.")
        address = destination.get("address", "")
        match = re.fullmatch(r"https://(?:www\.)?whatsapp\.com/channel/([A-Za-z0-9]{20,32})/?(?:\?[^\s#]*)?", address)
        if not match:
            raise HTTPException(422, "Cadastre o link completo do canal, no formato https://whatsapp.com/channel/…")
        try:
            proof = await whatsapp(user, "channel", invite=match[1])
        except ProviderError as exc:
            if exc.code == "permissions" and destination["verified"]:
                app.state.store.update_destination(company, destination,
                    {"verified": False, "issue": exc.message}, "whatsapp:channel_permission_lost")
            raise
        except HTTPException as exc:
            if exc.status_code == 422 and destination["verified"]:
                app.state.store.update_destination(company, destination,
                    {"verified": False, "issue": exc.detail}, "whatsapp:channel_permission_lost")
            raise
        update = {"title": proof["title"], "channel_jid": proof["address"], "role": proof["role"],
                  "verified": True, "checked_at": int(time.time()), "connection_version": proof["version"],
                  "issue": "Administração verificada."}
        saved = app.state.store.update_destination(company, destination, update, "whatsapp:channel_verified")
        if not saved:
            raise HTTPException(409, "O destino mudou enquanto era verificado. Tente novamente.")
        return saved

    @app.delete("/api/destinations/{ident}")
    def destination_delete(ident: Identifier, user=User):
        app.state.store.delete(user["id"], "destination", ident)
        app.state.store.audit(user["id"], "destination:delete")
        return {"ok": True}

    @app.get("/api/catalog")
    async def search(keyword: str = "", niche: str = "", sort: int = 2, user=User):
        connector, _ = app.state.engine.shopee(user["id"])
        if len(keyword) > 80 or (niche and niche not in NICHES) or (not keyword.strip() and not niche):
            raise HTTPException(422, "Informe uma palavra-chave ou selecione um nicho.")
        rows = await search_niche(connector, niche, keyword.strip(), sort) if niche else await connector.search(keyword.strip(), sort=sort)
        return app.state.store.record_prices(user["id"], rows)

    @app.post("/api/offers")
    async def prepare(payload: OfferInput, user=User):
        from .link_input import ProductLinkChoice, normalize_product_input
        from .marketplaces import extract_offer
        try:
            source_url = normalize_product_input(payload.url)
        except ProductLinkChoice as exc:
            raise HTTPException(422, detail={"code": "PRODUCT_LINK_AMBIGUOUS", "message": exc.message,
                                             "candidates": exc.candidates}) from None
        company = user["id"]
        credentials, _ = app.state.store.credentials(company, "shopee")
        provider = app.state.engine.shopee(company)[0] if credentials else None
        offer = await extract_offer(source_url, shopee=provider)
        offer = {**offer, "source_url": source_url}
        if not any(offer.get(field) for field in ("title", "price", "image")):
            extraction = dict(offer.get("extraction") or {})
            if not payload.allow_manual:
                raise HTTPException(422, detail={
                    "code": "PRODUCT_EXTRACTION_EMPTY",
                    "message": extraction.get("reason") or "Não foi possível ler os dados deste produto. Tente novamente.",
                    "extraction": extraction,
                    "source_url": source_url,
                    "manual_available": True,
                })
            offer["extraction"] = {**extraction, "manual_requested": True}
        if offer.get("platform") == "shopee" and provider:
            try:
                offer = await app.state.engine.ready_offer(company, offer)
            except ProviderError as exc:
                offer = {**offer, "affiliate_url": None, "affiliate_status": "required", "link_status": "required", "affiliate_error": exc.message}
        else:
            offer = {**offer, "affiliate_status": "required", "link_status": "required"}
        phrase, source = initial_phrase(offer)
        offer = {**offer, "note": payload.note.strip(), "phrase": offer.get("phrase") or phrase,
                 "phrase_source": offer.get("phrase_source") or source}
        return app.state.store.put(company, "offer", uuid4().hex, offer)

    @app.put("/api/offers/{ident}")
    def edit_offer(ident: str, payload: OfferEdit, user=User):
        from .marketplaces import checked_affiliate_url, checked_image_url
        company = user["id"]
        row = required(company, "offer", ident)
        if row.get("demo"):
            raise HTTPException(422, "Prepare um produto real para editar e publicar.")
        platform = row.get("platform") or "shopee"
        changes = {}
        source_values = dict(row.get("source_values") or {})
        for field in payload.model_fields_set:
            value = getattr(payload, field)
            source_values.setdefault(field, row.get(field))
            if field == "title":
                if value is None:
                    raise HTTPException(422, "Informe o nome do produto.")
                changes[field] = value
            elif field == "price":
                changes.update(price=format(value, ".2f") if value is not None else None,
                               price_max=format(value, ".2f") if value is not None else None,
                               previous_price=None, discount_rate=None, currency="BRL", currency_source="user")
            elif field == "image":
                image = checked_image_url(value.strip(), platform) if value and value.strip() else None
                changes.update(image=image, images=[image] if image else [])
            elif field == "affiliate_url":
                link = checked_affiliate_url(value.strip(), platform) if value and value.strip() else None
                changes.update(affiliate_url=link, affiliate_status="provided" if link else "required",
                               link_status="provided" if link else "required",
                               link_source="Informado pelo usuário" if link else None, link_version=None,
                               tracking_status="unavailable", tracking_token=None, utm_content=None)
        manual_fields = sorted(set(row.get("manual_fields", [])) | set(payload.model_fields_set))
        row = {**row, **changes, "manual_fields": manual_fields, "source_values": source_values}
        extraction = dict(row.get("extraction") or {})
        missing = [field for field in ("title", "price", "image") if not row.get(field)]
        extraction.update(status="partial" if missing else "complete", missing=missing,
                          manually_edited=manual_fields)
        row["extraction"] = extraction
        return app.state.store.put(company, "offer", ident, row)

    @app.post("/api/offers/demo")
    def example(user=User):
        return app.state.store.put(user["id"], "offer", "demo", DEMO)

    @app.post("/api/offers/{ident}/phrase")
    async def generate_offer_phrase(ident: str, user=User):
        company = user["id"]
        row = required(company, "offer", ident)
        text, provider = await app.state.engine.phrase(company, row)
        return app.state.store.put(company, "offer", ident, {**row, "phrase": text, "phrase_source": provider})

    @app.put("/api/offers/{ident}/phrase")
    def edit_offer_phrase(ident: str, payload: PhraseEdit, user=User):
        company = user["id"]
        row = required(company, "offer", ident)
        text = " ".join(payload.text.split())
        return app.state.store.put(company, "offer", ident, {**row, "phrase": text, "phrase_source": "manual"})

    @app.post("/api/offers/{ident}/phrases")
    async def suggest_offer_phrases(ident: str, user=User):
        company = user["id"]
        row = required(company, "offer", ident)
        generation = int(row.get("phrase_generation") or 0) + 1
        suggestions = await app.state.engine.suggestions(company, row, generation)
        current_row = required(company, "offer", ident)
        saved = app.state.store.put(company, "offer", ident,
                                    {**current_row, "phrase_suggestions": suggestions, "phrase_generation": generation})
        return {"suggestions": suggestions, "phrase": saved.get("phrase"), "phrase_source": saved.get("phrase_source")}

    @app.put("/api/product-flag/{key}")
    def set_flag(key: str, payload: ProductFlag, user=User):
        if not re.fullmatch(r"shopee:\d{1,18}:\d{1,18}", key):
            raise HTTPException(422, "Identificador de oferta desconhecido.")
        return app.state.store.put(user["id"], "product_flag", key, payload.model_dump())

    def template_document(payload, user):
        doc = payload.model_dump()
        doc["brand"] = (" ".join(doc["brand"].split()) or " ".join(user["name"].split()) or "Orion Afiliados")[:32]
        return doc

    @app.post("/api/templates/preview-image")
    async def template_preview_image(payload: Template, user=User):
        # Never create offers, jobs or publications merely to preview an image.
        # The same renderer is used by the manual and automated transports.
        doc = template_document(payload, user)
        sample = {**DEMO, "title":"Produto de exemplo • Prévia do template", "price":"79.90",
                  "price_max":"79.90","previous_price":"99.90","retailer_name":"Loja de exemplo",
                  "coupon":{"code":"EXEMPLO"}}
        async with render_slots:
            image = await asyncio.to_thread(card_png, sample, doc)
        return {"image":"data:image/png;base64," + base64.b64encode(image).decode("ascii")}

    @app.put("/api/templates/{ident}")
    def save_template(ident: Identifier, payload: Template, user=User):
        validate_template(payload.text)
        doc=template_document(payload,user)
        if "image_style" not in payload.model_fields_set:
            previous=app.state.store.get(user["id"],"template",ident)
            if previous and "image_style" in previous:
                doc["image_style"]=previous["image_style"]
        return app.state.store.put(user["id"], "template", ident, doc)

    @app.post("/api/templates/{ident}/default")
    def select_default_template(ident: Identifier, user=User):
        required(user["id"],"template",ident)
        app.state.store.put(user["id"],"preferences","templates",{"default":ident})
        return {"ok":True,"default":ident}

    @app.get("/api/offers/{ident}/preview")
    def preview(ident: str, template: Identifier = "default", destination: Identifier | None = None, user=User):
        company = user["id"]
        row = required(company, "offer", ident)
        selected = required(company, "template", template)
        dest = required(company, "destination", destination) if destination else None
        text = caption(row, selected, dest["name"] if dest else "")
        issue = app.state.engine.manual_offer_issue(company, row, selected)
        destinations = app.state.engine.manual_destinations(company)
        if issue is None and not any(destination["eligible"] for destination in destinations):
            issue = "Conecte e verifique pelo menos um destino para enviar."
        suggestions = phrase_suggestions(row, row.get("phrase_generation") or 0)
        return {"text": text, "offer": row, "media": selected["media"],
                "html": formatted_html(text) if selected.get("style") == "whatsapp" else None,
                "template": {key: selected[key] for key in ("id", "name", "media", "layout")},
                "card_url": f"/api/offers/{ident}/card.png?template={template}" if selected["media"] == "card" else None,
                "destinations": destinations, "suggestions": suggestions,
                "send": {"enabled": issue is None, "reason": issue,
                         "preview_token": preview_token(company, row, selected)}}

    @app.get("/api/offers/{ident}/card.png")
    async def preview_image(ident: str, template: Identifier = "default", user=User):
        row = required(user["id"], "offer", ident)
        selected = required(user["id"], "template", template)
        async with render_slots:
            image = await render_card(row, selected)
        return Response(image, media_type="image/png")

    @app.post("/api/offers/{ident}/send")
    def manual_send(ident: str, payload: ManualSend, user=User):
        company = user["id"]
        if len(set(payload.destinations)) != len(payload.destinations):
            raise HTTPException(422, "Cada destino deve aparecer apenas uma vez na seleção.")
        fingerprint = hashlib.sha256(json.dumps([ident, payload.template, sorted(payload.destinations),
                                                 payload.preview_token], separators=(",", ":")).encode()).hexdigest()
        previous = app.state.store.manual_batch(company, payload.request_id)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise HTTPException(409, "Esta solicitação já foi usada para outra seleção. Atualize a prévia.")
            return batch_response(previous)
        row = required(company, "offer", ident)
        selected = required(company, "template", payload.template)
        if not secrets.compare_digest(payload.preview_token, preview_token(company, row, selected)):
            raise HTTPException(409, "A oferta ou o template mudou. Atualize a prévia antes de enviar.")
        if issue := app.state.engine.manual_offer_issue(company, row, selected):
            raise HTTPException(422, issue)
        destinations = [required(company, "destination", item) for item in payload.destinations]
        for destination in destinations:
            eligible, reason = app.state.engine.manual_destination_capability(company, destination)
            if not eligible:
                raise HTTPException(422, reason)
            validate_length(caption(row, selected, destination["name"]), selected["media"])
        try:
            batch, created = app.state.store.enqueue_manual(company, payload.request_id, fingerprint,
                                                           row, selected, destinations)
        except ManualSendConflict as exc:
            raise HTTPException(409, str(exc)) from None
        return batch_response(batch, created)

    @app.get("/api/send-batches/{request_id}")
    def manual_send_status(request_id: Identifier, user=User):
        batch = app.state.store.manual_batch(user["id"], request_id)
        if batch is None:
            raise HTTPException(404, "Envio não encontrado nesta conta.")
        return batch_response(batch)

    @app.post("/api/offers/{ident}/queue")
    def schedule(ident: str, payload: QueueInput, user=User):
        company = user["id"]
        row = required(company, "offer", ident)
        template = required(company, "template", payload.template)
        destination = required(company, "destination", payload.destination)
        if row.get("demo") or not row.get("affiliate_url") or not row.get("price"):
            raise HTTPException(422, "A fila precisa de oferta real com preço e link Shopee confirmados.")
        if not destination["test"] or not destination["verified"]:
            raise HTTPException(422, "Escolha um destino Telegram de teste autorizado.")
        now = int(time.time())
        due = payload.due if payload.due is not None else now
        if not now - 10 <= due <= now + 7 * 86400:
            raise HTTPException(422, "O horário precisa estar dentro dos próximos sete dias.")
        job, created = app.state.store.enqueue(company, destination["id"], row, template, due,
                                               approval=True, repeat_hours=24)
        return {"id": job, "created": created}

    @app.post("/api/jobs/{ident}")
    def job_action(ident: Identifier, payload: JobAction, user=User):
        company = user["id"]
        rows = app.state.store.history(company, ident)
        if not rows:
            raise HTTPException(404, "Publicação não encontrada.")
        job = rows[0]
        if payload.action == "approve":
            if job["doc"].get("automation"):
                if not app.state.store.approve_automation(company, ident):
                    raise HTTPException(409, "A publicação expirou ou a automação mudou. Confira a regra e prepare uma nova ocorrência.")
                return {"ok": True}
            if not sending or not payload.confirm_test:
                raise HTTPException(409, "Envio real bloqueado neste piloto. A aprovação está disponível após a liberação do teste.")
            if job["expires"] <= time.time():
                raise HTTPException(409, "A oferta expirou. Prepare uma nova publicação.")
            ok = app.state.store.change(company, ident, "approval", "queued")
        else:
            ok = any(app.state.store.change(company, ident, old, "cancelled") for old in
                     ("approval", "queued", "expired", "paused", "failed"))
        if not ok:
            raise HTTPException(409, "O estado atual da publicação não permite essa ação.")
        app.state.store.audit(company, f"job:{payload.action}")
        return {"ok": True}

    @app.put("/api/rules/{ident}")
    def save_rule(ident: Identifier, payload: Plan, user=User):
        company = user["id"]
        if (not payload.keyword.strip() and not payload.niches) or set(payload.niches) - NICHES.keys():
            raise HTTPException(422, "Escolha um ou mais nichos da Shopee ou preencha uma palavra-chave.")
        required(company, "template", payload.template_id)
        if len(set(payload.destinations)) != len(payload.destinations):
            raise HTTPException(422, "Cada destino deve aparecer apenas uma vez na automação.")
        orion = app.state.store.orion_tenant(company)
        for dest in payload.destinations:
            item = required(company, "destination", dest)
            if orion:
                eligible, reason = app.state.engine.automation_destination_capability(company, item,
                    {"id": ident, **payload.model_dump()})
                if not eligible:
                    raise HTTPException(422, f"{item.get('name') or 'Destino'}: {reason}")
            elif not item["verified"] or not (item["mode"] == "whatsapp_channel" or
                                              item["mode"] == "telegram" and item["test"]):
                raise HTTPException(422, "Escolha um canal WhatsApp verificado ou um destino Telegram de teste.")
        try:
            ZoneInfo(payload.timezone)
            if payload.opens == payload.closes == "00:00":
                pass
            elif wall_time.fromisoformat(payload.opens) >= wall_time.fromisoformat(payload.closes):
                raise ValueError
            if payload.min_price is not None and payload.max_price is not None and payload.min_price > payload.max_price:
                raise ValueError
        except (ValueError, ZoneInfoNotFoundError):
            raise HTTPException(422, "Confira o horário inicial, final, fuso e faixa de preço.") from None
        doc = {**payload.model_dump(), "keyword": payload.keyword.strip(), "niches": list(dict.fromkeys(payload.niches)),
               "days": list(dict.fromkeys(payload.days)), "revision": uuid4().hex,
               "active": False, "next_at": None, "niche_cursor": 0}
        binding = app.state.engine.promotech
        if binding and company == binding.binding["company"] and ident == binding.binding["rule"] and payload.mode == "automatic":
            if payload.interval < 30 or payload.destinations != [binding.binding["destination"]]:
                raise HTTPException(422, "A automação do PromoTech precisa de intervalo de pelo menos 30 minutos e de um único canal.")
        previous = app.state.store.get(company, "rule", ident)
        if previous and previous.get("niches") == doc["niches"]:
            doc["niche_cursor"] = previous.get("niche_cursor", 0)
        saved = app.state.store.save_rule(company, ident, doc, expected=previous)
        return rule_response(company, saved)

    @app.post("/api/rules/{ident}/activate")
    async def activate_rule(ident: Identifier, payload: Toggle, user=User):
        company = user["id"]
        rule = required(company, "rule", ident)
        orion = app.state.store.orion_tenant(company)
        if payload.active == rule.get("active"):
            if not payload.active or not orion:
                return rule_response(company, rule)
            runtime = app.state.engine.automation_rule_runtime(company, rule)
            if runtime.get("status") in {"ready", "review"}:
                return rule_response(company, rule)
        previous = dict(rule)
        rule = {**rule, "revision": uuid4().hex}
        rule.pop("automation_authorization", None)
        if payload.active:
            _, shopee_version = app.state.engine.shopee(company)
            if orion:
                destinations = [required(company, "destination", dest) for dest in rule["destinations"]]
                if not destinations or len({d["id"] for d in destinations}) != len(destinations):
                    raise HTTPException(422, "Confira os destinos da automação.")
                async def verify(destination):
                    eligible, reason = app.state.engine.automation_destination_capability(company, destination, rule)
                    if not eligible:
                        raise HTTPException(422, f"{destination.get('name') or 'Destino'}: {reason}")
                    if destination["mode"] == "telegram":
                        transport, credentials, _ = app.state.engine.telegram(company)
                        proof = await transport.verify_destination(destination["chat_id"], credentials["bot_id"], credentials["operator"])
                        if proof.get("chat_id") != destination["chat_id"]:
                            raise HTTPException(409, "O Telegram retornou outro destino. Confira a configuração.")
                    else:
                        proof = await app.state.engine.manual_transport.verify(company, destination)
                        address = destination.get("channel_jid") if destination["mode"] == "whatsapp_channel" else destination.get("address")
                        if proof.get("version") != destination["connection_version"] or proof.get("address") != address:
                            raise HTTPException(409, "A conexão do WhatsApp mudou. Verifique o destino novamente.")
                    return destination["id"], destination_identity(destination)
                try:
                    async with asyncio.timeout(25):
                        verified = await asyncio.gather(*(verify(destination) for destination in destinations))
                except TimeoutError:
                    raise HTTPException(504, "Uma conexão demorou para confirmar o destino. A ativação não foi concluída; tente novamente.") from None
                rule["automation_authorization"] = {"revision": rule["revision"], "authorized_at": int(time.time()),
                    "destinations": dict(verified), "shopee_version": shopee_version}
        rule["active"] = payload.active
        rule["next_at"] = next_slot(rule, int(time.time()) + 2) if payload.active else None
        saved = app.state.store.save_rule(company, ident, rule, expected=previous,
                                          action="rule:enable" if payload.active else "rule:pause")
        return rule_response(company, saved)

    return app


app = create_app()
