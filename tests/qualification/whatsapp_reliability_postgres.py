"""Cross-process ownership transfer on real synthetic PostgreSQL channels.

These tests kill a disposable broker-lease holder, not a WhatsApp socket. Actual
cryptographic session resume, network reconnect and recipient legibility require
separate explicitly authorized device tests.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
# Existing integration fixtures intentionally import neighboring fixture modules
# by basename. Keep that established convention usable for this standalone file.
sys.path.insert(0, str(ROOT / "tests/integration"))

# Reuse the existing license/auth/RLS fixtures and actual production operations.
from tests.integration.test_qr_channels_postgres import channel_case, create, lease, profiles  # noqa: F401
from tests.integration.test_onboarding_completion_plan_postgres import disposable_environment  # noqa: F401


pytestmark = pytest.mark.integration


async def start_owner(row):
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "scripts.qualification.whatsapp_reliability_owner",
        cwd=ROOT, env={**os.environ, "QR_CHANNELS_ENABLED": "1"},
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    owner = uuid4()
    process.stdin.write((json.dumps({"channel": str(row["id"]), "owner": str(owner),
                                    "credential": row["credential"]}) + "\n").encode())
    await process.stdin.drain()
    try:
        raw = await asyncio.wait_for(process.stdout.readline(), 15)
        if not raw:
            await process.wait()
            raise RuntimeError("Disposable owner exited before claiming its lease")
        grant = json.loads(raw)
        assert grant["owner"] == str(owner) and type(grant["fence"]) is int
        return process, owner, grant["fence"]
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise


async def stop_owner(process):
    if process.returncode is None:
        process.kill()
        await asyncio.wait_for(process.wait(), 10)


async def test_graceful_process_release_transfers_lease_without_waiting_for_expiry(channel_case):
    c = channel_case
    row, _ = await create(c)
    process, owner, fence = await start_owner(row)
    try:
        process.stdin.write(b"release\n")
        await process.stdin.drain()
        assert (await asyncio.wait_for(process.stdout.readline(), 10)).strip() == b"released"
        assert await asyncio.wait_for(process.wait(), 10) == 0
        next_grant = await lease(c, row, uuid4())
        assert next_grant["fence"] > fence
        with pytest.raises(HTTPException) as stale:
            await lease(c, row, owner, "check", fence)
        assert stale.value.status_code == 409
    finally:
        await stop_owner(process)


async def test_crashed_owner_cannot_steal_back_and_does_not_interrupt_another_company(channel_case):
    c = channel_case
    failed_row, _ = await create(c)
    healthy_row, _ = await create(c, tenant=c.b)
    healthy_owner = uuid4()
    healthy = await lease(c, healthy_row, healthy_owner)
    process, old_owner, old_fence = await start_owner(failed_row)
    try:
        await stop_owner(process)  # No finally/release code runs in the owner.
        with pytest.raises(HTTPException) as premature:
            await lease(c, failed_row, uuid4())
        assert premature.value.status_code == 409
        # Compress only the synthetic failed channel's remaining 30-second TTL.
        # This is a transfer/fencing check, not an observed production RTO.
        async with c.migration.begin() as db:
            await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()+interval '1 second' WHERE id=:id"),
                             {"id": failed_row["id"]})
        assert (await lease(c, healthy_row, healthy_owner, "check", healthy["fence"]))["fence"] == healthy["fence"]
        await asyncio.sleep(1.1)
        successor = await lease(c, failed_row, uuid4())
        assert successor["fence"] > old_fence
        for action in ("check", "renew", "release"):
            with pytest.raises(HTTPException) as stale:
                await lease(c, failed_row, old_owner, action, old_fence)
            assert stale.value.status_code == 409
        assert (await lease(c, healthy_row, healthy_owner, "renew", healthy["fence"]))["fence"] == healthy["fence"]
    finally:
        await stop_owner(process)
