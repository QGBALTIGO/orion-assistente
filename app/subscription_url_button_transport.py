"""Fresh support proof for the new direct format, outside owner SQL locks.

Existing message formats and idempotent recovery do not depend on this probe.
The URL in the message is never consulted; only the fixed private bridge is read.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from uuid import UUID

from fastapi import HTTPException

from app import qr_channels as channels
from app import subscription_direct_store as store


def unavailable():
    return store.fail("DIRECT_FORMAT_UNAVAILABLE")


@dataclass(frozen=True)
class TransportSnapshot:
    tenant: UUID
    actor: UUID
    device: UUID
    generation: UUID
    revision: int
    channel: UUID
    fence: int
    owner: UUID
    credential_hash: str
    transport: dict = field(repr=False)


@dataclass(frozen=True)
class CompatibleTransport:
    snapshot: TransportSnapshot
    verified_at: float


async def snapshot(db, tenant, actor, device_id):
    device, transport = await store.shared.check_device(db, tenant, UUID(str(device_id)), actor_id=actor.account_id)
    expiry = transport.get("lease_expires_at")
    if (device.get("status") in {"disabled", "removing", "removed"} or transport.get("enabled") is not True
            or not isinstance(transport.get("lease_owner"), UUID) or transport["lease_owner"].int == 0
            or type(transport.get("fence")) is not int or transport["fence"] < 1
            or not isinstance(expiry, datetime) or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
        raise unavailable()
    return TransportSnapshot(tenant, actor.account_id, device["id"], device["generation"], device["revision"],
        device["channel_id"], transport["fence"], transport["lease_owner"], transport["credential_hash"], dict(transport))


async def compatible(expected):
    # Call only after the caller's owner_transaction has exited. The bridge
    # validates its lease through the API and needs those same database locks.
    try:
        value = await channels._bridge_control(expected.transport, "device/status", "GET")
    except HTTPException:
        raise unavailable() from None
    if (not isinstance(value, dict) or value.get("status") != "connected"
            or not isinstance(value.get("capabilities"), dict) or value["capabilities"].get("url_button") is not True
            or value.get("channel_id") != str(expected.channel) or value.get("device_id") != str(expected.device)
            or value.get("generation") != str(expected.generation)
            or type(value.get("settings_revision")) is not int or value["settings_revision"] != expected.revision
            or type(value.get("applied_revision")) is not int or value["applied_revision"] != expected.revision
            or type(value.get("fence")) is not int or value["fence"] != expected.fence
            or isinstance(value.get("connection_health"), dict) and value["connection_health"].get("state") != "connected"):
        raise unavailable()
    return CompatibleTransport(expected, time.monotonic())


async def revalidate(db, tenant, actor, device_id, proof):
    if (not isinstance(proof, CompatibleTransport) or time.monotonic() - proof.verified_at > 20
            or proof.snapshot.tenant != tenant or proof.snapshot.actor != actor.account_id
            or str(proof.snapshot.device) != str(device_id)):
        raise unavailable()
    current = await snapshot(db, tenant, actor, device_id)
    before = proof.snapshot
    if any(getattr(current, key) != getattr(before, key) for key in
           ("tenant", "actor", "device", "generation", "revision", "channel", "fence", "owner", "credential_hash")):
        raise unavailable()
