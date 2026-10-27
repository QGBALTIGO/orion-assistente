import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, deferred, defaultResponse, tenantA, tenantB } from './harness.mjs';

const endpoint = id => `/api/v1/tenants/${id}/management/site`;
const prefix = endpoint(tenantA);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const afterDebounce = async () => { await sleep(340); await flush(); };
const site = (extra = {}) => ({
  title: 'Empresa sintética', description: 'Atendimento por agendamento', slug: 'empresa-sintetica',
  theme_color: '#2e7bf6', show_prices: true, version: 4, published: false,
  preview_url: `/app/site-preview/${tenantA}`, public_url: null,
  address: null, address_number: null, address_no_number: false, address_complement: null,
  neighborhood: null, city: null, state: null, postal_code: null, ...extra,
});
const publication = (extra = {}) => ({
  state: 'ready', can_edit: true, can_publish: true, ready: true, issues: [],
  draft_version: 4, public_version: null, public_url: null, history: [], ...extra,
});
const viaCep = (extra = {}) => ({
  cep: '01001-000', logradouro: 'Praça da Sé', bairro: 'Sé', localidade: 'São Paulo',
  uf: 'SP', complemento: 'lado ímpar', ...extra,
});
const field = (h, name) => h.document.querySelector(`#osSiteForm [name="${name}"]`);
const status = (h, kind) => h.document.getElementById(`osSite${kind}Status`);
const writes = h => h.calls.filter(call => call.method !== 'GET');
const slugReads = h => h.calls.filter(call => call.path.includes('/slug-availability?'));
function dispatch(h, element, type) { element.dispatchEvent(new h.window.Event(type, { bubbles: true })); }
function change(h, name, value) {
  const input = field(h, name);
  input.value = value;
  dispatch(h, input, 'change');
  return input;
}

async function setup(t, { data = site(), role = 'owner', capabilities = {}, respond, lookup } = {}) {
  const server = { data, publication: publication(capabilities) };
  const h = await makeSuite({ initialRoute: 'site', role, respond: async (path, method = 'GET', payload) => {
    if (respond) {
      const result = await respond(path, method, payload, server);
      if (result !== undefined) return result;
    }
    if (path === prefix && method === 'GET') return server.data;
    if (path === prefix + '/publication' && method === 'GET') return server.publication;
    if (path === prefix + '/metrics?days=30') return { daily: [], visits: 0 };
    if (path.startsWith(prefix + '/slug-availability?')) return { slug: new URL(path, 'https://orion.test').searchParams.get('slug'), available: true, suggestions: [] };
    if (path === prefix && method === 'PUT') {
      server.data = { ...server.data, ...payload, version: server.data.version + 1 };
      server.publication = { ...server.publication, draft_version: server.data.version };
      return server.data;
    }
    return defaultResponse(path);
  } });
  const fetches = [];
  h.window.fetch = async (url, options) => {
    const call = { url: String(url), options };
    fetches.push(call);
    const body = lookup ? await lookup(call, fetches.length) : viaCep();
    return { ok: true, status: 200, json: async () => body };
  };
  t.after(() => h.close());
  await flush();
  h.click('[data-os-site-tab=settings]');
  await flush();
  return { h, server, fetches };
}

test('CEP appears first and opening or renaming a saved legacy site preserves its address and link', async t => {
  const legacy = 'Avenida São João, 42 — Loja 5';
  const { h, fetches } = await setup(t, { data: site({
    address: legacy, city: 'São Paulo', state: 'SP', postal_code: '01001-000',
    address_number: undefined, address_complement: undefined, neighborhood: undefined,
  }) });
  const location = field(h, 'postal_code').closest('.os-site-location');
  assert.equal(location.querySelector('input').name, 'postal_code');
  assert.equal(status(h, 'Cep').getAttribute('role'), 'status');
  assert.equal(field(h, 'address').value, legacy);
  assert.equal(field(h, 'address_number').value, '', 'old addresses are not split or guessed');
  h.fill('#osSiteForm [name=title]', 'Novo nome público');
  await afterDebounce();
  assert.equal(fetches.length, 0, 'saved CEP is not sent to an external provider on opening');
  assert.equal(slugReads(h).length, 0);
  assert.equal(field(h, 'slug').value, 'empresa-sintetica', 'renaming never changes a chosen link');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 1);
  const saved = writes(h)[0];
  assert.equal(saved.method, 'PUT'); assert.equal(saved.path, prefix);
  assert.equal(saved.data.address, legacy);
  assert.equal(saved.data.address_number, null);
  assert.equal(saved.data.address_no_number, false);
  assert.equal(saved.data.expected_version, 4);
  assert.equal(saved.data.slug, 'empresa-sintetica');
  assert.equal(h.errors.length, 0);
});

