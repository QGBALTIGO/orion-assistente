"""Customer cancellation proposals; a separate, fresh turn authorizes the write.

The caller supplies only the authenticated tenant/PN and trusted ingress message
ID. This module is deliberately not wired into QR yet. It never cancels a hold,
infers policy exceptions, or treats a proposal/preview as a completed operation.
"""
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from app import qr_intake_revision as revision, qr_presentation as presentation
from app.booking_service import cancel_appointment
from app.database import get_session_factory
from app.qr_customer import customer_appointments
from app.qr_dialogue import contextual_response, interpret_contextual_response, summary_review_requested
from app.qr_understanding import build_plan, normalized
from app.whatsapp_dialogue import owned_booking, summary as booking_summary

TTL = 300
STEPS = frozenset({"cancel_pick", "cancel_confirm"})
ID_PATTERN = r"[a-zA-Z0-9._:-]{1,160}"


def _now():
    return datetime.now(timezone.utc)


def _scope(tenant, phone):
    if not isinstance(tenant, UUID) or not isinstance(phone, str) or not re.fullmatch(r"55[1-9][0-9]{9,10}", phone):
        raise ValueError("Authenticated tenant and PN required")
    return hashlib.sha256(f"{tenant}:{phone}".encode()).hexdigest()


def _context(business):
    facts = {key: business.get(key) for key in ("name", "timezone", "profile_id", "profile_revision", "profile")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


def _policy_denial(business):
    profile = business.get("profile") or {}
    if business.get("profile_id") and "appointments" not in profile.get("capabilities", []):
        return {"answer": "Este atendimento não está habilitado para alterar agendamentos. A equipe pode ajudar com o cancelamento.", "step": "capability_unavailable"}
    # Free-form policy text is not an executable cutoff/fee/refund rule. An LLM
    # or a customer saying yes cannot grant an exception to such a policy.
    for row in profile.get("policies", []):
        if re.search(r"\b(?:cancel\w*|desmarca\w*)\b", normalized(str(row.get("topic", "")) + " " + str(row.get("description", "")))):
            return {"answer": "Há uma política de cancelamento cadastrada que precisa ser conferida pela equipe. Nenhuma reserva foi cancelada; posso deixar sua solicitação para atendimento humano.", "step": "cancel_policy_review"}
    return None


def _deadline(state):
    value = state.get("confirmation_expires_at")
    return value if type(value) in {float, int} and math.isfinite(value) else 0


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(ID_PATTERN, value) is not None


def declines_cancellation(body):
    """A whole withdrawal closes only the pending request, never a reservation."""
    text = normalized(body).strip(" .!,;")
    return bool(re.fullmatch(
        r"nao(?:\s+(?:obrigad[oa]|quero(?:\s+mais)?(?:\s+(?:cancelar|desmarcar))?|"
        r"cancel(?:a|e|ar)|desmarc(?:a|ar)|desmarque))?|n|negativo|"
        r"(?:(?:quero|prefiro|pode)\s+)?(?:manter|mantenha)"
        r"(?:\s+(?:(?:a|minha|essa|esta)\s+reserva|(?:o|meu|esse|este)\s+agendamento))?|"
        r"(?:desist[oi]|desistir)(?:\s+(?:do cancelamento|de (?:cancelar|desmarcar)))?|"
        r"(?:deixa|deixe)\s+como\s+esta|"
        r"(?:cancel(?:a|e|ar)|desmarc(?:a|ar)|desmarque)\s*(?:[,;]|\.{3}|…)\s*"
        r"nao\s*[,;]?\s*(?:deixa|deixe)\s+como\s+esta", text))


def _future(record):
    value = record.get("start_at")
    return isinstance(value, datetime) and value.tzinfo is not None and value > _now()


def _target_reference(body, names):
    """A new target/factual reference cannot authorize the old proposal."""
    text = normalized(body)
    if re.search(r"\b(?:primeir\w*|segund\w*|terceir\w*|ultim\w*|outr[oa]s?|amb[oa]s|tod[oa]s|duas|dois|"
                 r"profissional|barbeiro|servico|protocolo|horario|data)\b|\d", text):
        return True
    if re.search(r"\bcom\s+(?!(?:certeza|confianca|tranquilidade|seguranca)\b)\w", text):
        return True
    target = re.search(r"\b(?:cancelar|cancela|cancele|desmarcar|desmarca|desmarque)\s+(?:(?:o|a|os|as)\s+)?(.+)", text)
    if target and not re.fullmatch(r"(?:por favor|entao|sim|isso|(?:est[ea]|ess[ea]|minha?|meu)\s+(?:reserva|agendamento))[,!.\s]*", target.group(1)):
        return True
    if re.search(r"\b(?:cancelamento|desmarcacao)\s+(?:do|da|dos|das|de)\s+", text):
        return True
    return any(name and re.search(r"(?<!\w)"+re.escape(normalized(name))+r"(?!\w)", text) for name in names)


def _selection(body, records, tz_name):
    """Resolve only an unambiguous reference to the displayed owned candidates."""
    text = normalized(body).strip(" .!?")
    if re.search(r"\b(?:nao|nem|ou|se|caso|talvez|espera|pera)\b", text):
        return "ambiguous", None
    candidates = list(records)
    reference = False
    matches = [row for row in records if re.search(r"(?<!\w)" + re.escape(normalized(row["protocol"])) + r"(?!\w)", text)]
    if len(matches) > 1:
        return "ambiguous", None
    if matches:
        candidates = matches; reference = True
    elif re.search(r"\bag-[a-z0-9-]+\b", text):
        return "unavailable", None  # A foreign/unknown protocol is never a password.
    ordinals = {"primeiro": 0, "primeira": 0, "segundo": 1, "segunda opcao": 1,
                "terceiro": 2, "terceira": 2, "ultimo": len(records)-1, "ultima": len(records)-1}
    ordinal = re.fullmatch(r"(?:(?:quero|cancele|cancelar|o|a|da|de|opcao)\s+)*(primeir[oa]|segundo|segunda opcao|terceir[oa]|ultim[oa])(?:\s+(?:reserva|agendamento|opcao))?", text)
    if text.isdigit() or ordinal:
        index = int(text)-1 if text.isdigit() else ordinals[ordinal.group(1)]
        return ("selected", records[index]["id"]) if 0 <= index < len(records) else ("unavailable", None)
    plan = build_plan(message=body, catalog=[], reference_date=_now().astimezone(ZoneInfo(tz_name)).date())
    date_reference = bool(re.search(r"\b(?:hoje|amanha|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|\d{1,4}[-/]\d{1,2}", text))
    time_reference = bool(re.search(r"\b(?:as|pelas|pras)\s+|\d{1,2}(?::\d{2}|h)", text))
    if ((date_reference and not plan.requested_date) or (time_reference and not plan.requested_time)
            or set(plan.ambiguities) & {"date", "time"} or plan.time_relation not in {"none", "exact"}):
        return "ambiguous", None
    if plan.requested_date or plan.requested_time:
        filtered = []
        for row in candidates:
            local = datetime.fromisoformat(row["start_at"]).astimezone(ZoneInfo(tz_name))
            if (not plan.requested_date or local.date().isoformat() == plan.requested_date) and (
                    not plan.requested_time or local.strftime("%H:%M") == plan.requested_time):
                filtered.append(row)
        candidates = filtered; reference = True
    # Names come only from the same contact's read-only factual records. A
    # shared first name can narrow candidates, but never silently pick one.
    names = {normalized(row.get("professional", "")) for row in records if row.get("professional")}
    matched_names = {name for name in names if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text)}
    matched_names |= {name for name in names if len(name.split()[0]) >= 3 and
        re.search(r"(?<!\w)" + re.escape(name.split()[0]) + r"(?!\w)", text)}
    if matched_names:
        if re.search(r"\be\b", text):
            return "ambiguous", None
        candidates = [row for row in candidates if normalized(row.get("professional", "")) in matched_names]
        reference = True
    elif re.search(r"\b(?:com|pelo|pela|profissional|barbeiro)\s+(?:(?:o|a)\s+)?[a-z]", text):
        return "ambiguous", None
    if reference:
        return ("selected", candidates[0]["id"]) if len(candidates) == 1 else ("ambiguous", None)
    return "none", None


