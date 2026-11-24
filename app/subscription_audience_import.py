"""CSV/TXT decoding without network access, execution or silent row loss."""
from __future__ import annotations

import csv
from io import StringIO
import re
import unicodedata

from app import subscription_audience_schemas as schemas

PHONE_HEADERS = {"telefone", "phone", "phonee164", "whatsapp", "numero", "celular", "phone_number"}
NAME_HEADERS = {"nome", "name", "cliente", "contato"}


def header(value):
    return re.sub(r"[^a-z0-9_]", "", unicodedata.normalize("NFKD", value.strip().lower()).encode("ascii", "ignore").decode())


def delimiter(content):
    # Sniffer can be confused by a malformed row. Detect delimiters outside
    # quoted fields in the first nonblank physical record, retaining strict
    # csv.reader validation for the full file (including multiline cells).
    quotes, counts = False, {",": 0, ";": 0, "\t": 0}
    for char in content[:65536]:
        if char == '"':
            quotes = not quotes
        elif char in counts and not quotes:
            counts[char] += 1
        elif char in "\r\n" and not quotes and any(counts.values()):
            break
    return max(counts, key=counts.get) if any(counts.values()) else None


def parse(content, default_country="BR"):
    if not isinstance(content, str):
        raise ValueError("Conteúdo de arquivo inválido")
    if len(content.encode("utf-8")) > schemas.MAX_IMPORT_BYTES:
        raise ValueError("O arquivo excede 8 MiB; divida-o sem descartar linhas")
    if "\x00" in content or "\ufffd" in content:
        raise ValueError("O arquivo contém texto inválido; exporte como CSV UTF-8")
    schemas.country_code(default_country)
    content = content.removeprefix("\ufeff")
    sep = delimiter(content)
    reader = csv.reader(StringIO(content, newline=""), delimiter=sep or ",", strict=True)
    contacts, errors, sample, seen = [], [], [], {}
    counts = {"total": 0, "valid": 0, "invalid": 0, "duplicates": 0, "blank": 0, "unique": 0}
    columns = None
    first = True
    previous_line = 0
    fatal = False
    try:
        for cells in reader:
            line = previous_line + 1
            previous_line = reader.line_num
            if not cells or not any(cell.strip() for cell in cells):
                counts["blank"] += 1
                continue
            if first:
                first = False
                headings = [header(cell) for cell in cells]
                phone_indices = [i for i, value in enumerate(headings) if value in PHONE_HEADERS]
                name_indices = [i for i, value in enumerate(headings) if value in NAME_HEADERS]
                if phone_indices or name_indices:
                    if len(cells) != 2 or len(phone_indices) != 1 or len(name_indices) != 1:
                        errors.append({"line": line, "code": "invalid_header", "message": "O cabeçalho deve conter somente Telefone e Nome"})
                        fatal = True
                        break
                    columns = (phone_indices[0], name_indices[0])
                    continue
            counts["total"] += 1
            if counts["total"] > schemas.MAX_IMPORT_ROWS:
                raise ValueError("O arquivo excede 20.000 registros; divida-o antes de importar")
            try:
                if len(cells) != 2:
                    raise ValueError("Use duas colunas: Telefone e Nome; coloque separadores dentro de aspas")
                if columns is None:
                    # Infer once; never reinterpret each bad row as a new format.
                    try:
                        schemas.normalize_phone(cells[0], default_country)
                        columns = (0, 1)
                    except ValueError:
                        schemas.normalize_phone(cells[1], default_country)
                        columns = (1, 0)
                phone = schemas.normalize_phone(cells[columns[0]], default_country)
                if phone is None:
                    raise ValueError("O telefone está vazio")
                name = re.sub(r"[\r\n\t]+", " ", cells[columns[1]])
                name = schemas.clean(name, maximum=120, empty=False)
                duplicate = phone in seen
                counts["valid"] += 1
                if duplicate:
                    counts["duplicates"] += 1
                else:
                    seen[phone] = line
                    contacts.append({"line": line, "name": name, "phone_e164": phone})
                if len(sample) < 50:
                    sample.append({"line": line, "name": name, "phone_e164": phone, "duplicate": duplicate})
            except ValueError as exc:
                counts["invalid"] += 1
                errors.append({"line": line, "code": "invalid_row", "message": str(exc)})
    except csv.Error:
        counts["total"] += 1
        counts["invalid"] += 1
        errors.append({"line": previous_line + 1, "code": "malformed_csv", "message": "Aspas ou estrutura CSV inválidas; corrija o arquivo inteiro"})
        fatal = True
    counts["unique"] = len(contacts)
    if first:
        fatal = True
        errors.append({"line": 1, "code": "empty_file", "message": "O arquivo está vazio"})
    return {"contacts": contacts, "counts": counts, "errors": errors, "sample": sample,
            "fatal": fatal, "delimiter": sep or ","}


def safe_csv_cell(value):
    text = "" if value is None else str(value)
    # Leading whitespace does not prevent spreadsheet formula interpretation.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def csv_lines(rows):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["Telefone", "Nome", "Telegram", "E-mail", "Plano", "Valor", "Vencimento", "Grupos"])
    yield "\ufeff" + output.getvalue()
    for row in rows:
        output.seek(0)
        output.truncate(0)
        # No notes, service username or password in an ordinary export.
        writer.writerow([safe_csv_cell(value) for value in (
            row.get("phone_e164"), row.get("name"), row.get("telegram"), row.get("email"),
            row.get("plan_name"), "" if row.get("price_cents") is None else f'{row["price_cents"] / 100:.2f}',
            row.get("due_date"), ", ".join(group["name"] for group in row.get("groups", [])))])
        yield output.getvalue()


def csv_document(rows):
    return "".join(csv_lines(rows))