test('a complete CEP fills address fields after debounce without sending identity or guessing a number', async t => {
  const { h, fetches } = await setup(t);
  h.fill('#osSiteForm [name=address_number]', '123A');
  h.fill('#osSiteForm [name=address_complement]', 'Sala 4');
  h.fill('#osSiteForm [name=postal_code]', '01001000');
  assert.equal(field(h, 'postal_code').value, '01001-000');
  assert.equal(fetches.length, 0, 'typing does not issue an immediate request');
  await afterDebounce();
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, 'https://viacep.com.br/ws/01001000/json/');
  assert.equal(fetches[0].options.credentials, 'omit');
  assert.equal(fetches[0].options.referrerPolicy, 'no-referrer');
  assert.equal(fetches[0].options.signal.aborted, false);
  assert.equal(fetches[0].options.headers, undefined);
  assert.equal(fetches[0].options.body, undefined);
  assert.deepEqual(['address', 'neighborhood', 'city', 'state'].map(name => field(h, name).value), ['Praça da Sé', 'Sé', 'São Paulo', 'SP']);
  assert.equal(field(h, 'address_number').value, '123A');
  assert.equal(field(h, 'address_complement').value, 'Sala 4', 'ViaCEP complemento is not the establishment complement');
  assert.match(status(h, 'Cep').textContent, /Endereço preenchido/);
  assert.equal(h.document.getElementById('osSiteForm').dataset.cepPending, 'false');
  assert.equal(writes(h).length, 0);
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 1);
  const payload = writes(h)[0].data;
  assert.deepEqual(Object.fromEntries(['address', 'address_number', 'address_complement', 'neighborhood', 'city', 'state', 'postal_code', 'address_no_number', 'expected_version'].map(name => [name, payload[name]])), {
    address: 'Praça da Sé', address_number: '123A', address_complement: 'Sala 4', neighborhood: 'Sé',
    city: 'São Paulo', state: 'SP', postal_code: '01001-000', address_no_number: false, expected_version: 4,
  });
  assert.equal(field(h, 'address_number').value, '123A', 'saved address comes back through the regular site read');
});

test('incomplete CEP does not query and a generic city CEP leaves room for manual street and neighborhood', async t => {
  const { h, fetches } = await setup(t, { lookup: () => viaCep({ cep: '18150-000', logradouro: '', bairro: '', localidade: 'Ibiúna' }) });
  h.fill('#osSiteForm [name=postal_code]', '18150');
  await afterDebounce();
  assert.equal(fetches.length, 0);
  assert.match(status(h, 'Cep').textContent, /8 dígitos/);
  h.fill('#osSiteForm [name=postal_code]', '18150000');
  await afterDebounce();
  assert.equal(fetches.length, 1);
  assert.equal(field(h, 'city').value, 'Ibiúna');
  assert.equal(field(h, 'state').value, 'SP');
  assert.equal(field(h, 'address').value, '');
  assert.equal(field(h, 'neighborhood').value, '');
  assert.match(status(h, 'Cep').textContent, /Complete a rua, o bairro e o número/);
  for (const name of ['address', 'neighborhood', 'city', 'state']) assert.equal(field(h, name).matches(':disabled'), false);
  h.fill('#osSiteForm [name=address]', 'Estrada Municipal');
  h.fill('#osSiteForm [name=neighborhood]', 'Centro');
  h.fill('#osSiteForm [name=address_number]', '42');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h)[0].data.address, 'Estrada Municipal');
  assert.equal(writes(h)[0].data.neighborhood, 'Centro');
});

