"""Signed Meta command flow: references read facts, summaries precede mutations."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app import whatsapp_commands as commands, whatsapp_dialogue as dialogue


class Redis:
    def __init__(self):
        self.values = {}
        self.expiry = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value
        self.expiry[key] = ex

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def flow(monkeypatch):
    tenant, channel, phone = uuid4(), uuid4(), "+5567999999901"
    day = (datetime.now(ZoneInfo("America/Cuiaba")) + timedelta(days=3)).replace(hour=14, minute=0, second=0, microsecond=0)
    records = []
    for index, (service, professional) in enumerate((("Corte de cabelo", "Ana Silva"), ("Barba", "Bruno"), ("Barba", "Ana Silva"))):
        records.append({"id": uuid4(), "protocol": ("AG-ABCDEF", "AG-GHJKLM", "AG-NPQRST")[index],
            "tenant_id": tenant, "phone": phone, "status": "confirmed", "start_at": day+timedelta(days=index),
            "timezone": "America/Cuiaba", "professional": professional, "professional_id": uuid4(),
            "services": [service], "total_cents": 2000, "expected_snapshot": "snapshot-"+str(index),
            "items": [{"service_id": uuid4(), "name_snapshot": service, "duration_snapshot_min": 20}]})
    events, queries = [], []
    redis = Redis()

    class Rows(list):
        def mappings(self): return self
        def all(self): return self

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def execute(self, statement, params):
            sql = str(statement)
            assert sql.lstrip().startswith("SELECT")
            assert "a.tenant_id=:tenant" in sql and "c.whatsapp_e164=:phone" in sql
            assert "a.status='confirmed'" in sql and "a.start_at>now()" in sql
            assert "i.tenant_id=a.tenant_id" in sql and "p.tenant_id=a.tenant_id" in sql
            queries.append((sql, params))
            return Rows(row.copy() for row in sorted(records, key=lambda row: row["start_at"])
                if row["tenant_id"] == params["tenant"] and row["phone"] == params["phone"]
                and row["status"] == "confirmed" and row["start_at"] > datetime.now(timezone.utc))

    @asynccontextmanager
    async def transaction(_db, actual_tenant):
        yield

    async def owned(actual_tenant, sender, protocol):
        return next((dict(row) for row in records if row["tenant_id"] == actual_tenant and row["phone"] == sender
                     and row["protocol"] == protocol and row["status"] == "confirmed"), None)

    async def cancel(_db, actual_tenant, item_id, *, customer_phone, expected_snapshot, future_only=False, source):
        row = next(row for row in records if row["id"] == item_id)
        assert row["tenant_id"] == actual_tenant and row["phone"] == customer_phone and future_only is True
        assert source == "whatsapp_meta"
        if expected_snapshot != row["expected_snapshot"] or row["start_at"] <= datetime.now(timezone.utc):
            raise HTTPException(409, "changed")
        events.append(("cancel", item_id))
        row["status"] = "cancelled"
        return dict(row)

    async def unauthorized(*args):
        events.append(("staff_auth", args))
        return None

    monkeypatch.setattr(dialogue, "get_session_factory", lambda: Session)
    monkeypatch.setattr(dialogue, "tenant_transaction", transaction)
    monkeypatch.setattr(commands, "get_session_factory", lambda: Session)
    monkeypatch.setattr(commands, "_owned_booking", owned)
    monkeypatch.setattr(commands, "cancel_appointment", cancel)
    monkeypatch.setattr(commands, "_authorized_staff", unauthorized)
    async def synthetic_accepted_summary(*args): return True
    monkeypatch.setattr(commands, "summary_was_accepted", synthetic_accepted_summary)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    async def turn(body, message_id="new", *, caller_tenant=None, sender=None):
        current = json.loads(await redis.get("signed-meta") or "{}")
        result = await commands.try_command(caller_tenant or tenant, channel, sender or phone,
            body, current, redis, "signed-meta", message_id=message_id)
        # The real wrapper records every intervening response for consent freshness.
        if result[0] and await redis.get("signed-meta"):
            current = json.loads(await redis.get("signed-meta"))
            current["last_message_id"] = message_id
            await redis.set("signed-meta", json.dumps(current), ex=300)
        return result

    return {"turn": turn, "records": records, "events": events, "queries": queries,
            "redis": redis, "tenant": tenant, "phone": phone}


@pytest.mark.asyncio
@pytest.mark.parametrize("body,index", [
    ("Quero cancelar o corte", 0), ("cancele a barba com Bruno", 1),
    ("Quero cancelar a barba com Ana", 2), ("Quero remarcar o corte de cabelo", 0),
])
async def test_natural_reference_prepares_only_owned_summary(flow, body, index):
    handled, answer = await flow["turn"](body, "request")
    state = json.loads(await flow["redis"].get("signed-meta"))
    assert handled and state["protocol"] == flow["records"][index]["protocol"]
    assert flow["events"] == [] and flow["queries"]
    if "remarcar" not in body:
        assert "selecionado para cancelamento" in answer and "R$ 20,00" in answer
        assert state["expected_snapshot"] == flow["records"][index]["expected_snapshot"]
        assert state["summary_message_id"] == "request"


@pytest.mark.asyncio
async def test_date_and_time_reference_uses_tenant_timezone(flow):
    target = flow["records"][1]
    handled, answer = await flow["turn"]("Quero cancelar o de " + target["start_at"].strftime("%d/%m/%Y às %H:%M"), "request")
    assert handled and "Bruno" in answer
    assert json.loads(await flow["redis"].get("signed-meta"))["protocol"] == target["protocol"]
    assert flow["events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["cancelar a barba", "cancelar o corte com Carlos", "cancelar corte ou barba",
    "não quero cancelar o corte", "cancelar o corte se não tiver multa", "quero cancelar a primeira"])
async def test_ambiguities_conditions_unknown_names_and_unshown_ordinals_do_not_propose(flow, body):
    handled, answer = await flow["turn"](body, "request")
    assert handled and "Qual dessas reservas" in answer and "selecionado para cancelamento" not in answer
    assert flow["events"] == []
    state = json.loads(await flow["redis"].get("signed-meta"))
    assert state["step"] == "customer_booking_pick" and "expected_snapshot" not in state


@pytest.mark.asyncio
async def test_listing_and_ordinal_use_the_actual_display_order_then_fresh_confirmation(flow):
    _, answer = await flow["turn"]("meus horários", "list")
    assert "pelo protocolo" not in answer and "Corte de cabelo" in answer and "Bruno" in answer
    assert flow["redis"].expiry["signed-meta"] == 300
    _, proposal = await flow["turn"]("cancelar a segunda", "selection")
    assert "Bruno" in proposal and flow["events"] == []
    assert json.loads(await flow["redis"].get("signed-meta"))["protocol"] == flow["records"][1]["protocol"]
    _, repeat = await flow["turn"]("pode cancelar", "selection")
    assert "nova resposta" in repeat and flow["events"] == []
    _, completed = await flow["turn"]("pode cancelar", "fresh-consent")
    assert "Cancelamento confirmado" in completed
    assert flow["events"] == [("cancel", flow["records"][1]["id"])]


@pytest.mark.asyncio
async def test_selection_without_action_asks_action_before_summary(flow):
    await flow["turn"]("meus horários", "list")
    _, question = await flow["turn"]("o de barba com Bruno", "choice")
    assert "cancelar ou remarcar" in question and flow["events"] == []
    _, proposal = await flow["turn"]("cancelar", "action")
    assert "Bruno" in proposal and "selecionado para cancelamento" in proposal
    assert json.loads(await flow["redis"].get("signed-meta"))["protocol"] == flow["records"][1]["protocol"]


@pytest.mark.asyncio
async def test_removed_displayed_candidate_never_renumbers_to_other_booking(flow):
    await flow["turn"]("meus horários", "list")
    flow["records"][0]["status"] = "cancelled"
    _, answer = await flow["turn"]("cancelar a primeira", "choice")
    assert "selecionado para cancelamento" not in answer and flow["events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["phone", "tenant"])
async def test_other_contact_or_company_cannot_resolve_existing_list(flow, scope):
    await flow["turn"]("meus horários", "list")
    kwargs = {"sender": "+5567999999902"} if scope == "phone" else {"caller_tenant": uuid4()}
    _, answer = await flow["turn"]("cancelar a primeira", "foreign", **kwargs)
    assert "Não há reservas" in answer and "AG-" not in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_protocol_remains_optional_and_missing_identity_cannot_prepare_operation(flow):
    _, denied = await flow["turn"]("cancelar o corte", None)
    assert "nova mensagem" in denied and flow["events"] == []
    _, answer = await flow["turn"]("cancelar AG-ABCDEF", "protocol")
    assert "selecionado para cancelamento" in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_changed_snapshot_and_intervening_question_require_new_review(flow):
    await flow["turn"]("cancelar o corte", "request")
    flow["records"][0]["expected_snapshot"] = "updated"
    _, answer = await flow["turn"]("pode cancelar", "consent")
    assert "não foi confirmada" in answer and flow["events"] == []
    await flow["turn"]("cancelar o corte", "request-2")
    await flow["turn"]("Tem certeza?", "question")
    _, answer = await flow["turn"]("pode cancelar", "consent-2")
    assert "selecionado para cancelamento" in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_candidate_listing_never_intercepts_staff_authorization(flow):
    await flow["turn"]("meus horários", "list")
    _, answer = await flow["turn"]("agenda hoje", "staff-command")
    assert "restrito" in answer and flow["events"][0][0] == "staff_auth"


@pytest.mark.asyncio
async def test_bounded_candidate_query_never_claims_uniqueness_from_truncated_results(flow):
    original = flow["records"][0]
    flow["records"].extend({**original, "id": uuid4()} for _ in range(49))
    _, answer = await flow["turn"]("cancelar o corte", "request")
    assert "muitas reservas" in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_protocol_only_can_answer_existing_selection_without_becoming_authority(flow):
    await flow["turn"]("cancelar a barba", "request")
    _, answer = await flow["turn"]("AG-GHJKLM", "selection")
    assert "Bruno" in answer and "selecionado para cancelamento" in answer and flow["events"] == []
    _, answer = await flow["turn"]("cancelar AG-INVALIDO", "foreign")
    assert "não encontrada" in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_selection_is_reread_before_summary_and_does_not_trust_old_candidate(flow, monkeypatch):
    async def gone(*args):
        return None
    monkeypatch.setattr(commands, "_owned_booking", gone)
    _, answer = await flow["turn"]("cancelar o corte", "request")
    assert "não está mais disponível" in answer and flow["events"] == []
    assert await flow["redis"].get("signed-meta") is None


def test_weekday_is_not_second_ordinal_without_explicit_ordinal_context(flow):
    records = flow["records"]
    now = datetime(2030, 1, 1, tzinfo=ZoneInfo("America/Cuiaba"))  # Tuesday.
    records[0]["start_at"] = datetime(2030, 1, 7, 14, tzinfo=now.tzinfo)  # Monday.
    records[1]["start_at"] = datetime(2030, 1, 8, 14, tzinfo=now.tzinfo)
    records[2]["start_at"] = datetime(2030, 1, 9, 14, tzinfo=now.tzinfo)
    shown = [str(row["id"]) for row in records]
    assert dialogue.booking_reference("cancelar segunda", records, presented_ids=shown, now=now) == ("selected", shown[0])
    assert dialogue.booking_reference("cancelar a segunda", records, presented_ids=shown, now=now) == ("selected", shown[1])


def test_all_reference_constraints_intersect_instead_of_ignoring_unknown_details(flow):
    records = flow["records"]
    records[1]["start_at"] = records[1]["start_at"].replace(hour=15)
    assert dialogue.booking_reference("a barba com Bruno às 14h", records)[0] != "selected"
    assert dialogue.booking_reference("a barba com Bruno às 15h", records) == ("selected", str(records[1]["id"]))
    assert dialogue.booking_reference("a barba com Bruno às três da tarde", records) == ("selected", str(records[1]["id"]))
    assert dialogue.booking_reference("o corte com Carlos", records)[0] != "selected"


@pytest.mark.asyncio
async def test_expired_list_cannot_reinterpret_a_bare_ordinal(flow):
    await flow["turn"]("meus horários", "list")
    await flow["redis"].delete("signed-meta")
    _, answer = await flow["turn"]("cancelar a primeira", "choice")
    assert "selecionado para cancelamento" not in answer and flow["events"] == []


@pytest.mark.asyncio
async def test_consent_that_names_another_booking_never_cancels_previous_summary(flow, monkeypatch):
    from app.qr_dialogue import DialogueDecision
    async def overconfident(*args, **kwargs):
        return DialogueDecision("affirm", "semantic")
    monkeypatch.setattr(commands, "consent", overconfident)
    await flow["turn"]("cancelar o corte", "request")
    _, answer = await flow["turn"]("confirmo o cancelamento da barba com Bruno", "different-target")
    assert "selecionado para cancelamento" in answer and "Bruno" in answer
    assert flow["events"] == []
    assert json.loads(await flow["redis"].get("signed-meta"))["summary_message_id"] == "different-target"
