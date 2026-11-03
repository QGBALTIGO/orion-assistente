"""Read-only verification of three one-time Pix offers and their dedicated hook.

This script never makes a Cakto POST, grants access or alters the Railway
environment. The temporary flag is *process-local* and only used so the
production checkout's exact validator can run before it is enabled.
"""
from __future__ import annotations
import os
from app.cakto_api import CaktoClient
from app.prepaid_pix_api import verified_pix_catalog

async def check():
    original=os.environ.get("ORION_PIX_PREPAID_ENABLED")
    try:
        os.environ["ORION_PIX_PREPAID_ENABLED"]="1"
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
