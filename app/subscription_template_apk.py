"""Bounded Android manifest format validation, with no package execution.

The binary layout follows AOSP's ResChunk_header, ResStringPool_header,
ResXMLTree_node/attrExt/attribute and Res_value declarations:
https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/libs/androidfw/include/androidfw/ResourceTypes.h

This independently implemented reader checks a complete XML tree, not APK
signatures, resource resolution, installation eligibility or application code.
"""
from __future__ import annotations

import re
import struct
from xml.etree import ElementTree


MAX_MANIFEST_BYTES = 2_000_000
MAX_STRINGS = 50_000
MAX_DEPTH = 64
MAX_NODES = 50_000
MAX_ATTRIBUTES = 100_000
NONE = 0xFFFFFFFF
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
VALUE_TYPES = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 0x10, 0x11, 0x12, 0x1C, 0x1D, 0x1E, 0x1F})


def _invalid():
    return ValueError("O manifesto Android não contém uma estrutura XML completa e válida.")


def _u32(raw, offset, end):
    if offset < 0 or offset + 4 > end:
        raise _invalid()
    return struct.unpack_from("<I", raw, offset)[0]


def _chunk(raw, offset, end):
    if offset < 0 or offset + 8 > end:
        raise _invalid()
    kind, header, size = struct.unpack_from("<HHI", raw, offset)
    if header < 8 or size < header or (header | size) & 3 or offset + size > end:
        raise _invalid()
    return kind, header, offset + size


