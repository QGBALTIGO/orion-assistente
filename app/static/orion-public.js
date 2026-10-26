/* Public catalogue data are rendered as text; quotes and contact details stay in memory. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const dialog = $('booking-dialog');
  const previewMatch = location.pathname.match(/^\/app\/site-preview\/([0-9a-f-]+)\/?$/i);
  const slugMatch = location.pathname.match(/^\/s\/([a-z0-9-]+)\/?$/);
  const preview = !!previewMatch;
  const apiBase = preview ? `/api/v1/tenants/${previewMatch[1]}/management/site/preview` : `/api/v1/public/sites/${slugMatch?.[1] || ''}`;
  let data, category = 'Todos', requestSequence = 0, availabilityController;
  let booking = newBooking();
  const themeButton=$('public-theme-toggle');
  let selectedTheme='system';
  function applyPublicTheme(){
    const systemDark=window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    const dark=selectedTheme==='dark'||(selectedTheme==='system'&&systemDark);
    document.documentElement.dataset.publicTheme=dark?'dark':'light';
    themeButton.setAttribute('aria-label','Tema: '+(selectedTheme==='system'?'sistema':selectedTheme==='dark'?'escuro':'claro')+'. Toque para alternar.');
    $('public-theme-symbol').textContent=selectedTheme==='system'?'◐':selectedTheme==='dark'?'☾':'☀';
  }
  themeButton.addEventListener('click',()=>{
    selectedTheme=selectedTheme==='system'?'light':selectedTheme==='light'?'dark':'system';
    try{localStorage.setItem('orion-public-theme',selectedTheme)}catch{}
    applyPublicTheme();
  });
  try{const stored=localStorage.getItem('orion-public-theme');if(['light','dark','system'].includes(stored))selectedTheme=stored;}catch{}
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change',applyPublicTheme);
  applyPublicTheme();


  function newBooking() { return { step: 1, service: null, addons: [], professional: null, day: null, start: null, quote: null, availability: null, availabilityError: null, loading: false, busy: false, result: null, name: '', phone: '', consent: false }; }
  function el(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = value; return node; }
  function button(text, cls, handler) { const node = el('button', cls, text); node.type = 'button'; if (handler) node.addEventListener('click', handler); return node; }
  function safeHTTPS(value) { try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : null; } catch { return null; } }
  function safeImageURL(value) {
    if(typeof value==='string' && /^\/media\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.webp$/.test(value))return value;
    return safeHTTPS(value);
  }
  function link(text, value, cls = 'outline') { const href = safeHTTPS(value); if (!href) return null; const node = el('a', cls, text); node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; return node; }
  function setLink(node, value) { const href = safeHTTPS(value); node.hidden = !href; if (href) { node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; } }
  function picture(container, url, initials) { const safe = safeImageURL(url); container.textContent = initials; if (safe) { const img = el('img'); img.src = safe; img.alt = ''; img.loading = 'lazy'; img.referrerPolicy = 'no-referrer'; img.addEventListener('error', () => { container.textContent = initials; }, { once: true }); container.replaceChildren(img); } }
  function initials(name) { return String(name || '').trim().split(/\s+/).slice(0, 2).map(p => p[0] || '').join('').toUpperCase(); }
  function money(cents) { return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(Number(cents) / 100); }
  function priceText(cents, mode) { return (mode === 'from' ? 'A partir de ' : '') + money(cents); }
  function duration(minutes) { const n = Number(minutes); return n >= 60 ? `${Math.floor(n / 60)} h${n % 60 ? ` ${n % 60} min` : ''}` : `${n} min`; }
  const WEEKDAYS=['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo'];
  function validTime(value) { return typeof value==='string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value); }
  function weeklyHours(raw) {
    if(!Array.isArray(raw)||raw.length!==7)return null;
    const slots=new Array(7).fill(null);
    for(const entry of raw) {
      const day=Number(entry?.weekday);
      if(!Number.isInteger(day)||day<0||day>6||slots[day]!==null||!Array.isArray(entry.intervals))return null;
      const intervals=entry.intervals.map(x=>[x?.open,x?.close]);
      if(intervals.some(([start,end])=>!validTime(start)||!validTime(end)||start>=end))return null;
      slots[day]=intervals;
    }
    return slots.every(Boolean)?slots:null;
  }
  function groupedHours(slots) {
    const groups=[];
    for(let day=0;day<7;day++){
      const value=slots[day].map(([start,end])=>start+'–'+end).join('|'),previous=groups.at(-1);
      if(previous?.key===value)previous.last=day;
      else groups.push({first:day,last:day,key:value,intervals:slots[day]});
    }
    return groups;
  }
  function renderOpeningHours(raw){
    const card=$('hours-card'),root=$('hours-list'),weekly=weeklyHours(raw);
    if(!weekly||!weekly.some(day=>day.length)){card.hidden=true;root.replaceChildren();return;}
    card.hidden=false;
    root.replaceChildren(...groupedHours(weekly).map(group=>{
      const row=el('div','hours-row');row.setAttribute('role','listitem');
      const label=group.first===group.last?WEEKDAYS[group.first]:
        WEEKDAYS[group.first]+' a '+WEEKDAYS[group.last].toLocaleLowerCase('pt-BR');
      const name=el('span','hours-day',label);
      const times=el('div',group.intervals.length?'hours-time':'hours-time hours-closed');
      if(!group.intervals.length) {
        times.textContent='Fechado';
      } else {
        const shiftList=el('span','hours-shifts');
        for(const [index,[start,end]] of group.intervals.entries()){
          if(index)shiftList.append(el('span','hours-separator','·'));
          shiftList.append(el('span','hours-shift',start+'–'+end));
        }
        times.append(shiftList);
        const pauses=group.intervals.slice(0,-1).map(([_,end],index)=>{
          const next=group.intervals[index+1][0];
          return end<next?end+'–'+next:null;
        }).filter(Boolean);
        if(pauses.length)
          times.append(el('small','hours-break','Intervalo '+pauses.join(' · ')));
      }
      row.append(name,times);return row;
    }));
  }
  function mapDestination(site){
    const address=[site.title,site.address,site.city,site.state,site.postal_code,'Brasil']
      .filter(v=>typeof v==='string'&&v.trim()).map(v=>v.trim()).join(', ');
    const hasAddress=[site.address,site.city,site.postal_code].some(v=>typeof v==='string'&&v.trim());
    let precise=null,placeId=null;
    const knownURL=safeHTTPS(site.maps_url);
    if(knownURL)try{
      const map=new URL(knownURL);
      if(['google.com','www.google.com','maps.google.com'].includes(map.hostname)){
        const linkText=map.pathname+map.search;
        const coord=linkText.match(/!3d(-?\d{1,2}(?:\.\d+)?)!4d(-?\d{1,3}(?:\.\d+)?)/)
          ||(map.searchParams.get('q')||'').match(/^(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)$/);
        if(coord){
          const lat=Number(coord[1]),lng=Number(coord[2]);
          if(Number.isFinite(lat)&&Number.isFinite(lng)&&Math.abs(lat)<=90&&Math.abs(lng)<=180)
            precise=lat+','+lng;
        }
        const id=map.searchParams.get('query_place_id')||map.searchParams.get('destination_place_id');
        if(id&&/^[\w-]{6,200}$/.test(id))placeId=id;
      }
    }catch{ /* Invalid Maps URLs fall back to business address. */ }
    if(!precise&&!hasAddress&&!placeId)return null;
    const destination=precise||address;
    const route=new URL('https://www.google.com/maps/dir/');
    route.searchParams.set('api','1');
    route.searchParams.set('destination',destination);
    if(placeId&&address&&!precise)route.searchParams.set('destination_place_id',placeId);
    route.searchParams.set('travelmode','driving');
    route.searchParams.set('dir_action','navigate');
    // A verified Google Maps Share > Embed URL works without a Maps API key.
    // Other Maps links fall back to the public search iframe. The supported,
    // key-based Maps Embed API can replace this fallback when configured.
    let embed=null;
    if(knownURL)try{
      const shared=new URL(knownURL);
      if(['www.google.com','google.com'].includes(shared.hostname)
          &&shared.pathname==='/maps/embed'&&shared.searchParams.has('pb'))
        embed=shared;
    }catch{}
    if(!embed){
      embed=new URL('https://maps.google.com/maps');
      embed.searchParams.set('q',destination);
      embed.searchParams.set('z','16');
      embed.searchParams.set('output','embed');
    }
    return {route:route.href,embed:embed.href};
  }
  function renderLocation(site){
    const address=[site.address,[site.city,site.state].filter(Boolean).join(' — '),site.postal_code]
      .filter(Boolean).join('\n');
    const where=$('address-card'),box=$('map-preview'),frame=$('location-map'),target=mapDestination(site);
    $('full-address').textContent=address;
    where.hidden=!address&&!target;
    box.hidden=!target;
    if(target){
      frame.src=target.embed;
      frame.title='Prévia do Google Maps: '+(site.title||'local de atendimento');
      setLink($('maps-link'),target.route);
      setLink($('hero-map'),target.route);
    }else{
      frame.removeAttribute('src');
      setLink($('maps-link'),null);
      setLink($('hero-map'),null);
    }
  }
  function color(node, key, value) { if (/^#[0-9a-f]{6}$/i.test(value || '')) node.style.setProperty(key, value); }
  function dateLabel(day, options) { return new Intl.DateTimeFormat('pt-BR', { timeZone: 'UTC', ...options }).format(new Date(`${day}T12:00:00Z`)); }
  function slotTime(start) { return new Intl.DateTimeFormat('pt-BR', { timeZone: data.timezone, hour: '2-digit', minute: '2-digit' }).format(new Date(start)); }
  function addDays(day, count) { const date = new Date(`${day}T12:00:00Z`); date.setUTCDate(date.getUTCDate() + count); return date.toISOString().slice(0, 10); }
  function selectedService() { return data.services.find(s => s.id === booking.service); }
  function selectedAddons() { return (selectedService()?.addons || []).filter(a => booking.addons.includes(a.addon_service_id)); }
  function eligibleProfessionals() { const service = selectedService(); return data.professionals.filter(p => service?.professional_ids.includes(p.id) && selectedAddons().every(a => a.professional_ids.includes(p.id))); }
  function selectionTotal() { const service = selectedService(), addons = selectedAddons(); return {
    price: (service?.price_cents || 0) + addons.reduce((n, a) => n + a.price_cents, 0),
    price_mode: service?.price_mode === 'from' || addons.some(a => a.price_mode === 'from') ? 'from' : 'fixed',
    minutes: (service?.duration_min || 0) + addons.reduce((n, a) => n + (a.add_duration ? a.duration_min : 0), 0)
  }; }
  function showError(message) { $('booking-error').textContent = message || ''; $('booking-error').hidden = !message; }

  async function api(path = '', options = {}) {
    const controller = options.signal ? null : new AbortController();
    const timeout = controller && setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(apiBase + path, { credentials: preview ? 'same-origin' : 'omit', ...options, signal: options.signal || controller?.signal, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) { const error = new Error(typeof body.detail === 'string' ? body.detail : response.status === 422 ? 'Confira os dados e tente novamente.' : 'Não foi possível concluir. Tente novamente.'); error.status = response.status; throw error; }
      return body;
    } finally { if (timeout) clearTimeout(timeout); }
  }

  function serviceCard(service, handler, isSelected = false) {
    const card = button('', 'service-card', handler); card.setAttribute('aria-pressed', String(isSelected));
    const thumb = el('span', 'service-thumb'); color(thumb, '--service-color', service.color); picture(thumb, service.image_url, '✂');
    const detail = el('span', 'service-detail');
    if (service.catalog_kind === 'combo') detail.append(el('span', 'tag', service.featured ? 'COMBO EM DESTAQUE' : 'COMBO'));
    detail.append(el('strong', '', service.name));
    if (service.description) detail.append(el('p', '', service.description));
    const meta = el('span', 'service-meta');
    if (data.site.show_prices) {
      meta.append(el('span', 'price', priceText(service.price_cents, service.price_mode)));
      const separator = el('span', 'service-meta-separator', '•');
      separator.setAttribute('aria-hidden', 'true');
      meta.append(separator);
    }
    meta.append(el('span', 'service-duration', duration(service.duration_min).replace(/(\d)\s+(h|min)\b/g, '$1$2')));
    detail.append(meta); card.append(thumb, detail, el('span', 'service-arrow', isSelected ? '✓' : '›'));
    return card;
  }

  function renderServiceGrid() {
    const term = $('service-search').value.trim().toLocaleLowerCase('pt-BR');
    const items = data.services.filter(s => (category === 'Todos' || (s.category || 'Serviços') === category) && `${s.name} ${s.description || ''}`.toLocaleLowerCase('pt-BR').includes(term));
    $('services-grid').replaceChildren(...items.map(s => serviceCard(s, () => openBooking(s.id))));
    if (!items.length) $('services-grid').append(el('p', 'empty', data.services.length ? 'Nenhum serviço encontrado. Tente outra busca.' : 'Não há serviços disponíveis para agendamento neste momento.'));
  }

  function renderPage() {
    const site = data.site;
    document.title = `${site.title} • Agendamento`;
    color(document.documentElement, '--primary', site.theme_color);
    const hex = /^#[0-9a-f]{6}$/i.test(site.theme_color || '') ? site.theme_color.slice(1) : '2e7bf6';
    const luminance = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
    document.documentElement.style.setProperty('--primary-ink', luminance > .179 ? '#0a1425' : '#ffffff');
    document.querySelector('meta[name=theme-color]').content = `#${hex}`;
    const cover = safeImageURL(site.cover_url); if (cover) { const image = el('img'); image.src = cover; image.alt = ''; image.referrerPolicy = 'no-referrer'; image.style.width = '100%'; image.style.height = '100%'; $('cover').replaceChildren(image); }
    picture($('site-logo'), site.logo_url, initials(site.title));
    $('site-title').textContent = site.title; $('footer-title').textContent = site.title;
    $('hero-address').textContent = [site.address, site.city].filter(Boolean).join(' · ');
    $('hero-address').hidden = !$('hero-address').textContent;
    $('about-text').textContent = site.description || '';
    $('about-card').hidden = !site.description;
    renderOpeningHours(data.opening_hours);
    renderLocation(site);
    $('services-count').textContent = String(data.services.length);
    $('hero-book').disabled = !data.services.length;
    const cats = ['Todos', ...new Set(data.services.map(s => s.category || 'Serviços'))];
    $('categories').hidden = cats.length <= 2;
    function renderCats() { $('categories').replaceChildren(...cats.map(name => { const node = button(name, 'category', () => { category = name; renderCats(); renderServiceGrid(); }); node.setAttribute('aria-pressed', String(name === category)); return node; })); }
    renderCats(); renderServiceGrid();
    $('team-grid').replaceChildren(...data.professionals.map(person => { const card = el('article', 'team-card'); const portrait = el('div', 'portrait'); color(portrait, '--person-color', person.color); picture(portrait, person.image_url, initials(person.name)); const info = el('div', 'person-info'); info.append(el('strong', '', person.name)); if (person.specialty) info.append(el('p', '', person.specialty)); card.append(portrait, info); return card; }));
    $('equipe').hidden = !data.professionals.length;
    const contacts=[];
    const glyphs={
      Instagram:'<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r=".9" fill="currentColor" stroke="none"/>',
      Facebook:'<path d="M15 21v-8h3l.5-4H15V7c0-1.3.4-2 2-2h2V1.5A24 24 0 0 0 16 1c-3.5 0-5 2-5 5v3H8v4h3v8"/>',
      TikTok:'<path d="M14 3v11.5a4.5 4.5 0 1 1-4.5-4.5"/><path d="M14 3c1 3 3 4.5 6 4.5"/>',
      WhatsApp:'<path d="M20 11.5a8 8 0 0 1-11.7 7L4 20l1.5-4.3A8 8 0 1 1 20 11.5Z"/><path d="M9 8c.4 3 2.4 5 5.5 6l1.5-1.5"/>',
      Telefone:'<path d="M7 3 4 6c-.5 5 9 14.5 14 14l3-3-5-4-2 2c-2-1-4-3-5-5l2-2-4-5Z"/>',
      Site:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c-5 5-5 13 0 18M12 3c5 5 5 13 0 18"/>'
    };
    function contactIcon(name,href){
      const node=el('a','contact-social');
      node.href=href;node.setAttribute('aria-label',name);node.title=name;
      if(!href.startsWith('tel:')){node.target='_blank';node.rel='noopener noreferrer';}
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');
      svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.8');
      svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');
      svg.setAttribute('aria-hidden','true');
      // Fixed trusted SVG paths, never interpolated from tenant content.
      const fragment=document.createElement('template');fragment.innerHTML='<svg xmlns="http://www.w3.org/2000/svg">'+glyphs[name]+'</svg>';
      while(fragment.content.firstChild?.firstChild)svg.append(fragment.content.firstChild.firstChild);
      node.append(svg);return node;
    }
    if(/^\+[1-9]\d{9,14}$/.test(site.whatsapp_phone||''))contacts.push(contactIcon('WhatsApp','https://wa.me/'+site.whatsapp_phone.slice(1)));
    if(/^\+[1-9]\d{9,14}$/.test(site.contact_phone||''))contacts.push(contactIcon('Telefone','tel:'+site.contact_phone));
    for(const [key,name] of [['instagram_url','Instagram'],['facebook_url','Facebook'],['tiktok_url','TikTok'],['external_url','Site']]){
      const url=safeHTTPS(site[key]);if(url)contacts.push(contactIcon(name,url));
    }
    $('contact-links').replaceChildren(...contacts);
    $('contact-section').hidden=!contacts.length;
    $('orion-credit').hidden = !!site.hide_orion_brand; $('preview-banner').hidden = !preview;
    $('site-content').hidden = false; $('page-message').hidden = true;
  }

  function openBooking(service = null) {
    booking = newBooking(); booking.service = service; booking.day = data.today;
    $('booking-shop').textContent = data.site.title; showError(); renderBooking();
    dialog.showModal(); document.body.classList.add('booking-open');
  }
  function closeBooking() { if (booking.busy) return; availabilityController?.abort(); requestSequence++; dialog.close(); document.body.classList.remove('booking-open'); }
  function invalidateAvailability() { availabilityController?.abort(); requestSequence++; booking.quote = null; booking.start = null; booking.availability = null; booking.availabilityError = null; booking.loading = false; booking.consent = false; }
  function chooseService(service) { invalidateAvailability(); booking.service = service; booking.addons = []; booking.professional = null; showError(); renderBooking(); }

  function renderBooking() {
    const body = $('booking-body'); body.replaceChildren();
    $('booking-steps').hidden = !!booking.result;
    for (const step of $('booking-steps').children) { const n = Number(step.dataset.step); step.classList.toggle('done', n < booking.step); if (n === booking.step) step.setAttribute('aria-current', 'step'); else step.removeAttribute('aria-current'); }
    if (booking.result) renderSuccess(body);
    else if (booking.step === 1) renderSelection(body);
    else if (booking.step === 2) renderTime(body);
    else renderConfirmation(body);
    updateFooter();
  }

  function renderSelection(body) {
    body.append(el('h3', '', 'Escolha o serviço'));
    const list = el('div', 'services-grid'); list.append(...data.services.map(s => serviceCard(s, () => chooseService(s.id), s.id === booking.service))); body.append(list);
    const service = selectedService();
    if (service?.components?.length) body.append(el('p', 'small muted', `Inclui: ${service.components.map(c => c.name).join(' + ')}.`));
    if (service?.addons?.length) {
      const extras = el('section', 'addon-section'); extras.append(el('h3', '', 'Complemente seu atendimento'), el('p', '', 'Adicionais opcionais. Confira o valor e o tempo de cada opção.'));
      for (const addon of service.addons) { const label = el('label', 'addon-option'); const input = el('input'); input.type = 'checkbox'; input.checked = booking.addons.includes(addon.addon_service_id); input.addEventListener('change', () => { invalidateAvailability(); booking.addons = input.checked ? [...booking.addons, addon.addon_service_id] : booking.addons.filter(id => id !== addon.addon_service_id); booking.professional = null; showError(eligibleProfessionals().length ? '' : 'Nenhum profissional realiza essa combinação. Remova um adicional para continuar.'); updateFooter(); }); const copy = el('span', '', addon.name); copy.append(el('small', '', addon.add_duration ? `Acrescenta ${duration(addon.duration_min)}` : 'Realizado dentro do tempo do serviço')); label.append(input, copy); if (data.site.show_prices) label.append(el('b', '', addon.price_mode==='from'?priceText(addon.price_cents,'from'):`+ ${money(addon.price_cents)}`)); extras.append(label); }
      body.append(extras);
    }
  }

  function renderTime(body) {
    body.append(el('h3', '', 'Escolha o profissional e o horário'));
    const people = eligibleProfessionals();
    const picker = el('div', 'professional-picker'); picker.setAttribute('aria-label', 'Profissionais disponíveis');
    for (const person of people) { const node = button('', 'professional-choice', () => { invalidateAvailability(); booking.professional = person.id; renderBooking(); loadAvailability(); }); node.setAttribute('aria-pressed', String(person.id === booking.professional)); const face = el('span', 'mini-portrait'); picture(face, person.image_url, initials(person.name)); node.append(face, el('span', '', person.name)); picker.append(node); }
    body.append(picker);
    const heading = el('div', 'date-heading'); heading.append(el('strong', '', dateLabel(booking.day, { month: 'long', year: 'numeric' })));
    const label = el('label', '', 'Outra data'); const input = el('input'); input.type = 'date'; input.min = data.today; input.max = addDays(data.today, data.booking_horizon_days); input.value = booking.day; input.addEventListener('change', () => { if (input.value && input.checkValidity()) selectDay(input.value); }); label.append(input); heading.append(label); body.append(heading);
    const strip = el('div', 'date-strip'); const begin = booking.day > addDays(data.today, 13) ? booking.day : data.today;
    for (let i = 0; i < 14; i++) { const day = addDays(begin, i); if (day > input.max) break; const node = button('', 'day-button', () => selectDay(day)); node.append(el('span', '', dateLabel(day, { weekday: 'short' }).replace('.', '')), el('strong', '', dateLabel(day, { day: '2-digit' }))); node.setAttribute('aria-label', dateLabel(day, { dateStyle: 'full' })); node.setAttribute('aria-pressed', String(day === booking.day)); strip.append(node); }
    body.append(strip, el('p', 'timezone-note', `Horários locais · ${data.timezone.replaceAll('_', ' ')}`));
    const slots = el('div'); slots.id = 'time-slots'; slots.setAttribute('aria-live', 'polite'); body.append(slots); renderSlots();
  }
  function selectDay(day) { invalidateAvailability(); booking.day = day; showError(); renderBooking(); loadAvailability(); }
  function renderSlots() {
    const root = $('time-slots'); if (!root) return; root.replaceChildren();
    if (preview) { root.append(el('p', 'preview-note', 'Esta é uma prévia. Publique o site para disponibilizar a consulta de horários e receber agendamentos de clientes.')); return; }
    if (booking.loading) { const message = el('div', 'slot-message'); message.append(el('span', 'spinner'), el('span', '', 'Buscando horários disponíveis…')); root.append(message); return; }
    if (booking.availabilityError) { root.append(el('p', 'slot-message', booking.availabilityError), button('Tentar novamente', 'outline', loadAvailability)); return; }
    if (!booking.availability) { root.append(el('p', 'slot-message', 'Selecione o profissional e a data para consultar a agenda.')); return; }
    if (!booking.availability.slots.length) { const reason = booking.availability.availability_reason; root.append(el('p', 'empty', reason === 'no_working_hours' ? 'Este profissional não tem expediente cadastrado neste dia. Escolha outra data ou profissional.' : 'Não há vagas compatíveis com a duração do serviço, o expediente e os intervalos nesta data. Experimente outro dia ou profissional.')); return; }
    const list = el('div', 'slots-grid');
    for (const start of booking.availability.slots) { const node = button(slotTime(start), 'slot', () => { booking.start = start; booking.consent = false; showError(); renderSlots(); updateFooter(); }); node.setAttribute('aria-pressed', String(start === booking.start)); list.append(node); }
    root.append(list);
  }
  async function loadAvailability() {
    if (preview || !booking.professional) return;
    const seq = ++requestSequence; availabilityController?.abort(); const controller = new AbortController(); availabilityController = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    booking.loading = true; booking.availabilityError = null; showError(); renderSlots(); updateFooter();
    const params = new URLSearchParams({ service_id: booking.service, professional_id: booking.professional, day: booking.day }); booking.addons.forEach(id => params.append('addon_ids', id));
    try { const result = await api(`/availability?${params}`, { signal: controller.signal }); if (seq !== requestSequence) return; booking.availability = result; booking.availabilityError = null; booking.quote = result.quote; }
    catch (error) { if (seq !== requestSequence) return; booking.availabilityError = error.name === 'AbortError' ? 'A consulta demorou mais que o esperado. Tente novamente.' : (error.message || 'Não foi possível consultar a agenda. Tente novamente.'); showError(); }
    finally { clearTimeout(timeout); if (seq === requestSequence) { booking.loading = false; renderSlots(); updateFooter(); } }
  }

  function reviewCard(result = null) {
    const box = el('div', 'review-card'); box.append(el('h3', '', 'Seu atendimento'));
    const person = result?.professional || data.professionals.find(p => p.id === booking.professional)?.name;
    const services = result?.services || booking.availability?.services || [selectedService()?.name, ...selectedAddons().map(a => a.name)];
    const start = result?.start_at || booking.start;
    const lines = [['Serviços', services.filter(Boolean).join(' + ')], ['Profissional', person], ['Data', new Intl.DateTimeFormat('pt-BR', { dateStyle: 'long', timeZone: data.timezone }).format(new Date(start))], ['Horário', slotTime(start)]];
    if (result?.protocol) lines.push(['Protocolo', result.protocol]);
    for (const [key, value] of lines) { const row = el('div', 'review-row'); row.append(el('span', '', key), el('strong', '', value)); box.append(row); }
    const estimate = booking.availability || selectionTotal();
    const variable = (result?.price_mode || estimate.price_mode) === 'from';
    const total = el('div', 'review-row total');
    total.append(el('span', '', variable?'Valor mínimo previsto':'Valor do atendimento'),
      el('strong', '', priceText(result?.price_cents ?? estimate.price_cents ?? estimate.price,variable?'from':'fixed')));
    box.append(total);
    if(variable)box.append(el('p','price-disclaimer',
      'O valor exibido é inicial. O preço final depende da avaliação do serviço e será confirmado pela equipe. Nenhum pagamento é cobrado nesta página.'));
    return box;
  }
  function renderConfirmation(body) {
    body.append(el('h3', '', 'Confira e confirme seu agendamento'), reviewCard());
    const form = el('form', 'booking-form'); form.id = 'customer-form'; form.addEventListener('submit', event => { event.preventDefault(); submitBooking(); });
    for (const spec of [{ key: 'name', title: 'Seu nome', autocomplete: 'name', type: 'text', max: 120, placeholder: 'Como podemos chamar você?' }, { key: 'phone', title: 'Telefone com DDD', autocomplete: 'tel', type: 'tel', max: 40, placeholder: '(11) 99999-9999' }]) {
      const label = el('label', 'field', spec.title); const field = el('input'); field.name = `customer_${spec.key}`; field.type = spec.type; field.autocomplete = spec.autocomplete; field.maxLength = spec.max; field.required = true; field.placeholder = spec.placeholder; field.value = booking[spec.key]; if (spec.key === 'phone') { field.inputMode = 'tel'; field.minLength = 10; } field.addEventListener('input', () => { booking[spec.key] = field.value; }); label.append(field); form.append(label);
    }
    const consent = el('label', 'consent-label'); const check = el('input'); check.type = 'checkbox'; check.required = true; check.name = 'confirmed'; check.checked = booking.consent; check.addEventListener('change', () => { booking.consent = check.checked; updateFooter(); }); consent.append(check, el('span', '', 'Conferi os serviços, o profissional, a data, o horário e o valor. Quero confirmar este agendamento.')); form.append(consent, el('p', 'privacy-note',
      'Seu nome e telefone serão usados para identificar o agendamento. Serviços “a partir de” terão o valor final confirmado pela equipe. Nenhum pagamento é cobrado nesta página.')); body.append(form);
  }
  function updateFooter() {
    const next = $('booking-next'), back = $('booking-back'), total = $('booking-total');
    back.hidden = booking.step === 1 || !!booking.result; back.disabled = booking.busy; $('close-booking').disabled = booking.busy;
    total.replaceChildren();
    if (booking.service && !booking.result) { const amount = selectionTotal(); total.append(el('span', '', duration(booking.availability?.duration_min ?? amount.minutes))); if (data.site.show_prices || booking.step === 3) total.append(el('strong', '', priceText(booking.availability?.price_cents ?? amount.price,booking.availability?.price_mode || amount.price_mode))); }
    next.textContent = booking.result ? 'Concluir' : booking.busy ? 'Confirmando…' : booking.step === 3 ? 'Confirmar agendamento' : 'Continuar →';
    next.disabled = booking.busy || (!booking.result && (booking.step === 1 ? !booking.service || !eligibleProfessionals().length : booking.step === 2 ? !booking.start || !booking.quote || booking.loading || preview : !booking.consent));
  }
  async function submitBooking() {
    if (booking.busy || !booking.consent || preview || !booking.quote || !booking.start) return;
    const form = $('customer-form'); if (!form.reportValidity()) return;
    if (!booking.name.trim() || booking.phone.replace(/\D/g, '').length < 10) { showError('Informe seu nome e um telefone válido com DDD.'); return; }
    booking.busy = true; showError(); updateFooter();
    try {
      booking.result = await api('/appointments', { method: 'POST', body: JSON.stringify({ quote: booking.quote, start_at: booking.start, customer_name: booking.name.trim(), customer_phone: booking.phone, confirmed: true }) });
      booking.name = ''; booking.phone = ''; booking.quote = null; renderBooking();
    } catch (error) {
      if ([404, 409, 422].includes(error.status)) { invalidateAvailability(); booking.step = 2; renderBooking(); }
      showError(error.name === 'AbortError' ? 'A resposta demorou. Tente confirmar novamente com os mesmos dados para recuperar o resultado.' : error.message);
    } finally { booking.busy = false; updateFooter(); }
  }
  function renderSuccess(body) {
    const success = el('div', 'success'); success.append(el('div', 'success-check', '✓'), el('h3', '', 'Agendamento confirmado!'), el('p', '', 'Seu horário está reservado. Guarde o protocolo e chegue no horário combinado.'), reviewCard(booking.result));
    success.append(button('Adicionar ao meu calendário', 'outline', downloadCalendar)); body.append(success);
  }
  function downloadCalendar() {
    const result = booking.result; if (!result) return;
    const escape = value => String(value || '').replaceAll('\\', '\\\\').replaceAll('\n', '\\n').replaceAll(';', '\\;').replaceAll(',', '\\,').replaceAll('\r', '');
    const stamp = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Orion//Agendamento//PT-BR', 'BEGIN:VEVENT', `UID:${escape(result.protocol)}-${stamp(result.start_at)}@orion.invalid`, `DTSTAMP:${stamp(new Date())}`, `DTSTART:${stamp(result.start_at)}`, `DTEND:${stamp(result.end_at)}`, `SUMMARY:${escape(data.site.title)} — ${escape(result.services.join(' + '))}`, `DESCRIPTION:Protocolo ${escape(result.protocol)}. Profissional: ${escape(result.professional)}.`, `LOCATION:${escape([data.site.address, data.site.city, data.site.state].filter(Boolean).join(', '))}`, 'END:VEVENT', 'END:VCALENDAR'];
    const url = URL.createObjectURL(new Blob([lines.join('\r\n') + '\r\n'], { type: 'text/calendar;charset=utf-8' })); const anchor = el('a'); anchor.href = url; anchor.download = 'agendamento.ics'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  $('service-search').addEventListener('input', renderServiceGrid);
  $('hero-book').addEventListener('click', () => openBooking());
  $('close-booking').addEventListener('click', closeBooking);
  dialog.addEventListener('cancel', event => { if (booking.busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { document.body.classList.remove('booking-open'); availabilityController?.abort(); requestSequence++; });
  $('booking-back').addEventListener('click', () => { if (booking.busy) return; booking.step--; showError(); renderBooking(); });
  $('booking-next').addEventListener('click', () => {
    if (booking.result) { closeBooking(); return; }
    if (booking.step === 3) { submitBooking(); return; }
    if (booking.step === 1) { if (!selectedService() || !eligibleProfessionals().length) return; booking.professional = eligibleProfessionals()[0].id; booking.step = 2; showError(); renderBooking(); loadAvailability(); }
    else if (booking.start && booking.quote) { booking.step = 3; showError(); renderBooking(); }
  });
  $('share-site').addEventListener('click', async () => {
    const message = $('share-message');
    if (preview) { message.textContent = 'Publique o site para compartilhar o endereço público.'; message.hidden = false; return; }
    try { if (navigator.share) await navigator.share({ title: data.site.title, url: location.href }); else { await navigator.clipboard.writeText(location.href); message.textContent = 'Link copiado.'; message.hidden = false; } }
    catch (error) { if (error.name !== 'AbortError') { message.textContent = 'Copie o endereço do site na barra do navegador para compartilhar.'; message.hidden = false; } }
  });
  api().then(result => { data = result; renderPage(); }).catch(error => { const node = $('page-message'); node.replaceChildren(el('h1', '', error.status === 404 ? 'Página indisponível' : 'Não foi possível carregar o site'), el('p', 'muted', error.status === 404 ? 'Este endereço ainda não foi publicado ou deixou de estar disponível.' : error.message)); });
})();
