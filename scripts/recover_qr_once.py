"""One-shot safe recovery on a confirmed revoked WhatsApp session.

Does NOT delete a connected session and never emits QR, phone or secrets.
Run once as a Railway pre-deploy job, then restore regular probe.
"""
import asyncio
import sys

from fastapi import HTTPException
from app.qr_api import _bridge


async def main():
    result=await _bridge("/internal/status")
    state=result.get("status")
    print(f"QR recovery preflight state={state}")
    if state=="connected":
        print("Already connected; no changes.")
        return
    if state in ("logged_out","pair_failed"):
        changed=await _bridge("/internal/recover","POST")
        if changed.get("status")!="connecting":
            print("Recovery request was not acknowledged.")
            raise SystemExit(3)
        print("Revoked session isolated; awaiting fresh QR.")
    elif state not in ("starting","connecting","qr_ready","qr_delayed",
                        "qr_expired","reconnecting","pairing","pair_expired"):
        print("Unexpected status; no mutation performed.")
        raise SystemExit(2)
    for attempt in range(20):
        await asyncio.sleep(2)
        result=await _bridge("/internal/status")
        if result.get("status") in ("qr_ready","connected"):
            print(f"QR_RECOVERY_READY status={result['status']}")
            return
        if result.get("status")=="logged_out":
            print("Session still revoked; manual inspection required.")
            raise SystemExit(3)
    print("QR recovery timed out")
    raise SystemExit(3)


if __name__=="__main__":
    asyncio.run(main())
