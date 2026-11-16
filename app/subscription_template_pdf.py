"""Bounded PDF structure inspection, without rendering or changing its bytes.

The parser runs in a short-lived process so a malformed attachment cannot keep
the API's media thread busy indefinitely or allocate unbounded parser memory.
This verifies a readable document structure, not signatures or active content.
"""
from __future__ import annotations

import io
from pathlib import Path
import re
import subprocess
import sys


MAX_PDF_BYTES = 10_000_000
MAX_OBJECTS = 20_000
MAX_PAGES = 2_000
MAX_GRAPH_ENTRIES = 100_000
MAX_DEPTH = 64
MAX_DECODED_BYTES = 20_000_000
PARSER_TIMEOUT_SECONDS = 10


def _invalid():
    return ValueError("O PDF não contém uma estrutura completa e válida dentro dos limites permitidos.")


def _envelope(raw: bytes) -> None:
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_PDF_BYTES
            or not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n]", raw)):
        raise _invalid()
    ending = re.search(rb"(?:\r|\n)startxref[\x00\t\n\f\r ]+([0-9]{1,10})[\x00\t\n\f\r ]+%%EOF[\x00\t\n\f\r ]*\Z", raw[-2048:])
    if ending is None or not 8 <= int(ending[1]) < len(raw):
        raise _invalid()


def _inspect_structure(raw: bytes) -> None:
    # Import only inside the isolated worker. Configuration is context-local;
    # neither global parser limits nor the application's loggers are changed.
    from pypdf import PdfReader, apply_configuration
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

    _envelope(raw)
    with apply_configuration(
        maximum_declared_stream_length=MAX_PDF_BYTES,
        array_based_stream_maximum_output_length=MAX_DECODED_BYTES,
        array_maximum_element_count=MAX_OBJECTS,
        dictionary_maximum_element_count=MAX_OBJECTS,
        zlib_maximum_output_length=MAX_DECODED_BYTES,
        lzw_maximum_output_length=MAX_DECODED_BYTES,
        run_length_maximum_output_length=MAX_DECODED_BYTES,
        brotli_maximum_output_length=MAX_DECODED_BYTES,
        stream_decoding_work_maximum_length=MAX_DECODED_BYTES,
        page_tree_maximum_entries=MAX_PAGES * 2,
        page_tree_maximum_depth=MAX_DEPTH,
        page_tree_maximum_page_entries=MAX_GRAPH_ENTRIES,
        jbig2dec_binary=None,
    ):
        with PdfReader(io.BytesIO(raw), strict=True, root_object_recovery_limit=0,
                       xref_stream_maximum_entries=MAX_OBJECTS) as reader:
            if reader.is_encrypted:
                raise _invalid()
            count = sum(len(entries) for entries in reader.xref.values()) + len(reader.xref_objStm)
            if not 1 <= count <= MAX_OBJECTS:
                raise _invalid()
            root = reader.root_object
            if root.get("/Type") != "/Catalog" or "/Pages" not in root:
                raise _invalid()
            if not 1 <= len(reader.pages) <= MAX_PAGES:
                raise _invalid()
            # Visit the entire active cross-reference graph, including objects
            # outside the page tree, so a valid header does not hide broken
            # references or malformed trailing objects. Cycles such as /Parent
            # are legal; visit identities only once instead of recursing.
            pending = [(reader.trailer, 0)]
            for generation, entries in reader.xref.items():
                pending.extend((IndirectObject(number, generation, reader), 0)
                               for number in entries if number)
            pending.extend((IndirectObject(number, 0, reader), 0) for number in reader.xref_objStm)
            indirect, containers, visited = set(), set(), 0
            while pending:
                value, depth = pending.pop()
                visited += 1
                if visited > MAX_GRAPH_ENTRIES or depth > MAX_DEPTH:
                    raise _invalid()
                if isinstance(value, IndirectObject):
                    identity = value.idnum, value.generation
                    if identity in indirect:
                        continue
                    indirect.add(identity)
                    if value.idnum in reader.xref_objStm:
                        if value.generation != 0:
                            raise _invalid()
                    elif value.idnum not in reader.xref.get(value.generation, {}):
                        raise _invalid()
                    resolved = value.get_object()
                    if resolved is None:
                        raise _invalid()
                    pending.append((resolved, depth))
                elif isinstance(value, (DictionaryObject, ArrayObject)):
                    identity = id(value)
                    if identity in containers:
                        continue
                    containers.add(identity)
                    items = value.values() if isinstance(value, DictionaryObject) else value
                    pending.extend((child, depth + 1) for child in items)


def inspect_pdf(raw: bytes) -> bool:
    """Accept an unencrypted, bounded PDF, preserving the caller's exact bytes."""
    _envelope(raw)
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve()), "--inspect"],
            input=raw, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PARSER_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _invalid() from None
    if result.returncode != 0:
        raise _invalid()
    return True


def _worker() -> int:
    try:
        import resource
        # This worker is used on the Linux API/worker services, not in a browser.
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        _inspect_structure(sys.stdin.buffer.read(MAX_PDF_BYTES + 1))
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker() if sys.argv[1:] == ["--inspect"] else 1)
