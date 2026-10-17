"""Deterministic, testable control intents: never grant the LLM business authority."""
import re
import unicodedata

def norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if unicodedata.category(c) != "Mn").strip()

def human_control(text: str) -> str | None:
    """Pause only after an unqualified current request across the whole turn."""
    human = r"(?:atendente(?: humano)?|humano|pessoa de verdade|falar com uma pessoa)"
    assistant = r"(?:bot|robo|(?:continuar|seguir|ficar|falar|conversar|quero|prefiro)\s+(?:(?:com|por|o|a)\s+)*(?:voce|aqui))"
    mentions = [(match.start(), match.end(), "human") for match in re.finditer(r"\b" + human + r"\b", text)]
    if not mentions:
        return None
    if re.search(r"\b(?:se|caso|desde que|a menos que|contanto que|dependendo|"
                 r"como|quando|quero saber|queria saber|gostaria de saber)\b", text):
        return "human_clarify"
    mentions.extend((match.start(), match.end(), "assistant") for match in re.finditer(r"\b" + assistant + r"\b", text))
    events = []
    for start, end, target in sorted(mentions):
        prefix = re.split(r"[,;.!?]|\bmas\b", text[:start])[-1]
        verbs = list(re.finditer(r"\b(?:nao|nem|sem|dispenso|quero|prefiro|preciso|desejo|gostaria|melhor|continuar|seguir|ficar|falar|conversar)\b", prefix))
        negative = False
        if verbs:
            latest = verbs[-1]
            negative = latest.group() in {"nao", "nem", "sem", "dispenso"} or bool(re.search(
                r"\b(?:nao|nem)(?:\s+(?:quero|preciso|desejo|gostaria|vou|prefiro|mais|falar|conversar|continuar|seguir|ficar))*\s*$",
                prefix[:latest.end()]))
        events.append((start, end, target, not negative))
    positive_human = [row for row in events if row[2:] == ("human", True)]
    positive_assistant = [row for row in events if row[2:] == ("assistant", True)]
    negative_assistant = [row for row in events if row[2:] == ("assistant", False)]
    if re.search(r"\bou\b", text) and positive_human:
        return "human_clarify"
    if positive_human and positive_assistant:
        final = max(positive_human + positive_assistant)
        earlier = min(positive_human + positive_assistant)
        if not re.search(r"\b(?:melhor|na verdade|quer dizer|prefiro|nao|pera|espera)\b", text[earlier[1]:final[0]]):
            return "human_clarify"
        return "human" if final[2] == "human" else "human_declined"
    if not positive_human:
        return "human_clarify" if negative_assistant and not positive_assistant else "human_declined"
    last = positive_human[-1]
    if re.search(r"(?:[,;.]|\bmas)\s*(?:nao|pera|espera)\b", text[last[1]:]):
        return "human_clarify"
    request = re.search(r"\b(?:quero|preciso|desejo|gostaria|prefiro|cham[ae]|pass[ae]|encaminh[ae]|falar|conversar)\b", text)
    if not request and ("?" in text or re.search(r"\b(?:como|quando|quem|qual|quais)\b", text)):
        return "human_clarify"
    return "human"


def control_intent(text: str) -> str | None:
    lower = norm(text)
    if human := human_control(lower):
        return human
    if any(s in lower for s in ("remarcar", "trocar meu horario", "mudar meu horario",
                               "adiar meu horario", "reagendar")):
        return "reschedule"
    if any(s in lower for s in ("meus agendamentos", "tenho agendamento",
                               "minha reserva", "meu horario marcado",
                               "quando esta meu horario", "historico de agendamentos")):
        return "my_bookings"
    if any(s in lower for s in ("quero cancelar", "cancelar minha reserva",
                               "desmarcar meu horario")):
        return "cancel_request"
    if re.fullmatch(r"(oi+|ola+|bom dia|boa tarde|boa noite|e ai|eae|opa)[!.,\s]*", lower):
        return "greeting"
    if lower.strip(" ?!.,") in {"qual o seu nome", "qual seu nome", "como voce se chama", "quem e voce"}:
        return "identity"
    if re.search(r"\b(idiota|burro|imbecil|vai se foder|porra|merda)\b",lower):
        return "hostile"
    return None


