"""Portable, bounded Orion Chatbot JSON/ZIP codec without DB or filesystem IO.

Media bytes are integrity-checked here and decoded by the authenticated importer
before becoming usable assets. URLs in rules are declarative data, never fetched.
Only this versioned Orion format is accepted; no BotBot compatibility is implied.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import hmac
import io
import json
import re
import stat
import struct
from typing import Literal
from uuid import UUID
import zipfile
import zlib

from pydantic import Field, ValidationError

from app.catalog_schemas import StrictModel
from app.subscription_chatbot_schemas import RuleDefinition, TemplateDefinition, IMPORT_BYTES, MAX_RULES, MAX_ASSETS
from app.subscription_chatbot_rules import validate_graph
from app.subscription_template_documents import DOCUMENT_MIMES
from app.subscription_templates_schemas import media_filename


FORMAT = "orion.subscription-chatbot"
VERSION = 1
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 1 + 2 * MAX_ASSETS
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 200_000
_COMMON = {"format", "version", "exported_at", "rules", "templates", "assets"}
_META = {"id", "kind", "mime", "size", "sha256", "width", "height", "duration_ms", "thumbnail"}
_SHA = re.compile(r"[a-f0-9]{64}")
_KINDS = {"image": ({"image/jpeg"}, 5_000_000), "sticker": ({"image/webp"}, 500_000),
          "video": ({"video/mp4"}, 16_000_000), "ptv": ({"video/mp4"}, 16_000_000),
          "audio": ({"audio/mpeg"}, 16_000_000), "document": (DOCUMENT_MIMES, 10_000_000)}


class RuleRecord(StrictModel):
    id: UUID
    creation_order: int = Field(strict=True, ge=1, le=2**63-1)
    definition: RuleDefinition


class TemplateRecord(StrictModel):
    id: UUID
    definition: TemplateDefinition


def _invalid(message="O arquivo não é uma exportação válida do Chatbot Orion."):
    return ValueError(message)


def _uuid(value):
    if not isinstance(value, (str, UUID)):
        raise _invalid("Uma referência de arquivo é inválida.")
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise _invalid("Uma referência de arquivo é inválida.") from None


def _object(value, allowed, *, required=None):
    if (not isinstance(value, dict) or set(value) - allowed
            or not (allowed if required is None else required) <= set(value)):
        raise _invalid("O arquivo contém campos ausentes ou desconhecidos.")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid("O arquivo contém limites inválidos.")
    return value


def _integrity(data, size, checksum):
    if (not isinstance(data, bytes) or len(data) != size or not isinstance(checksum, str)
            or not _SHA.fullmatch(checksum) or not hmac.compare_digest(sha256(data).hexdigest(), checksum)):
        raise _invalid("A integridade dos arquivos não pôde ser confirmada.")
    return data


def _asset_metadata(value):
    _object(value, _META)
    identity = _uuid(value["id"])
    kind = value["kind"]
    if not isinstance(kind, str) or kind not in _KINDS:
        raise _invalid("O tipo de mídia do arquivo é inválido.")
    mimes, limit = _KINDS[kind]
    size = _integer(value["size"], 1, limit)
    checksum = value["sha256"]
    if value["mime"] not in mimes or not isinstance(checksum, str) or not _SHA.fullmatch(checksum):
        raise _invalid("Os metadados da mídia são inválidos.")
    width, height, duration = value["width"], value["height"], value["duration_ms"]
    if kind == "audio":
        if width is not None or height is not None or value["thumbnail"] is not None:
            raise _invalid("Os metadados do áudio são inválidos.")
        _integer(duration, 1, 1_800_000)
    elif kind == "document":
        if width is not None or height is not None or duration is not None or value["thumbnail"] is not None:
            raise _invalid("Os metadados do documento são inválidos.")
    else:
        # Resizing a narrow accepted image can produce an edge below sixteen
        # pixels; metadata describes that normalized output, not input limits.
        minimum = 1 if kind == "image" else 16
        _integer(width, minimum, 4096)
        _integer(height, minimum, 4096)
        if kind in {"sticker", "ptv"} and (width != 512 or height != 512):
            raise _invalid("As dimensões da mídia são inválidas.")
        if kind in {"video", "ptv"}:
            _integer(duration, 1, 60_000 if kind == "ptv" else 180_000)
        elif kind == "sticker" and duration is not None:
            _integer(duration, 1, 6000)
        elif duration is not None:
            raise _invalid("A duração da mídia é inválida.")
        if kind == "sticker" and duration is None and size > 100_000:
            raise _invalid("A figurinha está acima do limite permitido.")
    thumbnail = value["thumbnail"]
    if thumbnail is not None:
        _object(thumbnail, {"mime", "size", "sha256"})
        _integer(thumbnail["size"], 1, 100_000)
        if (thumbnail["mime"] != "image/jpeg" or not isinstance(thumbnail["sha256"], str)
                or not _SHA.fullmatch(thumbnail["sha256"])):
            raise _invalid("A miniatura é inválida.")
    if kind in {"image", "video", "ptv"} and thumbnail is None:
        raise _invalid("A miniatura da mídia está ausente.")
    return {**value, "id": identity, "thumbnail": deepcopy(thumbnail)}


def _base(document):
    _object(document, _COMMON)
    if document["format"] != FORMAT or type(document["version"]) is not int or document["version"] != VERSION:
        raise _invalid("Formato ou versão de exportação não suportado.")
    exported_at = document["exported_at"]
    try:
        if not isinstance(exported_at, str) or len(exported_at) > 64:
            raise ValueError()
        parsed = datetime.fromisoformat(exported_at)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
    except (ValueError, TypeError):
        raise _invalid("A data da exportação é inválida.") from None
    for name, maximum in (("rules", MAX_RULES), ("templates", MAX_RULES), ("assets", MAX_ASSETS)):
        if not isinstance(document[name], list) or len(document[name]) > maximum:
            raise _invalid("A exportação ultrapassa o limite de registros.")
    try:
        rules = [RuleRecord.model_validate(value).model_dump(mode="json") for value in document["rules"]]
        templates = []
        for value in document["templates"]:
            normalized = TemplateRecord.model_validate(value).model_dump(mode="json")
            # Old version-1 archives retain their exact empty/default shape;
            # the newly optional fields travel only when explicitly present.
            for field in ("followup_text", "location", "file_name"):
                if field not in value["definition"]:
                    normalized["definition"].pop(field)
            templates.append(normalized)
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise _invalid("As definições das respostas ou templates são inválidas.") from None
    for records in (rules, templates):
        if len({record["id"] for record in records}) != len(records):
            raise _invalid("Há referências duplicadas no arquivo.")
    if len({rule["creation_order"] for rule in rules}) != len(rules):
        raise _invalid("A ordem das respostas está duplicada.")
    return {"format": FORMAT, "version": VERSION, "exported_at": exported_at,
            "rules": sorted(rules, key=lambda rule: (rule["creation_order"], rule["id"])),
            "templates": sorted(templates, key=lambda template: template["id"]), "assets": []}


def _closed_graph(document):
    assets = {asset["id"]: asset for asset in document["assets"]}
    if len(assets) != len(document["assets"]):
        raise _invalid("Há arquivos duplicados na exportação.")
    templates = {item["id"]: {"id": item["id"], **item["definition"]} for item in document["templates"]}
    for template in templates.values():
        identity = template["asset_id"]
        if identity is not None and (identity not in assets or assets[identity]["kind"] != template["kind"]):
            raise _invalid("Um template aponta para um arquivo ausente ou incompatível.")
        if identity is not None and template.get("file_name"):
            if media_filename(template["file_name"], assets[identity]["mime"]) != template["file_name"]:
                raise _invalid("O nome do arquivo não corresponde ao formato da mídia.")
    rules = {item["id"]: {"id": item["id"], "creation_order": item["creation_order"], **item["definition"]}
             for item in document["rules"]}
    try:
        validate_graph(rules, templates)
    except (ValueError, KeyError, TypeError, RecursionError):
        raise _invalid("As referências das respostas não formam um conjunto válido e completo.") from None
    document["assets"].sort(key=lambda asset: asset["id"])
    return document


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("O JSON contém campos duplicados.")
        result[key] = value
    return result


def _nonfinite(_):
    raise _invalid("O JSON contém um número inválido.")


def _json(raw):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_MANIFEST_BYTES:
        raise _invalid("O manifesto ultrapassa o limite permitido.")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
        stack, nodes = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
                raise ValueError()
            if isinstance(item, dict):
                stack.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                stack.extend((child, depth + 1) for child in item)
        return value
    except (ValueError, TypeError, UnicodeDecodeError, RecursionError):
        raise _invalid("O manifesto JSON é inválido ou ultrapassa os limites permitidos.") from None


def _decode(value, size, checksum):
    if not isinstance(value, str) or len(value) != ((size + 2) // 3) * 4:
        raise _invalid("A codificação de um arquivo é inválida.")
    try:
        data = base64.b64decode(value, validate=True)
        if base64.b64encode(data).decode("ascii") != value:
            raise ValueError()
    except (ValueError, binascii.Error):
        raise _invalid("A codificação de um arquivo é inválida.") from None
    return _integrity(data, size, checksum)


def _files(identity):
    return f"media/{identity}.bin", f"media/{identity}.thumb.jpg"


def _zip_info(raw):
    # Bound the central-directory entry count before asking zipfile to allocate
    # descriptors. Our format has no archive comment, multiple disks or ZIP64.
    if len(raw) < 22 or raw[:4] != b"PK\x03\x04" or raw[-22:-18] != b"PK\x05\x06":
        raise _invalid("O ZIP não é um arquivo Orion válido.")
    disk, central_disk, disk_count, count, central_size, central_start, comment = struct.unpack("<HHHHIIH", raw[-18:])
    if (disk or central_disk or comment or disk_count != count or not 1 <= count <= MAX_ENTRIES
            or central_start + central_size != len(raw) - 22):
        raise _invalid("A estrutura ou a quantidade de entradas do ZIP é inválida.")
    cursor, observed = central_start, 0
    while cursor < central_start + central_size:
        if raw[cursor:cursor + 4] != b"PK\x01\x02" or cursor + 46 > central_start + central_size:
            raise _invalid("O diretório do ZIP é inválido.")
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", raw, cursor + 28)
        if not 1 <= name_size <= 80 or extra_size or comment_size:
            raise _invalid("O ZIP contém caminhos ou metadados não permitidos.")
        cursor += 46 + name_size + extra_size + comment_size
        observed += 1
        if observed > count:
            raise _invalid("O diretório do ZIP é inválido.")
    if observed != count or cursor != central_start + central_size:
        raise _invalid("O diretório do ZIP está incompleto.")


def _read_entry(archive, info, *, maximum):
    if info.file_size < 0 or info.file_size > maximum:
        raise _invalid("Um arquivo expandido ultrapassa o limite permitido.")
    with archive.open(info, "r") as stream:
        data = stream.read(info.file_size + 1)
    if len(data) != info.file_size:
        raise _invalid("O ZIP contém um arquivo incompleto.")
    return data


def read_archive(raw: bytes, filename: str) -> dict:
    if (not isinstance(raw, bytes) or not 0 < len(raw) <= IMPORT_BYTES
            or not isinstance(filename, str) or not 0 < len(filename) <= 150
            or any(ord(c) < 32 for c in filename) or any(c in filename for c in "/\\:")):
        raise _invalid("Escolha um arquivo JSON ou ZIP de até 10 MB exportado pelo Orion.")
    if filename.lower().endswith(".json"):
        source = _json(raw)
        result = _base(source)
        expanded = len(raw)
        for item in source["assets"]:
            _object(item, _META | {"data_base64", "thumbnail_base64"})
            meta = _asset_metadata({key: item[key] for key in _META})
            data = _decode(item["data_base64"], meta["size"], meta["sha256"])
            if meta["thumbnail"]:
                thumb = _decode(item["thumbnail_base64"], meta["thumbnail"]["size"], meta["thumbnail"]["sha256"])
            else:
                if item["thumbnail_base64"] is not None:
                    raise _invalid("A miniatura não corresponde ao manifesto.")
                thumb = None
            expanded += len(data) + len(thumb or b"")
            if expanded > MAX_EXPANDED_BYTES:
                raise _invalid("O conteúdo expandido ultrapassa 40 MB.")
            result["assets"].append({**meta, "data": data, "thumbnail_data": thumb})
        return _closed_graph(result)
    if not filename.lower().endswith(".zip"):
        raise _invalid("Selecione um arquivo JSON ou ZIP exportado pelo Orion.")
    _zip_info(raw)
    try:
        with zipfile.ZipFile(io.BytesIO(raw), "r", allowZip64=False) as archive:
            infos = archive.infolist()
            if (len(infos) > MAX_ENTRIES or archive.comment or len({entry.filename for entry in infos}) != len(infos)
                    or sum(entry.file_size for entry in infos) > MAX_EXPANDED_BYTES):
                raise _invalid("O ZIP contém duplicatas ou ultrapassa o limite expandido de 40 MB.")
            for info in infos:
                file_type = stat.S_IFMT(info.external_attr >> 16)
                if (info.filename != info.orig_filename or "\x00" in info.orig_filename
                        or info.is_dir() or info.flag_bits & ~0x808 or info.extra or info.comment
                        or file_type not in {0, stat.S_IFREG}
                        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise _invalid("O ZIP contém entradas não permitidas.")
            entries = {entry.filename: entry for entry in infos}
            if "chatbot.json" not in entries:
                raise _invalid("O manifesto chatbot.json está ausente.")
            source = _json(_read_entry(archive, entries["chatbot.json"], maximum=MAX_MANIFEST_BYTES))
            result, expected = _base(source), {"chatbot.json"}
            for item in source["assets"]:
                _object(item, _META | {"file", "thumbnail_file"})
                meta = _asset_metadata({key: item[key] for key in _META})
                original_name, thumb_name = _files(meta["id"])
                if item["file"] != original_name or original_name not in entries:
                    raise _invalid("O caminho do arquivo não corresponde ao manifesto.")
                expected.add(original_name)
                data = _integrity(_read_entry(archive, entries[original_name], maximum=meta["size"]),
                                  meta["size"], meta["sha256"])
                if meta["thumbnail"]:
                    if item["thumbnail_file"] != thumb_name or thumb_name not in entries:
                        raise _invalid("A miniatura do manifesto está ausente.")
                    expected.add(thumb_name)
                    thumb = _integrity(_read_entry(archive, entries[thumb_name], maximum=meta["thumbnail"]["size"]),
                                       meta["thumbnail"]["size"], meta["thumbnail"]["sha256"])
                else:
                    if item["thumbnail_file"] is not None:
                        raise _invalid("O manifesto não permite essa miniatura.")
                    thumb = None
                result["assets"].append({**meta, "data": data, "thumbnail_data": thumb})
            if set(entries) != expected:
                raise _invalid("O ZIP contém arquivos fora do manifesto.")
            return _closed_graph(result)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, OSError, zlib.error, struct.error):
        raise _invalid("O ZIP está corrompido, incompleto ou usa um formato não permitido.") from None


class _BoundedOutput(io.BytesIO):
    def write(self, value):
        if self.tell() + len(value) > IMPORT_BYTES:
            raise _invalid("A exportação ultrapassa 10 MB. Reduza a seleção de respostas.")
        return super().write(value)


def _json_bytes(value):
    try:
        output = io.BytesIO()
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False)
        for chunk in encoder.iterencode(value):
            encoded = chunk.encode("utf-8")
            if output.tell() + len(encoded) + 1 > MAX_MANIFEST_BYTES:
                raise _invalid("A exportação ultrapassa o limite. Reduza a seleção de respostas.")
            output.write(encoded)
        output.write(b"\n")
        return output.getvalue()
    except (ValueError, TypeError, UnicodeEncodeError, RecursionError):
        raise _invalid("Não foi possível gerar o manifesto JSON.") from None


def write_archive(document: dict, format: Literal["json", "zip"] = "zip") -> bytes:
    if not isinstance(format, str) or format not in {"json", "zip"}:
        raise _invalid("Formato de exportação não suportado.")
    result = _base(document)
    expanded, encoded_lower_bound = 0, 0
    for item in document["assets"]:
        _object(item, _META | {"data", "thumbnail_data"})
        meta = _asset_metadata({key: item[key] for key in _META})
        data = _integrity(item["data"], meta["size"], meta["sha256"])
        if meta["thumbnail"]:
            thumb = _integrity(item["thumbnail_data"], meta["thumbnail"]["size"], meta["thumbnail"]["sha256"])
        else:
            if item["thumbnail_data"] is not None:
                raise _invalid("A miniatura não corresponde ao manifesto.")
            thumb = None
        expanded += len(data) + len(thumb or b"")
        encoded_lower_bound += ((len(data) + 2) // 3) * 4 + ((len(thumb or b"") + 2) // 3) * 4
        if expanded > MAX_EXPANDED_BYTES or format == "json" and encoded_lower_bound > IMPORT_BYTES:
            raise _invalid("A exportação ultrapassa o limite. Reduza a seleção de respostas.")
        result["assets"].append({**meta, "data": data, "thumbnail_data": thumb})
    _closed_graph(result)
    manifest = {**result, "assets": []}
    files = []
    for asset in result["assets"]:
        meta = {key: asset[key] for key in _META}
        if format == "json":
            manifest["assets"].append({**meta, "data_base64": base64.b64encode(asset["data"]).decode("ascii"),
                "thumbnail_base64": base64.b64encode(asset["thumbnail_data"]).decode("ascii") if asset["thumbnail_data"] else None})
        else:
            name, thumb_name = _files(asset["id"])
            manifest["assets"].append({**meta, "file": name, "thumbnail_file": thumb_name if asset["thumbnail_data"] else None})
            files.append((name, asset["data"]))
            if asset["thumbnail_data"]:
                files.append((thumb_name, asset["thumbnail_data"]))
    encoded = _json_bytes(manifest)
    if len(encoded) > MAX_MANIFEST_BYTES or (format == "zip" and len(encoded) + expanded > MAX_EXPANDED_BYTES):
        raise _invalid("A exportação ultrapassa o limite. Reduza a seleção de respostas.")
    if format == "json":
        if len(encoded) > IMPORT_BYTES:
            raise _invalid("A exportação ultrapassa 10 MB. Reduza a seleção de respostas.")
        return encoded
    output = _BoundedOutput()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False, compresslevel=6) as archive:
        for name, data in [("chatbot.json", encoded), *files]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=6)
    return output.getvalue()
