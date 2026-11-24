/* Customer area: private server sessions only, never cache private history. */
(() => {
  'use strict';
  const slug = location.pathname.match(/^\/s\/([a-z0-9-]+)\/?$/)?.[1];
  const $ = id => document.getElementById(id);
  const trigger = $('customer-area-trigger'), dialog = $('customer-area-dialog');
  if (!slug || !trigger || !dialog) return; // Preview and demo remain untouched.
  const fields = window.OrionFields;
  const root = $('customer-area-body'), tabs = $('customer-area-tabs');
  const notice = $('customer-area-feedback');
  const base = '/api/v1/public/sites/' + encodeURIComponent(slug) + '/account';
  const customerKey = 'orion:public:customer:v1:' + slug;
  let generation = 0, controller = null, dashboard = null;
  let activeTab = 'overview', currentPhone = '', historyStatus = 'all', history = null;
  let historySequence = 0, working = false, loginMethod = 'private_link';
  const moneyFormat = new Intl.NumberFormat('pt-BR', {style:'currency',currency:'BRL'});
  const numberFormat = new Intl.NumberFormat('pt-BR');
  const statusNames = {confirmed:'Agendado',completed:'Concluído',cancelled:'Cancelado',no_show:'Não compareceu'};
  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }
  function button(value, className, action, type = 'button') {
    const node = el('button', className, value); node.type = type;
    if (action) node.addEventListener('click', action);
    return node;
  }
  function money(cents) { return moneyFormat.format(Number.isFinite(Number(cents)) ? Number(cents)/100 : 0); }
  function count(value) { return numberFormat.format(Number(value) || 0); }
  function timeZone(value) {
    try { new Intl.DateTimeFormat('pt-BR',{timeZone:value}).format(); return value; }
    catch { return 'America/Campo_Grande'; }
  }
  function dateTime(value, zone, withTime = true) {
    if (!value) return 'Ainda não registrado';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'Data não informada';
    const opt = {day:'2-digit',month:'short',year:'numeric',timeZone:timeZone(zone || dashboard?.timezone)};
    const day = new Intl.DateTimeFormat('pt-BR',opt).format(date);
    return withTime ? day + ' · ' + new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:opt.timeZone}).format(date) : day;
  }
  function birthDate(value) {
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const [year, month, day] = value.split('-'); return day + '/' + month + '/' + year;
    }
    return 'Não informada';
  }
  function displayPhone(value) {
    try { return fields.phoneDisplay(value || '', {allowInternational:true}); }
    catch { return String(value || ''); }
  }
  function canonicalPhone(value) {
    const result = fields.phoneValue(value,{required:true,allowInternational:true});
    if (!/^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$/.test(result))
      throw Error('Confira o telefone com DDD.');
    return result;
  }
  function rememberedPhone() {
    // This is only the existing opt-in form autofill, NOT an account credential.
    try {
      const item = JSON.parse(localStorage.getItem(customerKey) || 'null');
      if (item?.version === 1 && item.expiresAt > Date.now()) return displayPhone(item.value?.phone);
    } catch { /* No persistent browser storage. */ }
    return '';
  }
  function showNotice(message = '', error = false) {
    notice.textContent = message; notice.hidden = !message;
    notice.classList.toggle('is-error',error);
  }
  function busy(value) {
    working = value;
    root.setAttribute('aria-busy', String(value));
    root.querySelectorAll('button[type="submit"], .customer-area-busy-action')
      .forEach(node => { node.disabled = value; });
  }
  function resetBody() { showNotice(); root.replaceChildren(); }
  function showLoading(label = 'Carregando sua área…') {
    resetBody();
    const box = el('div','customer-area-busy');
    const spinner = el('span','spinner'); spinner.setAttribute('aria-hidden','true');
    box.append(spinner,el('span','',label)); root.append(box);
  }
  async function api(path = '', method = 'GET', payload = undefined) {
    const opts = {
      method, credentials:'same-origin', cache:'no-store', redirect:'error',
      referrerPolicy:'no-referrer', headers:{'Accept':'application/json'},
      signal:controller?.signal
    };
    if (payload !== undefined) {
      opts.headers['Content-Type']='application/json'; opts.body=JSON.stringify(payload);
    }
    const response = await fetch(base + path,opts);
    if (!response.ok) {
      let value = {};
      try { value = await response.json(); } catch {}
      const error = Error(response.status===422 ? 'Confira os dados informados.' :
        typeof value.detail==='string' ? value.detail : 'Não foi possível acessar agora. Tente novamente.');
      error.status = response.status; throw error;
    }
    return response.json();
  }
  function live(epoch) { return epoch === generation && dialog.open && !controller?.signal.aborted; }
  function refreshCompany() {
    const title = $('site-title')?.textContent?.trim() || 'Estabelecimento';
    $('customer-company-name').textContent = title;
    const company = $('customer-company-logo');
    company.replaceChildren();
    const photo = $('site-logo')?.querySelector('img');
    if (photo && photo.getAttribute('src')) {
      const img = el('img'); img.src=photo.src; img.alt=''; img.decoding='async';
      img.addEventListener('error',() => { company.replaceChildren(el('span','',initials(title))); },{once:true});
      company.append(img);
    } else company.textContent = initials(title);
  }
  function initials(name) {
    return String(name || '').trim().split(/\s+/).slice(0,2)
      .map(part => part[0]?.toLocaleUpperCase('pt-BR') || '').join('') || 'C';
  }
  function welcome(title, sub) {
    const div = el('div','customer-area-welcome');
    const avatar = el('span','customer-area-avatar', initials(dashboard?.profile?.name || ''));
    const copy = el('div','customer-area-welcome-copy');
    copy.append(el('h3','',title),el('p','customer-area-muted',sub));
    div.append(avatar,copy); return div;
  }
  function openBooking() {
    const booking = $('hero-book');
    if (!booking || booking.disabled) { showNotice('O catálogo está sendo atualizado. Tente novamente em instantes.',true); return; }
    dialog.close(); booking.click();
  }
  function loginNote() {
    const note = el('div','customer-area-login-note');
    note.append(el('h4','','Ainda não é cliente?'),
      el('p','customer-area-muted',loginMethod === 'birth_date' ? 'Sua área do cliente é criada automaticamente no primeiro agendamento. Não precisa fazer outro cadastro.' : 'Faça seu primeiro agendamento neste estabelecimento para começar. Se já é cliente, use o link privado de um agendamento anterior no primeiro acesso deste aparelho.'),
      button('Agendar horário','customer-area-secondary',openBooking));
    return note;
  }
  function showLogin(phone = currentPhone || rememberedPhone()) {
    dashboard = null; activeTab = 'overview'; history = null; tabs.hidden = true;
    resetBody();
    const heading = el('div','customer-area-section');
    heading.append(el('h3','','Acesse sua área do cliente'),
      el('p','customer-area-muted','Consulte seus agendamentos, histórico e dados cadastrais neste estabelecimento.'));
    const form = el('form','customer-area-form');
    const label = el('label','customer-area-field','Seu telefone');
    const input = el('input'); input.name='phone'; input.type='tel'; input.inputMode='tel';
    input.autocomplete='tel'; input.required=true; input.maxLength=40;
    input.placeholder='(00) 00000-0000'; input.value=displayPhone(phone);
    input.addEventListener('blur',() => { input.value=displayPhone(input.value); });
    label.append(input);
    form.append(label);
    let birthInput = null, check = null;
    if (loginMethod === 'birth_date') {
      const birthLabel=el('label','customer-area-field','Data de nascimento');
      birthInput=el('input'); birthInput.type='date'; birthInput.name='birth_date';
      birthInput.autocomplete='bday'; birthInput.lang='pt-BR'; birthInput.required=true;
      birthInput.min='1900-01-01'; birthInput.max=new Date().toISOString().slice(0,10);
      birthLabel.append(birthInput); form.append(birthLabel);
      const keep=el('label','customer-area-check');
      check=el('input'); check.type='checkbox'; check.name='remember'; check.checked=true;
      keep.append(check,el('span','','Manter conectado neste aparelho por 30 dias.'));
      form.append(keep);
    }
    form.append(button(loginMethod === 'birth_date' ? 'Acessar minha conta' : 'Continuar','customer-area-button',null,'submit'));
    form.addEventListener('submit',async event => {
      event.preventDefault(); if (working) return;
      showNotice();
      try { currentPhone=canonicalPhone(input.value); }
      catch (error) { showNotice(error.message,true); input.focus(); return; }
      const epoch = generation; busy(true);
      try {
        const payload={phone:currentPhone};
        if (birthInput) {
          payload.birth_date=fields.dateValue(birthInput.value,{required:true,min:'1900-01-01',max:birthInput.max});
          payload.remember=check.checked;
        }
        const result = await api('/enter','POST',payload);
        if (!live(epoch)) return;
        if (result.authenticated) acceptDashboard(result);
        else if (result.birth_date_required) { loginMethod='birth_date'; showLogin(currentPhone); }
        else if (loginMethod === 'private_link') showLink();
        else showNotice('Confira seu telefone e data de nascimento cadastrados.',true);
      } catch (error) { if (live(epoch) && error.name!=='AbortError') showNotice(error.message,true); }
      finally { if (live(epoch)) busy(false); }
    });
    root.append(heading,form,el('hr','customer-area-divider'),loginNote());
  }
  function showLink() {
    resetBody(); tabs.hidden=true;
    const heading=el('div','customer-area-section');
    heading.append(el('h3','','Primeiro acesso neste aparelho'),
      el('p','customer-area-muted','Para proteger seu histórico, use o link privado de um agendamento que você já recebeu. Não enviamos SMS, mensagens, e-mail ou códigos.'));
    const form=el('form','customer-area-form');
    const phoneLabel=el('label','customer-area-field','Telefone informado');
    const phone=el('input'); phone.type='tel'; phone.value=displayPhone(currentPhone); phone.readOnly=true; phoneLabel.append(phone);
    const label=el('label','customer-area-field','Link privado do agendamento');
    const link=el('input'); link.name='booking_link'; link.type='text'; link.inputMode='url';
    link.placeholder='Cole aqui o seu link de agendamento'; link.autocomplete='off';
    link.spellcheck=false; link.autocapitalize='off'; link.required=true; link.maxLength=650;
    label.append(link);
    const keep=el('label','customer-area-check');
    const check=el('input'); check.name='remember'; check.type='checkbox'; check.checked=true;
    keep.append(check,el('span','','Manter conectado neste aparelho por 30 dias. Desmarque se o aparelho for compartilhado.'));
    form.append(phoneLabel,label,keep,button('Acessar minha conta','customer-area-button',null,'submit'));
    form.addEventListener('submit',async event => {
      event.preventDefault(); if (working) return; showNotice();
      const epoch=generation; busy(true);
      try {
        const result=await api('/link-login','POST',{
          phone:currentPhone,booking_link:link.value.trim(),remember:check.checked
        });
        link.value='';
        if (!live(epoch)) return;
        if (result.authenticated) acceptDashboard(result);
        else showNotice('Não foi possível validar esse acesso.',true);
      } catch (error) { if (live(epoch) && error.name!=='AbortError') showNotice(error.message,true); }
      finally { if (live(epoch)) busy(false); }
    });
    root.append(heading,form,button('Voltar e trocar telefone','customer-area-secondary',() => showLogin(currentPhone)),
      el('hr','customer-area-divider'),loginNote());
  }
  function stat(label,value,note) {
    const item=el('div','customer-area-stat');
    item.append(el('span','customer-area-stat-label',label),el('strong','customer-area-stat-value',value));
    if (note) item.append(el('span','customer-area-stat-note',note)); return item;
  }
  function appointmentCard(item) {
    const card=el('article','customer-area-card');
    const head=el('div','customer-area-card-head');
    const status=el('span','customer-area-status',statusNames[item.status] || 'Atendimento');
    status.dataset.status=item.status || '';
    const service=Array.isArray(item.services) ? item.services.filter(value=>typeof value==='string').join(' + ') : '';
    head.append(el('strong','',service || 'Atendimento'),status); card.append(head);
    const meta=el('div','customer-area-card-meta');
    meta.append(el('span','',dateTime(item.start_at,item.timezone)));
    if (item.professional) meta.append(el('span','','· '+item.professional));
    if (Number.isFinite(item.duration_min) && item.duration_min>0) meta.append(el('span','','· '+count(item.duration_min)+' min'));
    card.append(meta);
    if (Number.isFinite(item.price_cents)) {
      card.append(el('span','customer-area-card-price',(item.price_mode==='from' && item.status!=='completed' ? 'A partir de ' : '')+money(item.price_cents)));
    }
    return card;
  }
  function empty(title,detail) {
    const box=el('div','customer-area-empty'); box.append(el('strong','',title),el('p','customer-area-muted',detail)); return box;
  }
  function renderOverview() {
    resetBody();
    const profile=dashboard.profile || {}, stats=dashboard.stats || {};
    const first=String(profile.name || '').trim().split(/\s+/)[0];
    root.append(welcome(first ? 'Olá, '+first+'!' : 'Bem-vindo!','Seu histórico neste estabelecimento.'));
    const statsGrid=el('div','customer-area-stats');
    statsGrid.append(
      stat('Total em serviços realizados',money(stats.completed_cents),'Soma dos atendimentos concluídos. Não comprova pagamentos.'),
      stat('Agendamentos',count(stats.appointments)),
      stat('Concluídos',count(stats.completed)),
      stat('Cancelados',count(stats.cancelled)),
      stat('Última visita',stats.last_visit?dateTime(stats.last_visit,dashboard.timezone,false):'—')
    );
    root.append(statsGrid);
    const section=el('section','customer-area-section');
    const sectionTitle=el('div','customer-area-section-title');
    sectionTitle.append(el('h4','','Próximos agendamentos'));
    section.append(sectionTitle);
    const items=dashboard.upcoming?.items || [];
    if (items.length) items.forEach(item=>section.append(appointmentCard(item)));
    else section.append(empty('Nenhum horário marcado','Escolha um serviço e agende seu próximo atendimento.'));
    if (dashboard.upcoming?.has_more) section.append(button('Ver todos os agendamentos','customer-area-secondary',()=>setTab('history','all')));
    root.append(section,button('Agendar novo horário','customer-area-button',openBooking));
  }
  function renderProfile() {
    resetBody();
    const profile=dashboard.profile || {};
    root.append(welcome('Seus dados','Dados cadastrados neste estabelecimento.'));
    const list=el('dl','customer-area-details');
    for (const [label,value] of [
      ['Nome completo',profile.name || 'Não informado'],
      ['Telefone',displayPhone(profile.phone) || 'Não informado'],
      ['Data de nascimento',birthDate(profile.birth_date)]
    ]) {
      const item=el('div','customer-area-detail'); item.append(el('dt','',label),el('dd','',value)); list.append(item);
    }
    root.append(list,el('p','customer-area-muted','Para corrigir seus dados cadastrais, entre em contato com o estabelecimento.'));
    const logout=button('Sair da conta','customer-area-secondary customer-area-busy-action',async () => {
      if (working) return; const epoch=generation; busy(true); showNotice();
      try {
        await api('/logout','POST',{});
        if (!live(epoch)) return;
        try { localStorage.removeItem(customerKey); } catch {}
        currentPhone=''; showLogin('');
      } catch(error) { if (live(epoch) && error.name!=='AbortError') showNotice(error.message,true); }
      finally { if (live(epoch)) busy(false); }
    });
    root.append(el('hr','customer-area-divider'),logout,el('p','customer-area-muted','Ao sair, sua sessão e os dados de formulário lembrados neste aparelho serão removidos.'));
  }
  function renderHistory() {
    resetBody();
    const title=el('div','customer-area-section');
    title.append(el('h3','','Seus agendamentos'),el('p','customer-area-muted','Acompanhe horários marcados e atendimentos anteriores.'));
    const filters=el('div','customer-area-filters');
    const list=[['all','Todos'],['confirmed','Agendados'],['completed','Concluídos'],['cancelled','Cancelados'],['no_show','Não compareceu']];
    list.forEach(([id,label])=>{
      const filter=button(label,'',()=>{historyStatus=id;history=null;loadHistory(false);});
      filter.setAttribute('aria-pressed',String(historyStatus===id)); filters.append(filter);
    });
    root.append(title,filters);
    if (!history) { root.append(el('p','customer-area-muted','Carregando agendamentos…')); return; }
    if (!history.items.length) root.append(empty('Nenhum agendamento encontrado','Seus próximos atendimentos aparecerão aqui.'));
    else history.items.forEach(item=>root.append(appointmentCard(item)));
    if (history.has_more) root.append(button('Carregar mais','customer-area-secondary customer-area-busy-action',()=>loadHistory(true)));
  }
  async function loadHistory(append) {
    const sequence=++historySequence, epoch=generation, filter=historyStatus;
    const previous=append ? history : null;
    if (!append) renderHistory();
    busy(true);
    try {
      const offset=previous?.next_offset || 0;
      const query=new URLSearchParams({kind:'all',status:filter,offset:String(offset)});
      const result=await api('/appointments?'+query);
      if (!live(epoch) || activeTab!=='history' || sequence!==historySequence) return;
      history={...result,items:[...(previous?.items || []),...(Array.isArray(result.items)?result.items:[])]};
      renderHistory();
    } catch (error) {
      if (!live(epoch) || activeTab!=='history' || sequence!==historySequence || error.name==='AbortError') return;
      if (error.status===401) { showLogin('');showNotice('Sua sessão expirou. Acesse novamente.');return; }
      if (!history) { history={items:[],has_more:false}; renderHistory(); }
      showNotice(error.message,true);
    } finally {
      if (live(epoch) && sequence===historySequence) busy(false);
    }
  }
  function setTab(tab,filter) {
    if (!dashboard || !['overview','history','profile'].includes(tab)) return;
    activeTab=tab; historySequence++; working=false;
    root.setAttribute('aria-busy','false');
    tabs.querySelectorAll('button').forEach(node => {
      if (node.dataset.customerTab===tab) node.setAttribute('aria-current','page');
      else node.removeAttribute('aria-current');
    });
    if (tab==='overview') renderOverview();
    else if (tab==='profile') renderProfile();
    else { historyStatus=filter || 'all'; history=null; loadHistory(false); }
  }
  function acceptDashboard(result) {
    if (!result || result.authenticated!==true || !result.profile || !result.stats) throw Error('Não foi possível carregar sua área.');
    dashboard=result; tabs.hidden=false; currentPhone=''; setTab('overview');
  }
  function cleanup() {
    generation++; historySequence++; controller?.abort(); controller=null; working=false;
    dashboard=null; history=null; currentPhone=''; activeTab='overview';
    tabs.hidden=true; root.replaceChildren(); showNotice(); root.setAttribute('aria-busy','false');
    document.body.classList.remove('customer-area-open');
  }
  async function open() {
    if (dialog.open || trigger.hidden) return;
    cleanup(); controller=new AbortController(); const epoch=generation;
    refreshCompany(); showLoading();
    dialog.showModal(); document.body.classList.add('customer-area-open');
    await loadAccount(epoch);
  }
  async function loadAccount(epoch) {
    showLoading();
    try {
      const state=await api();
      if (!live(epoch)) return;
      if (state.authenticated) acceptDashboard(state);
      else if (state.available===false) {
        resetBody(); root.append(empty('Área do cliente indisponível','Tente novamente mais tarde.'),button('Agendar horário','customer-area-secondary',openBooking));
      } else { loginMethod=state.login_method==='birth_date'?'birth_date':'private_link'; showLogin(); }
    } catch(error) {
      if (live(epoch) && error.name!=='AbortError') {
        resetBody(); root.append(empty('Não foi possível carregar','Verifique sua conexão e tente novamente.'),
          button('Tentar novamente','customer-area-button',()=>{controller?.abort();controller=new AbortController();generation++;loadAccount(generation);}));
      }
    }
  }
  trigger.addEventListener('click',open);
  $('customer-area-close').addEventListener('click',()=>dialog.close());
  tabs.addEventListener('click',event=>{
    const item=event.target.closest('button[data-customer-tab]');
    if (item) setTab(item.dataset.customerTab);
  });
  dialog.addEventListener('close',cleanup);
  // Do not retain private HTML or input values in browser back/forward snapshots.
  window.addEventListener('pagehide',()=>{
    if (dialog.open) dialog.close();
    cleanup();
  });
})();
