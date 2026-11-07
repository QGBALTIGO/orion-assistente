"""Conservative BotBot ZIP reply converter; all imported rules remain inactive.

No external links are fetched, media is not executed, and unsupported actions
raise an explicit error rather than silently dropping behaviour.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import re
from pathlib import PurePosixPath
import stat
from uuid import UUID, uuid5
import zipfile

MAX_FILES = 512
MAX_EXPANDED = 40 * 1024 * 1024
MAX_MANIFEST = 10 * 1024 * 1024
NAMESPACE = UUID("741d70f4-b6f9-4d2e-b604-a9b169fe35b1")
FALLBACK_DATE = "2000-01-01T00:00:00+00:00"


def _invalid(message: str):
    return ValueError(message)


def _bounded_json(raw: bytes):
    def distinct(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise _invalid("O arquivo JSON contém campos duplicados.")
            result[name] = value
        return result

    def nonfinite(_value):
        raise _invalid("O arquivo JSON contém número inválido.")

    try:
        root = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=distinct,
                          parse_constant=nonfinite)
        nodes, stack = 0, [(root, 0)]
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > 200_000 or depth > 32:
                raise _invalid("O JSON do BotBot ultrapassa o limite de estrutura.")
            if isinstance(value, dict):
                stack.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)
        return root
    except (UnicodeDecodeError, RecursionError):
        raise _invalid("O JSON de respostas do ZIP é inválido.") from None


_KEYWORDS = ("keyword", "keywords", "trigger", "command", "phrase", "word", "match_text", "matchText")
_CONTENT = ("response", "messages", "actions", "replies", "reply", "message", "content", "text", "answer")
_WRAPPERS = ("replies", "reply", "responses", "rules", "rule", "items",
             "chatbot_replies", "records", "data", "result", "export",
             "chatbot", "backup", "payload")


def _keyword_field(row):
    for key in _KEYWORDS:
        if key in row and row[key] is not None:
            value = row[key]
            if isinstance(value, dict):
                value = value.get("keyword", value.get("value", value.get("text")))
            return value
    return None


def _looks_like_rule(value):
    return isinstance(value, dict) and _keyword_field(value) is not None and any(
        key in value for key in _CONTENT
    )


def _records(value, depth=0):
    """Detect reply collections across BotBot's wrapped and split-JSON exports.

    Never accept a generic list of arbitrary JSON dictionaries. A response must
    contain an explicit trigger and payload before entering the converter.
    """
    if depth > 8:
        return None
    if isinstance(value, list):
        if value and all(_looks_like_rule(row) for row in value):
            return value
        return None
    if not isinstance(value, dict):
        return None
    if _looks_like_rule(value):
        return [value]
    if value and all(isinstance(key, str) and key.isdigit() and _looks_like_rule(child)
                     for key, child in value.items()):
        return list(value.values())
    for key in _WRAPPERS:
        if key in value:
            found = _records(value[key], depth + 1)
            if found:
                return found
    return None


def _source_id(row, index):
    raw = row.get("id", row.get("reply_id", row.get("replyId")))
    if not isinstance(raw, (str, int)) or isinstance(raw, bool) or not str(raw):
        raw = f'position:{index}:{row.get("keyword", row.get("trigger", ""))}'
    return str(raw)


def _identity(source_id, suffix):
    return str(uuid5(NAMESPACE, source_id + ":" + suffix))


def _delay(value, *, unit="seconds"):
    """Convert a BotBot time into whole seconds without rounding or truncation.

    Explicit millisecond fields are authoritative. The legacy 'delay' field
    uses seconds for small values and milliseconds for values >= 1000, as
    supported by the source export. Other fields never infer units.
    """
    if value is None:
        return 1
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 16 or not re.fullmatch(r"[0-9]+(?:\.0+)?", value):
            raise _invalid("O atraso da resposta precisa ser um número inteiro.")
        value = float(value)
    if type(value) not in (int, float) or not 0 <= value or value != int(value):
        raise _invalid("O atraso da resposta precisa ser um número inteiro.")
    if unit == "milliseconds" or unit == "auto" and value >= 1000:
        if int(value) % 1000:
            raise _invalid("O atraso em milissegundos não corresponde a segundos inteiros.")
        value //= 1000
    if value > 300:
        raise _invalid("O atraso de uma mensagem ultrapassa 300 segundos (5 minutos).")
    # Orion uses a one-second minimum; preserve zero-delay as the legacy
    # one-second floor rather than introducing unsupported fractional sends.
    return max(1, int(value))


def _action_delay(row):
    for key, unit in (
        ("delay_seconds", "seconds"), ("delaySeconds", "seconds"),
        ("delay_ms", "milliseconds"), ("delayMs", "milliseconds"),
        ("delayMilliseconds", "milliseconds"), ("delay", "auto"),
    ):
        if key in row and row[key] is not None:
            return _delay(row[key], unit=unit)
    return 1


def _message_text(value, depth=0):
    """Find an explicit, nonblank text payload in common BotBot wrappers.

    Exported reply records can contain an empty legacy `text` field
    alongside a populated `message`, `body` or nested content field.
    Accept those without inventing text or flattening non-text actions.
    """
    if isinstance(value, str):
        return value if value.strip() else None
    if not isinstance(value, dict) or depth >= 4:
        return None
    for field in ("text", "message", "body", "content", "reply", "answer",
                  "value", "payload", "data"):
        if field in value:
            candidate = value[field]
            if isinstance(candidate, (str, dict)):
                found = _message_text(candidate, depth + 1)
                if found is not None:
                    return found
    return None


def _content_present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)) and not value:
        return False
    if isinstance(value, dict):
        kind = str(value.get("type", value.get("kind", ""))).lower()
        if kind in ("text", "texto", "message", "send_text") and _message_text(value) is None:
            return False
        if kind in ("sequence", "messages") and not value.get(
                "actions", value.get("messages")):
            return False
    return True


def _action(row, name, references):
    if isinstance(row, str):
        row = {"type": "text", "text": row}
    if not isinstance(row, dict):
        raise _invalid("A exportação contém uma mensagem que não pode ser convertida.")
    kind = str(row.get("type", row.get("kind", "text"))).lower()
    if kind in ("message", "send_text", "text", "texto"):
        value = _message_text(row)
        if value is None:
            raise _invalid("Uma mensagem está sem texto. Confira o arquivo de origem.")
        definition = {"type": "text", "text": value}
    elif kind in ("url", "http", "webhook", "external_request"):
        url = row.get("url", row.get("endpoint"))
        if not isinstance(url, str):
            raise _invalid("Uma ação de URL não possui endpoint válido.")
        method = str(row.get("method", "POST")).upper()
        if method not in ("GET", "POST"):
            raise _invalid("Uma ação de URL usa método não suportado.")
        custom = row.get("custom_template", row.get("customTemplate", row.get("template", "")))
        if not isinstance(custom, str):
            raise _invalid("A resposta personalizada da URL não é texto.")
        definition = {"type": "url", "url": url, "method": method,
                      "custom_template": custom, "as_file": False}
    elif kind in ("url_button", "button_url", "link_button", "urlbutton"):
        # Preserve each supplied field; the strict schema handles HTTPS and
        # the allowed contact tags without making an external request.
        definition = {
            "type": "url_button",
            "text": _message_text(row),
            "footer": row.get("footer") or "",
            "button_text": row.get("button_text") or row.get("buttonText") or row.get("label"),
            "url": row.get("url", row.get("link")),
        }
    elif kind in ("carousel", "cards"):
        cards = row.get("cards")
        if not isinstance(cards, list) or len(cards) != 3:
            raise _invalid("Um carrossel precisa ter exatamente três cards para ser preservado.")
        definition = {"type": "carousel", "body": row.get("body") or _message_text(row),
                      "cards": []}
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                raise _invalid("Um carrossel contém card em formato incompatível.")
            definition["cards"].append({
                "id": _identity(name, f"card:{index}"),
                "title": card.get("title") or card.get("name"),
                "body": card.get("body") or card.get("description") or "",
                "image_url": card.get("image_url", card.get("imageUrl")),
                "button_text": card.get("button_text") or card.get("buttonText"),
            })
    elif kind in ("nested_reply", "nested_list", "send_reply"):
        raw = row.get("rule_id", row.get("reply_id", row.get("replyId")))
        if raw is None or str(raw) not in references:
            raise _invalid("Um menu faz referência a uma resposta ausente da exportação.")
        definition = {"type": "nested_reply" if kind == "send_reply" else kind,
                      "rule_id": references[str(raw)]}
    else:
        raise _invalid("O arquivo contém uma ação que precisa de adaptação antes de importar.")
    definition["id"] = _identity(name, "action")
    definition["delay_seconds"] = _action_delay(row)
    return definition


def _response(raw, source_id, references):
    if isinstance(raw, str):
        actions = [raw]
    elif isinstance(raw, list):
        actions = raw
    elif isinstance(raw, dict):
        kind = raw.get("kind", raw.get("type"))
        if kind in ("list", "menu"):
            sections = raw.get("sections", raw.get("lists"))
            if not isinstance(sections, list) or not sections:
                raise _invalid("Uma lista de opções não contém seções compatíveis.")
            parsed_sections = []
            for i, section in enumerate(sections):
                if not isinstance(section, dict) or not isinstance(section.get("rows", section.get("options")), list):
                    raise _invalid("Uma seção de lista contém opções incompatíveis.")
                rows = []
                for j, row in enumerate(section.get("rows", section.get("options"))):
                    if not isinstance(row, dict):
                        raise _invalid("Uma opção de lista não é válida.")
                    value = row.get("action", row.get("response", row.get("message")))
                    title = row.get("title", row.get("label"))
                    description = row.get("description") or ""
                    rows.append({"id": _identity(source_id, f"row:{i}:{j}"),
                                 "title": title, "description": description,
                                 "action": _action(value, f"{source_id}:row:{i}:{j}", references)})
                parsed_sections.append({"id": _identity(source_id, f"section:{i}"),
                                       "title": section.get("title", ""), "rows": rows})
            # The BotBot export can omit display labels which Orion requires.
            # Use neutral UI defaults without modifying choices or actions.
            return {"kind": "list", "title": raw.get("title") or "Opções",
                    "description": raw.get("description") or "Escolha uma opção.",
                    "button_text": raw.get("button_text", raw.get("buttonText")) or "Ver opções",
                    "footer": raw.get("footer") or "", "sections": parsed_sections}
        if kind in ("sequence", "messages") or "actions" in raw or "messages" in raw:
            actions = raw.get("actions", raw.get("messages"))
        else:
            actions = [raw]
    else:
        raise _invalid("Uma resposta não contém mensagens reconhecidas.")
    if not isinstance(actions, list) or not actions or len(actions) > 30:
        raise _invalid("A sequência de mensagens está vazia ou excede o limite do Orion.")
    result = {"kind": "sequence", "actions": [
        _action(value, f"{source_id}:{index}", references)
        for index, value in enumerate(actions)]}
    if sum(action["delay_seconds"] for action in result["actions"]) > 300:
        raise _invalid("A sequência supera 300 segundos de atraso e precisa ser ajustada.")
    return result


def read_botbot(raw: bytes, filename: str):
    if not isinstance(raw, bytes) or len(raw) > 10 * 1024 * 1024 or not filename.lower().startswith("botbot_replies_"):
        raise _invalid("Selecione o ZIP original exportado em Respostas no BotBot.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_FILES or sum(i.file_size for i in infos) > MAX_EXPANDED:
                raise _invalid("O ZIP do BotBot contém arquivos demais ou excede o limite permitido.")
            candidates = []
            observed = set()
            for info in infos:
                path = PurePosixPath(info.filename)
                if (info.filename in observed or info.is_dir() or info.filename.startswith("/")
                        or "\\" in info.filename or ".." in path.parts or info.flag_bits & 1
                        or stat.S_IFMT(info.external_attr >> 16) not in (0, stat.S_IFREG)):
                    raise _invalid("O ZIP contém caminhos ou arquivos não permitidos.")
                observed.add(info.filename)
                if not info.filename.lower().endswith(".json"):
                    continue
                if info.file_size <= 0 or info.file_size > MAX_MANIFEST:
                    raise _invalid("O JSON de respostas do BotBot ultrapassa o limite.")
                with archive.open(info) as stream:
                    blob = stream.read(MAX_MANIFEST + 1)
                if len(blob) > MAX_MANIFEST:
                    raise _invalid("O JSON de respostas do BotBot ultrapassa o limite.")
                try:
                    value = _bounded_json(blob)
                except ValueError:
                    raise _invalid("O arquivo de respostas do ZIP não é JSON válido ou excede os limites.") from None
                records = _records(value)
                if records is not None:
                    candidates.append((info.filename, value, records))
    except (zipfile.BadZipFile, RuntimeError, OSError, EOFError):
        raise _invalid("O ZIP exportado pelo BotBot está incompleto ou corrompido.") from None
    if not candidates:
        raise _invalid(
            "Não foi encontrada uma estrutura de respostas reconhecida no ZIP do BotBot. "
            "Nenhuma resposta foi importada; confira o arquivo de exportação."
        )
    # Support one JSON manifest and exports containing one reply per JSON file.
    # Duplicate records must agree exactly; never let an index entry silently
    # overwrite a fuller definition from another file.
    records, signatures = [], {}
    manifest = candidates[0][1]
    for filename, source, rows in candidates:
        for index, row in enumerate(rows):
            raw_id = row.get("id", row.get("reply_id", row.get("replyId")))
            signature = json.dumps(row, sort_keys=True, ensure_ascii=False)
            key = ("id:" + str(raw_id) if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool)
                   else "content:" + signature)
            previous = signatures.get(key)
            if previous is not None:
                if previous != signature:
                    raise _invalid("O ZIP possui versões conflitantes da mesma resposta.")
                continue
            signatures[key] = signature
            records.append(row)
    if not records:
        raise _invalid("O ZIP do BotBot não contém respostas importáveis.")
    if len(records) > 2000:
        raise _invalid("A exportação ultrapassa o limite de respostas do Orion.")
    source_keys = [_source_id(row, index) for index, row in enumerate(records)]
    if len(set(source_keys)) != len(source_keys):
        raise _invalid("Existem identificadores de respostas duplicados no arquivo.")
    references = {key: _identity(key, "rule") for key in source_keys}
    definitions, positions, conversion_issues = [], [], []
    # Error messages are a closed set from the converter. Never echo source
    # text, URLs, keys or untrusted exception strings in the import dialog.
    allowed_errors = frozenset({
        "A exportação contém uma mensagem que não pode ser convertida.",
        "Uma mensagem está sem texto. Confira o arquivo de origem.",
        "Uma ação de URL não possui endpoint válido.",
        "Uma ação de URL usa método não suportado.",
        "A resposta personalizada da URL não é texto.",
        "Um menu faz referência a uma resposta ausente da exportação.",
        "O arquivo contém uma ação que precisa de adaptação antes de importar.",
        "O atraso da resposta precisa ser um número inteiro.",
        "O atraso em milissegundos não corresponde a segundos inteiros.",
        "O atraso de uma mensagem ultrapassa 300 segundos (5 minutos).",
        "Uma lista de opções não contém seções compatíveis.",
        "Uma seção de lista contém opções incompatíveis.",
        "Uma opção de lista não é válida.",
        "Uma resposta não contém mensagens reconhecidas.",
        "A sequência de mensagens está vazia ou excede o limite do Orion.",
        "A sequência supera 300 segundos de atraso e precisa ser ajustada.",
        "Um carrossel precisa ter exatamente três cards para ser preservado.",
        "Um carrossel contém card em formato incompatível.",
    })
    for index, row in enumerate(records):
        number = index + 1
        if not isinstance(row, dict):
            conversion_issues.append(f"Resposta {number}: registro inválido")
            continue
        keywords = _keyword_field(row)
        if isinstance(keywords, list) and all(isinstance(v, str) for v in keywords):
            keyword = ",".join(keywords)
            mode = "array"
        else:
            keyword = keywords
            mode = str(row.get("match_mode", row.get("matchMode", row.get("match_type", row.get("match", "equal"))))).lower()
        if mode not in ("equal", "like", "array"):
            conversion_issues.append(f"Resposta {number}: correspondência incompatível")
            continue
        if not isinstance(keyword, str) or not keyword.strip():
            conversion_issues.append(f"Resposta {number}: palavra-chave ausente")
            continue
        raw_response = next((row[key] for key in _CONTENT if key in row
                             and _content_present(row[key])), None)
        if raw_response is None:
            conversion_issues.append(f"Resposta {number}: não contém mensagens que possam ser importadas")
            continue
        try:
            converted_response = _response(raw_response, source_keys[index], references)
        except ValueError as error:
            message = str(error)
            if message not in allowed_errors:
                message = "tipo de resposta incompatível"
            conversion_issues.append(f"Resposta {number}: {message}")
            continue
        definitions.append({
            "id": references[source_keys[index]], "creation_order": number,
            "definition": {"keyword": keyword, "match_mode": mode,
                           "device_id": None, "enabled": False,
                           "response": converted_response}
        })
        positions.append(number)
    exported_at = FALLBACK_DATE
    if isinstance(manifest, dict) and isinstance(manifest.get("exported_at"), str):
        try:
            date = datetime.fromisoformat(manifest["exported_at"])
            if date.tzinfo is not None and date.utcoffset() is not None:
                exported_at = date.isoformat()
        except ValueError:
            pass
    from app.subscription_chatbot_botbot_preflight import validate_imported_rules
    return {"format": "orion.subscription-chatbot", "version": 1,
            "exported_at": exported_at,
            "rules": validate_imported_rules(definitions, positions=positions,
                                            conversion_issues=conversion_issues),
            "templates": [], "assets": []}
