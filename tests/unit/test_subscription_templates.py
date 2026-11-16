"""Template library boundaries; all accounts, files and messages are synthetic."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
import httpx
from pydantic import ValidationError
import pytest

from app import subscription_templates as api, subscription_templates_schemas as schemas
from app import subscription_chatbot_archive as archive, subscription_chatbot_archive_format as codec
from app import subscription_chatbot_schemas as common, subscription_chatbot_store as store
from app import plan_permissions, plan_routing
from app.catalog_api import Actor, authenticate
from test_subscription_chatbot_archive_format import document, asset, text_rule, uid

CONTRACT = json.loads((Path(__file__).parents[1] / "fixtures/subscription_templates_contract.json").read_text())
TENANT = UUID("10000000-0000-4000-8000-000000000001")
ACCOUNT = UUID("90000000-0000-4000-8000-000000000001")
TEMPLATE = UUID("60000000-0000-4000-8000-000000000001")
OWNER = Actor("member", TENANT, ACCOUNT, "owner")
BASE = api.BASE.format(tenant_id=TENANT)


def definition(kind="text", **updates):
    value = {"name": "Exemplo", "kind": kind, "text": "Olá" if kind == "text" else "",
             "followup_text": "", "asset_id": None if kind in {"text", "location"} else uuid4(),
             "location": {"latitude": 0, "longitude": 0} if kind == "location" else None, "file_name": None}
    return value | updates


@pytest.mark.parametrize("name,model", [("create_request", schemas.CreateTemplate),
    ("update_request", schemas.UpdateTemplate), ("delete_request", schemas.DeleteTemplate),
    ("upload_request", schemas.UploadAsset)])
def test_canonical_fixture_requests_validate_without_coercing_revision(name, model):
    assert model.model_validate(CONTRACT[name]).request_id == UUID(CONTRACT[name]["request_id"])


@pytest.mark.parametrize("field", ["request_id", "expected_chatbot_revision", "template"])
def test_required_create_fields_are_not_guessed(field):
    value = deepcopy(CONTRACT["create_request"])
    value.pop(field)
    with pytest.raises(ValidationError):
        schemas.CreateTemplate.model_validate(value)


@pytest.mark.parametrize("revision", [True, "1", 1.5, 0, -1])
def test_revision_requires_positive_integer(revision):
    with pytest.raises(ValidationError):
        schemas.CreateTemplate.model_validate(CONTRACT["create_request"] | {"expected_chatbot_revision": revision})


@pytest.mark.parametrize("kind", schemas.KINDS)
def test_all_observed_types_have_valid_declarative_definitions(kind):
    result = schemas.LibraryTemplate.model_validate(definition(kind))
    assert result.kind == kind


def test_only_text_may_omit_name_and_default_is_stable():
    assert schemas.LibraryTemplate.model_validate(definition(name="  ")).name == "Texto sem nome"
    for kind in set(schemas.KINDS) - {"text"}:
        with pytest.raises(ValidationError):
            schemas.LibraryTemplate.model_validate(definition(kind, name="  "))


@pytest.mark.parametrize("field,limit,kind", [("name", 100, "text"), ("text", 1000, "text"),
    ("text", 1000, "image"), ("followup_text", 1000, "image")])
def test_utf16_limits_preserve_whole_emoji_and_never_truncate(field, limit, kind):
    value = definition(kind, **{field: "😀" * (limit // 2)})
    assert getattr(schemas.LibraryTemplate.model_validate(value), field) == value[field]
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(value | {field: value[field] + "😀"})


@pytest.mark.parametrize("text", ["bad\x00text", "bad\ud800text", "{unknown}", "{{name}}"])
@pytest.mark.parametrize("field", ["text", "followup_text"])
def test_text_and_followup_only_allow_supported_contact_tags(field, text):
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(definition("image", **{field: text}))


@pytest.mark.parametrize("kind", ["text", "location", "sticker", "ptv"])
def test_followup_not_invented_for_tabs_without_that_field(kind):
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(definition(kind, followup_text="Extra"))


@pytest.mark.parametrize("kind", ["sticker", "ptv"])
def test_sticker_and_video_note_never_silently_drop_a_caption(kind):
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(definition(kind, text="Não descartar"))


@pytest.mark.parametrize("location", [
    {"latitude": 90.1, "longitude": 0}, {"latitude": 0, "longitude": -180.1},
    {"latitude": True, "longitude": 0}, {"latitude": "1", "longitude": 0},
    {"latitude": float("nan"), "longitude": 0}, {"latitude": 0, "longitude": float("inf")},
    {"latitude": 0}, {"latitude": 0, "longitude": 0, "url": "private"},
])
def test_location_is_finite_numeric_and_bounded(location):
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(definition("location", location=location))


def test_location_includes_zero_and_poles_without_coercing_strings():
    for latitude, longitude in ((0, 0), (-90, 180), (90, -180)):
        result = schemas.LibraryTemplate.model_validate(definition("location", location={
            "latitude": latitude, "longitude": longitude}))
        assert result.location == {"latitude": latitude, "longitude": longitude}


def test_new_form_limit_does_not_invalidate_legacy_archive_text():
    value = definition(text="a" * 32000)
    assert len(common.TemplateDefinition.model_validate(value).text) == 32000
    with pytest.raises(ValidationError):
        schemas.LibraryTemplate.model_validate(value)


@pytest.mark.parametrize("filename", ["../secret.txt", "/tmp/a.txt", "a\\b.txt", "a:b.txt", "a\n.txt", "a\x00.txt", "..", "x.", "😀" * 61])
def test_filenames_are_only_short_display_names(filename):
    with pytest.raises(ValueError):
        schemas.safe_filename(filename)


@pytest.mark.parametrize("filename,mime,expected", [("Foto.png", "image/jpeg", "Foto.jpg"),
    ("Voz.opus", "audio/mpeg", "Voz.mp3"), ("Preços.csv", "text/plain; charset=utf-8", "Preços.csv"),
    ("Contrato.pdf", "application/pdf", "Contrato.pdf")])
def test_delivery_filename_uses_verified_mime_without_overwriting_shared_asset(filename, mime, expected):
    assert schemas.media_filename(filename, mime) == expected


def test_longer_normalized_extension_is_rejected_without_cutting_the_display_name():
    valid = "a" * 115 + ".png"
    assert len(schemas.media_filename(valid, "image/webp")) == 120
    with pytest.raises(ValueError):
        schemas.media_filename("a" * 116 + ".png", "image/webp")


@pytest.mark.parametrize("kind,filename", [("image", "a.pdf"), ("sticker", "a.webp"), ("ptv", "a.mov"),
    ("audio", "a.exe"), ("document", "a.svg"), ("video", "a.wav")])
def test_upload_extension_cannot_change_the_selected_kind(kind, filename):
    with pytest.raises(ValidationError):
        schemas.UploadAsset.model_validate(CONTRACT["upload_request"] | {"kind": kind, "filename": filename})


def test_all_library_routes_have_own_feature_and_workspace_classification():
    routes = [route for route in api.router.routes if isinstance(route, APIRoute)]
    assert len(routes) == 7
    for route in routes:
        relative = route.path.removeprefix(plan_routing.TENANT_PREFIX)
        for method in route.methods:
            assert plan_routing.feature_for_path(relative, method) == "templates.manage"
            assert plan_routing.subscription_workspace_route(relative, method, {}) is True


@pytest.mark.parametrize("niche,status,allowed", [("servico_assinaturas", "active", True),
    ("servico_assinaturas", "inactive", False), ("barbearia", "active", False), (None, "active", False)])
def test_template_permission_stays_inside_active_subscription_workspace(niche, status, allowed):
    state = plan_permissions.derive_plan({"niche": niche, "tenant_status": status})
    assert state["permissions"]["templates.manage"] is allowed


@pytest.mark.parametrize("method,suffix", [("POST", ""), ("PUT", f"/{TEMPLATE}"),
    ("DELETE", f"/{TEMPLATE}"), ("POST", "/assets")])
async def test_owner_is_checked_before_json_or_base64_decoding(monkeypatch, method, suffix):
    application = FastAPI()
    # Isolate the endpoint's owner/body ordering from the shared router's plan
    # dependency, which app.main may already have attached during collection.
    # Endpoint-declared authentication still runs; PostgreSQL integration tests
    # exercise the complete router with its real plan and ownership checks.
    for route in api.router.routes:
        if isinstance(route, APIRoute):
            application.add_api_route(route.path, route.endpoint, methods=route.methods)
    application.dependency_overrides[authenticate] = lambda: OWNER
    calls = []

    async def rejected(tenant, actor):
        assert tenant == TENANT and actor == OWNER
        calls.append("authorize")
        raise HTTPException(403, "forbidden")

    async def forbidden(*args, **kwargs):
        pytest.fail("An unauthorized upload body must remain unread")

    monkeypatch.setattr(api, "authorize", rejected)
    monkeypatch.setattr(api, "bounded_json", forbidden)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://synthetic.test") as client:
        response = await client.request(method, BASE + suffix, content=b"private-malformed-body")
    assert response.status_code == 403 and calls == ["authorize"]


async def test_create_limit_counts_all_existing_templates_before_graph_materialization():
    rows = {str(uuid4()): {} for _ in range(100)}
    value = schemas.LibraryTemplate.model_validate(definition()).model_dump(mode="python")
    with pytest.raises(HTTPException) as error:
        await api.validate_candidate(TENANT, value, TEMPLATE, {}, rows, {})
    assert error.value.status_code == 409 and error.value.detail["code"] == "TEMPLATE_LIMIT"


async def test_template_kind_is_immutable_even_when_no_rule_references_it():
    value = schemas.LibraryTemplate.model_validate(definition("location")).model_dump(mode="python")
    with pytest.raises(HTTPException) as error:
        await api.validate_candidate(TENANT, value, TEMPLATE, {}, {}, {}, existing={"kind": "text"})
    assert error.value.detail["code"] == "TEMPLATE_KIND_IMMUTABLE"


@pytest.mark.parametrize("rows", [{}, {"kind": "video"}])
async def test_missing_or_wrong_kind_asset_is_not_a_cross_tenant_or_format_oracle(rows):
    value = schemas.LibraryTemplate.model_validate(definition("image")).model_dump(mode="python")
    assets = {str(value["asset_id"]): rows} if rows else {}
    with pytest.raises(HTTPException) as error:
        await api.validate_candidate(TENANT, value, TEMPLATE, {}, {}, assets)
    assert error.value.status_code == 422 and error.value.detail["code"] == "TEMPLATE_ASSET_INVALID"


def test_dependency_index_counts_inactive_rules_and_list_rows_once_per_rule():
    actions = [{"type": "template", "template_id": str(TEMPLATE)}] * 2
    graph = {"a": {"id": uuid4(), "keyword": "a", "enabled": False, "device_id": None,
        "creation_order": 1, "response": {"kind": "sequence", "actions": actions}},
        "b": {"id": uuid4(), "keyword": "b", "enabled": True, "device_id": None,
        "creation_order": 2, "response": {"kind": "list", "sections": [{"rows": [{"action": actions[0]}]}]}}}
    found = api.dependency_map(graph)[str(TEMPLATE)]
    assert len(found) == 2 and [row["keyword"] for row in found] == ["b", "a"]


@pytest.mark.parametrize("format", ["json", "zip"])
@pytest.mark.parametrize("kind", ["audio", "document", "location"])
def test_archive_preserves_new_template_content_names_and_coordinate_types(format, kind):
    value = document(with_media=False)
    data = b"synthetic decoder input validated separately"
    if kind == "location":
        template = definition(kind, location={"latitude": -22.9, "longitude": -43.2})
    else:
        record = asset(201, data)
        if kind == "audio":
            record.update(kind="audio", mime="audio/mpeg", duration_ms=1500)
        value["assets"] = [record]
        template = definition(kind, asset_id=uid(201), text="Legenda", followup_text="Depois, {name}",
                              file_name="Mensagem.mp3" if kind == "audio" else "Tabela.csv")
    value["templates"] = [{"id": uid(301), "definition": template}]
    value["rules"] = [text_rule(1, action={"id": uid(101), "type": "template", "template_id": uid(301), "delay_seconds": 1})]
    returned = codec.read_archive(codec.write_archive(value, format), "templates." + format)
    assert returned == value


@pytest.mark.parametrize("field,value", [("followup_body", "mensagem diferente"),
    ("location", {"latitude": 1, "longitude": 2}), ("file_name", "outro.csv")])
def test_restore_identity_distinguishes_every_new_template_field(field, value):
    before = archive._template_signature("document", "Texto", None, "Nome")
    after = archive._template_signature("document", "Texto", None, "Nome", **{field: value})
    assert before != after


@pytest.mark.parametrize("field,value", [("width", 1), ("height", 1), ("duration_ms", 0),
    ("duration_ms", 1_800_001), ("mime", "audio/ogg"), ("thumbnail", {})])
def test_audio_archive_metadata_is_bounded_and_cannot_smuggle_image_fields(field, value):
    item = asset(201, b"audio", kind="document")
    item.update(kind="audio", mime="audio/mpeg", duration_ms=1000)
    item[field] = value
    with pytest.raises(ValueError):
        codec._asset_metadata({key: item[key] for key in codec._META})


def test_fixture_limits_and_media_contract_are_canonical():
    assert CONTRACT["list_response"]["limits"] == api.limits()
    assert CONTRACT["list_response"]["media_types"] == api.MEDIA_TYPES
    assert CONTRACT["list_response"]["tags"] == api.TAGS
