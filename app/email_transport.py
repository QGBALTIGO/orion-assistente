"""Finite transactional templates and a guarded, idempotent Resend transport.

Neither provider bodies, credentials, recipients nor template secrets belong in
exceptions. A provider receipt means accepted, never delivered to the mailbox.
"""
from email.utils import parseaddr
from html import escape
import hmac
import os
import re

from cryptography.fernet import Fernet
import httpx

KINDS = frozenset({"verify", "reset", "password_changed", "team_invite",
                   "team_access_approved", "team_access_revoked"})


class EmailProviderError(Exception):
    def __init__(self, code, *, retryable=False, ambiguous=False):
        super().__init__(code)
        self.code, self.retryable, self.ambiguous = code, retryable, ambiguous


def normalize_recipient(value):
    if not isinstance(value, str):
        raise ValueError("invalid_email_recipient")
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", value):
        raise ValueError("invalid_email_recipient")
    return value


def public_origin():
    from app.public_origins import canonical_public_origin
    if not os.getenv("PANEL_PUBLIC_URL", "").strip():
        raise ValueError("missing_email_public_origin")
    return canonical_public_origin()


def cipher():
    return Fernet(os.environ.get("PANEL_EMAIL_FERNET_KEY", "").encode())


def allowed_recipient(recipient):
    if os.getenv("PANEL_TEST_MODE") != "1":
        return True
    target = os.getenv("PANEL_TEST_EMAIL", "").strip().lower()
    return bool(target) and hmac.compare_digest(recipient, target)


def sender():
    value = os.getenv("PANEL_SMTP_FROM", "").strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid_email_sender")
    _, address = parseaddr(value)
    normalize_recipient(address)
    return value


def configured():
    try:
        public_origin()
        sender()
        cipher()
        if os.getenv("PANEL_TEST_MODE") == "1":
            normalize_recipient(os.getenv("PANEL_TEST_EMAIL", ""))
    except (ValueError, TypeError, KeyError):
        return False
    return (os.getenv("PANEL_EMAIL_ENABLED") == "1"
            and os.getenv("PANEL_EMAIL_LIVE_SEND") == "1"
            and os.getenv("PANEL_EMAIL_TRANSPORT") == "resend_https"
            and bool(os.getenv("PANEL_SMTP_PASSWORD"))
            and len(os.getenv("PANEL_OTP_SECRET", "")) >= 32)


def _plain(value, limit=120):
    value = " ".join(str(value or "").split())
    return "".join(c for c in value if ord(c) >= 32 and ord(c) != 127)[:limit]


