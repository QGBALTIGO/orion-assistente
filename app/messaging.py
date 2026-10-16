"""Interface de provedor; a conta Meta real nunca é substituída por mock sem aviso."""
import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Protocol

import httpx


class MessagingProvider(Protocol):
    async def send_text(self, channel: dict, to_e164: str, text: str) -> str: ...
    async def send_list(self, channel: dict, to_e164: str, header: str, rows: list) -> str: ...
    async def send_buttons(self, channel: dict, to_e164: str, body: str, buttons: list) -> str: ...
    async def send_template(self, channel: dict, to_e164: str, name: str, params: dict) -> str: ...
    def parse_webhook(self, headers: dict, body: bytes) -> list: ...


def verify_meta_signature(body: bytes, provided: str | None, secret: str | None) -> bool:
    if not secret or len(secret) < 16 or not provided or not provided.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def extract_messages(payload: dict) -> list[dict]:
    if payload.get("object") != "whatsapp_business_account":
        return []
    events = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_id = value.get("metadata", {}).get("phone_number_id")
            if not isinstance(phone_id, str) or len(phone_id) > 80:
                continue
            for item in value.get("messages", []):
                sender, msgid = item.get("from"), item.get("id")
                if not isinstance(sender, str) or not sender.isdigit() or not sender.startswith("55"):
                    continue
                if not isinstance(msgid, str) or len(msgid) > 200 or not msgid:
                    continue
                body = item.get("text", {}).get("body", "") if item.get("type") == "text" else ""
                if item.get("type") == "interactive":
                    interaction = item.get("interactive", {})
                    body = interaction.get("button_reply", {}).get("id") or interaction.get("list_reply", {}).get("id") or ""
                if not isinstance(body, str):
                    continue
                if not body.strip():
                    continue
                events.append({"phone_number_id": phone_id, "id": msgid,
                               "sender_e164": "+" + sender,
                               "text": body.strip()[:1500]})
    return events[:100]


class FakeMessagingProvider:
    """Provedor determinístico para testes, nunca envia mensagens externas."""
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, channel: dict, to_e164: str, text: str) -> str:
        self.sent.append((to_e164, text))
        return "fake-" + str(len(self.sent))

    async def send_list(self, channel, to_e164, header, rows):
        return await self.send_text(channel, to_e164, header + "\n" + "\n".join(map(str, rows)))

    async def send_buttons(self, channel, to_e164, body, buttons):
        return await self.send_text(channel, to_e164, body + "\n" + "\n".join(map(str, buttons)))

    async def send_template(self, channel, to_e164, name, params):
        return await self.send_text(channel, to_e164, "[modelo de teste] " + name)

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        import json
        return extract_messages(json.loads(body))


class CloudApiProvider(FakeMessagingProvider):
    """Meta oficial: exige token descriptografado em memória via secret vault.

    Não usa endpoint alternativo/QR Code nem mascara falha de entrega.
    """
    async def send_text(self, channel: dict, to_e164: str, text: str) -> str:
        token = channel.get("access_token")
        phone_id = channel.get("phone_number_id")
        version = os.getenv("META_GRAPH_VERSION", "v26.0")
        if not token or not phone_id or not version.startswith("v"):
            raise RuntimeError("Canal Meta não autorizado/configurado")
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.post(
                f"https://graph.facebook.com/{version}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={"messaging_product": "whatsapp", "to": to_e164.removeprefix("+"),
                      "type": "text", "text": {"body": text}},
            )
            r.raise_for_status()
            data = r.json()
            return data["messages"][0]["id"]

    async def send_list(self, channel, to_e164, header, rows):
        # Texto é o fallback universal; nenhuma capacidade não validada é presumida.
        return await self.send_text(channel, to_e164, header + "\n" + "\n".join(map(str, rows)))

    async def send_buttons(self, channel, to_e164, body, buttons):
        return await self.send_text(channel, to_e164, body + "\n" + "\n".join(map(str, buttons)))

    async def send_template(self, channel, to_e164, name, params):
        raise RuntimeError("Template Meta exige aprovação e validação por canal")

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        import json
        return extract_messages(json.loads(body))