test('CEP lookup keeps fields edited after typing started and blocks saving while the result is pending', async t => {
  const pending = deferred();
  const { h } = await setup(t, { lookup: () => pending.promise });
  h.fill('#osSiteForm [name=postal_code]', '01001000');
  h.fill('#osSiteForm [name=address]', 'Rua informada manualmente');
  await afterDebounce();
  h.fill('#osSiteForm [name=city]', 'Cidade escolhida');
  h.fill('#osSiteForm [name=address_number]', '88');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 0);
  assert.match(h.document.querySelector('#osSiteForm .os-form-message').textContent, /Aguarde.*CEP/);
  pending.resolve(viaCep()); await flush();
  assert.equal(field(h, 'address').value, 'Rua informada manualmente', 'edits during the debounce are preserved too');
  assert.equal(field(h, 'city').value, 'Cidade escolhida');
  assert.equal(field(h, 'address_number').value, '88');
  assert.equal(field(h, 'neighborhood').value, 'Sé');
  assert.equal(field(h, 'state').value, 'SP');
  assert.match(status(h, 'Cep').textContent, /Mantivemos os campos que você editou/);
  assert.equal(h.document.querySelector('#osSiteForm .os-form-message').textContent, '', 'the wait message is removed when lookup finishes');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.city, 'Cidade escolhida');
});

test('a changed CEP aborts the earlier request and ignores a late response even if the provider ignores abort', async t => {
  const first = deferred(), second = deferred();
  const { h, fetches } = await setup(t, { lookup: (call, count) => count === 1 ? first.promise : second.promise });
  h.fill('#osSiteForm [name=postal_code]', '01001000'); await afterDebounce();
  h.fill('#osSiteForm [name=postal_code]', '20040002');
  assert.equal(fetches[0].options.signal.aborted, true);
  await afterDebounce();
  second.resolve(viaCep({ cep: '20040-002', logradouro: 'Rua da Assembleia', bairro: 'Centro', localidade: 'Rio de Janeiro', uf: 'RJ' }));
  await flush();
  first.resolve(viaCep()); await flush();
  assert.equal(field(h, 'postal_code').value, '20040-002');
  assert.equal(field(h, 'address').value, 'Rua da Assembleia');
  assert.equal(field(h, 'city').value, 'Rio de Janeiro');
  assert.equal(field(h, 'state').value, 'RJ');
  assert.equal(h.document.getElementById('osSiteForm').dataset.cepPending, 'false');
  assert.equal(writes(h).length, 0);
});

test('a timed-out CEP request is aborted and releases the form for manual completion and saving', async t => {
  const { h, fetches } = await setup(t, { lookup: ({ options }) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => {
      const error = Error('Consulta interrompida'); error.name = 'AbortError'; reject(error);
    }, { once: true });
  }) });
  const schedule = h.window.setTimeout.bind(h.window);
  h.window.setTimeout = (callback, delay, ...args) => schedule(callback, delay === 8000 ? 20 : delay, ...args);
  h.fill('#osSiteForm [name=postal_code]', '01001000');
  h.submit('#osSiteForm');
  assert.match(h.document.querySelector('#osSiteForm .os-form-message').textContent, /Aguarde.*CEP/);
  await afterDebounce(); await sleep(30); await flush();
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].options.signal.aborted, true);
  assert.equal(h.document.getElementById('osSiteForm').dataset.cepPending, 'false');
  assert.equal(h.document.querySelector('#osSiteForm .os-form-message').textContent, '');
  assert.match(status(h, 'Cep').textContent, /manualmente/);
  assert.equal(h.document.querySelector('[data-os-action=site-cep-retry]').hidden, false);
  h.fill('#osSiteForm [name=address]', 'Rua preenchida manualmente');
  h.fill('#osSiteForm [name=address_number]', '17');
  h.fill('#osSiteForm [name=neighborhood]', 'Centro');
  h.fill('#osSiteForm [name=city]', 'São Paulo');
  h.fill('#osSiteForm [name=state]', 'sp');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.address, 'Rua preenchida manualmente');
  assert.equal(writes(h)[0].data.state, 'SP');
});

