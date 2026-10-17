"""Safe live-test orchestration; every network operation is a local fake."""
from copy import deepcopy
import json
import os
import stat
from types import SimpleNamespace

import pytest

from scripts import test_live_delivery_once as runner


PILOT = "5511999999999"
OWN = "5511888888888"
ENV = {"QR_AI_PILOT_NUMBER": PILOT}


def options(**changes):
    values = dict(phone=PILOT, marker="ORION-SYNTHETIC-001", ledger_dir="/synthetic-persistent",
                  text=None, scenario="recipient", poll_only=False, observe_seconds=0, interval_seconds=1)
    values.update(changes)
    return SimpleNamespace(**values)


class MemoryLedgers:
    """Cross-invocation snapshots independent of each runner's mutable state."""

    def __init__(self):
        self.saved = {}
        self.history = []

    def __call__(self, directory, marker):
        owner = self

        class Entry:
            created = marker not in owner.saved
            data = deepcopy(owner.saved.get(marker, {}))

            def __enter__(self):
                return self

            def save(self):
                owner.saved[marker] = deepcopy(self.data)
                owner.history.append(deepcopy(self.data))

            def __exit__(self, *args):
                pass

        return Entry()


class FakeBridge:
    def __init__(self, ledgers, *, delivery="pending", ambiguous=False, self_target=False):
        self.ledgers = ledgers
        self.calls = []
        self.rows = []
        self.delivery = delivery
        self.ambiguous = ambiguous
        self.self_target = self_target
        self.connected = True
        self.registered = True

    async def __call__(self, path, method="GET", payload=None, query=None):
        self.calls.append((path, method))
        if path == "/internal/status":
            return {"status": "connected" if self.connected else "reconnecting",
                    "phone": PILOT if self.self_target else OWN}
        if path == "/internal/recipient":
            assert query == {"phone": PILOT}
            return {"registered": self.registered, "identity": "pn_confirmed",
                    "self": self.self_target, "delivery": {"retryRequests": 0}}
        if path == "/internal/messages":
            assert query == {"phone": PILOT}
            return {"items": deepcopy(self.rows)}
        assert path == "/internal/send" and method == "POST"
        assert payload["phone"] == PILOT
        marker = payload["text"].split("\n", 1)[0]
        # Snapshot must already say attempting before the only external mutation.
        assert self.ledgers.saved[marker]["send_state"] == "attempting"
        assert self.ledgers.saved[marker]["send_attempted_at"]
        self.rows.append({"id": "out:SYNTHETIC-ID", "phone": PILOT, "direction": "out",
                          "body": payload["text"], "delivery": self.delivery})
        if self.ambiguous:
            raise TimeoutError("secret exception details must never reach the ledger")
        return {"accepted": True, "delivery": "pending", "id": "out:SYNTHETIC-ID"}

    @property
    def sends(self):
        return self.calls.count(("/internal/send", "POST"))


@pytest.mark.asyncio
@pytest.mark.parametrize("env,phone", [
    (ENV, OWN), ({}, PILOT),
    ({"QR_AI_ALLOWED_SENDERS": PILOT + "," + OWN}, PILOT),
    ({"QR_AI_PILOT_NUMBER": PILOT, "QR_AI_ALLOWED_SENDERS": OWN}, PILOT),
    ({"QR_AI_PILOT_NUMBER": "invalid", "QR_AI_ALLOWED_SENDERS": PILOT}, PILOT),
])
async def test_unconfigured_or_nonpilot_rejected_before_any_network(env, phone):
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    with pytest.raises(runner.Refused):
        await runner.run(options(phone=phone), env=env, bridge=bridge, ledger_factory=ledger)
    assert bridge.calls == []
    assert ledger.saved == {}


def test_singleton_backend_allowlist_can_supply_exact_pilot():
    assert runner.authorized_phone(PILOT, {"QR_AI_ALLOWED_SENDERS": " " + PILOT + " "}) == PILOT


