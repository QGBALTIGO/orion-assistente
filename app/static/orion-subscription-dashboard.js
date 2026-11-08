/* Orion · Visão geral do nicho de serviços por assinatura.
 * Fonte exclusiva: APIs autenticadas, somente leitura, da empresa ativa.
 * Nenhum valor sintético, acesso entre empresas ou envio é feito por esta tela.
 */
(function () {
  'use strict';
  const routes = Object.freeze({
    devices: 'dispositivos', chatbot: 'chatbot', messages: 'mensagens-enviadas',
    schedules: 'programadas', campaigns: 'campanhas', templates: 'templates',
    direct: 'enviar-mensagem', diagnostics: 'diagnosticos'
  });
  const icons = Object.freeze({
    dashboard: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    phone: 'M6 2h12v20H6zM10 5h4m-3 14h2',
    bot: 'M12 3v4M8 7h8a5 5 0 0 1 5 5v4a5 5 0 0 1-5 5H9l-4 2v-4a5 5 0 0 1-2-4v-3a5 5 0 0 1 5-5ZM9 13h.01M15 13h.01',
    send: 'm22 2-7 20-4-9-9-4 20-7Z',
    clock: 'M8 3v3m8-3v3M3 9h18M5 5h14a2 2 0 0 1 2 2v6M3 7v12a2 2 0 0 0 2 2h7M18 15a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0 2v2l1.5 1',
    activity: 'M2 12h4l3-8 5 16 3-8h5',
    refresh: 'M20 11a8 8 0 0 0-15.5-2M4 4v5h5M4 13a8 8 0 0 0 15.5 2M20 20v-5h-5',
    alert: 'M12 9v4m0 4h.01M10 3h4l9 16H1L10 3Z',
    check: 'm4 12 5 5L20 6',
    arrow: 'M5 12h14m-5-5 5 5-5 5',
    layers: 'm12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 17l9 5 9-5',
    chart: 'M3 3v18h18M7 15l5-5 3 3 6-7',
    megaphone: 'M3 10v4h4l13 5V5L7 10H3Zm4 4 2 7h3',
    info: 'M12 11v6m0-10h.01M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20'
  });
  const svg = name => '<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="' + (icons[name] || icons.info) + '"/></svg>';
  const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, chr => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[chr]);
  const integer = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const format = value => value == null ? '—' : new Intl.NumberFormat('pt-BR').format(value);
  const responseObject = value => !!value && typeof value === 'object' && !Array.isArray(value);
  const validate = (kind, data) => {
    if (!responseObject(data)) throw Error('Resposta indisponível');
    if (kind === 'devices' && (!Array.isArray(data.items) || !responseObject(data.usage) ||
      integer(data.usage.used) === null || integer(data.usage.limit) === null)) throw Error('Dispositivos indisponíveis');
    if (kind === 'chatbot' && (!responseObject(data.counts) || integer(data.counts.active) === null)) throw Error('Respostas indisponíveis');
    if (kind === 'messages' && (!Array.isArray(data.items) || !responseObject(data.counts) ||
      integer(data.counts.today) === null || integer(data.counts.last_30_days) === null ||
      data.counts.scope !== 'message_attempts')) throw Error('Envios indisponíveis');
    if (kind === 'schedules' && (!Array.isArray(data.items) || !responseObject(data.counts) ||
      integer(data.counts.active) === null || integer(data.counts.attention) === null)) throw Error('Programações indisponíveis');
    if (kind === 'campaigns' && (!Array.isArray(data.items) || integer(data.total) === null)) throw Error('Campanhas indisponíveis');
    return data;
  };
  const blank = () => ({});
  let current = null;

  function allowed(view, key) {
    return view.allowed && view.allowed[key] === true;
  }
  function link(view, key, label, symbol, kind) {
    if (!allowed(view, key)) return '';
    return '<button type="button" class="os-button ' + (kind || 'os-outline') +
      '" data-os-route="' + routes[key] + '">' + (symbol ? svg(symbol) : '') + esc(label) + '</button>';
  }
  function ready(view, key) {
    return responseObject(view.data[key]) && !view.errors[key];
  }
  function statistic(view, key, title, symbol, value, helper, extra) {
    return '<article class="os-card os-subdash-stat"><div class="os-subdash-stat-header">' +
      '<span class="os-square-icon">' + svg(symbol) + '</span><span>' + esc(title) + '</span></div>' +
      '<strong class="os-subdash-stat-number" aria-label="' + esc(title + ': ' + value) + '">' + esc(value) + '</strong>' +
      '<p>' + esc(helper) + '</p>' + (extra || '') + '</article>';
  }
  function shortTime(value, zone) {
    if (typeof value !== 'string') return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    try {
      return new Intl.DateTimeFormat('pt-BR', {timeZone: zone || 'UTC', day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit'}).format(date);
    } catch { return ''; }
  }
  function statusLabel(status) {
    return {
      sent: 'Enviada', pending: 'Pendente', failed: 'Falhou', uncertain: 'Incerto'
    }[status] || 'Em análise';
  }
  function pill(text, tone) {
    return '<span class="os-badge os-' + (tone || 'neutral') + '">' + esc(text) + '</span>';
  }
  function panelHeading(icon, title, subtitle) {
    return '<div class="os-subdash-panel-heading"><span class="os-square-icon">' + svg(icon) +
      '</span><div><h2>' + esc(title) + '</h2><p>' + esc(subtitle) + '</p></div></div>';
  }
  function healthLine(view, symbol, title, note, badgeText, tone, route) {
    return '<div class="os-subdash-health-row"><span class="os-subdash-health-icon">' + svg(symbol) +
      '</span><div class="os-subdash-health-main"><strong>' + esc(title) +
      '</strong><small>' + esc(note) + '</small></div><div class="os-subdash-health-side">' +
      pill(badgeText, tone) + (route && allowed(view, route) ? '<button type="button" data-os-route="' +
      routes[route] + '" aria-label="Abrir ' + esc(title) + '">' + svg('arrow') + '</button>' : '') + '</div></div>';
  }
  function health(view) {
    const device = ready(view, 'devices') ? view.data.devices.items[0] : null;
    const active = ready(view, 'chatbot') ? view.data.chatbot.counts.active : null;
    const attention = ready(view, 'schedules') ? view.data.schedules.counts.attention : null;
    const campaigns = ready(view, 'campaigns') ? view.data.campaigns.total : null;
    const list = [
      !allowed(view, 'devices') ? healthLine(view, 'phone', 'WhatsApp', 'Consulta não autorizada nesta conta.', 'Sem acesso', 'neutral', null) :
      !ready(view, 'devices') ? healthLine(view, 'phone', 'WhatsApp', 'Não foi possível verificar a conexão.', 'Indisponível', 'warning', 'devices') :
      !device ? healthLine(view, 'phone', 'WhatsApp', 'Conecte um número para iniciar os atendimentos.', 'Não configurado', 'warning', 'devices') :
      device.status === 'connected' ? healthLine(view, 'phone', 'WhatsApp', esc(device.name || 'Dispositivo') + ' conectado.', 'Conectado', 'success', 'devices') :
      healthLine(view, 'phone', 'WhatsApp', esc(device.name || 'Dispositivo') + ': revise a conexão.', 'Atenção', 'warning', 'devices'),
      !allowed(view, 'chatbot') ? '' : active === null ?
        healthLine(view, 'bot', 'Chatbot', 'Não foi possível consultar as respostas.', 'Indisponível', 'warning', 'chatbot') :
        healthLine(view, 'bot', 'Chatbot', active ? format(active) + ' resposta(s) ativa(s).' : 'Configure sua primeira resposta automática.',
          active ? 'Configurado' : 'Pendente', active ? 'success' : 'warning', 'chatbot'),
      !allowed(view, 'schedules') ? '' : attention === null ?
        healthLine(view, 'clock', 'Programações', 'Situação não verificada.', 'Indisponível', 'warning', 'schedules') :
        healthLine(view, 'clock', 'Programações', attention ?
          format(attention) + ' programação(ões) precisam de revisão.' : 'Nenhuma programação sinalizada para revisão.',
          attention ? 'Revisar' : 'Em dia', attention ? 'warning' : 'success', 'schedules'),
      !allowed(view, 'campaigns') ? '' : campaigns === null ?
        healthLine(view, 'megaphone', 'Campanhas em execução', 'Consulta indisponível.', 'Indisponível', 'warning', 'campaigns') :
        healthLine(view, 'megaphone', 'Campanhas em execução', format(campaigns) + ' campanha(s) em execução.',
          campaigns ? 'Ativas' : 'Nenhuma', campaigns ? 'success' : 'neutral', 'campaigns')
    ].filter(Boolean).join('');
    return '<section class="os-card os-subdash-panel" aria-labelledby="osSubdashHealthTitle">' +
      panelHeading('activity', 'Saúde da operação', 'O que precisa da sua atenção agora.') +
      '<div class="os-subdash-health-list">' + list + '</div></section>';
  }
  function activity(view) {
    if (!allowed(view, 'messages')) return '<section class="os-card os-subdash-panel">' +
      panelHeading('send', 'Atividade recente', 'O histórico de mensagens não está disponível para esta conta.') +
      '<p class="os-subdash-empty">Sem permissão para visualizar as tentativas de envio.</p></section>';
    if (!ready(view, 'messages')) return '<section class="os-card os-subdash-panel">' +
      panelHeading('send', 'Atividade recente', 'Últimas tentativas de mensagens registradas.') +
      '<div class="os-subdash-empty" role="status">Não foi possível consultar o histórico. Use Atualizar para tentar novamente.</div></section>';
    const data = view.data.messages, items = data.items.slice(0, 5);
    const rows = items.map(message => {
      const status = message.status;
      const tone = status === 'sent' ? 'success' : status === 'failed' || status === 'uncertain' ? 'warning' : 'neutral';
      const origin = message.origin && message.origin.label || 'Mensagem';
      const device = message.device && message.device.name || 'WhatsApp';
      const date = shortTime(message.created_at, data.timezone);
      return '<li class="os-subdash-activity-item"><span class="os-subdash-activity-icon">' + svg('send') +
        '</span><span class="os-subdash-activity-copy"><strong>' + esc(origin) +
        '</strong><small>' + esc(device + (date ? ' · ' + date : '')) +
        '</small></span>' + pill(statusLabel(status), tone) + '</li>';
    }).join('');
    return '<section class="os-card os-subdash-panel" aria-labelledby="osSubdashActivityTitle">' +
      panelHeading('send', 'Atividade recente', 'Últimas tentativas de mensagem, não confirmações de entrega.') +
      (items.length ? '<ol class="os-subdash-activity-list">' + rows + '</ol>' :
        '<div class="os-subdash-empty">Nenhuma tentativa de envio registrada até agora.</div>') +
      '<div class="os-subdash-panel-footer">' +
      link(view, 'messages', 'Ver histórico', 'arrow', 'os-ghost') + '</div></section>';
  }
  function quickActions(view) {
    const entries = [
      ['devices', 'phone', 'Dispositivos', 'Conexão e configuração'],
      ['chatbot', 'bot', 'Chatbot', 'Respostas automáticas'],
      ['templates', 'layers', 'Templates', 'Mensagens prontas'],
      ['direct', 'send', 'Enviar mensagem', 'Envio individual'],
      ['campaigns', 'megaphone', 'Campanhas', 'Acompanhar campanhas'],
      ['diagnostics', 'activity', 'Diagnósticos', 'Verificar a conexão']
    ].filter(([key]) => allowed(view, key));
    if (!entries.length) return '';
    return '<section class="os-subdash-actions" aria-label="Acessos rápidos">' +
      '<div class="os-subdash-section-title"><h2>Acessos rápidos</h2><p>Vá direto às ferramentas do seu atendimento.</p></div>' +
      '<div class="os-subdash-action-grid">' + entries.map(([key, symbol, title, desc]) =>
        '<button type="button" class="os-subdash-action os-card" data-os-route="' + routes[key] + '">' +
        '<span class="os-square-icon">' + svg(symbol) + '</span><span class="os-subdash-action-copy"><strong>' +
        esc(title) + '</strong><small>' + esc(desc) + '</small></span>' + svg('arrow') + '</button>').join('') +
      '</div></section>';
  }
  function render(view) {
    if (!view || !view.host || !view.host.isConnected) return;
    const hadFocus = view.host.contains(document.activeElement) ?
      {action: document.activeElement.getAttribute('data-sd-action'),
        route: document.activeElement.getAttribute('data-os-route'),
        period: document.activeElement.getAttribute('data-sd-period')} : null;
    const devices = ready(view, 'devices') ? view.data.devices : null;
    const connected = devices ? devices.items.filter(item => item.status === 'connected').length : null;
    const total = devices ? devices.usage.limit : null;
    const chatbot = ready(view, 'chatbot') ? view.data.chatbot : null;
    const messages = ready(view, 'messages') ? view.data.messages : null;
    const schedules = ready(view, 'schedules') ? view.data.schedules : null;
    const value = messages ? messages.counts[view.period === '30' ? 'last_30_days' : 'today'] : null;
    const periodHTML = allowed(view, 'messages') ?
      '<div class="os-subdash-period" role="group" aria-label="Período das tentativas de envio">' +
      [['today', 'Hoje'], ['30', '30 dias']].map(([key, label]) =>
        '<button type="button" data-sd-period="' + key + '" aria-pressed="' + (view.period === key) +
        '">' + label + '</button>').join('') + '</div>' : '';
    view.host.innerHTML = '<div class="os-subdash"><div class="os-subdash-head">' +
      '<div><p class="os-subdash-overline">VISÃO GERAL</p><h1 id="osSubdashTitle">Início</h1>' +
      '<p>Seus canais, automações e envios em um só lugar.</p></div>' +
      '<button class="os-button os-outline os-subdash-refresh" type="button" data-sd-action="refresh"' +
      (view.loading ? ' disabled' : '') + '>' + svg('refresh') + (view.loading ? 'Atualizando…' : 'Atualizar') + '</button></div>' +
      '<div class="os-subdash-stats" aria-label="Indicadores do atendimento">' +
      statistic(view, 'devices', 'WhatsApp conectado', 'phone',
        connected == null ? '—' : format(connected) + ' de ' + format(total),
        connected == null ? 'Não foi possível verificar os dispositivos.' :
          connected ? 'Canal disponível no momento da consulta.' : 'Nenhum dispositivo conectado.') +
      statistic(view, 'chatbot', 'Respostas ativas', 'bot',
        chatbot ? format(chatbot.counts.active) : '—',
        chatbot ? 'Respostas automáticas habilitadas.' : 'Não foi possível consultar as respostas.') +
      statistic(view, 'messages', 'Tentativas de envio', 'send',
        format(value), messages ? (view.period === '30' ? 'Nos últimos 30 dias, incluindo hoje.' : 'Registradas hoje.') :
          'Consulta indisponível.', periodHTML) +
      statistic(view, 'schedules', 'Programações ativas', 'clock',
        schedules ? format(schedules.counts.active) : '—',
        schedules ? 'Envios agendados em andamento ou a executar.' : 'Consulta indisponível.') +
      '</div><p class="os-subdash-footnote">' + svg('info') +
      'Os números representam registros da sua empresa. Tentativas de envio não significam mensagens entregues.</p>' +
      '<div class="os-subdash-panels">' + health(view) + activity(view) + '</div>' + quickActions(view) +
      '<span class="os-subdash-sr-only" role="status" aria-live="polite">' +
      (view.loading ? 'Atualizando visão geral.' : 'Visão geral atualizada.') + '</span></div>';
    view.host.setAttribute('aria-busy', String(!!view.loading && !view.loaded));
    if (hadFocus) {
      const attr = hadFocus.action ? 'data-sd-action' : hadFocus.period ? 'data-sd-period' : 'data-os-route';
      const key = hadFocus.action || hadFocus.period || hadFocus.route;
      const target = Array.from(view.host.querySelectorAll('[' + attr + ']')).find(node => node.getAttribute(attr) === key);
      target && target.focus({preventScroll: true});
    }
  }
  async function refresh(view) {
    if (!view || view.loading) return;
    const epoch = ++view.epoch;
    view.loading = true;
    if (view.loaded) render(view);
    const path = '/api/v1/tenants/' + encodeURIComponent(view.tenantId) + '/subscription-whatsapp/';
    const specs = [
      ['devices', 'devices', 'devices'],
      ['chatbot', 'chatbot?per_page=1', 'chatbot'],
      ['messages', 'sent-messages?page=1', 'messages'],
      ['schedules', 'scheduled-messages?tab=active&page=1', 'schedules'],
      ['campaigns', 'campaigns?status=running&page=1', 'campaigns']
    ].filter(([key]) => allowed(view, key));
    const outcomes = await Promise.allSettled(specs.map(async ([key, endpoint]) =>
      validate(key, await view.request(path + endpoint))));
    if (current !== view || view.epoch !== epoch) return;
    specs.forEach(([key], index) => {
      const result = outcomes[index];
      if (result.status === 'fulfilled') {
        view.data[key] = result.value;
        delete view.errors[key];
      } else {
        delete view.data[key];
        view.errors[key] = result.reason && result.reason.message || 'Consulta indisponível';
      }
    });
    view.loaded = true;
    view.loading = false;
    render(view);
  }
  function onClick(view, event) {
    const period = event.target.closest('[data-sd-period]');
    if (period && view.host.contains(period)) {
      const next = period.dataset.sdPeriod;
      if (next === 'today' || next === '30') {
        view.period = next;
        render(view);
      }
      return;
    }
    const button = event.target.closest('[data-sd-action=refresh]');
    if (button && view.host.contains(button)) void refresh(view);
  }
  async function mount(options) {
    if (!options || !options.host || typeof options.request !== 'function' || !options.tenantId) {
      throw Error('Não foi possível confirmar o contexto da visão geral.');
    }
    if (current && current.host === options.host && current.tenantId === options.tenantId) {
      current.allowed = {...options.allowed};
      current.request = options.request;
      return refresh(current);
    }
    unmount();
    const view = {host: options.host, tenantId: options.tenantId, allowed: {...options.allowed},
      request: options.request, period: 'today', data: blank(), errors: blank(), epoch: 0,
      loaded: false, loading: false};
    current = view;
    view.listener = event => onClick(view, event);
    view.host.addEventListener('click', view.listener);
    view.host.innerHTML = '<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>Consultando indicadores da sua empresa…</div>';
    await refresh(view);
  }
  function unmount() {
    if (!current) return;
    current.epoch++;
    current.host.removeEventListener('click', current.listener);
    current = null;
  }
  window.OrionSubscriptionDashboard = Object.freeze({mount, unmount});
})();
