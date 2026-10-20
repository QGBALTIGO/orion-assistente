"""Composed turn tests, not ASR, provider, PostgreSQL or load qualification.

The state machine, deterministic understanding, permission checks and presenters
are real. The existing flow fixture supplies an in-memory Redis and factual
catalog/agenda doubles. Each generated node calls qr_ai.turn(preview=False).
"""
from copy import deepcopy
from datetime import datetime
from itertools import product
import json
from uuid import uuid4

import pytest
import httpx

from app import qr_ai, qr_cancellation
from app.qr_understanding import UnderstandingPlan
from test_qr_multi_flow import flow  # noqa: F401 - shared factual test boundary


CONTEXTS = ("new", "cut_draft", "beard_draft", "combo_draft", "declined_summary")
CATALOGS = ("open", "catalog_only", "no_slots", "split_eligibility", "literal_service")
STORIES = (
    "price_cut", "price_beard", "price_combo", "negated_booking_price",
    "literal_corrected_price", "book_cut", "book_combo", "correct_clock",
    "correct_service", "add_service", "inquiry", "human", "provider_failure",
    "ambiguous_service", "ambiguous_professional", "decline_then_sm",
    "sm_without_summary", "expired_summary", "cancel_then_decline", "fresh_summary_sm",
)
CASES = tuple(product(STORIES, CONTEXTS, CATALOGS))


@pytest.fixture(autouse=True)
def no_external_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    async def deny_async(*args, **kwargs):
        raise AssertionError("Generated qualification must not call an external HTTP provider")
    def deny_sync(*args, **kwargs):
        raise AssertionError("Generated qualification must not call an external HTTP provider")
    monkeypatch.setattr(httpx.AsyncClient, "send", deny_async)
    monkeypatch.setattr(httpx.Client, "send", deny_sync)


async def identified(h, body, identity):
    # This explicit provider proposal is a test input, not proof of NLP quality.
    # The real planner must validate it against the current catalog and scope.
    h.proposal["value"] = (UnderstandingPlan(intents=["availability"], query_service_ids=[h.cut],
        requested_date=h.tomorrow.isoformat(), professional_id=h.bruno)
        if body == "Quais horários para corte amanhã com Bruno? É só consulta." else None)
    token = qr_ai._message_identity.set(identity)
    try:
        return await h.turn(body, preview=False)
    finally:
        qr_ai._message_identity.reset(token)


def factual_environment(h, monkeypatch, catalog, *, owned=True):
    """Five operationally different fixtures; no renamed copies of one catalog."""
    if catalog == "catalog_only":
        h.business["profile"]["capabilities"] = ["catalog_answers"]
    extra = uuid4()
    if catalog == "literal_service":
        h.catalog.append({"id": extra, "name": "Corte de Barba", "price_cents": 2700, "duration_min": 30})
    eligibility = {h.bruno: {row["id"] for row in h.catalog}, h.breno: {row["id"] for row in h.catalog}}
    if catalog == "split_eligibility":
        eligibility = {h.bruno: {h.cut}, h.breno: {h.beard}}
    async def professionals(tenant, ids):
        assert tenant == h.tenant
        return [p for p in h.professionals if set(ids) <= eligibility[p["id"]]]
    async def slots(db, tenant, pro, ids, day, **kwargs):
        assert tenant == h.tenant and pro in eligibility
        h.calls["reads"].append((pro, list(ids), day))
        rows = [r for r in h.catalog if r["id"] in ids]
        available = catalog != "no_slots" and set(ids) <= eligibility[pro]
        return {"slots": [datetime.combine(day, datetime.min.time(), h.zone).replace(hour=hour, minute=minute).isoformat()
                          for hour in range(8, 19) for minute in (0, 30)] if available else [],
                "price_cents": sum(r["price_cents"] for r in rows), "duration_min": sum(r["duration_min"] for r in rows)}
    holds = {}
    async def book(db, tenant, pro, ids, phone, name, when, idem, **kwargs):
        assert tenant == h.tenant and pro in eligibility and set(ids) <= eligibility[pro]
        assert catalog not in {"catalog_only", "no_slots"}
        assert phone in {"5567999999999", "+5567999999999"}
        result = h.hold_result(pro, ids, when)
        holds[str(result["id"])] = result
        h.calls["writes"].append(("hold", list(ids), when, pro))
        return result
    async def confirm(db, tenant, hold, **scope):
        assert tenant == h.tenant and scope == {"customer_phone": "+5567999999999", "expected_snapshot": "a" * 64, "source": "whatsapp_qr"}
        result = holds[str(hold)]
        h.calls["writes"].append(("confirm", hold))
        return {**result, "status": "confirmed", "protocol": "SYNTHETIC"}
    current = {"id": h.appointment, "customer_id": uuid4(), "professional_id": h.bruno, "status": "confirmed",
               "start_at": datetime.combine(h.tomorrow, datetime.min.time(), h.zone).replace(hour=14),
               "end_at": datetime.combine(h.tomorrow, datetime.min.time(), h.zone).replace(hour=14, minute=40),
               "total_cents": 3500, "protocol": "AG-SYNTHETIC", "updated_at": datetime.now(h.zone),
               "timezone": str(h.zone), "professional": "Bruno", "expected_snapshot": "a" * 64,
               "items": [{"service_id": h.cut, "name_snapshot": "Corte", "price_snapshot_cents": 3500, "duration_snapshot_min": 40}]}
    async def records(tenant, phone, **kwargs):
        assert tenant == h.tenant and phone == "5567999999999"
        return [deepcopy(current)] if owned else []
    async def owned_booking(tenant, phone, *, item_id):
        assert tenant == h.tenant and phone == "+5567999999999" and item_id == h.appointment
        return deepcopy(current) if owned else None
    async def no_cancel(*args, **kwargs):
        raise AssertionError("A declined or absent cancellation must not call the mutation backend")
    monkeypatch.setattr(qr_ai, "professionals_for", professionals)
    monkeypatch.setattr(qr_ai, "list_slots", slots)
    monkeypatch.setattr(qr_ai, "book_appointment", book)
    monkeypatch.setattr(qr_ai, "confirm_hold", confirm)
    monkeypatch.setattr(qr_ai, "customer_appointments", records)
    monkeypatch.setattr(qr_cancellation, "customer_appointments", records)
    monkeypatch.setattr(qr_cancellation, "owned_booking", owned_booking)
    monkeypatch.setattr(qr_cancellation, "cancel_appointment", no_cancel)
    return eligibility


