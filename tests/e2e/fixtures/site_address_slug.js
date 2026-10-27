/* Synthetic browser data only. This file is never loaded by the application. */
(() => {
  const originalFetch = window.fetch.bind(window);
  const copy = value => JSON.parse(JSON.stringify(value));

  window.mountSiteAddressFixture = () => {
    const tenant = '10000000-0000-4000-8000-000000000001';
    const prefix = '/api/v1/tenants/' + tenant + '/management/site';
    const fixture = window.siteAddressFixture = {
      calls: [], cepCalls: [],
      site: {
        title: 'Barbearia São José', description: 'Atendimento com horário marcado.',
        slug: 'empresa-sintetica', theme_color: '#2e7bf6', version: 4,
        published: false, public_url: null, public_enabled: false,
        preview_url: '/app/site-preview/' + tenant, has_unpublished_changes: false,
        logo_url: null, cover_url: null, show_prices: true, hide_orion_brand: false,
        address: 'Rua do Cadastro, 99', address_number: null,
        address_complement: null, address_no_number: false, neighborhood: null,
        city: 'São Paulo', state: 'SP', postal_code: '01311-000',
        contact_phone: null, whatsapp_phone: null, instagram_url: null,
        facebook_url: null, tiktok_url: null, maps_url: null, external_url: null,
        amenity_wifi: false, amenity_parking: false,
        amenity_accessibility: false, amenity_children: false,
      },
    };
    const postalResponses = {
      '01001000': {cep: '01001-000', logradouro: 'Praça da Sé', bairro: 'Sé',
        localidade: 'São Paulo', uf: 'SP', complemento: 'lado ímpar'},
      '88010000': {cep: '88010-000', logradouro: 'Rua Felipe Schmidt', bairro: 'Centro',
        localidade: 'Florianópolis', uf: 'SC', complemento: ''},
      '99999999': {erro: true},
    };
    window.fetch = (input, options = {}) => {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      if (url.origin !== 'https://viacep.com.br') return originalFetch(input, options);
      const match = url.pathname.match(/^\/ws\/([0-9]{8})\/json\/$/);
      if (!match) return Promise.reject(new Error('Unexpected synthetic ViaCEP path'));
      const cep = match[1];
      fixture.cepCalls.push({cep, url: url.href, credentials: options.credentials,
        referrerPolicy: options.referrerPolicy});
      const firstUnavailable = cep === '88010000' &&
        fixture.cepCalls.filter(call => call.cep === cep).length === 1;
      const body = postalResponses[cep] || {erro: true};
      return new Promise((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException('Synthetic request aborted', 'AbortError'));
        };
        const timer = setTimeout(() => {
          options.signal?.removeEventListener('abort', abort);
          resolve(new Response(JSON.stringify(body), {
            status: firstUnavailable ? 503 : 200,
            headers: {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
          }));
        }, 80);
        if (options.signal?.aborted) abort();
        else options.signal?.addEventListener('abort', abort, {once: true});
      });
    };

    const request = async (path, method = 'GET', data) => {
      fixture.calls.push({path, method, ...(data === undefined ? {} : {data: copy(data)})});
      const url = new URL(path, 'https://orion-fixture.invalid');
      if (url.pathname === prefix + '/publication' && method === 'GET') {
        return {state: 'ready', can_edit: true, can_publish: true, ready: true,
          issues: [], draft_version: fixture.site.version, public_version: null,
          published_at: null, public_url: null, history: []};
      }
      if (url.pathname === prefix + '/slug-availability' && method === 'GET') {
        const slug = url.searchParams.get('slug');
        const taken = slug === 'barbearia-sao-jose';
        return {slug, available: !taken,
          ...(taken ? {reason: 'Este endereço já está em uso. Escolha uma das opções disponíveis'} : {}),
          suggestions: taken ? ['barbearia-sao-jose-2', 'barbearia-sao-jose-3', 'barbearia-sao-jose-4'] : []};
      }
      if (url.pathname === prefix && method === 'GET') return copy(fixture.site);
      if (url.pathname === prefix && method === 'PUT') {
        if (data.expected_version !== fixture.site.version) {
          const error = new Error('As preferências foram alteradas. Atualize antes de salvar');
          error.status = 409;
          throw error;
        }
        const {expected_version, ...values} = data;
        fixture.site = {...fixture.site, ...copy(values), version: expected_version + 1};
        return copy(fixture.site);
      }
      if (url.pathname === prefix + '/metrics') return {daily: [], visits: 0, site_bookings: 0};
      if (method !== 'GET') throw new Error('Unexpected synthetic write: ' + method + ' ' + path);
      return {items: [], total: 0};
    };

    window.OrionSuite.mount({
      user: {role: 'member', name: 'Conta de demonstração', email: 'synthetic@example.invalid'},
      tenants: [{id: tenant, name: 'Barbearia São José', slug: 'empresa-sintetica',
        timezone: 'America/Sao_Paulo', role: 'owner'}],
      tenantId: tenant, initialRoute: 'site', request, openLegacy() {}, onRouteChange() {},
    });
  };
})();