test('changing business aborts pending CEP work and cannot populate the other business form', async t => {
  const pending = deferred();
  const other = site({ title: 'Outra empresa', slug: 'outra-empresa', address: 'Rua da outra empresa', city: 'Recife', state: 'PE', postal_code: '50030-230' });
  const { h, fetches } = await setup(t, { lookup: () => pending.promise, respond: path => {
    if (path === endpoint(tenantB)) return other;
    if (path === endpoint(tenantB) + '/publication') return publication();
    if (path === endpoint(tenantB) + '/metrics?days=30') return { daily: [], visits: 0 };
  } });
  h.fill('#osSiteForm [name=postal_code]', '01001000'); await afterDebounce();
  h.window.OrionSuite.sync({ ...h.bridge, tenantId: tenantB, tenants: [{ id: tenantB, name: 'Outra empresa', slug: 'outra-empresa', timezone: 'America/Recife', role: 'owner' }] });
  await flush();
  h.click('[data-os-site-tab=settings]'); await flush();
  assert.equal(fetches[0].options.signal.aborted, true);
  pending.resolve(viaCep()); await flush();
  assert.equal(field(h, 'address').value, 'Rua da outra empresa');
  assert.equal(field(h, 'city').value, 'Recife');
  assert.equal(field(h, 'slug').value, 'outra-empresa');
  assert.equal(fetches.length, 1);
  assert.equal(writes(h).length, 0);
});

test('missing and failed CEP results preserve manual data and permit an explicit retry or manual save', async t => {
  for (const failure of ['missing', 'network', 'mismatched-response']) {
    await t.test(failure, async t => {
      const { h, fetches } = await setup(t, { data: site({ address: 'Rua já informada', neighborhood: 'Bairro já informado', city: 'Cidade já informada', state: 'SP' }), lookup: (call, count) => {
        if (count > 1) return viaCep();
        if (failure === 'network') throw Error('Rede indisponível');
        return failure === 'missing' ? { erro: true } : viaCep({ cep: '99999-999' });
      } });
      h.fill('#osSiteForm [name=postal_code]', '01001000'); await afterDebounce();
      assert.equal(field(h, 'address').value, 'Rua já informada');
      assert.equal(field(h, 'neighborhood').value, 'Bairro já informado');
      assert.match(status(h, 'Cep').textContent, /manualmente/);
      const retry = h.document.querySelector('[data-os-action=site-cep-retry]');
      assert.equal(retry.hidden, false);
      assert.equal(h.document.getElementById('osSiteForm').dataset.cepPending, 'false');
      assert.equal(field(h, 'postal_code').checkValidity(), true);
      if (failure === 'network') {
        h.fill('#osSiteForm [name=address_number]', '7');
        h.submit('#osSiteForm'); await flush();
        assert.equal(writes(h).length, 1, 'lookup outage does not prevent saving a manually completed address');
        assert.equal(writes(h)[0].data.address, 'Rua já informada');
      } else {
        retry.click(); await sleep(15); await flush();
        assert.equal(fetches.length, 2);
        assert.equal(field(h, 'address').value, 'Praça da Sé');
        assert.equal(retry.hidden, true);
        assert.equal(writes(h).length, 0);
      }
    });
  }
});

test('Sem número is reversible and saves an explicit no-number flag with complement and neighborhood', async t => {
  const { h } = await setup(t, { data: site({ address: 'Rua do estabelecimento', address_number: '12B', address_complement: 'Loja 2', neighborhood: 'Centro' }) });
  h.click('#osSiteForm [name=address_no_number]');
  assert.equal(field(h, 'address_number').disabled, true);
  assert.equal(field(h, 'address_number').value, '');
  h.click('#osSiteForm [name=address_no_number]');
  assert.equal(field(h, 'address_number').disabled, false);
  assert.equal(field(h, 'address_number').value, '12B', 'toggling off does not discard the number typed earlier');
  h.click('#osSiteForm [name=address_no_number]');
  h.submit('#osSiteForm'); await flush();
  const saved = writes(h)[0].data;
  assert.equal(saved.address_no_number, true);
  assert.equal(saved.address_number, null);
  assert.equal(saved.address_complement, 'Loja 2');
  assert.equal(saved.neighborhood, 'Centro');
  assert.equal(field(h, 'address_no_number').checked, true);
  assert.equal(field(h, 'address_number').disabled, true);
});

