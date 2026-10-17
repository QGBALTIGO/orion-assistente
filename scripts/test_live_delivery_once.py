"""One deliberately authorized pilot send, followed by read-only observation.

Run inside Railway's private network, never as a deploy/predeploy command:
  python -m scripts.test_live_delivery_once --phone "$QR_AI_PILOT_NUMBER" \
    --marker ORION-TESTE-001 --ledger-dir /persistent/operator-delivery-tests
Repeat that command (or add --poll-only) to observe, NEVER to resend.

The operator must select a PERSISTENT mounted directory, retained across runs
and deployments. /tmp and an ordinary container filesystem are not durable
across deployment. The API currently has no volume: mount protected storage
before using this runner there; the bridge's /data is not shared with the API.
Do not put this ledger inside or replace the WhatsApp authentication directory.
Losing the ledger loses the cross-run send guard. Keep the same directory for
every run; do not change markers to retry an ambiguous result.

POSIX directory/file ownership and 0700/0600 modes are enforced. Windows is
refused: chmod does not verify Windows ACL privacy. Use the private POSIX runner
with protected persistent storage, rather than treating a Windows ACL as checked.
No phone, body, credential, or exception detail is printed or stored in the ledger.
A delivery receipt never proves that a human saw readable content on the handset.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AbstractContextManager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


PHONE = re.compile(r"[1-9][0-9]{7,14}\Z")
MARKER = re.compile(r"ORION-[A-Za-z0-9][A-Za-z0-9._-]{0,89}\Z")
DELIVERY = {"pending", "server_accepted", "delivered", "read", "played", "failed"}
CONFIRMED = {"delivered", "read", "played"}


class Refused(Exception):
    """Safe, non-sensitive error codes only."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def authorized_phone(phone: str, env: dict[str, str]) -> str:
    if not PHONE.fullmatch(phone):
        raise Refused("invalid_explicit_phone")
    pilot = env.get("QR_AI_PILOT_NUMBER", "")
    allowed = {item.strip() for item in env.get("QR_AI_ALLOWED_SENDERS", "").split(",") if item.strip()}
    if pilot:
        # A contradictory API allowlist is a configuration error, not permission.
        if not PHONE.fullmatch(pilot) or (allowed and allowed != {pilot}):
            raise Refused("inconsistent_pilot_configuration")
    elif len(allowed) == 1:
        pilot = next(iter(allowed))
    else:
        raise Refused("single_pilot_not_configured")
    if phone != pilot:
        raise Refused("recipient_is_not_configured_pilot")
    return phone


