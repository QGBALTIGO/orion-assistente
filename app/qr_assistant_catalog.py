"""Service discovery from this tenant's current facts, without booking writes."""
import json
import re

from app.qr_behaviors import norm
from app.qr_presentation import escape_label, money, duration, message_units, PresentationPreferences

PAGE_SIZE = 8
MAX_LIST_ROWS = 10


def clip(value, maximum):
    value = " ".join(str(value).split())
    truncated = message_units(value) > maximum
    if truncated:
        maximum -= 1
    while message_units(value) > maximum:
        value = value[:-1]
    return value + ("…" if truncated else "")


def service_option(row):
    """Keep names and price readable within the native row's 24/72 limits."""
    name = " ".join(str(row["name"]).split())
    title = name
    if message_units(title) > 24:
        words, title = name.split(), ""
        for word in words:
            candidate = (title+" "+word).strip()
            if message_units(candidate) > 24:
                break
            title = candidate
        title = title or clip(name, 24)
    minutes = row.get("duration_min")
    if type(minutes) is int and minutes > 0:
        hours, remainder = divmod(minutes, 60)
        short_time = (f"{hours}h"+(f"{remainder:02d}" if remainder else "")) if hours else f"{minutes} min"
    else:
        short_time = duration(minutes)
    price = ("a partir de " if row.get("price_mode") == "from" else "")+money(row["price_cents"])
    facts = price+" · "+short_time
    suffix = name[len(title):].strip() if name.startswith(title) else name
    description = (suffix+" · " if suffix else "")+facts
    if message_units(description) > 72:
        description = facts+" · "+clip(suffix, max(1, 72-message_units(facts)-3))
    return title, clip(description, 72)


def _request(body, browser):
    text = norm(body).strip(" !?.")
    text = re.sub(r"(?:,\s*)?\bpor favor$", "", text).strip(" ,!?.")
    text = re.sub(r"^(?:(?:pode|poderia|consegue)\s+(?:me\s+)?(?:mostrar|listar|mandar|enviar|ver)|"
                  r"me\s+(?:mostra|mostre|manda|mande)|quero\s+(?:ver|conhecer))\s+(?:os\s+)?", "", text)
    if re.fullmatch(r"(?:valores|precos|tabela de (?:valores|precos)|(?:valores|precos)(?: de todos| dos| de)? (?:os )?servicos|todos os servicos)", text):
        return {"page": 0, "query": "", "category": ""}
    if re.fullmatch(r"quais(?: sao)?(?: os)? servicos(?: (?:voces tem|tem|disponiveis))?", text):
        return {"page": 0, "query": "", "category": ""}
    if re.fullmatch(r"(?:(?:me )?(?:manda|mande|mostra|mostre|ver|veja|quero ver|quais sao)(?:\s+o[sa]?)?\s+)?(?:lista(?: de servicos)?|catalogo(?: de servicos)?|servicos|opcoes de servicos)", text):
        return {"page": 0, "query": "", "category": ""}
    found = re.fullmatch(r"(?:buscar|procurar|pesquisar) servicos?\s*:?\s*(.{1,80})", text)
    if found:
        return {"page": 0, "query": found[1], "category": ""}
    found = re.fullmatch(r"categoria\s*:?\s*(.{1,80})", text)
    if found:
        return {"page": 0, "query": "", "category": found[1]}
    found = re.fullmatch(r"(?:ver )?servicos pagina (\d{1,3})", text)
    if found and isinstance(browser, dict):
        return {**browser, "page": max(0, int(found[1])-1)}
    if text in {"mais servicos", "outros servicos", "tem mais servicos", "proximos servicos", "proxima pagina"} and isinstance(browser, dict):
        return {**browser, "page": browser.get("page", 0)+1}
    if text in {"pagina anterior", "servicos anteriores", "voltar para a pagina anterior", "voltar a pagina anterior"} and isinstance(browser, dict):
        return {**browser, "page": max(0, browser.get("page", 0)-1)}
    return None