def choose_candidate(body, records, timezone):
    """Pure selector for a caller-supplied, authenticated candidate snapshot."""
    return _selection(body, records, timezone)


async def owned_candidates(tenant, phone):
    """At most eight future records, each re-read through contact ownership."""
    _scope(tenant, phone)
    rows = await customer_appointments(tenant, phone, limit=15)
    now = _now()
    future = sorted((row for row in rows if row["status"] == "confirmed" and row["start_at"] > now), key=lambda row: row["start_at"])[:8]
    records = []
    for row in future:
        current = await owned_booking(tenant, "+"+phone, item_id=row["id"])
        if current and current.get("status") == "confirmed" and _future(current):
            records.append({"id": str(current["id"]), "protocol": current["protocol"],
                "start_at": current["start_at"].isoformat(), "professional": current.get("professional")})
    return records


def _listing(records, tz_name):
    rows = []
    for row in records:
        when = datetime.fromisoformat(row["start_at"]).astimezone(ZoneInfo(tz_name))
        clock = f"{when.hour:02d}h" + (f"{when.minute:02d}" if when.minute else "")
        professional = " · " + presentation.escape_label(row["professional"]) if row.get("professional") else ""
        rows.append(f"• {when:%d/%m/%Y} às {clock}{professional} · {presentation.escape_label(row['protocol'])}")
    return "Qual reserva você quer cancelar? Pode indicar a data, o horário ou o protocolo.\n\n" + "\n".join(rows)


