"""Public producer/consumer contract and one-shot membership admission boundaries.

Database rows and WhatsApp responses are synthetic. The endpoint, device DTO,
preview assembler, encryption and receipt classifier are the actual producers.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Response
import httpx
from pydantic import ValidationError
import pytest

from app.catalog_api import Actor, authenticate
from app import subscription_group_add as api
from app import subscription_group_add_schemas as schemas
from app import subscription_group_add_store as store
from app import subscription_group_add_worker as worker

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "fixtures/subscription_group_add_contract.json").read_text())
NOW = datetime(2026, 11, 5, 20, tzinfo=timezone.utc)
TENANT, OWNER, CHANNEL, GENERATION = (UUID(f"00000000-0000-4000-8000-{n:012d}") for n in (1, 2, 3, 4))
DEVICE = UUID(CONTRACT["ids"]["device_id"])
ACTOR = Actor("member", account_id=OWNER)
ROW = {"id": DEVICE, "tenant_id": TENANT, "owner_account_id": OWNER, "channel_id": CHANNEL,
       "generation": GENERATION, "revision": 2, "status": "connected", "name": "Dispositivo sintético"}
PHONES = [member["phone"] for member in CONTRACT["preview_contacts"]["members"]]
DEST, NONADMIN, SOURCE = (group["id"] for group in CONTRACT["groups"]["items"])


def app():
    value = FastAPI()
    for route in api.router.routes:
        value.add_api_route(route.path, route.endpoint, methods=route.methods, status_code=route.status_code)
    value.dependency_overrides[authenticate] = lambda: ACTOR
    return value


def url(suffix):
    return api.BASE.format(tenant_id=TENANT) + suffix


def request(**changes):
    return deepcopy(CONTRACT["preview_request_contacts"]) | changes


def create(**changes):
    return deepcopy(CONTRACT["create_request"]) | changes


class Result:
    def __init__(self, rows=(), scalar=None): self.rows, self.scalar = rows, scalar
    def mappings(self): return self
    def scalars(self): return self
    def all(self): return self.rows
    def scalar_one(self): return self.scalar
    def one(self): return self.rows[0]


def install_producer(monkeypatch, *, status="connected", after=None):
    state = SimpleNamespace(row=ROW | {"status": status}, calls=[], boundaries=[], reads=0,
                            local_reads=0, unresolved=set(), failure=None)

    class Database:
        async def execute(self, statement, values=None):
            sql = str(statement)
            if "SELECT timezone FROM tenants" in sql:
                return Result(scalar="America/Sao_Paulo")
            if "FROM subscription_devices" in sql:
                return Result([state.row])
            if "WITH all_sources" in sql:
                state.local_reads += 1
                return Result([{"phone": member["phone"][1:], "names": [member["name"]],
                    "origins": [{"kind": "customer", "id": str(UUID(int=index+1)),
                        "phone": member["phone"][1:], "version": 1}],
                    "permission_version": 1 if index == 3 else 0, "blocked": index == 3,
                    "source_count": 2 if index == 0 else 1}
                    for index, member in enumerate(CONTRACT["preview_contacts"]["members"])])
            if "FROM subscription_campaign_permissions" in sql:
                return Result([{"phone": PHONES[3][1:], "version": 1, "revoked": True}])
            if "SELECT i.phone FROM subscription_group_add_items" in sql:
                return Result(list(state.unresolved))
            raise AssertionError(sql)

    @asynccontextmanager
    async def owner(tenant, actor):
        assert (tenant, actor) == (TENANT, ACTOR)
        state.boundaries.append("enter")
        yield Database()
        state.boundaries.append("exit")

    async def device(db, tenant, identity, *, actor):
        assert (tenant, identity, actor) == (TENANT, DEVICE, ACTOR)
        state.reads += 1
        if after is not None and state.reads > 1:
            if isinstance(after, Exception): raise after
            state.row.update(after)
        return deepcopy(state.row), {"id": CHANNEL, "enabled": True, "allowed_senders": []}

    async def bridge(transport, action, method, payload):
        assert method == "POST" and action in {"device/group-add/groups", "device/group-add/members"}
        state.calls.append((action, deepcopy(payload)))
        if state.failure: raise state.failure
        context = {key: payload[key] for key in ("device_id", "generation", "settings_revision")}
        if action.endswith("groups"):
            return {**context, "groups": [{key: value for key, value in group.items() if key != "blocked_reason"}
                for group in CONTRACT["groups"]["items"]]}
        group = next(group for group in CONTRACT["groups"]["items"] if group["id"] == payload["group_id"])
        source = group["id"] == SOURCE
        members = CONTRACT["preview_group"]["members"] if source else [CONTRACT["preview_contacts"]["members"][2]]
        return {**context, "group": {key: group[key] for key in ("id", "name", "member_count")},
            "members": [{"phone": member["phone"], "name": member["name"], "role": "member"} for member in members],
            "total": group["member_count"], "unavailable_count": 1 if source else group["member_count"]-1,
            "duplicate_count": 1 if source else 0, "is_admin": group["can_add"]}

    async def eligible(*args): return [{"id": DEVICE, "available": True}]
    async def local_groups(*args): return deepcopy(CONTRACT["options"]["groups"])
    async def clock(*args): return NOW
    monkeypatch.setattr(store, "owner_transaction", owner)
    monkeypatch.setattr(store.devices, "_device", device)
    monkeypatch.setattr(store.channels, "_bridge_control", bridge)
    monkeypatch.setattr(store.campaigns, "device_options", eligible)
    monkeypatch.setattr(store.campaigns, "group_summaries", local_groups)
    monkeypatch.setattr(store, "clock", clock)
    return state


@pytest.fixture
def cipher(monkeypatch):
    value = Fernet(Fernet.generate_key())
    monkeypatch.setattr(store.channels, "_cipher", lambda: value)
    return value


def sealed(*, phones=None):
    phones = phones or PHONES[:2]
    rows = [{"phone": phone, "blocked": False} for phone in phones]
    return store.create_preview(TENANT, ACTOR, ROW, schemas.PreviewRequest.model_validate(request()), rows, phones, NOW)[0]


@pytest.mark.parametrize("changes", [
    {"destination_group_id": "123@lid"}, {"destination_group_id": "x@g.us"},
    {"source": "group"}, {"source_group_id": SOURCE},
    {"contact_group_id": str(uuid4()), "without_group": True}, {"without_group": 1},
    {"source": "group", "source_group_id": DEST},
    {"source": "group", "source_group_id": SOURCE, "search": "x"},
    {"created_from": "2026-11-06", "created_to": "2026-11-05"},
    {"created_from": "not-a-date"}, {"search": "🙂" * 128}, {"search": "x\x00"},
    {"tenant_id": str(uuid4())}, {"is_admin": True},
])
def test_preview_rejects_ambiguous_sources_and_client_supplied_authority(changes):
    with pytest.raises(ValidationError): schemas.PreviewRequest.model_validate(request(**changes))


def test_other_group_can_be_loaded_before_destination_and_search_uses_utf16_limit():
    value = schemas.PreviewRequest.model_validate(request(source="group", source_group_id=SOURCE, destination_group_id=None))
    assert value.destination_group_id is None and value.source_group_id == SOURCE
    assert schemas.PreviewRequest.model_validate(request(search="🙂" * 127 + "a")).search.endswith("a")


@pytest.mark.parametrize("changes", [
    {"phones": []}, {"phones": PHONES[:1]*2}, {"phones": ["551199990001"]},
    {"phones": ["123456789@lid"]}, {"phones": ["+０１２３４５６７８"]},
    {"phones": ["+012345678"]}, {"phones": PHONES*1251},
    {"preview_token": "x"*360001}, {"interval_seconds": 1}, {"invite": True}, {"operation_id": str(uuid4())},
])
def test_admission_only_accepts_unique_selected_phones_and_a_bound_preview(changes):
    with pytest.raises(ValidationError): schemas.CreateBatch.model_validate(create(**changes))


def test_encrypted_preview_binds_purpose_owner_device_source_and_exact_lifetime(cipher):
    token = sealed()
    value = store.decode_preview(token, TENANT, ACTOR, NOW+timedelta(seconds=299))
    assert value.phones == sorted(PHONES[:2]) and value.expires_at-value.issued_at == 300
    assert value.request.destination_group_id == DEST
    assert PHONES[0] not in token and str(OWNER) not in token
    for field in ("id", "channel_id", "generation", "owner_account_id", "tenant_id"):
        with pytest.raises(HTTPException): store.validate_device(value, ROW | {field: uuid4()})
    with pytest.raises(HTTPException): store.validate_device(value, ROW | {"revision": 3})
    with pytest.raises(HTTPException) as error:
        store.decode_preview(token, TENANT, ACTOR, NOW+timedelta(seconds=300))
    assert error.value.status_code == 410
    maximum = [f"+5567{n:09d}" for n in range(5000)]
    large = sealed(phones=maximum)
    assert len(large) <= 360000 and len(store.decode_preview(large, TENANT, ACTOR, NOW).phones) == 5000


@pytest.mark.parametrize("change", ["tamper", "tenant", "actor", "purpose", "lifetime", "future", "key"])
def test_sealed_preview_rejects_foreign_or_modified_context(cipher, monkeypatch, change):
    token, tenant, actor = sealed(), TENANT, ACTOR
    if change == "tamper": token = token[:-5] + "abcde"
    elif change == "tenant": tenant = uuid4()
    elif change == "actor": actor = Actor("member", account_id=uuid4())
    elif change == "key": monkeypatch.setattr(store.channels, "_cipher", lambda: Fernet(Fernet.generate_key()))
    else:
        value = json.loads(cipher.decrypt(token.encode()))
        if change == "purpose": value["purpose"] = "different.integration"
        if change == "lifetime": value["expires_at"] += 1
        if change == "future": value.update(issued_at=value["issued_at"]+1, expires_at=value["expires_at"]+1)
        token = cipher.encrypt(json.dumps(value).encode()).decode()
    with pytest.raises(HTTPException) as error: store.decode_preview(token, tenant, actor, NOW)
    assert error.value.status_code in {404, 410, 422}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["connected", "disconnected"])
async def test_options_shared_with_dom_are_generated_by_the_actual_endpoint(monkeypatch, status):
    state = install_producer(monkeypatch, status=status)
    value = await api.options(TENANT, Response(), ACTOR)
    assert value == CONTRACT["options" if status == "connected" else "options_disconnected"]
    assert state.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["preview_contacts", "preview_group", "preview_no_destination", "preview_no_admin"])
async def test_all_shared_previews_match_real_endpoint_and_projection(monkeypatch, key):
    state = install_producer(monkeypatch)
    wanted = CONTRACT[key]
    payload = request(source=wanted["source"], **wanted["filters"],
                      destination_group_id=wanted["destination"]["id"] if wanted["destination"] else None)
    # Only Fernet randomness is replaced for a byte-for-byte public fixture.
    # Encryption/scope/TTL are independently exercised above.
    def deterministic(*args): return CONTRACT["preview_contacts"]["preview_token"], NOW+timedelta(minutes=5)
    monkeypatch.setattr(store, "create_preview", deterministic)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        result = await client.post(url("/preview"), json=payload)
    assert result.status_code == 200, result.text
    assert result.json() == wanted and result.headers["cache-control"] == "no-store"
    assert len(state.calls) == (2 if key == "preview_group" else 0 if key == "preview_no_destination" else 1)
    assert state.local_reads == (0 if key == "preview_group" else 2)


@pytest.mark.asyncio
async def test_groups_projection_uses_real_admin_capability_without_exposing_provider_context(monkeypatch):
    install_producer(monkeypatch)
    assert await api.groups(TENANT, DEVICE, Response(), ACTOR) == CONTRACT["groups"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["disconnected", "connecting", "qr", "pairing", "error", "disabled", "removing", "removed"])
async def test_offline_read_stops_before_any_provider_consultation(monkeypatch, status):
    state = install_producer(monkeypatch, status=status)
    with pytest.raises(HTTPException) as error:
        await api.prepare_preview(TENANT, ACTOR, schemas.PreviewRequest.model_validate(request()))
    assert error.value.detail["code"] == "GROUP_ADD_DEVICE_UNAVAILABLE" and state.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [{"status": "disconnected"}, {"revision": 3}, HTTPException(403, "revoked")])
async def test_successful_provider_read_is_discarded_when_context_changes_in_flight(monkeypatch, after):
    state = install_producer(monkeypatch, after=after)
    with pytest.raises(HTTPException):
        await api.prepare_preview(TENANT, ACTOR, schemas.PreviewRequest.model_validate(request()))
    assert len(state.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["/preview", "/batches"])
async def test_owner_check_precedes_body_parsing_and_stream_limit(monkeypatch, suffix):
    @asynccontextmanager
    async def denied(*args):
        raise HTTPException(403, "owner required")
        yield
    monkeypatch.setattr(store, "owner_transaction", denied)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        result = await client.post(url(suffix), content=b"x"*(schemas.MAX_BODY+1))
    assert result.status_code == 403


@pytest.mark.asyncio
async def test_authorized_body_is_still_bounded_and_invalid_source_never_calls_provider(monkeypatch):
    state = install_producer(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        result = await client.post(url("/preview"), content=b"x"*(schemas.MAX_BODY+1))
        invalid = await client.post(url("/preview"), json=request(source="group", source_group_id=DEST))
    assert result.status_code == 413 and invalid.status_code == 422 and state.calls == []


@pytest.mark.asyncio
async def test_unknown_phone_is_blocked_but_current_presence_is_not_claimed_as_an_add(monkeypatch, cipher):
    state = install_producer(monkeypatch)
    state.unresolved = {PHONES[0], PHONES[2]}
    value, *_ = await api.prepare_preview(TENANT, ACTOR, schemas.PreviewRequest.model_validate(request()))
    assert value["members"][0]["status"] == "blocked"
    assert value["members"][2]["status"] == "already_member"
    assert store.decode_preview(value["preview_token"], TENANT, ACTOR, NOW).phones == [PHONES[1]]


@pytest.mark.asyncio
async def test_replay_is_resolved_before_preview_expiry_or_any_new_provider_read(monkeypatch):
    state = install_producer(monkeypatch)
    async def prior(*args): return {"id": UUID(CONTRACT["ids"]["batch_id"])}
    async def response(*args, **kwargs): return deepcopy(CONTRACT["batch_recovered"])
    def deny(*args): raise AssertionError("Replay tried to decode old preview")
    monkeypatch.setattr(store, "existing", prior)
    monkeypatch.setattr(store, "response", response)
    monkeypatch.setattr(store, "decode_preview", deny)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app()), base_url="https://test") as client:
        result = await client.post(url("/batches"), json=create())
    assert result.status_code == 200 and result.json() == CONTRACT["batch_recovered"] and state.calls == []


def private_payload():
    return {**store.contacts.transport_scope(ROW), "operation_id": str(uuid4()), "claim_token": str(uuid4()),
            "group_id": DEST, "phone": PHONES[0]}


def receipt(payload, **changes):
    return {key: value for key, value in payload.items() if key != "claim_token"} | {
        "status": "added", "reason": None, "settled": True} | changes


@pytest.mark.parametrize("changes", [{"phone": PHONES[1]}, {"operation_id": str(uuid4())},
    {"settings_revision": 3}, {"settled": False}, {"status": "not_added", "reason": None},
    {"status": "uncertain", "reason": "private-secret"}, {"is_admin": True}])
def test_receipt_never_accepts_foreign_or_unsettled_success(changes):
    payload = private_payload()
    with pytest.raises((ValueError, ValidationError)): worker.outcome(receipt(payload, **changes), payload)


@pytest.mark.parametrize("state,reason,settled,expected", [
    ("added", None, True, ("added", None, False, True)),
    ("already_member", None, True, ("already_member", None, False, True)),
    ("not_added", "provider_refused", True, ("not_added", "GROUP_ADD_PROVIDER_REFUSED", False, True)),
    ("not_attempted", "not_admin", True, ("not_added", "GROUP_ADD_ADMIN_REQUIRED", True, True)),
    ("not_attempted", "deadline_exceeded", False, ("unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False)),
    ("uncertain", "deadline_exceeded", False, ("unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False)),
    ("uncertain", "provider_result_unverified", True, ("unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, True)),
    ("unrecorded", None, False, ("unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False)),
])
def test_only_known_individual_refusal_may_continue_the_batch(state, reason, settled, expected):
    payload = private_payload()
    assert worker.outcome(receipt(payload, status=state, reason=reason, settled=settled), payload) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), HTTPException(503, "lost acknowledgement"), {"status": "added"}])
async def test_dispatch_failure_finishes_unknown_and_never_repeats_http(monkeypatch, failure):
    payload = private_payload()
    claimed = {"payload": payload, "transport": {"id": CHANNEL}}
    calls, finishes, claims = [], [], [claimed, None]
    async def claim(*args): return claims.pop(0)
    async def bridge(*args):
        calls.append(args)
        if isinstance(failure, Exception): raise failure
        return failure
    async def finish(*args): finishes.append(args)
    monkeypatch.setattr(worker, "claim", claim)
    monkeypatch.setattr(worker.store.channels, "_bridge_control", bridge)
    monkeypatch.setattr(worker, "finish", finish)
    assert await worker.process_subscription_group_add({}, TENANT, uuid4()) == "unknown"
    assert await worker.process_subscription_group_add({}, TENANT, uuid4()) == "not_claimed"
    assert len(calls) == len(finishes) == 1
    assert calls[0][1:] == ("device/group-add/add", "POST", payload)
    assert finishes[0][1:] == ("unknown", "GROUP_ADD_OUTCOME_UNKNOWN", True, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,expected", [
    ("deadline_exceeded", "GROUP_ADD_BRIDGE_TIMEOUT"),
    ("device_busy", "GROUP_ADD_READ_BUSY"),
    ("device_unavailable", "GROUP_ADD_DEVICE_UNAVAILABLE"),
    ("invalid_metadata", "GROUP_ADD_PROVIDER_DATA_UNAVAILABLE"),
    ("group_unavailable", "GROUP_ADD_BRIDGE_UNAVAILABLE"),
    ("PRIVATE-PHONE-SECRET", "GROUP_ADD_BRIDGE_UNAVAILABLE"),
])
async def test_group_add_read_maps_only_safe_private_diagnostic_codes(monkeypatch, reason, expected):
    state = install_producer(monkeypatch)
    state.failure = HTTPException(409, {"group_read_reason": reason,
                                        "not_exposed": "SENSITIVE-CONTACT-AND-SECRET"})
    with pytest.raises(HTTPException) as error:
        await api.groups(TENANT, DEVICE, Response(), ACTOR)
    assert error.value.detail["code"] == expected
    assert "SENSITIVE" not in json.dumps(error.value.detail)
    assert len(state.calls) == 1


@pytest.mark.asyncio
async def test_group_add_timeout_does_not_trigger_membership_mutation(monkeypatch):
    state = install_producer(monkeypatch)
    state.failure = TimeoutError()
    with pytest.raises(HTTPException) as error:
        await api.groups(TENANT, DEVICE, Response(), ACTOR)
    assert error.value.status_code == 504
    assert error.value.detail["code"] == "GROUP_ADD_BRIDGE_TIMEOUT"
    assert len(state.calls) == 1
    assert state.calls[0][0] == "device/group-add/groups"


def test_group_add_list_validates_omission_count_without_changing_admin_fields():
    groups = deepcopy(CONTRACT["groups"]["items"])
    value = schemas.BridgeGroups.model_validate({
        "device_id": str(DEVICE), "generation": str(GENERATION),
        "settings_revision": 2,
        "groups": [{key: val for key, val in group.items() if key != "blocked_reason"}
                   for group in groups],
        "unavailable_groups": 2
    })
    assert value.unavailable_groups == 2
    for count in (-1, 1001, True, "2"):
        with pytest.raises(ValidationError):
            schemas.BridgeGroups.model_validate({
                **value.model_dump(mode="json"), "unavailable_groups": count
            })


@pytest.mark.asyncio
async def test_api_reports_unavailable_group_metadata_without_allowing_new_members(monkeypatch):
    state = install_producer(monkeypatch)
    original = store.channels._bridge_control
    async def bridge(transport, action, method, payload):
        result = await original(transport, action, method, payload)
        return {**result, "unavailable_groups": 1}
    monkeypatch.setattr(store.channels, "_bridge_control", bridge)
    response = await api.groups(TENANT, DEVICE, Response(), ACTOR)
    assert response["unavailable_groups"] == 1
    assert response["total"] == len(response["items"])
    assert all(isinstance(group["can_add"], bool) for group in response["items"])
    assert all(action.endswith("/groups") for action, _ in state.calls)
