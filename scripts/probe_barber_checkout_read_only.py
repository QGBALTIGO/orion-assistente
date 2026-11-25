"""Read-only Cakto readiness check for the embedded Barbearia checkout."""
import asyncio
import os

from fastapi import HTTPException
from sqlalchemy import text

from app.catalog_api import support_session_factory

from app.cakto_api import cached_cakto_client
from app.plan_catalog import CODES
from app.plan_management import _exact_offer, verify_embedded_checkout


async def check():
    client = cached_cakto_client()
    lifecycle = os.getenv("ORION_CAKTO_LIFECYCLE_ENABLED", "0") == "1"
    print(f"orion_barber_checkout_lifecycle={int(lifecycle)}", flush=True)
    try:
        async with support_session_factory()() as db:
            outside = (await db.execute(text("""
                SELECT count(*) FROM orion_cakto_bindings b
                LEFT JOIN business_onboarding o ON o.tenant_id=b.tenant_id
                WHERE o.configuration->>'niche' IS DISTINCT FROM 'barbearia'
            """))).scalar_one()
        print(f"orion_barber_checkout_other_niche_bindings={outside}", flush=True)
    except Exception:
        print("orion_barber_checkout_other_niche_bindings=unavailable", flush=True)
    if not client.configured:
        print("orion_barber_checkout_blocked=provider_credentials", flush=True)
        return
    try:
        offers = await client.list_offers()
        matches = sum(_exact_offer(offers, plan) is not None for plan in CODES.values())
        print(f"orion_barber_checkout_monthly_offers={matches}/3", flush=True)
        scopes = client.granted_scopes(frozenset({"read", "orders", "subscriptions"})) is True
        print(f"orion_barber_checkout_order_read_scope={int(scopes)}", flush=True)
        await verify_embedded_checkout(client, offers)
    except HTTPException as error:
        print(f"orion_barber_checkout_blocked=provider_verification:{error.status_code}", flush=True)
    except Exception:
        # Provider errors can include payment or credential data.
        print("orion_barber_checkout_blocked=provider_unavailable", flush=True)
    else:
        print("orion_barber_checkout_provider_verified", flush=True)


if __name__ == "__main__":
    asyncio.run(check())
