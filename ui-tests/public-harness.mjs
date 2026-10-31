import { readFile } from 'node:fs/promises';
import { JSDOM, VirtualConsole } from 'jsdom';

export const publicService = '50000000-0000-4000-8000-000000000005';
export const publicAddon = '60000000-0000-4000-8000-000000000006';
export const publicProfessional = '70000000-0000-4000-8000-000000000007';
export const publicBase = '/api/v1/public/sites/empresa-sintetica';
export const firstStart = '2030-01-07T09:00:00-03:00';
export const quote = 'synthetic-quote-with-at-least-forty-characters-0001';

export function catalogue() {
  return {
    timezone: 'America/Sao_Paulo', today: '2030-01-07', booking_horizon_days: 60,
    opening_hours: Array.from({length:7}, (_,weekday)=>({
      weekday,intervals:weekday<5?[{open:'09:00',close:'18:00'}]:
        weekday===5?[{open:'09:00',close:'13:00'}]:[]
    })),
    site: { title: 'Empresa sintética', theme_color: '#2e7bf6', show_prices: true, hide_orion_brand: false,
      description: 'Atendimento com horário marcado.', address: 'Endereço sintético', city: 'São Paulo', state: 'SP',
      maps_url: 'https://www.google.com/maps', whatsapp_phone: '+5511999999999' },
    services: [{ id: publicService, name: 'Corte + barba', category: 'Combos', catalog_kind: 'combo',
      duration_min: 50, price_cents: 4500, color: '#8b5cf6', professional_ids: [publicProfessional],
      components: [{ name: 'Corte' }, { name: 'Barba' }],
      addons: [{ addon_service_id: publicAddon, name: 'Lavagem', price_cents: 700, duration_min: 20,
        add_duration: false, professional_ids: [publicProfessional] }] }],
    professionals: [{ id: publicProfessional, name: 'Profissional sintético', specialty: 'Cabelo e barba', color: '#31ad52' }],
  };
}

export const available = (start = firstStart, value = quote) => ({ quote: value, slots: [start],
  price_cents: 5200, duration_min: 50, services: ['Corte + barba', 'Lavagem'], timezone: 'America/Sao_Paulo' });
export const confirmed = () => ({ status: 'confirmed', protocol: 'SYNTHETIC-1', start_at: firstStart,
  end_at: '2030-01-07T09:50:00-03:00', price_cents: 5200, timezone: 'America/Sao_Paulo',
  professional: 'Profissional sintético', services: ['Corte + barba', 'Lavagem'] });
export const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

export async function makePublic({ path = '/s/empresa-sintetica', data = catalogue(), respond } = {}) {
  const html = await readFile(new URL('../app/static/orion-public.html', import.meta.url), 'utf8');
  const source = await readFile(new URL('../app/static/orion-public.js', import.meta.url), 'utf8');
  const errors = [], virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(html, { url: `https://orion.test${path}`, runScripts: 'outside-only', virtualConsole });
  const { window } = dom, { document } = window, calls = [];
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new window.Event('close')); };
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options, body: options.body ? JSON.parse(options.body) : null });
    if (respond) {
      const result = await respond(String(url), options);
      if (result !== undefined) return result;
    }
    if (/\/availability\?/.test(url)) return response(available());
    if (url.endsWith('/appointments')) return response(confirmed(), 201);
    return response(data);
  };
  window.eval(await readFile(new URL('../app/static/orion-fields.js', import.meta.url), 'utf8'));
  window.eval(await readFile(new URL('../app/static/orion-calendar-links.js', import.meta.url), 'utf8'));
  window.eval(source);
  function click(selector) { const node = document.querySelector(selector); if (!node) throw new Error(`Missing clickable element: ${selector}`); node.click(); return node; }
  function fill(selector, value) { const node = document.querySelector(selector); if (!node) throw new Error(`Missing field: ${selector}`); node.value = value; node.dispatchEvent(new window.Event('input', { bubbles: true })); return node; }
  return { dom, window, document, calls, errors, click, fill, close() { dom.window.close(); } };
}
