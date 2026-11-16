"""Synthetic complete AXML fixtures; no APK download, install or execution."""
from struct import pack, unpack_from

import pytest

from app import subscription_template_apk as apk

NONE = apk.NONE


def length(value, utf8):
    if utf8:
        return bytes([value]) if value < 128 else bytes([0x80 | (value >> 8), value & 255])
    return pack("<H", value) if value < 32768 else pack("<HH", 0x8000 | (value >> 16), value & 65535)


def pool(strings, *, utf8=True, aliases=None, styles=None):
    data, offsets = bytearray(), []
    for value in strings:
        offsets.append(len(data))
        units = len(value.encode("utf-16-le")) // 2
        encoded = value.encode("utf-8" if utf8 else "utf-16-le")
        data.extend(length(units, utf8))
        if utf8:
            data.extend(length(len(encoded), True))
        data.extend(encoded + (b"\0" if utf8 else b"\0\0"))
    data.extend(b"\0" * (-len(data) % 4))
    entries = offsets if aliases is None else [offsets[index] for index in aliases]
    style_count = len(styles) if styles else 0
    strings_start = 28 + 4 * (len(entries) + style_count)
    style_data, style_offsets = bytearray(), []
    for spans in styles or []:
        if spans is None:
            style_offsets.append(NONE)
            continue
        style_offsets.append(len(style_data))
        for name, first, last in spans:
            style_data.extend(pack("<III", name, first, last))
        style_data.extend(pack("<III", NONE, NONE, NONE))
    styles_start = strings_start + len(data) if style_count else 0
    result = pack("<HHI", 1, 28, strings_start + len(data) + len(style_data))
    result += pack("<IIIII", len(entries), style_count, 0x100 if utf8 else 0, strings_start, styles_start)
    return result + b"".join(pack("<I", value) for value in entries + style_offsets) + data + style_data


def node(kind, extension, *, comment=NONE):
    return pack("<HHIII", kind, 16, 16 + len(extension), 1, comment) + extension


def attribute(name, data, *, ns=NONE, raw=NONE, kind=3, size=8, reserved=0):
    return pack("<IIIHBBI", ns, name, raw, size, reserved, kind, data)


def start(name=0, *, ns=NONE, attrs=(), id_index=0, class_index=0, style_index=0):
    return node(0x102, pack("<IIHHHHHH", ns, name, 20, 20, len(attrs), id_index, class_index, style_index) + b"".join(attrs))


def end(name=0, ns=NONE):
    return node(0x103, pack("<II", ns, name))


def namespace(prefix, uri, *, closing=False):
    return node(0x101 if closing else 0x100, pack("<II", prefix, uri))


def resource_map(*values):
    return pack("<HHI", 0x180, 8, 8 + len(values) * 4) + b"".join(pack("<I", value) for value in values)


def binary(*nodes, strings=("manifest",), utf8=True, string_pool=None):
    body = (pool(strings, utf8=utf8) if string_pool is None else string_pool) + b"".join(nodes)
    return pack("<HHI", 3, 8, 8 + len(body)) + body


def overwrite(raw, offset, value, format="<I"):
    data = bytearray(raw)
    data[offset:offset + len(pack(format, value))] = pack(format, value)
    return bytes(data)


@pytest.mark.parametrize("utf8", [True, False])
def test_complete_minimal_binary_manifest_has_real_pool_and_balanced_root(utf8):
    assert apk.inspect_apk_manifest(binary(start(), end(), utf8=utf8)) is True


@pytest.mark.parametrize("utf8", [True, False])
def test_compiled_manifest_namespaces_attributes_children_cdata_and_resource_ids(utf8):
    strings = ("manifest", "package", "example.synthetic", "application", "android",
               "http://schemas.android.com/apk/res/android", "label", "Aplicação 😀", "Text")
    data = binary(resource_map(0, 0, 0, 0, 0, 0, 0x1010001),
        namespace(4, 5), start(attrs=[attribute(1, 2, raw=2)]),
        start(3, attrs=[attribute(6, 7, ns=5, raw=7)]),
        node(0x104, pack("<IHBBI", 8, 8, 0, 3, 8)), end(3), end(), namespace(4, 5, closing=True),
        strings=strings, utf8=utf8)
    assert apk.inspect_apk_manifest(data)