test('read-only site access cannot initiate lookups or save through synthetic field events', async t => {
  const { h, fetches } = await setup(t, { role: 'viewer', capabilities: { can_edit: false, can_publish: false } });
  for (const name of ['postal_code', 'address', 'address_number', 'address_no_number', 'address_complement', 'neighborhood', 'slug']) assert.equal(field(h, name).matches(':disabled'), true);
  h.fill('#osSiteForm [name=postal_code]', '01001000');
  h.fill('#osSiteForm [name=slug]', 'Nome alterado');
  change(h, 'postal_code', '01001-000');
  h.click('[data-os-action=site-cep-retry]');
  h.submit('#osSiteForm'); await afterDebounce();
  assert.equal(fetches.length, 0);
  assert.equal(slugReads(h).length, 0);
  assert.equal(writes(h).length, 0);
});

test('slug normalizes accents, case and separators while preserving a word boundary during typing', async t => {
  const { h } = await setup(t);
  const input = h.fill('#osSiteForm [name=slug]', '  Clínica & Café — São João  ');
  assert.equal(input.value, 'clinica-cafe-sao-joao-');
  assert.equal(h.document.getElementById('osSiteSlugPreview').textContent, '/s/clinica-cafe-sao-joao');
  dispatch(h, input, 'focusout');
  assert.equal(input.value, 'clinica-cafe-sao-joao');
  await afterDebounce();
  assert.equal(slugReads(h).length, 1);
  assert.equal(new URL(slugReads(h)[0].path, 'https://orion.test').searchParams.get('slug'), 'clinica-cafe-sao-joao');
  assert.equal(input.checkValidity(), true);
  h.fill('#osSiteForm [name=slug]', 'Minha ');
  assert.equal(input.value, 'minha-');
  h.fill('#osSiteForm [name=slug]', input.value + 'Empresa');
  assert.equal(input.value, 'minha-empresa', 'a typed space remains a separator for the next word');
  h.fill('#osSiteForm [name=slug]', 'Á'.repeat(79) + ' B');
  assert.ok(input.value.length <= 80);
  dispatch(h, input, 'change');
  assert.equal(input.value, 'a'.repeat(79));
  assert.match(input.value, /^[a-z0-9]+(?:-[a-z0-9]+)*$/);
  h.fill('#osSiteForm [name=slug]', 'Meu Negócio ');
  assert.equal(input.checkValidity(), true, 'the temporary final hyphen must not prevent native submit before normalization');
  h.submit('#osSiteForm'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.slug, 'meu-negocio', 'submit finalizes a trailing separator even without blur');
});

test('change-only browser autofill still normalizes and checks both the CEP and the link', async t => {
  const { h, fetches } = await setup(t);
  change(h, 'slug', 'Clínica Preenchida ');
  change(h, 'postal_code', '01001000');
  assert.equal(field(h, 'slug').value, 'clinica-preenchida');
  assert.equal(field(h, 'postal_code').value, '01001-000');
  await afterDebounce();
  assert.equal(fetches.length, 1);
  assert.equal(field(h, 'address').value, 'Praça da Sé');
  assert.equal(slugReads(h).length, 1);
  assert.equal(new URL(slugReads(h)[0].path, 'https://orion.test').searchParams.get('slug'), 'clinica-preenchida');
  assert.match(status(h, 'Slug').textContent, /Endereço disponível/);
  assert.equal(writes(h).length, 0);
});

