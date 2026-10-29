"""Read-only Cakto OAuth/offer connectivity probe for Railway predeploy.

This command never creates payments, webhooks, products, sales or subscriptions.
Only emits generic status/counts, never API responses or credentials.
"""
from __future__ import annotations
import asyncio
import sys
from app.cakto_api import CaktoClient, match_plans
from scripts.probe_orion_webhooks_read_only import probe as probe_orion_webhooks


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
    try:
        audit = await probe_orion_webhooks(client, offers, detailed=True)
        print("cakto_orion_webhook_coverage=" + str(audit["webhook_ready"]) +
              "/3", flush=True)
        # Counts only: no URLs, product IDs, webhook IDs or secrets.
        print("cakto_webhook_diagnostics="
              + "offers:" + str(audit["offer_matches"])
              + ";product_links:" + str(audit["offer_product_links"])
              + ";distinct_products:" + str(audit["distinct_products"])
              + ";active_target_hooks:" + str(audit["active_target_hooks"])
              + ";product_coverage:" + str(audit["product_coverage"])
              + ";secret_coverage:" + str(audit["secret_coverage"])
              + ";event_coverage:" + str(audit["event_coverage"]),
              flush=True)
    except Exception:
        # A missing read-webhooks scope must not expose secrets or prevent
        # the safe UI-only rollout. Direct charging remains fail-closed.
        print("cakto_orion_webhook_read_not_verified", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(check()))
