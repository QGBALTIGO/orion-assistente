"""Pure Chatbot contracts and execution boundaries; no network, DB or WhatsApp.

The tests express Orion's documented choices where the reference could not be
exercised, including deterministic precedence and user-gated list navigation.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
import pytest

from app import subscription_chatbot_rules as engine
from app.subscription_chatbot_schemas import (
    RuleDefinition, SequenceResponse, ListResponse, TextAction, UrlAction,
    CarouselAction, TemplateDefinition, CreateRule, ImportPreview, RestorePreview, ExportSelection,
)

CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_chatbot_contract.json").read_text())
FACTS = {"name": "Cliente sintético", "phone_number": "5511999999999",
         "my_name": "Negócio sintético", "my_email": "owner@example.invalid",
         "my_contact_number": "5511900000000"}
CONTEXT = {"name": FACTS["name"], "phone": FACTS["phone_number"], "message": "Olá"}
DEVICE = "20000000-0000-4000-8000-000000000001"
OTHER_DEVICE = "20000000-0000-4000-8000-000000000002"


def identity(index):
    return f"30000000-0000-4000-8000-{index:012d}"


def action(kind="text", **extra):
    value = deepcopy(CONTRACT["actions"][kind])
    value["id"] = str(uuid4())
    value.update(extra)
    return value


def sequence(*actions):
    return {"kind": "sequence", "actions": list(actions) or [action()]}


def menu(*actions):
    value = deepcopy(CONTRACT["list_definition"])
    value["sections"][0]["rows"] = [{"id": str(uuid4()), "title": f"Opção {index + 1}",
        "description": "", "action": row} for index, row in enumerate(actions or [action()])]
    return value


def rule(index=1, response=None, **extra):
    definition = RuleDefinition.model_validate({"keyword": "Olá", "response": response or sequence(), **extra})
    return {**definition.model_dump(mode="json"), "id": identity(index), "creation_order": index,
            "revision": 1, "created_at": "2026-11-04T00:00:00Z", "updated_at": "2026-11-04T00:00:00Z"}


def graph(*rules):
    return {row["id"]: row for row in rules}


def at(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def units(value):
    return len(value.encode("utf-16-le")) // 2


def template(kind="image", text="", **extra):
    return {"kind": kind, "text": text, "asset": {"id": "80000000-0000-4000-8000-000000000001",
        "kind": kind, "mime": "image/jpeg", "size": 10, "file_name": "arquivo-sintetico"}, **extra}


@pytest.mark.parametrize("kind", list(CONTRACT["actions"]))
def test_all_eight_observed_action_types_are_strictly_declarative(kind):
    result = RuleDefinition.model_validate({"keyword": "resposta", "response": sequence(action(kind))})
    assert result.response.actions[0].type == kind
    invalid = result.model_dump(mode="json")
    invalid["response"]["actions"][0]["execute"] = "not an allowed field"
    with pytest.raises(ValidationError):
        RuleDefinition.model_validate(invalid)


@pytest.mark.parametrize("bad", ["", " ", "{password}", "{name.upper()}", "{{name}}", "a\x00b", "a\ud800b"])
def test_text_rejects_empty_unknown_expression_null_and_surrogate(bad):
    with pytest.raises(ValidationError):
        TextAction(text=bad)


def test_utf16_text_limit_accepts_exactly_32000_units_without_cutting_emoji():
    value = "😀" * 16000
    assert TextAction(text=value).text == value
    with pytest.raises(ValidationError):
        TextAction(text=value + "a")
    assert engine.render_contact("{name}", {"name": value}) == value
    with pytest.raises(ValueError):
        engine.render_contact("Olá {name}", {"name": value})


@pytest.mark.parametrize("delay", [0, 16, True, "1", 1.5])
def test_action_delays_are_strict_integers_between_one_and_fifteen(delay):
    with pytest.raises(ValidationError):
        TextAction(text="Resposta", delay_seconds=delay)


def test_sequence_rejects_duplicate_action_ids_and_bounds_count_and_delay_separately():
    maximum = sequence(*(action() for _ in range(30)))
    assert len(SequenceResponse.model_validate(maximum).actions) == 30
    with pytest.raises(ValidationError):
        SequenceResponse.model_validate(sequence(*(action() for _ in range(31))))
    repeated = action()
    with pytest.raises(ValidationError):
        SequenceResponse.model_validate(sequence(repeated, deepcopy(repeated)))
    assert SequenceResponse.model_validate(sequence(action(delay_seconds=15), action(delay_seconds=15)))
    with pytest.raises(ValidationError):
        SequenceResponse.model_validate(sequence(action(delay_seconds=15), action(delay_seconds=15), action()))


def test_ten_menu_options_are_mutually_exclusive_and_do_not_sum_their_delays():
    response = menu(*(action(delay_seconds=5) for _ in range(10)))
    assert len(ListResponse.model_validate(response).sections[0].rows) == 10
    root = rule(response=response)
    plan, dependencies = engine.compile_plan(root["id"], graph(root), {}, FACTS, CONTEXT)
    assert len(plan) == 1 and plan[0]["action"]["type"] == "list"
    assert plan[0]["delay_seconds"] == 1 and dependencies == [root["id"]]
    with pytest.raises(ValidationError):
        ListResponse.model_validate(menu(*(action() for _ in range(11))))
    with pytest.raises(ValidationError):
        ListResponse.model_validate(menu(action(delay_seconds=6)))


@pytest.mark.parametrize("which", ["sections", "rows", "actions"])
def test_menu_identities_are_unique_across_sections_and_rows(which):
    response = menu(action(), action())
    if which == "sections":
        response["sections"].append(deepcopy(response["sections"][0]))
        response["sections"][1]["rows"] = menu(action())["sections"][0]["rows"]
    elif which == "rows":
        response["sections"][0]["rows"][1]["id"] = response["sections"][0]["rows"][0]["id"]
    else:
        response["sections"][0]["rows"][1]["action"]["id"] = response["sections"][0]["rows"][0]["action"]["id"]
    with pytest.raises(ValidationError):
        ListResponse.model_validate(response)


@pytest.mark.parametrize("path,maximum", [("title", 60), ("description", 1000), ("footer", 60),
    ("button_text", 20), ("section.title", 24), ("row.title", 24), ("row.description", 72)])
def test_menu_text_limits_count_utf16_units(path, maximum):
    response = menu()
    target, key = (response["sections"][0], "title") if path == "section.title" else (
        response["sections"][0]["rows"][0], path[4:]) if path.startswith("row.") else (response, path)
    target[key] = "😀" * (maximum // 2)
    assert ListResponse.model_validate(response)
    target[key] += "x"
    with pytest.raises(ValidationError):
        ListResponse.model_validate(response)


def test_carousel_has_exactly_three_distinct_cards_and_a_required_first_body():
    source = action("carousel")
    assert CarouselAction.model_validate(source).cards[1].body == ""
    for change in [lambda row: row["cards"].pop(),
                   lambda row: row["cards"].append(deepcopy(row["cards"][0])),
                   lambda row: row["cards"][1].update(id=row["cards"][0]["id"]),
                   lambda row: row["cards"][0].update(body="")]:
        invalid = deepcopy(source)
        change(invalid)
        with pytest.raises(ValidationError):
            CarouselAction.model_validate(invalid)


@pytest.mark.parametrize("url", ["file:///tmp/x", "https://user:pass@example.com/a", "https://127.0.0.1/a",
    "https://[::1]/a", "https://internal.local/a", "https://example.com/a#secret"])
def test_url_actions_and_carousel_images_reject_non_public_destinations_without_network(url):
    with pytest.raises(ValidationError):
        UrlAction(url=url)
    value = action("carousel")
    value["cards"][0]["image_url"] = url
    with pytest.raises(ValidationError):
        CarouselAction.model_validate(value)


@pytest.mark.parametrize("custom", ["{{x()}}", "{password}", "{{{name}}}", "{{a..b}}", "{{items[-1]}}"])
def test_url_response_template_rejects_expressions_and_malformed_paths(custom):
    with pytest.raises(ValidationError):
        UrlAction(url="https://example.com/reply", custom_template=custom)
    assert UrlAction(url="https://example.com/reply", custom_template="{{value}} — {{_status2}}").method == "POST"


@pytest.mark.parametrize("name", ["\x00", "a\ud800b", "😀" * 51, " " * 4])
def test_template_names_fit_postgres_and_the_visible_utf16_limit(name):
    with pytest.raises(ValueError):
        TemplateDefinition(name=name, kind="text", text="Resposta")
    assert TemplateDefinition(name="😀" * 50, kind="text", text="Resposta").name == "😀" * 50


@pytest.mark.parametrize("kind", ["sticker", "ptv"])
def test_sticker_and_ptv_never_silently_drop_captions(kind):
    asset_id = "80000000-0000-4000-8000-000000000001"
    assert TemplateDefinition(name="Mídia sintética", kind=kind, asset_id=asset_id)
    with pytest.raises(ValidationError):
        TemplateDefinition(name="Mídia sintética", kind=kind, asset_id=asset_id, text="Legenda")
    root = rule(response=sequence(action("sticker" if kind == "sticker" else "shortvideo", template_id=identity(9))))
    with pytest.raises(ValueError, match="não aceita legenda"):
        engine.compile_plan(root["id"], graph(root), {identity(9): template(kind, "Legenda")}, FACTS, CONTEXT)


@pytest.mark.parametrize("kind", ["image", "video", "document"])
def test_captions_enforce_1024_utf16_units_before_and_after_contact_expansion(kind):
    asset_id = "80000000-0000-4000-8000-000000000001"
    assert TemplateDefinition(name="Mídia", kind=kind, asset_id=asset_id, text="😀" * 512)
    with pytest.raises(ValidationError):
        TemplateDefinition(name="Mídia", kind=kind, asset_id=asset_id, text="😀" * 512 + "x")
    root = rule(response=sequence(action("template", template_id=identity(9))))
    templates = {identity(9): template(kind, "Olá {name}")}
    with pytest.raises(ValueError, match="1024"):
        engine.compile_plan(root["id"], graph(root), templates, {"name": "😀" * 512}, CONTEXT)


@pytest.mark.parametrize("value,expected", [("  AÇÃO\t já\n", "acao ja"), ("AÇÃO", "acao"),
    ("Straße", "strasse"), ("ＦＩＭ", "fim"), ("Olá 😀", "ola 😀")])
def test_matching_normalization_has_explicit_unicode_case_and_space_semantics(value, expected):
    assert engine.normalize(value) == expected


@pytest.mark.parametrize("mode,keyword,text,expected", [
    ("equal", "olá", "  OLA  ", True), ("equal", "olá", "olá cliente", False),
    ("like", "ação", "A promoção está ativa", False), ("like", "promoção", "Tem PROMOCAO hoje", True),
    ("array", " , preço, horário, ", "Qual o HORARIO?", True),
    ("array", "preço, horário", "Olá", False), ("like", "bar", "barbearia", True),
    ("equal", "😀", "😀", True), ("equal", "*", "qualquer mensagem", False),
])
def test_matching_exact_substring_array_and_wildcard_are_distinct(mode, keyword, text, expected):
    assert engine.matches(rule(keyword=keyword, match_mode=mode), text) is expected


def test_specific_newest_creation_order_wins_without_edit_timestamp_reordering():
    older = rule(1)
    older["updated_at"] = "2099-01-01T00:00:00Z"
    newer = rule(2, match_mode="like", keyword="ola")
    wildcard = rule(99, keyword="****")
    disabled = rule(100, enabled=False)
    removed = rule(101)
    removed["removed_at"] = "2026-11-04T01:00:00Z"
    other = rule(102, device_id=OTHER_DEVICE)
    assert [row["id"] for row in engine.candidates([wildcard, older, disabled, removed, other, newer], "OLÁ", DEVICE)] == [newer["id"], older["id"]]
    assert engine.candidates([wildcard, older], "sem correspondência", DEVICE) == [wildcard]
    scoped = rule(3, device_id=DEVICE)
    assert engine.candidates([older, scoped], "olá", UUID(DEVICE)) == [scoped, older]


@pytest.mark.parametrize("keyword,hours", [("*", 24), ("**", 14 * 24), ("*****", 1), ("****", 0)])
def test_rolling_windows_have_exact_end_inclusive_admission(keyword, hours):
    start = at("2026-11-04T02:30:00Z")
    end, local_day = engine.window_end(keyword, start, "America/Sao_Paulo")
    assert end == start + timedelta(hours=hours) and local_day is None
    previous = {"next_allowed_at": end, "local_day": None}
    assert engine.window_available(keyword, previous, end, "America/Sao_Paulo")
    assert engine.window_available(keyword, None, start, "America/Sao_Paulo")
    assert engine.window_available(keyword, previous, end - timedelta(microseconds=1), "America/Sao_Paulo") is (keyword == "****")


def test_daily_wildcard_uses_device_day_instead_of_the_utc_date():
    start = at("2026-11-04T02:59:59Z")
    end, local_day = engine.window_end("***", start, "America/Sao_Paulo")
    assert local_day == date(2026, 11, 3) and end == at("2026-11-04T03:00:00Z")
    previous = {"next_allowed_at": end, "local_day": local_day}
    assert not engine.window_available("***", previous, start, "America/Sao_Paulo")
    assert engine.window_available("***", previous, end, "America/Sao_Paulo")
    assert engine.window_available("***", previous, start, "UTC")


@pytest.mark.parametrize("clock,hours", [("2026-03-08T00:00:00-05:00", 23), ("2026-11-01T00:00:00-04:00", 25)])
def test_calendar_day_windows_respect_daylight_saving_transitions(clock, hours):
    start = at(clock)
    end, _ = engine.window_end("***", start, "America/New_York")
    assert end - start == timedelta(hours=hours)


def test_contact_substitution_is_single_pass_data_and_requires_used_facts():
    assert engine.render_contact("{name} · {my_name}", {**FACTS, "name": "{my_email}"}) == "{my_email} · Negócio sintético"
    assert engine.render_contact("Sem tags", {}) == "Sem tags"
    for value in [None, "", "  ", 12, False]:
        with pytest.raises(ValueError):
            engine.render_contact("Olá {name}", {"name": value})
    assert engine.render_contact("", FACTS, maximum=20) == ""


def test_nested_sequence_preserves_every_message_order_delay_and_source_objects():
    child = rule(2, response=sequence(action(text="Segunda", delay_seconds=2), action(text="Terceira", delay_seconds=3)))
    root = rule(1, response=sequence(action(text="Primeira"), action("nested_reply", rule_id=child["id"], delay_seconds=4)))
    rules = graph(root, child)
    before = deepcopy(rules)
    plan, dependencies = engine.compile_plan(root["id"], rules, {}, FACTS, CONTEXT)
    assert [row["action"]["text"] for row in plan] == ["Primeira", "Segunda", "Terceira"]
    assert [row["delay_seconds"] for row in plan] == [1, 6, 3]
    assert dependencies == sorted([root["id"], child["id"]]) and rules == before


def test_expanded_delay_is_checked_before_save_and_never_truncates_later_actions():
    child = rule(2, response=sequence(action(delay_seconds=15), action(delay_seconds=14)))
    root = rule(response=sequence(action("nested_reply", rule_id=child["id"])))
    assert engine.validate_graph(graph(root, child), {})
    plan, _ = engine.compile_plan(root["id"], graph(root, child), {}, FACTS, CONTEXT)
    assert len(plan) == 2 and sum(row["delay_seconds"] for row in plan) == 30
    child["response"]["actions"][1]["delay_seconds"] = 15
    with pytest.raises(ValueError, match="trinta"):
        engine.validate_graph(graph(root, child), {})
    with pytest.raises(ValueError):
        engine.compile_plan(root["id"], graph(root, child), {}, FACTS, CONTEXT)


def test_duplicate_references_count_each_expansion_toward_limits():
    child = rule(2, response=sequence(*(action() for _ in range(15))))
    root = rule(response=sequence(action("nested_reply", rule_id=child["id"]), action("nested_reply", rule_id=child["id"])))
    with pytest.raises(ValueError, match="trinta"):
        engine.validate_graph(graph(root, child), {})


def chain(length):
    rows = [rule(index, response=sequence(action("nested_reply", rule_id=identity(index + 1))))
            for index in range(1, length)]
    rows.append(rule(length))
    return rows


@pytest.mark.parametrize("leaf_first", [False, True])
def test_eight_level_limit_is_independent_of_graph_insertion_order(leaf_first):
    valid = chain(8)
    engine.validate_graph(graph(*(reversed(valid) if leaf_first else valid)), {})
    plan, _ = engine.compile_plan(valid[0]["id"], graph(*valid), {}, FACTS, CONTEXT)
    assert len(plan) == 1 and plan[0]["delay_seconds"] == 8
    invalid = chain(9)
    with pytest.raises(ValueError, match="oito"):
        engine.validate_graph(graph(*(reversed(invalid) if leaf_first else invalid)), {})


@pytest.mark.parametrize("length", [1, 2, 3])
def test_automatic_cycles_are_rejected_even_when_inactive(length):
    rows = [rule(index, enabled=False, response=sequence(action("nested_reply", rule_id=identity(index % length + 1))))
            for index in range(1, length + 1)]
    with pytest.raises(ValueError, match="circulares"):
        engine.validate_graph(graph(*rows), {})


def test_user_selected_list_navigation_can_return_to_a_previous_menu_without_automatic_recursion():
    first = rule(1, response=sequence(action("nested_list", rule_id=identity(2))))
    second = rule(2, response=menu(action("nested_reply", rule_id=identity(1))))
    rules = graph(first, second)
    engine.validate_graph(rules, {})
    plan, dependencies = engine.compile_plan(first["id"], rules, {}, FACTS, CONTEXT)
    assert len(plan) == 1 and plan[0]["action"]["type"] == "list"
    selected = second["response"]["sections"][0]["rows"][0]["action"]
    next_plan, next_dependencies = engine.compile_plan(second["id"], rules, {}, FACTS, CONTEXT, selected_action=selected)
    assert len(next_plan) == 1 and next_plan[0]["action"]["type"] == "list"
    assert set(dependencies) == set(next_dependencies) == {first["id"], second["id"]}


def test_list_selection_rechecks_that_its_root_is_still_active():
    root = rule(response=menu(action(text="Não enviar quando inativo")), enabled=False)
    selected = root["response"]["sections"][0]["rows"][0]["action"]
    with pytest.raises(ValueError, match="inativa"):
        engine.compile_plan(root["id"], graph(root), {}, FACTS, CONTEXT, selected_action=selected)
    plan, _ = engine.compile_plan(root["id"], graph(root), {}, FACTS, CONTEXT,
                                  selected_action=selected, allow_inactive_root=True)
    assert plan[0]["action"]["text"] == "Não enviar quando inativo"


def test_disabled_and_removed_references_never_execute_but_inactive_root_test_is_explicit():
    child = rule(2, enabled=False)
    root = rule(response=sequence(action("nested_reply", rule_id=child["id"])))
    engine.validate_graph(graph(root, child), {})
    with pytest.raises(ValueError, match="inativa"):
        engine.validate_graph(graph(root, child), {}, require_enabled=True, root_ids=[root["id"]])
    with pytest.raises(ValueError, match="inativa"):
        engine.compile_plan(root["id"], graph(root, child), {}, FACTS, CONTEXT)
    root["enabled"] = False
    with pytest.raises(ValueError, match="inativa"):
        engine.compile_plan(root["id"], graph(root, child), {}, FACTS, CONTEXT, allow_inactive_root=True)
    child["enabled"] = True
    assert engine.compile_plan(root["id"], graph(root, child), {}, FACTS, CONTEXT, allow_inactive_root=True)
    child["removed_at"] = "2026-11-04T01:00:00Z"
    with pytest.raises(ValueError, match="não existe"):
        engine.validate_graph(graph(root, child), {})


@pytest.mark.parametrize("root_device,child_device,allowed", [(None, None, True),
    (DEVICE, None, True), (DEVICE, DEVICE, True), (None, DEVICE, False), (DEVICE, OTHER_DEVICE, False)])
def test_references_cannot_escape_device_scope(root_device, child_device, allowed):
    child = rule(2, device_id=child_device)
    root = rule(device_id=root_device, response=sequence(action("nested_reply", rule_id=child["id"])))
    if allowed:
        assert engine.validate_graph(graph(root, child), {})
    else:
        with pytest.raises(ValueError, match="dispositivo"):
            engine.validate_graph(graph(root, child), {})


def test_reference_types_and_template_media_kinds_must_match():
    child = rule(2, response=menu())
    root = rule(response=sequence(action("nested_reply", rule_id=child["id"])))
    with pytest.raises(ValueError, match="tipo"):
        engine.validate_graph(graph(root, child), {})
    for kind in ["sticker", "shortvideo"]:
        root = rule(response=sequence(action(kind, template_id=identity(9))))
        with pytest.raises(ValueError, match="tipo"):
            engine.validate_graph(graph(root), {identity(9): template("image")})
        with pytest.raises(ValueError, match="disponível"):
            engine.validate_graph(graph(root), {})


@pytest.mark.parametrize("kind,template_kind,result_type", [("template", "text", "text"),
    ("template", "image", "image"), ("template", "video", "video"), ("template", "document", "document"),
    ("sticker", "sticker", "sticker"), ("shortvideo", "ptv", "ptv")])
def test_template_compilation_resolves_real_media_and_preserves_transport_type(kind, template_kind, result_type):
    text = "" if template_kind in {"sticker", "ptv"} else "Olá, {name}!"
    templates = {identity(9): template(template_kind, text)}
    root = rule(response=sequence(action(kind, template_id=identity(9))))
    before = deepcopy(templates)
    plan, _ = engine.compile_plan(root["id"], graph(root), templates, FACTS, CONTEXT)
    message = plan[0]["action"]
    assert message["type"] == result_type and templates == before
    if result_type == "text":
        assert message["text"] == "Olá, Cliente sintético!"
    else:
        assert message["asset"] == templates[identity(9)]["asset"]
        assert message["caption"] == ("" if result_type in {"sticker", "ptv"} else "Olá, Cliente sintético!")
        assert message["file_name"] == "arquivo-sintetico"


def test_carousel_compile_renders_bounded_text_without_fetching_or_changing_image_urls():
    item = action("carousel", body="Olá, {name}!")
    item["cards"][0].update(title="Olá {my_name}", button_text="Ver {name}")
    root = rule(response=sequence(item))
    plan, _ = engine.compile_plan(root["id"], graph(root), {}, {**FACTS, "name": "Ana"}, CONTEXT)
    result = plan[0]["action"]
    assert result["type"] == "carousel" and result["body"] == "Olá, Ana!"
    assert [card["image_url"] for card in result["cards"]] == [card["image_url"] for card in item["cards"]]
    assert all("id" not in card for card in result["cards"])
    with pytest.raises(ValueError, match="20"):
        engine.compile_plan(root["id"], graph(root), {}, {**FACTS, "name": "😀" * 10}, CONTEXT)


def test_url_compile_only_describes_the_http_action_and_copies_context():
    item = action("url", url="https://example.com/reply", method="GET", custom_template="{{status}}", as_file=True)
    root = rule(response=sequence(item))
    plan, _ = engine.compile_plan(root["id"], graph(root), {}, FACTS, CONTEXT)
    result = plan[0]["action"]
    assert result == {"type": "http", "url": item["url"], "method": "GET", "custom_template": "{{status}}",
                      "as_file": True, "mode": "reply", "data": CONTEXT}
    result["data"]["name"] = "Alterado somente na cópia"
    assert CONTEXT["name"] == FACTS["name"]


def test_mixed_contact_and_api_tags_compile_and_render_without_second_pass():
    custom = "Olá, {name}! Usuário: {{username}} | Senha: {{password}}."
    item = action("url", url="https://example.com/reply", method="GET", custom_template=custom)
    root = rule(response=sequence(item))
    plan, _ = engine.compile_plan(root["id"], graph(root), {}, FACTS, CONTEXT)
    transport = plan[0]["action"]
    assert transport["custom_template"] == custom
    assert transport["contact_facts"] == {"name": FACTS["name"]}
    assert transport["data"] == CONTEXT
    body = {"username": "usuario-de-teste", "password": "senha-sintetica"}
    assert engine.render_http_result(transport, body) == (
        "Olá, Cliente sintético! Usuário: usuario-de-teste | Senha: senha-sintetica.")
    # Neither the user's name nor an API field is evaluated again after insertion.
    malicious = {**transport, "contact_facts": {"name": "{{password}}"}}
    assert engine.render_http_result(malicious, body).startswith("Olá, {{password}}! ")
    assert engine.render_http_result(
        transport, {"username": "{name}", "password": "x"}
    ).startswith("Olá, Cliente sintético! Usuário: {name} |")
    assert UrlAction(url="https://example.com/reply", custom_template=custom)
    with pytest.raises(ValidationError):
        UrlAction(url="https://example.com/reply", custom_template="Olá, {password}!")
    with pytest.raises(ValueError, match="Dados de contato indisponíveis"):
        engine.compile_plan(root["id"], graph(root), {}, {}, CONTEXT)


def test_http_scalar_tags_are_non_recursive_and_preserve_values_without_evaluating_expressions():
    descriptor = {"custom_template": "{{name}} | {{count}} | {{active}} | {{amount}}"}
    assert engine.render_http_result(descriptor, {"name": "{{secret}}", "count": 3, "active": False, "amount": 2.5}) == "{{secret}} | 3 | false | 2.5"
    assert engine.render_http_result({}, {"reply": "Resposta direta"}) == "Resposta direta"
    for bad in [None, [], {}, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            engine.render_http_result({"custom_template": "{{value}}"}, {"value": bad})
    with pytest.raises(ValueError):
        engine.render_http_result({}, {"reply": {"nested": "no"}})


def test_http_output_splitting_preserves_every_character_and_utf16_boundaries():
    text = "a" * 31999 + "😀" + "b" * 31999 + "😀" + "Fim"
    parts = engine.split_text(text)
    assert "".join(parts) == text and all(units(part) <= 32000 for part in parts)
    assert len(parts) == 3 and parts[0] == "a" * 31999
    with pytest.raises(ValueError, match="trinta"):
        engine.split_text("x" * (32000 * 30 + 1))
    with pytest.raises(ValueError, match="512000"):
        engine.render_http_result({}, {"reply": "x" * 512001})


def test_clone_changes_only_owned_identifiers_and_preserves_references_and_source():
    source = menu(action("carousel"), action("nested_reply", rule_id=identity(9)))
    before = deepcopy(source)
    result = engine.clone_response(source)
    assert source == before
    assert result["sections"][0]["id"] != source["sections"][0]["id"]
    for row, old in zip(result["sections"][0]["rows"], source["sections"][0]["rows"]):
        assert row["id"] != old["id"] and row["action"]["id"] != old["action"]["id"]
        if row["action"]["type"] == "carousel":
            assert all(card["id"] != previous["id"] for card, previous in zip(row["action"]["cards"], old["action"]["cards"]))
        else:
            assert row["action"]["rule_id"] == identity(9)


def test_versioned_commands_reject_weak_revision_types_and_import_cannot_request_exact():
    payload = {"expected_chatbot_revision": 1, "idempotency_key": str(uuid4()), "rule": RuleDefinition(keyword="olá", response=sequence()).model_dump(mode="json")}
    assert CreateRule.model_validate(payload)
    for revision in [0, True, "1", 1.5]:
        with pytest.raises(ValidationError):
            CreateRule.model_validate({**payload, "expected_chatbot_revision": revision})
    for filename in ["../orion.json", "orion.txt", "nested\\orion.zip", "orion\n.json"]:
        with pytest.raises(ValidationError):
            ImportPreview(filename=filename, content_base64="e30=")
    with pytest.raises(ValidationError):
        ImportPreview(filename="orion.json", content_base64="e30=", mode="exact")
    assert RestorePreview(mode="exact")
    with pytest.raises(ValidationError):
        ExportSelection(ids=[])
