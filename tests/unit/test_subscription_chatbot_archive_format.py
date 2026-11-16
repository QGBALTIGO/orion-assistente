"""Portable archives: closed graphs, integrity, hostile containers and limits.

All content is synthetic. These tests never extract files, fetch URLs, access
storage or contact WhatsApp; real media decoding is covered by the media suite.
"""
import base64
from copy import deepcopy
from hashlib import sha256
import io
import json
import stat
import struct
from uuid import UUID
import zipfile

import pytest

from app import subscription_chatbot_archive_format as codec


def uid(number):
    return str(UUID(int=number))


def text_rule(number=1, *, action=None):
    return {"id": uid(number), "creation_order": number, "definition": {
        "device_id": None, "enabled": True, "match_mode": "equal", "keyword": "Olá",
        "response": {"kind": "sequence", "actions": [action or {
            "id": uid(100 + number), "type": "text", "text": "Olá, {name}!", "delay_seconds": 1}]}}}


def asset(number=201, data=b"Conteudo sintetico\n", *, kind="document"):
    item = {"id": uid(number), "kind": kind, "mime": "text/plain; charset=utf-8",
            "size": len(data), "sha256": sha256(data).hexdigest(), "width": None,
            "height": None, "duration_ms": None, "thumbnail": None,
            "data": data, "thumbnail_data": None}
    if kind == "image":
        thumb = b"synthetic-thumbnail-bytes-decoded-by-importer"
        item.update(mime="image/jpeg", width=80, height=60,
                    thumbnail={"mime": "image/jpeg", "size": len(thumb), "sha256": sha256(thumb).hexdigest()},
                    thumbnail_data=thumb)
    return item


def document(*, with_media=True):
    result = {"format": codec.FORMAT, "version": 1, "exported_at": "2026-11-04T10:00:00+00:00",
              "rules": [text_rule()], "templates": [], "assets": []}
    if with_media:
        result["assets"] = [asset(), asset(202, b"synthetic-image-decoded-by-importer", kind="image")]
        result["templates"] = [
            {"id": uid(301), "definition": {"name": "Documento de teste", "kind": "document", "text": "", "asset_id": uid(201)}},
            {"id": uid(302), "definition": {"name": "Imagem de teste", "kind": "image", "text": "Olá, {name}", "asset_id": uid(202)}}]
        for number in (2, 3):
            result["rules"].append(text_rule(number, action={"id": uid(100 + number), "type": "template",
                "template_id": uid(299 + number), "delay_seconds": 1}))
    return result