async def initial_context(h, context):
    if context == "new":
        return
    if context == "declined_summary":
        h.seed("confirm", hold=str(uuid4()), price=3500, duration=40,
               confirmation_summary="Corte com Bruno", confirmation_expires_at=datetime.now().timestamp() + 200)
        assert (await identified(h, "Não", "generated-old-decline"))["step"] == "request_closed"
    else:
        selected = {"cut_draft": [h.cut], "beard_draft": [h.beard], "combo_draft": [h.cut, h.beard]}[context]
        h.seed("date", services=list(map(str, selected)), day=None, professional=None, professional_name=None)
    assert not h.calls["writes"]


def invariant(h, result):
    assert result["step"]
    assert result.get("answer") is None or len(result["answer"]) <= 1200
    assert len(h.state().get("history", [])) <= 8
    assert set(h.state().get("services", [])) <= {str(r["id"]) for r in h.catalog}
    assert not h.calls["names"]


@pytest.mark.asyncio
@pytest.mark.parametrize("story,context,catalog", CASES, ids=["-".join(c) for c in CASES])
async def test_composed_conversation(flow, monkeypatch, story, context, catalog):
    h = flow
    factual_environment(h, monkeypatch, catalog)
    await initial_context(h, context)
    before = h.state()
    sequence = []
    async def say(body):
        sequence.append(body)
        result = await identified(h, body, "generated-" + str(len(sequence)))
        invariant(h, result)
        return result
    prices = {"price_cut": ("Quanto custa só o corte?", 3500),
              "price_beard": ("Quanto custa só um corte de barba?", 2700 if catalog == "literal_service" else 2000),
              "price_combo": ("Quanto custa cabelo e barba?", 5500),
              "negated_booking_price": ("Não quero agendar. Quanto custa só a barba?", 2000),
              "literal_corrected_price": ("Quanto custa cabelo e barba? Não, quer dizer, só o preço da barba, não quero agendar.", 2000)}
    if story in prices:
        phrase, cents = prices[story]
        reply = await say(phrase)
        assert qr_ai.format_money(cents) in reply["answer"]
        assert h.state().get("services", []) == before.get("services", [])
        assert not h.calls["writes"] and not h.calls["reads"]
    elif story in {"book_cut", "book_combo", "correct_clock", "correct_service", "add_service", "fresh_summary_sm", "expired_summary", "decline_then_sm"}:
        messages = {
            "book_cut": "Quero agendar corte amanhã às 15:00 com Bruno",
            "book_combo": "Quero agendar corte e barba amanhã às 15:00 com Bruno",
            "correct_clock": "Quero corte amanhã às duas, não, melhor às três, com Bruno",
            "correct_service": "Quero corte e barba amanhã às 15:00 com Bruno, não, deixa só o corte",
            "add_service": "Quero corte amanhã às 15:00 com Bruno e inclui a barba também",
        }
        ids = [h.cut, h.beard] if story in {"book_combo", "add_service"} else [h.cut]
        reply = await say(messages.get(story, messages["book_cut"]))
        may_hold = catalog not in {"catalog_only", "no_slots"} and not (catalog == "split_eligibility" and len(ids) == 2)
        if may_hold:
            assert reply["step"] == "confirm"
            assert [write[0] for write in h.calls["writes"]] == ["hold"]
            assert set(h.calls["writes"][0][1]) == set(ids)
            assert h.calls["writes"][0][2].hour == 15
            assert qr_ai.format_money(5500 if len(ids) == 2 else 3500) in reply["answer"]
        else:
            assert reply["step"] != "confirm" and not h.calls["writes"]
        if story == "fresh_summary_sm":
            done = await say("Sm")
            assert (done["step"] == "confirmed") is may_hold
            assert sum(w[0] == "confirm" for w in h.calls["writes"]) == int(may_hold)
        elif story in {"expired_summary", "decline_then_sm"}:
            if may_hold and story == "expired_summary":
                state = h.state(); state["confirmation_expires_at"] = datetime.now().timestamp() - 1
                h.redis.data[h.key] = json.dumps(state)
            elif story == "decline_then_sm":
                await say("Não")
            result = await say("Sm")
            assert result["step"] != "confirmed" and not any(w[0] == "confirm" for w in h.calls["writes"])
    elif story == "inquiry":
        reply = await say("Quais horários para corte amanhã com Bruno? É só consulta.")
        assert not h.calls["writes"] and not h.state().get("hold")
        if catalog == "catalog_only":
            assert reply["step"] == "capability_unavailable" and not h.calls["reads"]
        else:
            assert h.calls["reads"], (reply, h.state(), h.calls["plans"])
    elif story == "human":
        assert (await say("Quero agendar mas preciso falar com uma pessoa de verdade"))["step"] == "human"
        assert (await say("Sm"))["answer"] is None
        assert not h.calls["writes"] and not h.calls["reads"]
    elif story == "provider_failure":
        h.proposal["error"] = True
        reply = await say("Quero barba, não, melhor corte amanhã às 15:00")
        assert reply.get("degraded")
        assert not h.calls["writes"] and not h.calls["reads"]
        assert h.state().get("services", []) == before.get("services", [])
    elif story.startswith("ambiguous_"):
        phrase = "Quero barba ou corte amanhã às 15:00" if story == "ambiguous_service" else "Quero corte amanhã às 15:00 com Bruno ou Breno"
        reply = await say(phrase)
        assert reply["step"] != "confirm" and not h.calls["writes"] and not h.calls["reads"]
    elif story == "sm_without_summary":
        reply = await say("Sm")
        assert reply["step"] != "confirmed" and not h.calls["writes"]
    elif story == "cancel_then_decline":
        reply = await say("Quero cancelar minha reserva")
        assert reply["step"] == ("capability_unavailable" if catalog == "catalog_only" else "cancel_confirm")
        await say("Não")
        reply = await say("Sm")
        assert reply["step"] not in {"cancelled", "confirmed"} and not h.calls["writes"]
    else:
        raise AssertionError(story)


