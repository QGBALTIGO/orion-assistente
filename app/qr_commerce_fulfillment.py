"""QR receiving choices. Only scoped backend facts authorize a quote contract."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from zoneinfo import ZoneInfo

from app.qr_cancellation import choose_candidate
from app.qr_commerce_understanding import norm
from app.qr_presentation import escape_label, fits_message


def enabled():
    return os.getenv("COMMERCIAL_FULFILLMENT_ENABLED", "0") == "1"


@dataclass(frozen=True)
class ReceivingIntent:
    relevant: bool = False
    mode: str | None = None
    linked: bool | None = None
    query: bool = False
    ambiguous: bool = False


def interpret_receiving(body, *, waiting=False):
    text = norm(body).strip()
    pickup = bool(re.search(r"\b(?:retirada|retirar na loja|retirar no local|para retirar|vou retirar|quero retirar|vou buscar|buscar na loja|busco na loja)\b", text))
    pickup |= bool(re.fullmatch(r"(?:retirar|buscar|na loja|no local)[.!\s]*", text))
    delivery = bool(re.search(r"\b(?:entrega|entregar|entregam|delivery|receber em casa)\b", text))
    unlinked = bool(re.search(r"\b(?:sem (?:um |meu )?agendamento|sem vinculo|sem associar|separado do agendamento)\b", text))
    linked = bool(re.search(r"\b(?:no|junto (?:ao|do)|com o)\s+(?:meu |mesmo )?(?:agendamento|horario)\b|\b(?:na|no)\s+(?:primeir[oa]|segund[oa]|terceir[oa]|ultim[oa])\s+(?:reserva|agendamento)\b", text))
    relevant = pickup or delivery or unlinked or linked or waiting
    if not relevant:
        return ReceivingIntent()
    # The mode/reference is never consent to the old order. Doubts, alternative
    # targets and conditions require a new review rather than a best guess.
    guarded = re.sub(r"\bsem (?:um |meu )?agendamento\b|\bsem vinculo\b|\bsem associar\b", "", text)
    ambiguous = bool(re.search(r"\b(?:nao|nem|talvez|se|caso|desde que|ou|ambos|ambas|todos|todas)\b", guarded))
    ambiguous |= pickup and delivery or unlinked and linked
    query = bool(re.search(r"\b(?:quanto|preco|frete|prazo|como funciona|voces (?:fazem|tem|entregam)|posso retirar)\b", text))
    query |= "?" in text and not re.search(r"\b(?:quero comprar|vou levar|quero levar|quero pedir)\b", text)
    return ReceivingIntent(relevant, "delivery" if delivery and not pickup else "pickup" if pickup or linked or unlinked else None,
        False if unlinked else True if linked else None, query, ambiguous)


def context_revision(options):
    facts = {key: options.get(key) for key in ("enabled", "enabled_modes", "profile_version_id", "profile_revision", "timezone")}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


def mode_question(options):
    modes = options.get("enabled_modes", []) if options.get("enabled") is True else []
    labels = ["retirada na empresa" if mode == "pickup" else "entrega, sujeita à análise da equipe" for mode in modes]
    if not labels:
        return "As opções de recebimento ainda precisam ser conferidas pela equipe. Nenhum pedido foi confirmado."
    return "Como prefere receber os produtos: " + " ou ".join(labels) + "? A retirada pode ser feita sem vincular um agendamento."


def options_answer(options):
    answer = mode_question(options)
    if "delivery" in options.get("enabled_modes", []) and options.get("enabled") is True:
        answer += " Entrega exige análise da equipe; este atendimento não calcula frete nem confirma prazo ou pagamento."
    return answer


def clear_selection(state):
    for field in ("pending", "fulfillment", "fulfillment_waiting_for", "pickup_candidates"):
        state.pop(field, None)


def receiving_reference(body, state=None):
    """Read-only routing; a receiving link is not a request for a new booking."""
    state = state or {}
    if re.search(r"\b(?:agendar|marcar|reservar|remarcar|reagendar|cancelar|desmarcar)\b", norm(body)):
        return False
    if interpret_receiving(body).relevant:
        return True
    if (state.get("fulfillment") or {}).get("pickup_appointment_id") and target_reference(body):
        return True
    records = state.get("pickup_candidates", [])
    if state.get("fulfillment_waiting_for") == "appointment" and records and state.get("fulfillment_timezone"):
        if target_reference(body):
            return True
        return choose_candidate(body, records, state["fulfillment_timezone"])[0] != "none"
    return False


def target_reference(body):
    return bool(re.search(r"\b(?:primeir[oa]|segund[oa]|terceir[oa]|ultim[oa]|outro|outra|ambos|ambas|todos|todas|"
        r"hoje|amanha|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|\b(?:os dois|as duas)\b|\bag-[a-z0-9-]+\b|"
        r"\d{1,2}(?::\d{2}|h)|\bcom\s+(?!(?:certeza|confianca|seguranca)\b)[a-z]+", norm(body)))


CANDIDATE_QUESTION = "Em qual dos seus agendamentos confirmados pretende retirar? Pode indicar a data, o horário, o profissional ou o protocolo."


def _candidate_line(record, timezone_name):
    when = datetime.fromisoformat(str(record["start_at"])).astimezone(ZoneInfo(timezone_name))
    clock = f"{when.hour:02d}h" + (f"{when.minute:02d}" if when.minute else "")
    professional = " · " + escape_label(record["professional"]) if record.get("professional") else ""
    return f"• {when:%d/%m/%Y} às {clock}{professional} · {escape_label(record['protocol'])}"


def _visible_candidates(records, timezone_name):
    visible, lines = [], [CANDIDATE_QUESTION]
    for row in records[:8]:
        line = _candidate_line(row, timezone_name)
        if not fits_message("\n\n".join(lines + [line]), 900):
            break
        visible.append(row)
        lines.append(line)
    return visible


def candidate_listing(records, timezone_name):
    return "\n\n".join([CANDIDATE_QUESTION, *[_candidate_line(row, timezone_name) for row in records]])


async def select(scope, body, state, options, store):
    """Return (selection, prompt); no quote, order, stock or calendar write."""
    proposal = interpret_receiving(body, waiting=state.get("fulfillment_waiting_for") == "appointment")
    revision = context_revision(options)
    old_context = state.get("fulfillment_context")
    if old_context and old_context != revision:
        clear_selection(state)
        state["fulfillment_context"] = revision
        return None, "As opções publicadas de recebimento mudaram. Precisamos revisar um novo resumo.\n\n" + mode_question(options)
    state["fulfillment_context"] = revision
    state["fulfillment_timezone"] = options["timezone"]
    if options.get("enabled") is not True or not options.get("enabled_modes"):
        clear_selection(state)
        return None, mode_question(options)
    if proposal.ambiguous:
        state.pop("pending", None)
        return None, "Preciso distinguir o recebimento e o agendamento pretendidos.\n\n" + mode_question(options)
    selection = dict(state.get("fulfillment") or {})
    if proposal.mode:
        state.pop("pending", None)
        if selection.get("mode") != proposal.mode:
            selection = {"mode": proposal.mode, "pickup_appointment_id": None}
            state.pop("pickup_candidates", None)
            state.pop("fulfillment_waiting_for", None)
        elif proposal.linked is not True:
            state.pop("fulfillment_waiting_for", None)
    if not selection.get("mode"):
        state["fulfillment_waiting_for"] = "mode"
        return None, mode_question(options)
    if selection["mode"] not in options["enabled_modes"]:
        clear_selection(state)
        return None, "Essa opção não está habilitada no atendimento publicado.\n\n" + mode_question(options)
    if selection["mode"] == "delivery":
        state["fulfillment"] = {"mode": "delivery", "pickup_appointment_id": None}
        return None, "A entrega precisa ser analisada pela equipe. Não confirmei pedido nem reservei estoque; frete, prazo e pagamento não foram definidos."
    if proposal.linked is False:
        selection["pickup_appointment_id"] = None
        state.pop("pickup_candidates", None)
        state.pop("fulfillment_waiting_for", None)
    elif proposal.linked is True or state.get("fulfillment_waiting_for") == "appointment":
        # Fresh scope-filtered candidates are the only source of IDs. A model,
        # a protocol from another contact or the first array entry is no authority.
        previous = state.get("pickup_candidates", [])
        rows = await store.pickup_appointments(scope)
        records = []
        now = datetime.now(timezone.utc)
        for row in rows[:15]:
            when = datetime.fromisoformat(str(row["start_at"]))
            if when.tzinfo is not None and when > now:
                records.append({**row, "id": str(row["id"]), "start_at": when.isoformat(),
                    "professional": row.get("professional_name") or row.get("professional")})
        records = _visible_candidates(records, options["timezone"])
        state["fulfillment_waiting_for"] = "appointment"
        state["pickup_candidates"] = records
        selection["pickup_appointment_id"] = None
        state["fulfillment"] = selection
        if not records:
            return None, "Não encontrei agendamento próprio, confirmado e futuro para vincular. Você prefere retirar sem agendamento? A equipe também pode ajudar."
        if previous and previous != records:
            return None, "Os agendamentos disponíveis mudaram; confira as opções atuais antes de escolher.\n\n" + candidate_listing(records, options["timezone"])
        reference = re.sub(r"^.*?\b(?:no|na)\s+(?=(?:primeir[oa]|segund[oa]|terceir[oa]|ultim[oa])\b)", "", norm(body))
        reference = re.sub(r"^(?:pode confirmar|confirmo|quero retirar|retirar)\s+", "", reference)
        kind, selected = choose_candidate(reference, records, options["timezone"])
        if kind != "selected":
            return None, candidate_listing(records, options["timezone"])
        selection["pickup_appointment_id"] = str(selected)
        state.pop("fulfillment_waiting_for", None)
        state.pop("pickup_candidates", None)
    state["fulfillment"] = selection
    return selection, None


def render_receiving(facts):
    """Only immutable server snapshot and separately labelled current facts."""
    mode, snapshot = facts.get("fulfillment_mode"), facts.get("fulfillment_snapshot")
    if mode is None and snapshot is None and facts.get("pickup_appointment_id") is None:
        return ""
    if mode not in {"pickup", "delivery"} or not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or snapshot.get("mode") != mode:
        raise ValueError("Invalid server receiving snapshot")
    if mode == "delivery":
        if snapshot.get("requires_team_review") is not True or facts.get("pickup_appointment_id") is not None or snapshot.get("pickup_appointment") is not None:
            raise ValueError("Delivery cannot be automatically confirmed")
        return "*Recebimento:* entrega sujeita à análise da equipe.\n_Frete, prazo e pagamento não definidos; sem confirmação automática._"
    lines = ["*Recebimento:* retirada na empresa."]
    if snapshot.get("pickup_location"):
        lines.append("*Local publicado:* " + escape_label(snapshot["pickup_location"]))
    appointment = snapshot.get("pickup_appointment")
    if appointment:
        if str(facts.get("pickup_appointment_id")) != str(appointment.get("id")):
            raise ValueError("Appointment must match the immutable server contract")
        when = datetime.fromisoformat(str(appointment["start_at"]))
        if when.tzinfo is None:
            raise ValueError("Appointment snapshot requires timezone")
        local = when.astimezone(ZoneInfo(appointment["timezone"]))
        clock = f"{local.hour:02d}h" + (f"{local.minute:02d}" if local.minute else "")
        lines.append(f"*Vínculo revisado:* {local:%d/%m/%Y} às {clock} · " + escape_label(appointment["protocol"]))
        lines.append("_Essa associação não cria nem altera agendamento._")
        current = facts.get("pickup_appointment_current")
        if isinstance(current, dict):
            if current.get("available") is False:
                lines.append("_Não foi possível conferir a situação atual do agendamento; o vínculo acima é histórico._")
            elif current.get("available") is True:
                labels = {"confirmed": "confirmado", "cancelled": "cancelado", "held": "aguardando confirmação"}
                status = labels.get(current.get("status"), "em análise")
                current_time = ""
                if current.get("start_at"):
                    value = datetime.fromisoformat(str(current["start_at"]))
                    if value.tzinfo is None:
                        raise ValueError("Current appointment requires timezone")
                    value = value.astimezone(ZoneInfo(appointment["timezone"]))
                    current_time = f" · {value:%d/%m/%Y} às {value.hour:02d}h" + (f"{value.minute:02d}" if value.minute else "")
                lines.append("*Situação atual do agendamento:* " + status + current_time + ". O vínculo revisado acima permanece histórico.")
    else:
        if facts.get("pickup_appointment_id") is not None:
            raise ValueError("Linked pickup requires its factual snapshot")
        lines.append("_Sem agendamento associado; horário de retirada não definido._")
    return "\n".join(lines)