def packed(manifest, extras=(), *, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as target:
        target.writestr("chatbot.json", manifest if isinstance(manifest, bytes) else json.dumps(manifest).encode())
        for name, data in extras:
            target.writestr(name, data)
    return stream.getvalue()


def unpacked(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as source:
        return json.loads(source.read("chatbot.json")), [(name, source.read(name)) for name in source.namelist() if name != "chatbot.json"]


@pytest.mark.parametrize("format", ["json", "zip"])
def test_roundtrip_self_contained_graph_preserves_media_ids_order_and_input(format):
    original = document()
    before = deepcopy(original)
    encoded = codec.write_archive(original, format)
    assert codec.read_archive(encoded, "orion." + format) == original
    assert codec.write_archive(original, format) == encoded
    assert original == before
    original["rules"].reverse()
    original["templates"].reverse()
    original["assets"].reverse()
    assert codec.write_archive(original, format) == encoded
    if format == "zip":
        with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
            assert set(archive.namelist()) == {"chatbot.json", f"media/{uid(201)}.bin",
                                              f"media/{uid(202)}.bin", f"media/{uid(202)}.thumb.jpg"}
            assert all(entry.date_time == (1980, 1, 1, 0, 0, 0) for entry in archive.infolist())
            assert all(stat.S_IMODE(entry.external_attr >> 16) == 0o600 for entry in archive.infolist())


@pytest.mark.parametrize("format", ["json", "zip"])
def test_empty_archive_is_valid_for_exact_restore_and_export(format):
    original = document(with_media=False)
    original["rules"] = []
    assert codec.read_archive(codec.write_archive(original, format), "empty." + format) == original


def test_narrow_normalized_image_remains_portable():
    original = document()
    original["assets"][1].update(width=6, height=1600)
    assert codec.read_archive(codec.write_archive(original), "orion.zip") == original


@pytest.mark.parametrize("change", [
    lambda value: value.update(format="botbot"),
    lambda value: value.update(version=True),
    lambda value: value.update(version=2),
    lambda value: value.update(exported_at="2026-11-04T00:00:00"),
    lambda value: value.update(exported_at="not-a-date"),
    lambda value: value.update(tenant_id=uid(999)),
    lambda value: value.pop("rules"),
    lambda value: value.update(rules={}),
    lambda value: value["rules"].append(deepcopy(value["rules"][0])),
    lambda value: value["rules"].append({**deepcopy(value["rules"][0]), "id": uid(2)}),
    lambda value: value["rules"][0].update(creation_order=True),
    lambda value: value["rules"][0].update(creation_order=0),
    lambda value: value["rules"][0]["definition"].update(enabled="true"),
    lambda value: value["rules"][0]["definition"].update(keyword="🙂" * 128),
    lambda value: value["rules"][0]["definition"].update(arbitrary_code="do not execute"),
])
def test_invalid_format_schema_identity_and_order_rejected(change):
    value = document(with_media=False)
    change(value)
    with pytest.raises(ValueError):
        codec.read_archive(json.dumps(value).encode(), "orion.json")


@pytest.mark.parametrize("format", [None, [], {}, "tar", "JSON"])
def test_bad_export_format_has_safe_value_error(format):
    with pytest.raises(ValueError):
        codec.write_archive(document(with_media=False), format)


@pytest.mark.parametrize("filename", ["../orion.zip", "C:\\orion.json", "x:y.json", "x\x00.json", "x.tar", "", 7])
def test_invalid_filename_rejected(filename):
    with pytest.raises(ValueError):
        codec.read_archive(b"{}", filename)


@pytest.mark.parametrize("raw", [b"", b"{broken", b'[]', b'{"a":1,"a":2}', b'{"a":NaN}',
                                 b'{"a":Infinity}', b'{"a":"\xff"}', b"[" * 100 + b"0" + b"]" * 100])
def test_malformed_json_duplicate_keys_nonfinite_and_depth_rejected(raw):
    with pytest.raises(ValueError):
        codec.read_archive(raw, "orion.json")


@pytest.mark.parametrize("change", [
    lambda value: value["assets"][0].update(size=1),
    lambda value: value["assets"][0].update(sha256="0" * 64),
    lambda value: value["assets"][0].update(data=b"other content"),
    lambda value: value["assets"][0].update(width=10),
    lambda value: value["assets"][0].update(mime="text/html"),
    lambda value: value["assets"][0].update(thumbnail_data=b"unexpected"),
    lambda value: value["assets"][1].update(thumbnail=None, thumbnail_data=None),
    lambda value: value["assets"][1].update(thumbnail_data=b"changed"),
    lambda value: value["assets"].append(deepcopy(value["assets"][0])),
    lambda value: value["templates"].append(deepcopy(value["templates"][0])),
    lambda value: value["templates"][0]["definition"].update(asset_id=uid(999)),
    lambda value: value["templates"][0]["definition"].update(kind="image"),
    lambda value: value.update(templates=[]),
    lambda value: value.update(assets=[]),
])
def test_integrity_metadata_and_missing_graph_members_rejected(change):
    value = document()
    change(value)
    with pytest.raises(ValueError):
        codec.write_archive(value)


@pytest.mark.parametrize("value", ["%%%=" , "QQ==\n", "QQ=", "QR=="])
def test_noncanonical_base64_rejected_even_when_decoded_byte_would_match(value):
    original = document()
    original["assets"][0] = asset(data=b"A")
    source = json.loads(codec.write_archive(original, "json"))
    source["assets"][0]["data_base64"] = value
    with pytest.raises(ValueError):
        codec.read_archive(json.dumps(source).encode(), "orion.json")


def list_rule(number, target):
    value = text_rule(number)
    value["definition"]["response"] = {"kind": "list", "title": "Menu", "description": "Escolha uma opção",
        "footer": "", "button_text": "Abrir", "sections": [{"id": uid(400 + number), "title": "",
        "rows": [{"id": uid(500 + number), "title": "Voltar", "description": "", "action": {
            "id": uid(600 + number), "type": "nested_list", "rule_id": uid(target), "delay_seconds": 1}}]}]}
    return value


def test_interactive_back_navigation_roundtrips_but_automatic_cycle_fails():
    value = document(with_media=False)
    value["rules"] = [list_rule(1, 2), list_rule(2, 1)]
    assert codec.read_archive(codec.write_archive(value, "zip"), "menus.zip") == value
    value["rules"] = [text_rule(1, action={"id": uid(101), "type": "nested_reply", "rule_id": uid(1), "delay_seconds": 1})]
    with pytest.raises(ValueError, match="referências"):
        codec.write_archive(value)


@pytest.mark.parametrize("target_kind", ["missing", "wrong_type", "device_scope"])
def test_import_rejects_missing_wrong_type_and_foreign_device_graph(target_kind):
    value = document(with_media=False)
    value["rules"][0]["definition"]["response"]["actions"] = [
        {"id": uid(101), "type": "nested_reply", "rule_id": uid(2), "delay_seconds": 1}]
    if target_kind == "wrong_type":
        value["rules"].append(list_rule(2, 2))
    if target_kind == "device_scope":
        child = text_rule(2)
        child["definition"]["device_id"] = uid(999)
        value["rules"].append(child)
    with pytest.raises(ValueError):
        codec.write_archive(value)


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "media/../../escape", "extra.txt", "folder/"])
def test_zip_has_only_manifest_declared_regular_members(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = document(with_media=False)
    with pytest.raises(ValueError):
        codec.read_archive(packed(source, [(name, b"never extracted")]), "orion.zip")
    assert list(tmp_path.iterdir()) == []


def test_symlink_encrypted_unknown_compression_and_duplicate_members_rejected():
    source = document(with_media=False)
    raw = packed(source)
    central = raw.index(b"PK\x01\x02")
    symbolic = bytearray(raw)
    struct.pack_into("<I", symbolic, central + 38, (stat.S_IFLNK | 0o777) << 16)
    encrypted = bytearray(raw)
    struct.pack_into("<H", encrypted, central + 8, 1)
    unsupported = packed(source, compression=zipfile.ZIP_BZIP2)
    with pytest.warns(UserWarning, match="Duplicate name"):
        duplicate = packed(source, [("chatbot.json", b"{}")])
    for hostile in (bytes(symbolic), bytes(encrypted), unsupported, duplicate):
        with pytest.raises(ValueError):
            codec.read_archive(hostile, "orion.zip")


def test_zip_embedded_nul_must_not_be_normalized_into_manifest_name():
    raw = packed(document(with_media=False), [("chatbot.jsonXhidden", b"{}")])
    raw = raw.replace(b"chatbot.jsonXhidden", b"chatbot.json\x00hidden")
    with pytest.raises(ValueError):
        codec.read_archive(raw, "orion.zip")
    # A lone normalized manifest must also be rejected, not just a collision.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("chatbot.jsonXhidden", json.dumps(document(with_media=False)))
    raw = stream.getvalue().replace(b"chatbot.jsonXhidden", b"chatbot.json\x00hidden")
    with pytest.raises(ValueError):
        codec.read_archive(raw, "orion.zip")


@pytest.mark.parametrize("mutation", ["prefix", "suffix", "truncated", "crc", "count", "expanded", "comment", "overlap"])
def test_corrupt_zip_and_declared_zip_bomb_fail_before_result(mutation):
    raw = bytearray(codec.write_archive(document(), "zip"))
    central = raw.index(b"PK\x01\x02")
    if mutation == "prefix":
        raw = b"executable stub" + raw
    elif mutation == "suffix":
        raw += b"trailing content"
    elif mutation == "truncated":
        raw = raw[:-1]
    elif mutation == "crc":
        struct.pack_into("<I", raw, central + 16, 0)
    elif mutation == "count":
        struct.pack_into("<HH", raw, len(raw) - 14, codec.MAX_ENTRIES + 1, codec.MAX_ENTRIES + 1)
    elif mutation == "expanded":
        struct.pack_into("<I", raw, central + 24, codec.MAX_EXPANDED_BYTES + 1)
    elif mutation == "comment":
        struct.pack_into("<H", raw, len(raw) - 2, 3)
        raw += b"abc"
    elif mutation == "overlap":
        struct.pack_into("<I", raw, central + 20, len(raw))
    with pytest.raises(ValueError):
        codec.read_archive(bytes(raw), "orion.zip")


@pytest.mark.parametrize("mutation", ["path", "missing_file", "tampered_file", "missing_thumb", "extra_thumb"])
def test_zip_manifest_paths_and_all_byte_hashes_are_enforced(mutation):
    manifest, files = unpacked(codec.write_archive(document(), "zip"))
    if mutation == "path":
        manifest["assets"][0]["file"] = "../other"
    elif mutation == "missing_file":
        files = files[1:]
    elif mutation == "tampered_file":
        files[0] = (files[0][0], b"X" * len(files[0][1]))
    elif mutation == "missing_thumb":
        files = files[:-1]
    elif mutation == "extra_thumb":
        files.append((f"media/{uid(201)}.thumb.jpg", b"unexpected"))
    with pytest.raises(ValueError):
        codec.read_archive(packed(manifest, files), "orion.zip")


@pytest.mark.parametrize("format", ["json", "zip"])
def test_export_never_truncates_when_file_limit_is_exceeded(monkeypatch, format):
    monkeypatch.setattr(codec, "IMPORT_BYTES", 100)
    with pytest.raises(ValueError, match="limite|10 MB"):
        codec.write_archive(document(), format)


def test_media_expansion_record_count_and_input_limit_are_enforced(monkeypatch):
    source = document()
    monkeypatch.setattr(codec, "MAX_EXPANDED_BYTES", 20)
    with pytest.raises(ValueError, match="limite"):
        codec.write_archive(source, "zip")
    monkeypatch.setattr(codec, "MAX_RULES", 1)
    with pytest.raises(ValueError, match="registros"):
        codec.write_archive(source, "zip")
    monkeypatch.setattr(codec, "IMPORT_BYTES", 10)
    with pytest.raises(ValueError):
        codec.read_archive(b" " * 11, "orion.json")


def test_manifest_serialization_stops_at_limit_without_building_complete_json(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Full JSON allocation must not be used")
    monkeypatch.setattr(codec.json, "dumps", forbidden)
    monkeypatch.setattr(codec, "MAX_MANIFEST_BYTES", 256)
    with pytest.raises(ValueError):
        codec.write_archive(document(), "zip")


def test_rule_url_remains_declarative_and_values_are_never_executed():
    source = document(with_media=False)
    source["rules"][0]["definition"]["response"]["actions"] = [{"id": uid(101), "type": "url",
        "delay_seconds": 1, "url": "https://example.com/private-token?key=synthetic", "method": "POST",
        "custom_template": "Resposta: {{reply}}", "as_file": False}]
    assert codec.read_archive(codec.write_archive(source), "orion.zip") == source
