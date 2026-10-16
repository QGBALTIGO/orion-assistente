"""Probe Railway interno API -> WhatsApp bridge antes de promover novo deploy.

Nunca imprime número, QR Code, tokens ou credenciais.
"""
import asyncio
import sys

from fastapi import HTTPException
from app.qr_api import _bridge


async def main():
    print("Orion QR pre-deploy: verificando conectividade privada.")
    for attempt in range(1,13):
        try:
            result=await _bridge("/internal/status")
            state=result.get("status")
            if state not in ("starting","connecting","qr_ready","qr_delayed",
                             "qr_expired","pairing","pair_expired","pair_failed",
                             "connected","reconnecting","logged_out","error"):
                print("Orion QR: resposta inválida do bridge")
                sys.exit(2)
            print(f"Orion QR: CONNECTED_TO_PRIVATE_BRIDGE, state={state}")
            return
        except HTTPException as exc:
            if attempt in (1,4,8,12):
                print(f"Orion QR: tentativa {attempt}/12, HTTP={exc.status_code}")
            await asyncio.sleep(3)
    print("Orion QR: FAILED_PRIVATE_NETWORK_PROBE")
    sys.exit(2)


if __name__=="__main__":
    asyncio.run(main())
