"""Disposable test owner: actual broker lease; no WhatsApp socket or send path."""
import asyncio
import json
import sys
from uuid import UUID

from app.catalog_api import support_session_factory
from app.qr_channels import LeaseRequest, lease_operation


async def main():
    payload = json.loads(await asyncio.to_thread(sys.stdin.readline))
    channel, owner = UUID(payload["channel"]), UUID(payload["owner"])
    async with support_session_factory()() as session:
        grant = await lease_operation(session, channel, payload["credential"], LeaseRequest(owner=owner), "claim")
    # Credential never appears in stdout, log or the process command line.
    print(json.dumps({"owner": str(owner), "fence": grant["fence"]}), flush=True)
    command = await asyncio.to_thread(sys.stdin.readline)
    if command.strip() == "release":
        async with support_session_factory()() as session:
            await lease_operation(session, channel, payload["credential"],
                                  LeaseRequest(owner=owner, fence=grant["fence"]), "release")
        print("released", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
