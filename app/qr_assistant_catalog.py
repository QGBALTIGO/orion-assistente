"""Service discovery from this tenant's current facts, without booking writes."""
import json
import re

from app.qr_behaviors import norm
from app.qr_presentation import escape_label, money, duration, message_units, PresentationPreferences

PAGE_SIZE = 6


def clip(value, maximum):
    value = " ".join(str(value).split())
    truncated = message_units(value) > maximum
    if truncated:
        maximum -= 1
    while message_units(value) > maximum:
        value = value[:-1]
    return value + ("…" if truncated else "")


def _request(body, browser):
    text = norm(body).strip(" !?.")
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
    if text in {"mais servicos", "proximos servicos", "proxima pagina"} and isinstance(browser, dict):
        return {**browser, "page": browser.get("page", 0)+1}
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
    pages = max(1, (len(rows)+PAGE_SIZE-1)//PAGE_SIZE)
    page = min(request["page"], pages-1)
    shown = rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]
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
        heading += f" · página {page+1}/{pages}"
    lines = []
    for index, row in enumerate(shown, 1):
        price = ("a partir de " if row.get("price_mode") == "from" else "") + money(row["price_cents"])
        lines.append(f"{index}. *{escape_label(clip(row['name'], 75))}*\n{price} · {duration(row['duration_min'])}")
    answer = heading + "\n\n" + "\n\n".join(lines)
    answer += "\n\nPode escolher pela lista ou escrever o serviço. Para pesquisar: *buscar serviço* e o nome."
    if page+1 < pages:
        answer += "\nPara continuar: *mais serviços*."
    if business.get("catalog_has_more"):
        answer += "\nCatálogo extenso: a equipe pode ajudar com os demais serviços."
    return {"answer": answer, "step": "catalog", "_catalog_page": {
        "rows": shown, "page": page, "pages": pages, "query": query, "category": category,
        "intro": marker+"*Escolha seu serviço*\nVeja o valor e a duração nas opções. Você também pode escrever o nome.",
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