@pytest.mark.asyncio
async def test_accepted_is_not_delivered_or_readable_and_same_marker_never_resends():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    first = await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert first["send_state"] == "accepted_unverified"
    assert first["delivery"] == "pending"
    assert first["handset_readability"] == "unverified"
    bridge.calls.clear()
    await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.calls == [("/internal/messages", "GET")]
    saved = json.dumps(ledger.saved)
    assert PILOT not in saved and OWN not in saved
    assert '"body"' not in saved and '"phone"' not in saved


@pytest.mark.asyncio
async def test_timeout_after_acceptance_recovers_exact_body_and_never_resends():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger, delivery="delivered", ambiguous=True)
    first = await runner.run(options(text="Synthetic hello"), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert first["send_state"] == "ambiguous_never_resend"
    assert first["delivery"] == "delivered"
    assert first["message_id"] == "out:SYNTHETIC-ID"
    assert first["handset_readability"] == "unverified"
    second = await runner.run(options(poll_only=True), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert second["delivery"] == "delivered"
    assert bridge.sends == 1
    assert "secret exception" not in json.dumps(ledger.saved)


@pytest.mark.asyncio
async def test_ambiguous_missing_message_is_observation_only_forever():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger, ambiguous=True)
    await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    bridge.rows.clear()  # May be absent after an ambiguous relay or history eviction.
    for _ in range(2):
        result = await runner.run(options(poll_only=True), env=ENV, bridge=bridge, ledger_factory=ledger)
        assert result["observation"] == "message_not_in_recent_history_never_resend"
    assert bridge.sends == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnected", "unregistered", "self"])
async def test_preflight_refuses_unsafe_or_wrong_scenario(failure):
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger, self_target=failure == "self")
    bridge.connected = failure != "disconnected"
    bridge.registered = failure != "unregistered"
    with pytest.raises(runner.Refused):
        await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0
    assert ledger.saved[options().marker]["send_state"] == "preflight_blocked_never_resend"


@pytest.mark.asyncio
async def test_self_scenario_requires_explicit_selection_and_still_exact_pilot():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger, self_target=True)
    await runner.run(options(scenario="self"), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 1
    assert ledger.saved[options().marker]["self"] is True
    assert ledger.saved[options().marker]["scenario"] == "self"


@pytest.mark.asyncio
async def test_existing_exact_marker_recovers_without_a_send():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    bridge.rows.append({"id": "out:EXISTING", "phone": PILOT, "direction": "out",
                        "body": options().marker, "delivery": "read"})
    result = await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0
    assert result["send_state"] == "existing_message_observation_only"
    assert result["delivery"] == "read"
    assert result["handset_readability"] == "unverified"


@pytest.mark.asyncio
async def test_duplicate_matches_are_ambiguous_not_delivery_success():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    bridge.rows = [{"id": "out:" + message_id, "phone": PILOT, "direction": "out",
                    "body": options().marker, "delivery": "read"} for message_id in ("ONE", "TWO")]
    with pytest.raises(runner.Refused, match="multiple_matching_messages"):
        await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0


@pytest.mark.asyncio
async def test_marker_cannot_be_reused_with_different_body():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    await runner.run(options(text="First"), env=ENV, bridge=bridge, ledger_factory=ledger)
    with pytest.raises(runner.Refused, match="different_body"):
        await runner.run(options(text="Second"), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 1


@pytest.mark.asyncio
async def test_marker_collision_in_history_refuses_new_send():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    bridge.rows.append({"id": "out:EXISTING", "phone": PILOT, "direction": "out",
                        "body": options().marker + "\nDifferent content", "delivery": "pending"})
    with pytest.raises(runner.Refused, match="different_body"):
        await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0


@pytest.mark.asyncio
async def test_failed_attempt_persistence_prevents_send():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)

    def failing_factory(directory, marker):
        entry = ledger(directory, marker)
        save = entry.save

        def fail_before_post():
            if entry.data.get("send_state") == "attempting":
                raise OSError("disk unavailable")
            save()

        entry.save = fail_before_post
        return entry

    with pytest.raises(OSError):
        await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=failing_factory)
    assert bridge.sends == 0
    await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0


