"""Read-only source index of Orion form definitions; never imports application code.

One emitted row is a source definition, not one DOM instance per customer/service.
The index deliberately keeps dormant and readonly controls separate. It is a
discovery aid: runtime branches, API contracts and visual evidence are reviewed in
docs/fields-standardization-coverage.md. Run against a commit for reproducibility.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "app/static/panel.html", "app/static/orion-suite.js",
    "app/static/orion-onboarding.js", "app/static/orion-public.html",
    "app/static/orion-public.js", "app/static/orion-invite.html",
    "app/static/orion-invite.js", "app/static/orion-channels.js",
    "app/static/orion-conversations.js", "app/static/orion-inbox.js",
    "app/static/orion-automations.js", "app/static/orion-communication.js",
    "app/static/orion-flows.js", "app/static/qr_connect.html",
    "landing/index.html", "landing/assets/orion-landing.js",
]
DORMANT = {"orion-automations.js", "orion-communication.js", "orion-flows.js"}
GENERATORS = {"field", "moneyField", "selectField", "textField", "checkField", "colorField",
              "customerInputField", "imageUploadField", "select", "check"}
HELPERS = GENERATORS | {"imageURL"}
TAG = re.compile(r"<(input|select|textarea)\b[^>]*>", re.I)
ATTR = re.compile(r'([\w:-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')
FUNCTION = re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")


def source(path: str, ref: str | None) -> str:
    if ref:
        return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT, text=True)
    return (ROOT / path).read_text(encoding="utf-8")


def literal(value: str) -> str | None:
    value = value.strip()
    if len(value) < 2 or value[0] not in "'\"" or value[-1] != value[0]:
        return None
    try:
        parsed = ast.literal_eval(value)
        return parsed if isinstance(parsed, str) else None
    except (ValueError, SyntaxError):
        return None


def call_args(text: str, start: int) -> list[str]:
    """Split a helper invocation without evaluating its JavaScript arguments."""
    args, begin, depth, quote, escaped = [], start, 0, None, False
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "'\"`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if char == ")" and depth == 0:
                args.append(text[begin:i].strip())
                return args
            depth -= 1
        elif char == "," and depth == 0:
            args.append(text[begin:i].strip())
            begin = i + 1
    return []


def context(text: str, offset: int) -> str:
    matches = list(FUNCTION.finditer(text, 0, offset))
    return matches[-1].group(1) if matches else "module"


def reachability(path: str, scope: str, locator: str, attrs: str) -> str:
    if Path(path).name in DORMANT or scope == "showLoyalty" or "centralReplySelect" in locator:
        return "dormant"
    if re.search(r"\sreadonly(?:\s|>|=)", attrs, re.I):
        return "readonly"
    if "qr_connect" in path:
        return "technical"
    if locator == "#lead-website":
        return "technical-honeypot"
    if locator == "<unidentified>" and re.search(r"\bdisabled\b", attrs):
        return "disabled-placeholder"
    if "panel.html" in path and scope not in {"loginForm", "registerForm", "verifyForm", "forgotForm", "resetForm"}:
        return "legacy-active"
    return "active"


def row(path: str, line: int, scope: str, locator: str, kind: str, origin: str,
        attrs: str = "", label: str = "", dynamic: bool = False) -> dict:
    return {"file": path, "line": line, "scope": scope, "locator": locator,
            "kind": kind, "origin": origin, "reachability": reachability(path, scope, locator, attrs),
            "label": label, "dynamic_family": dynamic,
            "has_placeholder": "placeholder" in attrs,
            "has_autocomplete": "autocomplete" in attrs,
            "has_describedby": "aria-describedby" in attrs,
            "has_inputmode": "inputmode" in attrs,
            "has_mask_optin": bool(re.search(r"data-(?:os-money-mask|os-professional-phone|orion-field|field-mask)", attrs))}


class StaticHTML(HTMLParser):
    def __init__(self, path: str):
        super().__init__(convert_charrefs=True)
        self.path, self.forms, self.rows, self.section = path, [], [], "page"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.forms.append(attrs.get("id") or attrs.get("data-os-form") or attrs.get("data-ob-form") or "form")
        if tag == "section" and attrs.get("id"):
            self.section = attrs["id"]
        if tag not in {"input", "select", "textarea"}:
            return
        kind = attrs.get("type", "text") if tag == "input" else tag
        if kind in {"hidden", "button", "submit", "reset"}:
            return
        locator = "#" + attrs["id"] if attrs.get("id") else "[name=" + attrs["name"] + "]" if attrs.get("name") else "<unidentified>"
        self.rows.append(row(self.path, self.getpos()[0], self.forms[-1] if self.forms else self.section,
                             locator, kind, "html", self.get_starttag_text()))

    def handle_endtag(self, tag):
        if tag == "form" and self.forms:
            self.forms.pop()


def javascript_rows(path: str, text: str, line_offset: int = 0) -> list[dict]:
    result = []
    is_onboarding = path.endswith("orion-onboarding.js")
    helper_pattern = re.compile(r"\b(" + "|".join(sorted(HELPERS, key=len, reverse=True)) + r")\s*\(")
    for match in helper_pattern.finditer(text):
        helper = match.group(1)
        if helper in {"select", "check"} and not is_onboarding:
            continue
        args = call_args(text, match.end())
        if len(args) < 2:
            continue
        name_index, label_index = (0, 1) if is_onboarding else (1, 0)
        name = literal(args[name_index])
        if not name:
            continue  # Definition of a generic builder, not a consumer field.
        scope = context(text, match.start())
        if scope in GENERATORS:
            continue
        attrs = " ".join(args[3:])
        kind = {"selectField":"select", "select":"select", "textField":"textarea", "checkField":"checkbox",
                "check":"checkbox", "colorField":"color", "imageURL":"file", "imageUploadField":"file"}.get(helper)
        if kind is None:
            kind = literal(args[2]) if not is_onboarding and len(args) > 2 else None
            if is_onboarding:
                found = re.search(r'type=["\']([\w-]+)', attrs)
                kind = found.group(1) if found else "text"
            kind = kind or "text"
        result.append(row(path, line_offset + text.count("\n", 0, match.start()) + 1,
                          scope, f"[name={name}]", kind, "helper:" + helper, attrs, literal(args[label_index]) or "<conditional label>"))
    for match in TAG.finditer(text):
        markup = match.group()
        scope = context(text, match.start())
        if scope in GENERATORS:
            continue
        attrs = {m.group(1): m.group(2) if m.group(2) is not None else m.group(3) for m in ATTR.finditer(markup)}
        kind = attrs.get("type", "text") if match.group(1).lower() == "input" else match.group(1).lower()
        if kind in {"hidden", "button", "submit", "reset"}:
            continue
        boolean_data = re.findall(r"\b(data-[a-z-]+)(?=[\s>])", markup)
        locator = "#" + attrs["id"] if attrs.get("id") else "[name=" + attrs["name"] + "]" if attrs.get("name") else next(("[" + k + "]" for k in attrs if k.startswith("data-")), "[" + boolean_data[0] + "]" if boolean_data else "<unidentified>")
        if locator in {"#'+id+'", "#'+esc(id)+'", "[name='+name+']", "#osField-'+name+'", "#ob-'+esc(name)+'", "[name='+esc(name)+']", "[name='+item.name+']"}:
            continue
        if locator == "<unidentified>" and "readonly" not in markup:
            continue  # An explanatory comment can contain a bare <input> tag.
        result.append(row(path, line_offset + text.count("\n", 0, match.start()) + 1, scope,
                          locator, kind, "js-markup", markup, dynamic="'+" in locator or "${" in locator))
    return result


def manual_dynamic(path: str, text: str) -> list[dict]:
    """DOM builders whose metadata is an array and therefore has no HTML tag."""
    definitions = {}
    if path == "app/static/panel.html":
        definitions = {
        "authorized_staff": [("phone_e164","text"),("role","text"),("active","checkbox")],
        "time_off": [("professional_id","text"),("starts_at","text"),("ends_at","text"),("reason","text")],
        "businessRepeatConfig.hours": [("weekday","select"),("opens","time"),("closes","time")],
        "businessRepeatConfig.faqs": [("question","text"),("answer","textarea")],
        "businessRepeatConfig.policies": [("topic","text"),("description","textarea")],
        "businessRepeatConfig.differentiators": [("text","text")],
        "commerceAddFields.faq": [("question","text"),("answer","textarea")],
        "commerceAddFields.attribute": [("key","text"),("value","text")],
        "commerceRenderBasket": [("quantity","number")],
        }
    if path.endswith("orion-public.js"):
        definitions = {
            "renderSelectedExtras": [(".addon-option input", "checkbox")],
            "renderTime": [("#booking-day-input", "date")],
            "renderConfirmation": [("customer_name", "text"), ("customer_phone", "tel"), ("customer_birth_date", "date")],
        }
    if path.endswith("orion-suite.js"):
        definitions = {
            "marketingAutomations": [("rule_enabled_{event}", "checkbox"), ("rule_template_{event}", "textarea")],
            "siteAmenityControls": [("amenity_wifi", "checkbox"), ("amenity_parking", "checkbox"), ("amenity_accessibility", "checkbox"), ("amenity_children", "checkbox")],
        }
    if path.endswith("orion-automations.js"):
        definitions = {
            "execution": [("daily_limit", "number"), ("enabled", "checkbox"), ("acknowledged", "checkbox")],
            "load-consent": [("phone", "tel")],
            "consent": [("operational_opt_in", "checkbox"), ("marketing_opt_in", "checkbox"), ("birthday_opt_in", "checkbox"), ("evidence", "text")],
            "contact-pause": [("pause_acknowledged", "checkbox")],
            "approve": [("acknowledged", "checkbox")],
        }
    if path.endswith("orion-communication.js"):
        definitions = {"communication-policy": [(name, "number") for name in ["operational_start_hour", "operational_end_hour", "marketing_start_hour", "marketing_end_hour"]]}
    rows = []
    for scope, fields in definitions.items():
        anchor = scope.split(".")[0]
        declaration = re.search(r"\b(?:function\s+" + re.escape(anchor) + r"\s*\(|(?:const|let)\s+" + re.escape(anchor) + r"\s*=)", text)
        index = declaration.start() if declaration else text.find(anchor)
        for name, kind in fields:
            if path.endswith("panel.html") and scope == "time_off" and name in {"starts_at", "ends_at"} and 'input.type="datetime-local"' in text:
                kind = "datetime-local"
            if path.endswith("panel.html") and scope == "authorized_staff" and name == "phone_e164" and 'configureLegacyField(input,"phone")' in text:
                kind = "tel"
            if index < 0:
                index = text.find("'" + name + "'")
            locator = name if name.startswith(("#", ".")) else f"[name={name}]" if "." not in scope and scope != "commerceRenderBasket" else "{" + name + "}"
            rows.append(row(path, text.count("\n", 0, index) + 1, scope,
                            locator,
                            kind, "dom-builder-metadata", dynamic=True))
    return rows


def expand_shared_forms(rows: list[dict]) -> list[dict]:
    """One occurrence per distinct form; repeated services/days remain families."""
    result = []
    for item in rows:
        scopes = [item["scope"]]
        if item["file"].endswith("orion-suite.js") and item["scope"] == "hoursEditorForm":
            scopes = ["hours-edit"] if item["locator"] == "[name=weekday]" else ["hours", "hours-edit", "pause", "pause-edit"]
        if item["file"].endswith("orion-onboarding.js"):
            if item["scope"] == "businessForm":
                scopes = ["create", "business"]
            if item["scope"] == "catalogForm":
                name = item["locator"]
                if name in {"[name=name]", "[name=description]", "[name=active]"}:
                    scopes = ["service", "product"]
                elif name in {"[name=variant_id]", "[name=variant_name]", "[name=price_kind]", "[name=variant_active]"}:
                    scopes = ["product"]
                elif name == "[name=price]":
                    # There is one source call in each distinct price branch.
                    scopes = ["product" if any(previous["file"] == item["file"] and previous["scope"] == "service" and previous["locator"] == name for previous in result) else "service"]
                else:
                    scopes = ["service"]
        for scope in scopes:
            result.append({**item, "scope": scope, "source_scope": item["scope"]})
    return result


AREAS = {
    1: "Acesso — entrar", 2: "Acesso — cadastro e confirmação", 3: "Acesso — recuperação e redefinição",
    4: "Convite de equipe", 5: "Site institucional — contato", 6: "Catálogo e agendamento públicos",
    7: "Clientes", 8: "Profissionais e histórico", 9: "Equipe e permissões", 10: "Serviços e combos",
    11: "Agenda — filtros", 12: "Agenda — novo agendamento", 13: "Agenda — ações e conclusão",
    14: "Agenda — bloqueios e séries", 15: "Financeiro — lançamentos e filtros", 16: "Financeiro — vales e quitação",
    17: "Desempenho e metas", 18: "Meu site — conteúdo, link e endereço", 19: "Configurações — preferências",
    20: "Configurações — horários e pausas", 21: "Configurações — Google Agenda", 22: "Solicitações do site",
    23: "Marketing IA", 24: "Fidelização", 25: "Consultor IA e ajuda",
    26: "Onboarding — negócio e endereço", 27: "Onboarding — profissionais e catálogo",
    28: "Onboarding — jornada, canais e revisão", 29: "Canais WhatsApp", 30: "Central — Conversas",
    31: "Legado acessível — perfil da empresa", 32: "Legado acessível — catálogo e pedidos",
    33: "Legado acessível — WhatsApp e conversa", 34: "Administração, simulação e segredo técnico",
}


def area_for(item: dict) -> str:
    path, scope = item["file"], item["scope"]
    if Path(path).name in DORMANT or scope == "showLoyalty":
        return "Dormente — sem entrada ativa"
    if path.startswith("landing/"):
        return AREAS[5]
    if "orion-public" in path:
        return AREAS[6]
    if "orion-invite" in path:
        return AREAS[4]
    if "orion-channels" in path:
        return AREAS[29]
    if "orion-conversations" in path or "orion-inbox" in path:
        return AREAS[30]
    if "orion-onboarding" in path:
        return AREAS[26 if scope in {"create", "business", "addressForm"} else 27 if scope in {"professionalForm", "service", "product"} else 28]
    if "qr_connect" in path:
        return AREAS[34]
    if "panel.html" in path:
        if scope == "loginForm": return AREAS[1]
        if scope in {"registerForm", "verifyForm"}: return AREAS[2]
        if scope in {"forgotForm", "resetForm"}: return AREAS[3]
        if scope.startswith("business") or scope == "page-empresa": return AREAS[31]
        if scope.startswith(("com", "commerce")) or scope == "page-catalogo": return AREAS[32]
        if scope in {"deviceSettingsForm", "pairForm", "sendForm", "page-conversas"}: return AREAS[33]
        return AREAS[34]
    scope_areas = {
        7: {"showCustomer", "customerNotesField", "renderRoute"},
        8: {"showProfessional", "showProfessionalHistory"},
        9: {"showInvitation", "showTeamMember", "showInvitationResult"},
        10: {"showService", "showPackage", "renderServices"},
        11: {"agendaFrame"}, 12: {"renderAppointmentForm", "renderBookingAddons"},
        13: {"showAppointmentAction", "showCompleteAppointment"}, 14: {"showBlock", "action"},
        15: {"showFinance", "renderFinance", "financeMonthPicker"},
        16: {"showStaffFinance", "showSettlement", "renderAdvances"},
        17: {"showGoal", "periodFilters", "performancePeriodFilters"},
        18: {"siteSlugControls", "siteLocationFields", "sitePreferencesForm", "siteAmenityControls"},
        19: {"businessPreferencesForm", "renderSettings", "taxDocumentField"},
        20: {"hours", "hours-edit", "pause", "pause-edit", "settingsHours"},
        21: {"showGoogleCalendars", "showGoogleEmailVerification"}, 22: {"renderSiteRequests"},
        23: {"marketingAutomations", "marketingGenerator", "showCampaign"},
        24: {"loyaltyConfiguration", "loyaltyIdeas", "showLoyaltyPoints", "renderLoyalty", "loyaltyShare"},
        25: {"renderConsultor", "renderHelp"},
    }
    return next((AREAS[number] for number, scopes in scope_areas.items() if scope in scopes), "Revisar escopo: " + scope)


def form_for(item: dict) -> str:
    scope, path = item["scope"], item["file"]
    if path.endswith("panel.html"):
        if scope in {"authorized_staff", "time_off"}:
            return f"#recordForm / resource={scope}"
        if scope.startswith("businessRepeatConfig."):
            return "#businessForm / " + scope
        if scope == "commerceAddFields.faq":
            return "#comProductForm / commerceAddFields(faq)"
        if scope == "commerceAddFields.attribute":
            return "#comVariantForm / commerceAddFields(attribute)"
        if scope == "commerceRenderBasket":
            return "#comPlanForm / commerceRenderBasket()"
    suite_forms = {
        "showCustomer":"customer", "customerNotesField":"customer", "showProfessional":"professional",
        "showService":"service", "showPackage":"package", "showInvitation":"invitation", "showTeamMember":"team-member",
        "renderAppointmentForm":"appointment", "renderBookingAddons":"appointment", "showAppointmentAction":"appointment-action",
        "showCompleteAppointment":"complete-appointment", "showBlock":"block", "action":"delete-series",
        "showFinance":"finance", "showStaffFinance":"staff-finance", "showSettlement":"settlement", "showGoal":"goal",
        "siteSlugControls":"site", "siteLocationFields":"site", "sitePreferencesForm":"site", "siteAmenityControls":"site",
        "businessPreferencesForm":"business-preferences", "taxDocumentField":"business-preferences", "showGoogleCalendars":"google-calendar", "showGoogleEmailVerification":"google-email",
        "marketingAutomations":"automations", "marketingGenerator":"ai-campaign", "showCampaign":"campaign",
        "loyaltyConfiguration":"loyalty-inline", "loyaltyIdeas":"loyalty-ideas", "showLoyaltyPoints":"loyalty-points",
        "renderConsultor":"ai-consultant", "showLoyalty":"loyalty",
    }
    if path.endswith("orion-suite.js"):
        kind = suite_forms.get(scope, scope if scope in {"hours", "hours-edit", "pause", "pause-edit"} else None)
        return f"[data-os-form={kind}]" if kind else scope + "()"
    if path.endswith("orion-onboarding.js"):
        kind = {"professionalForm":"professional", "addressForm":"location", "hoursForm":"hours", "channelsForm":"channels"}.get(scope,scope)
        return f"[data-ob-form={kind}]"
    if path.endswith("orion-public.js"):
        return "#customer-form" if scope == "renderConfirmation" else "#booking-dialog / " + scope + "()"
    if path.endswith("orion-channels.js"):
        return "[data-qc-form=" + ("create" if scope == "createForm" else "confirm") + "]"
    return "#" + scope if scope.endswith("Form") or scope in {"sales-request-form", "inviteVerifyForm"} else scope


def inventory(ref: str | None) -> dict:
    rows = []
    for path in FILES:
        text = source(path, ref)
        if path.endswith(".html"):
            parser = StaticHTML(path)
            parser.feed(text)
            rows.extend(parser.rows)
            for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", text, re.S | re.I):
                rows.extend(javascript_rows(path, match.group(1), text.count("\n", 0, match.start(1))))
        else:
            rows.extend(javascript_rows(path, text))
        rows.extend(manual_dynamic(path, text))
    rows = expand_shared_forms(rows)
    # Definitions shared by distinct forms are expanded; data-driven repetitions
    # are one family, never multiplied by the number of customers or services.
    for i, item in enumerate(rows, 1):
        item["source_index"] = i
        item["area"] = area_for(item)
        item["form"] = form_for(item)
    summary = {"definitions": len(rows), "reachability": dict(Counter(row["reachability"] for row in rows)),
               "kind": dict(Counter(row["kind"] for row in rows)),
               "files": dict(Counter(row["file"] for row in rows)),
               "active_areas": dict(Counter(row["area"] for row in rows if row["reachability"] in {"active", "legacy-active"}))}
    return {"revision": ref or "working-tree", "method": "source-form-occurrences-v2",
            "limitations": ["Does not execute JavaScript, call APIs or inspect authenticated live DOM.",
                            "Repeated controls are families, not multiplied by runtime data; explicit shared form variants are expanded.",
                            "Dynamic branches require review against the coverage document; source_index is not an ORI ID."],
            "summary": summary, "fields": rows}


def markdown_inventory(data: dict) -> str:
    """Reviewable field references; generated counts never imply test coverage."""
    lines = ["## Índice de campos por área", "", "Gerado por `scripts/inventory_fields.py`, método `" + data["method"] + "`, revisão `" + data["revision"] + "`.", "",
             "Uma família repetida por serviço, cliente, adicional ou dia aparece uma vez. Formulários distintos que compartilham um construtor aparecem separadamente.", ""]
    active = [item for item in data["fields"] if item["reachability"] in {"active", "legacy-active"}]
    for number, area in AREAS.items():
        items = [item for item in active if item["area"] == area]
        lines.extend([f"### {number}. {area} — {len(items)} ocorrências", "", "| Arquivo / escopo do formulário | Campos / famílias (tipo HTML na fonte) |", "| --- | --- |"])
        grouped = defaultdict(list)
        for item in items:
            grouped[(item["file"],item["form"])].append(item)
        for (path, form), records in grouped.items():
            fields = "; ".join("`" + item["locator"].replace("|", "\\|") + "` (" + item["kind"] + ")" for item in records)
            lines.append(f"| `{path}` — `{form}` | {fields} |")
        lines.append("")
    lines.extend(["### Controles fora do total preenchível ativo", "", "| Estado | Arquivo / escopo | Campos |", "| --- | --- | --- |"])
    grouped = defaultdict(list)
    for item in data["fields"]:
        if item["reachability"] not in {"active", "legacy-active"}:
            grouped[(item["reachability"],item["file"],item["form"])].append(item)
    for (state, path, form), items in grouped.items():
        fields = "; ".join("`" + item["locator"].replace("|", "\\|") + "` (" + item["kind"] + ")" for item in items)
        lines.append(f"| {state} | `{path}` — `{form}` | {fields} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="Existing git revision; default reads the working tree")
    parser.add_argument("--output", type=Path, help="Write JSON to this path; default prints JSON")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Machine-readable JSON or reviewable Markdown table")
    args = parser.parse_args()
    index = inventory(args.ref)
    data = markdown_inventory(index) if args.format == "markdown" else json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")
