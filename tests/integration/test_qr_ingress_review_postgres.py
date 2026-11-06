"""No-replay operator binding uses real SQL, credentials and current ownership.

No bridge or business callback is available. All rows are disposable fixtures.
"""
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import text

from app import qr_channels, qr_ingress_review as review
from tests.integration.test_qr_native_selection_roundtrip_postgres import (  # noqa: F401
    roundtrip_case, inbox_db, automation_db,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def state(c):
    async with c.db.migration.connect() as db:
        result = {}
        for table in ("tenants", "qr_channels", "tenant_memberships", "inbox_channel_links",
                      "appointments", "inbox_controls", "inbox_messages"):
            column = "id" if table == "tenants" else "tenant_id"
            result[table] = (await db.execute(text(f"SELECT to_jsonb(x) FROM {table} x WHERE {column}=:t ORDER BY to_jsonb(x)::text"),
                                           {"t": c.db.tenant})).scalars().all()
        return result


@pytest.mark.parametrize("change", ["active", "assistant_paused", "disabled_channel", "revoked_owner",
    "expired_lease", "membership_revoked", "membership_editor", "unadopted", "wrong_key",
    "wrong_ciphertext", "foreign_primary", "wrong_canonical", "transition_disabled"])
async def test_primary_operator_binding_is_read_only_current_and_server_scoped(roundtrip_case, monkeypatch, change):
    c = roundtrip_case
    secret = "synthetic-operator-primary-" + str(uuid4())
    monkeypatch.setenv("QR_BRIDGE_TOKEN", secret)
    async with c.db.migration.begin() as db:
        await db.execute(text("UPDATE tenants SET whatsapp_channel_id=:c WHERE id=:t"), {"c": c.channel, "t": c.db.tenant})
        await db.execute(text("UPDATE qr_channels SET credential_hash=:hash,credential_encrypted=:encrypted WHERE id=:c"),
                         {"c": c.channel, "hash": qr_channels.credential_hash(secret),
                          "encrypted": qr_channels._cipher().encrypt(secret.encode()).decode()})
        mutations = {
            "assistant_paused": "UPDATE qr_channels SET assistant_enabled=false WHERE id=:c",
            "disabled_channel": "UPDATE qr_channels SET enabled=false WHERE id=:c",
            "revoked_owner": "UPDATE qr_channels SET lease_owner=CAST('00000000-0000-0000-0000-000000000000' AS uuid) WHERE id=:c",
            "expired_lease": "UPDATE qr_channels SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:c",
            "membership_revoked": "UPDATE tenant_memberships SET active=false WHERE tenant_id=:t",
            "membership_editor": "UPDATE tenant_memberships SET role='editor' WHERE tenant_id=:t",
            "unadopted": "DELETE FROM inbox_channel_links WHERE tenant_id=:t AND channel_id=:c",
            "wrong_key": "UPDATE qr_channels SET credential_hash=repeat('f',64) WHERE id=:c",
            "wrong_ciphertext": "UPDATE qr_channels SET credential_encrypted='invalid-synthetic' WHERE id=:c",
            "wrong_canonical": "UPDATE tenants SET whatsapp_channel_id=NULL WHERE id=:t",
        }
        if change in mutations:
            await db.execute(text(mutations[change]), {"c": c.channel, "t": c.db.tenant})
    if change == "foreign_primary": monkeypatch.setenv("QR_AI_TENANT_ID", str(c.db.other))
    if change == "transition_disabled": monkeypatch.setenv("QR_LEGACY_CENTRAL_ENABLED", "0")
    before = await state(c)
    if change in {"active", "assistant_paused"}:
        result = await review._primary_binding()
        assert result == {"tenant_id": str(c.db.tenant), "channel_id": str(c.channel),
                          "session_digest": "b" * 64, "owner": str(c.owner), "fence": 4}
    else:
        with pytest.raises(HTTPException) as denied:
            await review._primary_binding()
        assert denied.value.status_code in {409, 423}
    assert await state(c) == before and not c.db.sent
