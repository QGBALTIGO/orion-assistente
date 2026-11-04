"""Pure matching, graph validation and declarative transport plans.

The choices not observable in the reference are explicit here: case/space/accent
normalization, specific matches before wildcards, substring list terms, and
conservative wildcard windows reserved when a match is admitted.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import re
import unicodedata
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from app.subscription_chatbot_schemas import CONTACT_TAGS, TAG_PATTERN, URL_TAG_PATTERN, clean_text


WILDCARDS = {"*": timedelta(hours=24), "**": timedelta(days=14), "***": None,
             "****": timedelta(0), "*****": timedelta(hours=1)}
MAX_DEPTH = 8
MAX_ACTIONS = 30


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    return " ".join("".join(char for char in value if not unicodedata.combining(char)).split())


def matches(rule, value):
    keyword = rule["keyword"]
    if keyword in WILDCARDS:
        return False
    value = normalize(value)
    normalized = rule.get("normalized_keyword") or normalize(keyword)
    if rule["match_mode"] == "equal":
        return value == normalized
    if rule["match_mode"] == "like":
        return normalized in value
    return any(term.strip() and term.strip() in value for term in normalized.split(","))


def candidates(rules, value, device_id):
    eligible = [rule for rule in rules if rule["enabled"] and not rule.get("removed_at")
                and (rule.get("device_id") is None or str(rule["device_id"]) == str(device_id))]
    specific = [rule for rule in eligible if matches(rule, value)]
    selected = specific or [rule for rule in eligible if rule["keyword"] in WILDCARDS]
    return sorted(selected, key=lambda rule: rule["creation_order"], reverse=True)


def window_available(keyword, previous, now, zone):
    if keyword not in WILDCARDS or keyword == "****" or not previous:
        return True
    if keyword == "***":
        return previous.get("local_day") != now.astimezone(ZoneInfo(zone)).date()
    return previous["next_allowed_at"] <= now


def window_end(keyword, now, zone):
    if keyword == "***":
        local = now.astimezone(ZoneInfo(zone))
        tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.astimezone(timezone.utc), local.date()
    return now + WILDCARDS.get(keyword, timedelta(0)), None


def render_contact(value, facts, *, maximum=32000):
    def substitute(match):
        replacement = facts.get(match[1])
        if not isinstance(replacement, str) or not replacement.strip():
            raise ValueError("Dados indisponíveis para a tag " + match[1])
        return replacement.strip()
    # Replacement strings are data, never evaluated or recursively expanded.
    return clean_text(TAG_PATTERN.sub(substitute, value), empty=not value, maximum=maximum)


def response_actions(response):
    if response["kind"] == "sequence":
        return response["actions"]
    return [row["action"] for section in response["sections"] for row in section["rows"]]


def references(response):
    return {str(action["rule_id"]) for action in response_actions(response)
            if action["type"] in {"nested_reply", "nested_list"}}


def template_references(response):
    return {str(action["template_id"]) for action in response_actions(response)
            if action["type"] in {"template", "sticker", "shortvideo"}}


def validate_graph(rules, templates, *, require_enabled=False, root_ids=None):
    """Validate every reference, scope, cycle and expanded timing before saving.

    A list selection starts a new sequence. Its paths are still validated for
    cycles and limits, but all ten mutually exclusive options are not added up.
    """
    rules = {str(key): value for key, value in rules.items()}
    templates = {str(key): value for key, value in templates.items()}
    memo = {}

    def walk(identity, stack, *, origin_device=None, enabled=False):
        identity = str(identity)
        rule = rules.get(identity)
        if not rule or rule.get("removed_at"):
            raise ValueError("Uma resposta referenciada não existe mais")
        if identity in stack or len(stack) >= MAX_DEPTH:
            raise ValueError("Referências circulares ou com mais de oito níveis não são permitidas")
        if enabled and not rule["enabled"]:
            raise ValueError("Uma resposta referenciada está inativa")
        own_device = rule.get("device_id")
        if own_device is not None and (origin_device is None or str(own_device) != str(origin_device)):
            raise ValueError("Uma resposta global não pode depender de outro dispositivo")
        cache = (identity, str(origin_device), enabled, len(stack))
        if cache in memo:
            return memo[cache]
        response = rule["response"]
        children = stack + (identity,)

        def action_stats(action):
            kind = action["type"]
            if kind in {"nested_reply", "nested_list"}:
                target = rules.get(str(action["rule_id"]))
                if not target:
                    raise ValueError("Selecione uma resposta existente")
                expected = "list" if kind == "nested_list" else "sequence"
                if target["response"]["kind"] != expected:
                    raise ValueError("A referência não corresponde ao tipo de resposta selecionado")
                count, delay = walk(action["rule_id"], children, origin_device=origin_device, enabled=enabled)
                return count, delay + action["delay_seconds"]
            if kind in {"template", "sticker", "shortvideo"}:
                template = templates.get(str(action["template_id"]))
                if not template or template.get("removed_at"):
                    raise ValueError("Um template referenciado não está disponível")
                required = {"sticker": "sticker", "shortvideo": "ptv"}.get(kind)
                if required and template["kind"] != required:
                    raise ValueError("O template não corresponde ao tipo de mídia selecionado")
            return 1, action["delay_seconds"]

        if response["kind"] == "sequence":
            values = [action_stats(action) for action in response["actions"]]
            result = sum(value[0] for value in values), sum(value[1] for value in values)
        else:
            # Displaying a list ends synchronous traversal. Its row actions
            # are separate entry points, reached only by a verified selection.
            result = 1, 1
        if result[0] > MAX_ACTIONS or result[1] > 30:
            raise ValueError("A sequência expandida deve ter até trinta ações e trinta segundos de atraso")
        memo[cache] = result
        return result

    for identity in root_ids or rules:
        rule = rules[str(identity)]
        walk(identity, (), origin_device=rule.get("device_id"), enabled=require_enabled)
        if rule["response"]["kind"] == "list":
            # A -> menu B -> (new customer selection) -> A is navigation, not
            # recursive execution. Validate each selected path with a fresh
            # stack while still rejecting automatic sequence A -> A cycles.
            for action in response_actions(rule["response"]):
                if action["type"] in {"nested_reply", "nested_list"}:
                    target = rules.get(str(action["rule_id"]))
                    expected = "list" if action["type"] == "nested_list" else "sequence"
                    if not target or target["response"]["kind"] != expected:
                        raise ValueError("A referência não corresponde ao tipo de resposta selecionado")
                    count, delay = walk(action["rule_id"], (), origin_device=rule.get("device_id"), enabled=require_enabled)
                    if count > MAX_ACTIONS or delay + action["delay_seconds"] > 30:
                        raise ValueError("Uma opção expandida ultrapassa trinta ações ou trinta segundos")
                elif action["type"] in {"template", "sticker", "shortvideo"}:
                    template = templates.get(str(action["template_id"]))
                    required = {"sticker": "sticker", "shortvideo": "ptv"}.get(action["type"])
                    if not template or template.get("removed_at") or required and template["kind"] != required:
                        raise ValueError("Um template referenciado não está disponível")
    return memo


def compile_plan(rule_id, rules, templates, facts, http_context, *, selected_action=None, allow_inactive_root=False):
    """Build bounded SDK-neutral actions; tokenized selections are added by SQL."""
    rules = {str(key): value for key, value in rules.items()}
    templates = {str(key): value for key, value in templates.items()}
    root = rules[str(rule_id)]
    if not root["enabled"] and not allow_inactive_root:
        raise ValueError("A resposta está inativa")
    validate_graph(rules, templates, root_ids=[str(rule_id)])
    dependencies = set()

    def walk(identity, stack=()):
        identity = str(identity)
        rule = rules[identity]
        if not rule["enabled"] and not (allow_inactive_root and identity == str(rule_id)):
            raise ValueError("Uma resposta está inativa")
        if identity in stack or len(stack) >= MAX_DEPTH:
            raise ValueError("Referência circular")
        dependencies.add(identity)
        response = rule["response"]
        if response["kind"] == "list":
            result = deepcopy(response)
            result.pop("kind")
            result["type"] = "list"
            for key, maximum in (("title", 60), ("description", 1000), ("footer", 60), ("button_text", 20)):
                result[key] = render_contact(result.get(key, ""), facts, maximum=maximum)
            for section in result["sections"]:
                section["title"] = render_contact(section["title"], facts, maximum=24)
                for row in section["rows"]:
                    row["title"] = render_contact(row["title"], facts, maximum=24)
                    row["description"] = render_contact(row["description"], facts, maximum=72)
            return [{"delay_seconds": 1, "action": result}]
        return [item for action in response["actions"] for item in expand(action, stack + (identity,))]

    def expand(action, stack):
        kind = action["type"]
        delay = action["delay_seconds"]
        if kind in {"nested_reply", "nested_list"}:
            result = walk(action["rule_id"], stack)
            result[0]["delay_seconds"] += delay
            return result
        if kind == "text":
            transport = {"type": "text", "text": render_contact(action["text"], facts)}
        elif kind in {"template", "sticker", "shortvideo"}:
            template = templates[str(action["template_id"])]
            text = render_contact(template.get("body", template.get("text", "")), facts)
            if template["kind"] == "text":
                transport = {"type": "text", "text": text}
            else:
                asset = template.get("asset")
                if not asset:
                    raise ValueError("O arquivo do template está indisponível")
                if text and template["kind"] in {"sticker", "ptv"}:
                    raise ValueError("Esse tipo de mídia não aceita legenda; use uma ação Texto separada")
                if text:
                    text = clean_text(text, maximum=1024)
                transport = {"type": template["kind"], "asset": deepcopy(asset), "caption": text,
                             "file_name": asset["file_name"]}
        elif kind == "url":
            transport = {"type": "http", "url": action["url"], "method": action["method"],
                         "data": deepcopy(http_context), "custom_template": action["custom_template"],
                         "as_file": action["as_file"], "mode": "reply"}
        elif kind == "carousel":
            transport = {"type": "carousel", "body": render_contact(action["body"], facts, maximum=1024),
                         "cards": deepcopy(action["cards"])}
            for card in transport["cards"]:
                for key, maximum in (("title", 60), ("body", 1024), ("button_text", 20)):
                    card[key] = render_contact(card[key], facts, maximum=maximum)
                card.pop("id", None)
        else:
            raise ValueError("Tipo de resposta indisponível")
        return [{"delay_seconds": delay, "action": transport}]

    plan = expand(selected_action, ()) if selected_action else walk(rule_id)
    if not plan or len(plan) > MAX_ACTIONS or sum(item["delay_seconds"] for item in plan) > 30:
        raise ValueError("A sequência expandida ultrapassa os limites")
    return plan, sorted(dependencies | {str(rule_id)})


def clone_response(response):
    result = deepcopy(response)
    if result["kind"] == "sequence":
        actions = result["actions"]
    else:
        actions = []
        for section in result["sections"]:
            section["id"] = str(uuid4())
            for row in section["rows"]:
                row["id"] = str(uuid4())
                actions.append(row["action"])
    for action in actions:
        action["id"] = str(uuid4())
        if action["type"] == "carousel":
            for card in action["cards"]:
                card["id"] = str(uuid4())
    return result


def render_http_result(action, body):
    """A flat scalar tag vocabulary; never eval, nested lookup or recursion."""
    if not isinstance(body, dict):
        raise ValueError("O servidor deve retornar um objeto JSON")
    custom = action.get("custom_template", "")
    if not custom:
        result = body.get("reply")
        if not isinstance(result, str):
            raise ValueError("O servidor deve retornar reply como texto")
    else:
        def substitute(match):
            value = body.get(match[1])
            if value is None or isinstance(value, (dict, list)) or isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Tag ausente ou não escalar: " + match[1])
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError("Tipo de tag inválido")
            return str(value) if not isinstance(value, bool) else "true" if value else "false"
        result = URL_TAG_PATTERN.sub(substitute, custom)
    return clean_text(result, maximum=512000)


def split_text(value):
    parts, current, size = [], [], 0
    for char in value:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > 32000:
            parts.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        parts.append("".join(current))
    if len(parts) > 30:
        raise ValueError("A resposta do servidor ultrapassa trinta mensagens")
    return parts


def summary(rule):
    response = rule["response"]
    if response["kind"] == "list":
        types, count, delay, preview = ["list"], 1, 1, response["description"]
    else:
        actions = response["actions"]
        types = list(dict.fromkeys(action["type"] for action in actions))
        count, delay = len(actions), sum(action["delay_seconds"] for action in actions)
        first = actions[0]
        preview = first.get("text", first.get("body", first.get("url", {
            "template": "Template", "sticker": "Figurinha", "shortvideo": "Recado de vídeo",
            "nested_reply": "Outra resposta", "nested_list": "Lista de opções"}.get(first["type"], ""))))
    return {"response_kind": response["kind"], "response_types": types,
            "preview": preview[:200], "action_count": count, "total_delay_seconds": delay}
