"""Safe one-shot checks for Orion authentication. No credentials or OTPs in logs.

Enable ORION_EMAIL_DELIVERY_PROBE=1 ONLY to send one diagnostic message
to PANEL_TEST_EMAIL via the Resend HTTPS API.
"""
import asyncio
import os
import secrets
import httpx
from sqlalchemy import text
from app.catalog_api import support_session_factory


async def check_schema():
    async with support_session_factory()() as session:
        row=(await session.execute(text("""
            SELECT to_regclass('public.panel_accounts')::text AS account_table,
                   to_regclass('public.panel_email_tokens')::text AS token_table
        """))).mappings().one()
        account=bool(row["account_table"])
        token=bool(row["token_table"])
        attempts=False
        if token:
            result=(await session.execute(text("""
                SELECT EXISTS(SELECT 1 FROM pg_attribute
                    WHERE attrelid='public.panel_email_tokens'::regclass
                      AND attname='attempts' AND NOT attisdropped)
            """))).scalar_one()
            attempts=bool(result)
        print("ORION_ACCOUNT_SCHEMA "+str({
            "accounts_table":account,"email_tokens_table":token,
            "otp_attempts_column":attempts
        }))
        return account and token and attempts


def probe_resend():
    address=os.environ.get("PANEL_TEST_EMAIL","").strip().lower()
    sender=os.environ.get("PANEL_SMTP_FROM","")
    api_key=os.environ.get("PANEL_SMTP_PASSWORD","")
    if not address or not api_key or not sender:
        raise RuntimeError("ORION_EMAIL_PROBE_MISSING_CONFIGURATION")
    if os.environ.get("PANEL_EMAIL_TRANSPORT")!="resend_https":
        raise RuntimeError("ORION_EMAIL_PROBE_NOT_HTTPS")
    payload={
        "from":sender,
        "to":[address],
        "subject":"Orion — teste de entrega de e-mail",
        "text":"Este e-mail confirma o teste de entrega do Orion. Não contém código de acesso. Nenhuma senha foi modificada.",
        "html":"<main style='font-family:sans-serif;padding:24px'><h1>Orion</h1><p>Teste de entrega concluído. Nenhuma senha foi modificada.</p></main>",
    }
    response=httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization":"Bearer "+api_key,
            "Content-Type":"application/json",
            "Idempotency-Key":"orion-email-probe-"+secrets.token_hex(14),
        },
        json=payload,timeout=15.0,
    )
    if response.status_code!=200 or not response.json().get("id"):
        # Do not log provider response body; provider may echo request details.
        print("ORION_RESEND_PROBE_FAILED_HTTP_"+str(response.status_code))
        raise RuntimeError("ORION_RESEND_DELIVERY_REJECTED")
    print("ORION_RESEND_PROBE_ACCEPTED_TO_RESTRICTED_TEST_MAILBOX")


async def main():
    print("ORION_ACCOUNT_PREFLIGHT_START")
    schema_ready=await check_schema()
    if os.environ.get("ORION_EMAIL_DELIVERY_PROBE")=="1":
        await asyncio.to_thread(probe_resend)
    if os.environ.get("ORION_REQUIRE_ACCOUNT_SCHEMA")=="1" and not schema_ready:
        raise RuntimeError("ORION_ACCOUNT_SCHEMA_MIGRATION_REQUIRED")
    print("ORION_ACCOUNT_PREFLIGHT_COMPLETE")


if __name__=="__main__":
    asyncio.run(main())
