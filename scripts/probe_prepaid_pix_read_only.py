"""Read-only verification of three one-time Pix offers and their dedicated hook.

This script never makes a Cakto POST, grants access or alters the Railway
environment. The temporary flag is *process-local* and only used so the
production checkout's exact validator can run before it is enabled.
"""
from __future__ import annotations
import asyncio
import os
from app.cakto_api import CaktoClient
from app.prepaid_pix_api import verified_pix_catalog, pix_cipher

async def check():
    original=os.environ.get("ORION_PIX_PREPAID_ENABLED")
    try:
        os.environ["ORION_PIX_PREPAID_ENABLED"]="1"
        # Creating a resumable Pix requires its encryption key. Validate it
        # locally before any provider GET; never print or retain that key.
        pix_cipher()
        catalog=await verified_pix_catalog(CaktoClient())
        if len(catalog)!=3 or any(
            catalog[code].get("type")!="unique" for code in catalog
        ):
            return False
        return True
    finally:
        if original is None:
            os.environ.pop("ORION_PIX_PREPAID_ENABLED",None)
        else:
            os.environ["ORION_PIX_PREPAID_ENABLED"]=original


def main():
    try:
        ready = asyncio.run(check()) is True
    except Exception:
        # Provider errors can contain sensitive response bodies or credentials.
        # Pre-deploy needs a failed gate, never that private exception text.
        print("orion_prepaid_pix_read_only_probe_failed_closed", flush=True)
        return 3
    print("orion_prepaid_pix_read_only_ready" if ready else
          "orion_prepaid_pix_read_only_not_ready", flush=True)
    return 0 if ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