async def handle_request(body, state, business, catalog, redis, key):
    request = _request(body, state.get("catalog_browser"))
    if request is None:
        return None
    profile = business.get("profile") or {}
    if business.get("profile_id") and "appointments" not in profile.get("capabilities", []):
        return None  # Non-booking niches keep their existing product/catalog path.
    if business.get("profile_id") and "catalog_answers" not in profile.get("capabilities", []):
        return {"answer": "A equipe precisa conferir os serviços e valores deste atendimento. Quer falar com uma pessoa?", "step": "capability_unavailable"}
    category, query = request["category"], request["query"]
    rows = [row for row in catalog if (not category or norm(row.get("category") or "") == category)
            and (not query or all(word in norm(row["name"]) for word in query.split()))]
    page_size = MAX_LIST_ROWS if len(rows) <= MAX_LIST_ROWS else PAGE_SIZE
    pages = max(1, (len(rows)+page_size-1)//page_size)
    page = min(request["page"], pages-1)
    shown = rows[page*page_size:(page+1)*page_size]
    request["page"] = page
    # Browsing withdraws consent, while retaining the customer's draft choices.
    if state.get("step") in {"confirm", "reschedule_confirm", "cancel_confirm"}:
        state["confirmation_requires_review"] = True
    state["catalog_browser"] = request
    state["catalog_menu_ids"] = [str(row["id"]) for row in shown]
    state["catalog_choice_expected"] = bool(shown)
    if not state.get("step"):
        state["step"] = "menu"
    await redis.set(key, json.dumps(state), ex=7*24*3600)
    if not shown:
        return {"answer": "Não encontrei serviços com essa busca. Pode dizer outro nome ou pedir a lista de serviços?", "step": "catalog"}
    prefs = PresentationPreferences.from_mapping(profile.get("style_rules"))
    title = {"professional": "Serviços e valores", "direct": "Serviços"}.get(prefs.style, "Serviços disponíveis")
    marker = "📋 " if prefs.emoji == "light" else ""
    heading = marker+f"*{title}*"
    if pages > 1:
        heading += f"\nPágina {page+1}/{pages} · {page*page_size+1}–{page*page_size+len(shown)} de {len(rows)} serviços"
    lines = []
    for index, row in enumerate(shown, 1):
        price = ("a partir de " if row.get("price_mode") == "from" else "") + money(row["price_cents"])
        lines.append(f"{index}. *{escape_label(clip(row['name'], 100))}*\n{price} · {duration(row['duration_min'])}")
    answer = heading + "\n\n" + "\n\n".join(lines)
    answer += "\n\nEscolha uma opção ou me diga o serviço que procura."
    if page+1 < pages:
        answer += "\nQuer ver os próximos serviços? É só pedir."
    if page:
        answer += "\nVocê também pode voltar à página anterior."
    if business.get("catalog_has_more"):
        answer += "\nCatálogo extenso: a equipe pode ajudar com os demais serviços."
    return {"answer": answer, "step": "catalog", "_catalog_page": {
        "rows": shown, "page": page, "pages": pages, "query": query, "category": category, "total": len(rows),
        "intro": marker+"*Escolha seu serviço*\nVeja os valores e a duração na lista. Se preferir, me diga o nome do serviço.",
    }}


async def focus_service(redis, key, service_id):
    """An explicit service selection withdraws old consent, never confirms it."""
    state = json.loads(await redis.get(key) or "{}")
    if not isinstance(state, dict):
        state = {}
    state.update(step="menu", menu_ids=[str(service_id)], service_reply_expected=True)
    for field in ("confirmation_summary", "confirmation_message_id", "confirmation_expires_at",
                  "confirmation_presentation", "hold_snapshot", "confirmation_requires_review",
                  "catalog_choice_expected", "availability_query"):
        state.pop(field, None)
    await redis.set(key, json.dumps(state), ex=7*24*3600)
    return "1"


async def numeric_choice(redis, key, body):
    if not body.strip().isdecimal():
        return body
    try:
        state = json.loads(await redis.get(key) or "{}")
    except (TypeError, ValueError):
        return body
    if not isinstance(state, dict) or state.get("catalog_choice_expected") is not True:
        return body
    ids = state.get("catalog_menu_ids") or []
    number = int(body.strip())
    if not 1 <= number <= len(ids):
        return body
    return await focus_service(redis, key, ids[number-1])