async def _save(redis, key, state):
    remaining = _deadline(state) - _now().timestamp()
    await redis.set(key, json.dumps(state), ex=max(1, min(TTL, int(remaining))))


async def _proposal(tenant, phone, appointment_id, business, redis, key, message_id, *, deadline=None, prefix=""):
    record = await owned_booking(tenant, "+" + phone, item_id=appointment_id)
    if not record or record.get("status") != "confirmed":
        return {"answer": "Não encontrei essa reserva confirmada para este número neste estabelecimento. Nenhuma reserva foi alterada.", "step": "no_reservation"}
    if not _future(record):
        return {"answer": "Esse agendamento já começou. A equipe precisa conferir o cancelamento; nenhuma reserva foi alterada por esta solicitação.", "step": "cancel_expired"}
    if not isinstance(record.get("expected_snapshot"), str) or not re.fullmatch(r"[a-f0-9]{64}", record["expected_snapshot"]):
        return {"answer": "Preciso conferir um novo resumo completo com a equipe antes do cancelamento. Nenhuma reserva foi alterada.", "step": "cancel_review"}
    try:
        summary = booking_summary(record)
        composition = presentation.compose_summary(summary, (business.get("profile") or {}).get("style_rules"), operation="cancel")
        answer = prefix + composition.render()
        completed = presentation.render_summary(summary, (business.get("profile") or {}).get("style_rules"), operation="cancel", confirmed=True)
    except (KeyError, TypeError, ValueError):
        return {"answer": "Não consegui conferir os dados completos da reserva. O cancelamento precisa ser revisado pela equipe.", "step": "cancel_review"}
    if not presentation.fits_message(answer) or not presentation.fits_message(completed):
        return {"answer": "Os dados completos da reserva precisam ser revisados pela equipe antes do cancelamento. Nenhuma reserva foi alterada.", "step": "cancel_review"}
    if not _valid_id(message_id):
        return {"answer": "Preciso apresentar o resumo em uma nova mensagem identificada antes de aceitar o cancelamento. Nenhuma reserva foi alterada.", "step": "cancel_review"}
    state = {"step": "cancel_confirm", "appointment": str(record["id"]), "expected_snapshot": record["expected_snapshot"],
        "confirmation_summary": answer, "confirmation_message_id": message_id,
        "confirmation_presentation": composition.as_dict(),
        "confirmation_expires_at": deadline if deadline is not None else _now().timestamp()+TTL,
        "cancellation_scope": _scope(tenant, phone), "cancellation_context": _context(business),
        "cancellation_target_names": [(record.get("professional") or "").split(" ")[0], *summary.services]}
    await _save(redis, key, state)
    return {"answer": answer, "step": "cancel_confirm"}