EVENTS = (
    ("price_cut", "Quanto custa só o corte?"),
    ("price_beard", "Quanto custa só a barba?"),
    ("choose_cut", "Quero só corte"),
    ("add_beard", "Inclui a barba também"),
    ("remove_cut", "Tira o corte"),
    ("inquiry", "Quais horários para corte amanhã com Bruno? É só consulta."),
    ("human", "Quero falar com uma pessoa de verdade"),
    ("decline", "Não"),
    ("affirm", "Sm"),
    ("cancel", "Cancela minha reserva"),
)


@pytest.mark.asyncio
async def test_ten_thousand_four_event_sequences_cannot_invent_write_authority(flow, monkeypatch):
    """A prefix tree executes all 10**4 paths, not ten thousand duplicate loops.

    No event supplies an operational clock, and this customer owns no bookings.
    The model therefore permits reads/drafts/handoff but zero domain writes.
    Every unique prefix runs the real turn once (11,110 turns). The Redis state
    produced by that prefix is restored for each distinct next-event branch.
    """
    h = flow
    factual_environment(h, monkeypatch, "open", owned=False)
    foreign = "orion:qr:ai:other-tenant:other-contact"
    h.redis.data[foreign] = '{"step":"confirm","private":"foreign-sentinel"}'
    leaves, nodes, steps, grid_reads = set(), 0, set(), 0
    async def visit(prefix):
        nonlocal nodes, grid_reads
        if len(prefix) == 4:
            leaves.add(prefix)
            return
        data, ttls = deepcopy(h.redis.data), deepcopy(h.redis.ttls)
        for event, body in EVENTS:
            h.redis.data, h.redis.ttls = deepcopy(data), deepcopy(ttls)
            for values in h.calls.values():
                values.clear()
            result = await identified(h, body, "generated-seq-" + "-".join((*prefix, event)))
            nodes += 1; steps.add(result["step"])
            grid_reads += len(h.calls["reads"])
            invariant(h, result)
            assert not h.calls["writes"] and not h.state().get("hold"), (*prefix, event, result["step"])
            assert not result.get("_verified_operation"), (*prefix, event)
            assert h.redis.data[foreign] == data[foreign]
            await visit((*prefix, event))
    await visit(())
    assert len(leaves) == 10_000 and nodes == 11_110
    assert {"human", "date", "no_history", "request_closed"} <= steps, sorted(steps)
    assert grid_reads > 0, "Availability paths must actually consult the factual grid"
    print(json.dumps({"generated_event_sequences": len(leaves), "executed_turn_nodes": nodes,
        "steps_observed": sorted(steps), "factual_grid_reads": grid_reads,
        "real_external_providers": False, "real_database": False}))

