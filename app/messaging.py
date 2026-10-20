"""Interface de provedor; a conta Meta real nunca é substituída por mock sem aviso."""
import hashlib
import hmac
import os
from datetime import datetime,timezone
from uuid import UUID
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.customer_names import contact_phone, useful_name
from app.communication_events import envelope, safe_body, identifier


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
            names = {}
            contacts = value.get("contacts", [])
            for contact in contacts if isinstance(contacts, list) else []:
                if not isinstance(contact, dict):
                    continue
                wa_id = contact_phone(contact.get("wa_id"))
                profile = contact.get("profile")
                name = useful_name(profile.get("name")) if isinstance(profile, dict) else None
                if wa_id and name:
                    names[wa_id] = name
            for item in value.get("messages", []):
                sender, msgid = item.get("from"), item.get("id")
                if not isinstance(sender, str) or not sender.isdigit() or not sender.startswith("55"):
                    continue
                if not isinstance(msgid, str) or len(msgid) > 200 or not msgid:
                    continue
                kind = item.get('type', 'unknown')
                body = item.get("text", {}).get("body", "") if kind == "text" else ""
                if kind in {'image','video','document'}:
                    media = item.get(kind)
                    body = media.get('caption', '') if isinstance(media, dict) else ''
                if kind == "interactive":
                    interaction = item.get("interactive", {})
                    body = interaction.get("button_reply", {}).get("id") or interaction.get("list_reply", {}).get("id") or ""
                from app.communication_events import bounded_control
                safety_control=bounded_control(body) if isinstance(body,str) and len(body)>1500 and kind!='interactive' else None
                body, issue = safe_body(body)
                if not body and kind == 'text' and not issue: continue
                context = item.get('context') if isinstance(item.get('context'),dict) else {}
                target = (item.get('reaction') or {}).get('message_id') if isinstance(item.get('reaction'),dict) else None
                event = {"phone_number_id": phone_id, "id": msgid,
                         "sender_e164": "+" + sender,
                         "text": body,
                         "envelope": envelope(kind, reply_to=context.get('id'), target=target,
                             oversized=issue=='oversized',invalid=issue=='invalid',control=safety_control)}
                timestamp=item.get("timestamp")
                if isinstance(timestamp,str) and timestamp.isascii() and timestamp.isdigit() and len(timestamp)<=10:
                    event["provider_at"]=datetime.fromtimestamp(int(timestamp),timezone.utc)
                if sender in names:
                    event["whatsapp_name"] = names[sender]
                events.append(event)
                if len(events)>100:raise ValueError('too_many_events')
    return events


def extract_statuses(payload):
    if payload.get("object")!="whatsapp_business_account":
        return []
    result=[]
    for entry in payload.get("entry",[]):
        for change in entry.get("changes",[]):
            value=change.get("value",{})
            channel=value.get("metadata",{}).get("phone_number_id")
            if not isinstance(channel,str) or not 1<=len(channel)<=80:
                continue
            for item in value.get("statuses",[]):
                external,phone,status=item.get("id"),contact_phone(item.get("recipient_id")),item.get("status")
                if isinstance(external,str) and 1<=len(external)<=200 and phone and status in {"sent","delivered","read","failed"}:
                    record={"phone_number_id":channel,"id":external,"phone":"+"+phone,"status":"accepted" if status=="sent" else status}
                    try:record['operation_id']=str(UUID(item.get('biz_opaque_callback_data','')))
                    except (ValueError,TypeError,AttributeError):pass
                    result.append(record)
                if len(result)>100:raise ValueError('too_many_events')
    return result


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
        from app.qr_presentation import fits_message
        if not isinstance(text,str) or not text.strip() or not fits_message(text):
            raise ValueError('Complete bounded message required before transport')
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
                      "type": "text", "text": {"body": text},
                      **({"biz_opaque_callback_data":str(UUID(channel["operation_id"]))} if channel.get("operation_id") else {})},
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
