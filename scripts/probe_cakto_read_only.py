"""Read-only Cakto OAuth/offer connectivity probe for Railway predeploy.

This command never creates payments, webhooks, products, sales or subscriptions.
Only emits generic status/counts, never API responses or credentials.
"""
from __future__ import annotations
import asyncio
import sys
from app.cakto_api import CaktoClient, match_plans


async def check():
    client = CaktoClient()
    if not client.configured:
        print("cakto_oauth_not_configured", file=sys.stderr)
        return 2
    try:
        offers = await client.list_offers()
        matched = match_plans(offers)
    except Exception:
        print("cakto_read_only_connectivity_failed", file=sys.stderr)
        return 3
    print("cakto_oauth_offers_read_ok", flush=True)
    print("cakto_catalog_matches=" + str(sum(plan["found"] for plan in matched)) +
          "/3;cakto_ambiguous_matches=" + str(sum(plan["ambiguous"] for plan in matched)),
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
