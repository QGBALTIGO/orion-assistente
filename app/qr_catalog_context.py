"""Bounded candidate discovery from authenticated service facts, never authority.

The complete catalog stays in the deterministic planner. Only a relevant window
is sent to the model, so a large business cannot silently lose its later services
or expand every model request to hundreds of entries.
"""
import re

from app.qr_behaviors import norm


MAX_SERVICES = 500
MODEL_SERVICES = 60


def candidates(message, catalog, context=None, *, limit=MODEL_SERVICES):
    if not isinstance(catalog, list) or len(catalog) > MAX_SERVICES:
        raise ValueError("Catalog outside supported bounds")
    if type(limit) is not int or not 1 <= limit <= MODEL_SERVICES:
        raise ValueError("Invalid candidate limit")
    context = context or {}
    required = {str(value) for value in context.get("service_ids", [])}
    available = {str(row["id"]) for row in catalog}
    if not required <= available or len(required) > 6 or len(available) != len(catalog):
        raise ValueError("Selected services outside the current catalog")
    if len(catalog) <= limit:
        return list(catalog)
    text = norm(message)
    words = set(re.findall(r"[a-z0-9]{3,}", text)) - {
        "quero", "queria", "gostaria", "marcar", "agendar", "amanha", "hoje",
        "servico", "servicos", "preco", "quanto", "custa", "para", "com",
    }

    def rank(pair):
        index, row = pair
        names = [norm(row["name"]), *[norm(x) for x in row.get("aliases", [])[:12]]]
        exact = any(name and re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text) for name in names)
        overlap = max((len(words & set(re.findall(r"[a-z0-9]{3,}", name))) for name in names), default=0)
        return (str(row["id"]) not in required, not exact, -overlap, index)

    picked = sorted(enumerate(catalog), key=rank)[:limit]
    # Keep stable catalog order after retrieval, including numeric reply order.
    return [row for _, row in sorted(picked)]