async def handle(tenant, phone, body, *, business, state, redis, key, message_id,
                 preview=False, requested=False, semantic_kind=None):
    """Handle only an explicit confirmed-booking request or an existing proposal.

    ``requested`` is the caller's whole-turn operation classification, never a
    model-selected booking ID. ``semantic_kind`` can reuse that same semantic
    pass, but all local guards and the backend snapshot remain mandatory.
    The caller must hold its conversation lock and run the usual post-write
    revision check/receipt processing. Private markers must not reach HTTP.
    """
    scope = _scope(tenant, phone)
    if not isinstance(body, str) or not body.strip() or len(body) > 1500 or not key.startswith(f"orion:qr:ai:{tenant}:"):
        raise ValueError("Trusted QR conversation required")
    step = state.get("step")
    if not requested and step not in STEPS:
        return None
    if preview:
        return {"answer": "Prévia de cancelamento: nenhuma reserva real é consultada ou alterada nesta simulação.", "step": "preview_cancel"}
    if step in STEPS and state.get("cancellation_scope") == scope and declines_cancellation(body):
        await redis.delete(key)
        return {"answer": "Tudo bem, encerrei a solicitação de cancelamento. Quando precisar de algo, é só me chamar.",
                "step": "cancel_declined"}
    if denial := _policy_denial(business):
        return denial
    text = normalized(body)
    if step not in STEPS:
        if re.search(r"\b(?:se|caso|desde que|talvez|nao\s+(?:quero\s+)?cancel\w*)\b", text):
            return {"answer": "Você quer cancelar uma reserva já confirmada ou está consultando uma condição? Preciso distinguir antes de continuar.", "step": "cancel_clarify"}
        if step in {"date", "professional", "time", "confirm"} and not re.search(r"\b(?:reserva|agendamento|marcado|confirmad\w*)\b", text):
            return {"answer": "Você quer encerrar o pedido que ainda não foi confirmado ou cancelar uma reserva já confirmada?", "step": "cancel_clarify"}
        records = await owned_candidates(tenant, phone)
        now = _now()
        if not records:
            return {"answer": "Não encontrei reservas futuras confirmadas para este número neste estabelecimento. Nenhuma reserva foi alterada.", "step": "no_reservation"}
        kind, selected = _selection(body, records, business["timezone"])
        if kind == "selected" or (len(records) == 1 and kind == "none"):
            return await _proposal(tenant, phone, UUID(selected or records[0]["id"]), business, redis, key, message_id)
        prompt = _listing(records, business["timezone"])
        if not presentation.fits_message(prompt):
            return {"answer": "Há mais de uma reserva. Qual data ou protocolo você quer revisar para cancelamento?", "step": "cancel_clarify"}
        await _save(redis, key, {"step": "cancel_pick", "candidates": records,
            "confirmation_expires_at": now.timestamp()+TTL, "cancellation_scope": scope, "cancellation_context": _context(business)})
        return {"answer": prompt, "step": "cancel_pick"}
    if state.get("cancellation_scope") != scope:
        return {"answer": "O resumo de cancelamento expirou ou precisa ser conferido novamente. Nenhuma reserva foi alterada; diga qual reserva deseja revisar.", "step": "cancel_expired"}
    if _deadline(state) <= _now().timestamp():
        await redis.delete(key)
        return {"answer": "O resumo de cancelamento expirou. Se quiser cancelar, vamos conferir a reserva de novo.", "step": "cancel_expired"}
    if step == "cancel_pick":
        records = state.get("candidates", [])
        kind, selected = _selection(body, records, business["timezone"])
        if kind != "selected":
            prefix = "Vamos revisar uma reserva por vez. " if re.fullmatch(r"(?:tod[oa]s|amb[oa]s|os dois|as duas)[.!?\s]*", text) else ""
            return {"answer": prefix + _listing(records, business["timezone"]), "step": "cancel_pick"}
        return await _proposal(tenant, phone, UUID(selected), business, redis, key, message_id)
    if not isinstance(state.get("confirmation_summary"), str) or not state["confirmation_summary"].strip():
        return {"answer": "Preciso apresentar o resumo completo novamente antes do cancelamento. Nenhuma reserva foi alterada.", "step": "cancel_review"}
    if not _valid_id(message_id) or not _valid_id(state.get("confirmation_message_id")) or message_id == state["confirmation_message_id"]:
        return {"answer": "O cancelamento ainda não foi feito. Preciso de uma nova resposta depois do resumo para continuar.", "step": "cancel_confirm"}
    if summary_review_requested(body):
        return await _proposal(tenant, phone, UUID(state["appointment"]), business, redis, key, message_id,
            deadline=_deadline(state))
    decision = contextual_response(body, awaiting_confirmation=True, semantic_kind=semantic_kind, confirmation_action="cancel")
    if (_target_reference(body, state.get("cancellation_target_names", [])) or
            re.search(r"\b(?:agendar|agende|reservar|reserve|marcar|marque|remarc\w*|reagend\w*|compr\w*|adicion\w*|orcamento|pedido)\b", text)):
        decision = contextual_response("mudar o pedido", awaiting_confirmation=True, confirmation_action="cancel")
    if decision.reason == "unresolved":
        decision = await interpret_contextual_response(message=body, awaiting_confirmation=True,
            confirmation_action="cancel", key=os.getenv("GROQ_API_KEY", ""), model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    if decision.kind != "affirm":
        state = {**state, "confirmation_requires_review": True}
        await _save(redis, key, state)
        answer = "Quer conferir de novo o agendamento que você pediu para cancelar?"
        return {"answer": answer, "step": "cancel_confirm"}
    if state.get("confirmation_requires_review") or state.get("cancellation_context") != _context(business):
        return await _proposal(tenant, phone, UUID(state["appointment"]), business, redis, key, message_id,
            deadline=_deadline(state), prefix="Vou apresentar o resumo novamente; o cancelamento ainda não foi feito.\n\n")
    record = await owned_booking(tenant, "+" + phone, item_id=UUID(state["appointment"]))
    if not record or record.get("status") != "confirmed":
        return {"answer": "Essa reserva já não está disponível para cancelamento neste atendimento. Nenhuma nova alteração foi realizada.", "step": "no_reservation"}
    if not _future(record):
        return {"answer": "Esse agendamento já começou. Nenhum cancelamento foi realizado; a equipe precisa conferir sua solicitação.", "step": "cancel_expired"}
    if (not isinstance(state.get("expected_snapshot"), str) or not re.fullmatch(r"[a-f0-9]{64}", state["expected_snapshot"])
            or record.get("expected_snapshot") != state["expected_snapshot"]):
        return await _proposal(tenant, phone, UUID(state["appointment"]), business, redis, key, message_id,
            prefix="A reserva mudou. Confira o novo resumo antes de decidir; ainda não cancelei nada.\n\n")
    target_kind, target_id = _selection(body, [{"id": str(record["id"]), "protocol": record["protocol"],
        "start_at": record["start_at"].isoformat(), "professional": record.get("professional")}], business["timezone"])
    if target_kind not in {"none", "selected"} or (target_id and target_id != state["appointment"]):
        state = {**state, "confirmation_requires_review": True}
        await _save(redis, key, state)
        return {"answer": "Preciso distinguir a reserva que você mencionou. Nenhuma reserva foi cancelada; vamos conferir o resumo antes de continuar.", "step": "cancel_confirm"}
    try:
        summary = booking_summary(record)
        completed = presentation.render_summary(summary, (business.get("profile") or {}).get("style_rules"), operation="cancel", confirmed=True)
        if not presentation.fits_message(completed):
            return {"answer": "Preciso preservar os dados completos da reserva. A equipe deve revisar o cancelamento; nenhuma nova alteração foi feita.", "step": "cancel_review"}
        async with get_session_factory()() as db:
            await revision.check_before_write()
            updated = await cancel_appointment(db, tenant, UUID(state["appointment"]), customer_phone="+"+phone,
                expected_snapshot=state["expected_snapshot"], future_only=True)
    except HTTPException:
        state = {**state, "confirmation_requires_review": True}
        await _save(redis, key, state)
        return {"answer": "Não confirmei o cancelamento: a reserva ou o contato mudou. Precisamos conferir um novo resumo com a equipe.", "step": "cancel_review"}
    if updated.get("status") != "cancelled" or str(updated.get("id")) != state["appointment"]:
        return {"answer": "Não consegui verificar o resultado do cancelamento. A equipe precisa conferir a reserva antes de outra tentativa.", "step": "cancel_uncertain"}
    await redis.delete(key)
    return {"answer": completed, "step": "cancelled", "protocol": updated["protocol"], "_verified_operation": True}
