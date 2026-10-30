/* Public catalogue data are rendered as text; quotes and contact details stay in memory. */
(() => {
  'use strict';
  const fields = window.OrionFields;
  fields.bind(document);
  const $ = id => document.getElementById(id);
  const dialog = $('booking-dialog');
  const previewMatch = location.pathname.match(/^\/app\/site-preview\/([0-9a-f-]+)\/?$/i);
  const slugMatch = location.pathname.match(/^\/s\/([a-z0-9-]+)\/?$/);
  const preview = !!previewMatch;
  const apiBase = preview ? `/api/v1/tenants/${previewMatch[1]}/management/site/preview` : `/api/v1/public/sites/${slugMatch?.[1] || ''}`;
  let data, category = 'Todos', requestSequence = 0, availabilityController;
  let retainedTimeView = null; // Keep photos mounted across wizard step navigation.
  let booking = newBooking();

  function newBooking() { return { step: 1, service: null, services: [], addons: [], notice: '', professional: null, day: null, start: null, quote: null, availability: null, availabilityError: null, loading: false, busy: false, result: null, name: '', phone: '', birthDate: '', consent: false }; }
  function el(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = value; return node; }
  function button(text, cls, handler) { const node = el('button', cls, text); node.type = 'button'; if (handler) node.addEventListener('click', handler); return node; }
  function safeHTTPS(value) { try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : null; } catch { return null; } }
  function safeImageURL(value) {
    if(typeof value==='string' && /^\/media\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.webp$/.test(value))return value;
    return safeHTTPS(value);
  }
  function setLink(node, value) { const href = safeHTTPS(value); node.hidden = !href; if (href) { node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer'; } }
  const CONTACT_ICONS = {"whatsapp":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.85\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M20 11.7a8 8 0 0 1-11.8 7.1L3.5 20l1.4-4.5A8 8 0 1 1 20 11.7Z\"/><path d=\"M8.2 8c.4-.5.8-.4 1.1.1l.9 1.6c.2.4.1.6-.2.9l-.6.6a6.5 6.5 0 0 0 3.4 3.4l.7-.7c.2-.3.5-.3.9-.1l1.6.9c.4.2.5.6.2 1.1-.3.6-1.1 1.1-1.9 1-3.5-.7-6-3.2-6.8-6.7-.1-.8.2-1.6.7-2.1\"/></svg>","phone":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.85\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M21 16.7v2.8a2 2 0 0 1-2.2 2A18.3 18.3 0 0 1 2.5 5.2 2 2 0 0 1 4.5 3h2.8a2 2 0 0 1 2 1.7l.4 2.7a2 2 0 0 1-.6 1.7l-1.5 1.3a14.6 14.6 0 0 0 6 6l1.3-1.5a2 2 0 0 1 1.7-.6l2.7.4a2 2 0 0 1 1.7 2Z\"/></svg>","instagram":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.9\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"5\"/><circle cx=\"12\" cy=\"12\" r=\"4\"/><circle cx=\"17.5\" cy=\"6.5\" r=\"1\" fill=\"currentColor\" stroke=\"none\"/></svg>","facebook":"<svg viewBox=\"0 0 24 24\" fill=\"currentColor\" aria-hidden=\"true\"><path d=\"M14.2 21v-7.8h2.6l.4-3.1h-3V8c0-.9.3-1.6 1.6-1.6h1.5V3.6c-.3-.1-1.3-.2-2.5-.2-2.5 0-4.2 1.5-4.2 4.2v2.5H8v3.1h2.6V21h3.6Z\"/></svg>","tiktok":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M14.2 3v11.5a4.2 4.2 0 1 1-4.2-4.2c.4 0 .9.1 1.2.2\"/><path d=\"M14.2 3c.6 3.3 2.5 5.3 5.8 5.6\"/></svg>","website":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.85\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M3 12h18M12 3c2.7 2.4 4 5.4 4 9s-1.3 6.6-4 9c-2.7-2.4-4-5.4-4-9s1.3-6.6 4-9Z\"/></svg>"};
  function contactAnchor(name, kind, address, isPhone = false) {
    const href = isPhone ? (/^tel:\+[1-9]\d{9,14}$/.test(address) ? address : null) : safeHTTPS(address);
    if (!href) return null;
    const node = el('a', 'social-link social-link--' + kind);
    node.href = href;
    node.setAttribute('aria-label', name);
    node.title = name;
    if (!isPhone) { node.target = '_blank'; node.rel = 'noopener noreferrer'; }
    node.innerHTML = CONTACT_ICONS[kind]; // Fixed local SVGs; untrusted site data never enters markup.
    return node;
  }
  function thumbnailURL(safe) {
    return /^\/media\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.webp$/.test(safe)
      ? safe.replace(/\.webp$/, '/thumb.webp') : safe;
  }
  function picture(container, url, initials, priority = 'lazy') {
    const safe = safeImageURL(url);
    container.textContent = initials;
    if (!safe) return;
    const thumb = thumbnailURL(safe);
    const img = el('img');
    img.alt = '';
    img.loading = priority === 'lazy' ? 'lazy' : 'eager';
    img.fetchPriority = priority === 'hero' ? 'high' : priority === 'eager' ? 'auto' : 'low';
    img.decoding = 'async';
    img.referrerPolicy = 'no-referrer';
    // A rolling deploy or storage hiccup must not hide the original photo.
    img.addEventListener('error', () => {
      if (thumb !== safe && img.getAttribute('src') === thumb) { img.src = safe; return; }
      container.textContent = initials;
    });
    img.src = thumb;
    container.replaceChildren(img);
  }
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
  function siteAddressLine(site){return site.address_display||site.address;}
  function mapDestination(site){
    const address=[site.title,siteAddressLine(site),site.city,site.state,site.postal_code,'Brasil']
      .filter(v=>typeof v==='string'&&v.trim()).map(v=>v.trim()).join(', ');
    const hasAddress=[siteAddressLine(site),site.city,site.postal_code].some(v=>typeof v==='string'&&v.trim());
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
    const address=[siteAddressLine(site),[site.city,site.state].filter(Boolean).join(' — '),site.postal_code]
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
  function selectedServices() {
    return booking.services.map(id => data.services.find(s => s.id === id)).filter(Boolean);
  }
  function selectedService() { return selectedServices()[0] || null; }
  function selectedAddons() {
    return (selectedService()?.addons || []).filter(a => booking.addons.includes(a.addon_service_id));
  }
  function eligibleProfessionals() {
    const services = selectedServices(), addons = selectedAddons();
    if (!services.length) return [];
    return data.professionals.filter(p =>
      services.every(s => s.professional_ids?.includes(p.id)) &&
      addons.every(a => a.professional_ids?.includes(p.id)));
  }
  function selectionTotal() {
    const services = selectedServices(), addons = selectedAddons(), all = [...services, ...addons];
    return {
      price: all.reduce((n, item) => n + item.price_cents, 0),
      price_mode: all.some(s => s.price_mode === 'from') ? 'from' : 'fixed',
      minutes: services.reduce((n, s) => n + s.duration_min, 0) +
        addons.reduce((n, a) => n + (a.add_duration ? a.duration_min : 0), 0)
    };
  }
  function componentIds(service) {
    if (service.catalog_kind === 'combo' && service.components?.length &&
        service.components.every(c => c.service_id))
      return service.components.map(c => c.service_id);
    return [service.id];
  }
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

  function serviceCard(service, handler, isSelected = false, imagePriority = 'lazy') {
    const card = button('', 'service-card', handler); card.setAttribute('aria-pressed', String(isSelected));
    const thumb = el('span', 'service-thumb'); color(thumb, '--service-color', service.color); picture(thumb, service.image_url, '✂', imagePriority);
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
    meta.append(el('span', 'service-duration', duration(service.duration_min)));
    detail.append(meta); card.append(thumb, detail, el('span', 'service-arrow', isSelected ? '✓' : '›'));
    return card;
  }

  function renderServiceGrid() {
    const term = $('service-search').value.trim().toLocaleLowerCase('pt-BR');
    const items = data.services.filter(s => (category === 'Todos' || (s.category || 'Serviços') === category) && `${s.name} ${s.description || ''}`.toLocaleLowerCase('pt-BR').includes(term));
    $('services-grid').replaceChildren(...items.map((s, i) => serviceCard(s, () => openBooking(s.id), false, i < 2 ? 'eager' : 'lazy')));
    if (!items.length) $('services-grid').append(el('p', 'empty', data.services.length ? 'Nenhum serviço encontrado. Tente outra busca.' : 'Não há serviços disponíveis para agendamento neste momento.'));
  }

  const AMENITY_ICONS = {"wifi":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.9\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M2 8.8a15.3 15.3 0 0 1 20 0\"/><path d=\"M5.3 12.3a10 10 0 0 1 13.4 0\"/><path d=\"M8.7 15.8a4.7 4.7 0 0 1 6.6 0\"/><circle cx=\"12\" cy=\"19.4\" r=\"1\" fill=\"currentColor\" stroke=\"none\"/></svg>","parking":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.9\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><path d=\"M5.4 11l1.5-4.8A2.2 2.2 0 0 1 9 4.7h6a2.2 2.2 0 0 1 2.1 1.5l1.5 4.8\"/><path d=\"M4.5 11h15a1.5 1.5 0 0 1 1.5 1.5V17H3v-4.5A1.5 1.5 0 0 1 4.5 11Z\"/><path d=\"M5.5 17v2.5h2V17M16.5 17v2.5h2V17\"/><circle cx=\"6.8\" cy=\"14.1\" r=\".9\" fill=\"currentColor\" stroke=\"none\"/><circle cx=\"17.2\" cy=\"14.1\" r=\".9\" fill=\"currentColor\" stroke=\"none\"/></svg>","accessibility":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.85\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><circle cx=\"11\" cy=\"3.7\" r=\"1.4\"/><path d=\"M11 6.6v5.3h5.2l2.7 5.3M11 9.5h4.3\"/><circle cx=\"9.1\" cy=\"16.1\" r=\"4.15\"/></svg>","children":"<svg viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.9\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\"><circle cx=\"12\" cy=\"13.3\" r=\"7.7\"/><path d=\"M7 7.3A3.3 3.3 0 0 1 10 4c1.4 0 2.2.8 2.2 2.1M5 11.6a2.8 2.8 0 0 0 0 4.6M19 11.6a2.8 2.8 0 0 1 0 4.6\"/><path d=\"M9.1 12.6h.01M14.9 12.6h.01M9.5 16a3.6 3.6 0 0 0 5 0\"/></svg>"};
  const AMENITIES = [{"key":"amenity_wifi","title":"Wi-Fi","description":"Wi-Fi disponível para clientes durante a visita.","icon":"wifi"},{"key":"amenity_parking","title":"Estacionamento","description":"O estabelecimento informa oferecer estacionamento. Confirme a disponibilidade de vagas com a equipe.","icon":"parking"},{"key":"amenity_accessibility","title":"Acessibilidade","description":"O estabelecimento informa oferecer acesso acessível. Para necessidades específicas, entre em contato antes da visita.","icon":"accessibility"},{"key":"amenity_children","title":"Atende crianças","description":"O estabelecimento realiza atendimento infantil. Consulte a equipe sobre idades e serviços disponíveis.","icon":"children"}];
  function closeAmenity(restoreFocus = false) {
    const active = document.querySelector('#amenities-grid .amenity-tile[aria-expanded="true"]');
    if (active) active.setAttribute('aria-expanded', 'false');
    $('amenity-detail').hidden = true;
    if (restoreFocus) active?.focus();
  }
  function toggleAmenity(item, button) {
    const wasOpen = button.getAttribute('aria-expanded') === 'true';
    closeAmenity();
    if (wasOpen) return;
    $('amenity-detail-title').textContent = item.title;
    $('amenity-detail-description').textContent = item.description;
    $('amenity-detail').hidden = false;
    button.setAttribute('aria-expanded', 'true');
  }
  function renderAmenities(site) {
    const root = $('amenities-section');
    const grid = $('amenities-grid');
    const enabled = AMENITIES.filter(item => site[item.key] === true);
    grid.dataset.count = String(enabled.length);
    closeAmenity();
    grid.replaceChildren(...enabled.map(item => {
      const tile = el('button', 'amenity-tile');
      tile.type = 'button';
      tile.setAttribute('aria-label', item.title + ': disponível. Ver informações');
      tile.setAttribute('aria-controls', 'amenity-detail');
      tile.setAttribute('aria-expanded', 'false');
      tile.title = item.title;
      tile.innerHTML = AMENITY_ICONS[item.icon]; // Trusted static SVG markup only.
      tile.addEventListener('click', () => toggleAmenity(item, tile));
      return tile;
    }));
    root.hidden = enabled.length === 0;
  }

  function renderPage() {
    const site = data.site;
    document.title = `${site.title} • Agendamento`;
    color(document.documentElement, '--primary', site.theme_color);
    const hex = /^#[0-9a-f]{6}$/i.test(site.theme_color || '') ? site.theme_color.slice(1) : '2e7bf6';
    const luminance = [0, 2, 4].map(i => parseInt(hex.slice(i, i + 2), 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4).reduce((n, v, i) => n + v * [.2126, .7152, .0722][i], 0);
    document.documentElement.style.setProperty('--primary-ink', luminance > .179 ? '#0a1425' : '#ffffff');
    document.querySelector('meta[name=theme-color]').content = `#${hex}`;
    const cover = safeImageURL(site.cover_url); if (cover) { const image = el('img'); image.src = cover; image.alt = ''; image.loading = 'eager'; image.fetchPriority = 'high'; image.decoding = 'async'; image.referrerPolicy = 'no-referrer'; image.style.width = '100%'; image.style.height = '100%'; $('cover').replaceChildren(image); }
    picture($('site-logo'), site.logo_url, initials(site.title), 'hero');
    $('site-title').textContent = site.title; $('footer-title').textContent = site.title;
    $('hero-address').textContent = [siteAddressLine(site), site.city].filter(Boolean).join(' · ');
    $('hero-address').hidden = !$('hero-address').textContent;
    $('about-text').textContent = site.description || '';
    $('about-card').hidden = !site.description;
    renderOpeningHours(data.opening_hours);
    renderLocation(site);
    renderAmenities(site);
    $('services-count').textContent = String(data.services.length);
    $('hero-book').disabled = !data.services.length;
    const cats = ['Todos', ...new Set(data.services.map(s => s.category || 'Serviços'))];
    $('categories').hidden = cats.length <= 2;
    function renderCats() { $('categories').replaceChildren(...cats.map(name => { const node = button(name, 'category', () => { category = name; renderCats(); renderServiceGrid(); }); node.setAttribute('aria-pressed', String(name === category)); return node; })); }
    renderCats(); renderServiceGrid();
    $('team-grid').replaceChildren(...data.professionals.map(person => { const card = el('article', 'team-card'); const portrait = el('div', 'portrait'); color(portrait, '--person-color', person.color); picture(portrait, person.image_url, initials(person.name)); const info = el('div', 'person-info'); info.append(el('strong', '', person.name)); if (person.specialty) info.append(el('p', '', person.specialty)); card.append(portrait, info); return card; }));
    $('equipe').hidden = !data.professionals.length;
    const contacts = [];
    if (/^\+[1-9]\d{9,14}$/.test(site.whatsapp_phone || ''))
      contacts.push(contactAnchor('Conversar no WhatsApp', 'whatsapp', 'https://wa.me/' + site.whatsapp_phone.slice(1)));
    if (/^\+[1-9]\d{9,14}$/.test(site.contact_phone || ''))
      contacts.push(contactAnchor('Ligar para ' + site.contact_phone, 'phone', 'tel:' + site.contact_phone, true));
    for (const [field, name, kind] of [
      ['instagram_url', 'Abrir Instagram', 'instagram'],
      ['tiktok_url', 'Abrir TikTok', 'tiktok'],
      ['facebook_url', 'Abrir Facebook', 'facebook'],
      ['external_url', 'Abrir site da empresa', 'website']
    ]) {
      contacts.push(contactAnchor(name, kind, site[field]));
    }
    $('contact-links').replaceChildren(...contacts.filter(Boolean));
    $('contact-section').hidden = !contacts.some(Boolean);
    $('orion-credit').hidden = !!site.hide_orion_brand; $('preview-banner').hidden = !preview;
    $('site-content').hidden = false; $('page-message').hidden = true;
  }

  function openBooking(service = null) {
    booking = newBooking(); retainedTimeView = null; booking.service = service; booking.services = service ? [service] : []; booking.day = data.today;
    $('booking-shop').textContent = data.site.title; showError(); renderBooking();
    dialog.showModal(); document.body.classList.add('booking-open');
  }
  function closeBooking() { if (booking.busy) return; availabilityController?.abort(); requestSequence++; dialog.close(); document.body.classList.remove('booking-open'); }
  function invalidateAvailability() { availabilityController?.abort(); requestSequence++; booking.quote = null; booking.start = null; booking.availability = null; booking.availabilityError = null; booking.loading = false; booking.consent = false; }
  function toggleService(id) {
    const already = booking.services.includes(id);
    if (!already && booking.services.length >= 6) {
      showError('Você pode escolher até 6 serviços por agendamento.');
      return;
    }
    if (!already) {
      const candidate = data.services.find(s => s.id === id);
      const used = new Set(selectedServices().flatMap(componentIds));
      if (candidate && componentIds(candidate).some(source => used.has(source))) {
        const conflict = selectedServices().find(service =>
          componentIds(service).some(source => componentIds(candidate).includes(source)));
        showError(candidate.catalog_kind === 'combo' ?
          (conflict.catalog_kind === 'combo' ?
            'Estes combos incluem serviços em comum. Remova o combo anterior para trocar.' :
            `Este combo já inclui ${conflict.name}. Remova o serviço avulso para selecionar o combo.`) :
          `${candidate.name} já está incluído no combo ${conflict.name}.`);
        return;
      }
    }
    const keep = already ? booking.services.filter(service => service !== id) : [...booking.services, id];
    const removedExtras = booking.addons.length > 0 && (keep.length > 1 || keep[0] !== booking.service);
    invalidateAvailability();
    booking.services = keep;
    booking.service = keep[0] || null;
    if (removedExtras) booking.addons = [];
    if (!eligibleProfessionals().some(person => person.id === booking.professional))
      booking.professional = null;
    retainedTimeView = null;
    booking.notice = removedExtras ? 'Adicionais anteriores foram removidos ao combinar serviços completos.' : '';
    showError();
    refreshServiceSelection();
  }
  function selectionWarning() {
    if (booking.services.length > 0 && !eligibleProfessionals().length)
      return 'Nenhum profissional realiza todos esses serviços. Retire um dos itens para continuar.';
    return booking.notice;
  }
  function refreshSelectionSummary() {
    const count = $('booking-selected-count'), summary = $('booking-selection-summary'),
          warning = $('booking-selection-warning');
    if (!count || !summary || !warning) return;
    const amount = selectionTotal(), quantity = booking.services.length;
    count.textContent = quantity + (quantity === 1 ? ' serviço selecionado' : ' serviços selecionados');
    summary.replaceChildren();
    if (quantity) {
      summary.append(el('span', '', 'Tempo total: ' + duration(amount.minutes)));
      if (data.site.show_prices) summary.append(el('strong', '', priceText(amount.price, amount.price_mode)));
    } else summary.append(el('span', '', 'Selecione os serviços que deseja realizar no mesmo horário.'));
    warning.textContent = selectionWarning();
    warning.hidden = !warning.textContent;
  }
  function renderSelectedExtras() {
    const container = $('booking-extras');
    if (!container) return;
    container.replaceChildren();
    const service = selectedService();
    if (!service || booking.services.length !== 1 || !service.addons?.length) return;
    const extras = el('section', 'addon-section');
    extras.append(el('h3', '', 'Complemente seu atendimento'),
      el('p', '', 'Adicionais opcionais. O tempo e o valor aparecem no resumo.'));
    for (const addon of service.addons) {
      const label = el('label', 'addon-option'), input = el('input');
      input.type = 'checkbox';
      input.checked = booking.addons.includes(addon.addon_service_id);
      input.addEventListener('change', () => {
        if (input.checked && booking.services.length + booking.addons.length >= 6) {
          input.checked = false; showError('Limite de 6 serviços e adicionais por agendamento.'); return;
        }
        invalidateAvailability();
        booking.addons = input.checked ?
          [...booking.addons, addon.addon_service_id] :
          booking.addons.filter(id => id !== addon.addon_service_id);
        booking.professional = null;
        booking.notice = '';
        showError();
        refreshSelectionSummary();
        updateFooter();
      });
      const copy = el('span', '', addon.name);
      copy.append(el('small', '', addon.add_duration ?
        'Acrescenta ' + duration(addon.duration_min) : 'Realizado dentro do tempo do serviço'));
      label.append(input, copy);
      if (data.site.show_prices) label.append(el('b', '', addon.price_mode === 'from' ?
        priceText(addon.price_cents, 'from') : '+ ' + money(addon.price_cents)));
      extras.append(label);
    }
    container.append(extras);
  }
  function refreshServiceSelection() {
    const list = $('booking-service-list');
    if (!list) return;
    list.querySelectorAll('button[data-service-id]').forEach(card => {
      const selected = booking.services.includes(card.dataset.serviceId);
      card.setAttribute('aria-pressed', String(selected));
      card.querySelector('.service-arrow').textContent = selected ? '✓' : '›';
    });
    renderSelectedExtras(); // The service cards and photo elements stay mounted.
    refreshSelectionSummary();
    updateFooter();
  }

  function renderBooking() {
    const body = $('booking-body');
    body.classList.toggle('booking-confirmation-body', booking.step === 3 && !booking.result);
    $('booking-steps').hidden = !!booking.result;
    for (const step of $('booking-steps').children) {
      const n = Number(step.dataset.step);
      step.classList.toggle('done', n < booking.step);
      if (n === booking.step) step.setAttribute('aria-current', 'step');
      else step.removeAttribute('aria-current');
    }
    if (booking.step === 1 || booking.result) retainedTimeView = null;
    if (!booking.result && booking.step === 2 && retainedTimeView) {
      // Reuse the original people/photo elements when returning from confirmation.
      body.replaceChildren(...retainedTimeView);
      refreshDateChoice();
      renderSlots();
    } else {
      body.replaceChildren();
      if (booking.result) renderSuccess(body);
      else if (booking.step === 1) renderSelection(body);
      else if (booking.step === 2) {
        renderTime(body);
        retainedTimeView = [...body.childNodes];
      } else renderConfirmation(body);
    }
    updateFooter();
  }

  function renderSelection(body) {
    const intro = el('div', 'booking-selection-intro');
    intro.append(el('h3', '', 'Escolha seus serviços'),
      el('p', 'small muted', 'Selecione um ou mais serviços para realizar no mesmo horário.'));
    const count = el('span', 'booking-selected-count'); count.id = 'booking-selected-count';
    intro.append(count);
    const summary = el('div', 'booking-selection-summary'); summary.id = 'booking-selection-summary';
    summary.setAttribute('aria-live', 'polite');
    const warning = el('p', 'booking-selection-warning'); warning.id = 'booking-selection-warning';
    warning.setAttribute('role', 'status');
    const list = el('div', 'services-grid booking-service-list'); list.id = 'booking-service-list';
    list.append(...data.services.map(service => {
      const card = serviceCard(service, () => toggleService(service.id),
        booking.services.includes(service.id));
      card.dataset.serviceId = service.id;
      return card;
    }));
    const extras = el('div'); extras.id = 'booking-extras';
    body.append(intro, summary, warning, list, extras);
    refreshServiceSelection();
  }

  function renderDateButtons(strip, begin, max) {
    const nodes = [];
    for (let i = 0; i < 14; i++) {
      const day = addDays(begin, i);
      if (day > max) break;
      const node = button('', 'day-button', () => selectDay(day));
      node.dataset.day = day;
      node.append(el('span', '', dateLabel(day, { weekday: 'short' }).replace('.', '')),
        el('strong', '', dateLabel(day, { day: '2-digit' })),
        el('span', 'day-month', dateLabel(day, { month: 'short' }).replace('.', '')));
      node.setAttribute('aria-label', dateLabel(day, { dateStyle: 'full' }));
      node.setAttribute('aria-pressed', String(day === booking.day));
      nodes.push(node);
    }
    strip.replaceChildren(...nodes); // Only if date falls outside the existing 14-day window.
  }

  function refreshDateChoice() {
    const heading = $('booking-month-heading'), input = $('booking-day-input'),
          strip = $('booking-day-strip');
    if (!heading || !input || !strip) return;
    const month = dateLabel(booking.day, { month: 'long', year: 'numeric' });
    if (heading.textContent !== month) heading.textContent = month;
    fields.clearError(input);
    if (input.value !== booking.day) input.value = booking.day;
    $('booking-day-value').textContent = fields.dateDisplay(booking.day);
    const visible = [...strip.querySelectorAll('.day-button')];
    if (!visible.some(node => node.dataset.day === booking.day)) {
      const first = booking.day > addDays(data.today, 13) ? booking.day : data.today;
      renderDateButtons(strip, first, input.max);
    }
    strip.querySelectorAll('.day-button').forEach(node => {
      const active = node.dataset.day === booking.day;
      if (node.getAttribute('aria-pressed') !== String(active))
        node.setAttribute('aria-pressed', String(active));
    });
    // Reveal only inside the horizontal strip, without moving the dialog or
    // recreating its photos/slots. This also covers native date input changes.
    const active = strip.querySelector('[aria-pressed=true]');
    if (active) {
      const item = active.getBoundingClientRect(), frame = strip.getBoundingClientRect();
      if (item.left < frame.left || item.right > frame.right)
        strip.scrollLeft += item.left - frame.left - (strip.clientWidth - item.width) / 2;
    }
  }

  function renderTime(body) {
    body.append(el('h3', '', 'Escolha o profissional e o horário'));
    const people = eligibleProfessionals();
    const picker = el('div', 'professional-picker');
    picker.setAttribute('aria-label', 'Profissionais disponíveis');
    for (const person of people) {
      const node = button('', 'professional-choice', () => {
        // Clicking an already selected professional should not refetch or redraw.
        if (person.id === booking.professional) return;
        invalidateAvailability();
        booking.professional = person.id;
        picker.querySelectorAll('.professional-choice').forEach(option => {
          const active = option.dataset.professionalId === person.id;
          if (option.getAttribute('aria-pressed') !== String(active))
            option.setAttribute('aria-pressed', String(active));
        });
        showError();
        updateFooter();
        loadAvailability();
      });
      node.dataset.professionalId = person.id;
      node.setAttribute('aria-pressed', String(person.id === booking.professional));
      node.title = person.name;
      node.setAttribute('aria-label', 'Agendar com ' + person.name);
      const face = el('span', 'mini-portrait professional-photo');
      picture(face, person.image_url, initials(person.name));
      const name = el('span', 'professional-name', person.name);
      node.append(face, name);
      picker.append(node);
    }
    body.append(picker);
    const heading = el('div', 'date-heading');
    heading.id = 'booking-date-heading';
    const title = el('strong');
    title.id = 'booking-month-heading';
    const label = el('label', 'booking-date-field');
    label.id = 'booking-date-field';
    const caption = el('span', '', 'Outra data'); caption.id = 'booking-day-label';
    label.append(caption);
    const control = el('span', 'booking-date-control');
    const value = el('span', 'booking-date-value');
    value.id = 'booking-day-value'; value.setAttribute('aria-hidden', 'true');
    const calendar = el('span', 'booking-date-icon');
    calendar.setAttribute('aria-hidden', 'true');
    calendar.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></svg>';
    const input = el('input');
    input.type = 'date'; input.lang = 'pt-BR'; input.id = 'booking-day-input'; input.dataset.orionField = 'date';
    input.setAttribute('aria-labelledby', caption.id);
    input.dataset.fieldErrorTarget = heading.id;
    input.min = data.today; input.max = addDays(data.today, data.booking_horizon_days);
    input.value = booking.day;
    // Keep the real, accessible native picker over a stable date presentation.
    // iOS's localized date text must not determine this control's dimensions.
    input.addEventListener('input', () => {
      try { value.textContent = fields.dateDisplay(input.value) || 'dd/mm/aaaa'; }
      catch { value.textContent = 'Data inválida'; }
      updateFooter();
    });
    input.addEventListener('keydown', event => {
      if (!['Enter', ' '].includes(event.key) || typeof input.showPicker !== 'function') return;
      try { input.showPicker(); event.preventDefault(); } catch { /* Native input remains usable. */ }
    });
    input.addEventListener('change', () => {
      if (!input.value) { refreshDateChoice(); updateFooter(); return; }
      if (input.value && fields.validate(input) && input.checkValidity()) selectDay(input.value);
      updateFooter();
    });
    control.append(value, calendar, input); label.append(control);
    heading.append(title, label); body.append(heading);
    const strip = el('div', 'date-strip'); strip.id = 'booking-day-strip';
    body.append(strip, el('p', 'timezone-note',
      'Horários locais · ' + data.timezone.replaceAll('_', ' ')));
    body.append(el('h3', 'booking-slots-heading', 'Horários disponíveis'));
    const slots = el('div'); slots.id = 'time-slots';
    slots.setAttribute('aria-live', 'polite');
    body.append(slots);
    refreshDateChoice();
    renderSlots();
  }

  function selectDay(day) {
    if (day === booking.day) {
      refreshDateChoice(); updateFooter();
      return; // Preserve focus/scroll, photo and slot nodes, also after invalid input.
    }
    invalidateAvailability();
    booking.day = day;
    showError();
    refreshDateChoice();
    updateFooter();
    if (preview) renderSlots();
    else loadAvailability();
  }

  function slotStatus(root, text, kind = 'slot-message', retry = false) {
    let status = root.querySelector('[data-slot-status]');
    if (!text) { status?.remove(); return; }
    if (!status) {
      status = el('div');
      status.dataset.slotStatus = 'true';
      root.prepend(status);
    }
    status.className = kind;
    if (status.dataset.message !== text || status.dataset.retry !== String(retry)) {
      status.replaceChildren(el('span', '', text));
      if (retry) status.append(button('Tentar novamente', 'outline', loadAvailability));
      status.dataset.message = text;
      status.dataset.retry = String(retry);
    }
  }

  // The server owns availability. These buckets affect presentation only and
  // must use the business timezone rather than the device's timezone/UTC.
  const slotPeriods = [
    { key: 'morning', label: 'Manhã', spoken: 'manhã' },
    { key: 'afternoon', label: 'Tarde', spoken: 'tarde' },
    { key: 'evening', label: 'Noite', spoken: 'noite' }
  ];
  function groupedAvailableSlots(slots) {
    const unique = new Map();
    for (const start of slots) {
      const date = new Date(start);
      if (Number.isFinite(date.getTime()) && !unique.has(start)) unique.set(start, date);
    }
    const hourFormat = new Intl.DateTimeFormat('en-GB', {
      timeZone: data.timezone, hour: '2-digit', hourCycle: 'h23'
    });
    const groups = slotPeriods.map(period => ({ ...period, starts: [] }));
    [...unique].sort((a, b) => a[1].getTime() - b[1].getTime())
      .forEach(([start, date]) => {
        const hour = Number(hourFormat.format(date));
        groups[hour < 12 ? 0 : hour < 18 ? 1 : 2].starts.push(start);
      });
    return groups.filter(group => group.starts.length);
  }

  function updateSlotSelection(root, start) {
    root.querySelectorAll('.slot[data-slot-start]').forEach(node => {
      const active = node.dataset.slotStart === start;
      if (node.getAttribute('aria-pressed') !== String(active))
        node.setAttribute('aria-pressed', String(active));
    });
    const period = root.querySelector('.slot[aria-pressed="true"]')
      ?.closest('[data-slot-period]')?.dataset.slotPeriod;
    root.querySelectorAll('[data-slot-jump]').forEach(node => {
      if (period && node.dataset.slotJump === period)
        node.setAttribute('aria-current', 'location');
      else node.removeAttribute('aria-current');
    });
  }

  function chooseSlot(start) {
    if (booking.start === start) return;
    booking.start = start;
    booking.consent = false;
    showError();
    const root = $('time-slots');
    if (root) updateSlotSelection(root, start);
    updateFooter(); // Preserve button/photo nodes and focus.
  }

  function renderSlots() {
    const root = $('time-slots');
    if (!root) return;
    let list = root.querySelector('.slots-grid');
    if (preview) {
      root.removeAttribute('aria-busy');
      list?.remove();
      slotStatus(root, 'Esta é uma prévia. Publique o site para disponibilizar a consulta de horários e receber agendamentos de clientes.', 'preview-note');
      return;
    }
    if (booking.loading) {
      root.setAttribute('aria-busy', 'true');
      if (list) {
        list.classList.add('slots-grid-pending');
        list.querySelectorAll('.slot').forEach(node => { node.disabled = true; });
      }
      slotStatus(root, list ? 'Atualizando horários disponíveis…' :
        'Buscando horários disponíveis…');
      return;
    }
    root.removeAttribute('aria-busy');
    if (booking.availabilityError || !booking.availability ||
        !booking.availability.slots?.length) {
      list?.remove();
      const reason = booking.availability?.availability_reason;
      const message = booking.availabilityError ||
        (!booking.availability ? 'Selecione o profissional e a data para consultar a agenda.' :
          reason === 'no_working_hours' ?
            'Este profissional não tem expediente cadastrado neste dia. Escolha outra data ou profissional.' :
            'Não há vagas compatíveis com a duração do serviço, o expediente e os intervalos nesta data. Experimente outro dia ou profissional.');
      slotStatus(root, message, booking.availabilityError ? 'slot-message' :
        booking.availability ? 'empty' : 'slot-message', !!booking.availabilityError);
      return;
    }
    slotStatus(root, null);
    const groups = groupedAvailableSlots(booking.availability.slots);
    const nextTimes = groups.flatMap(group => group.starts);
    if (!list) {
      list = el('div', 'slots-grid');
      root.append(list);
    }
    const current = [...list.querySelectorAll('.slot[data-slot-start]')];
    if (!list.querySelector('.slot-period-nav') ||
        current.length !== nextTimes.length ||
        current.some((node, index) => node.dataset.slotStart !== nextTimes[index])) {
      // Key by server timestamp. Unchanged slots keep their DOM node, handlers,
      // and focus when the date/professional is refreshed.
      const oldNodes = new Map(current.map(node => [node.dataset.slotStart, node]));
      const nav = el('nav', 'slot-period-nav');
      nav.setAttribute('aria-label', 'Ir para os períodos do dia');
      const sections = groups.map(group => {
        const section = el('section', 'slot-period');
        section.dataset.slotPeriod = group.key;
        section.id = 'slot-period-' + group.key;
        section.setAttribute('aria-labelledby', 'slot-period-title-' + group.key);
        const heading = el('div', 'slot-period-heading');
        const title = el('h4', 'slot-period-title', group.label);
        title.id = 'slot-period-title-' + group.key;
        const line = el('span', 'slot-period-line');
        line.setAttribute('aria-hidden', 'true');
        const count = el('span', 'slot-period-count',
          group.starts.length + ' ' + (group.starts.length === 1 ? 'horário' : 'horários'));
        heading.append(title, line, count);
        const grid = el('div', 'slot-period-grid');
        grid.setAttribute('role', 'group');
        grid.setAttribute('aria-label', 'Horários da ' + group.spoken);
        grid.append(...group.starts.map(start => {
          const existing = oldNodes.get(start);
          if (existing) return existing;
          const node = button(slotTime(start), 'slot', () => chooseSlot(start));
          node.dataset.slotStart = start;
          node.setAttribute('aria-pressed', String(start === booking.start));
          return node;
        }));
        section.append(heading, grid);

        const jump = button(group.label, 'slot-period-chip', () => {
          nav.querySelectorAll('[data-slot-jump]').forEach(node =>
            node.removeAttribute('aria-current'));
          jump.setAttribute('aria-current', 'location');
          const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
          section.scrollIntoView({
            behavior: reduced ? 'auto' : 'smooth', block: 'start'
          });
        });
        jump.dataset.slotJump = group.key;
        jump.setAttribute('aria-label', 'Ir para os horários da ' + group.spoken);
        nav.append(jump);
        return section;
      });
      list.replaceChildren(nav, ...sections);
    }
    list.classList.remove('slots-grid-pending');
    list.querySelectorAll('.slot').forEach(node => { node.disabled = false; });
    updateSlotSelection(list, booking.start);
  }

  async function loadAvailability() {
    if (preview || !booking.professional) return;
    const seq = ++requestSequence;
    availabilityController?.abort();
    const controller = new AbortController();
    availabilityController = controller;
    const timeout = setTimeout(() => controller.abort(), 20000);
    booking.loading = true;
    booking.availabilityError = null;
    showError();
    renderSlots(); // Existing slot tiles dim in place instead of flashing.
    updateFooter();
    const params = new URLSearchParams({
      professional_id: booking.professional, day: booking.day
    });
    if (booking.services.length > 1)
      booking.services.forEach(id => params.append('service_ids', id));
    else {
      params.set('service_id', booking.service);
      booking.addons.forEach(id => params.append('addon_ids', id));
    }
    try {
      const result = await api('/availability?' + params, { signal: controller.signal });
      if (seq !== requestSequence) return;
      booking.availability = result;
      booking.availabilityError = null;
      booking.quote = result.quote;
    } catch (error) {
      if (seq !== requestSequence) return;
      booking.availabilityError = error.name === 'AbortError' ?
        'A consulta demorou mais que o esperado. Tente novamente.' :
        (error.message || 'Não foi possível consultar a agenda. Tente novamente.');
      showError();
    } finally {
      clearTimeout(timeout);
      if (seq === requestSequence) {
        booking.loading = false;
        renderSlots();
        updateFooter();
      }
    }
  }

  function reviewCard(result = null) {
    const box = el('div', 'review-card'); box.append(el('h3', '', 'Seu atendimento'));
    const person = result?.professional || data.professionals.find(p => p.id === booking.professional)?.name;
    const services = result?.services || booking.availability?.services ||
      [...selectedServices().map(s => s.name), ...selectedAddons().map(a => a.name)];
    const start = result?.start_at || booking.start;
    const lines = [['Serviços', services.filter(Boolean).join(' + ')], ['Profissional', person], ['Data', new Intl.DateTimeFormat('pt-BR', { dateStyle: 'long', timeZone: data.timezone }).format(new Date(start))], ['Horário', slotTime(start)]];
    const minutes = result?.end_at ?
      Math.round((new Date(result.end_at) - new Date(start)) / 60000) :
      (booking.availability?.duration_min ?? selectionTotal().minutes);
    if (Number.isFinite(minutes) && minutes > 0) {
      lines.push(['Duração', duration(minutes)]);
      lines.push([result ? 'Término' : 'Término previsto',
        slotTime(result?.end_at || new Date(new Date(start).getTime() + minutes * 60000))]);
    }
    if (result?.protocol) lines.push(['Protocolo', result.protocol]);
    for (const [key, value] of lines) { const row = el('div', 'review-row'); row.append(el('span', '', key), el('strong', '', value)); box.append(row); }
    const estimate = booking.availability || selectionTotal();
    const variable = (result?.price_mode || estimate.price_mode) === 'from';
    const total = el('div', 'review-row total');
    total.append(el('span', '', variable?'Valor mínimo previsto':'Valor do atendimento'),
      el('strong', '', priceText(result?.price_cents ?? estimate.price_cents ?? estimate.price,variable?'from':'fixed')));
    box.append(total);
    return box;
  }
  function renderConfirmation(body) {
    // The confirmation button itself is the affirmative booking action.
    // No duplicate consent checkbox, price or long explanatory paragraph.
    body.append(reviewCard());
    body.append(el('h3', 'booking-contact-heading', 'Seus dados'));
    const instructions = el('p', 'small muted booking-form-note',
      'Todos os campos são obrigatórios. O nascimento registra seu aniversário no cadastro do estabelecimento.');
    instructions.id = 'customer-form-note';
    body.append(instructions);
    const form = el('form', 'booking-form');
    form.id = 'customer-form';
    form.setAttribute('aria-describedby', instructions.id);
    form.addEventListener('submit', event => { event.preventDefault(); submitBooking(); });
    const contactFields = [
      { key: 'name', title: 'Nome completo', name: 'customer_name', autocomplete: 'name',
        type: 'text', fieldKind: 'native', max: 120, placeholder: 'Nome completo' },
      { key: 'phone', title: 'WhatsApp com DDD ou DDI', name: 'customer_phone',
        autocomplete: 'tel', type: 'tel', fieldKind: 'phone', placeholder: '(00) 00000-0000 ou +595 9...' },
      { key: 'birthDate', title: 'Data de nascimento', name: 'customer_birth_date',
        autocomplete: 'bday', type: 'date', fieldKind: 'date', placeholder: '' }
    ];
    for (const spec of contactFields) {
      const label = el('label', 'field', spec.title);
      label.dataset.fieldWrapper = '';
      label.htmlFor = spec.name;
      const field = el('input');
      field.id = spec.name;
      field.name = spec.name;
      field.type = spec.type;
      field.autocomplete = spec.autocomplete;
      if (spec.max) field.maxLength = spec.max;
      if (spec.fieldKind) field.dataset.orionField = spec.fieldKind;
      if (spec.key === 'phone') field.dataset.orionInternational = 'true';
      field.required = true;
      if (spec.placeholder) field.placeholder = spec.placeholder;
      field.value = spec.key === 'phone' ? fields.phoneDisplay(booking.phone, { allowInternational: true }) : booking[spec.key];
      if (spec.key === 'phone') {
        field.inputMode = 'tel';
        field.addEventListener('input', () => { booking.phone = field.value; field.setCustomValidity(''); });
        field.addEventListener('change', () => { booking.phone = field.value; });
      } else if (spec.key === 'birthDate') {
        field.lang = 'pt-BR';
        field.min = '1900-01-01';
        field.max = data.today;
        field.addEventListener('input', () => { booking.birthDate = field.value; });
        field.addEventListener('change', () => { booking.birthDate = field.value; });
      } else {
        field.addEventListener('input', () => {
          booking.name = field.value;
          field.setCustomValidity('');
        });
      }
      label.append(field);
      form.append(label);
    }
    body.append(form);
    fields.refresh(form);
  }

  function updateFooter() {
    const next = $('booking-next'), back = $('booking-back'), total = $('booking-total');
    const dateInput = $('booking-day-input');
    const confirmStep = booking.step === 3 && !booking.result;
    $('booking-dialog').querySelector('.booking-footer')
      ?.classList.toggle('booking-footer-confirm', confirmStep);
    total.hidden = confirmStep || !!booking.result;
    back.hidden = booking.step === 1 || !!booking.result;
    back.disabled = booking.busy;
    $('close-booking').disabled = booking.busy;
    total.replaceChildren();
    if (!total.hidden && booking.services.length) {
      const amount = selectionTotal();
      total.append(el('span', '', duration(booking.availability?.duration_min ?? amount.minutes)));
      if (data.site.show_prices) total.append(el('strong', '',
        priceText(booking.availability?.price_cents ?? amount.price,
          booking.availability?.price_mode || amount.price_mode)));
    }
    next.textContent = booking.result ? 'Concluir' :
      booking.busy ? 'Confirmando…' : confirmStep ? 'Confirmar agendamento' : 'Continuar →';
    next.disabled = booking.busy || (!booking.result &&
      (booking.step === 1 ? !booking.services.length || !eligibleProfessionals().length :
       booking.step === 2 ? !booking.start || !booking.quote || booking.loading || preview ||
         !dateInput?.value || !dateInput.validity.valid : preview));
  }

  function bookingWhatsAppURL(result) {
    const value = result?.whatsapp_confirmation_url;
    if (typeof value !== 'string' || !/^https:\/\/wa\.me\/[1-9][0-9]{7,14}\?text=/.test(value)) return null;
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.hostname !== 'wa.me' ||
          url.username || url.password || url.port || url.hash ||
          !/^\/[1-9][0-9]{7,14}$/.test(url.pathname) ||
          url.searchParams.size !== 1 || !url.searchParams.get('text')) return null;
      return url.href;
    } catch { return null; }
  }

  function openBookingWhatsApp(url) {
    // Only a successful reservation may open WhatsApp. A separate tab keeps the
    // Orion receipt visible when the customer returns from the messenger.
    try {
      const tab = window.open('about:blank', '_blank');
      if (tab) {
        try { tab.opener = null; } catch { /* opener is detached when supported */ }
        tab.location.replace(url);
        return;
      }
    } catch { /* A popup blocker must not discard the completed booking. */ }
    try { window.location.assign(url); } catch {
      // Restricted browsers can still use the visible link on the receipt.
    }
  }

  async function submitBooking() {
    if (booking.busy || booking.step !== 3 || preview || !booking.quote || !booking.start) return;
    const form = $('customer-form');
    if (!form || !fields.validateForm(form) || !form.reportValidity()) return;
    // Read the live controls too: password managers/autofill may update without input events.
    booking.name = form.elements.customer_name.value;
    booking.phone = form.elements.customer_phone.value;
    booking.birthDate = form.elements.customer_birth_date.value;
    if (!booking.name.trim()) {
      const nameField = form.elements.customer_name;
      nameField.setCustomValidity('Informe seu nome completo.');
      nameField.reportValidity();
      return;
    }
    const canonicalPhone = fields.phoneValue(booking.phone, { required: true, allowInternational: true });
    if (!/^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$/.test(canonicalPhone)) {
      form.elements.customer_phone.setCustomValidity('Informe +55 com DDD ou +595 com celular paraguaio.');
      form.elements.customer_phone.reportValidity();
      return;
    }
    const birthDate = fields.dateValue(booking.birthDate, { required: true, min: '1900-01-01', max: data.today });
    booking.busy = true;
    showError();
    updateFooter();
    try {
      booking.result = await api('/appointments', {
        method: 'POST',
        body: JSON.stringify({
          quote: booking.quote,
          start_at: booking.start,
          customer_name: booking.name.trim(),
          customer_phone: canonicalPhone,
          customer_birth_date: birthDate,
          confirmed: true
        })
      });
      booking.name = '';
      booking.phone = '';
      booking.birthDate = '';
      booking.quote = null;
      renderBooking();
      const whatsappURL = bookingWhatsAppURL(booking.result);
      if (whatsappURL) openBookingWhatsApp(whatsappURL);
    } catch (error) {
      if ([404, 409, 422].includes(error.status)) {
        invalidateAvailability();
        booking.step = 2;
        renderBooking();
      }
      showError(error.name === 'AbortError' ?
        'A resposta demorou. Tente confirmar novamente com os mesmos dados para recuperar o resultado.' :
        error.message);
    } finally {
      booking.busy = false;
      updateFooter();
    }
  }

  function renderSuccess(body) {
    const success = el('div', 'success'); success.append(el('div', 'success-check', '✓'), el('h3', '', 'Agendamento confirmado!'), el('p', '', 'Seu horário está reservado. Guarde o protocolo e chegue no horário combinado.'), reviewCard(booking.result));
    const whatsappURL = bookingWhatsAppURL(booking.result);
    if (whatsappURL) {
      const wa = el('a', 'pill', 'Abrir WhatsApp novamente');
      wa.href = whatsappURL; wa.target = '_blank'; wa.rel = 'noopener noreferrer';
      success.append(wa);
    } else {
      success.append(el('p', 'small muted',
        'Seu agendamento foi salvo, mas o WhatsApp do Orion está indisponível neste momento. Guarde o protocolo.'));
    }

    success.append(button('Copiar protocolo', 'outline', async()=>{try{await navigator.clipboard.writeText(booking.result.protocol);showError('Protocolo copiado. Seu agendamento está salvo.');}catch{showError('Guarde o protocolo: '+booking.result.protocol);}}));
    success.append(button('Adicionar ao meu calendário', 'outline', downloadCalendar)); body.append(success);
  }
  function downloadCalendar() {
    const result = booking.result; if (!result) return;
    const escape = value => String(value || '').replaceAll('\\', '\\\\').replaceAll('\n', '\\n').replaceAll(';', '\\;').replaceAll(',', '\\,').replaceAll('\r', '');
    const stamp = value => new Date(value).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Orion//Agendamento//PT-BR', 'BEGIN:VEVENT', `UID:${escape(result.protocol)}-${stamp(result.start_at)}@orion.invalid`, `DTSTAMP:${stamp(new Date())}`, `DTSTART:${stamp(result.start_at)}`, `DTEND:${stamp(result.end_at)}`, `SUMMARY:${escape(data.site.title)} — ${escape(result.services.join(' + '))}`, `DESCRIPTION:Protocolo ${escape(result.protocol)}. Profissional: ${escape(result.professional)}.`, `LOCATION:${escape([siteAddressLine(data.site), data.site.city, data.site.state].filter(Boolean).join(', '))}`, 'END:VEVENT', 'END:VCALENDAR'];
    const url = URL.createObjectURL(new Blob([lines.join('\r\n') + '\r\n'], { type: 'text/calendar;charset=utf-8' })); const anchor = el('a'); anchor.href = url; anchor.download = 'agendamento.ics'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  $('amenity-detail-close').addEventListener('click', () => closeAmenity(true));
  document.addEventListener('click', event => {
    if (!$('amenities-section').contains(event.target)) closeAmenity();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('amenity-detail').hidden) {
      event.preventDefault();
      closeAmenity(true);
    }
  });
  $('service-search').addEventListener('input', renderServiceGrid);
  $('hero-book').addEventListener('click', () => openBooking());
  $('footer-book').addEventListener('click', () => openBooking());
  $('close-booking').addEventListener('click', closeBooking);
  dialog.addEventListener('cancel', event => { if (booking.busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { document.body.classList.remove('booking-open'); availabilityController?.abort(); requestSequence++; });
  $('booking-back').addEventListener('click', () => { if (booking.busy) return; booking.step--; showError(); renderBooking(); });
  $('booking-next').addEventListener('click', () => {
    if (booking.result) { closeBooking(); return; }
    if (booking.step === 3) { submitBooking(); return; }
    if (booking.step === 1) {
      const people = eligibleProfessionals();
      if (!booking.services.length || !people.length) return;
      // Back/Continue is navigation, not a new professional choice. A quote
      // and start may only survive while they still belong to this person.
      if (!people.some(person => person.id === booking.professional)) {
        invalidateAvailability();
        booking.professional = people[0].id;
      }
      booking.step = 2; showError(); renderBooking(); loadAvailability();
    }
    else if (booking.start && booking.quote) { booking.step = 3; showError(); renderBooking(); }
  });
  $('share-site').addEventListener('click', async () => {
    const message = $('share-message');
    if (preview) { message.textContent = 'Publique o site para compartilhar o endereço público.'; message.hidden = false; return; }
    try { if (navigator.share) await navigator.share({ title: data.site.title, url: location.href }); else { await navigator.clipboard.writeText(location.href); message.textContent = 'Link copiado.'; message.hidden = false; } }
    catch (error) { if (error.name !== 'AbortError') { message.textContent = 'Copie o endereço do site na barra do navegador para compartilhar.'; message.hidden = false; } }
  });
  api().then(result => { data = result; renderPage();
    const params=new URLSearchParams(location.search),ids=(params.get('servicos')||'').split(',').filter(Boolean);
    if(!preview&&ids.length&&ids.length<=6&&new Set(ids).size===ids.length&&ids.every(id=>data.services.some(s=>s.id===id))){
      openBooking(ids[0]);booking.services=ids;
      const professional=params.get('profissional');if(eligibleProfessionals().some(p=>p.id===professional))booking.professional=professional;
      renderBooking();
    }
  }).catch(error => { const node = $('page-message'); node.replaceChildren(el('h1', '', error.status === 404 ? 'Página indisponível' : 'Não foi possível carregar o site'), el('p', 'muted', error.status === 404 ? 'Este endereço ainda não foi publicado ou deixou de estar disponível.' : error.message)); });
})();