test('reserved slugs show vetted suggestions and selecting one rechecks availability without saving', async t => {
  const selected = deferred();
  const { h } = await setup(t, { respond: path => {
    if (path === prefix + '/slug-availability?slug=clinica-cafe') return {
      slug: 'clinica-cafe', available: false, reason: 'Este link já está reservado',
      suggestions: ['clinica-cafe-2', 'clinica-cafe-2', '<img src=x onerror=alert(1)>', 'INVALIDO', 'clinica-cafe', 'clinica-cafe-3', 'clinica-cafe-4', 'clinica-cafe-5'],
    };
    if (path === prefix + '/slug-availability?slug=clinica-cafe-2') return selected.promise;
  } });
  h.fill('#osSiteForm [name=slug]', 'Clínica Café'); await afterDebounce();
  const host = h.document.getElementById('osSiteSlugSuggestions');
  assert.equal(host.hidden, false);
  assert.deepEqual(Array.from(host.querySelectorAll('button'), button => button.dataset.osSiteSlugSuggestion), ['clinica-cafe-2', 'clinica-cafe-3', 'clinica-cafe-4']);
  assert.equal(host.querySelector('img'), null);
  assert.equal(field(h, 'slug').value, 'clinica-cafe', 'a suggestion is never chosen automatically');
  assert.equal(field(h, 'slug').checkValidity(), false);
  h.click('[data-os-site-slug-suggestion="clinica-cafe-2"]'); await sleep(15); await flush();
  assert.equal(field(h, 'slug').value, 'clinica-cafe-2');
  assert.equal(h.document.getElementById('osSiteSlugPreview').textContent, '/s/clinica-cafe-2');
  assert.equal(h.document.activeElement, field(h, 'slug'));
  assert.equal(host.hidden, true);
  assert.equal(slugReads(h).length, 2, 'chosen suggestions are checked again in case availability changed');
  assert.match(status(h, 'Slug').textContent, /Verificando/);
  selected.resolve({ slug: 'clinica-cafe-2', available: true, suggestions: [] }); await flush();
  assert.equal(field(h, 'slug').checkValidity(), true);
  assert.match(status(h, 'Slug').textContent, /Endereço disponível/);
  assert.equal(writes(h).length, 0);
});

test('stale availability replies cannot restore suggestions for a link the user already changed', async t => {
  const pending = deferred();
  const { h } = await setup(t, { respond: path => path === prefix + '/slug-availability?slug=primeiro-link' ? pending.promise : undefined });
  h.fill('#osSiteForm [name=slug]', 'Primeiro Link'); await afterDebounce();
  h.fill('#osSiteForm [name=slug]', 'Segundo Link'); await afterDebounce();
  pending.resolve({ slug: 'primeiro-link', available: false, reason: 'Reservado', suggestions: ['primeiro-link-2'] }); await flush();
  assert.equal(field(h, 'slug').value, 'segundo-link');
  assert.match(status(h, 'Slug').textContent, /Endereço disponível/);
  assert.equal(h.document.getElementById('osSiteSlugSuggestions').hidden, true);
  assert.equal(h.document.querySelector('[data-os-site-slug-suggestion]'), null);
  assert.equal(writes(h).length, 0);
});

test('a reservation race keeps the unsaved form, checks fresh suggestions and never chooses a replacement', async t => {
  let available = true;
  const { h } = await setup(t, { respond: (path, method) => {
    if (path === prefix && method === 'PUT') {
      available = false;
      const error = Error('Este endereço acabou de ser reservado. Escolha outro link.');
      error.status = 409;
      throw error;
    }
    if (path === prefix + '/slug-availability?slug=link-concorrido') return { slug: 'link-concorrido', available, suggestions: available ? [] : ['link-concorrido-2'] };
  } });
  h.fill('#osSiteForm [name=slug]', 'Link Concorrido'); await afterDebounce();
  h.fill('#osSiteForm [name=address]', 'Rua ainda não salva');
  h.fill('#osSiteForm [name=address_number]', '15');
  const form = h.document.getElementById('osSiteForm');
  h.submit('#osSiteForm'); await flush(); await sleep(15); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.expected_version, 4);
  assert.equal(writes(h)[0].data.slug, 'link-concorrido');
  assert.equal(h.document.getElementById('osSiteForm'), form);
  assert.equal(form.dataset.dirty, 'true');
  assert.equal(field(h, 'slug').value, 'link-concorrido');
  assert.equal(field(h, 'address').value, 'Rua ainda não salva');
  assert.equal(field(h, 'address_number').value, '15');
  assert.match(form.querySelector('.os-form-message').textContent, /reservado/);
  assert.ok(h.document.querySelector('[data-os-site-slug-suggestion="link-concorrido-2"]'));
  assert.equal(field(h, 'slug').checkValidity(), false);
  assert.equal(h.errors.length, 0);
});
