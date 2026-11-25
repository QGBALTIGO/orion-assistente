"""Public Shopee BR departments with their affiliate catalog counterparts."""
from __future__ import annotations

import re

from .providers import ProviderError


# The public cat.* links and productOfferV2 intentionally use different IDs.
DEPARTMENTS = (
    ("moda", "Roupas Femininas", 11059998, 100017, "vestido feminino", "ModaFeminina"),
    ("casa", "Casa e Construção", 11059983, 100636, "jogo de panelas", "Casa"),
    ("plus-size", "Roupas Plus Size", 11116689, 100017, "plus size", "PlusSize"),
    ("beleza", "Beleza", 11059974, 100630, "hidratante facial", "Beleza"),
    ("moda-masculina", "Roupas Masculinas", 11059986, 100011, "camiseta masculina", "ModaMasculina"),
    ("sapatos-femininos", "Sapatos Femininos", 11059999, 100532, "sandália feminina", "SapatosFemininos"),
    ("sapatos-masculinos", "Sapatos Masculinos", 11059987, 100012, "tênis masculino", "SapatosMasculinos"),
    ("moda-infantil", "Moda Infantil", 11059973, 100633, "conjunto infantil", "ModaInfantil"),
    ("acessorios-moda", "Acessórios de Moda", 11059978, 100009, "colar feminino", "AcessóriosDeModa"),
    ("relogios", "Relógios", 11059996, 100534, "relógio de pulso", "Relógios"),
    ("celulares", "Celulares e Dispositivos", 11059988, 100013, "capinha iphone", "Celulares"),
    ("esportes", "Esportes e Lazer", 11059992, 100637, "halter musculação", "Esportes"),
    ("eletrodomesticos", "Eletrodomésticos", 11059984, 100010, "air fryer", "Eletrodomésticos"),
    ("brinquedos", "Brinquedos e Hobbies", 11059982, 100684, "blocos de montar brinquedo", "Brinquedos"),
    ("automoveis", "Automóveis", 11059972, 102187, "palheta limpador parabrisa", "Automóveis"),
    ("acessorios-veiculos", "Acessórios para Veículos", 11117089, 102249, "capa de banco carro", "AcessóriosVeiculares"),
    ("saude", "Saúde", 11059981, 100001, "oxímetro", "Saúde"),
    ("motocicletas", "Motocicletas", 11059990, 102251, "manete moto", "Motocicletas"),
    ("eletronicos", "Áudio", 11059971, 100535, "fone bluetooth", "Eletrônicos"),
    ("mae-bebe", "Mãe e Bebê", 11059989, 100632, "fralda descartável bebê", "MãeEBebê"),
    ("bolsas-femininas", "Bolsas Femininas", 11059997, 100016, "bolsa feminina transversal", "BolsasFemininas"),
    ("bolsas-masculinas", "Bolsas Masculinas", 11059985, 100533, "bolsa masculina transversal", "BolsasMasculinas"),
    ("pets", "Animais Domésticos", 11059991, 100631, "ração cachorro", "Pets"),
    ("papelaria", "Papelaria", 11059993, 100638, "caderno universitário", "Papelaria"),
    ("computadores", "Computadores e Acessórios", 11059977, 100644, "mouse sem fio", "Computadores"),
    ("alimentos", "Alimentos e Bebidas", 11059979, 100629, "café torrado", "Alimentos"),
    ("jogos", "Jogos e Consoles", 11059980, 100634, "controle playstation", "JogosEConsoles"),
    ("cameras-drones", "Câmeras e Drones", 11059976, 100635, "drone câmera", "CâmerasEDrones"),
    ("viagens", "Viagens e Bagagens", 11059995, 100015, "mala viagem bordo", "Viagens"),
)

PUBLIC_NICHES = {
    key: {"name": name, "site_category": site, "category": category, "keyword": term, "tag": tag}
    for key, name, site, category, term, tag in DEPARTMENTS
}

NICHES = {**PUBLIC_NICHES, "ferramentas": {
    "name": "Ferramentas", "site_category": 11059983, "category": 100715, "keyword": "furadeira", "tag": "Ferramentas",
}}

# These newer storefront departments share part of the affiliate taxonomy.
ADDITIONAL_CATEGORIES = {
    "plus-size": (100011, 100637), "motocicletas": (102271,), "automoveis": (100640,),
    "acessorios-veiculos": (100747, 102271), "brinquedos": (100639,),
}
ALTERNATE_TERMS = {"celulares": "película iphone", "brinquedos": "afinador violao", "automoveis": "chave roda carro"}
EXCLUDED_CATEGORIES = {"mae-bebe": {100684}, "automoveis": {102251, 102271}}
PLUS_SIZE = re.compile(r"\bplus[\s-]*size\b", re.I)
SORTS = {
    1: "Relevância",
    2: "Mais vendidos",
    3: "Maior preço",
    4: "Menor preço",
    5: "Maior comissão",
}


def matches_niche(key, offer):
    categories = set(offer.get("categories", []))
    if not categories.intersection((NICHES[key]["category"], *ADDITIONAL_CATEGORIES.get(key, ()))):
        return False
    if categories.intersection(EXCLUDED_CATEGORIES.get(key, ())):
        return False
    return key != "plus-size" or bool(PLUS_SIZE.search(offer["title"]))


async def search_niche(connector, niche, keyword, sort):
    spec = NICHES[niche]
    term = keyword or spec["keyword"]
    failed = None
    for category in (spec["category"], *ADDITIONAL_CATEGORIES.get(niche, ())):
        try:
            rows = await connector.search(term, limit=12, category=category, sort=sort)
        except ProviderError as exc:
            if exc.code != "schema":
                raise
            failed = exc
            try:
                alternate = keyword or ALTERNATE_TERMS.get(niche, term)
                rows = await connector.search(alternate, limit=3, category=category, sort=1)
            except ProviderError as fallback:
                if fallback.code != "schema":
                    raise
                continue
        matches = {row["identity"]: {**row, "niche": niche} for row in rows if matches_niche(niche, row)}
        if matches:
            return list(matches.values())
    if failed:
        raise failed
    return []


def niche_for_offer(offer):
    tailored = ("plus-size", "motocicletas", "acessorios-veiculos", "brinquedos")
    return next((key for key in (*tailored, *PUBLIC_NICHES) if matches_niche(key, offer)), None)
