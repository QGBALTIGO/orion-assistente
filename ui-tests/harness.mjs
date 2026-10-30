import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { JSDOM, VirtualConsole } from 'jsdom';

export const tenantA = '10000000-0000-4000-8000-000000000001';
export const tenantB = '20000000-0000-4000-8000-000000000002';
export const customerId = '30000000-0000-4000-8000-000000000003';
const defaultSourceURL = new URL('../app/static/orion-suite.js', import.meta.url);
export const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setImmediate(resolve)); };
export function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
export function customer(name = 'Cliente sintético', id = customerId) { return { id, name, active: true, phone: null, email: null, version: 1, total_appointments: 0, total_spent_cents: 0, loyalty_points: 0 }; }
export function planState(code='elite',overrides={}){
  const basic=['workspace.access','dashboard.basic','agenda.manage','clients.manage','performance.advanced','services.manage','professionals.manage','finance.basic','site.manage','notifications.basic','settings.manage','calendar.manage'];
  const advanced=['conversations.manual','goals.manage','finance.advanced','commissions.advanced','loyalty.manage','marketing.ai','marketing.campaigns','coupons.manage','consultant.ai','relationships.manage'];
  const features=code==='none'?[]:code==='essencial'?basic:code==='profissional'?[...basic,...advanced]:[...basic,...advanced,'reception.ai','whatsapp.ai_booking','reception.automation'];
  features.push('plan.manage','account.manage','business.setup');
  return {code,name:{essencial:'Essencial',profissional:'Profissional',elite:'Elite',none:'Sem acesso ativo'}[code],source:code==='none'?'none':'manual',status:code==='none'?'inactive':'active',active:code!=='none',valid_until:null,version:1,revision:'synthetic-'+code,features,permissions:Object.fromEntries(features.map(feature=>[feature,true])),limits:{max_professionals:{essencial:2,profissional:6,elite:10,none:0}[code],active_professionals:0,excess_professionals:0},manual_assignment:null,commercial_subscription:null,trial:null,platform_workspace:false,...overrides};
}

export function defaultResponse(path) {
  if(path==='/api/v1/admin/accounts/companies/unassigned')return {companies:[]};
  if(path.endsWith('/management/permissions'))return {effective_plan:planState()};
  if (path.includes('/management/dashboard')) return { summary: { appointments: 0, completed: 0, customers: 0 } };
  if (path.includes('/management/customers')) return { items: [], total: 0 };
  if (path.endsWith('/ai/status')) return { enabled: true, configured: true, remaining_today: 10, daily_limit: 10 };
  if (path.endsWith('/ai/history')) return { items: [] };
  if (path.endsWith('/management/team')) return { can_manage: true, members: [], invitations: [] };
  if (path.endsWith('/management/service-packages')) return { items: [] };
  return [];
}

export async function makeSuite({ initialRoute = 'clientes', role = 'owner', respond = defaultResponse, permissions=planState(), configureWindow, bridge: extra = {}, sourceURL = defaultSourceURL, url = 'https://orion.test/panel' } = {}) {
  const console = new VirtualConsole(), errors = [];
  console.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM('<!doctype html><html lang="pt-BR" data-theme="light"><body><div id="appView" class="app"><aside class="sidebar"><div class="brand">Orion</div></aside><main class="content"><section id="page-original" class="page">Área original</section></main></div></body></html>', { url, runScripts: 'outside-only', virtualConsole: console });
  const { window } = dom;
  window.scrollTo = () => {};
  window.HTMLElement.prototype.scrollTo = () => {};
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.matchMedia = query => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new window.Event('close')); };
  Object.defineProperty(window.crypto, 'randomUUID', { value: randomUUID, configurable: true });
  Object.defineProperty(window.navigator, 'clipboard', { value: { writeText: async () => {} }, configurable: true });
  configureWindow?.(window);
  const calls = [];
  const bridge = { user: { role: 'member', name: 'Conta sintética', email: 'synthetic@example.invalid' },
    tenants: [{ id: tenantA, name: 'Empresa A', slug: 'empresa-a', timezone: 'America/Sao_Paulo', role }],
    tenantId: tenantA, initialRoute, request: async (path, method = 'GET', data) => { calls.push({ path, method, data });if(path.endsWith('/management/permissions'))return typeof permissions==='function'?permissions(path):{effective_plan:permissions};return respond(path, method, data); }, openLegacy() {}, onRouteChange() {}, ...extra };
  window.eval(await readFile(new URL('../app/static/orion-fields.js', import.meta.url), 'utf8'));
  window.OrionFields.bind(window.document);
  window.eval(await readFile(new URL('../app/static/orion-appointment-communications.js',import.meta.url),'utf8'));
  window.eval(await readFile(sourceURL, 'utf8'));
  window.OrionSuite.mount(bridge);
  const document = window.document;
  function click(selector) { const element = document.querySelector(selector); if (!element) throw new Error(`Missing clickable element: ${selector}`); element.click(); return element; }
  function fill(selector, value) { const element = document.querySelector(selector); if (!element) throw new Error(`Missing field: ${selector}`); element.value = value; element.dispatchEvent(new window.Event('input', { bubbles: true })); return element; }
  function submit(selector) { const form = document.querySelector(selector); if (!form) throw new Error(`Missing form: ${selector}`); form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true })); return form; }
  return { dom, window, document, bridge, calls, errors, click, fill, submit, close() { window.OrionSuite.destroy(); dom.window.close(); } };
}
