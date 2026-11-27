"""Look up a Brazilian postal code without replacing delivery coverage checks."""
import asyncio

from fastapi import HTTPException
from httpx import AsyncClient, HTTPError, Timeout

from app.brazilian_fields import normalize_postal_code, normalize_state


_lookups = asyncio.Semaphore(8)


async def lookup(cep):
    try:
        postal_code = normalize_postal_code(cep)
    except ValueError:
        postal_code = None
    if not postal_code:
        raise HTTPException(422, "Informe um CEP com oito dígitos")
    if _lookups.locked():
        raise HTTPException(503, "A consulta de CEP está ocupada. Preencha o endereço ou tente novamente.")
    async with _lookups:
        try:
            async with AsyncClient(timeout=Timeout(4.0, connect=2.0), follow_redirects=False) as client:
                response = await client.get(f"https://viacep.com.br/ws/{postal_code.replace('-', '')}/json/")
                response.raise_for_status()
                data = response.json()
        except (HTTPError, ValueError):
            raise HTTPException(503, "Consulta indisponível. Preencha o endereço manualmente.") from None
    if isinstance(data, dict) and str(data.get("erro")).lower() == "true":
        raise HTTPException(404, "CEP não encontrado. Confira os números ou preencha o endereço.")
    try:
        if normalize_postal_code(data["cep"]) != postal_code:
            raise ValueError("postal_code_mismatch")
        state = normalize_state(data["uf"])
        city = data["localidade"].strip()[:120]
        if not city:
            raise ValueError("city_missing")
        return {"postal_code": postal_code, "state": state, "city": city,
                "street": (data.get("logradouro") or "").strip()[:160],
                "neighborhood": (data.get("bairro") or "").strip()[:120]}
    except (AttributeError, KeyError, TypeError, ValueError):
        raise HTTPException(503, "O CEP não retornou um endereço válido. Preencha os campos manualmente.") from None
