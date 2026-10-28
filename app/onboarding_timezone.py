"""Infer a Brazilian company's IANA timezone from the private onboarding address.

The setup's first step has no location. A new business temporarily uses
America/Sao_Paulo; when its operational address is saved, its timezone is
updated within the same tenant-scoped transaction. No IP/DDD inference.

Sources: Lei 12.876/2013 (Senado Federal) and TRE-AM's 25/09/2026 list of
the eleven western Amazonas municipalities (UTC-5).
"""
from __future__ import annotations

import unicodedata

WESTERN_AMAZONAS = frozenset({
    "amatura", "atalaia do norte", "benjamin constant", "eirunepe",
    "envira", "guajara", "ipixuna", "itamarati", "jutai",
    "sao paulo de olivenca", "tabatinga",
})
BR_STATES = frozenset({
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT",
    "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS",
    "RO", "RR", "SC", "SP", "SE", "TO",
})
REGIONAL_ZONES = {
    "AC": "America/Rio_Branco",
    "AM": "America/Manaus",
    "MT": "America/Cuiaba",
    "MS": "America/Campo_Grande",
    "RO": "America/Porto_Velho",
    "RR": "America/Boa_Vista",
    "PA": "America/Belem",
    "AP": "America/Belem",
    "TO": "America/Araguaina",
    "BA": "America/Bahia",
    "PE": "America/Recife",
    "AL": "America/Maceio",
    "SE": "America/Maceio",
    "MA": "America/Fortaleza",
    "PI": "America/Fortaleza",
    "CE": "America/Fortaleza",
    "RN": "America/Fortaleza",
    "PB": "America/Fortaleza",
}


def timezone_from_address(state: str, city: str) -> str | None:
    """Return a stable IANA zone, without trusting a user-provided timezone."""
    uf = str(state or "").strip().upper()
    if uf not in BR_STATES:
        return None
    normalized_city = unicodedata.normalize("NFKD", str(city or ""))
    city_key = " ".join(
        "".join(char for char in normalized_city if not unicodedata.combining(char))
        .casefold().split()
    )
    if uf == "AM" and city_key in WESTERN_AMAZONAS:
        return "America/Eirunepe"
    if uf == "PE" and city_key == "fernando de noronha":
        return "America/Noronha"
    return REGIONAL_ZONES.get(uf, "America/Sao_Paulo")
