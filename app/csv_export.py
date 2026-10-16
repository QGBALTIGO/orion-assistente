"""Exportação de dados do estabelecimento, CSV Excel pt-BR, sem fórmula."""
import csv
import io

DANGEROUS = ("=", "+", "-", "@")


def safe_cell(value: object) -> str:
    if value is None:return ""
    v=str(value).replace("\r"," ").replace("\n"," ").replace("\x00","")
    # Neutraliza formula injection inclusive com espaços e tabulações anteriores.
    if v.lstrip().startswith(DANGEROUS) or v.startswith(("\t","\v","\f")):
        return "'" + v
    return v


def csv_document(headers: list[str], rows: list[tuple]) -> bytes:
    target=io.StringIO(newline="")
    writer=csv.writer(target,delimiter=";",quoting=csv.QUOTE_MINIMAL,lineterminator="\r\n")
    writer.writerow([safe_cell(x) for x in headers])
    for row in rows:
        writer.writerow([safe_cell(x) for x in row])
    return ("\ufeff"+target.getvalue()).encode("utf-8")
