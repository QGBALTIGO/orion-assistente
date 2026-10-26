"""Real PostgreSQL: published marketing, current agenda and canonical HTML agree."""
import re
from uuid import uuid4

import pytest
from sqlalchemy import text

from test_public_catalog import public_db, save_site, publish, availability, book_body

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_visual_publication_and_operational_catalogue_have_distinct_lifetimes(public_db):
    d = public_db
    await publish(d)
    original = (await d['client'].get(d['public'])).json()
    old_name = next(item['name'] for item in original['services'] if item['id'] == str(d['cut']))
    new_service = uuid4()
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE services SET name='Imagem ainda privada',image_url='https://example.invalid/draft.webp',price_cents=4700,duration_min=40 WHERE tenant_id=:t AND id=:id"),
                         {'t': d['a'], 'id': d['cut']})
        await db.execute(text("UPDATE professionals SET name='Nome profissional ainda privado' WHERE tenant_id=:t AND id=:id"),
                         {'t': d['a'], 'id': d['pro']})
        await db.execute(text("INSERT INTO services(id,tenant_id,name,duration_min,price_cents) VALUES(:id,:t,'Novo ainda privado',20,1500)"),
                         {'t': d['a'], 'id': new_service})
        await db.execute(text("INSERT INTO professional_services(tenant_id,professional_id,service_id) VALUES(:t,:p,:s)"),
                         {'t': d['a'], 'p': d['pro'], 's': new_service})
    current = (await d['client'].get(d['public'])).json()
    service = next(item for item in current['services'] if item['id'] == str(d['cut']))
    assert service['name'] == old_name and service['image_url'] is None
    assert service['price_cents'] == 4700 and service['duration_min'] == 40
    assert str(new_service) not in str(current)
    assert 'Nome profissional ainda privado' not in str(current)
    preview = (await d['client'].get(d['base']+'/site/preview')).json()
    assert 'Imagem ainda privada' in str(preview) and str(new_service) in str(preview)
    assert (await d['client'].get(d['base']+'/site')).json()['has_unpublished_changes']
    denied = await d['client'].get(d['public']+'/availability', params={
        'professional_id': str(d['pro']), 'service_id': str(new_service), 'day': d['day'].isoformat()})
    assert denied.status_code == 422
    await publish(d)
    updated = (await d['client'].get(d['public'])).json()
    assert 'Imagem ainda privada' in str(updated) and str(new_service) in str(updated)
    assert not (await d['client'].get(d['base']+'/site')).json()['has_unpublished_changes']


@pytest.mark.asyncio
async def test_server_metadata_uses_published_snapshot_and_alias_never_leaks_offline_site(public_db, monkeypatch):
    d = public_db
    monkeypatch.setenv('PANEL_PUBLIC_URL', 'https://orion-assistente-production.up.railway.app')
    await save_site(d, title='Empresa <script>alert(1)</script>', description='Descrição pública única')
    await publish(d)
    page = await d['client'].get('/s/'+d['slug'], headers={'Host': 'host-nao-confiavel.invalid'})
    assert page.status_code == 200
    assert '<script>alert(1)</script>' not in page.text
    assert '<title>Empresa &lt;script&gt;' in page.text
    assert 'host-nao-confiavel.invalid' not in page.text
    assert 'property="og:title"' in page.text and 'application/ld+json' in page.text
    assert re.search(r"script-src 'self' 'sha256-[A-Za-z0-9+/=]+'", page.headers['content-security-policy'])
    await save_site(d, title='Título ainda privado', slug=d['slug']+'-novo')
    assert 'Título ainda privado' not in (await d['client'].get('/s/'+d['slug'])).text
    renewed = await publish(d)
    alias = await d['client'].get('/s/'+d['slug'])
    assert alias.status_code == 308 and alias.headers['location'] == renewed['public_url']
    assert (await d['client'].get(d['public'])).status_code == 200
    offline = await d['client'].post(d['base']+'/site/publication', json={
        'expected_version': renewed['version'], 'enabled': False})
    assert offline.status_code == 200
    for slug in (d['slug'], d['slug']+'-novo'):
        unavailable = await d['client'].get('/s/'+slug)
        assert unavailable.status_code == 404
        assert 'Empresa' not in unavailable.text and 'Título ainda privado' not in unavailable.text


@pytest.mark.asyncio
async def test_public_booking_summary_confirmation_and_replay_keep_published_names(public_db):
    d = public_db
    await publish(d)
    async with d['migrator'].begin() as db:
        await db.execute(text("UPDATE services SET name='Nome novo ainda privado' WHERE tenant_id=:t AND id=:id"),
                         {'t': d['a'], 'id': d['cut']})
        await db.execute(text("UPDATE professionals SET name='Profissional ainda privado' WHERE tenant_id=:t AND id=:id"),
                         {'t': d['a'], 'id': d['pro']})
    quoted = await availability(d)
    assert quoted['services'] == ['Corte']
    body = book_body(quoted)
    response = await d['client'].post(d['public']+'/appointments', json=body)
    assert response.status_code == 201, response.text
    booked = response.json()
    assert booked['services'] == ['Corte'] and booked['professional'] == 'Profissional sintético'
    await publish(d)
    replay = await d['client'].post(d['public']+'/appointments', json=body)
    assert replay.status_code == 201, replay.text
    assert replay.json() == booked