class Ledger(AbstractContextManager):
    """Exclusive reservation, per-marker process lock, and fsynced snapshots.

    A partial/corrupt snapshot fails closed. Never delete or recreate it to retry.
    The containing directory must be on operator-provisioned persistent storage.
    """

    def __init__(self, directory: Path, marker: str):
        if os.name != "posix":
            raise Refused("posix_private_storage_required_windows_acl_not_verified")
        import fcntl

        if not directory.is_absolute():
            raise Refused("absolute_persistent_ledger_directory_required")
        if directory.resolve() != directory:
            raise Refused("ledger_symlink_or_noncanonical_path")
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700
                or metadata.st_uid != os.geteuid()):
            raise Refused("ledger_directory_must_be_owned_mode_0700")
        self.path = directory / (marker + ".json")
        flags = os.O_RDWR | os.O_NOFOLLOW
        self.created = False
        try:
            fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            self.created = True
        except FileExistsError:
            fd = os.open(self.path, flags)
        try:
            metadata = os.fstat(fd)
            if (not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1):
                raise Refused("ledger_file_must_be_owned_mode_0600")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("ledger_already_in_use") from None
            self.file = os.fdopen(fd, "r+", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        try:
            if self.created:
                # Persist the directory entry before any request can send.
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            self.data = {} if self.created else json.load(self.file)
            if not isinstance(self.data, dict):
                raise Refused("invalid_ledger_never_resend")
        except BaseException:
            self.file.close()
            raise

    def save(self) -> None:
        self.file.seek(0)
        self.file.write(json.dumps(self.data, sort_keys=True, ensure_ascii=True) + "\n")
        self.file.truncate()
        self.file.flush()
        os.fsync(self.file.fileno())

    def __exit__(self, *args):
        self.file.close()


async def private_bridge(path, method="GET", payload=None, query=None):
    # _bridge validates the .railway.internal:3000 destination and authenticates
    # with the existing key. There is deliberately no public URL/token CLI option.
    from app.qr_api import _bridge
    return await _bridge(path, method, payload, query)


def matching_rows(snapshot: dict, phone: str, body_hash: str) -> list[dict]:
    return [row for row in snapshot.get("items", []) if isinstance(row, dict)
            and row.get("phone") == phone and row.get("direction") == "out"
            and isinstance(row.get("body"), str) and digest(row["body"]) == body_hash]


def observe(ledger, rows: list[dict]) -> None:
    data = ledger.data
    if len(rows) > 1:
        data["observation"] = "multiple_matching_messages_never_resend"
        ledger.save()
        raise Refused("multiple_matching_messages_never_resend")
    if rows:
        row = rows[0]
        message_id = row.get("id")
        if not isinstance(message_id, str) or not re.fullmatch(r"out:[A-Za-z0-9._:-]{1,160}", message_id):
            raise Refused("invalid_observed_message_id")
        if data.get("message_id") and data["message_id"] != message_id:
            raise Refused("observed_message_id_mismatch")
        data["message_id"] = message_id
        state = row.get("delivery")
        if state in DELIVERY and state != data.get("delivery"):
            now = utc_now()
            data["delivery"] = state
            data.setdefault("receipts", []).append({"state": state, "observed_at": now})
            if state in CONFIRMED:
                data.setdefault("first_delivery_observed_at", now)
                started = data.get("send_attempted_epoch")
                if isinstance(started, (int, float)):
                    data.setdefault("delivery_observed_after_seconds", round(max(0, time.time() - started), 3))
        data["observation"] = "matched_exact_message"
    else:
        data["observation"] = "message_not_in_recent_history_never_resend"
    data["last_observed_at"] = utc_now()
    ledger.save()


async def run(args, *, env=None, bridge=None, ledger_factory=None, sleep=asyncio.sleep) -> dict:
    env = dict(os.environ) if env is None else env
    phone = authorized_phone(args.phone, env)
    if not MARKER.fullmatch(args.marker):
        raise Refused("invalid_unique_marker")
    body = args.marker + ("\n" + args.text.strip() if args.text else "")
    if len(body) > 1200:
        raise Refused("test_message_too_long")
    if args.scenario not in {"recipient", "self"}:
        raise Refused("invalid_scenario")
    bridge = bridge or private_bridge
    ledger_factory = ledger_factory or Ledger
    with ledger_factory(Path(args.ledger_dir), args.marker) as ledger:
        data = ledger.data
        if ledger.created:
            data.update(schema=1, marker=args.marker, recipient_sha256=digest(phone),
                        body_sha256=digest(body), scenario=args.scenario, created_at=utc_now(),
                        send_state="reserved", delivery="unobserved", handset_readability="unverified",
                        retry_evidence="per_message_retry_result_not_exposed_by_current_endpoint")
            ledger.save()
            if args.poll_only:
                data["send_state"] = "poll_only_without_prior_send"
                ledger.save()
                raise Refused("poll_only_requires_existing_ledger")
        elif (data.get("schema") != 1 or data.get("marker") != args.marker
              or data.get("recipient_sha256") != digest(phone) or data.get("scenario") != args.scenario
              or not isinstance(data.get("body_sha256"), str)):
            raise Refused("ledger_identity_mismatch_never_resend")
        elif args.text is not None and data["body_sha256"] != digest(body):
            raise Refused("marker_already_reserved_for_different_body")

        if ledger.created:
            try:
                status = await bridge("/internal/status")
                if status.get("status") != "connected":
                    raise Refused("whatsapp_not_connected")
                own_phone = status.get("phone")
                if not isinstance(own_phone, str) or not PHONE.fullmatch(own_phone):
                    raise Refused("linked_account_identity_unverified")
                is_self = phone == own_phone
                if is_self != (args.scenario == "self"):
                    raise Refused("self_and_recipient_scenarios_must_be_distinct")
                recipient = await bridge("/internal/recipient", query={"phone": phone})
                if (recipient.get("registered") is not True or recipient.get("identity") != "pn_confirmed"
                        or recipient.get("self") is not is_self):
                    raise Refused("recipient_identity_unverified")
                data["identity_verified_at"] = utc_now()
                data["self"] = is_self
                data["recipient_retry_requests_before"] = recipient.get("delivery", {}).get("retryRequests")
                # Also refuse marker reuse found in the recent inbox after ledger loss.
                snapshot = await bridge("/internal/messages", query={"phone": phone})
                existing = matching_rows(snapshot, phone, data["body_sha256"])
                collision = any(isinstance(row, dict) and row.get("direction") == "out"
                                and isinstance(row.get("body"), str)
                                and row["body"].split("\n", 1)[0] == args.marker
                                for row in snapshot.get("items", []))
                if existing:
                    data["send_state"] = "existing_message_observation_only"
                    observe(ledger, existing)
                elif collision:
                    raise Refused("marker_already_present_with_different_body")
                else:
                    # Record the attempt BEFORE POST. Even a crash/time-out must
                    # leave a permanent no-resend guard for this marker.
                    data.update(send_state="attempting", send_attempted_at=utc_now(),
                                send_attempted_epoch=time.time())
                    ledger.save()
                    try:
                        result = await bridge("/internal/send", "POST", {"phone": phone, "text": body})
                    except Exception:
                        data["send_state"] = "ambiguous_never_resend"
                    else:
                        message_id = result.get("id")
                        if result.get("accepted") is True and isinstance(message_id, str) and re.fullmatch(
                                r"out:[A-Za-z0-9._:-]{1,160}", message_id):
                            data.update(send_state="accepted_unverified", message_id=message_id)
                        else:
                            data["send_state"] = "ambiguous_never_resend"
                    ledger.save()
            except Exception:
                if data["send_state"] == "reserved":
                    data["send_state"] = "preflight_blocked_never_resend"
                    ledger.save()
                raise

        # Existing markers reach only GET calls, even after a crash during POST.
        deadline = time.monotonic() + args.observe_seconds
        while True:
            try:
                snapshot = await bridge("/internal/messages", query={"phone": phone})
            except Exception:
                data["observation"] = "query_failed_never_resend"
                ledger.save()
            else:
                observe(ledger, matching_rows(snapshot, phone, data["body_sha256"]))
            if data.get("delivery") in CONFIRMED | {"failed"} or time.monotonic() >= deadline:
                break
            await sleep(min(args.interval_seconds, max(0, deadline - time.monotonic())))
        return {key: data.get(key) for key in ("marker", "send_state", "message_id", "delivery",
                                               "observation", "handset_readability",
                                               "delivery_observed_after_seconds", "retry_evidence")}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phone", required=True, help="Exact configured singleton pilot; never inferred")
    parser.add_argument("--marker", required=True, help="Unique ORION-... marker, reused only for observation")
    parser.add_argument("--ledger-dir", required=True, help="Absolute directory on protected PERSISTENT storage")
    parser.add_argument("--text", default=None, help="Optional synthetic text appended after the marker")
    parser.add_argument("--scenario", choices=("recipient", "self"), default="recipient")
    parser.add_argument("--poll-only", action="store_true")
    parser.add_argument("--observe-seconds", type=float, default=45)
    parser.add_argument("--interval-seconds", type=float, default=3)
    args = parser.parse_args(argv)
    if not 0 <= args.observe_seconds <= 600 or not 1 <= args.interval_seconds <= 30:
        parser.error("observation must be 0..600 seconds and interval 1..30 seconds")
    return args


def main() -> int:
    try:
        result = asyncio.run(run(parse_args()))
    except Refused as exc:
        print(json.dumps({"result": "refused", "reason": str(exc)}))
        return 2
    except Exception:
        # Request/JSON/filesystem exceptions can contain identifiers or secrets.
        print(json.dumps({"result": "failed_or_ambiguous", "action": "inspect_ledger_never_resend"}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0 if result["delivery"] in CONFIRMED else 3


if __name__ == "__main__":
    raise SystemExit(main())
