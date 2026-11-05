"""Revocation fences API effects immediately without overlapping auth writers."""
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

from app import qr_channels as channels
from test_qr_channels_postgres import channel_case, profiles, create, lease, request


pytestmark = pytest.mark.integration
NIL = UUID(int=0)


async def stored(case, row):
    async with case.migration.connect() as db:
        return dict((await db.execute(text("""SELECT lease_owner,lease_expires_at,fence
            FROM qr_channels WHERE id=:id"""), {"id": row["id"]})).mappings().one())


@pytest.mark.asyncio
async def test_state_revocation_quarantines_both_previous_and_new_owner_until_old_deadline(channel_case):
    c = channel_case
    row, _ = await create(c)
    previous, replacement = uuid4(), uuid4()
    first = await lease(c, row, previous)
    # An ordinary lost-response retry by the same still-live owner is retained.
    assert (await lease(c, row, previous))["fence"] == first["fence"]
    before = await stored(c, row)
    await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, assistant_enabled=True), c.actor)
    for candidate in (previous, replacement):
        with pytest.raises(HTTPException) as denied:
            await lease(c, row, candidate)
        assert denied.value.status_code == 409
    quarantined = await stored(c, row)
    assert quarantined["lease_owner"] == NIL
    assert quarantined["lease_expires_at"] == before["lease_expires_at"]
    assert quarantined["fence"] > first["fence"]
    for action in ("check", "renew", "release"):
        with pytest.raises(HTTPException) as denied:
            await lease(c, row, previous, action, first["fence"])
        assert denied.value.status_code == 409
    for candidate, fence in ((previous, first["fence"]), (NIL, quarantined["fence"])):
        with pytest.raises(HTTPException):
            await channels.authenticated_channel(request(row, candidate, fence), phone="5567999999901")
    # Repeated configuration changes cannot prolong a retired writer's slot.
    await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=3, assistant_enabled=False), c.actor)
    assert (await stored(c, row))["lease_expires_at"] == before["lease_expires_at"]
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                         {"id": row["id"]})
    next_owner = await lease(c, row, replacement)
    assert next_owner["fence"] > quarantined["fence"]
    assert (await channels.authenticated_channel(request(row, replacement, next_owner["fence"]),
                                               phone="5567999999901")).lease_owner == replacement


@pytest.mark.parametrize("owner", [NIL, str(NIL), "00000000000000000000000000000000"])
def test_reserved_quarantine_owner_is_not_a_lease_request(owner):
    with pytest.raises(ValidationError):
        channels.LeaseRequest(owner=owner)


@pytest.mark.asyncio
async def test_state_change_without_live_lease_does_not_add_a_quarantine_delay(channel_case):
    c = channel_case
    row, _ = await create(c)
    no_lease = await stored(c, row)
    assert no_lease["lease_owner"] is None and no_lease["lease_expires_at"] is None
    owner = uuid4()
    await lease(c, row, owner)
    async with c.migration.begin() as db:
        await db.execute(text("UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"),
                         {"id": row["id"]})
    await channels.change_state(c.a, row["id"], channels.ChannelState(expected_revision=2, assistant_enabled=True), c.actor)
    expired = await stored(c, row)
    assert expired["lease_owner"] is None and expired["lease_expires_at"] is None
    assert (await lease(c, row, uuid4()))["fence"] > expired["fence"]
