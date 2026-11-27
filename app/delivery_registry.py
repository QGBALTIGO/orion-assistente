"""ANP lookup for the current merchant's CNPJ using the official GLP register."""
from datetime import datetime, timezone
import json

import httpx
from fastapi import HTTPException
from sqlalchemy import text

from app.brazilian_fields import normalize_tax_document


REGISTRY = "https://revendedoresapi.anp.gov.br/v1/glp"
REGISTRY_FIELDS = {"authorization": "autorizacao", "company": "razaoSocial", "distributor": "distribuidora",
                   "published_on": "dataPublicacao", "storage_class": "classe", "sigaf": "statusSIGAF"}


async def merchant(db, tenant):
    row = (await db.execute(text("""
        SELECT b.configuration->>'delivery_offering' AS offering,
               p.preferences->'business'->>'tax_document_kind' AS kind,
               p.preferences->'business'->>'tax_document' AS document
        FROM business_onboarding b LEFT JOIN management_preferences p USING (tenant_id)
        WHERE b.tenant_id=:t
    """), {"t": tenant})).mappings().one()
    if row["offering"] != "gas_agua":
        raise HTTPException(404, "Consulta de revenda disponível para Gás e Água")
    try:
        document = normalize_tax_document("cnpj", row["document"]) if row["kind"] == "cnpj" else None
    except ValueError:
        document = None
    if not document:
        raise HTTPException(422, "Confira o CNPJ da empresa em Configurações antes de consultar a ANP.")
    return {"cnpj": document}


def registry_result(data, document):
    if not isinstance(data, dict) or data.get("succeeded") is not True or not isinstance(data.get("data"), list):
        raise ValueError("Resposta inválida")
    entries = []
    for item in data["data"]:
        if not isinstance(item, dict) or normalize_tax_document("cnpj", item.get("cnpj")) != document:
            raise ValueError("CNPJ divergente")
        if not isinstance(item.get("autorizacao"), str) or not item["autorizacao"].strip():
            raise ValueError("Registro sem identificação")
        if any(item.get(key) is not None and not isinstance(item[key], str) for key in REGISTRY_FIELDS.values()):
            raise ValueError("Registro inesperado")
        entries.append({out: (item.get(key) or "").strip() or None for out, key in REGISTRY_FIELDS.items()})
    total = (data.get("searchPageFilter") or {}).get("totalRegistro")
    if total is not None and total != len(entries):
        raise ValueError("Resposta incompleta")
    return {"cnpj": document, "records": entries, "source": REGISTRY,
            "checked_at": datetime.now(timezone.utc).isoformat()}


async def lookup(document):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=2.0), follow_redirects=False) as client:
            async with client.stream("GET", REGISTRY, params={"cnpj": document, "numeropagina": 1}) as response:
                response.raise_for_status()
                body = bytearray()
                async for part in response.aiter_bytes(16384):
                    body.extend(part)
                    if len(body) > 131072:
                        raise ValueError("Resposta extensa")
                return registry_result(json.loads(body), document)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        raise HTTPException(503, "A consulta à ANP está indisponível. Tente novamente ou confira o cadastro no portal da agência.") from None