def render_message(recipient, kind, *, code=None, invitation_token=None, business_name=None, origin=None):
    """Freeze the complete provider payload once; retries must be identical."""
    recipient = normalize_recipient(recipient)
    if kind not in KINDS:
        raise ValueError("invalid_email_kind")
    # Callers cannot select another origin through a request body or header.
    canonical = public_origin()
    if origin is not None and origin != canonical:
        raise ValueError("invalid_email_origin")
    business = _plain(business_name) or "sua empresa"
    link = canonical + "/panel"
    if kind in {"verify", "reset"}:
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError("invalid_email_code")
        subject = "Seu código de confirmação — Orion" if kind == "verify" else "Seu código para redefinir a senha — Orion"
        intro = "Para confirmar seu e-mail no Orion, digite o código abaixo." if kind == "verify" else "Se você solicitou uma nova senha no Orion, digite este código."
        detail = "O código expira em 10 minutos e só pode ser usado uma vez. Se não foi você, ignore este e-mail."
        content = '<p>' + intro + '</p><p style="font-size:32px;font-weight:bold;letter-spacing:6px">' + code + '</p><p>' + detail + '</p>'
        body = intro + "\n\n" + code + "\n\n" + detail
        if kind == "reset":
            link += "?auth=forgot"
    elif kind == "team_invite":
        if not isinstance(invitation_token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", invitation_token):
            raise ValueError("invalid_invitation_token")
        subject = "Você recebeu um convite de equipe — Orion"
        body = "Você recebeu um convite para " + business + ". Entre com o e-mail que recebeu este convite e solicite acesso. O proprietário revisará a solicitação. O convite expira em sete dias. Se não esperava este convite, ignore a mensagem."
        link = canonical + "/convite#token=" + invitation_token
        content = '<p>' + escape(body) + '</p>'
    elif kind == "password_changed":
        subject = "Sua senha foi alterada — Orion"
        body = "A senha da sua conta Orion foi alterada. As sessões anteriores foram invalidadas. Se não reconhece esta alteração, recupere sua senha no site oficial. Nunca compartilhe códigos de acesso."
        link += "?auth=forgot"
        content = '<p>' + body + '</p>'
    else:
        approved = kind == "team_access_approved"
        subject = "Seu acesso à empresa foi aprovado — Orion" if approved else "Seu acesso à empresa foi encerrado — Orion"
        body = ("Seu acesso a " + business + " foi aprovado. Entre no Orion para acessar a empresa."
                if approved else "Seu acesso a " + business + " foi encerrado. Se precisar de esclarecimentos, fale com o proprietário da empresa.")
        content = '<p>' + escape(body) + '</p>'
    html = ('<!doctype html><html lang="pt-BR"><body style="margin:0;background:#f5f8fc;color:#172338;font-family:Arial,sans-serif;line-height:1.6">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td style="padding:24px 16px">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:480px;margin:auto;background:#ffffff;border:1px solid #dce4ef;border-radius:16px">'
            '<tr><td style="padding:28px"><a href="' + escape(canonical, quote=True) + '" style="text-decoration:none">'
            '<img src="' + escape(canonical, quote=True) + '/brand/orion-email-logo.png" width="176" height="61" '
            'alt="Orion — Recepcionista Digital" style="display:block;border:0;width:176px;max-width:100%;height:auto;margin:0 0 28px"></a>'
            + content + '<p style="margin:24px 0"><a href="' + escape(link, quote=True)
            + '" style="display:inline-block;background:#347fff;color:#ffffff;padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:bold">Acessar Orion</a></p>'
            '<p style="margin:24px 0 0;padding-top:20px;border-top:1px solid #dce4ef;font-size:12px;color:#65738a">'
            'Mensagem automática do Orion. Não responda com senhas ou códigos.</p></td></tr></table></td></tr></table></body></html>')
    return {"from": sender(), "to": [recipient], "subject": subject,
            "text": body + "\n\n" + link + "\n\nEquipe Orion", "html": html}


async def send_message(payload, outbox_id):
    """The only new egress boundary; fail closed even if called directly."""
    if not configured():
        raise EmailProviderError("email_disabled")
    if (not isinstance(payload, dict) or set(payload) != {"from", "to", "subject", "text", "html"}
            or not isinstance(payload["to"], list) or len(payload["to"]) != 1):
        raise EmailProviderError("email_payload_invalid")
    try:
        recipient = normalize_recipient(payload["to"][0])
    except ValueError:
        raise EmailProviderError("email_payload_invalid") from None
    if not allowed_recipient(recipient):
        raise EmailProviderError("email_recipient_suppressed")
    # Payload, including From and URLs, was frozen at enqueue. A configuration
    # change must never cause the same key to identify a different request.
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.post("https://api.resend.com/emails",
                headers={"Authorization": "Bearer " + os.environ["PANEL_SMTP_PASSWORD"],
                         "Content-Type": "application/json", "Idempotency-Key": "orion-email/" + str(outbox_id)},
                json=payload)
    except (httpx.HTTPError, OSError):
        raise EmailProviderError("email_transport_ambiguous", retryable=True, ambiguous=True) from None
    if response.status_code == 429 or response.status_code >= 500:
        raise EmailProviderError("email_provider_unavailable", retryable=True, ambiguous=response.status_code >= 500)
    if response.status_code == 409:
        # Same-key concurrent request is retryable; payload mismatch is not.
        try:
            data = response.json()
        except ValueError:
            data = {}
        if isinstance(data, dict) and data.get("name") == "concurrent_idempotent_requests":
            raise EmailProviderError("email_provider_busy", retryable=True)
        raise EmailProviderError("email_idempotency_conflict")
    if not 200 <= response.status_code < 300:
        raise EmailProviderError("email_provider_rejected")
    try:
        data = response.json()
    except ValueError:
        data = None
    message_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(message_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", message_id):
        raise EmailProviderError("email_receipt_ambiguous", retryable=True, ambiguous=True)
    return message_id
