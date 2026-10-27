"""Published visual contracts and server-rendered public sharing metadata.

Booking prices, durations, assignments and opening hours remain operational.
Only the public visual whitelist is versioned; private database rows never enter
the publication or the HTML metadata.
"""
import base64
import hashlib
import html
import json
import os
import re
from urllib.parse import urlsplit
from app.site_address import address_display

SERVICE_VISUAL_FIELDS = ('name', 'description', 'category', 'color', 'image_url', 'sort_order', 'featured', 'catalog_kind')
PROFESSIONAL_VISUAL_FIELDS = ('name', 'specialty', 'color', 'image_url')


def visual_catalogue_fingerprint(content):
    """Stable visual-only value; dates, occupancy and live prices cannot dirty it."""
    def rows(items, fields):
        return sorted(({'id': str(item['id']), **{key: item.get(key) for key in fields}}
                       for item in items), key=lambda row: row['id'])
    services = rows(content.get('services', []), SERVICE_VISUAL_FIELDS)
    source = {str(item['id']): item for item in content.get('services', [])}
    for item in services:
        original = source[item['id']]
        for field, identity in (('components', 'service_id'), ('addons', 'addon_service_id')):
            item[field] = [{'id': str(part[identity]), 'name': part.get('name')}
                           for part in original.get(field, [])]
    value = {'services': services,
             'professionals': rows(content.get('professionals', []), PROFESSIONAL_VISUAL_FIELDS)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def project_catalogue_visuals(content, snapshot):
    """Apply the published visuals to still-bookable entities without stale authority."""
    saved = (snapshot or {}).get('_catalogue')
    if not isinstance(saved, dict):
        return content  # Existing publications keep their prior behavior until republished.
    professionals = {str(item['id']): item for item in saved.get('professionals', [])}
    services = {str(item['id']): item for item in saved.get('services', [])}
    people = [{**item, **{key: professionals[str(item['id'])].get(key)
                         for key in PROFESSIONAL_VISUAL_FIELDS}}
              for item in content['professionals'] if str(item['id']) in professionals]
    allowed_people = {str(item['id']) for item in people}
    items = []
    for live in content['services']:
        old = services.get(str(live['id']))
        if old is None:
            continue
        if live.get('catalog_kind') != old.get('catalog_kind'):
            continue
        if [str(part['service_id']) for part in live.get('components', [])] != [
                str(part['service_id']) for part in old.get('components', [])]:
            continue  # A changed combo needs its owner's new publication.
        item = {**live, **{key: old.get(key) for key in SERVICE_VISUAL_FIELDS}}
        item['professional_ids'] = [person for person in live.get('professional_ids', [])
                                    if str(person) in allowed_people]
        if not item['professional_ids']:
            continue
        if 'components' in live:
            names = {str(part['service_id']): part.get('name') for part in old.get('components', [])}
            item['components'] = [{**part, 'name': names.get(str(part['service_id']), part.get('name'))}
                                  for part in live['components']]
        # Add-ons retain current commercial/eligibility data, with their published labels.
        saved_addons = {str(part['addon_service_id']): part for part in old.get('addons', [])}
        item['addons'] = [{**part, 'name': saved_addons[str(part['addon_service_id'])].get('name'),
                           'professional_ids': [person for person in part.get('professional_ids', [])
                                                if str(person) in allowed_people]}
                          for part in live.get('addons', []) if str(part['addon_service_id']) in saved_addons]
        items.append(item)
    items.sort(key=lambda item: (not item.get('featured', False), item.get('sort_order') or 0,
                                 item.get('name') or '', str(item['id'])))
    people.sort(key=lambda item: (item.get('name') or '', str(item['id'])))
    return {**content, 'services': items, 'professionals': people}


def published_booking_labels(site, professional, items):
    """Public summaries use the same published labels as the service cards."""
    saved = (site.get('public_snapshot') or {}).get('_catalogue')
    if not isinstance(saved, dict):
        return None
    names = {str(item['id']): item.get('name') for item in saved.get('services', [])}
    for item in saved.get('services', []):
        names.update({str(addon['addon_service_id']): addon.get('name') for addon in item.get('addons', [])})
    people = {str(item['id']): item.get('name') for item in saved.get('professionals', [])}
    return {'professional': people.get(str(professional)) or 'Profissional',
            'services': [names.get(str(item['id'])) or 'Serviço' for item in items]}


def public_site_url(slug):
    # Never trust Host/X-Forwarded-Host for metadata or redirects. This is the
    # existing, configured shared deployment; custom-domain routing is future work.
    origin = os.getenv('PANEL_PUBLIC_URL', 'https://orion-assistente-production.up.railway.app').strip().rstrip('/')
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Invalid configured public site origin')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug):
        raise ValueError('Invalid canonical slug')
    return origin + '/s/' + slug


def public_html(template, preference, slug):
    """Return escaped metadata and its exact CSP hash; never include draft content."""
    canonical = public_site_url(slug)
    title = str(preference.get('title') or 'Agendamento')[:160]
    description = ' '.join(str(preference.get('description') or
        'Conheça os serviços, escolha seu profissional e agende seu próximo atendimento.').split())[:300]
    escaped = lambda value: html.escape(str(value), quote=True)
    metadata = [f'<title>{escaped(title)} • Agendamento</title>',
                f'<meta name="description" content="{escaped(description)}">',
                f'<link rel="canonical" href="{escaped(canonical)}">',
                '<meta property="og:type" content="website">',
                '<meta property="og:locale" content="pt_BR">',
                f'<meta property="og:title" content="{escaped(title)}">',
                f'<meta property="og:description" content="{escaped(description)}">',
                f'<meta property="og:url" content="{escaped(canonical)}">']
    structured = {'@context': 'https://schema.org', '@type': 'LocalBusiness',
                  'name': title, 'description': description, 'url': canonical}
    address = {target: preference.get(source) for target, source in
               (('addressLocality', 'city'),
                ('addressRegion', 'state'), ('postalCode', 'postal_code')) if preference.get(source)}
    if street := address_display(preference):
        address['streetAddress'] = street
    if address:
        structured['address'] = {'@type': 'PostalAddress', **address}
    phone = preference.get('contact_phone')
    if phone:
        structured['telephone'] = str(phone)
    for candidate in (preference.get('cover_url'), preference.get('logo_url')):
        if not isinstance(candidate, str):
            continue
        if re.fullmatch(r'/media/[0-9a-f-]{36}/[0-9a-f-]{36}\.webp', candidate):
            candidate = canonical.split('/s/', 1)[0] + candidate
        parsed = urlsplit(candidate)
        if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
            structured['image'] = candidate
            metadata.append(f'<meta property="og:image" content="{escaped(candidate)}">')
            break
    schema = json.dumps(structured, ensure_ascii=False, separators=(',', ':'))
    schema = schema.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    digest = base64.b64encode(hashlib.sha256(schema.encode()).digest()).decode()
    metadata.append('<script type="application/ld+json">' + schema + '</script>')
    result = re.sub(r'<title>.*?</title>', '', template, count=1, flags=re.S)
    result = re.sub(r'<meta name="description"[^>]*>', '', result, count=1)
    result = result.replace('</head>', '\n'.join(metadata) + '\n</head>', 1)
    return result, digest
