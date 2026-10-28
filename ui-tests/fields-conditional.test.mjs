import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, defaultResponse } from './harness.mjs';

const professionalId = 'd1000000-0000-4000-8000-000000000001';
const cutId = 'd2000000-0000-4000-8000-000000000002';
const beardId = 'd3000000-0000-4000-8000-000000000003';
const loyaltyId = 'd4000000-0000-4000-8000-000000000004';
const goalId = 'd5000000-0000-4000-8000-000000000005';

async function suite(t, initialRoute, { servicePrices = [3500, 2000] } = {}) {
  const professionals = [{ id: professionalId, name: 'Profissional sintético', active: true,
    specialty: 'Barbeiro', commission_mode: 'percent', commission_percent: 10,
    commission_config_version: 1, accepts_appointments: true, color: '#31ad52' }];
  const services = [
    { id: cutId, name: 'Corte', active: true, catalog_kind: 'base', price_mode: 'fixed', price_cents: servicePrices[0], duration_min: 40 },
    { id: beardId, name: 'Barba', active: true, catalog_kind: 'base', price_mode: 'fixed', price_cents: servicePrices[1], duration_min: 20 },
  ];
  const links = services.map((service, index) => ({ id: 'link-' + index, professional_id: professionalId, service_id: service.id }));
  let commissionRules = [], goals = [], loyalty = { id: loyaltyId, version: 1, name: 'Programa sintético',
    active: true, earning_mode: 'both', points_per_visit: 1, points_per_step: 1,
    spend_step_cents: 100, reward_points: 100, reward_type: 'percent', reward_percent: 10,
    reward_value_cents: null, reward_service_id: null, reward_valid_days: 30 };
  const h = await makeSuite({ initialRoute, respond(path, method = 'GET', payload) {
    if (path.endsWith('/professionals/' + professionalId + '/configure') && method === 'PUT') {
      Object.assign(professionals[0], payload, { commission_config_version: professionals[0].commission_config_version + 1 });
      commissionRules = payload.commission_rules.map(rule => ({ ...rule, professional_id: professionalId }));
      return { ...professionals[0] };
    }
    if (path.endsWith('/loyalty/rules/' + loyaltyId) && method === 'PATCH') {
      loyalty = { ...loyalty, ...payload, version: loyalty.version + 1 }; return loyalty;
    }
    if (path.endsWith('/goals') && method === 'POST') {
      const goal = { ...payload, id: goalId, version: 1, template_version: 1, can_edit: true,
        actual_value: 0, progress_percent: 0, is_current_cycle: true };
      goals = [goal]; return goal;
    }
    if (path.endsWith('/professionals')) return professionals;
    if (path.endsWith('/services')) return services;
    if (path.endsWith('/professional_services')) return links;
    if (path.endsWith('/professionals/commission-rules')) return { items: commissionRules };
    if (path.includes('/performance/daily?')) return { items: [] };
    if (path.includes('/performance?')) return { items: [] };
    if (path.includes('/goals?')) return { items: goals, templates: [] };
    if (path.endsWith('/loyalty')) return { rules: [loyalty], summary: {} };
    if (path.includes('/loyalty/ranking?')) return { items: [], total: 0 };
    return defaultResponse(path);
  } });
  t.after(() => h.close()); await flush();
  return h;
}

function change(h, selector, value) {
  const input = h.document.querySelector(selector); assert.ok(input, selector);
  input.value = value; input.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  return input;
}
const writes = h => h.calls.filter(call => call.method !== 'GET');
const commissionValue = id => '[name="commission_value_' + id + '"]';
const commissionKind = id => '[name="commission_kind_' + id + '"]';

async function professional(t) {
  const h = await suite(t, 'funcionarios');
  h.click('[data-os-professional="' + professionalId + '"]'); await flush();
  change(h, '[name=commission_mode]', 'per_service');
  return h;
}
async function goal(t) {
  const h = await suite(t, 'desempenho');
  h.click('[data-os-action=new-goal]'); await flush();
  change(h, '[data-os-form=goal] [name=scope]', 'company');
  return h;
}
async function loyalty(t) {
  const h = await suite(t, 'fidelizacao');
  h.click('[data-os-loyalty-tab=settings]'); await flush();
  return h;
}