def _xml_characters(value):
    if any((ord(char) < 32 and char not in "\t\n\r") or ord(char) in {0xFFFE, 0xFFFF}
           or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _invalid()
    return value


def _name_start(char):
    if char == "_" or "A" <= char <= "Z" or "a" <= char <= "z":
        return True
    value = ord(char)
    return any(first <= value <= last for first, last in (
        (0xC0, 0xD6), (0xD8, 0xF6), (0xF8, 0x2FF), (0x370, 0x37D), (0x37F, 0x1FFF),
        (0x200C, 0x200D), (0x2070, 0x218F), (0x2C00, 0x2FEF), (0x3001, 0xD7FF),
        (0xF900, 0xFDCF), (0xFDF0, 0xFFFD), (0x10000, 0xEFFFF)))


def _name_character(char):
    return (_name_start(char) or "0" <= char <= "9" or char in "-.\u00b7"
            or 0x300 <= ord(char) <= 0x36F or 0x203F <= ord(char) <= 0x2040)


def _length(raw, position, end, utf8):
    width, high = (1, 0x80) if utf8 else (2, 0x8000)
    if position + width > end:
        raise _invalid()
    first = raw[position] if utf8 else struct.unpack_from("<H", raw, position)[0]
    position += width
    if first & high:
        if position + width > end:
            raise _invalid()
        second = raw[position] if utf8 else struct.unpack_from("<H", raw, position)[0]
        position += width
        first = ((first & (high - 1)) << (width * 8)) | second
    return first, position


class _Pool:
    def __init__(self, raw, start, header, end):
        if header != 28:
            raise _invalid()
        count, style_count, flags, strings_start, styles_start = struct.unpack_from("<IIIII", raw, start + 8)
        if (not 1 <= count <= MAX_STRINGS or style_count > count or flags & ~0x101
                or strings_start < header + 4 * (count + style_count) or strings_start & 3
                or start + strings_start >= end):
            raise _invalid()
        if style_count:
            if styles_start & 3 or styles_start <= strings_start or start + styles_start >= end:
                raise _invalid()
        elif styles_start:
            raise _invalid()
        entries = [_u32(raw, start + header + 4 * index, end) for index in range(count)]
        data_start, data_end = start + strings_start, start + styles_start if style_count else end
        offsets = sorted(set(entries))
        values, lengths = {}, {}
        utf8 = bool(flags & 0x100)
        for index, offset in enumerate(offsets):
            bound = data_start + offsets[index + 1] if index + 1 < len(offsets) else data_end
            position = data_start + offset
            if position < data_start or position >= data_end or bound > data_end or (not utf8 and offset & 1):
                raise _invalid()
            units, position = _length(raw, position, bound, utf8)
            if utf8:
                byte_count, position = _length(raw, position, bound, True)
                finish = position + byte_count
                if finish + 1 > bound or raw[finish] != 0:
                    raise _invalid()
                value = raw[position:finish].decode("utf-8")
                if len(value.encode("utf-16-le")) // 2 != units:
                    raise _invalid()
            else:
                finish = position + 2 * units
                if finish + 2 > bound or raw[finish:finish + 2] != b"\0\0":
                    raise _invalid()
                value = raw[position:finish].decode("utf-16-le")
            values[offset], lengths[offset] = _xml_characters(value), units
        self.values = [values[offset] for offset in entries]
        self.lengths = [lengths[offset] for offset in entries]
        self.names = set()
        if style_count:
            self._styles(raw, start, header, end, count, style_count, styles_start)

    def _styles(self, raw, start, header, end, count, style_count, styles_start):
        # Alias style offsets are legal. Validate their spans once against the
        # shortest associated string, preventing quadratic repeated scans.
        spans = {}
        for index in range(style_count):
            offset = _u32(raw, start + header + 4 * (count + index), end)
            if offset != NONE:
                spans[offset] = min(spans.get(offset, self.lengths[index]), self.lengths[index])
        offsets = sorted(spans)
        total = 0
        for index, offset in enumerate(offsets):
            position = start + styles_start + offset
            bound = start + styles_start + offsets[index + 1] if index + 1 < len(offsets) else end
            if offset & 3 or position >= end or bound > end:
                raise _invalid()
            while True:
                name = _u32(raw, position, bound)
                if name == NONE:
                    break
                if position + 12 > bound:
                    raise _invalid()
                self.ref(name)
                first, last = struct.unpack_from("<II", raw, position + 4)
                total += 1
                if total > MAX_STRINGS or first > last or last >= spans[offset]:
                    raise _invalid()
                position += 12

    def ref(self, index, *, optional=False):
        if optional and index == NONE:
            return None
        if index >= len(self.values):
            raise _invalid()
        return self.values[index]

    def name(self, index):
        value = self.ref(index)
        if value not in self.names:
            if not value or not _name_start(value[0]) or not all(_name_character(char) for char in value[1:]):
                raise _invalid()
            self.names.add(value)
        return value


def _typed_value(raw, offset, end, pool):
    if offset + 8 > end:
        raise _invalid()
    size, reserved, kind, data = struct.unpack_from("<HBBI", raw, offset)
    if size != 8 or reserved != 0 or kind not in VALUE_TYPES:
        raise _invalid()
    if kind == 3:
        pool.ref(data)


def _binary_manifest(raw):
    kind, header, end = _chunk(raw, 0, len(raw))
    if kind != 3 or header != 8 or end != len(raw):
        raise _invalid()
    position, pool, mapped = header, None, False
    elements, namespaces, bindings, uris = [], [], set(), {}
    root_seen, root_closed, nodes, attributes = False, False, 0, 0

    def namespace(index):
        value = pool.ref(index, optional=True)
        if value and value != XML_NAMESPACE and not uris.get(value):
            raise _invalid()
        return value or None

    while position < end:
        kind, header, finish = _chunk(raw, position, end)
        nodes += 1
        if nodes > MAX_NODES * 4:
            raise _invalid()
        if kind == 1:
            if pool is not None or position != 8:
                raise _invalid()
            pool = _Pool(raw, position, header, finish)
        elif kind == 0x180:
            if pool is None or mapped or root_seen or namespaces or header != 8 or (finish - position - header) // 4 > len(pool.values):
                raise _invalid()
            mapped = True
        elif kind in {0x100, 0x101, 0x102, 0x103, 0x104}:
            if pool is None or header != 16:
                raise _invalid()
            pool.ref(_u32(raw, position + 12, finish), optional=True)
            extension = position + header
            if kind in {0x100, 0x101}:
                if extension + 8 != finish:
                    raise _invalid()
                prefix_id, uri_id = struct.unpack_from("<II", raw, extension)
                prefix = pool.ref(prefix_id, optional=True) or ""
                if prefix:
                    pool.name(prefix_id)
                uri = pool.ref(uri_id)
                binding = prefix, uri, len(elements)
                if prefix == "xmlns" or prefix == "xml" and uri != XML_NAMESPACE or prefix and not uri:
                    raise _invalid()
                if kind == 0x100:
                    if (root_closed or len(namespaces) >= MAX_STRINGS
                            or (prefix, len(elements)) in bindings):
                        raise _invalid()
                    namespaces.append(binding)
                    bindings.add((prefix, len(elements)))
                    uris[uri] = uris.get(uri, 0) + 1
                else:
                    if not namespaces or namespaces[-1] != binding:
                        raise _invalid()
                    namespaces.pop()
                    bindings.remove((prefix, len(elements)))
                    uris[uri] -= 1
            elif kind == 0x102:
                if extension + 20 > finish or root_closed or len(elements) >= MAX_DEPTH:
                    raise _invalid()
                ns_id, name_id, attr_start, attr_size, count, id_index, class_index, style_index = struct.unpack_from("<IIHHHHHH", raw, extension)
                name, ns = pool.name(name_id), namespace(ns_id)
                if (attr_start < 20 or attr_start & 3 or attr_size < 20 or attr_size & 3
                        or extension + attr_start + attr_size * count != finish
                        or any(value > count for value in (id_index, class_index, style_index))):
                    raise _invalid()
                if not elements:
                    if root_seen or name != "manifest" or ns is not None:
                        raise _invalid()
                    root_seen = True
                attributes += count
                if attributes > MAX_ATTRIBUTES:
                    raise _invalid()
                found = set()
                for index in range(count):
                    cursor = extension + attr_start + attr_size * index
                    attr_ns, attr_name, raw_value = struct.unpack_from("<III", raw, cursor)
                    key = namespace(attr_ns), pool.name(attr_name)
                    if key in found:
                        raise _invalid()
                    found.add(key)
                    pool.ref(raw_value, optional=True)
                    _typed_value(raw, cursor + 12, cursor + attr_size, pool)
                elements.append((ns, name))
            elif kind == 0x103:
                if extension + 8 != finish or not elements:
                    raise _invalid()
                ns_id, name_id = struct.unpack_from("<II", raw, extension)
                if elements.pop() != (namespace(ns_id), pool.name(name_id)):
                    raise _invalid()
                if not elements:
                    root_closed = True
            else:
                if extension + 12 != finish or not elements:
                    raise _invalid()
                pool.ref(_u32(raw, extension, finish))
                _typed_value(raw, extension + 4, finish, pool)
        else:
            raise _invalid()
        position = finish
    if pool is None or not root_seen or not root_closed or elements or namespaces:
        raise _invalid()


def _text_manifest(raw):
    value = _xml_characters(raw.decode("utf-8-sig"))
    if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
        raise _invalid()
    declaration = re.match(r"\s*<\?xml\s+[^?]*encoding\s*=\s*(['\"])([^'\"]+)\1", value, re.I)
    if declaration and declaration[2].lower() not in {"utf-8", "utf8", "us-ascii", "ascii"}:
        raise _invalid()
    parser = ElementTree.XMLPullParser(events=("start", "end"))
    depth, nodes, attributes, root_seen, closed = 0, 0, 0, False, False

    def events():
        nonlocal depth, nodes, attributes, root_seen, closed
        for event, element in parser.read_events():
            if event == "start":
                if closed or depth == 0 and (root_seen or element.tag != "manifest"):
                    raise _invalid()
                root_seen = True
                depth += 1
                nodes += 1
                attributes += len(element.attrib)
                if depth > MAX_DEPTH or nodes > MAX_NODES or attributes > MAX_ATTRIBUTES:
                    raise _invalid()
            else:
                depth -= 1
                if depth == 0:
                    closed = True
                element.clear()

    for offset in range(0, len(value), 8192):
        parser.feed(value[offset:offset + 8192])
        events()
    parser.close()
    events()
    if depth or not root_seen or not closed:
        raise _invalid()


def inspect_apk_manifest(raw: bytes) -> bool:
    """Accept a complete bounded AXML/UTF-8 manifest, or raise ValueError."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise _invalid()
    try:
        if raw[:2] == b"\x03\x00":
            _binary_manifest(raw)
        else:
            _text_manifest(raw)
    except (UnicodeError, struct.error, ElementTree.ParseError, ValueError, OverflowError):
        raise _invalid() from None
    return True
