"""Bounded attachment inspection; documents are never executed or rendered.

Opaque PDF/Office/APK contents are preserved, including their signatures. This
is format/integrity validation, not malware scanning or metadata removal.
"""
import io
import stat
import struct
import zipfile
import zlib
from xml.etree import ElementTree

from fastapi import HTTPException

from app.subscription_template_apk import inspect_apk_manifest
from app.subscription_template_pdf import inspect_pdf


DOCUMENT_MIMES = frozenset({
    "text/plain; charset=utf-8", "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.android.package-archive",
})
MAX_DOCUMENT_BYTES = 10_000_000
MAX_EXPANDED_BYTES = 100_000_000
MAX_DOCUMENT_ENTRIES = 2000
CONTENT_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"


def _invalid():
    return HTTPException(415, {"code": "CHATBOT_MEDIA_INVALID",
        "message": "O arquivo não corresponde a um documento PDF, DOCX, XLSX, APK ou texto válido."})


def _xml(raw):
    if not raw or len(raw) > 2_000_000:
        raise _invalid()
    try:
        value = raw.decode("utf-8-sig")
        if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
            raise ValueError()
        return ElementTree.fromstring(value)
    except (UnicodeError, ValueError, ElementTree.ParseError):
        raise _invalid() from None


def _zip_document(raw):
    try:
        # Bound descriptors before ZipFile allocates a central-directory list.
        # ZIP64 is unnecessary for a 10 MB attachment with at most 2,000 entries.
        end = raw.rfind(b"PK\x05\x06", max(0, len(raw) - 65_557))
        if end < 0 or end + 22 > len(raw):
            raise _invalid()
        disk, central_disk, disk_count, count, central_size, central_start, comment = struct.unpack(
            "<HHHHIIH", raw[end + 4:end + 22])
        if (disk or central_disk or disk_count != count or not 1 <= count <= MAX_DOCUMENT_ENTRIES
                or central_start + central_size != end or end + 22 + comment != len(raw)):
            raise _invalid()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_DOCUMENT_ENTRIES:
                raise _invalid()
            names, expanded = set(), 0
            for entry in entries:
                name = entry.filename
                mode = entry.external_attr >> 16
                expanded += entry.file_size
                if (not name or name.startswith("/") or "\\" in name or "\0" in name
                        or any(part in {".", ".."} for part in name.rstrip("/").split("/"))
                        or name in names or len(name) > 1024 or entry.flag_bits & 1
                        or stat.S_ISLNK(mode) or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or entry.file_size > 50_000_000 or expanded > MAX_EXPANDED_BYTES
                        or entry.file_size > max(1_000_000, entry.compress_size * 500)):
                    raise _invalid()
                names.add(name)
            # Read every entry to verify CRC and actual expanded bounds, without
            # extracting paths or passing a document to its application.
            total = 0
            for entry in entries:
                count = 0
                with archive.open(entry) as stream:
                    while chunk := stream.read(65_536):
                        count += len(chunk)
                        total += len(chunk)
                        if count > entry.file_size or total > MAX_EXPANDED_BYTES:
                            raise _invalid()
                if count != entry.file_size:
                    raise _invalid()
            if "AndroidManifest.xml" in names:
                inspect_apk_manifest(archive.read("AndroidManifest.xml"))
                return "application/vnd.android.package-archive"
            if not {"[Content_Types].xml", "_rels/.rels"} <= names:
                raise _invalid()
            types = _xml(archive.read("[Content_Types].xml"))
            rels = _xml(archive.read("_rels/.rels"))
            if types.tag != "{" + CONTENT_NAMESPACE + "}Types" or rels.tag != "{" + REL_NAMESPACE + "}Relationships":
                raise _invalid()
            formats = (
                ("word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                 "http://schemas.openxmlformats.org/wordprocessingml/2006/main", "document",
                 "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                 "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "workbook",
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            )
            matches = []
            for part, content_type, namespace, tag, mime in formats:
                if part not in names:
                    continue
                override = any(node.tag == "{" + CONTENT_NAMESPACE + "}Override"
                    and node.get("PartName") == "/" + part and node.get("ContentType") == content_type for node in types)
                relation = any(node.tag == "{" + REL_NAMESPACE + "}Relationship"
                    and node.get("Type", "").endswith("/officeDocument")
                    and node.get("Target", "").lstrip("/") == part
                    and node.get("TargetMode", "Internal") == "Internal" for node in rels)
                strict_namespace = namespace.replace("http://schemas.openxmlformats.org/", "http://purl.oclc.org/ooxml/").replace("/2006/main", "/main")
                if override and relation and _xml(archive.read(part)).tag in {"{" + namespace + "}" + tag, "{" + strict_namespace + "}" + tag}:
                    matches.append(mime)
            if len(matches) != 1:
                raise _invalid()
            return matches[0]
    except HTTPException:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error, NotImplementedError):
        raise _invalid() from None


def inspect_document(raw):
    """Return complete bytes and an allowlisted MIME derived from contents."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
        raise _invalid()
    if raw.startswith(b"%PDF-"):
        try:
            inspect_pdf(raw)
        except ValueError:
            raise _invalid() from None
        return raw, "application/pdf"
    if raw.startswith(b"PK\x03\x04"):
        return raw, _zip_document(raw)
    try:
        text = raw.decode("utf-8-sig")
        if not text.strip() or any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise ValueError()
    except (UnicodeDecodeError, ValueError):
        raise _invalid() from None
    return text.encode("utf-8"), "text/plain; charset=utf-8"