test('comissão fixa com milhar e percentual com vírgula salvam unidades corretas e reabrem sem alteração', async t => {
  const h = await professional(t);
  change(h, commissionKind(cutId), 'fixed');
  assert.equal(h.document.querySelector(commissionValue(cutId)).value, '', 'uma nova unidade não reaproveita o percentual');
  h.fill(commissionValue(cutId), '1.234,56');
  h.fill(commissionValue(beardId), '25,5');
  h.submit('[data-os-form=professional]'); await flush();
  assert.equal(writes(h).length, 1);
  const payload = writes(h)[0].data;
  assert.equal(payload.commission_rules.find(rule => rule.service_id === cutId).commission_fixed_cents, 123456);
  assert.equal(payload.commission_rules.find(rule => rule.service_id === beardId).commission_percent, 25.5);
  h.click('[data-os-professional="' + professionalId + '"]'); await flush();
  assert.equal(h.document.querySelector(commissionKind(cutId)).value, 'fixed');
  assert.equal(h.document.querySelector(commissionValue(cutId)).value, '1.234,56');
  assert.equal(h.document.querySelector(commissionValue(beardId)).value, '25,5');
  assert.equal(h.errors.length, 0);
});

test('alternar R$ e % restaura rascunhos separados e percentual acima de 100 não chega à API', async t => {
  const h = await professional(t), selector = commissionValue(cutId);
  const input = h.fill(selector, '25,5');
  change(h, commissionKind(cutId), 'fixed');
  assert.equal(input.value, ''); assert.equal(input.dataset.orionField, 'money');
  h.fill(selector, '0,01');
  change(h, commissionKind(cutId), 'percent');
  assert.equal(input.value, '25,5'); assert.equal(input.dataset.orionField, 'decimal');
  h.fill(selector, '100,01'); h.submit('[data-os-form=professional]'); await flush();
  assert.equal(writes(h).length, 0); assert.equal(input.getAttribute('aria-invalid'), 'true');
  change(h, commissionKind(cutId), 'fixed');
  assert.equal(input.value, '0,01'); assert.equal(input.hasAttribute('aria-invalid'), false);
  change(h, commissionKind(cutId), 'percent'); assert.equal(input.value, '100,01');
  h.fill(selector, '100'); h.submit('[data-os-form=professional]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.commission_rules.find(rule => rule.service_id === cutId).commission_percent, 100);
  assert.equal(h.errors.length, 0);
});

test('serviço desmarcado não valida nem envia comissão oculta inválida', async t => {
  const h = await professional(t);
  h.fill(commissionValue(cutId), 'valor inválido');
  h.submit('[data-os-form=professional]'); await flush();
  assert.equal(writes(h).length, 0);
  const checkbox = h.document.querySelector('[name=service_ids][value="' + cutId + '"]');
  checkbox.checked = false; checkbox.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  const hidden = h.document.querySelector(commissionValue(cutId));
  assert.equal(hidden.disabled, true); assert.equal(hidden.required, false);
  assert.equal(hidden.value, 'valor inválido'); assert.equal(hidden.hasAttribute('aria-invalid'), false);
  h.fill(commissionValue(beardId), '12,5'); h.submit('[data-os-form=professional]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.deepEqual(writes(h)[0].data.service_ids, [beardId]);
  assert.deepEqual(writes(h)[0].data.commission_rules.map(rule => rule.service_id), [beardId]);
  assert.equal(h.errors.length, 0);
});

test('meta alterna atendimentos e faturamento sem converter o rascunho e envia centavos corretos', async t => {
  const h = await goal(t), target = h.fill('[data-os-form=goal] [name=target]', '23');
  assert.equal(target.dataset.orionField, 'decimal'); assert.equal(target.inputMode, 'numeric');
  change(h, '[data-os-form=goal] [name=metric]', 'revenue_cents');
  assert.equal(target.value, ''); assert.equal(target.dataset.orionField, 'money');
  assert.match(h.document.querySelector('label[for=osField-target]').textContent, /R\$/);
  h.fill('[data-os-form=goal] [name=target]', '1.234,56');
  change(h, '[data-os-form=goal] [name=metric]', 'appointments');
  assert.equal(target.value, '23');
  assert.doesNotMatch(h.document.querySelector('label[for=osField-target]').textContent, /R\$/);
  change(h, '[data-os-form=goal] [name=metric]', 'revenue_cents');
  assert.equal(target.value, '1.234,56');
  h.submit('[data-os-form=goal]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.metric, 'revenue_cents');
  assert.equal(writes(h)[0].data.target_value, 123456);
  assert.equal(writes(h)[0].data.scope, 'company');
  assert.equal(writes(h)[0].data.professional_id, null);
  assert.equal(writes(h)[0].data.recurrence, 'monthly');
  assert.equal('start_date' in writes(h)[0].data, false);
  assert.equal(h.errors.length, 0);
});

test('meta de atendimentos exige inteiro e usa o rascunho da quantidade ao voltar de reais', async t => {
  const h = await goal(t), selector = '[data-os-form=goal] [name=target]';
  const target = h.fill(selector, '20,5');
  h.submit('[data-os-form=goal]'); await flush();
  assert.equal(writes(h).length, 0); assert.equal(target.getAttribute('aria-invalid'), 'true');
  change(h, '[data-os-form=goal] [name=metric]', 'revenue_cents');
  assert.equal(target.value, ''); assert.equal(target.hasAttribute('aria-invalid'), false);
  h.fill(selector, '8,00');
  change(h, '[data-os-form=goal] [name=metric]', 'appointments');
  assert.equal(target.value, '20,5');
  h.fill(selector, '20'); h.submit('[data-os-form=goal]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.metric, 'appointments');
  assert.equal(writes(h)[0].data.target_value, 20);
  assert.equal(h.errors.length, 0);
});

test('fidelidade guarda rascunhos de percentual e valor fixo ao passar por serviço grátis', async t => {
  const h = await loyalty(t), selector = '[data-os-form=loyalty-inline] [name=reward_value]';
  const input = h.fill(selector, '15,5');
  change(h, '[name=reward_type]', 'fixed');
  assert.equal(input.value, ''); assert.equal(input.dataset.orionField, 'money');
  h.fill(selector, '1.000,01');
  change(h, '[name=reward_type]', 'free_service');
  assert.equal(input.disabled, true); assert.equal(input.required, false);
  assert.equal(h.document.querySelector('[name=reward_service_id]').disabled, false);
  change(h, '[name=reward_service_id]', cutId);
  change(h, '[name=reward_type]', 'percent');
  assert.equal(input.value, '15,5'); assert.equal(input.disabled, false);
  assert.equal(h.document.querySelector('[name=reward_service_id]').disabled, true);
  change(h, '[name=reward_type]', 'fixed');
  assert.equal(input.value, '1.000,01');
  h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.reward_type, 'fixed');
  assert.equal(writes(h)[0].data.reward_value_cents, 100001);
  assert.equal(writes(h)[0].data.reward_percent, null);
  assert.equal(writes(h)[0].data.reward_service_id, null);
  assert.equal(h.document.querySelector(selector).value, '1.000,01');
  assert.equal(h.errors.length, 0);
});

test('cupom grátis ignora valor oculto inválido, limpa seu erro e envia só o serviço selecionado', async t => {
  const h = await loyalty(t), input = h.fill('[name=reward_value]', '100,01');
  h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 0); assert.equal(input.getAttribute('aria-invalid'), 'true');
  change(h, '[name=reward_type]', 'free_service');
  input.value = 'rascunho oculto inválido';
  assert.equal(input.disabled, true); assert.equal(input.hasAttribute('aria-invalid'), false);
  change(h, '[name=reward_service_id]', cutId);
  h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.reward_type, 'free_service');
  assert.equal(writes(h)[0].data.reward_service_id, cutId);
  assert.equal(writes(h)[0].data.reward_percent, null);
  assert.equal(writes(h)[0].data.reward_value_cents, null);
  assert.equal(h.errors.length, 0);
});

test('percentual de cupom preserva vírgula, exige valor positivo e aceita o limite de 100%', async t => {
  const h = await loyalty(t), input = h.fill('[name=reward_value]', '0');
  h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 0); assert.equal(input.getAttribute('aria-invalid'), 'true');
  h.fill('[name=reward_value]', '15,5'); h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.reward_percent, 15.5);
  assert.equal(h.document.querySelector('[name=reward_value]').value, '15,5');
  h.fill('[name=reward_value]', '100'); h.submit('[data-os-form=loyalty-inline]'); await flush();
  assert.equal(writes(h).length, 2);
  assert.equal(writes(h)[1].data.reward_type, 'percent');
  assert.equal(writes(h)[1].data.reward_percent, 100);
  assert.equal(writes(h)[1].data.reward_value_cents, null);
  assert.equal(writes(h)[1].data.reward_service_id, null);
  assert.equal(h.errors.length, 0);
});

test('combo bloqueia centavo acima do limite do catálogo e aceita exatamente 100.000.000 centavos', async t => {
  const h = await suite(t, 'servicos', { servicePrices: [50000000, 50000000] });
  h.click('[data-os-action=new-package]'); await flush();
  h.fill('[data-os-form=package] [name=name]', 'Combo sintético');
  for (const checkbox of h.document.querySelectorAll('[data-os-form=package] [name=service_ids]')) {
    checkbox.checked = true; checkbox.dispatchEvent(new h.window.Event('change', { bubbles: true }));
  }
  const input = h.fill('[data-os-form=package] [name=price]', '1.000.000,01');
  h.submit('[data-os-form=package]'); await flush();
  assert.equal(writes(h).length, 0);
  assert.equal(input.getAttribute('aria-invalid'), 'true');
  assert.equal(input.value, '1.000.000,01');
  h.fill('[data-os-form=package] [name=price]', '1.000.000,00');
  h.submit('[data-os-form=package]'); await flush();
  assert.equal(writes(h).length, 1);
  assert.equal(writes(h)[0].data.price_cents, 100000000);
  assert.equal(h.errors.length, 0);
});
