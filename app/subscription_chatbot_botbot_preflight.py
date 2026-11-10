"""Complete preflight for external BotBot exports without leaking their contents.

The strict Orion codec still checks the entire normalized graph afterwards.
This module only reports schema-level problems before the preview starts.
"""
from __future__ import annotations

from pydantic import ValidationError


def _safe_reason(issue):
    """Only use field paths and error types, never messages or untrusted values."""
    loc = issue.get("loc", ())
    names = [part for part in loc if isinstance(part, str)]
    position = ""
    for field, label in (("actions", "mensagem"), ("rows", "opção"), ("sections", "seção")):
        for index, part in enumerate(loc):
            if part == field and index + 1 < len(loc) and type(loc[index + 1]) is int:
                position = f"{label} {loc[index + 1] + 1}: "
    name = ("texto" if "text" in names else
            "modelo da URL" if "custom_template" in names else
            "URL" if "url" in names or "image_url" in names else
            "atraso" if "delay_seconds" in names else
            "palavra-chave" if "keyword" in names else
            "título" if "title" in names else
            "descrição" if "description" in names else
            "botão" if "button_text" in names else
            "referência" if "rule_id" in names or "template_id" in names else
            "formato")
    kind = issue.get("type", "")
    if kind == "missing":
        reason = "obrigatório ausente"
    elif kind in {"string_too_long", "too_long", "greater_than", "less_than_equal",
                  "too_short", "string_too_short"}:
        reason = "fora do limite"
    elif kind in {"literal_error", "union_tag_invalid", "union_tag_not_found"}:
        reason = "tipo incompatível"
    elif kind == "value_error" and name in {"texto", "modelo da URL"}:
        reason = "conteúdo ou tags incompatíveis"
    elif kind == "value_error" and name == "URL":
        reason = "endereço não aceito"
    else:
        reason = "valor incompatível"
    return position + name + " " + reason


def validate_imported_rules(definitions, *, positions=None, conversion_issues=()):
    """Collect every record's validation problems; apply nothing if any fail."""
    from app import subscription_chatbot_schemas as schemas
    from app import subscription_chatbot_rules as rules

    problems = list(conversion_issues)
    for sequence, record in enumerate(definitions, 1):
        position = positions[sequence - 1] if positions is not None else sequence
        try:
            checked = schemas.RuleDefinition.model_validate(record["definition"])
            record["definition"] = checked.model_dump(mode="json")
        except ValidationError as error:
            reasons = list(dict.fromkeys(_safe_reason(item) for item in error.errors()))
            problems.append(f"Resposta {position}: {', '.join(reasons[:2])}")
        except (ValueError, TypeError, OverflowError):
            problems.append(f"Resposta {position}: formato incompatível")

    # Check graph relationships only once all definitions have valid shapes.
    if not problems:
        graph = {record["id"]: {"id": record["id"],
                              "creation_order": record["creation_order"],
                              **record["definition"]} for record in definitions}
        try:
            rules.validate_graph(graph, {})
        except (ValueError, KeyError, TypeError, RecursionError):
            for sequence, record in enumerate(definitions, 1):
                position = positions[sequence - 1] if positions is not None else sequence
                try:
                    rules.validate_graph(graph, {}, root_ids=[record["id"]])
                except (ValueError, KeyError, TypeError, RecursionError):
                    problems.append(f"Resposta {position}: menu ou referência incompatível")
                if len(problems) >= 12:
                    break

    if problems:
        details = "; ".join(problems[:5])
        extra = f"; +{len(problems) - 5} outras" if len(problems) > 5 else ""
        raise ValueError((f"{len(problems)} resposta(s) precisam de ajuste: "
                          + details + extra)[:235])
    return definitions
