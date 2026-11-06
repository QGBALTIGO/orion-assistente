"""Catalogue completeness and natural navigation through the real presenter."""
import json
from uuid import uuid4

import pytest

from app.qr_assistant_catalog import handle_request
from app.qr_assistant_interactions import Capabilities, present
from app.qr_presentation import message_units


class CatalogueRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **_):
        self.values[key] = value

    async def delete(self, key):
        self.values.pop(key, None)


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['valores', 'preços', 'tabela de preços', 'valores dos serviços',
    'preços de todos os serviços', 'Me mostra os valores, por favor', 'todos os serviços'])
async def test_general_price_requests_use_complete_paginated_catalogue_instead_of_twelve_item_dump(message):
    redis, state, rows = CatalogueRedis(), {}, services(14)
    result = await handle_request(message, state, {}, rows, redis, 'catalogue:generic-prices')
    assert result['_catalog_page']['total'] == 14 and result['_catalog_page']['pages'] == 2
    assert len(result['_catalog_page']['rows']) == 8 and '12 dos' not in result['answer']


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['Quanto custa o corte?', 'valores do corte',
    'preços dos serviços e agende amanhã', 'tabela de preços, mas cancele meu horário'])
async def test_specific_prices_and_mixed_operations_keep_their_own_interpretation(message):
    assert await handle_request(message, {}, {}, services(14), CatalogueRedis(), 'catalogue:mixed') is None


def services(count):
    return [{"id": uuid4(), "name": f"Serviço cadastrado {n + 1:03}", "price_cents": 2500,
             "duration_min": 40, "price_mode": "fixed"} for n in range(count)]


async def catalogue(body, state, rows, redis, tenant, namespace):
    result = await handle_request(body, state, {"name": "Empresa sintética"}, rows, redis,
                                  f"orion:qr:ai:{tenant}:{namespace}")
    assert result is not None, body
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 6, 8, 9, 10])
async def test_up_to_ten_services_fit_one_native_list_without_hidden_items(count):
    tenant, namespace, redis, state = uuid4(), "catalogue:one", CatalogueRedis(), {}
    rows = services(count)
    result = await catalogue("Quais serviços vocês têm?", state, rows, redis, tenant, namespace)
    assert result["_catalog_page"]["pages"] == 1
    assert [row["id"] for row in result["_catalog_page"]["rows"]] == [row["id"] for row in rows]
    response = await present(redis, tenant, "5567999999999", namespace, result,
                             Capabilities(assistant_interactions=1))
    assert len(response["presentation"]["action"]["sections"][0]["rows"]) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [11, 14, 16, 17, 150, 500])
async def test_all_services_are_reachable_once_without_exceeding_ten_native_rows(count):
    tenant, namespace, redis, state = uuid4(), "catalogue:all", CatalogueRedis(), {}
    rows, seen = services(count), []
    result = await catalogue("Quais serviços vocês têm?", state, rows, redis, tenant, namespace)
    page_count = result["_catalog_page"]["pages"]
    if count == 14:
        assert page_count == 2
    for page in range(page_count):
        if page:
            result = await catalogue("Pode mostrar mais serviços?", state, rows, redis, tenant, namespace)
        shown = result["_catalog_page"]["rows"]
        assert 1 <= len(shown) <= 8
        seen.extend(str(row["id"]) for row in shown)
        response = await present(redis, tenant, "5567999999999", namespace, result,
                                 Capabilities(assistant_interactions=1))
        action = response["presentation"]["action"]
        options = [row for section in action["sections"] for row in section["rows"]]
        assert len(options) <= 10
        assert all(message_units(row["title"]) <= 24 and message_units(row["description"]) <= 72
                   for row in options)
        record = json.loads(redis.values[f"orion:qr:ai:{tenant}:{namespace}:presentation"])
        assert {choice["service_id"] for choice in record["choices"].values() if choice["kind"] == "service"} == {
            str(row["id"]) for row in shown}
    assert seen == [str(row["id"]) for row in rows]
    assert len(seen) == len(set(seen)) == count


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Pode mostrar mais serviços?", "Me mostra mais serviços, por favor",
    "Quero ver os outros serviços", "Tem mais serviços?", "Próxima página, por favor"])
async def test_polite_next_page_is_navigation_not_a_giant_price_dump(message):
    tenant, namespace, redis, state = uuid4(), "catalogue:next", CatalogueRedis(), {}
    rows = services(14)
    first = await catalogue("Quais serviços tem", state, rows, redis, tenant, namespace)
    first_ids = {row["id"] for row in first["_catalog_page"]["rows"]}
    result = await catalogue(message, state, rows, redis, tenant, namespace)
    assert result["step"] == "catalog" and result["_catalog_page"]["page"] == 1
    assert first_ids.isdisjoint(row["id"] for row in result["_catalog_page"]["rows"])
    assert "profissional prefere" not in result["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Voltar para a página anterior", "Pode mostrar os serviços anteriores?",
                                      "Página anterior, por favor"])
async def test_previous_page_keeps_the_same_query_and_catalogue(message):
    tenant, namespace, redis, state = uuid4(), "catalogue:previous", CatalogueRedis(), {}
    rows = services(14)
    first = await catalogue("Quais serviços tem", state, rows, redis, tenant, namespace)
    await catalogue("Mais serviços", state, rows, redis, tenant, namespace)
    result = await catalogue(message, state, rows, redis, tenant, namespace)
    assert result["_catalog_page"]["page"] == 0
    assert result["_catalog_page"]["rows"] == first["_catalog_page"]["rows"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Pode mostrar mais serviços e marcar amanhã às 10h?",
    "Quero outros serviços, mas cancele meu horário", "Página anterior e confirme meu agendamento"])
async def test_mixed_booking_or_cancellation_is_not_absorbed_by_navigation(message):
    tenant, namespace, redis, state = uuid4(), "catalogue:mixed", CatalogueRedis(), {}
    rows = services(14)
    await catalogue("Quais serviços tem", state, rows, redis, tenant, namespace)
    before = dict(redis.values)
    result = await handle_request(message, state, {}, rows, redis, f"orion:qr:ai:{tenant}:{namespace}")
    assert result is None and redis.values == before


@pytest.mark.asyncio
async def test_long_service_name_uses_description_for_the_suffix_instead_of_repeating_the_title():
    tenant, namespace, redis, state = uuid4(), "catalogue:labels", CatalogueRedis(), {}
    rows = [{"id": uuid4(), "name": "Alinhamento Capilar (Botox)", "price_cents": 10000,
             "price_mode": "from", "duration_min": 90}]
    result = await catalogue("Quais serviços tem", state, rows, redis, tenant, namespace)
    response = await present(redis, tenant, "5567999999999", namespace, result,
                             Capabilities(assistant_interactions=1))
    row = response["presentation"]["action"]["sections"][0]["rows"][0]
    assert row["title"] == "Alinhamento Capilar"
    assert "Botox" in row["description"] and "a partir de R$ 100,00" in row["description"]
    assert "1h30" in row["description"]
    assert row["title"] not in row["description"]
    assert message_units(row["title"]) <= 24 and message_units(row["description"]) <= 72