@pytest.mark.parametrize("kind,data", [(0, 0), (1, 0x7F010001), (2, 0x1010001),
    (3, 2), (4, 0x3F800000), (5, 0x100), (6, 0x100), (7, 0x7F010001), (8, 0x1010001),
    (0x10, 24), (0x11, 0xFF), (0x12, 0xFFFFFFFF), (0x1C, 0xFF00FF00), (0x1D, 0x00FF00),
    (0x1E, 0xF0F0), (0x1F, 0x0F0)])
def test_known_typed_values_are_validated_without_resolving_other_apk_resources(kind, data):
    manifest = binary(start(attrs=[attribute(1, data, kind=kind)]), end(), strings=("manifest", "value", "text"))
    assert apk.inspect_apk_manifest(manifest)


@pytest.mark.parametrize("value,utf8", [("a" * 200, True), ("😀" * 70, True), ("a" * 32768, False)])
def test_variable_string_lengths_and_non_bmp_units_are_decoded_completely(value, utf8):
    assert apk.inspect_apk_manifest(binary(start(attrs=[attribute(1, 2)]), end(),
        strings=("manifest", "label", value), utf8=utf8))


def test_identical_pool_offsets_do_not_repeat_decoding_or_require_distinct_indexes():
    data = binary(start(0), end(1), string_pool=pool(["manifest"], aliases=[0, 0]))
    assert apk.inspect_apk_manifest(data)


def test_optional_bounded_style_spans_are_verified():
    data = binary(start(), end(), string_pool=pool(["manifest", "b"], styles=[[(1, 0, 2)]]))
    assert apk.inspect_apk_manifest(data)
    bad = binary(start(), end(), string_pool=pool(["manifest", "b"], styles=[[(1, 0, 99)]]))
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(bad)


@pytest.mark.parametrize("raw", [b"", b"\x03\x00\x08\x00\x08\x00\x00\x00", b"not XML",
    b"<application/>", b"<manifest>", b"<manifest/><manifest/>", b"\x03\x00"])
def test_header_or_markers_without_a_complete_manifest_are_rejected(raw):
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(raw)


@pytest.mark.parametrize("offset,value,format", [(0, 4, "<H"), (2, 12, "<H"),
    (4, 8, "<I"), (8, 0x9999, "<H"), (10, 8, "<H"), (12, 0x7FFFFFFC, "<I"),
    (16, 50001, "<I"), (20, 50001, "<I"), (24, 0x200, "<I"), (28, 28, "<I"),
    (32, 4, "<I"), (36, 0xFFFFFFFC, "<I")])
def test_chunk_pool_counts_offsets_flags_and_alignment_are_checked_before_reading(offset, value, format):
    valid = binary(start(), end())
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(overwrite(valid, offset, value, format))


def test_every_truncated_suffix_is_rejected_even_with_matching_outer_size():
    valid = binary(start(attrs=[attribute(1, 2)]), end(), strings=("manifest", "label", "sample"))
    for boundary in range(8, len(valid)):
        truncated = overwrite(valid[:boundary], 4, boundary)
        with pytest.raises(ValueError):
            apk.inspect_apk_manifest(truncated)


@pytest.mark.parametrize("nodes", [
    (start(),), (end(),), (start(), end(1)), (start(1), end(1)),
    (start(), end(), start(), end()),
    (start(), node(0x104, pack("<IHBBI", 9, 8, 0, 3, 0)), end()),
    (start(attrs=[attribute(1, 99)]), end()),
    (start(attrs=[attribute(9, 0)]), end()),
    (start(attrs=[attribute(1, 0, raw=99)]), end()),
    (start(attrs=[attribute(1, 0, ns=2)]), end()),
    (start(attrs=[attribute(1, 0), attribute(1, 0)]), end()),
    (start(attrs=[attribute(1, 0, size=4)]), end()),
    (start(attrs=[attribute(1, 0, reserved=1)]), end()),
    (start(attrs=[attribute(1, 0, kind=0xFF)]), end()),
    (start(attrs=[attribute(1, 0)], id_index=2), end()),
    (node(0x104, pack("<IHBBI", 0, 8, 0, 3, 0)), start(), end()),
])
def test_tree_references_attributes_and_values_cannot_point_outside_valid_structures(nodes):
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(binary(*nodes, strings=("manifest", "application", "not-a-namespace")))


