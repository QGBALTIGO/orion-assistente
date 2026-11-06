"""Bounded JSON paths shared by URL discovery and webhook response rendering.

Paths are dictionary keys and array indexes only. No expression evaluation,
attribute access, wildcard expansion, implicit flattening or recursive templates.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache

MAX_TAGS = 128
MAX_PATH_UNITS = 512
MAX_DEPTH = 16
MAX_NODES = 8192
MAX_BYTES = 512 * 1024
MAX_SAFE_INTEGER = 2**53 - 1
IDENTIFIER = r"[a-zA-Z_][a-zA-Z0-9_]{0,79}"
_IDENT = re.compile(IDENTIFIER)
_INDEX = re.compile(r"0|[1-9][0-9]{0,3}")
URL_TAG_PATTERN = re.compile(r"\{\{([^{}]*)\}\}")
_RESERVED = frozenset({"__proto__", "prototype", "constructor"})


def _units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _key(value: str) -> bool:
    return (bool(value) and len(value) <= 2048 and value not in _RESERVED
            and not re.search(r"[\x00-\x1f\x7f{}\ud800-\udfff]", value))


@lru_cache(maxsize=512)
def parse_path(path: str) -> tuple[str | int, ...]:
    if not isinstance(path, str) or not path or _units(path) > MAX_PATH_UNITS:
        raise ValueError("Caminho de tag inválido")
    parts, position = [], 0
    decoder = json.JSONDecoder()
    while position < len(path):
        if len(parts) >= MAX_DEPTH:
            raise ValueError("A tag ultrapassa o limite de níveis")
        if parts and path[position] == ".":
            position += 1
            match = _IDENT.match(path, position)
            if not match:
                raise ValueError("Caminho de tag inválido")
            value, position = match[0], match.end()
        elif path[position] == "[":
            position += 1
            if position < len(path) and path[position] == '"':
                try:
                    value, position = decoder.raw_decode(path, position)
                except (ValueError, RecursionError):
                    raise ValueError("Caminho de tag inválido") from None
                if not isinstance(value, str):
                    raise ValueError("Caminho de tag inválido")
            else:
                match = _INDEX.match(path, position)
                if not match:
                    raise ValueError("Índice de tag inválido")
                value, position = int(match[0]), match.end()
                if value >= MAX_NODES:
                    raise ValueError("Índice de tag inválido")
            if position >= len(path) or path[position] != "]":
                raise ValueError("Caminho de tag inválido")
            position += 1
        elif not parts:
            match = _IDENT.match(path, position)
            if not match:
                raise ValueError("Caminho de tag inválido")
            value, position = match[0], match.end()
        else:
            raise ValueError("Caminho de tag inválido")
        if isinstance(value, str) and not _key(value):
            raise ValueError("Chave de tag inválida")
        parts.append(value)
    return tuple(parts)


def validate_template(value: str) -> str:
    for match in URL_TAG_PATTERN.finditer(value):
        parse_path(match[1].strip())
    if re.search(r"[{}]", URL_TAG_PATTERN.sub("", value)):
        raise ValueError("Use tags como {{campo}}, {{data.nome}} ou {{itens[0].nome}}")
    return value


def validate_json(value):
    """Same structural/number bounds as the JavaScript transport and jsonb."""
    if not isinstance(value, (dict, list)):
        raise ValueError("A resposta deve ser um objeto ou lista JSON")
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Resposta JSON acima do limite")
        if isinstance(item, dict):
            for key, child in item.items():
                if (not isinstance(key, str) or len(key) > 2048
                        or re.search(r"[\x00\ud800-\udfff]", key)):
                    raise ValueError("Chave JSON inválida")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if re.search(r"[\x00\ud800-\udfff]", item):
                raise ValueError("Texto JSON inválido")
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            if (isinstance(item, float) and not math.isfinite(item)
                    or (isinstance(item, int) or item.is_integer()) and abs(item) > MAX_SAFE_INTEGER):
                raise ValueError("Número JSON inválido; envie identificadores grandes como texto")
        elif item is not None and not isinstance(item, bool):
            raise ValueError("Tipo JSON inválido")
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_BYTES:
        raise ValueError("Resposta JSON acima do limite")
    return value


def discover_tags(value, *, include_containers: bool = False) -> list[str]:
    validate_json(value)
    tags = []
    containers = []

    def walk(item, path):
        # A complete JSON list/object is useful as a BotBot-style {{data}}
        # tag. Keep old scalar-only discovery for existing callers.
        if include_containers and path and isinstance(item, (dict, list)) and item:
            containers.append(path)
        if isinstance(item, dict):
            for key, child in item.items():
                if not _key(key):
                    continue
                suffix = (("." if path else "") + key if _IDENT.fullmatch(key)
                          else "[" + json.dumps(key, ensure_ascii=False) + "]")
                candidate = path + suffix
                if _units(candidate) <= MAX_PATH_UNITS:
                    walk(child, candidate)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                candidate = path + "[" + str(index) + "]"
                if _units(candidate) <= MAX_PATH_UNITS:
                    walk(child, candidate)
        elif item is not None:
            parse_path(path)
            tags.append(path)
            if len(tags) > MAX_TAGS:
                raise ValueError("A resposta ultrapassa 128 tags")

    walk(value, "")
    if include_containers:
        # Scalar paths keep their original order. Container aliases are
        # additional, bounded and do not make large responses fail by themselves.
        for path in sorted(set(containers), key=lambda item: (item.count(".") + item.count("["), item)):
            if len(tags) >= MAX_TAGS:
                break
            if path not in tags:
                parse_path(path)
                tags.append(path)
    return tags


def resolve_path(body, path: str):
    value = body
    for segment in parse_path(path):
        if isinstance(segment, int):
            if not isinstance(value, list) or segment >= len(value):
                raise ValueError("Tag ausente na resposta")
            value = value[segment]
        else:
            if not isinstance(value, dict) or segment not in value:
                raise ValueError("Tag ausente na resposta")
            value = value[segment]
    if value is None or isinstance(value, (dict, list)) and not value:
        raise ValueError("A tag deve apontar para um valor não nulo e não vazio")
    if isinstance(value, (dict, list)):
        # Exact, bounded JSON serialization; never execute or reinterpret it.
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def render_template(template: str, body, *, maximum=512000) -> str:
    validate_json(body)
    validate_template(template)
    result, cursor, budget = [], 0, maximum
    # Bound expansion before allocation: repeated tags cannot amplify one
    # provider response into gigabytes. Returned values are never re-parsed.
    for match in URL_TAG_PATTERN.finditer(template):
        for part in (template[cursor:match.start()], resolve_path(body, match[1].strip())):
            budget -= _units(part)
            if budget < 0:
                raise ValueError("Resposta expandida acima do limite")
            result.append(part)
        cursor = match.end()
    tail = template[cursor:]
    if _units(tail) > budget:
        raise ValueError("Resposta expandida acima do limite")
    result.append(tail)
    return "".join(result)