def test_recovery_only_matches_exact_outgoing_body_and_recipient():
    marker = options().marker
    base = {"id": "out:EXPECTED", "phone": PILOT, "direction": "out", "body": marker}
    snapshot = {"items": [base, {**base, "direction": "in"}, {**base, "phone": OWN},
                          {**base, "body": marker + " extra"}]}
    assert runner.matching_rows(snapshot, PILOT, runner.digest(marker)) == [base]


@pytest.mark.asyncio
async def test_poll_only_without_ledger_never_performs_network_calls():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)
    with pytest.raises(runner.Refused, match="poll_only_requires_existing_ledger"):
        await runner.run(options(poll_only=True), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.calls == []
    # Even a later non-poll invocation cannot convert that marker into a send.
    await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.sends == 0


@pytest.mark.asyncio
async def test_crash_after_attempt_reservation_can_only_poll():
    ledger = MemoryLedgers()
    bridge = FakeBridge(ledger)

    async def interrupted(path, method="GET", payload=None, query=None):
        if method == "POST":
            raise KeyboardInterrupt
        return await bridge(path, method, payload, query)

    with pytest.raises(KeyboardInterrupt):
        await runner.run(options(), env=ENV, bridge=interrupted, ledger_factory=ledger)
    assert ledger.saved[options().marker]["send_state"] == "attempting"
    bridge.calls.clear()
    await runner.run(options(), env=ENV, bridge=bridge, ledger_factory=ledger)
    assert bridge.calls == [("/internal/messages", "GET")]


@pytest.mark.skipif(os.name != "posix", reason="Real POSIX permissions/flock require Linux; run in CI")
def test_real_ledger_is_private_fsynced_locked_and_survives_reopening(tmp_path, monkeypatch):
    directory = tmp_path / "protected"
    syncs = []
    original = os.fsync

    def record_sync(fd):
        syncs.append(fd)
        original(fd)

    monkeypatch.setattr(os, "fsync", record_sync)
    with runner.Ledger(directory, "ORION-SYNTHETIC") as ledger:
        assert ledger.created
        ledger.data = {"send_state": "attempting"}
        ledger.save()
        assert len(syncs) >= 2  # Directory entry and file content before send.
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600
        with pytest.raises(runner.Refused, match="ledger_already_in_use"):
            runner.Ledger(directory, "ORION-SYNTHETIC")
    with runner.Ledger(directory, "ORION-SYNTHETIC") as resumed:
        assert not resumed.created
        assert resumed.data == {"send_state": "attempting"}


@pytest.mark.skipif(os.name != "posix", reason="Real POSIX permissions require Linux; run in CI")
def test_real_ledger_refuses_open_permissions_symlink_and_corruption(tmp_path):
    directory = tmp_path / "protected"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    with pytest.raises(runner.Refused, match="0700"):
        runner.Ledger(directory, "ORION-SYNTHETIC")
    directory.chmod(0o700)
    target = directory / "ORION-SYNTHETIC.json"
    target.write_text("{}")
    target.chmod(0o644)
    with pytest.raises(runner.Refused, match="0600"):
        runner.Ledger(directory, "ORION-SYNTHETIC")
    target.chmod(0o600)
    target.write_text("{truncated")
    with pytest.raises(json.JSONDecodeError):
        runner.Ledger(directory, "ORION-SYNTHETIC")
    (tmp_path / "linked").symlink_to(directory, target_is_directory=True)
    with pytest.raises(runner.Refused, match="symlink"):
        runner.Ledger(tmp_path / "linked", "ORION-SYNTHETIC")


@pytest.mark.skipif(os.name == "posix", reason="Windows-specific refusal")
def test_windows_permissions_are_not_assumed_safe(tmp_path):
    with pytest.raises(runner.Refused, match="windows_acl_not_verified"):
        runner.Ledger(tmp_path, "ORION-SYNTHETIC")