def test_pool_payload_lengths_terminators_invalid_unicode_and_overlaps_fail_closed():
    base_pool = pool(["manifest"])
    data_start = unpack_from("<I", base_pool, 20)[0]
    variants = [
        overwrite(base_pool, data_start, 7, "<B"),  # Wrong UTF-16 count for eight characters.
        overwrite(base_pool, data_start + 1, 100, "<B"),
        overwrite(base_pool, data_start + 2, 0xFF, "<B"),
        overwrite(base_pool, data_start + 2 + 8, 1, "<B"),
        overwrite(base_pool, data_start + 2, 0, "<B"),
    ]
    overlapping = pool(["manifest", "unused"])
    variants.append(overwrite(overlapping, 32, 1))
    for string_pool in variants:
        with pytest.raises(ValueError):
            apk.inspect_apk_manifest(binary(start(), end(), string_pool=string_pool))


@pytest.mark.parametrize("sequence", [
    lambda: (namespace(1, 2), start(), end()),
    lambda: (namespace(1, 2, closing=True), start(), end()),
    lambda: (namespace(1, 2), namespace(1, 2), start(), end(), namespace(1, 2, closing=True)),
    lambda: (namespace(1, 2), start(ns=2), end(ns=2), namespace(1, 2, closing=True)),
    lambda: (start(), namespace(1, 2), end(), namespace(1, 2, closing=True)),
])
def test_namespace_scope_and_root_namespace_are_balanced(sequence):
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(binary(*sequence(), strings=("manifest", "android", "urn:android")))


def test_namespace_shadowing_is_allowed_but_must_close_at_its_own_depth():
    data = binary(namespace(1, 2), start(), namespace(1, 3), start(4, ns=3), end(4, ns=3),
        namespace(1, 3, closing=True), end(), namespace(1, 2, closing=True),
        strings=("manifest", "p", "urn:parent", "urn:child", "child"))
    assert apk.inspect_apk_manifest(data)


def test_resource_map_is_optional_once_and_before_xml_nodes():
    assert apk.inspect_apk_manifest(binary(resource_map(0), start(), end()))
    for nodes in ((resource_map(0), resource_map(0), start(), end()),
                  (resource_map(0, 1), start(), end()), (start(), resource_map(0), end())):
        with pytest.raises(ValueError):
            apk.inspect_apk_manifest(binary(*nodes))


@pytest.mark.parametrize("depth,accepted", [(64, True), (65, False)])
def test_binary_depth_bound(depth, accepted):
    value = binary(start(), *([start(1)] * (depth - 1)), *([end(1)] * (depth - 1)), end(),
                   strings=("manifest", "child"))
    if accepted:
        assert apk.inspect_apk_manifest(value)
    else:
        with pytest.raises(ValueError):
            apk.inspect_apk_manifest(value)


@pytest.mark.parametrize("raw", [
    b'<manifest package="example.synthetic"/>',
    b'\xef\xbb\xbf<?xml version="1.0" encoding="UTF-8"?><manifest><application/></manifest>',
    '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:label="Ol\u00e1 \U0001f600"/></manifest>'.encode(),
])
def test_utf8_text_manifest_uses_xml_parser_without_installation_requirements(raw):
    assert apk.inspect_apk_manifest(raw)


@pytest.mark.parametrize("raw", [
    b'<!DOCTYPE manifest><manifest/>', b'<!DOCTYPE manifest [<!ENTITY x "value">]><manifest/>',
    b'<!ENTITY x SYSTEM "file:///synthetic"><manifest/>', b'<manifest>\x00</manifest>',
    b'<manifest>&unknown;</manifest>', b'<?xml version="1.0" encoding="UTF-16"?><manifest/>',
    b'<manifest xmlns="urn:unexpected"/>', b'<manifest><x></manifest>', b'<manifest>\xff</manifest>',
])
def test_text_manifest_rejects_entities_external_declarations_invalid_encoding_and_structure(raw):
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(raw)


def test_text_depth_is_limited_during_incremental_parsing():
    assert apk.inspect_apk_manifest(b"<manifest>" + b"<x>" * 63 + b"</x>" * 63 + b"</manifest>")
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(b"<manifest>" + b"<x>" * 64 + b"</x>" * 64 + b"</manifest>")


@pytest.mark.parametrize("raw", [bytearray(b"<manifest/>"), "<manifest/>", b" " * (apk.MAX_MANIFEST_BYTES + 1)])
def test_input_type_and_total_size_are_bounded_before_parsing(raw):
    with pytest.raises(ValueError):
        apk.inspect_apk_manifest(raw)