def catalog_match(text: str, catalog: list[dict]) -> list:
    lower = norm(text)
    found = []
    # Deterministic fallback only when an exact, active catalog name is quoted.
    # Do not rely on a substring that is another product or an arbitrary synonym.
    for item in catalog:
        name = norm(item["name"])
        if len(name) >= 3 and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lower):
            found.append(item["id"])
    return found[:6]


def catalog_question_services(value: str, catalog: list[dict], *, strict: bool = True) -> list | None:
    """Resolve a complete price subject from this active catalog, or ask again.

    None means no recognizable reference. An empty list means an ambiguous,
    negated or partly unknown combination, which must not produce a total.
    """
    lower = norm(value)
    price = list(re.finditer(r"\b(?:quanto\s+(?:que\s+)?(?:custa|custam|fica|ficam|e|sai)|"
                            r"(?:custa|custam|fica|ficam|sai)\s+quanto|"
                            r"(?:qual(?:\s+e)?(?:\s+o)?\s+)?(?:preco|valor))\b", lower))
    if strict and not price:
        return None
    parts = (lower[price[-1].end():] if price else lower).split("?", 1)
    subject = parts[0]
    if len(parts) == 2 and (catalog_match(parts[1], catalog) or re.search(
            r"\b(?:cabelo|corte|barba|sem|nao|nem|menos|mas|tambem|inclui|incluir|e)\b", parts[1])):
        return []  # A later addition/withdrawal makes the isolated subject unsafe.
    if price and not (set(re.findall(r"\w+", subject)) - {"mesmo", "entao", "por", "favor"}):
        subject = lower[:price[-1].start()]
    if re.search(r"\b(?:sem|menos|nao|nem)\b", subject):
        return []
    remaining = subject
    chosen = set()
    # Longer catalog names consume their whole span before component services.
    for item in sorted(catalog, key=lambda item: len(item["name"]), reverse=True):
        name = norm(item["name"])
        pattern = r"(?<!\w)" + re.escape(name) + r"(?!\w)"
        if len(name) >= 3 and re.search(pattern, remaining):
            chosen.add(item["id"])
            remaining = re.sub(pattern, " ", remaining)
    for alias, names in (
        (r"\b(?:cabelos?|corte)\b", r"corte(?: de cabelo| cabelo| masculino| feminino| infantil)?"),
        (r"\bbarba\b", r"barba(?: completa| tradicional| desenhada)?"),
    ):
        if not re.search(alias, remaining):
            continue
        candidates = [item for item in catalog if re.fullmatch(names, norm(item["name"]))]
        if len(candidates) != 1:
            return []
        chosen.add(candidates[0]["id"])
        remaining = re.sub(alias, " ", remaining)
    if not chosen:
        reference = {"isso", "disso", "tudo", "esse", "esses", "este", "estes", "servico", "servicos",
                     "o", "a", "os", "as", "total", "no", "mesmo", "por", "favor"}
        return [] if strict and set(re.findall(r"\w+", subject)) - reference else None
    filler = {"o", "a", "os", "as", "e", "um", "uma", "de", "do", "da", "dos", "das",
              "por", "favor", "mesmo", "entao", "so", "apenas", "total", "juntos", "junto",
              "cortar", "fazer", "aparar"}
    if not strict:
        filler |= {"quais", "qual", "sao", "dias", "dia", "que", "tem", "ha", "disponivel", "disponiveis",
                   "para", "agendamento", "agendamentos", "horario", "horarios", "livre", "livres",
                   "amanha", "hoje", "depois", "consultar", "consulta", "quero", "queria", "estao", "esta"}
        filler |= {"segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo", "feira", "na", "no", "pra", "proxima", "proximo"}
        remaining = re.sub(r"\b\d{1,4}\b", " ", remaining)
    if set(re.findall(r"\w+", remaining)) - filler:
        return []
    return [item["id"] for item in catalog if item["id"] in chosen][:6]


def availability_question(value: str) -> bool:
    lower = norm(value)
    return bool(re.search(r"\b(?:disponive(?:l|is)|vagas?|horarios? livres|dias? livres)\b", lower)
                or re.search(r"\b(?:quais?|tem|ha)\b.*\bhorarios?\b", lower)
                or re.search(r"\bdias?\b.*\b(?:livres?|vagos?|disponiveis?)\b", lower)
                or re.search(r"\b(?:quando|tem|ha)\b.*\blivres?\b|\blivres?\b.*\bquando\b", lower))
