"""Published visual boundaries and hostile metadata are checked without a browser."""
import base64
import hashlib
import json
import re
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.public_site_api import check_published_selection
from app.public_site_rendering import (project_catalogue_visuals, public_html,
    public_site_url, visual_catalogue_fingerprint, published_booking_labels)


def visual_fixture():
    pro, service = uuid4(), uuid4()
    content = {'services': [{'id': service, 'name': 'Corte publicado', 'price_cents': 3000,
        'duration_min': 30, 'image_url': 'https://example.invalid/published.webp',
        'professional_ids': [pro], 'addons': []}],
        'professionals': [{'id': pro, 'name': 'Equipe publicada'}], 'opening_hours': ['live']}
    snapshot = json.loads(json.dumps(content, default=str))
    return pro, service, content, {'_catalogue': snapshot}


def test_published_visuals_do_not_publish_draft_images_names_or_new_entities():
    pro, service, live, snapshot = visual_fixture()
    live['services'][0].update(name='Rascunho', image_url='https://example.invalid/draft.webp',
                                price_cents=4500, duration_min=45)
    live['professionals'][0]['name'] = 'Nome ainda privado'
    live['services'].append({'id': uuid4(), 'name': 'Serviço ainda privado'})
    live['professionals'].append({'id': uuid4(), 'name': 'Pessoa ainda privada'})
    public = project_catalogue_visuals(live, snapshot)
    assert [item['name'] for item in public['services']] == ['Corte publicado']
    assert [item['name'] for item in public['professionals']] == ['Equipe publicada']
    assert public['services'][0]['image_url'].endswith('/published.webp')
    assert public['services'][0]['price_cents'] == 4500
    assert public['services'][0]['duration_min'] == 45
    assert public['opening_hours'] == ['live']
    assert live['services'][0]['name'] == 'Rascunho'  # projection never mutates the live rows
    assert project_catalogue_visuals(live, {}) is live  # backward compatibility


def test_removed_professional_is_never_resurrected_by_visual_snapshot():
    pro, service, live, snapshot = visual_fixture()
    live['professionals'] = []
    public = project_catalogue_visuals(live, snapshot)
    assert public['services'] == [] and public['professionals'] == []
    check_published_selection({'public_snapshot': snapshot}, pro, [service])
    for person, services, addons in ((uuid4(), [service], []), (pro, [uuid4()], []),
                                    (pro, [service], [uuid4()])):
        with pytest.raises(HTTPException) as exc:
            check_published_selection({'public_snapshot': snapshot}, person, services, addons)
        assert exc.value.status_code == 422


def test_combo_composition_cannot_publish_a_new_component_label_implicitly():
    pro, service, live, snapshot = visual_fixture()
    component = str(uuid4())
    live['services'][0].update(catalog_kind='combo', components=[{'service_id': component, 'name': 'Corte'}])
    saved = snapshot['_catalogue']['services'][0]
    saved.update(catalog_kind='combo', components=[{'service_id': component, 'name': 'Corte'}])
    live['services'][0]['components'].append({'service_id': str(uuid4()), 'name': 'Novo privado'})
    assert project_catalogue_visuals(live, snapshot)['services'] == []
    with pytest.raises(HTTPException) as exc:
        check_published_selection({'public_snapshot': snapshot}, pro, [service], components=live['services'][0]['components'])
    assert exc.value.status_code == 409
    assert published_booking_labels({'public_snapshot': snapshot}, pro, [{'id': service}]) == {
        'professional': 'Equipe publicada', 'services': ['Corte publicado']}


def test_visual_pending_changes_ignore_prices_hours_and_list_order():
    pro, service, live, snapshot = visual_fixture()
    before = visual_catalogue_fingerprint(live)
    live['services'][0].update(price_cents=9000, duration_min=90, professional_ids=[])
    live['opening_hours'] = ['another current day']
    assert before == visual_catalogue_fingerprint(live)
    live['services'][0]['image_url'] = 'https://example.invalid/new.webp'
    assert before != visual_catalogue_fingerprint(live)


def test_combo_component_order_change_is_pending_and_cannot_silently_hide_a_card():
    pro, service, live, snapshot = visual_fixture()
    parts = [{'service_id': str(uuid4()), 'name': 'Corte'}, {'service_id': str(uuid4()), 'name': 'Barba'}]
    live['services'][0]['components'] = parts
    before = visual_catalogue_fingerprint(live)
    live['services'][0]['components'] = list(reversed(parts))
    assert before != visual_catalogue_fingerprint(live)


def test_html_metadata_is_per_tenant_escaped_canonical_and_csp_bound(monkeypatch):
    monkeypatch.setenv('PANEL_PUBLIC_URL', 'https://orion-assistente-production.up.railway.app')
    name = 'Empresa "><script>alert(1)</script>'
    description = 'Descrição </script><script>alert(2)</script> & contato'
    template = '<html><head><title>Generic</title><meta name="description" content="Generic"></head><body></body></html>'
    rendered, digest = public_html(template, {'title': name, 'description': description,
        'address': 'Endereço público', 'cover_url': 'javascript:alert(3)'}, 'empresa-sintetica')
    assert 'Generic' not in rendered and '<script>alert' not in rendered
    assert '<link rel="canonical" href="https://orion-assistente-production.up.railway.app/s/empresa-sintetica">' in rendered
    assert 'property="og:title"' in rendered and 'property="og:url"' in rendered
    raw = re.search(r'<script type="application/ld\+json">(.*?)</script>', rendered).group(1)
    structured = json.loads(raw)
    assert structured['name'] == name and structured['description'] == description
    assert 'image' not in structured
    assert base64.b64encode(hashlib.sha256(raw.encode()).digest()).decode() == digest
    assert '<' not in raw and '&' not in raw
    assert public_site_url('empresa-sintetica') == structured['url']


@pytest.mark.parametrize('origin', ['http://foreign.invalid', 'https://user:pass@foreign.invalid',
    'https://foreign.invalid/path', 'https://foreign.invalid?token=secret', '//foreign.invalid'])
def test_metadata_never_accepts_untrusted_or_invalid_origin(monkeypatch, origin):
    monkeypatch.setenv('PANEL_PUBLIC_URL', origin)
    with pytest.raises(ValueError):
        public_site_url('empresa-sintetica')


def test_public_cover_image_is_preloaded_before_javascript(monkeypatch):
    monkeypatch.setenv('PANEL_PUBLIC_URL', 'https://orionagenda.pro')
    cover = f"/media/{uuid4()}/{uuid4()}.webp"
    template = '<html><head><title>Generic</title></head><body></body></html>'
    output, _ = public_html(template, {'title': 'Studio', 'cover_url': cover}, 'studio')
    expected = f'<link rel="preload" as="image" href="https://orionagenda.pro{cover}" fetchpriority="high" referrerpolicy="no-referrer">'
    assert expected in output
    assert output.index(expected) < output.index('</head>')
    invalid, _ = public_html(template, {'title': 'Studio', 'cover_url': 'javascript:alert(1)'}, 'studio')
    assert 'rel="preload" as="image"' not in invalid
