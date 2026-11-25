"""Sender authorized for one recurring rule and one bound WhatsApp channel."""
from __future__ import annotations

import base64
import json
import os
import re
import time

import httpx

from .providers import ProviderError


class PromoTech:
    def __init__(self, binding, token, port):
        self.binding = binding
        self.token = token
        self.port = port

    @classmethod
    def configured(cls):
        raw = os.environ.get("AFFILIATE_PROMOTECH_BINDING")
        if not raw:
            return None
        b = json.loads(raw)
        assert all(re.fullmatch(r"[a-f0-9]{32}", b[key]) for key in ("company", "destination", "rule"))
        assert re.fullmatch(r"[1-9]\d{7,23}@newsletter", b["jid"]) and re.fullmatch(r"[a-f0-9]{64}", b["version"])
        assert re.fullmatch(r"[A-Za-z0-9]{20,32}", b["invite"])
        return cls(b, os.environ["AFFILIATE_WA_TOKEN"], int(os.environ["AFFILIATE_WA_PORT"]))

    def permits(self, company, destination, rule):
        b = self.binding
        return (company == b["company"] and destination.get("id") == b["destination"]
                and destination.get("channel_jid") == b["jid"] and destination.get("connection_version") == b["version"]
                and destination.get("verified") and rule and rule.get("id") == b["rule"]
                and rule.get("mode") == "automatic" and rule.get("interval", 0) >= 30
                and rule.get("destinations") == [b["destination"]])

    async def publish(self, ident, text, link, jpeg, thumbnail):
        request = {"id": ident, "caption": text, "link": link, "expires": int(time.time()) + 40,
                   "jpeg": base64.b64encode(jpeg).decode(), "thumbnail": base64.b64encode(thumbnail).decode()}
        try:
            async with httpx.AsyncClient(timeout=42, trust_env=False) as client:
                response = await client.post(f"http://127.0.0.1:{self.port}/promotech", json=request,
                                             headers={"Authorization": "Bearer " + self.token})
            result = response.json()
        except (httpx.HTTPError, ValueError):
            raise ProviderError("unknown", "O WhatsApp não confirmou o retorno. Confira o canal.") from None
        status = result.get("status")
        if status == "published" and re.fullmatch(r"\d{1,16}", result.get("server_id") or ""):
            return result["server_id"]
        if status == "cooldown":
            raise ProviderError("rate", "Aguardando o intervalo de 30 minutos.", retry_after=max(4, int(result["seconds"])))
        if status in {"offline", "busy"}:
            raise ProviderError("network", "O WhatsApp do piloto está temporariamente indisponível.")
        if status == "permission":
            raise ProviderError("permissions", "A conta vinculada precisa administrar o PromoTech.")
        if status in {"invalid", "rejected", "upload_failed"}:
            raise ProviderError("publish", "O WhatsApp não aceitou esta publicação.")
        raise ProviderError("unknown", "O WhatsApp pode ter recebido esta postagem. Confira o canal.")
