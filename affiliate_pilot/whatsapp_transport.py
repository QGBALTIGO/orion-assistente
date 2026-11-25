"""Use the tenant's existing Orion connection for explicit manual offer sends."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re

from cryptography.fernet import Fernet
import httpx

from .providers import ProviderError


ORIGIN = "https://orionagenda.pro"
BASE = "/api/v1/afiliados/bridge/whatsapp/"
PURPOSE = "orion.affiliate.manual-whatsapp.v1"


class OrionWhatsAppTransport:
    def __init__(self, store, *, client_factory=httpx.AsyncClient):
        self.store = store
        self.client_factory = client_factory
        try:
            self.signer = Fernet(os.environ.get("ORION_AFFILIATE_WORKSPACE_KEY", "").encode("ascii"))
        except (ValueError, UnicodeError):
            self.signer = None

    def tenant(self, company):
        with self.store.connect() as db:
            row = db.execute("SELECT username FROM company WHERE id=?", (company,)).fetchone()
        name = row["username"] if row else ""
        match = re.fullmatch(r"orion-([a-f0-9]{32})", name)
        return match[1] if match else None

    def available(self, company):
        return bool(self.signer and self.tenant(company))

    async def call(self, company, action, payload):
        tenant = self.tenant(company)
        if not self.signer or not tenant or action not in {"status", "groups", "verify", "send"}:
            raise ProviderError("permissions", "Conecte seu WhatsApp em Conexões e destinos.")
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path = BASE + action
        claims = {"purpose": PURPOSE, "tenant": tenant, "method": "POST", "path": path,
                  "action": action, "body_sha256": hashlib.sha256(body).hexdigest()}
        token = self.signer.encrypt(json.dumps(claims).encode()).decode("ascii")
        try:
            async with self.client_factory(timeout=48 if action == "send" else 33, trust_env=False,
                                           follow_redirects=False) as client:
                response = await client.post(ORIGIN + path, content=body,
                    headers={"X-Orion-Affiliate-Operation": token, "Content-Type": "application/json"})
            if len(response.content) > 256000:
                raise ValueError
            data = response.json()
            if response.is_success and isinstance(data, dict):
                return data
            if action == "send" and response.status_code >= 500:
                raise ProviderError("unknown", "O WhatsApp ainda não confirmou o envio. Confira o histórico antes de tentar novamente.")
            message = data.get("detail") if isinstance(data, dict) else None
            # Only the private fixed endpoint can supply this small user-facing
            # validation message; no provider body, token or headers are copied.
            if isinstance(message, str) and 1 <= len(message) <= 220:
                raise ProviderError("permissions", message)
        except (httpx.HTTPError, ValueError):
            if action == "send":
                raise ProviderError("unknown", "O WhatsApp ainda não confirmou o envio. Confira o histórico antes de tentar novamente.") from None
        raise ProviderError("connection", "Não foi possível consultar o WhatsApp. Confira a conexão e tente novamente.")

    @staticmethod
    def destination(destination, *, expected=True):
        mode = destination.get("mode")
        address = destination.get("address")
        if mode not in {"whatsapp_group", "whatsapp_channel"} or not isinstance(address, str):
            raise ProviderError("permissions", "Selecione um destino do WhatsApp configurado nesta conta.")
        result = {"mode": mode, "address": address}
        if expected and destination.get("connection_version"):
            result["connection_version"] = destination["connection_version"]
        return result

    async def control(self, company, path, **params):
        if path == "status":
            return await self.call(company, "status", {})
        if path == "groups":
            address = params.get("address")
            if address:
                proof = await self.call(company, "verify", {"destination": {"mode": "whatsapp_group", "address": address}})
                return {"groups": [{"address": proof["address"], "title": proof["title"], "role": proof["role"]}],
                        "version": proof["version"], "transport": "orion"}
            return await self.call(company, "groups", {})
        if path == "channel":
            invite = params.get("invite", "")
            if not isinstance(invite, str) or not re.fullmatch(r"[A-Za-z0-9]{20,32}", invite):
                raise ProviderError("permissions", "Informe o link completo do canal do WhatsApp.")
            return await self.call(company, "verify", {"destination": {
                "mode": "whatsapp_channel", "address": "https://whatsapp.com/channel/" + invite}})
        raise ProviderError("permissions", "Gerencie o aparelho em Conexões e destinos.")

    async def verify(self, company, destination):
        return await self.call(company, "verify", {"destination": self.destination(destination)})

    async def publish(self, job_id, company, destination, text, link, media, thumbnail=None):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ProviderError("permissions", "Publicação inválida.")
        if not destination.get("connection_version", "").startswith("orion:"):
            raise ProviderError("permissions", "Verifique o destino com o WhatsApp conectado nesta conta.")
        if media is not None and (not isinstance(media, bytes) or not 32 <= len(media) <= 1000000):
            raise ProviderError("media", "O card ultrapassou o tamanho permitido.")
        result = await self.call(company, "send", {"job_id": job_id, "destination": self.destination(destination),
            "text": text, "link": link, "media": base64.b64encode(media).decode("ascii") if media else None})
        if result.get("job_id") != job_id or result.get("accepted") is not True or result.get("state") != "sent":
            code = "permissions" if result.get("state") == "refused" else "unknown"
            raise ProviderError(code, "O WhatsApp não confirmou a publicação. Confira o histórico antes de tentar novamente.")
        return result
