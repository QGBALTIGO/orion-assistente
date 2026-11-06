"""Pure footer composition before a new WhatsApp payload is frozen."""
from copy import deepcopy
from string import Formatter


class FooterTooLong(ValueError):
    """The complete message and footer do not fit the original wire format."""


def units(value: str) -> int:
    if not isinstance(value, str):
        raise ValueError("Texto inválido")
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise ValueError("Texto inválido") from None


def spaced_footer(body: str, footer: str, *, maximum: int) -> tuple[str, str]:
    """Keep authored content, moving its explicit footer behind one blank line.

    Clearing the structured footer makes composition idempotent. A full body is
    rejected rather than truncated, silently downgraded or returned to a native
    footer whose spacing is controlled by the recipient's WhatsApp client.
    """
    units(body)
    units(footer)
    if type(maximum) is not int or maximum < 1:
        raise ValueError("Limite de texto inválido")
    if not footer:
        return body, ""
    combined = body.rstrip() + "\n\n" + footer.lstrip()
    if units(combined) > maximum:
        raise FooterTooLong("A mensagem completa com rodapé ultrapassa o limite deste formato")
    return combined, ""


def action_footer(action: dict) -> dict:
    """Copy only supported explicit footer fields; preserve every action ID."""
    result = deepcopy(action)
    field = "description" if result.get("type") == "list" else "text"
    maximum = 1000 if result.get("type") == "list" else 1024
    if result.get("type") not in {"list", "reply_buttons", "url_button"} or not result.get("footer"):
        return result
    if units(result["footer"]) > 60:
        raise ValueError("O rodapé deve ter até 60 caracteres")
    result[field], result["footer"] = spaced_footer(result[field], result["footer"], maximum=maximum)
    return result


def footer_template(template: str, *, name: str, transcript: str, footer: str) -> str:
    """Render the existing three-tag template, spacing only real footer tags.

    Literal/escaped braces and the configured order remain data. Never search
    for the footer's text inside a transcript or interpret a new template.
    """
    values = {"name": name, "transcript": transcript, "footer": footer}
    rendered = ""
    for literal, field, spec, conversion in Formatter().parse(template):
        rendered += literal
        if field is None:
            continue
        if field not in values or spec or conversion:
            raise ValueError("Modelo de transcrição inválido")
        value = values[field]
        units(value)
        if field == "footer" and value:
            rendered = rendered.rstrip() + "\n\n" + value.lstrip()
        else:
            rendered += value
    return rendered.strip()
