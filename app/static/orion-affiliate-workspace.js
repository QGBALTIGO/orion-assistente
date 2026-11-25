// Affiliate business views mounted by the Orion panel.
export function mount({host, tenantId, view, request, icon, open, showNotice, whatsappAvailable}) {
  const controller = new AbortController();
  const $ = selector => host.querySelector(selector);
  const apiRoot = "/api/v1/afiliados/"+encodeURIComponent(tenantId)+"/api/";
  const assetsRoot = "/api/v1/afiliados-assets";
  const loading = () => '<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>Carregando Afiliados…</div>';
  host.innerHTML = '<div class="af-native-content">'+loading()+'</div>'+
    '<dialog id="osAffiliateDialog" class="os-dialog" aria-labelledby="confirm-title"><h2 id="confirm-title"></h2>'+
    '<p id="confirm-detail"></p><form method="dialog" class="os-form-actions">'+
    '<button class="os-button os-outline" value="cancel" autofocus>Voltar</button>'+
    '<button class="os-button" id="confirm-action" value="confirm">Confirmar</button></form></dialog>';
  const content = $(".af-native-content");
  const html = value => String(value ?? "").replace(/[&<>"']/g, character =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[character]);
  const money = value => value == null ? "Preço indisponível" : Number(value).toLocaleString("pt-BR",{style:"currency",currency:"BRL"});
  const date = (value, timeZone) => {
    if (!value) return "—";
    const options = {dateStyle:"short", timeStyle:"short"};
    try { return new Date(value * 1000).toLocaleString("pt-BR",{...options,timeZone}); }
    catch { return new Date(value * 1000).toLocaleString("pt-BR",options); }
  };
  const normalize = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().trim();
  const id = () => crypto.randomUUID().replaceAll("-", "");
  const states = {approval:"Em revisão",queued:"Programada",preparing:"Preparando",dispatching:"Publicando",
    published:"Publicada",paused:"Pausada",failed:"Falhou",expired:"Expirada",cancelled:"Cancelada",unknown:"Envio sem confirmação"};
  const pendingStates = ["approval","queued","preparing","dispatching"];
  const attentionStates = ["failed","paused","unknown","expired"];
  const actionIcons = {
    refresh:"refresh", demo:"eye", "open-offer":"eye", "new-template":"plus", "edit-template":"edit",
    "new-rule":"plus", "edit-rule":"edit", "close-rule":"close", copy:"copy", "copy-link":"link",
    "ai-phrase":"sparkles", "start-rule":"check",
    "remove-dest":"trash", "cancel-job":"close", "prepare-result":"plus",
  };
  let state;
  let route = view;
  let opened = null;
  let chosenTemplate = null;
  let preview = null;
  let catalog = null;
  let searchQuery = {keyword:"", niche:"", sort:"2"};
  let offerTab = "link";
  let offerInput = {url:"",note:""};
  let offerBusy = false;
  let offerError = "";
  let offerFailure = null;
  let offerLinkChoices = null;
  let offerPreviewBusy = false;
  let offerPreviewError = "";
  let offerPreviewRequest = null;
  let offerSuggestions = [];
  let offerSuggestionsBusy = false;
  let offerPhraseValue = "";
  let offerPhraseBusy = false;
  let offerPhraseOpen = false;
  let offerDetails = {title:"",price:"",image:"",affiliate_url:""};
  let offerDetailsOpen = false;
  let offerDetailsDirty = false;
  let offerDetailsBusy = false;
  let offerDetailsError = "";
  let offerDestinations = new Set();
  let offerSendBusy = false;
  let offerSendError = "";
  let offerSendResult = null;
  let offerSendIntent = null;
  const offerSendPollDelays = [2000,3000,5000,8000];
  let offerSendPollTimer = null;
  let offerSendPollAttempts = 0;
  let offerSendPollRequest = null;
  let offerDraftsOpen = false;
  let offerStoresOpen = false;
  let offerNoteOpen = false;
  let offerPreviewExpanded = false;
  let whatsappGroups = null;
  let whatsappGroupsBusy = false;
  let whatsappGroupsError = "";
  let historyFilter = "all";
  let historySearch = "";
  let editTemplate = "default";
  let editRule = null;
  const ruleSteps = [["products","Produtos"],["destinations","Destinos"],["schedule","Frequência"],["template","Modelo"]];
  const ruleNumberFields = ["interval","daily","repeat_hours","sort"];
  const ruleOptionalNumbers = ["min_price","max_price","min_rating","min_commission","min_sales","min_discount"];
  let ruleDraft = null;
  let ruleDraftKey = null;
  let ruleDraftRevision = 0;
  let ruleEditorVersion = 0;
  let ruleStep = "products";
  let ruleFilter = "all";
  let ruleNicheFilter = "";
  let ruleAdvancedOpen = false;
  let ruleWindowBeforeAllDay = {opens:"09:00",closes:"21:00"};
  let ruleSaveBusy = false;
  let ruleRefreshBusy = false;
  let ruleError = "";
  let ruleListError = "";
  const ruleActionBusy = new Set();
  let navVersion = 0;
  let searchVersion = 0;
  let viewVersion = 0;
  let stateVersion = 0;
  let stateLoading = false;
  const dashboardPeriods = {today:"Hoje", "7d":"7 dias", "30d":"30 dias", month:"Este mês"};
  const dashboardMetrics = {
    commission_cents:{label:"Comissões", singular:"Comissão bruta estimada", currency:true, glyph:"wallet"},
    orders:{label:"Pedidos", singular:"Pedidos", currency:false, glyph:"box"},
    published:{label:"Publicações", singular:"Publicações", currency:false, glyph:"send"},
  };
  const analyticsCache = new Map();
  let analyticsPeriod = "month";
  let analyticsMetric = "commission_cents";
  let analyticsMetricChosen = false;
  let analyticsLoading = false;
  let analyticsError = false;
  let analyticsVersion = 0;
  let analyticsRequest = null;
  let analyticsDisplayedPeriod = null;
  const analyticsPollDelays = [5000,10000,15000,15000,20000,25000];
  let analyticsPollTimer = null;
  let analyticsPollAttempts = 0;
  let chartSelectedDate = null;
  let publicationTab = "upcoming";
  function notify(text, error=false) {
    if (!controller.signal.aborted) showNotice(text, error);
  }
  async function api(path, method="GET", payload, signal=controller.signal) {
    return request(apiRoot + path, method, payload, {signal});
  }
  function confirmAction(title, detail, label, danger=false) {
    const dialog = $("#osAffiliateDialog");
    $("#confirm-title").textContent = title;
    $("#confirm-detail").textContent = detail;
    $("#confirm-action").textContent = label;
    $("#confirm-action").classList.toggle("os-danger", danger);
    dialog.returnValue = "cancel";
    dialog.showModal();
    return new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once:true}));
  }
  const pill = (text, style="neutral") => `<span class="os-badge os-${style || "neutral"}">${html(text)}</span>`;
  const jobPill = job => pill(states[job.status] || job.status,
    job.status==="published"?"success":attentionStates.includes(job.status)?"warning":"neutral");
  const btn = (label, action, value="", primary=false) =>
    `<button class="os-button${primary ? "" : " os-outline"}" type="button" data-act="${action}" data-value="${html(value)}">${actionIcons[action]?icon(actionIcons[action]):""}${html(label)}</button>`;
  const link = (label, href, glyph="", primary=false) =>
    `<a class="os-button${primary ? " af-primary-link" : " os-outline"}" href="#${href}">${glyph?icon(glyph):""}${html(label)}</a>`;
  const submit = (label, glyph="check", disabled=false) =>
    `<button class="os-button" type="submit"${disabled?" disabled":""}>${icon(glyph)}${html(label)}</button>`;
  const square = symbol => `<span class="os-square-icon">${icon(symbol)}</span>`;
  const card = (title, content, glyph="layers", subtitle="", extra="") =>
    `<section class="os-card af-card ${extra}"><header class="af-card-head">${square(glyph)}<div><h2>${html(title)}</h2>${subtitle?`<p>${html(subtitle)}</p>`:""}</div></header>${content}</section>`;
  const empty = (title, text="", glyph="box", action="") =>
    `<div class="os-empty af-empty">${square(glyph)}<h3>${html(title)}</h3>${text?`<p>${html(text)}</p>`:""}${action}</div>`;
  const heading = (title, sub, action="") =>
    `<div class="os-title-row"><div><h1>${html(title)}</h1><p>${html(sub)}</p></div><div class="af-heading-actions">${action}</div></div>`;
  const option = (key, text, selected) => `<option value="${html(key)}"${key === selected ? " selected" : ""}>${html(text)}</option>`;
  const selectField = (label, name, options, id="af-"+name) =>
    `<div class="os-field" data-field-wrapper><label for="${id}">${label}</label><select data-orion-field="native" id="${id}" name="${name}">${options}</select></div>`;
  const templates = selected => state.template.map(row => option(row.id,row.name,selected)).join("");
  const automationDestinations = () => Array.isArray(state.automation_destinations)?state.automation_destinations.map(row=>
    ({...state.destination.find(destination=>destination.id===row.id),...row})):state.destination.map(row=>
    ({...row,eligible:row.verified&&["whatsapp_group","whatsapp_channel","telegram"].includes(row.mode),reason:row.issue}));
  const ruleDestinations = () => automationDestinations().filter(row=>row.eligible);
  const connected = name => state.connection.find(row => row.id === name);
  const defaultMedia = name => ({telegram:"Telegram",whatsapp_group:"Grupo do WhatsApp",whatsapp_channel:"Canal do WhatsApp"})[name];
  const brand = name => `<img src="${assetsRoot}/brands/${name}.${name==="whatsapp"?"svg":"png"}" alt="" width="42" height="42">`;
  const officialLogo = (name, label, status="") =>
    `<header class="af-platform">${name==="groq"?square("sparkles"):brand(name)}<div><h2>${html(label)}</h2>${status}</div></header>`;
  const settings = (label, fields) => `<details class="af-disclosure"><summary>${html(label)}</summary>${fields}</details>`;
  const pending = () => state.jobs.filter(job=>pendingStates.includes(job.status));
  const needingAttention = () => state.jobs.filter(job=>attentionStates.includes(job.status));
  const activeRules = () => state.rule.filter(rule=>rule.active);
  const ruleWindow = rule => rule.opens===rule.closes && rule.opens==="00:00" ? "Dia inteiro" : `${rule.opens}–${rule.closes}`;
  const ruleDate = rule => date(rule.next_at, rule.timezone);
  const ruleDestNames = rule => (rule.destinations || []).map(ident=>state.destination.find(d=>d.id===ident)?.name || "Destino removido").join(", ");


  function activityList(jobs) {
    if (!jobs.length) return `<p class="os-subdash-empty">As publicações preparadas aparecerão aqui.</p>`;
    return `<ul class="os-subdash-activity-list">${jobs.map(job=>`<li class="os-subdash-activity-item">
      <span class="os-subdash-activity-icon">${icon(job.status==="published"?"check":"clock")}</span>
      <span class="os-subdash-activity-copy"><strong>${html(job.doc.offer.title)}</strong>
      <small>${html(state.destination.find(d=>d.id===job.destination)?.name || "Destino removido")} · ${date(job.changed)}</small></span>
      ${jobPill(job)}</li>`).join("")}</ul>`;
  }


  const dashboardNumber = value => typeof value === "number" && Number.isFinite(value) ? value : null;
  const dashboardCount = value => dashboardNumber(value) === null ? "—" : value.toLocaleString("pt-BR");
  const dashboardMoney = value => dashboardNumber(value) === null ? "—" : money(value / 100);
  const dashboardTime = (value, options={dateStyle:"short",timeStyle:"short"}, timeZone="America/Sao_Paulo") => {
    if (!value) return "Ainda não atualizado";
    const parsed = new Date(typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : value);
    if (Number.isNaN(parsed.getTime())) return "Ainda não atualizado";
    try { return parsed.toLocaleString("pt-BR",{...options,timeZone}); }
    catch { return parsed.toLocaleString("pt-BR",options); }
  };
  const dashboardDay = value => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return "Data indisponível";
    return new Date(value+"T12:00:00Z").toLocaleDateString("pt-BR",{day:"2-digit",month:"short",timeZone:"UTC"});
  };
  const dashboardEmpty = (title, detail, glyph="chart", action="") =>
    `<div class="af-dashboard-empty"><span class="af-dashboard-empty-icon" aria-hidden="true">${icon(glyph)}</span><strong>${html(title)}</strong><p>${html(detail)}</p>${action}</div>`;
  const dashboardHasFinancialData = data => [data?.summary?.commission_cents,data?.summary?.sales_cents ?? data?.summary?.gross_sales_cents,data?.summary?.orders].some(value=>dashboardNumber(value)!==null);

  function dashboardSnapshot() {
    // Keep the displayed period in place while the next period is loading.
    // Its own date labels remain visible until the new response is ready.
    const data = analyticsCache.get(analyticsPeriod) || (analyticsLoading ? analyticsCache.get(analyticsDisplayedPeriod) : null);
    const summary = data?.summary || {};
    const jobs = state.jobs || [];
    const published = jobs.filter(job=>job.status==="published").length;
    const pendingCount = pending().length;
    const failed = jobs.filter(job=>job.status==="failed").length;
    const source = data?.sources?.sales || {status:connected("shopee")?.status==="connected"?"unavailable":"not_connected"};
    const publications = data?.sources?.publications || {status:"partial",complete:false,scope:"latest_120"};
    const financial = dashboardHasFinancialData(data);
    return {
      data, summary, source, publications, financial,
      publishedSnapshot:dashboardNumber(data?.operation?.published_snapshot) ?? published,
      pendingSnapshot:(publications.complete?dashboardNumber(summary.pending):null) ?? dashboardNumber(data?.operation?.pending_snapshot) ?? dashboardNumber(summary.pending) ?? pendingCount,
      failedSnapshot:(publications.complete?dashboardNumber(summary.failed):null) ?? dashboardNumber(data?.operation?.failed_snapshot) ?? dashboardNumber(summary.failed) ?? failed,
      rules:dashboardNumber(summary.active_rules) ?? activeRules().length,
      verified:dashboardNumber(summary.verified_destinations) ?? state.destination.filter(row=>row.verified).length,
      sampleLimit:dashboardNumber(data?.operation?.sample_limit) ?? 120,
    };
  }

  function dashboardSourceMessage(snapshot) {
    if (analyticsError || snapshot.source.status==="error") return "Não foi possível atualizar. Tente novamente.";
    if (snapshot.source.status==="partial") return "Resultados parciais deste período.";
    if (snapshot.source.status==="syncing" || analyticsLoading) return "Buscando seus resultados…";
    if (snapshot.financial) return "Sem dados para esta métrica no período.";
    if (snapshot.source.status==="not_connected") return "Conecte sua conta para preparar ofertas com seu link de afiliado.";
    return "Seus resultados aparecerão aqui.";
  }

  function dashboardHero(snapshot) {
    const {summary} = snapshot;
    const commission = dashboardNumber(summary.commission_cents);
    const validated = dashboardNumber(summary.validated_commission_cents ?? summary.approved_commission_cents);
    const first = snapshot.publishedSnapshot===0 && !(dashboardNumber(summary.published)>0);
    return `<section class="af-dashboard-revenue" aria-labelledby="af-revenue-title">
      <header><span class="af-dashboard-eyebrow">Seus resultados</span><span class="af-dashboard-revenue-icon" aria-hidden="true">${icon("wallet")}</span></header>
      <div class="af-dashboard-revenue-main">
        <h2 id="af-revenue-title">Comissão bruta estimada</h2>
        ${commission!==null?`<strong data-dashboard-kpi="commission_cents" class="af-dashboard-revenue-value${Math.abs(commission)>=1e9?" af-dashboard-long-value":""}">${dashboardMoney(commission)}</strong>
          <p>${html(dashboardPeriods[snapshot.data?.period?.key || analyticsPeriod])}</p>`:
          `<strong class="af-dashboard-revenue-intro">${first?"Uma boa oferta.<br>Seu próximo resultado.":"Cada oferta abre<br>uma nova oportunidade."}</strong>
          <p>${html(dashboardSourceMessage(snapshot))}</p>`}
      </div>
      ${commission!==null?`<div class="af-dashboard-revenue-validation"><span>Comissão validada</span><strong data-dashboard-kpi="validated_commission_cents">${dashboardMoney(validated)}</strong></div>`:
        `<div class="af-dashboard-revenue-actions">${link(first?"Preparar primeira oferta":"Preparar oferta","ofertas","plus",true)}</div>`}
    </section>`;
  }

  function dashboardKpis(snapshot) {
    const {summary,financial,publications} = snapshot;
    const scopedPublications = dashboardNumber(summary.published);
    const publishedValue = scopedPublications ?? snapshot.publishedSnapshot;
    const publishedNote = scopedPublications!==null ? publications.complete?"Confirmadas no período":"Contagem parcial do período" : `Últimas ${snapshot.sampleLimit} publicações`;
    const metrics = financial ? [
      {key:"sales_cents",label:"Vendas geradas",value:dashboardMoney(summary.sales_cents ?? summary.gross_sales_cents),glyph:"chart"},
      {key:"orders",label:"Pedidos",value:dashboardCount(summary.orders),glyph:"box"},
      {key:"published",label:"Publicações",value:dashboardCount(publishedValue),note:publishedNote,glyph:"send",href:"fila"},
      {key:"clicks",label:"Cliques",value:dashboardCount(summary.clicks),glyph:"external",external:true},
    ] : [
      {key:scopedPublications!==null?"published":"published_snapshot",label:"Publicadas",value:dashboardCount(publishedValue),note:publishedNote,glyph:"send",href:"fila"},
      {key:"pending",label:"Na fila",value:dashboardCount(snapshot.pendingSnapshot),note:"Programadas ou em revisão",glyph:"clock",href:"fila"},
      {key:"active_rules",label:"Automações",value:dashboardCount(snapshot.rules),note:"Regras ativas",glyph:"repeat",href:"automacoes"},
      {key:"verified_destinations",label:"Destinos",value:dashboardCount(snapshot.verified),note:"Verificados para publicar",glyph:"shieldCheck",href:"conexoes"},
    ];
    return `<section class="af-dashboard-kpis" aria-label="${financial?"Indicadores do período":"Sua operação agora"}">${metrics.map(metric=>{
      const tag = metric.href || metric.external ? "a" : "article";
      const attributes = metric.external ? ' href="https://affiliate.shopee.com.br/" target="_blank" rel="noopener noreferrer"' : metric.href?` href="#${metric.href}"`:"";
      const valueClass = metric.key==="sales_cents"?`af-dashboard-kpi-currency${metric.value.length>13?" af-dashboard-kpi-long":""}`:"";
      return `<${tag} class="af-dashboard-kpi" data-dashboard-card="${metric.key}"${attributes}><span class="af-dashboard-kpi-label">${html(metric.label)}${icon(metric.glyph)}</span><strong data-dashboard-kpi="${metric.key}"${valueClass?` class="${valueClass}"`:""}>${html(metric.value)}</strong>${metric.note?`<small>${html(metric.note)}</small>`:""}</${tag}>`;
    }).join("")}</section>`;
  }

  function dashboardChartModel(snapshot=dashboardSnapshot()) {
    const metric = analyticsMetric==="commission_cents" && !snapshot.financial && !analyticsMetricChosen ? "published" : analyticsMetric;
    const rows = Array.isArray(snapshot.data?.series) ? snapshot.data.series.filter(row=>/^\d{4}-\d{2}-\d{2}$/.test(String(row.date))).slice(0,366) : [];
    const points = rows.map(row=>({date:row.date,value:dashboardNumber(row[metric])}));
    const selectedDate = points.findIndex(point=>point.date===chartSelectedDate);
    const selected = selectedDate>=0?selectedDate:Math.max(points.length-1,0);
    return {metric,config:dashboardMetrics[metric],points,selected};
  }

  function dashboardChart(snapshot) {
    const {metric,config,points,selected} = dashboardChartModel(snapshot);
    const known = points.filter(point=>point.value!==null);
    const total = dashboardNumber(snapshot.summary[metric]);
    const range = snapshot.data?.period;
    const dates = range?.start_date && range?.end_date ? `${dashboardDay(range.start_date)} – ${dashboardDay(range.end_date)}` : dashboardPeriods[analyticsPeriod];
    const unavailable = metric!=="published" ? dashboardSourceMessage(snapshot) : "Sem informações diárias neste período.";
    let plot;
    if (!known.length) {
      const first = metric==="published" && snapshot.publishedSnapshot===0;
      plot = dashboardEmpty(first?"Sua primeira publicação começa aqui":metric==="published"?"Histórico diário indisponível":"Sem resultados disponíveis",
        first?"Prepare uma oferta e acompanhe os próximos envios por aqui.":unavailable,
        config.glyph,first?link("Buscar uma oferta","ofertas","search"):metric==="published"?link("Ver fila e histórico","fila","arrow"):"");
    } else {
      const min = Math.min(0,...known.map(point=>point.value));
      const max = Math.max(...known.map(point=>point.value),config.currency?100:1);
      const span = Math.max(max-min,1);
      const x = index => points.length===1?320:18+(index/(points.length-1))*604;
      const y = value => 158-((value-min)/span)*138;
      const baseline = y(0);
      const segments = [];
      let segment = [];
      points.forEach((point,index)=>{
        if (point.value===null) { if(segment.length) segments.push(segment); segment=[]; }
        else segment.push({x:x(index),y:y(point.value)});
      });
      if (segment.length) segments.push(segment);
      const lines = segments.map(items=>{
        const line = items.map((point,index)=>`${index?"L":"M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
        return `<path class="af-dashboard-chart-area" d="${line} L${items.at(-1).x},${baseline} L${items[0].x},${baseline} Z"/><path class="af-dashboard-chart-line" d="${line}"/>`;
      }).join("");
      const bars = points.map((point,index)=>{
        if (point.value===null) return "";
        const width = Math.min(28,480/Math.max(points.length,1));
        return `<rect class="af-dashboard-chart-bar" x="${x(index)-width/2}" y="${Math.min(y(point.value),baseline)}" width="${width}" height="${Math.abs(y(point.value)-baseline)}" rx="3"/>`;
      }).join("");
      const format = value=>config.currency?dashboardMoney(value):dashboardCount(value);
      plot = `<div class="af-dashboard-chart-readout" role="status" aria-live="polite"><span data-dashboard-point-date>${dashboardDay(points[selected].date)}</span><strong data-dashboard-point-value>${format(points[selected].value)}</strong><small>${html(config.singular)}</small></div>
        <div class="af-dashboard-chart-layout"><div class="af-dashboard-chart-scale" aria-hidden="true"><span>${config.currency?money(max/100).replace(",00",""):dashboardCount(max)}</span><span>${config.currency?money((min+span/2)/100).replace(",00",""):Number((min+span/2).toFixed(1)).toLocaleString("pt-BR")}</span><span>${config.currency?money(min/100).replace(",00",""):dashboardCount(min)}</span></div>
        <div class="af-dashboard-chart-plot"><svg viewBox="0 0 640 180" preserveAspectRatio="none" role="img" aria-labelledby="af-chart-title af-chart-desc">
          <title id="af-chart-title">${html(config.label)} por dia</title><desc id="af-chart-desc">${html(dates)}. Use as setas ou selecione um dia para consultar o valor exato. Dias sem informação não são representados como zero.</desc>
          ${[20,89,158].map(line=>`<line class="af-dashboard-chart-grid" x1="0" y1="${line}" x2="640" y2="${line}"/>`).join("")}
          ${metric==="commission_cents"?lines:bars}
          ${metric==="commission_cents"?points.map((point,index)=>point.value===null?"":`<circle class="af-dashboard-chart-dot" data-chart-dot="${index}" cx="${x(index)}" cy="${y(point.value)}" r="${index===selected?4.5:2.4}"/>`).join(""):""}
        </svg><div class="af-dashboard-chart-hits" role="group" aria-label="Valores diários de ${html(config.label.toLowerCase())}">${points.map((point,index)=>`<button type="button" data-dashboard-point="${index}" data-dashboard-date="${point.date}" tabindex="${index===selected?0:-1}" aria-pressed="${index===selected}" aria-label="${html(dashboardDay(point.date)+": "+format(point.value)+(point.value===null?" · sem informação":""))}" title="${html(dashboardDay(point.date)+": "+format(point.value))}" style="left:${(x(index)/640*100).toFixed(4)}%;width:${Math.min(100,100/points.length).toFixed(4)}%"><span class="os-sr-only">${dashboardDay(point.date)}</span></button>`).join("")}</div></div></div>
        <div class="af-dashboard-chart-axis" aria-hidden="true"><span>${dashboardDay(points[0].date)}</span>${points.length>2?`<span>${dashboardDay(points[Math.floor((points.length-1)/2)].date)}</span>`:""}<span>${points.length>1?dashboardDay(points.at(-1).date):""}</span></div>
        <div class="af-dashboard-chart-controls"><span>${known.every(point=>point.value===0)?"Nenhum resultado registrado neste período.":"Selecione um dia para ver os detalhes."}</span><div><button type="button" class="os-icon-button" data-dashboard-step="-1" aria-label="Dia anterior"${selected===0?" disabled":""}>${icon("left")}</button><button type="button" class="os-icon-button" data-dashboard-step="1" aria-label="Próximo dia"${selected===points.length-1?" disabled":""}>${icon("chevron")}</button></div></div>`;
    }
    return `<section class="af-dashboard-panel af-dashboard-performance" aria-labelledby="af-performance-title"><header class="af-dashboard-panel-head"><div><h2 id="af-performance-title">Desempenho no período</h2><p>${html(dates)}${snapshot.source.status==="partial" && metric!=="published"?" · relatório parcial":""}</p></div>${total!==null?`<span class="af-dashboard-chart-total"><small>${html(config.singular)}</small><strong>${config.currency?dashboardMoney(total):dashboardCount(total)}</strong></span>`:""}</header>
      <div class="af-dashboard-metric-tabs" role="group" aria-label="Métrica do gráfico">${Object.entries(dashboardMetrics).map(([key,item])=>`<button type="button" data-dashboard-metric="${key}" aria-pressed="${key===metric}">${icon(item.glyph)}${html(item.label)}</button>`).join("")}</div>
      <div class="af-dashboard-chart-body">${plot}</div>
    </section>`;
  }

  function dashboardOperation(snapshot) {
    const shopee = connected("shopee")?.status==="connected";
    const next = activeRules().filter(rule=>rule.next_at).sort((a,b)=>a.next_at-b.next_at)[0];
    const rows = [
      {label:"Conta Shopee",description:shopee?"Pronta para buscar e preparar ofertas":"Conecte sua conta de afiliados",value:shopee?"Conectada":"Conectar",glyph:"box",href:"plataformas",ok:shopee},
      {label:"Destinos",description:snapshot.verified?`${snapshot.verified} ${snapshot.verified===1?"destino verificado":"destinos verificados"}`:"Adicione um canal ou grupo",value:snapshot.verified?"Prontos":"Configurar",glyph:"send",href:"conexoes",ok:snapshot.verified>0},
      {label:"Automações",description:snapshot.rules?`${snapshot.rules} ${snapshot.rules===1?"regra ativa":"regras ativas"}`:"Programe suas próximas ofertas",value:snapshot.rules?"Ativas":"Criar regra",glyph:"repeat",href:"automacoes",ok:snapshot.rules>0},
    ];
    return `<section class="af-dashboard-panel af-dashboard-operation" aria-labelledby="af-operation-title"><header class="af-dashboard-panel-head"><div><h2 id="af-operation-title">Sua operação</h2><p>Conexões e automações</p></div><span class="af-dashboard-live-dot${shopee && snapshot.verified?" is-ready":""}" aria-hidden="true"></span></header>
      <div class="af-dashboard-operation-list">${rows.map(row=>`<a href="#${row.href}" class="af-dashboard-operation-row"><span class="af-dashboard-row-icon" aria-hidden="true">${icon(row.glyph)}</span><span class="af-dashboard-row-copy"><strong>${html(row.label)}</strong><small>${html(row.description)}</small></span><span class="af-dashboard-operation-state${row.ok?" is-ready":""}">${row.ok?icon("check"):icon("chevron")}<span class="os-sr-only">${html(row.value)}</span></span></a>`).join("")}</div>
      <div class="af-dashboard-next-run"><span>${icon("calendarClock")}Próxima automação</span>${next?`<strong>${html(next.name)}</strong><small>${html(ruleDate(next))}</small>`:`<strong>Uma rotina que trabalha por você</strong><small>Escolha os produtos, os destinos e os horários.</small>`}<a href="#automacoes" class="af-dashboard-text-link">${next?"Ver programação":"Configurar automação"}${icon("arrow")}</a></div>
    </section>`;
  }

  function dashboardProducts(snapshot) {
    const products = (Array.isArray(snapshot.data?.products)?snapshot.data.products:[]).slice().sort((a,b)=>
      (dashboardNumber(b.commission_cents)??dashboardNumber(b.orders)??0)-(dashboardNumber(a.commission_cents)??dashboardNumber(a.orders)??0)).slice(0,5);
    if (!snapshot.financial && !products.length) return "";
    const max = Math.max(1,...products.map(row=>dashboardNumber(row.commission_cents)??0));
    return `<section class="af-dashboard-panel" aria-labelledby="af-products-title"><header class="af-dashboard-panel-head"><div><h2 id="af-products-title">Produtos em destaque</h2></div>${icon("box")}</header>
      ${products.length?`<ol class="af-dashboard-rank-list">${products.map((row,index)=>{
        const commission = dashboardNumber(row.commission_cents);
        const orders = dashboardNumber(row.orders);
        return `<li><span class="af-dashboard-rank-number">${index+1}</span><div class="af-dashboard-rank-copy"><strong>${html(row.title || row.name || "Produto sem título")}</strong><small>${orders!==null?`${dashboardCount(orders)} ${orders===1?"pedido":"pedidos"}`:"Pedidos ainda não informados"}</small>${commission!==null?`<span class="af-dashboard-rank-track" aria-hidden="true"><span style="width:${Math.max(0,Math.min(100,commission/max*100))}%"></span></span>`:""}</div><strong class="af-dashboard-rank-value">${dashboardMoney(commission)}</strong></li>`;
      }).join("")}</ol>`:dashboardEmpty("Seu próximo destaque está por vir","Os produtos aparecem quando houver vendas atribuídas no período.","box",link("Buscar ofertas","ofertas","search"))}
      </section>`;
  }

  function dashboardDestinations(snapshot) {
    const sourceRows = Array.isArray(snapshot.data?.destinations)?snapshot.data.destinations:null;
    const rows = (sourceRows || state.destination.map(destination=>({...destination,published_snapshot:state.jobs.filter(job=>job.destination===destination.id&&job.status==="published").length})))
      .slice().sort((a,b)=>(dashboardNumber(b.published)??dashboardNumber(b.published_snapshot)??0)-(dashboardNumber(a.published)??dashboardNumber(a.published_snapshot)??0)).slice(0,5);
    const complete = snapshot.publications.complete===true;
    const max = Math.max(1,...rows.map(row=>dashboardNumber(row.published)??dashboardNumber(row.published_snapshot)??0));
    return `<section class="af-dashboard-panel" aria-labelledby="af-destinations-title"><header class="af-dashboard-panel-head"><div><h2 id="af-destinations-title">Onde você publica</h2><p>${complete?"Publicações confirmadas no período":`Resumo das últimas ${snapshot.sampleLimit} publicações`}</p></div>${icon("broadcast")}</header>
      ${rows.length?`<ul class="af-dashboard-rank-list af-dashboard-destination-list">${rows.map(row=>{
        const count = dashboardNumber(row.published)??dashboardNumber(row.published_snapshot);
        return `<li><span class="af-dashboard-row-icon" aria-hidden="true">${icon(row.mode?.startsWith("whatsapp")?"chat":"send")}</span><div class="af-dashboard-rank-copy"><strong>${html(row.name || "Destino")}</strong><small>${html(defaultMedia(row.mode)||"Canal de publicação")}${dashboardNumber(row.orders)!==null?` · ${dashboardCount(row.orders)} pedidos atribuídos`:""}</small>${count!==null && count>0?`<span class="af-dashboard-rank-track" aria-hidden="true"><span style="width:${Math.max(0,Math.min(100,count/max*100))}%"></span></span>`:""}</div><span class="af-dashboard-rank-value"><strong>${dashboardCount(count)}</strong><small>${count===1?"publicação":"publicações"}</small></span></li>`;
      }).join("")}</ul>`:dashboardEmpty("Sua audiência começa em um destino","Conecte um canal ou grupo para receber suas ofertas.","send",link("Adicionar destino","conexoes","plus"))}
      <footer class="af-dashboard-panel-foot"><a href="#conexoes" class="af-dashboard-text-link">Gerenciar destinos${icon("arrow")}</a></footer></section>`;
  }

  function dashboardPublications(snapshot) {
    const upcoming = Array.isArray(snapshot.data?.upcoming)?snapshot.data.upcoming:pending().slice().sort((a,b)=>(a.due||Infinity)-(b.due||Infinity));
    const recent = Array.isArray(snapshot.data?.recent)?snapshot.data.recent:state.jobs;
    const jobs = (publicationTab==="upcoming"?upcoming:recent).slice(0,5);
    if (!upcoming.length && !recent.length) return "";
    return `<section class="af-dashboard-panel af-dashboard-publications" aria-labelledby="af-publications-title"><header class="af-dashboard-panel-head"><div><h2 id="af-publications-title">Publicações</h2><p>Fila atual e atividade mais recente</p></div><a href="#fila" class="af-dashboard-text-link">Ver histórico${icon("arrow")}</a></header>
      <div class="af-dashboard-publication-tabs" role="group" aria-label="Publicações exibidas"><button type="button" data-dashboard-publications="upcoming" aria-pressed="${publicationTab==="upcoming"}">Próximas <span>${dashboardCount(upcoming.length)}</span></button><button type="button" data-dashboard-publications="recent" aria-pressed="${publicationTab==="recent"}">Recentes</button></div>
      <div class="af-dashboard-publication-list">${jobs.length?`<ul class="af-dashboard-job-list">${jobs.map(job=>{
        const destination = state.destination.find(row=>row.id===(job.destination_id||job.destination));
        const when = publicationTab==="upcoming"?(job.scheduled_at||job.due):(job.published_at||job.changed);
        const whenLabel = when?dashboardTime(when,undefined,job.doc?.rule?.timezone):publicationTab==="recent"?"Horário não informado":job.status==="approval"?"Aguardando revisão":"Sem horário definido";
        return `<li><span class="af-dashboard-row-icon" aria-hidden="true">${icon(job.status==="published"?"check":"clock")}</span><div class="af-dashboard-job-copy"><strong>${html(job.title||job.doc?.offer?.title||"Oferta preparada")}</strong><small>${html(job.destination_name||destination?.name||"Destino removido")}</small></div><span class="af-dashboard-job-time">${html(whenLabel)}</span>${jobPill(job)}</li>`;
      }).join("")}</ul>`:dashboardEmpty("Sua fila está livre","Prepare outra oferta ou configure uma automação para manter seus canais ativos.","calendarClock",link("Preparar oferta","ofertas","plus"))}</div></section>`;
  }

  function dashboard() {
    const snapshot = dashboardSnapshot();
    const products = dashboardProducts(snapshot);
    const busy = analyticsLoading || stateLoading;
    const syncing = snapshot.source.status==="syncing" && analyticsPollAttempts<analyticsPollDelays.length;
    const completeFailures = snapshot.publications.complete && dashboardNumber(snapshot.summary.failed)>0;
    const attention = completeFailures?snapshot.summary.failed:needingAttention().length;
    return `<div class="af-dashboard">
      <header class="af-dashboard-heading"><div><span class="af-dashboard-eyebrow">Afiliados</span><h1>Dashboard</h1></div><div class="af-dashboard-heading-actions">${link("Nova oferta","ofertas","plus",true)}</div><p>Suas ofertas, seus canais e seus próximos resultados.</p></header>
      <div class="af-dashboard-toolbar"><div class="af-dashboard-periods" role="group" aria-label="Período dos resultados">${Object.entries(dashboardPeriods).map(([key,label])=>`<button type="button" data-dashboard-period="${key}" aria-pressed="${key===analyticsPeriod}">${html(label)}</button>`).join("")}</div>
        <div class="af-dashboard-freshness"><button class="os-icon-button" type="button" data-act="refresh" aria-label="Atualizar Dashboard" aria-busy="${busy || syncing}" title="Atualizar Dashboard"${busy?" disabled":""}>${icon("refresh")}</button></div></div>
      ${analyticsError?`<div class="af-dashboard-alert" data-dashboard-notice="analytics" role="status">${icon("info")}<p>Não foi possível atualizar. Tente novamente.</p></div>`:""}
      <div class="af-dashboard-overview" aria-busy="${busy}">${dashboardHero(snapshot)}${dashboardKpis(snapshot)}</div>
      ${attention?`<a href="#fila" class="af-dashboard-alert af-dashboard-alert-warning">${icon("shieldAlert")}<p><strong>${attention} ${completeFailures?(attention===1?"publicação com falha":"publicações com falha"):(attention===1?"publicação precisa de atenção":"publicações precisam de atenção")}</strong><span>${completeFailures?"Revise os envios com falha na fila.":`Revise as ocorrências entre as últimas ${snapshot.sampleLimit} publicações.`}</span></p>${icon("chevron")}</a>`:""}
      <div class="af-dashboard-workspace">${dashboardChart(snapshot)}${dashboardOperation(snapshot)}</div>
      <div class="af-dashboard-rankings${products?"":" af-dashboard-rankings-single"}">${products}${dashboardDestinations(snapshot)}</div>
      ${dashboardPublications(snapshot)}
    </div>`;
  }

  function platforms() {
    const shopee = connected("shopee");
    const linked = shopee?.status==="connected";
    const groq = connected("groq")?.status==="connected";
    const shopeeForm = `<form class="os-form af-form" data-form="shopee">
      <label>App ID <input name="app_id" inputmode="numeric" pattern="[0-9]+" required></label>
      <label>Segredo da API <input name="key" type="password" autocomplete="new-password" required minlength="10"></label>
      <div class="af-row">${submit("Validar e conectar","link")}${linked?btn("Desconectar","revoke","shopee"):""}</div></form>`;
    const groqForm = `<form class="os-form af-form" data-form="groq">
      <label>Chave da Groq <input name="key" type="password" autocomplete="new-password" required></label>
      <div class="af-row">${submit("Validar e conectar","link")}${groq?btn("Desconectar","revoke-groq"):""}</div></form>`;
    return heading("Plataformas","Gerencie a conta da Shopee e o serviço que sugere as frases das ofertas.") +
      `<div class="af-page"><div class="af-grid">
        <section class="os-card af-card">
          ${officialLogo("shopee","Shopee",linked?pill("Conectada","success"):pill("Não conectada"))}
          <div class="af-stack"><p>${linked?`Conta ${html(shopee.account)} · validada em ${date(shopee.verified)}`:"Use o App ID e o segredo da API de Afiliados Shopee Brasil."}</p>
          ${linked?settings("Gerenciar credenciais da Shopee",shopeeForm):shopeeForm}</div>
        </section>
        <section class="os-card af-card">
          ${officialLogo("groq","Groq",state.phrase_ai?pill("Disponível","success"):pill("Chave necessária"))}
          <div class="af-stack"><p>Sugere uma frase para a legenda. Preço, desconto e link vêm da Shopee.</p>
          ${state.phrase_ai?settings("Gerenciar credenciais da Groq",groqForm):groqForm}</div>
        </section>
      </div>
      <section class="af-stack" aria-label="Próximas plataformas">
        <h2>Próximas plataformas</h2>
        <div class="af-grid">${[["amazon","Amazon"],["mercado-livre","Mercado Livre"]].map(([key,label])=>`
          <section class="os-card af-card">${officialLogo(key,label,pill("Planejada"))}
          <p>Conexão e links de afiliado ainda indisponíveis.</p></section>`).join("")}</div>
      </section></div>`;
  }


  function whatsappGroupPicker() {
    const rows=Array.isArray(whatsappGroups)?whatsappGroups:[];
    return '<div class="af-whatsapp-group-picker" data-wa-group-content>'+
      '<button type="button" class="os-button os-outline" data-act="load-wa-groups"'+(whatsappGroupsBusy?" disabled":"")+
      ' aria-busy="'+whatsappGroupsBusy+'">'+icon(whatsappGroupsBusy?"refresh":"users")+
      (whatsappGroupsBusy?"Consultando grupos…":whatsappGroups?"Atualizar grupos":"Buscar grupos do WhatsApp")+'</button>'+
      (whatsappGroups?'<label for="af-whatsapp-group">Grupo da conta conectada<select id="af-whatsapp-group" name="whatsapp_group" data-orion-field="native">'+
        option("","Selecione um grupo")+rows.map(group=>{
          const added=state.destination.some(destination=>destination.mode==="whatsapp_group"&&destination.address===group.address);
          return '<option value="'+html(group.address)+'"'+(added?" disabled":"")+'>'+html(group.title)+(added?" · Já cadastrado":"")+'</option>';
        }).join("")+'</select></label>'+
        (!rows.length?'<p class="os-help">Nenhum grupo disponível nesta conexão.</p>':""):"")+
      (whatsappGroupsError?'<p class="os-notice os-error" role="alert">'+html(whatsappGroupsError)+'</p>':"")+'</div>';
  }

  function updateWhatsappGroupPicker() {
    const current=content.querySelector("[data-wa-group-content]");
    if (!current) return;
    const template=document.createElement("template");
    template.innerHTML=whatsappGroupPicker();
    patchDashboardNode(current,template.content.firstElementChild);
    const select=current.querySelector("select");
    if (select) select.required=current.closest("form")?.elements.mode.value==="whatsapp_group";
    window.OrionFields?.refresh(current);
  }

  function connections() {
    const tg = connected("telegram");
    const linked = tg?.status==="connected";
    const pairing = tg?.status==="pairing";
    const tgForm = `<form class="os-form af-form" data-form="telegram">
      <label>Token do bot <input name="token" type="password" autocomplete="new-password" required></label>
      <label class="os-check"><input name="dedicated" type="checkbox" required> Este bot é dedicado às publicações de Afiliados.</label>
      <div class="af-row">${submit("Vincular bot","link")}${linked||pairing?btn("Desconectar","revoke","telegram"):""}</div>
      </form>`;
    const all = state.destination.map(d=>`<div class="af-item"><div><strong>${html(d.name)}</strong>
        <small>${html(defaultMedia(d.mode))}${d.issue?` · ${html(d.issue)}`:""}</small></div>
      <div class="af-row">${pill(d.verified?"Verificado":d.mode==="telegram"?"Verificação pendente":"Publicação assistida",d.verified?"success":"neutral")}
        ${d.mode==="whatsapp_channel"?btn(d.verified?"Verificar novamente":"Verificar canal","verify-wa-channel",d.id):""}
        ${btn("Remover","remove-dest",d.id)}</div></div>`).join("");
    return heading("Conexões e destinos","Vincule a conta que publica e confirme a administração de cada canal ou grupo.") + `
      <div class="af-page"><div class="af-grid">
        <section class="os-card af-card">
          <header class="af-platform">${brand("whatsapp")}<div><h2>WhatsApp</h2></div></header>
          <p>Conecte o aparelho da sua empresa usando o fluxo de dispositivos da Orion.</p>
          <div class="os-form-actions">${whatsappAvailable ? '<button class="os-button" type="button" data-os-route="afiliados-whatsapp">'+icon("phone")+'Gerenciar WhatsApp</button>' : '<p class="os-help">A conexão do WhatsApp está indisponível neste momento.</p>'}</div>
        </section>
        <section class="os-card af-card">
          ${officialLogo("telegram","Telegram",pill(linked?"Vinculado":pairing?"Vínculo pendente":"Não vinculado",linked?"success":pairing?"warning":"neutral"))}
          <div class="af-stack"><p>${linked?`Bot @${html(tg.username)} vinculado.`:"Vincule um bot dedicado. O bot e sua conta devem administrar o destino."}</p>
          ${pairing?`<div class="af-row"><a href="${html(tg.pair_url)}" target="_blank" rel="noopener noreferrer">Abrir o bot e tocar em Iniciar</a>${btn("Confirmar vínculo","verify-tg","",true)}</div>`:""}
          ${linked||pairing?settings("Gerenciar credenciais do Telegram",tgForm):tgForm}</div>
        </section>
      </div><div class="af-sheet">
        ${card("Destinos cadastrados",all?`<div class="af-list">${all}</div>`:empty("Nenhum destino cadastrado","Cadastre um canal ou verifique um grupo administrado.","link"),"list")}
        ${card("Cadastrar destino",`<form class="os-form af-form" data-form="destination">
          <label>Nome <input name="name" required maxlength="80" placeholder="Ex.: Meu canal de ofertas"></label>
          ${selectField("Tipo","mode",`${Object.keys({telegram:0,whatsapp_group:0,whatsapp_channel:0}).map(m=>option(m,defaultMedia(m),"telegram")).join("")}`)}
          <label data-destination-address><span data-address-label>ID numérico ou @ do Telegram</span> <input name="address" maxlength="180" placeholder="@canal_publico ou ID -100…"></label>
          <div data-wa-group-picker hidden>${whatsappGroupPicker()}</div>
          <label class="os-check af-test-destination"><input name="test" type="checkbox"> Usar também nos testes de automação.</label>
          <p class="os-help" data-assisted-destination hidden>Depois de salvar, verifique a administração do canal.</p>
          <div class="os-form-actions">${submit("Salvar destino")}</div></form>`,"plus")}
      </div></div>`;
  }



  const currentOffer = () => state.offer.find(row=>row.id===opened) || null;
  const offerRetailer = row => row?.retailer_name || (row?.platform==="shopee" || row?.shop ? "Shopee" : row?.store || "Oferta");
  const offerUrl = value => {
    if (!value) return "";
    try {
      const url=new URL(value,window.location.origin);
      return url.protocol==="https:" || url.origin===window.location.origin ? url.href : "";
    } catch { return ""; }
  };
  const offerPriceInput = value => value==null?"":Number(value).toLocaleString("pt-BR",{minimumFractionDigits:2,maximumFractionDigits:2});
  const offerPrice = row => {
    if (row?.price==null || !Number.isFinite(Number(row.price))) return "Preço indisponível";
    if (!/^[A-Z]{3}$/.test(row.currency||"")) return "Preço a confirmar";
    try { return Number(row.price).toLocaleString("pt-BR",{style:"currency",currency:row.currency}); }
    catch { return "Preço a confirmar"; }
  };
  const offerCaption = value => {
    if (!value) return html(preview?.text || "");
    const source=document.createElement("template");
    source.innerHTML=String(value);
    const clean=node=>{
      if (node.nodeType===3) return html(node.nodeValue);
      if (node.nodeType!==1) return "";
      const children=[...node.childNodes].map(clean).join("");
      const tag=node.tagName.toLowerCase();
      return ["strong","em","s"].includes(tag)?"<"+tag+">"+children+"</"+tag+">":tag==="br"?"\n":children;
    };
    return [...source.content.childNodes].map(clean).join("");
  };
  const offerCardUrl = row => apiRoot+"offers/"+encodeURIComponent(row.id)+"/card.png?template="+encodeURIComponent(chosenTemplate)+
    "&v="+encodeURIComponent([row.title,row.price,row.image].join("|"));
  const offerDestinationRows = () => Array.isArray(preview?.destinations)?preview.destinations:
    state.destination.map(row=>({...row,eligible:false,reason:"Atualize a prévia para verificar este destino."}));
  const eligibleOfferDestinations = () => offerDestinationRows().filter(row=>row.eligible);
  const offerAlert = (text,key) => text?'<p class="af-offer-alert" role="alert" data-offer-notice="'+key+'">'+icon("info")+'<span>'+html(text)+'</span></p>':"";
  const offerSubmit = (label,glyph,busy=false,disabled=false) => '<button class="os-button" type="submit"'+(disabled?" disabled":"")+
    ' aria-disabled="'+(busy||disabled)+'" aria-busy="'+busy+'">'+(busy?'<span class="os-spinner" aria-hidden="true"></span>':icon(glyph))+'<span>'+html(label)+'</span></button>';

  function offerPrepareFeedback() {
    const failure=offerFailure?.url===offerInput.url?offerFailure:null;
    const choices=offerLinkChoices?.input===offerInput.url?offerLinkChoices.candidates:[];
    return '<div data-offer-prepare-feedback>'+offerAlert(offerError||(offerBusy?failure?.message:""),"prepare")+
      (choices.length?'<div class="af-offer-link-choices" data-offer-link-choices role="group" aria-label="Escolha o link do produto">'+choices.map((row,index)=>
        '<button type="button" class="af-offer-link-choice" data-offer-link-choice="'+index+'" data-act="offer-link-choice" data-value="'+index+'" aria-disabled="'+offerBusy+'">'+
        '<span><strong>'+html(row.retailer_name||"Produto")+'</strong><span>'+html(row.url)+'</span></span>'+icon("arrow")+'</button>').join("")+'</div>':"")+
      (failure?'<div class="af-offer-recovery-actions"><button type="button" class="os-button os-outline" data-act="offer-retry" aria-disabled="'+offerBusy+'" aria-busy="'+offerBusy+'">'+icon("refresh")+'Tentar novamente</button>'+
        '<button type="button" class="af-offer-text-button" data-act="offer-manual" aria-disabled="'+offerBusy+'">'+icon("edit")+'Preencher manualmente</button></div>':"")+'</div>';
  }

  function offerExtractionNotice(row) {
    const extraction=row.extraction;
    if (!extraction || row.demo) return "";
    const missing=[];
    if (!row.title) missing.push("nome");
    if (row.price==null) missing.push("preço");
    else if (row.currency!=="BRL") missing.push("preço em reais");
    if (!row.image && preview?.media!=="text") missing.push("imagem");
    if (!missing.length) return "";
    const fields=missing.length>1?missing.slice(0,-1).join(", ")+" e "+missing.at(-1):missing[0];
    const reason=extraction.manual_requested?"Preenchimento manual. Adicione "+fields+" para preparar a publicação.":
      "A leitura do produto ficou incompleta. Confira "+fields+" antes de publicar.";
    return offerAlert(reason,"extraction");
  }

  function offerSource() {
    const shops=Array.isArray(state.retailers)?state.retailers:[];
    const results=(catalog||[]).map(row=>{
      const flag=state.product_flag.find(item=>item.id===row.identity);
      const photo=offerUrl(row.image);
      return '<article class="af-offer-catalog-item" data-offer-product="'+html(row.identity||row.catalog_url)+'">'+
        (photo?'<img src="'+html(photo)+'" alt="" loading="lazy" referrerpolicy="no-referrer" width="64" height="64">':'<span class="af-offer-product-symbol">'+icon("box")+'</span>')+
        '<div><h3>'+html(row.title)+'</h3><strong>'+html(offerPrice(row))+'</strong><div class="af-offer-catalog-actions">'+
        btn("Preparar","prepare-result",row.catalog_url,true)+
        '<button type="button" class="os-icon-button" data-act="favorite" data-value="'+html(row.identity)+'" aria-label="'+(flag?.favorite?"Remover dos favoritos":"Favoritar")+'" aria-pressed="'+!!flag?.favorite+'">'+icon("gift")+'</button>'+
        '<button type="button" class="os-icon-button" data-act="block" data-value="'+html(row.identity)+'" aria-label="'+(flag?.blocked?"Desbloquear produto":"Bloquear produto")+'" aria-pressed="'+!!flag?.blocked+'">'+icon("block")+'</button>'+
        '</div></div></article>';
    }).join("");
    return '<section class="af-offer-source af-offer-panel" data-offer-section="source" aria-labelledby="af-offer-source-title">'+
      '<div class="af-offer-source-head"><h2 id="af-offer-source-title">Prepare uma oferta</h2><div class="af-offer-tabs" role="group" aria-label="Como encontrar o produto">'+
      [["link","Por link","link"],["catalog","Catálogo Shopee","search"]].map(([key,label,glyph])=>
        '<button type="button" data-offer-tab="'+key+'" aria-pressed="'+(offerTab===key)+'" aria-controls="offer-'+key+'">'+icon(glyph)+html(label)+'</button>').join("")+
      '</div></div><div id="offer-link"'+(offerTab==="link"?"":" hidden")+'>'+
        '<form class="os-form af-form af-offer-link-form" data-form="offer" aria-busy="'+offerBusy+'">'+
          '<label class="af-offer-url-label" for="af-offer-url"><span class="os-sr-only">Link ou mensagem com o produto</span><span class="af-offer-input-icon" aria-hidden="true">'+icon("link")+'</span>'+
          '<input id="af-offer-url" name="url" type="text" required maxlength="8192" inputmode="url" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="Cole o link ou a mensagem da oferta" value="'+html(offerInput.url)+'" data-offer-input="url"></label>'+
          offerSubmit(offerBusy?"Preparando…":"Gerar prévia","arrow",offerBusy)+
        '</form>'+offerPrepareFeedback()+
        '<div class="af-offer-source-extra">'+
          '<details class="af-offer-note" data-offer-disclosure="note"'+(offerNoteOpen?" open":"")+'><summary>Adicionar observação</summary><label class="os-sr-only" for="af-offer-note">Observação da oferta</label><input id="af-offer-note" maxlength="300" name="note" placeholder="Uma informação para incluir na legenda" value="'+html(offerInput.note)+'" data-offer-input="note"></details>'+
          (shops.length?'<details class="af-offer-stores" data-offer-disclosure="stores"'+(offerStoresOpen?" open":"")+'><summary>Links de '+shops.length+' lojas</summary><div>'+shops.map(shop=>'<span>'+html(shop.name)+'</span>').join("")+'</div></details>':"")+
        '</div></div><div id="offer-catalog"'+(offerTab==="catalog"?"":" hidden")+'>'+
          '<form class="os-form af-form af-offer-search-form" data-form="search">'+
            '<label class="af-offer-search-keyword">Buscar produto<input name="keyword" placeholder="Ex.: fone sem fio" maxlength="80" value="'+html(searchQuery.keyword)+'" data-offer-search="keyword"></label>'+
            selectField("Categoria","niche",option("","Todas as categorias",searchQuery.niche)+state.niches.map(row=>option(row.id,row.name,searchQuery.niche)).join(""),"catalog-category")+
            selectField("Ordenar","sort",Object.entries(state.sorts).map(([key,label])=>option(key,label,searchQuery.sort)).join(""),"catalog-sort")+
            offerSubmit("Pesquisar","search")+
          '</form>'+(catalog?'<div class="af-offer-catalog-results">'+(results||empty("Nenhuma oferta encontrada","Tente outra busca ou categoria.","search"))+'</div>':"")+
        '</div></section>';
  }

  function offerProductEditor(row) {
    const missingLink=!row.affiliate_url && !row.demo;
    return '<div class="af-offer-product" data-offer-section="product">'+
      offerExtractionNotice(row)+
      '<div class="af-offer-product-summary"><span class="af-offer-product-symbol">'+icon("box")+'</span><div><span class="af-offer-retailer">'+html(row.demo?"Exemplo":offerRetailer(row))+'</span><h2>'+html(row.title||"Complete os detalhes do produto")+'</h2>'+
      '<div class="af-offer-product-price">'+(row.price!=null?'<strong>'+html(offerPrice(row))+'</strong>':'<span>Adicione o preço</span>')+
      (row.affiliate_url?'<button type="button" data-act="copy-link" data-value="'+html(row.id)+'">'+icon("copy")+'Copiar link</button>':"")+'</div></div></div>'+
      '<details class="af-offer-details" data-offer-disclosure="details"'+(offerDetailsOpen?" open":"")+'><summary>'+icon("edit")+'Editar detalhes do produto</summary>'+
      '<form class="os-form af-form" data-form="offer-details" aria-busy="'+offerDetailsBusy+'">'+
        '<label for="af-offer-title">Nome do produto<input id="af-offer-title" name="title" maxlength="300" value="'+html(offerDetails.title)+'" data-offer-detail="title"></label>'+
        (row.price!=null&&row.currency!=="BRL"?'<p class="af-offer-currency-note">Preço da loja: '+html(offerPrice(row))+'. Informe o valor em reais.</p>':"")+
        '<div class="af-offer-detail-grid"><label for="af-offer-price">Preço (R$)<input id="af-offer-price" name="price" inputmode="decimal" data-orion-field="money" placeholder="0,00" value="'+html(offerDetails.price)+'" data-offer-detail="price"></label>'+
        '<label for="af-offer-image">Link da imagem<input id="af-offer-image" name="image" type="url" maxlength="2048" placeholder="https://…" value="'+html(offerDetails.image)+'" data-offer-detail="image"></label></div>'+
        (!missingLink?'<label for="af-offer-affiliate-edit">Seu link de afiliado<input id="af-offer-affiliate-edit" name="affiliate_url" type="url" maxlength="2048" value="'+html(offerDetails.affiliate_url)+'" data-offer-detail="affiliate_url"></label>':"")+
        '<div class="af-offer-inline-actions">'+offerSubmit("Salvar detalhes","check",offerDetailsBusy)+'</div></form></details>'+
      (missingLink?'<form class="os-form af-form af-offer-own-link" data-form="offer-own-link" aria-busy="'+offerDetailsBusy+'">'+
        '<label for="af-offer-affiliate">Seu link de afiliado<span class="af-offer-own-link-input"><input id="af-offer-affiliate" name="affiliate_url" type="url" required maxlength="2048" placeholder="Cole o link gerado na sua conta" value="'+html(offerDetails.affiliate_url)+'" data-offer-detail="affiliate_url">'+offerSubmit("Salvar link","check",offerDetailsBusy)+'</span></label></form>':"")+
      offerAlert(offerDetailsError,"details")+'</div>';
  }

  function offerSettings(row) {
    const suggestions=offerSuggestions.slice(0,3);
    const current=row.phrase||"";
    return '<section class="af-offer-settings" data-offer-section="settings" aria-labelledby="af-offer-template-title">'+
      '<div class="af-offer-template-heading"><div><h2 id="af-offer-template-title">Template e legenda</h2><p>Deixe a oferta com a sua identidade.</p></div>'+icon("layers")+'</div>'+
      '<div class="af-form">'+selectField("Template","template",templates(chosenTemplate),"preview-template")+'</div>'+
      '<div class="af-offer-phrase-heading"><h3>Escolha uma frase</h3><button type="button" class="af-offer-text-button" data-act="offer-phrases"'+' aria-disabled="'+offerSuggestionsBusy+'"'+' aria-busy="'+offerSuggestionsBusy+'">'+icon(offerSuggestionsBusy?"refresh":"sparkles")+(offerSuggestionsBusy?"Gerando…":"Outras frases")+'</button></div>'+
      '<div class="af-offer-suggestions" role="group" aria-label="Frases para a oferta">'+suggestions.map((suggestion,index)=>
        '<button type="button" data-offer-phrase="'+html(suggestion.id||String(index))+'" aria-pressed="'+(suggestion.text===current)+'"'+' aria-disabled="'+offerPhraseBusy+'"'+'><span class="af-offer-phrase-check" aria-hidden="true">'+icon("check")+'</span><span>'+html(suggestion.text)+'</span></button>').join("")+
        (!suggestions.length&&current?'<p class="af-offer-current-phrase">'+html(current)+'</p>':"")+'</div>'+
      '<details class="af-offer-custom-phrase" data-offer-disclosure="phrase"'+(offerPhraseOpen?" open":"")+'><summary>'+icon("edit")+'Escrever minha frase</summary>'+
        '<form class="os-form af-form" data-form="phrase" aria-busy="'+offerPhraseBusy+'"><label class="os-sr-only" for="af-offer-phrase">Frase da oferta</label>'+
        '<textarea id="af-offer-phrase" name="text" maxlength="155" rows="2" placeholder="Escreva uma abertura para esta oferta" data-offer-phrase-input>'+html(offerPhraseValue)+'</textarea>'+
        '<div class="af-offer-inline-actions"><small data-offer-phrase-count>'+offerPhraseValue.length+'/155</small>'+offerSubmit("Aplicar frase","check",offerPhraseBusy)+'</div></form></details>'+
    '</section>';
  }

  function offerPreview() {
    const row=currentOffer();
    if (!row) return "";
    const image=offerCardUrl(row);
    const full=String(preview?.text||"");
    const fold=full.length>280||full.split("\n").length>6;
    return '<section class="af-offer-preview" data-offer-section="preview" aria-labelledby="af-offer-preview-title" aria-busy="'+offerPreviewBusy+'">'+
      '<div class="af-offer-preview-heading"><h2 id="af-offer-preview-title">Prévia da publicação</h2><span aria-live="polite">'+(offerPreviewBusy?"Atualizando…":"")+'</span></div>'+
      offerAlert(offerPreviewError,"preview")+
      '<div class="af-offer-message">'+
        (preview?.media==="card"?'<div class="af-offer-card-wrap"><div class="af-offer-card-frame"><img class="af-offer-card-image" src="'+html(image)+'" alt="Card da oferta: '+html(row.title||"produto")+'" width="900" height="970"></div></div>':"")+
        '<div class="af-offer-caption'+(offerPreviewExpanded?" is-expanded":"")+'" id="offer-text">'+
          (preview?offerCaption(preview.html):'<span class="af-offer-preview-loading">'+icon("refresh")+'Preparando sua prévia…</span>')+'</div>'+
        (fold?'<button type="button" class="af-offer-text-button af-offer-expand" data-act="offer-expand" aria-expanded="'+offerPreviewExpanded+'" aria-controls="offer-text">'+
          (offerPreviewExpanded?"Recolher legenda":"Ver legenda completa")+icon(offerPreviewExpanded?"arrowUp":"down")+'</button>':"")+
      '</div>'+
      '<div class="af-offer-preview-actions">'+
        '<button type="button" class="os-button os-outline" data-act="copy"'+(!preview?" disabled":"")+'>'+icon("copy")+'Copiar legenda</button>'+
        (preview?.media==="card"?'<a class="os-button os-outline" href="'+html(image)+'" download="oferta-orion.png">'+icon("download")+'Baixar card</a>':"")+
      '</div></section>';
  }


  function offerSendStatus() {
    const jobs=Array.isArray(offerSendResult?.jobs)?offerSendResult.jobs:[];
    const published=jobs.filter(job=>job.status==="published").length;
    const pending=jobs.some(job=>["queued","preparing","dispatching"].includes(job.status));
    const title=published===jobs.length&&jobs.length?
      (jobs.length===1?"Oferta publicada":"Publicações concluídas"):pending?
      jobs.length+" "+(jobs.length===1?"publicação":"publicações")+" na fila":"Confira os envios";
    return '<div class="af-offer-send-success" role="status" data-offer-send-result tabindex="-1">'+icon(published===jobs.length?"check":"clock")+
      '<div><strong>'+html(title)+'</strong><ul class="af-offer-send-status">'+jobs.map(job=>{
        const destination=offerDestinationRows().find(row=>row.id===job.destination);
        return '<li data-offer-job="'+html(job.id)+'"><span>'+html(destination?.name||"Destino")+'</span>'+jobPill(job)+
          (job.error?'<small>'+html(job.error)+'</small>':"")+'</li>';
      }).join("")+'</ul><a href="#fila">Acompanhar envios '+icon("arrow")+'</a></div></div>';
  }

  function offerTargets() {
    const rows=offerDestinationRows();
    const available=rows.filter(row=>row.eligible);
    const selected=available.filter(row=>offerDestinations.has(row.id));
    const all=available.length>0&&selected.length===available.length;
    const canSend=offerCanSend(true);
    const busy=offerSendBusy;
    const reason=offerDetailsDirty?"Salve os detalhes para atualizar a prévia.":offerPhraseValue!==(currentOffer()?.phrase||"")?
      "Aplique a frase para atualizar a prévia.":!preview?.send?.enabled?preview?.send?.reason:"";
    const sent=offerSendResult;
    return '<section class="af-offer-targets" data-offer-section="targets" aria-labelledby="af-offer-target-title">'+
      '<div class="af-offer-target-heading"><div><h2 id="af-offer-target-title">Onde publicar</h2><p>Selecione um ou mais destinos.</p></div><a href="#conexoes" aria-label="Gerenciar destinos">'+icon("settings")+'</a></div>'+
      (rows.length?'<div class="af-offer-target-toolbar"><label><input type="checkbox" data-offer-all'+(all?" checked":"")+(available.length?"":" disabled")+'>Selecionar todos</label><span data-offer-target-count>'+selected.length+' selecionado'+(selected.length===1?"":"s")+'</span></div>'+
        '<div class="af-offer-target-list">'+rows.map(row=>'<label class="af-offer-target'+(!row.eligible?" is-unavailable":"")+'" data-offer-destination-row="'+html(row.id)+'">'+
          '<input type="checkbox" data-offer-destination="'+html(row.id)+'" value="'+html(row.id)+'"'+(offerDestinations.has(row.id)&&row.eligible?" checked":"")+(row.eligible&&!busy?"":" disabled")+'>'+
          '<span class="af-offer-target-icon" aria-hidden="true">'+icon(row.mode==="telegram"?"send":row.mode==="whatsapp_channel"?"broadcast":"users")+'</span><span><strong>'+html(row.name)+'</strong>'+
          '<small>'+html(row.eligible?defaultMedia(row.mode)||row.mode:row.reason||"Verifique este destino em Conexões.")+'</small></span></label>').join("")+'</div>':
        '<div class="af-offer-no-destinations">'+icon("broadcast")+'<p>Adicione um canal ou grupo para publicar sua oferta.</p><a class="os-button os-outline" href="#conexoes">'+icon("plus")+'Adicionar destino</a></div>')+
      offerAlert(offerSendError,"send")+
      (sent?offerSendStatus():
        '<div class="af-offer-send-footer">'+(reason?'<p>'+html(reason)+'</p>':"")+
          '<button type="button" class="os-button" data-act="offer-send"'+(!canSend||!selected.length?" disabled":"")+' aria-disabled="'+busy+'" aria-busy="'+busy+'">'+
          (busy?'<span class="os-spinner" aria-hidden="true"></span>':icon("send"))+'<span>'+(busy?"Preparando envios…":selected.length?"Enviar para "+selected.length+" destino"+(selected.length===1?"":"s"):"Escolha os destinos")+'</span></button></div>')+
    '</section>';
  }

  function offerComposer() {
    const row=currentOffer();
    return '<section class="af-offer-composer af-offer-panel" data-offer-section="composer"'+(row?"":" hidden")+' aria-labelledby="af-offer-composer-title">'+
      (row?'<div class="af-offer-composer-head"><h2 id="af-offer-composer-title" tabindex="-1">Sua publicação</h2><button type="button" class="af-offer-text-button" data-act="offer-new">'+icon("plus")+'Nova oferta</button></div>'+
        offerProductEditor(row)+'<div class="af-offer-composer-grid">'+offerSettings(row)+offerPreview()+offerTargets()+'</div>':"")+'</section>';
  }

  function offerDrafts() {
    const rows=[...state.offer].slice(0,12);
    return '<details class="af-offer-drafts af-offer-panel" data-offer-disclosure="drafts"'+(offerDraftsOpen?" open":"")+'><summary><span>'+icon("fileCheck")+'Rascunhos<span class="af-offer-draft-count">'+state.offer.length+'</span></span>'+icon("down")+'</summary>'+
      (rows.length?'<div class="af-offer-draft-list">'+rows.map(row=>'<button type="button" data-act="open-offer" data-offer-draft="'+html(row.id)+'" data-value="'+html(row.id)+'" aria-current="'+(row.id===opened)+'">'+
        '<span class="af-offer-draft-icon">'+icon(row.demo?"flask":"box")+'</span><span><strong>'+html(row.title||"Oferta para completar")+'</strong><small>'+html(offerRetailer(row))+(row.price!=null?" · "+html(offerPrice(row)):"")+'</small></span>'+icon("chevron")+'</button>').join("")+'</div>':
        '<p class="af-offer-draft-empty">As ofertas que você preparar ficam salvas aqui.</p>')+'</details>';
  }

  function offers() {
    return '<div class="af-offers-workspace">'+
      '<header class="af-offers-heading"><div><span class="af-offers-eyebrow">Afiliados</span><h1>Ofertas</h1><p>Um link, a sua identidade e os destinos certos.</p></div>'+
        '<button type="button" class="os-button os-outline" data-act="demo">'+icon("eye")+'Ver exemplo</button></header>'+
      offerSource()+offerComposer()+offerDrafts()+'</div>';
  }

  function templatePage() {
    const template = state.template.find(t=>t.id===editTemplate) || state.template.find(t=>t.id===state.deal_template) || state.template[0];
    const name = editTemplate === "new" ? "" : template.name;
    const variables = ["produto","frase_ia","preco_destaque","preco","loja","preco_anterior","avaliacao_vendas","hashtags","desconto","cupom","condicoes","canal","observacao","link","publicidade"];
    return heading("Templates","Personalize a legenda e escolha o formato da imagem das suas ofertas.",
      btn("Novo template","new-template","",true)) + `<div class="af-sheet">
      ${card("Modelos salvos",`<div class="af-list">${state.template.map(t=>`<div class="af-item"><div><strong>${html(t.name)}</strong>
        <small>${t.media==="text"?"Somente texto":"Card e legenda"}</small></div>
        ${btn(t.id===editTemplate?"Selecionado":"Editar","edit-template",t.id)}</div>`).join("")}</div>
        <div class="af-row af-action-strip">${link("Ver prévia em Ofertas","ofertas","eye")}</div>`,"layers")}
      ${card(editTemplate==="new"?"Novo template":"Editor de template",`
        <form class="os-form af-form" data-form="template">
          <label>Nome do template <input name="name" required maxlength="80" value="${html(name)}"></label>
          <label>Marca no card <input name="brand" maxlength="32" placeholder="Ex.: PromoTech" value="${html(template.brand)}"></label>
          <div class="af-cols">${selectField("Layout da imagem","layout",`${["simple","discount","coupon"].map((key,i)=>option(key,["Oferta simples","Com desconto confirmado","Com cupom"][i],template.layout)).join("")}`)}
          ${selectField("Formato","media",`${option("card","Card e legenda",template.media)}${option("text","Somente texto",template.media)}`)}</div>
          ${selectField("Formatação","style",`${option("plain","Texto simples",template.style || "plain")}${option("whatsapp","Negrito, itálico e riscado",template.style)}`)}
          <label>Legenda da oferta <textarea name="text" required maxlength="1900">${html(template.text)}</textarea></label>
          <div><p class="af-copy">Toque em um campo para inserir na legenda. Linhas com dados indisponíveis saem automaticamente.</p>
          <div class="af-row af-template-tokens af-subtext">${variables.map(token=>btn("{"+token+"}","token",token)).join("")}</div></div>
          <div class="os-form-actions">${submit("Salvar template")}</div>
        </form>`,"edit","","af-rule-editor")}
    </div>`;
  }

  function ruleExecution(rule) {
    if(rule.activity?.last_execution)return rule.activity.last_execution;
    const matches=state.decision.filter(row=>row.rule_id===rule.id || (!row.rule_id&&row.rule===rule.name&&state.rule.filter(item=>item.name===rule.name).length===1));
    return matches.find(row=>row.executed_at)||matches[0];
  }

  function decisions() {
    const labels={prepared:"Ofertas preparadas",blocked:"Precisa de atenção",empty:"Sem oferta nesta busca",failed:"Falha na busca",missed:"Janela encerrada"};
    return state.decision.length?`<ol class="af-auto-activity">${state.decision.slice(0,6).map(row=>`<li data-automation-decision="${html(row.id)}">
      <span class="af-auto-activity-dot${["failed","blocked"].includes(row.status)?" is-warning":""}"></span><div><div class="af-auto-activity-head"><strong>${html(row.rule)}</strong>${row.executed_at?`<time>${html(date(row.executed_at,state.rule.find(rule=>rule.id===row.rule_id)?.timezone))}</time>`:""}</div>
      ${labels[row.status]?`<span class="af-auto-activity-status">${html(labels[row.status])}</span>`:""}<p>${html(row.reason)}</p>${row.niche?`<small>${html(state.niches.find(niche=>niche.id===row.niche)?.name||row.niche)}</small>`:""}</div></li>`).join("")}</ol>`:
      `<div class="af-auto-empty"><span>${icon("activity")}</span><h3>As próximas buscas aparecem aqui</h3><p>Acompanhe os produtos encontrados e o resultado de cada execução.</p></div>`;
  }

  function ruleDraftSummary() {
    if (!ruleDraft) return "";
    const destinations=automationDestinations().filter(row=>ruleDraft.destinations.includes(row.id));
    const selectedTemplate=state.template.find(row=>row.id===ruleDraft.template_id);
    const categories=ruleDraft.niches.length;
    const productText=categories?`${categories} ${categories===1?"categoria":"categorias"}${ruleDraft.keyword?" · "+ruleDraft.keyword:""}`:ruleDraft.keyword||"Escolha os produtos";
    return `<aside class="af-rule-summary" data-rule-summary aria-label="Resumo da automação"><span class="af-auto-eyebrow">Sua programação</span><h3>${html(ruleDraft.name||"Nova automação")}</h3>
      <dl><div><dt>${icon("box")}Produtos</dt><dd>${html(productText)}</dd></div>
      <div><dt>${icon("send")}Destinos</dt><dd>${html(destinations.map(row=>row.name).join(", ")||"Selecione os destinos")}</dd></div>
      <div><dt>${icon("clock")}Frequência</dt><dd>A cada ${html(ruleDraft.interval||"—")} min · até ${html(ruleDraft.daily||"—")}/dia por destino</dd><dd>${html(ruleWindow(ruleDraft))} · ${ruleDraft.days.length} ${ruleDraft.days.length===1?"dia":"dias"}/semana</dd></div>
      <div><dt>${icon("layers")}Publicação</dt><dd>${html(selectedTemplate?.name||"Selecione um modelo")}</dd><dd>${ruleDraft.mode==="automatic"?"Envio automático":"Revisão antes do envio"}</dd></div></dl>
      <p>${html(ruleDraft.timezone)}</p></aside>`;
  }

  function ruleEditor() {
    const rule=ruleDraft;
    if (!rule) return '<div data-automation-section="editor" hidden></div>';
    const input=(name,label,attributes="",type="text")=>`<label for="af-rule-${name}">${html(label)}<input id="af-rule-${name}" type="${type}" name="${name}" data-rule-field value="${html(rule[name]??"")}" ${attributes}></label>`;
    const available=automationDestinations();
    for(const ident of rule.destinations)if(!available.some(row=>row.id===ident))available.push({id:ident,name:"Destino indisponível",mode:"",eligible:false,reason:"Remova este destino ou configure a conexão novamente."});
    const eligible=available.filter(row=>row.eligible),chosen=rule.niches.length;
    const days=["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"];
    const timezones=[["America/Campo_Grande","Campo Grande"],["America/Cuiaba","Cuiabá"],["America/Sao_Paulo","Brasília / São Paulo"],["America/Manaus","Manaus"],["America/Rio_Branco","Rio Branco"],["America/Noronha","Fernando de Noronha"]];
    if(!timezones.some(([key])=>key===rule.timezone))timezones.push([rule.timezone,rule.timezone]);
    const selectedTemplate=state.template.find(row=>row.id===rule.template_id);
    const index=ruleSteps.findIndex(([key])=>key===ruleStep);
    const allDay=rule.opens==="00:00"&&rule.closes==="00:00";
    const panel=(key,number,title,description,body)=>`<section class="af-rule-panel" id="af-rule-panel-${key}" data-rule-panel="${key}" aria-labelledby="af-rule-${key}-title"${ruleStep===key?"":" hidden"}><header><span class="af-auto-eyebrow">Etapa ${number} de 4</span><h3 id="af-rule-${key}-title" tabindex="-1">${title}</h3><p>${description}</p></header>${body}</section>`;
    return `<section class="af-auto-panel af-rule-editor" data-automation-section="editor" aria-labelledby="af-rule-editor-title">
      <header class="af-auto-section-head"><div><span class="af-auto-eyebrow">${editRule==="new"?"Nova rotina":"Configuração"}</span><h2 id="af-rule-editor-title" tabindex="-1">${editRule==="new"?"Crie sua automação":"Editar automação"}</h2></div><button type="button" class="af-auto-text-button" data-act="close-rule" aria-disabled="${ruleSaveBusy}">${icon("close")}Fechar</button></header>
      <form class="os-form af-form" data-form="rule" novalidate aria-busy="${ruleSaveBusy}">
        <div class="af-rule-name">${input("name","Nome da automação",'required maxlength="80" placeholder="Ex.: Achados de tecnologia"')}</div>
        <nav class="af-rule-steps" aria-label="Etapas da automação">${ruleSteps.map(([key,label],position)=>`<button type="button" data-rule-step="${key}" aria-current="${key===ruleStep?"step":"false"}" aria-controls="af-rule-panel-${key}"><span>${position+1}</span>${label}</button>`).join("")}</nav>
        <div class="af-rule-editor-body"><div class="af-rule-fields">
        ${panel("products",1,"Quais produtos você quer publicar?","Busque na Shopee por categorias, por palavra-chave ou combine os dois.",`
          ${input("keyword","Palavra-chave",'maxlength="80" placeholder="Ex.: fone bluetooth, cafeteira"')}
          <fieldset class="af-rule-categories"><legend>Categorias</legend><div class="af-rule-choice-head"><label class="os-check"><input type="checkbox" data-select-niches${chosen===state.niches.length&&chosen?" checked":""}> Todas as categorias</label><span data-niche-count>${chosen} selecionadas</span></div>
          <label class="af-rule-search"><span class="os-sr-only">Buscar categoria</span>${icon("search")}<input type="search" data-niche-filter placeholder="Buscar categoria" value="${html(ruleNicheFilter)}"></label>
          <div class="af-rule-category-list">${state.niches.map(row=>`<label class="os-check af-rule-choice" data-niche-name="${html(row.name)}" data-rule-niche="${html(row.id)}"${normalize(row.name).includes(normalize(ruleNicheFilter))?"":" hidden"}><input type="checkbox" name="niches" data-rule-field value="${html(row.id)}"${rule.niches.includes(row.id)?" checked":""}><span>${html(row.name)}</span></label>`).join("")}</div>
          <p class="af-auto-muted" data-niche-empty${state.niches.some(row=>normalize(row.name).includes(normalize(ruleNicheFilter)))?" hidden":""}>Nenhuma categoria encontrada.</p></fieldset>
          ${selectField("Prioridade na busca","sort",Object.entries(state.sorts).map(([key,label])=>option(key,label,String(rule.sort))).join(""),"af-rule-sort")}
          <details class="af-rule-advanced" data-rule-disclosure="advanced"${ruleAdvancedOpen?" open":""}><summary>Refinar os produtos${ruleOptionalNumbers.some(key=>rule[key]!==""&&rule[key]!=null)||rule.blocked?" · filtros definidos":""}</summary><div>
          <div class="af-rule-field-pair">${input("min_price","Preço mínimo (R$)",'min="0" max="100000" step=".01"',"number")}${input("max_price","Preço máximo (R$)",'min="0" max="100000" step=".01"',"number")}</div>
          <div class="af-rule-field-pair">${input("min_rating","Avaliação mínima",'min="0" max="5" step=".1"',"number")}${input("min_commission","Comissão mínima (%)",'min="0" max="100" step=".1"',"number")}</div>
          <div class="af-rule-field-pair">${input("min_sales","Vendas mínimas",'min="0" max="1000000" step="1"',"number")}${input("min_discount","Desconto mínimo (%)",'min="0" max="99" step="1"',"number")}</div>
          ${input("blocked","Evitar palavras",'maxlength="700" placeholder="Separe por vírgula"')}</div></details>`)}
        ${panel("destinations",2,"Onde as ofertas devem aparecer?","Escolha até 5 destinos que já estão conectados à sua conta.",`
          <div class="af-rule-choice-head"><strong data-rule-destination-count>${rule.destinations.length} ${rule.destinations.length===1?"selecionado":"selecionados"}</strong>${eligible.length>0&&eligible.length<=5?`<label class="os-check"><input type="checkbox" data-rule-all-destinations${eligible.every(row=>rule.destinations.includes(row.id))?" checked":""}> Selecionar todos</label>`:""}</div>
          <div class="af-rule-destination-list">${available.map(row=>`<label class="af-rule-destination${row.eligible?"":" is-unavailable"}" data-rule-destination="${html(row.id)}"><input type="checkbox" name="destinations" data-rule-field value="${html(row.id)}"${rule.destinations.includes(row.id)?" checked":""}${row.eligible||rule.destinations.includes(row.id)?"":" disabled"}><span class="af-rule-destination-icon">${icon(row.mode==="telegram"?"send":"chat")}</span><span><strong>${html(row.name)}</strong><small>${html(defaultMedia(row.mode)||row.mode)}</small>${!row.eligible?`<small class="af-rule-destination-reason">${html(row.reason||"Verifique este destino em Conexões.")}</small>`:""}</span>${row.eligible?`<span class="af-rule-choice-check">${icon("check")}</span>`:""}</label>`).join("")||`<div class="af-auto-empty"><span>${icon("link")}</span><h3>Conecte seu primeiro destino</h3><p>Adicione um grupo ou canal do WhatsApp, ou um destino no Telegram.</p></div>`}</div>
          ${link("Gerenciar conexões","conexoes","link")}`)}
        ${panel("schedule",3,"Quando a automação deve funcionar?","Defina o ritmo das publicações e os horários permitidos.",`
          <div class="af-rule-field-pair">${input("interval","Intervalo em minutos",'required min="10" max="1440" step="1"',"number")}${input("daily","Limite diário por destino",'required min="1" max="48" step="1"',"number")}</div>
          <div class="af-rule-presets" aria-label="Intervalos sugeridos">${[30,60,90,180].map(value=>`<button type="button" data-rule-interval="${value}" aria-pressed="${String(rule.interval)===String(value)}">${value<60?value+" min":value===90?"1h30":value/60+" h"}</button>`).join("")}</div>
          <fieldset class="af-rule-days"><legend>Dias da semana</legend><div>${days.map((day,position)=>`<label><input type="checkbox" name="days" data-rule-field value="${position}"${rule.days.includes(position)?" checked":""}><span>${day}</span></label>`).join("")}</div><div class="af-rule-day-actions"><button type="button" data-rule-days="all">Todos os dias</button><button type="button" data-rule-days="weekdays">Dias úteis</button></div></fieldset>
          <div class="af-rule-choice-head"><strong>Horário de funcionamento</strong><label class="os-check"><input type="checkbox" data-rule-all-day${allDay?" checked":""}> Dia inteiro</label></div>
          <div class="af-rule-field-pair" data-rule-window${allDay?" hidden":""}>${input("opens","Das",'required',"time")}${input("closes","Até",'required',"time")}</div>
          ${selectField("Fuso horário","timezone",timezones.map(([value,label])=>option(value,label,rule.timezone)).join(""),"af-rule-timezone")}
          ${input("repeat_hours","Não repetir o mesmo produto por (horas)",'required min="1" max="720" step="1"',"number")}`)}
        ${panel("template",4,"Como você quer publicar?","Escolha o modelo da mensagem e revise sua programação.",`
          ${selectField("Modelo da publicação","template_id",templates(rule.template_id),"af-rule-template")}
          <div class="af-rule-template-preview" data-rule-template-summary>${icon("layers")}<span><strong>${html(selectedTemplate?.name||"Selecione um modelo")}</strong><small>${selectedTemplate?.media==="text"?"Somente texto":"Card e legenda"}${selectedTemplate?.brand?" · "+html(selectedTemplate.brand):""}</small></span></div>
          <fieldset class="af-rule-modes"><legend>Modo de publicação</legend>${[["automatic","Enviar automaticamente","Publica nos destinos escolhidos dentro da sua programação.","repeat"],["review","Revisar antes de enviar","As ofertas ficam na fila aguardando sua aprovação.","eye"]].map(([value,title,description,glyph])=>`<label class="af-rule-mode"><input type="radio" name="mode" data-rule-field value="${value}"${rule.mode===value?" checked":""}><span>${icon(glyph)}<strong>${title}</strong><small>${description}</small></span></label>`).join("")}</fieldset>`)}
        </div>${ruleDraftSummary()}</div>
        <div data-rule-feedback>${ruleError?`<p class="af-auto-alert" role="alert">${icon("info")}<span>${html(ruleError)}</span></p>`:""}</div>
        <footer class="af-rule-editor-actions"><div>${index?`<button type="button" class="os-button os-outline" data-rule-back>Voltar</button>`:`<button type="button" class="af-auto-text-button" data-act="close-rule" aria-disabled="${ruleSaveBusy}">Cancelar edição</button>`}</div><div>${index<3?`<button type="button" class="os-button" data-rule-next>Continuar${icon("arrow")}</button>`:
          `<button id="af-rule-save-paused" type="submit" class="os-button os-outline" name="intent" value="paused" aria-disabled="${ruleSaveBusy}">${ruleSaveBusy?"Salvando…":"Salvar pausada"}</button><button id="af-rule-save-active" type="submit" class="os-button" name="intent" value="active" aria-disabled="${ruleSaveBusy}" aria-busy="${ruleSaveBusy}">${ruleSaveBusy?'<span class="os-spinner" aria-hidden="true"></span>':icon("check")}${ruleSaveBusy?"Salvando…":"Salvar e ativar"}</button>`}</div></footer>
      </form></section>`;
  }

  function automationCard(rule) {
    const execution=ruleExecution(rule),runtime=rule.runtime;
    const jobs=state.jobs.filter(job=>job.doc?.rule?.id===rule.id);
    const activity=Number.isFinite(rule.activity?.published_today)?rule.activity:null;
    const published=activity?.published_today??jobs.filter(job=>job.status==="published").length;
    const approval=activity?.approval??jobs.filter(job=>job.status==="approval").length;
    const queued=activity?.pending??jobs.filter(job=>["queued","preparing","dispatching"].includes(job.status)).length;
    const failed=activity?(activity.failed||0)+(activity.unknown||0):jobs.filter(job=>attentionStates.includes(job.status)).length;
    const busy=ruleActionBusy.has(rule.id),blocked=runtime?.status==="blocked";
    const status=blocked?"Precisa de atenção":rule.active?"Ativa":"Pausada";
    const destinations=(rule.destinations||[]).map(ident=>state.destination.find(row=>row.id===ident)).filter(Boolean);
    const latest=execution?.executed_at?date(execution.executed_at,rule.timezone):execution?"Registrada sem horário":"Ainda não executada";
    return `<article class="af-auto-panel af-automation-card" data-automation-card="${html(rule.id)}"><header class="af-auto-card-head"><span class="af-auto-card-icon">${icon("repeat")}</span><div><h3>${html(rule.name)}</h3><span>${rule.mode==="automatic"?"Envio automático":"Revisão antes do envio"}</span></div>${pill(status,blocked?"warning":rule.active?"success":"neutral")}</header>
      <div class="af-auto-destinations">${destinations.map(row=>`<span>${icon(row.mode==="telegram"?"send":"chat")}${html(row.name)}</span>`).join("")||'<span>Nenhum destino configurado</span>'}</div>
      <div class="af-auto-schedule"><span>${icon("clock")}A cada ${html(rule.interval)} min</span><span>${icon("calendar")}${html(ruleWindow(rule))}</span><span>Até ${html(rule.daily)}/dia por destino</span></div>
      <dl class="af-auto-execution"><div><dt>Última busca</dt><dd>${html(latest)}</dd>${execution?`<small>${html(execution.reason)}</small>`:""}</div><div><dt>Próxima busca</dt><dd>${html(rule.active?(rule.next_at?date(rule.next_at,rule.timezone):"Sem horário definido"):"Automação pausada")}</dd><small>${html(rule.timezone)}</small></div></dl>
      ${blocked||runtime?.can_activate===false?`<p class="af-auto-alert">${icon("info")}<span>${html(runtime.reason||"Revise as conexões e os destinos desta automação.")}</span></p>`:""}
      ${activity||jobs.length?`<div class="af-auto-deliveries" aria-label="${activity?"Resultados da automação":"Envios recentes"}">${activity||published?`<span data-automation-kpi="published" class="${published?"is-success":""}">${icon("check")}${published} ${activity?"enviadas hoje":published===1?"publicada recentemente":"publicadas recentemente"}</span>`:""}${approval?`<span data-automation-kpi="approval">${approval} em revisão</span>`:""}${queued?`<span data-automation-kpi="pending">${queued} na fila</span>`:""}${failed?`<span data-automation-kpi="failed" class="is-warning">${failed} ${failed===1?"ocorrência":"ocorrências"}</span>`:""}</div>`:""}
      <footer><button type="button" class="os-button os-outline" data-act="edit-rule" data-value="${html(rule.id)}" aria-disabled="${ruleSaveBusy}">${icon("edit")}Editar</button><button type="button" class="os-button${rule.active?" os-outline":""}" data-act="${rule.active?"pause-rule":"start-rule"}" data-value="${html(rule.id)}" aria-disabled="${busy}" aria-busy="${busy}"${!rule.active&&runtime?.can_activate===false?" disabled":""}>${busy?'<span class="os-spinner" aria-hidden="true"></span>':icon(rule.active?"clock":"check")}${busy?"Atualizando…":rule.active?"Pausar":"Ativar"}</button></footer></article>`;
  }

  function automations() {
    const active=activeRules().length,blocked=state.rule.filter(rule=>rule.runtime?.status==="blocked").length;
    const upcoming=activeRules().filter(rule=>rule.next_at).sort((a,b)=>a.next_at-b.next_at)[0];
    const rules=state.rule.filter(rule=>ruleFilter==="all"||ruleFilter==="active"&&rule.active||ruleFilter==="paused"&&!rule.active);
    return `<div class="af-automations-workspace"><header class="af-auto-heading"><div><span class="af-auto-eyebrow">Afiliados</span><h1>Automações</h1><p>Suas ofertas, nos destinos certos, no ritmo que você escolhe.</p></div><div><button type="button" class="os-icon-button" data-act="refresh-automations" aria-label="Atualizar automações" aria-busy="${ruleRefreshBusy}" aria-disabled="${ruleRefreshBusy}">${icon("refresh")}</button>${btn("Nova automação","new-rule","",true)}</div></header>
      <div class="af-auto-overview" data-automation-section="overview"><div><span>${icon("repeat")}Programações ativas</span><strong>${active}<small>${blocked?blocked+" precisam de atenção":active===1?"automação ativa":"automações ativas"}</small></strong></div><div><span>${icon("clock")}Em pausa</span><strong>${state.rule.length-active}<small>salvas para retomar</small></strong></div><div><span>${icon("calendarClock")}Próxima busca</span><strong class="af-auto-next">${html(upcoming?date(upcoming.next_at,upcoming.timezone):"Sem programação")}<small>${html(upcoming?.name||"Crie sua primeira rotina")}</small></strong></div></div>
      ${ruleEditor()}
      <div data-automation-section="feedback">${ruleListError?`<p class="af-auto-alert" role="alert">${icon("info")}<span>${html(ruleListError)}</span></p>`:""}</div>
      <div class="af-auto-grid"><section class="af-auto-rules" data-automation-section="list" aria-labelledby="af-auto-list-title"><header class="af-auto-section-head"><h2 id="af-auto-list-title">Suas automações <span>${state.rule.length}</span></h2><div class="af-auto-filters" role="group" aria-label="Filtrar automações">${[["all","Todas"],["active","Ativas"],["paused","Pausadas"]].map(([key,label])=>`<button type="button" data-rule-filter="${key}" aria-pressed="${ruleFilter===key}">${label}</button>`).join("")}</div></header><div class="af-auto-rule-list">${rules.map(automationCard).join("")||`<div class="af-auto-panel af-auto-empty"><span>${icon("repeat")}</span><h3>${state.rule.length?"Nenhuma automação neste filtro":"Crie sua primeira automação"}</h3><p>${state.rule.length?"Escolha outro filtro para ver suas programações.":"Defina os produtos, os destinos e a frequência. O Orion cuida das próximas buscas."}</p>${!state.rule.length?btn("Começar agora","new-rule","",true):""}</div>`}</div></section>
      <section class="af-auto-panel af-auto-history" data-automation-section="history" aria-labelledby="af-auto-history-title"><header class="af-auto-section-head"><div><span class="af-auto-eyebrow">Acompanhamento</span><h2 id="af-auto-history-title">Últimas buscas</h2></div>${icon("activity")}</header>${decisions()}<a class="af-auto-text-button" href="#fila">Ver fila e publicações${icon("arrow")}</a></section></div></div>`;
  }

  function history() {
    const filters = [
      ["all","Todas",state.jobs.length],
      ["pending","Na fila",pending().length],
      ["published","Publicadas",state.jobs.filter(job => job.status==="published").length],
      ["attention","Com pendências",needingAttention().length],
    ];
    const rows = state.jobs.map(job=>{
      const dest = state.destination.find(d=>d.id===job.destination);
      const when = job.status==="published" ? job.changed : job.due;
      const provider = dest?.mode?.startsWith("whatsapp")?"WhatsApp":"Telegram";
      return `<tr data-history-status="${html(job.status)}" data-history-text="${html(normalize(job.doc.offer.title+" "+(dest?.name||"")))}">
        <th scope="row"><strong>${html(job.doc.offer.title)}</strong><small>${html(dest?.name || "Destino removido")}</small></th>
        <td>${jobPill(job)}${job.error||job.status==="published"&&job.receipt?`<small>${html(job.error || `Envio confirmado pelo ${provider}`)}</small>`:""}</td>
        <td>${date(when,job.doc.rule?.timezone)}</td>
        <td>${job.status==="approval"&&(job.doc?.automation?.mode==="review"||state.sending)?btn(job.doc?.automation?.mode==="review"?"Aprovar envio":"Aprovar teste","approve",job.id,true):""}
          ${["approval","queued","paused","expired","failed"].includes(job.status)?btn("Cancelar","cancel-job",job.id):""}</td>
      </tr>`;
    }).join("");
    return heading("Fila e histórico","Acompanhe os envios programados, publicações confirmadas e ocorrências.",btn("Atualizar","refresh")) +
      card("Publicações",`
        <div class="af-toolbar">
          <div class="af-search">${icon("search")}<label class="os-sr-only" for="filter-history">Buscar oferta ou destino</label>
            <input class="af-control" id="filter-history" type="search" placeholder="Buscar oferta ou destino" value="${html(historySearch)}"></div>
          <div class="af-status-tabs" role="group" aria-label="Situação da publicação">
            ${filters.map(([key,label,count])=>`<button class="os-button os-outline os-small" type="button" data-history-filter="${key}" aria-pressed="${key===historyFilter}">${label} · ${count}</button>`).join("")}</div>
        </div>
        <table class="af-table" id="history-table">
          <thead><tr><th scope="col">Oferta</th><th scope="col">Situação</th><th scope="col">Horário</th><th scope="col">Ações</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
        <div id="history-empty" class="af-history-empty" hidden>${empty(state.jobs.length?"Nenhuma publicação encontrada":"Nenhuma publicação registrada",
          state.jobs.length?"Limpe a busca ou selecione outra situação.":"As próximas ofertas preparadas aparecerão aqui.","fileCheck")}</div>
        <span id="history-count" class="os-sr-only" role="status"></span>`,"fileCheck");
  }

  function filterHistory() {
    const table = $("#history-table");
    if (!table) return;
    const allowed = {all:null,pending:pendingStates,published:["published"],attention:attentionStates}[historyFilter];
    const keyword = normalize(historySearch);
    let count = 0;
    for (const row of table.tBodies[0].rows) {
      row.hidden = !!(allowed && !allowed.includes(row.dataset.historyStatus)) || !row.dataset.historyText.includes(keyword);
      if (!row.hidden) count++;
    }
    table.hidden = count===0;
    $("#history-empty").hidden = count>0;
    $("#history-count").textContent = `${count} publicações exibidas`;
    for (const control of content.querySelectorAll("[data-history-filter]")) {
      control.setAttribute("aria-pressed",String(control.dataset.historyFilter===historyFilter));
    }
  }

  const views = {dashboard,plataformas:platforms,conexoes:connections,ofertas:offers,templates:templatePage,automacoes:automations,fila:history};

  // Reconcile only the Dashboard's rendered nodes. Stable keys keep controls,
  // chart dates and cards mounted during both user interactions and polling.
  function dashboardNodeKey(node) {
    if (node.nodeType!==1) return null;
    for (const name of ["id","data-automation-section","data-automation-card","data-automation-decision","data-rule-panel","data-rule-step","data-rule-niche","data-rule-destination","data-rule-filter","data-offer-section","data-offer-draft","data-offer-destination-row","data-offer-destination","data-offer-phrase","data-offer-tab","data-offer-product","data-offer-notice","data-offer-job","data-dashboard-date","data-dashboard-period","data-dashboard-metric","data-dashboard-publications","data-dashboard-card","data-dashboard-kpi","data-dashboard-step","data-dashboard-notice","data-act","aria-labelledby"]) {
      if (node.hasAttribute(name)) return `${node.nodeName}:${name}:${node.getAttribute(name)}`;
    }
    const name = node.getAttribute("class")?.split(/\s+/)[0];
    return name ? `${node.nodeName}:class:${name}` : null;
  }

  function patchDashboardNode(current, next) {
    if (current.isEqualNode(next)) return;
    if (current.nodeType!==1) {
      if (current.nodeValue!==next.nodeValue) current.nodeValue=next.nodeValue;
      return;
    }
    for (const attribute of [...current.attributes]) {
      if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
    }
    for (const attribute of next.attributes) {
      if (current.getAttribute(attribute.name)!==attribute.value) current.setAttribute(attribute.name,attribute.value);
    }
    const compatible = (a,b) => a && a.nodeType===b.nodeType && a.nodeName===b.nodeName && a.namespaceURI===b.namespaceURI;
    const counts=nodes=>{
      const result=new Map();
      for (const node of nodes) {
        const key=dashboardNodeKey(node);
        if (key) result.set(key,(result.get(key)||0)+1);
      }
      return result;
    };
    const before=counts(current.childNodes);
    const after=counts(next.childNodes);
    const keyFor=node=>{
      const key=dashboardNodeKey(node);
      return (before.get(key)||0)<=1 && (after.get(key)||0)<=1 ? key : null;
    };
    const keyed = new Map([...current.childNodes].map(node=>[keyFor(node),node]).filter(([key])=>key));
    const wanted = new Set([...next.childNodes].map(keyFor).filter(Boolean));
    for (const [key,node] of keyed) if (!wanted.has(key)) node.remove();
    let cursor = current.firstChild;
    for (const desired of [...next.childNodes]) {
      const key = keyFor(desired);
      let node = key ? keyed.get(key) : cursor && !keyFor(cursor) && compatible(cursor,desired) ? cursor : null;
      if (!compatible(node,desired)) node=desired.cloneNode(true);
      if (node!==cursor) current.insertBefore(node,cursor);
      patchDashboardNode(node,desired);
      cursor=node.nextSibling;
    }
    while (cursor) {
      const obsolete=cursor;
      cursor=cursor.nextSibling;
      obsolete.remove();
    }
  }

  function updateDashboard(selector, markup) {
    const current = content.querySelector(selector);
    if (!current) return;
    const template=document.createElement("template");
    template.innerHTML=markup;
    const next=template.content.firstElementChild;
    if (!next) return;
    const root=content.querySelector(".af-dashboard");
    const focused=root.contains(document.activeElement)?document.activeElement:null;
    const visible=node=>{
      const rect=node.getBoundingClientRect();
      return rect.height>0 && rect.bottom>0 && rect.top<window.innerHeight;
    };
    const anchor=selector!==".af-dashboard" && visible(current)?current:
      focused && visible(focused)?focused:[...root.querySelectorAll(".af-dashboard-toolbar, section, .af-dashboard-heading")].find(visible);
    const top=anchor?.getBoundingClientRect().top;
    const scrollX=window.scrollX;
    const scrollY=window.scrollY;
    patchDashboardNode(current,next);
    // A date can leave the selected range. Restore keyboard focus locally only
    // when its focused point actually disappeared, never on background updates.
    if (focused && !focused.isConnected && focused.hasAttribute("data-dashboard-point")) {
      content.querySelector('[data-dashboard-point][aria-pressed="true"]')?.focus({preventScroll:true});
    }
    const targetY=anchor?.isConnected?window.scrollY+anchor.getBoundingClientRect().top-top:scrollY;
    if (Math.abs(window.scrollY-targetY)>.5) window.scrollTo({left:scrollX,top:targetY,behavior:"instant"});
  }

  function updateDashboardBusy() {
    if (route!=="dashboard" || controller.signal.aborted) return;
    const busy=stateLoading || analyticsLoading;
    const syncing=analyticsCache.get(analyticsPeriod)?.sources?.sales?.status==="syncing" && analyticsPollAttempts<analyticsPollDelays.length;
    const button=content.querySelector('[data-act="refresh"]');
    if (button) {
      button.disabled=busy;
      button.setAttribute("aria-busy",String(busy || syncing));
    }
    content.querySelector(".af-dashboard-overview")?.setAttribute("aria-busy",String(busy));
  }


  function offerCurrent(version, origin=viewVersion) {
    return !controller.signal.aborted && route==="ofertas" && version===navVersion && origin===viewVersion;
  }

  function saveOfferRow(row) {
    if (!row?.id) return;
    const index=state.offer.findIndex(item=>item.id===row.id);
    if (index<0) state.offer.unshift(row);
    else state.offer[index]=row;
  }

  function resetOfferSend() {
    cancelOfferSendPoll(true);
    offerSendIntent=null;
    offerSendResult=null;
    offerSendError="";
  }

  function cancelOfferSendPoll(reset=false) {
    if (offerSendPollTimer!==null) window.clearTimeout(offerSendPollTimer);
    offerSendPollTimer=null;
    offerSendPollRequest?.abort();
    offerSendPollRequest=null;
    if (reset) offerSendPollAttempts=0;
  }

  function scheduleOfferSendPoll() {
    if (offerSendPollTimer!==null||offerSendPollRequest||document.hidden||route!=="ofertas"||
        controller.signal.aborted||offerSendPollAttempts>=offerSendPollDelays.length||
        !offerSendResult?.jobs?.some(job=>["queued","preparing","dispatching"].includes(job.status))) return;
    const key=opened,origin=viewVersion,requestId=offerSendIntent?.payload.request_id;
    if (!requestId) return;
    offerSendPollTimer=window.setTimeout(async()=>{
      offerSendPollTimer=null;
      if (route!=="ofertas"||origin!==viewVersion||opened!==key||controller.signal.aborted) return;
      offerSendPollAttempts++;
      const pendingRequest=new AbortController();
      offerSendPollRequest=pendingRequest;
      try {
        const result=await api("send-batches/"+encodeURIComponent(requestId),"GET",undefined,pendingRequest.signal);
        if (route!=="ofertas"||origin!==viewVersion||opened!==key||controller.signal.aborted||
            requestId!==offerSendIntent?.payload.request_id) return;
        offerSendResult=result;
        offerSendError="";
        render();
      } catch(error) {
        if (!controller.signal.aborted&&origin===viewVersion&&opened===key&&error.name!=="AbortError") {
          offerSendError="Não foi possível atualizar os envios. Confira o andamento na fila.";
          render();
        }
      } finally {
        if (offerSendPollRequest===pendingRequest) {
          offerSendPollRequest=null;
          scheduleOfferSendPoll();
        }
      }
    },offerSendPollDelays[offerSendPollAttempts]);
  }

  function setOfferDetails(row) {
    offerDetails={title:row?.title||"",price:row?.currency==="BRL"?offerPriceInput(row?.price):"",image:row?.image||"",affiliate_url:row?.affiliate_url||""};
    offerDetailsDirty=false;
  }

  function setOfferSuggestions(rows) {
    const phrase=currentOffer()?.phrase;
    const unique=new Map((Array.isArray(rows)?rows:[]).filter(row=>row?.text).map((row,index)=>[row.text,{...row,id:String(row.id||index)}]));
    if (phrase && !unique.has(phrase)) unique.set(phrase,{id:"current",text:phrase,source:currentOffer()?.phrase_source});
    const previous=offerSuggestions.map(row=>row.text);
    offerSuggestions=[...previous.map(text=>unique.get(text)).filter(Boolean),
      ...[...unique.values()].filter(row=>!previous.includes(row.text))];
  }

  function offerCanSend(ignoreSending=false) {
    return preview?.send?.enabled===true && !!preview?.send?.preview_token && !offerPreviewBusy &&
      !offerPhraseBusy && !offerDetailsBusy && !offerDetailsDirty &&
      offerPhraseValue===(currentOffer()?.phrase||"") && (ignoreSending||!offerSendBusy);
  }

  function syncOfferControls() {
    if (route!=="ofertas") return;
    for (const field of content.querySelectorAll("[data-offer-input]")) {
      const value=offerInput[field.dataset.offerInput]||"";
      if (field.value!==value) field.value=value;
    }
    for (const field of content.querySelectorAll("[data-offer-detail]")) {
      const value=offerDetails[field.dataset.offerDetail]||"";
      if (field.value!==value) field.value=value;
      field.disabled=offerDetailsBusy||offerSendBusy;
    }
    const phrase=content.querySelector("[data-offer-phrase-input]");
    if (phrase) {
      if (phrase.value!==offerPhraseValue) phrase.value=offerPhraseValue;
      phrase.disabled=offerPhraseBusy||offerSendBusy;
    }
    const selected=content.querySelector("#preview-template");
    if (selected) { selected.value=chosenTemplate;selected.disabled=offerSendBusy; }
    const allowed=new Set(eligibleOfferDestinations().map(row=>row.id));
    for (const field of content.querySelectorAll("[data-offer-destination]")) {
      field.checked=allowed.has(field.value)&&offerDestinations.has(field.value);
      field.disabled=!allowed.has(field.value)||offerSendBusy;
    }
    const count=[...offerDestinations].filter(key=>allowed.has(key)).length;
    const all=content.querySelector("[data-offer-all]");
    if (all) {
      all.checked=allowed.size>0&&count===allowed.size;
      all.indeterminate=count>0&&count<allowed.size;
      all.disabled=!allowed.size||offerSendBusy;
    }
    const send=content.querySelector('[data-act="offer-send"]');
    if (send) send.disabled=!offerCanSend(true)||!count;
    for (const button of content.querySelectorAll("[data-offer-phrase]")) {
      button.disabled=offerSendBusy;
      button.setAttribute("aria-disabled",String(offerPhraseBusy||offerSendBusy));
    }
    const suggestions=content.querySelector('[data-act="offer-phrases"]');
    if (suggestions) suggestions.disabled=offerSendBusy;
  }

  function updateOffers(selector=".af-offers-workspace", markup=offers()) {
    const root=content.querySelector(".af-offers-workspace");
    const current=root?.matches(selector)?root:root?.querySelector(selector);
    if (!current) return;
    const template=document.createElement("template");
    template.innerHTML=markup;
    const next=template.content.firstElementChild;
    if (!next) return;
    const focused=root.contains(document.activeElement)?document.activeElement:null;
    const visible=node=>{
      const rect=node.getBoundingClientRect();
      return rect.height>0 && rect.bottom>56 && rect.top<window.innerHeight;
    };
    const anchor=selector!==".af-offers-workspace"&&visible(current)?current:
      focused&&visible(focused)?focused:[...root.querySelectorAll("[data-offer-section], .af-offers-heading")].find(visible);
    const top=anchor?.getBoundingClientRect().top;
    const x=window.scrollX,y=window.scrollY;
    patchDashboardNode(current,next);
    syncOfferControls();
    if (focused?.isConnected && document.activeElement!==focused && !focused.disabled) focused.focus({preventScroll:true});
    const target=anchor?.isConnected?window.scrollY+anchor.getBoundingClientRect().top-top:y;
    if (Math.abs(window.scrollY-target)>.5) window.scrollTo({left:x,top:target,behavior:"instant"});
    window.OrionFields?.refresh(current);
  }

  function syncRuleControls() {
    for(const button of content.querySelectorAll('[data-act="start-rule"]')) {
      const rule=state.rule.find(row=>row.id===button.dataset.value);
      button.disabled=rule?.runtime?.can_activate===false;
    }
    const form=content.querySelector('[data-form="rule"]');
    if (!form||!ruleDraft) return;
    form.setAttribute('aria-busy',String(ruleSaveBusy));
    for (const field of form.querySelectorAll('[data-rule-field],select[name]')) {
      const name=field.name;
      if (!(name in ruleDraft)) continue;
      if (field.type==="checkbox") field.checked=ruleDraft[name].includes(name==="days"?Number(field.value):field.value);
      else if (field.type==="radio") field.checked=ruleDraft[name]===field.value;
      else if (field.value!==String(ruleDraft[name]??"")) field.value=ruleDraft[name]??"";
    }
    const all=form.querySelector('[data-select-niches]'),items=[...form.querySelectorAll('input[name="niches"]')];
    const selected=items.filter(field=>field.checked).length;
    if (all) {all.checked=items.length>0&&selected===items.length;all.indeterminate=selected>0&&selected<items.length;}
    const count=form.querySelector('[data-niche-count]');
    if(count)count.textContent=ruleDraft.niches.length+' selecionadas';
    const allDestinations=form.querySelector('[data-rule-all-destinations]'),available=ruleDestinations();
    if(allDestinations){allDestinations.checked=available.length>0&&available.every(row=>ruleDraft.destinations.includes(row.id));allDestinations.indeterminate=ruleDraft.destinations.length>0&&!allDestinations.checked;}
    const destinations=form.querySelector('[data-rule-destination-count]');
    if(destinations)destinations.textContent=ruleDraft.destinations.length+(ruleDraft.destinations.length===1?' selecionado':' selecionados');
    for (const panel of form.querySelectorAll('[data-rule-panel]')) panel.hidden=panel.dataset.rulePanel!==ruleStep;
    for (const button of form.querySelectorAll('[data-rule-step]')) button.setAttribute('aria-current',button.dataset.ruleStep===ruleStep?'step':'false');
    for (const button of form.querySelectorAll('[data-rule-interval]')) button.setAttribute('aria-pressed',String(button.dataset.ruleInterval===String(ruleDraft.interval)));
    for (const row of form.querySelectorAll('[data-niche-name]')) row.hidden=!normalize(row.dataset.nicheName).includes(normalize(ruleNicheFilter));
    const empty=form.querySelector('[data-niche-empty]');
    if(empty)empty.hidden=[...form.querySelectorAll('[data-niche-name]')].some(row=>!row.hidden);
  }

  function updateAutomations(selector='.af-automations-workspace',markup=automations()) {
    const root=content.querySelector('.af-automations-workspace');
    const current=root?.matches(selector)?root:root?.querySelector(selector);
    if(!current)return;
    const template=document.createElement('template');template.innerHTML=markup;
    const next=template.content.firstElementChild;if(!next)return;
    const focused=root.contains(document.activeElement)?document.activeElement:null;
    const visible=node=>{const rect=node.getBoundingClientRect();return rect.height>0&&rect.bottom>56&&rect.top<window.innerHeight;};
    const anchor=focused&&visible(focused)?focused:[...root.querySelectorAll('[data-automation-section],.af-auto-heading')].find(visible);
    const top=anchor?.getBoundingClientRect().top,x=window.scrollX,y=window.scrollY;
    const selection=focused&&typeof focused.selectionStart==='number'?[focused.selectionStart,focused.selectionEnd,focused.selectionDirection]:null;
    patchDashboardNode(current,next);syncRuleControls();
    if(focused?.isConnected&&!focused.disabled){
      if(document.activeElement!==focused)focused.focus({preventScroll:true});
      if(selection)focused.setSelectionRange(...selection);
    }
    const target=anchor?.isConnected?window.scrollY+anchor.getBoundingClientRect().top-top:y;
    if(Math.abs(window.scrollY-target)>.5)window.scrollTo({left:x,top:target,behavior:'instant'});
    window.OrionFields?.refresh(current);
  }

  function changeRuleDraft(field,refresh=true) {
    if(!ruleDraft||!field.name||!(field.name in ruleDraft))return;
    const key=field.name;
    let value=field.value;
    if(field.type==='checkbox'){
      const selected=new Set(ruleDraft[key]),item=key==='days'?Number(value):value;
      if(field.checked)selected.add(item);else selected.delete(item);
      value=[...selected];
    }else if(field.type==='radio'&&!field.checked)return;
    if(JSON.stringify(ruleDraft[key])===JSON.stringify(value))return;
    ruleDraft[key]=value;ruleDraftRevision++;ruleError='';
    if(refresh){updateAutomations('[data-rule-summary]',ruleDraftSummary());updateAutomations('[data-rule-feedback]','<div data-rule-feedback></div>');}
  }

  function openRuleEditor(ident) {
    if(ruleSaveBusy)return;
    const saved=state.rule.find(row=>row.id===ident);
    const defaults={name:'',keyword:'',niches:[],sort:'2',blocked:'',min_price:'',max_price:'',min_rating:'',min_commission:'',min_sales:'',min_discount:'',
      destinations:[],template_id:state.deal_template||state.template[0]?.id||'default',days:[0,1,2,3,4,5,6],opens:'09:00',closes:'21:00',timezone:'America/Campo_Grande',interval:'90',daily:'8',repeat_hours:'48',mode:'automatic'};
    ruleDraft=Object.fromEntries(Object.entries(defaults).map(([key,value])=>[key,saved?.[key]??value]));
    ruleDraft.blocked=Array.isArray(ruleDraft.blocked)?ruleDraft.blocked.join(', '):ruleDraft.blocked;
    for(const key of ['niches','destinations','days'])ruleDraft[key]=[...ruleDraft[key]];
    ruleWindowBeforeAllDay=ruleDraft.opens==='00:00'&&ruleDraft.closes==='00:00'?{opens:'09:00',closes:'21:00'}:{opens:ruleDraft.opens,closes:ruleDraft.closes};
    editRule=saved?.id||'new';ruleDraftKey=saved?.id||id();ruleEditorVersion++;ruleDraftRevision=0;
    ruleNicheFilter='';ruleAdvancedOpen=false;ruleStep='products';ruleError='';
    render();focusSection('.af-rule-editor');
  }

  function validateRule(step=null) {
    const form=content.querySelector('[data-form="rule"]');
    if(!ruleDraft||!form)return false;
    const fail=(message,section,field)=>{
      ruleError=message;ruleStep=section;render();
      content.querySelector(field||'[data-rule-feedback]')?.focus({preventScroll:true});return false;
    };
    if(!ruleDraft.name.trim())return fail('Dê um nome para esta automação.','products','#af-rule-name');
    if((!step||step==='products')&&!ruleDraft.keyword.trim()&&!ruleDraft.niches.length)return fail('Escolha uma categoria ou informe uma palavra-chave.','products','#af-rule-keyword');
    if(!step||step==='destinations'){
      if(!ruleDraft.destinations.length)return fail('Selecione pelo menos um destino.','destinations','#af-rule-destinations-title');
      if(ruleDraft.destinations.length>5)return fail('Selecione até 5 destinos por automação.','destinations','#af-rule-destinations-title');
      const available=new Set(ruleDestinations().map(row=>row.id));
      if(ruleDraft.destinations.some(key=>!available.has(key)))return fail('Um dos destinos selecionados precisa ser verificado em Conexões. Revise sua seleção.','destinations','#af-rule-destinations-title');
    }
    if(!step||step==='schedule'){
      if(!ruleDraft.days.length)return fail('Escolha pelo menos um dia da semana.','schedule','#af-rule-schedule-title');
      if(!/^\d{2}:\d{2}$/.test(ruleDraft.opens)||!/^\d{2}:\d{2}$/.test(ruleDraft.closes)||!(ruleDraft.opens==='00:00'&&ruleDraft.closes==='00:00')&&ruleDraft.opens>=ruleDraft.closes)
        return fail('O horário final precisa ser depois do inicial. Use Dia inteiro para funcionar 24 horas.','schedule','#af-rule-opens');
      try{new Intl.DateTimeFormat('pt-BR',{timeZone:ruleDraft.timezone}).format();}catch{return fail('Selecione um fuso horário válido.','schedule','#af-rule-timezone');}
    }
    if(!step||step==='products'){
      const blocked=ruleDraft.blocked.split(',').map(value=>value.trim()).filter(Boolean);
      if(blocked.length>20||blocked.some(value=>value.length>60)){ruleAdvancedOpen=true;return fail('Use até 20 palavras bloqueadas, com no máximo 60 caracteres cada.','products','#af-rule-blocked');}
      if(ruleDraft.min_price!==''&&ruleDraft.max_price!==''&&Number(ruleDraft.min_price)>Number(ruleDraft.max_price)){ruleAdvancedOpen=true;return fail('O preço máximo precisa ser maior ou igual ao mínimo.','products','#af-rule-max_price');}
    }
    for(const field of form.querySelectorAll('input[name],select[name]')){
      const section=field.closest('[data-rule-panel]')?.dataset.rulePanel||'products';
      if(step&&section!==step)continue;
      if(!field.disabled&&!field.checkValidity()){
        if(field.closest('[data-rule-disclosure]'))ruleAdvancedOpen=true;
        return fail('Confira o valor de '+(form.querySelector('label[for="'+CSS.escape(field.id)+'"]')?.firstChild.textContent||field.name)+'.',section,field.id?'#'+CSS.escape(field.id):null);
      }
    }
    if((!step||step==='template')&&!state.template.some(row=>row.id===ruleDraft.template_id))return fail('Escolha um modelo de publicação disponível.','template','#af-rule-template');
    return true;
  }

  function setRuleStep(next,validate=false) {
    if(!ruleDraft||!ruleSteps.some(([key])=>key===next))return;
    if(validate&&!validateRule(ruleStep))return;
    ruleStep=next;ruleError='';render();
    content.querySelector('#af-rule-'+next+'-title')?.focus({preventScroll:true});
  }

  function saveRuleRow(row) {
    // A read started before this mutation must not restore an older schedule.
    stateVersion++;stateLoading=false;
    const index=state.rule.findIndex(item=>item.id===row.id);
    const previous=index>=0?state.rule[index]:{};
    const saved={...previous,...row};
    if(!row.runtime)delete saved.runtime;
    if(index>=0)state.rule[index]=saved;else state.rule.unshift(saved);
  }

  async function saveAutomation(activate=false) {
    if(ruleSaveBusy||!ruleDraft||ruleActionBusy.size)return;
    const form=content.querySelector('[data-form="rule"]');
    for(const field of form.querySelectorAll('[data-rule-field],select[name]'))changeRuleDraft(field,false);
    if(!validateRule())return;
    const value={...ruleDraft,days:[...ruleDraft.days],destinations:[...ruleDraft.destinations],niches:[...ruleDraft.niches],blocked:ruleDraft.blocked.split(',').map(word=>word.trim()).filter(Boolean)};
    for(const key of ruleNumberFields)value[key]=Number(value[key]);
    for(const key of ruleOptionalNumbers)value[key]=value[key]===''?null:Number(value[key]);
    const key=ruleDraftKey,revision=ruleDraftRevision,editorVersion=ruleEditorVersion;
    let stored=false;
    ruleSaveBusy=true;ruleError='';render();
    try{
      const saved=await api('rules/'+encodeURIComponent(key),'PUT',value);
      if(controller.signal.aborted)return;
      stored=true;saveRuleRow(saved);
      if(editorVersion===ruleEditorVersion)editRule=saved.id;
      if(activate){
        const active=await api('rules/'+encodeURIComponent(saved.id)+'/activate','POST',{active:true});
        if(controller.signal.aborted)return;
        saveRuleRow(active);
      }
      if(editorVersion===ruleEditorVersion&&revision===ruleDraftRevision){editRule=null;ruleDraft=null;ruleDraftKey=null;ruleEditorVersion++;}
      else if(editorVersion===ruleEditorVersion)ruleError='A programação foi salva. As alterações feitas durante o salvamento ainda estão em edição.';
      notify(activate?'Automação salva e ativada.':'Automação salva e pausada.');
    }catch(error){
      if(!controller.signal.aborted&&editorVersion===ruleEditorVersion)ruleError=(stored&&activate?'Configuração salva e pausada. Não foi possível ativar: ':'')+(error.message||'Não foi possível salvar esta automação.');
    }finally{
      ruleSaveBusy=false;
      if(!controller.signal.aborted&&route==='automacoes')render();
    }
  }

  function render() {
    if (!state || controller.signal.aborted) return;
    if (route==="dashboard" && content.querySelector(".af-dashboard")) {
      updateDashboard(".af-dashboard",dashboard());
      return;
    }
    if (route==="automacoes" && content.querySelector(".af-automations-workspace")) {
      updateAutomations();
      return;
    }
    if (route==="ofertas" && content.querySelector(".af-offers-workspace")) {
      updateOffers();
      return;
    }
    content.innerHTML = views[route]();
    if (route==="ofertas") syncOfferControls();
    if (route==="automacoes") syncRuleControls();
    const all = $("[data-select-niches]");
    if (all) all.indeterminate = !all.checked && !!all.form.querySelector("input[name=niches]:checked");
    filterHistory();
    window.OrionFields?.refresh(content);
  }

  function cancelAnalyticsPoll(reset=false) {
    if (analyticsPollTimer!==null) window.clearTimeout(analyticsPollTimer);
    analyticsPollTimer = null;
    if (reset) analyticsPollAttempts = 0;
  }

  function scheduleAnalyticsPoll(version) {
    cancelAnalyticsPoll();
    if (controller.signal.aborted || route!=="dashboard" || document.hidden || version!==analyticsVersion || analyticsPollAttempts>=analyticsPollDelays.length) return;
    if (analyticsCache.get(analyticsPeriod)?.sources?.sales?.status!=="syncing") return;
    analyticsPollTimer = window.setTimeout(()=>{
      analyticsPollTimer = null;
      if (controller.signal.aborted || route!=="dashboard" || document.hidden || version!==analyticsVersion) return;
      analyticsPollAttempts++;
      void loadAnalytics({poll:true});
    },analyticsPollDelays[analyticsPollAttempts]);
  }

  function retainAnalyticsValues(cached, incoming) {
    return {...cached,sources:{...cached.sources,sales:{...cached.sources.sales,...incoming.sources.sales,updated_at:incoming.sources.sales.updated_at ?? cached.sources.sales.updated_at}}};
  }

  async function loadAnalytics({poll=false}={}) {
    if (!state || controller.signal.aborted) return;
    cancelAnalyticsPoll(!poll);
    const period = analyticsPeriod;
    const version = ++analyticsVersion;
    analyticsRequest?.abort();
    const requestController=new AbortController();
    analyticsRequest=requestController;
    analyticsLoading = true;
    analyticsError = false;
    if (route==="dashboard") render();
    try {
      const result = await api("analytics?period="+encodeURIComponent(period),"GET",undefined,requestController.signal);
      if (controller.signal.aborted || version!==analyticsVersion || period!==analyticsPeriod) return;
      if (!result || !result.summary || !result.sources || result.period?.key!==period) throw new Error("Relatório indisponível.");
      const cached = analyticsCache.get(period);
      const status = result.sources.sales?.status;
      const preserve = dashboardHasFinancialData(cached) && (status==="error" || ["syncing","partial"].includes(status) && !dashboardHasFinancialData(result));
      analyticsCache.set(period,preserve?retainAnalyticsValues(cached,result):result);
      analyticsDisplayedPeriod=period;
      analyticsError = status==="error";
    } catch (error) {
      if (controller.signal.aborted || version!==analyticsVersion || period!==analyticsPeriod) return;
      analyticsError = true;
      const cached = analyticsCache.get(period);
      if (cached) analyticsCache.set(period,retainAnalyticsValues(cached,{sources:{sales:{status:"error"}}}));
    } finally {
      if (!controller.signal.aborted && version===analyticsVersion && period===analyticsPeriod) {
        analyticsLoading = false;
        analyticsRequest=null;
        if (route==="dashboard") render();
        scheduleAnalyticsPoll(version);
      }
    }
  }

  function selectDashboardPoint(index, focus=false) {
    const model = dashboardChartModel();
    if (!model.points.length) return;
    const selectedIndex = Math.min(Math.max(Number(index)||0,0),model.points.length-1);
    const point = model.points[selectedIndex];
    chartSelectedDate=point.date;
    const dateNode = content.querySelector("[data-dashboard-point-date]");
    const valueNode = content.querySelector("[data-dashboard-point-value]");
    if (dateNode) dateNode.textContent = dashboardDay(point.date);
    if (valueNode) valueNode.textContent = model.config.currency?dashboardMoney(point.value):dashboardCount(point.value);
    for (const button of content.querySelectorAll("[data-dashboard-point]")) {
      const selected = Number(button.dataset.dashboardPoint)===selectedIndex;
      button.setAttribute("aria-pressed",String(selected));
      button.tabIndex = selected?0:-1;
      if (selected && focus) button.focus({preventScroll:true});
    }
    for (const dot of content.querySelectorAll("[data-chart-dot]")) dot.setAttribute("r",Number(dot.dataset.chartDot)===selectedIndex?"4.5":"2.4");
    for (const step of content.querySelectorAll("[data-dashboard-step]")) step.disabled = Number(step.dataset.dashboardStep)<0?selectedIndex===0:selectedIndex===model.points.length-1;
  }

  async function reload() {
    const version=++stateVersion;
    const viewAtStart=viewVersion;
    const initial=!state;
    stateLoading=true;
    updateDashboardBusy();
    try {
      const result = await api("state");
      if (controller.signal.aborted || version!==stateVersion || !initial && viewAtStart!==viewVersion) return false;
      const identity = connection => JSON.stringify([connection?.status,connection?.version || connection?.account,connection?.verified]);
      if (state && identity(connected("shopee"))!==identity(result.connection?.find(row=>row.id==="shopee"))) {
        cancelAnalyticsPoll(true);
        analyticsRequest?.abort();
        analyticsVersion++;
        analyticsCache.clear();
        analyticsDisplayedPeriod=null;
        analyticsError = false;
        analyticsLoading = false;
        chartSelectedDate = null;
      }
      state = result;
      if (!chosenTemplate) chosenTemplate=state.deal_template || "default";
      render();
      if (route==="dashboard") await loadAnalytics();
      return true;
    } catch (error) {
      if (controller.signal.aborted || version!==stateVersion || !initial && viewAtStart!==viewVersion) return false;
      throw error;
    } finally {
      if (version===stateVersion) {
        stateLoading=false;
        updateDashboardBusy();
      }
    }
  }

  function showError(error) {
    if (controller.signal.aborted) return;
    content.innerHTML = '<section class="os-card"><p class="os-notice os-error" role="alert">'+
      html(error.message || "O painel está indisponível.")+'</p><div class="os-form-actions">'+
      '<button type="button" class="os-button os-outline" data-af-retry>Tentar novamente</button></div></section>';
    content.querySelector("[data-af-retry]").addEventListener("click", () => {
      content.innerHTML = loading();
      void reload().catch(showError);
    }, {once:true});
  }
  function focusSection(selector) {
    const section = $(selector);
    if (!section) return;
    const header = section.querySelector("h2");
    header.tabIndex = -1;
    header.focus({preventScroll:true});
    header.scrollIntoView({block:"nearest"});
  }


  async function openOffer(value, focus=true) {
    if (route!=="ofertas") await open("ofertas");
    const origin=viewVersion;
    const changed=opened!==value;
    const beforeFocus=document.activeElement;
    opened=value;
    if (changed) {
      preview=null;
      offerSuggestions=[];
      offerDestinations=new Set();
      offerPreviewExpanded=false;
      offerPhraseOpen=false;
      offerDetailsError="";
      const row=currentOffer();
      setOfferDetails(row);
      offerDetailsOpen=!!row&&(!row.title||row.price==null||row.currency!=="BRL");
      offerPhraseValue=row?.phrase||"";
      resetOfferSend();
    }
    const version=++navVersion;
    offerPreviewRequest?.abort();
    const pendingRequest=new AbortController();
    offerPreviewRequest=pendingRequest;
    offerPreviewBusy=true;
    offerPreviewError="";
    render();
    try {
      const response=await api("offers/"+encodeURIComponent(value)+"/preview?template="+encodeURIComponent(chosenTemplate),"GET",undefined,pendingRequest.signal);
      if (!offerCurrent(version,origin)) return;
      const phraseEdited=offerPhraseValue!==(currentOffer()?.phrase||"");
      preview=response;
      saveOfferRow(response.offer);
      if (!offerDetailsDirty) setOfferDetails(currentOffer());
      if (!phraseEdited) offerPhraseValue=currentOffer()?.phrase||"";
      setOfferSuggestions(response.suggestions);
      const allowed=new Set(eligibleOfferDestinations().map(row=>row.id));
      offerDestinations=new Set([...offerDestinations].filter(key=>allowed.has(key)));
    } catch(error) {
      if (!offerCurrent(version,origin)||error.name==="AbortError") return;
      offerPreviewError=error.message||"Não foi possível carregar a prévia. Tente novamente.";
    } finally {
      if (offerCurrent(version,origin)) {
        offerPreviewBusy=false;
        offerPreviewRequest=null;
        render();
        if (focus && changed && (document.activeElement===beforeFocus || document.activeElement===document.body)) {
          const title=content.querySelector("#af-offer-composer-title");
          title?.focus({preventScroll:true});
          title?.scrollIntoView({block:"nearest",behavior:"instant"});
        }
      }
    }
  }

  async function prepareOffer(url,note="",allowManual=false) {
    if (offerBusy) return;
    // Keep the exact human input as the recovery binding. The API returns a
    // normalised source_url, which can differ from a copied share message.
    const attempt={url:String(url||""),note:String(note||"")};
    if (allowManual && offerFailure?.url!==attempt.url) return;
    const origin=viewVersion;
    const version=navVersion;
    offerInput={...attempt};
    if (offerFailure?.url!==attempt.url) offerFailure=null;
    if (offerLinkChoices?.input!==attempt.url) offerLinkChoices=null;
    offerBusy=true;
    offerError="";
    render();
    try {
      const saved=await api("offers","POST",{...attempt,...(allowManual?{allow_manual:true}:{})});
      if (!offerCurrent(version,origin)) return;
      saveOfferRow(saved);
      if (offerInput.url!==attempt.url) return;
      offerFailure=null;
      offerLinkChoices=null;
      await openOffer(saved.id);
    } catch(error) {
      if (offerCurrent(version,origin)&&offerInput.url===attempt.url) {
        offerError=error.message||"Não foi possível preparar esta oferta.";
        const detail=error.detail;
        offerFailure=detail?.code==="PRODUCT_EXTRACTION_EMPTY"&&detail.manual_available===true&&typeof detail.source_url==="string"?
          {url:attempt.url,sourceUrl:detail.source_url,message:offerError}:null;
        offerLinkChoices=detail?.code==="PRODUCT_LINK_AMBIGUOUS"&&Array.isArray(detail.candidates)?
          {input:attempt.url,candidates:detail.candidates.filter(row=>typeof row?.url==="string"&&row.url.startsWith("https://"))}:null;
      }
    } finally {
      if (!controller.signal.aborted&&origin===viewVersion&&route==="ofertas") {
        offerBusy=false;
        render();
      }
    }
  }

  async function saveOfferDetails(values) {
    if (!opened||offerDetailsBusy||offerSendBusy) return;
    const key=opened,origin=viewVersion,version=navVersion;
    offerDetailsBusy=true;
    offerDetailsError="";
    render();
    try {
      const saved=await api("offers/"+encodeURIComponent(key),"PUT",values);
      if (!offerCurrent(version,origin)||opened!==key) return;
      saveOfferRow(saved);
      setOfferDetails(saved);
      resetOfferSend();
      await openOffer(key,false);
    } catch(error) {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) offerDetailsError=error.message||"Não foi possível salvar os detalhes.";
    } finally {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) {offerDetailsBusy=false;render();}
    }
  }

  async function applyOfferPhrase(text) {
    if (!opened||offerPhraseBusy||offerSendBusy) return;
    const key=opened,origin=viewVersion,version=navVersion;
    const focused=document.activeElement;
    offerPhraseBusy=true;
    offerPreviewError="";
    render();
    try {
      const saved=await api("offers/"+encodeURIComponent(key)+"/phrase","PUT",{text:String(text||"")});
      if (!offerCurrent(version,origin)||opened!==key) return;
      saveOfferRow(saved);
      offerPhraseValue=saved.phrase||"";
      resetOfferSend();
      await openOffer(key,false);
    } catch(error) {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) offerPreviewError=error.message||"Não foi possível atualizar a frase.";
    } finally {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) {
        offerPhraseBusy=false;
        render();
        if (focused?.isConnected&&document.activeElement===document.body) focused.focus({preventScroll:true});
      }
    }
  }

  async function sendOffer() {
    if (!offerCanSend()) return;
    const selected=eligibleOfferDestinations().filter(row=>offerDestinations.has(row.id)).map(row=>row.id).sort();
    if (!selected.length) return;
    const key=opened,origin=viewVersion;
    const token=preview.send.preview_token;
    const fingerprint=JSON.stringify([key,chosenTemplate,selected,token]);
    if (offerSendIntent?.fingerprint!==fingerprint) offerSendIntent={fingerprint,payload:{template:chosenTemplate,destinations:selected,preview_token:token,request_id:id()}};
    const payload={...offerSendIntent.payload,destinations:[...offerSendIntent.payload.destinations]};
    offerSendBusy=true;
    offerSendError="";
    render();
    try {
      const result=await api("offers/"+encodeURIComponent(key)+"/send","POST",payload);
      if (controller.signal.aborted||origin!==viewVersion||opened!==key) return;
      offerSendResult=result;
      scheduleOfferSendPoll();
    } catch(error) {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) offerSendError=error.message||"Não foi possível confirmar o envio. Tente novamente.";
    } finally {
      if (!controller.signal.aborted&&origin===viewVersion&&opened===key) {offerSendBusy=false;render();}
    }
  }

  const formActions = {
    shopee: async form=>{
      await api("platforms/shopee","PUT",{app_id:form.get("app_id"),key:form.get("key")});
      await reload();
      notify("Shopee verificada e conectada.");
    },
    groq: async form=>{
      await api("platforms/groq","PUT",{key:form.get("key")});
      await reload();
      notify("Groq conectada.");
    },
    telegram: async form=>{
      await api("connection/telegram","PUT",{token:form.get("token"),dedicated:form.has("dedicated")});
      await reload();
      notify("Abra o bot no Telegram para confirmar o vínculo.");
    },
    destination: async form=>{
      await api("destinations/"+id(),"PUT",{
        name:form.get("name"),mode:form.get("mode"),address:form.get("mode")==="whatsapp_group"?form.get("whatsapp_group"):form.get("address"),test:form.has("test"),
      });
      await reload();
      notify("Destino salvo.");
    },

    offer: async form=>prepareOffer(form.get("url"),offerInput.note),
    "offer-details": async form=>{
      const current=currentOffer();
      const values={};
      const title=String(form.get("title")||"").trim(),image=String(form.get("image")||"").trim();
      if (title!==(current?.title||"")) values.title=title;
      if (image!==(current?.image||"")) values.image=image;
      const raw=String(form.get("price")||"").trim();
      if (raw) {
        const cents=window.OrionFields?.parseMoney?window.OrionFields.parseMoney(raw,{min:1}):(()=>{
          const normalized=raw.includes(",")?raw.replace(/\./g,"").replace(",","."):raw;
          if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)||Number(normalized)<=0) throw new Error("Informe um preço válido, como 149,90.");
          return Math.round(Number(normalized)*100);
        })();
        if (current?.currency!=="BRL"||cents!==Math.round(Number(current?.price)*100)) values.price=(cents/100).toFixed(2);
      }
      if (form.has("affiliate_url")) {
        const url=String(form.get("affiliate_url")||"").trim();
        if (url!==(current?.affiliate_url||"")) values.affiliate_url=url;
      }
      if (!Object.keys(values).length) {setOfferDetails(current);render();return;}
      await saveOfferDetails(values);
    },
    "offer-own-link": async form=>saveOfferDetails({affiliate_url:String(form.get("affiliate_url")||"").trim()}),
    search: async form=>{
      const filters=Object.fromEntries(form);
      const version=++searchVersion, originView=viewVersion, originRoute=route;
      const current=()=>!controller.signal.aborted && version===searchVersion && originView===viewVersion && originRoute===route;
      try {
        const result=await api("catalog?"+new URLSearchParams(filters));
        if (!current()) return;
        catalog=result;
        searchQuery=filters;
        render();
        content.querySelector("[data-form=search] button")?.focus({preventScroll:true});
        notify(`${catalog.length} oferta(s) encontrada(s).`);
      } catch(error) {
        if (current()) throw error;
      }
    },

    phrase: async form=>applyOfferPhrase(form.get("text")),
    template: async form=>{
      const key=editTemplate==="new"?id():editTemplate;
      await api("templates/"+key,"PUT",Object.fromEntries(form));
      editTemplate=key;
      await reload();
      notify("Template salvo.");
    },
    queue: async form=>{
      const when=form.get("due");
      await api(`offers/${opened}/queue`,"POST",{
        destination:form.get("destination"),template:chosenTemplate,due:when?Math.floor(new Date(when).getTime()/1000):null,
      });
      await reload();
      historyFilter="pending";
      historySearch="";
      await open("fila");
      notify("Publicação guardada para revisão.");
    },
    rule: async (form,submitter)=>saveAutomation(submitter?.value==="active"),
  };

  async function toggleRule(ident, active) {
    const rule=state.rule.find(row=>row.id===ident);
    if(!rule||ruleSaveBusy||ruleActionBusy.has(ident)||active&&rule.runtime?.can_activate===false)return;
    ruleActionBusy.add(ident);ruleListError='';
    const label=active?'Ativar automação':'Pausar automação';
    const detail=active?`${rule.name} · ${ruleWindow(rule)}, a cada ${rule.interval} minutos, até ${rule.daily} publicações por destino/dia.`:
      `${rule.name} deixará de programar novas ofertas até ser reativada.`;
    try{
      if(!await confirmAction(label+'?',detail,label))return;
      if(controller.signal.aborted)return;
      if(route==='automacoes')render();
      const saved=await api('rules/'+ident+'/activate','POST',{active});
      if(controller.signal.aborted)return;
      saveRuleRow(saved);notify(active?'Automação ativada.':'Automação pausada.');
    }catch(error){if(!controller.signal.aborted)ruleListError=error.message||'Não foi possível atualizar a automação.';}
    finally{ruleActionBusy.delete(ident);if(!controller.signal.aborted&&route==='automacoes')render();}
  }

  async function disconnect(name) {
    const provider = {shopee:"Shopee",telegram:"Telegram",groq:"Groq"}[name];
    if (!await confirmAction(`Desconectar ${provider}?`,"As funções que usam esta conta ficam indisponíveis até uma nova conexão.","Desconectar",true)) return;
    await api("connection/"+name,"DELETE");
    await reload();
    notify(`${provider} desconectada.`);
  }

  const actions = {
    refresh: async ()=>{if (await reload() && route!=="dashboard") notify("Dados atualizados.");},

    demo: async ()=>{
      const origin=viewVersion;
      const sample=await api("offers/demo","POST");
      if (controller.signal.aborted||origin!==viewVersion) return;
      saveOfferRow(sample);
      await openOffer(sample.id);
    },
    "open-offer": openOffer,
    "ai-phrase": async ()=>actions["offer-phrases"](),
    "offer-phrases": async ()=>{
      if (!opened||offerSuggestionsBusy||offerSendBusy) return;
      const key=opened,version=navVersion,origin=viewVersion;
      offerSuggestionsBusy=true;
      render();
      try {
        const result=await api("offers/"+encodeURIComponent(key)+"/phrases","POST",{});
        if (!offerCurrent(version,origin)||opened!==key) return;
        setOfferSuggestions(Array.isArray(result)?result:result.suggestions);
      } catch(error) {
        if (offerCurrent(version,origin)) notify(error.message,true);
      } finally {
        if (offerCurrent(version,origin)) {offerSuggestionsBusy=false;render();}
      }
    },
    "offer-expand": async ()=>{
      offerPreviewExpanded=!offerPreviewExpanded;
      updateOffers('[data-offer-section="preview"]',offerPreview());
    },
    "offer-send": sendOffer,
    "offer-link-choice": async index=>{
      if (offerBusy||offerLinkChoices?.input!==offerInput.url) return;
      const selected=offerLinkChoices.candidates[Number(index)];
      if (selected) await prepareOffer(selected.url,offerInput.note);
    },
    "offer-retry": async ()=>{
      if (offerFailure?.url===offerInput.url) await prepareOffer(offerInput.url,offerInput.note);
    },
    "offer-manual": async ()=>{
      if (offerFailure?.url===offerInput.url) await prepareOffer(offerInput.url,offerInput.note,true);
    },
    "offer-new": async ()=>{
      navVersion++;
      offerPreviewRequest?.abort();
      offerPreviewRequest=null;
      opened=null;
      preview=null;
      offerPreviewBusy=false;
      offerPreviewError="";
      offerInput={url:"",note:""};
      offerError="";
      offerFailure=null;
      offerLinkChoices=null;
      offerDetailsError="";
      offerDetailsOpen=false;
      offerDetailsDirty=false;
      offerPhraseOpen=false;
      offerDestinations=new Set();
      offerSuggestions=[];
      resetOfferSend();
      offerTab="link";
      render();
      const input=content.querySelector("#af-offer-url");
      input?.focus({preventScroll:true});
      input?.scrollIntoView({block:"nearest",behavior:"instant"});
    },
    "prepare-result": async url=>prepareOffer(url),
    copy: async ()=>{ if(preview) {await navigator.clipboard.writeText(preview.text); notify("Texto copiado.");} },
    "copy-link": async ident=>{
      const row=state.offer.find(o=>o.id===ident);
      if (row?.affiliate_url) {await navigator.clipboard.writeText(row.affiliate_url);notify("Link copiado.");}
    },
    "verify-tg": async ()=>{ await api("connection/telegram/verify","POST"); await reload(); notify("Telegram vinculado."); },
    "verify-wa-channel": async ident=>{
      await api("destinations/"+encodeURIComponent(ident)+"/verify-channel","POST",{});
      await reload();
      notify("Administração do canal verificada.");
    },
    "load-wa-groups": async ()=>{
      if (whatsappGroupsBusy) return;
      const origin=viewVersion;
      whatsappGroupsBusy=true;
      whatsappGroupsError="";
      updateWhatsappGroupPicker();
      try {
        const status=await api("whatsapp/status");
        if (controller.signal.aborted||origin!==viewVersion) return;
        if (status.status!=="connected") throw new Error("Conecte o WhatsApp da sua empresa antes de buscar os grupos.");
        const result=await api("whatsapp/groups");
        if (controller.signal.aborted||origin!==viewVersion) return;
        whatsappGroups=Array.isArray(result.groups)?result.groups:[];
      } catch(error) {
        if (!controller.signal.aborted&&origin===viewVersion) whatsappGroupsError=error.message;
      } finally {
        whatsappGroupsBusy=false;
        if (!controller.signal.aborted&&origin===viewVersion) updateWhatsappGroupPicker();
      }
    },

    revoke: disconnect,
    "revoke-groq": async ()=>{
      if (!await confirmAction("Desconectar Groq?","As ofertas usarão a sugestão padrão quando a integração estiver indisponível.","Desconectar",true)) return;
      await api("platforms/groq","DELETE");
      await reload();
      notify("Chave da Groq removida.");
    },
    "remove-dest": async ident=>{
      const name = state.destination.find(d=>d.id===ident)?.name;
      if (!await confirmAction("Remover destino?",`${name} deixará de aparecer entre os destinos disponíveis para publicação.`,"Remover",true)) return;
      await api("destinations/"+ident,"DELETE");
      await reload();
      notify("Destino removido.");
    },
    "new-template": async ()=>{ editTemplate="new"; render(); focusSection(".af-rule-editor"); },
    "edit-template": async ident=>{ editTemplate=ident; render(); focusSection(".af-rule-editor"); },
    "new-rule": async ()=>openRuleEditor('new'),
    "edit-rule": async ident=>openRuleEditor(ident),
    "close-rule": async ()=>{
      if(ruleSaveBusy)return;
      editRule=null;ruleDraft=null;ruleDraftKey=null;ruleError='';ruleEditorVersion++;
      render();content.querySelector('[data-act="new-rule"]')?.focus({preventScroll:true});
    },
    "refresh-automations": async ()=>{
      if(ruleRefreshBusy)return;
      ruleRefreshBusy=true;ruleListError='';render();
      try{await reload();}catch(error){ruleListError=error.message||'Não foi possível atualizar as automações.';}
      finally{ruleRefreshBusy=false;if(!controller.signal.aborted&&route==='automacoes')render();}
    },
    "start-rule": ident=>toggleRule(ident,true),
    "pause-rule": ident=>toggleRule(ident,false),
    approve: async ident=>{
      const job=state.jobs.find(row=>row.id===ident);
      const recurringReview=job?.doc?.automation?.mode==="review";
      if(!job||job.status!=="approval"||!recurringReview&&!state.sending)return;
      const destination=state.destination.find(row=>row.id===job.destination);
      const detail=recurringReview?`${job.doc.offer.title} entrará na fila de envio para ${destination?.name||"o destino desta publicação"}.`:"O teste será enviado ao destino escolhido para esta publicação.";
      if (!await confirmAction(recurringReview?"Aprovar envio?":"Publicar teste?",detail,recurringReview?"Aprovar envio":"Publicar teste")) return;
      await api("jobs/"+ident,"POST",{action:"approve",...(recurringReview?{}:{confirm_test:true})});
      await reload();
      notify("Publicação aprovada.");
    },
    "cancel-job": async ident=>{
      if (!await confirmAction("Cancelar publicação?","A publicação sairá da fila de envio.","Cancelar publicação",true)) return;
      await api("jobs/"+ident,"POST",{action:"cancel"});
      await reload();
      notify("Publicação cancelada.");
    },
    token: async value=>{
      const textarea=content.querySelector("textarea[name=text]");
      textarea.setRangeText(value,textarea.selectionStart,textarea.selectionEnd,"end");
      textarea.focus();
    },
    favorite: ident=>setFlag(ident,"favorite"),
    block: ident=>setFlag(ident,"blocked"),
  };

  async function setFlag(ident,key) {
    const row=state.product_flag.find(f=>f.id===ident)||{};
    await api("product-flag/"+encodeURIComponent(ident),"PUT",{favorite:!!row.favorite,blocked:!!row.blocked,[key]:!row[key]});
    await reload();
  }
  content.addEventListener("submit",async event=>{
    const form=event.target;
    if (!state || !Object.hasOwn(formActions,form.dataset.form)) return;
    event.preventDefault();
    const button=event.submitter;
    const keepFocus=!!form.closest(".af-offers-workspace,.af-automations-workspace")&&document.activeElement===button;
    if (button&&!form.closest(".af-offers-workspace,.af-automations-workspace")) button.disabled=true;
    form.setAttribute("aria-busy","true");
    try { await formActions[form.dataset.form](new FormData(form),button); }
    catch(error){ notify(error.message,true); }
    finally {
      if (button) button.disabled=false;
      form.removeAttribute("aria-busy");
      if (form.closest(".af-offers-workspace")) syncOfferControls();
      if (form.closest(".af-automations-workspace")) syncRuleControls();
      if (keepFocus&&button?.isConnected&&!button.disabled&&document.activeElement===document.body) button.focus({preventScroll:true});
    }
  }, {signal: controller.signal});

  content.addEventListener("click",async event=>{
    const link=event.target.closest('a[href^="#"]');
    if (link) {
      const target = link.hash.slice(1);
      if (Object.hasOwn(views,target)) {
        event.preventDefault();
        await open(target);
      }
      return;
    }
    if (!state) return;
    const ruleNavigation=event.target.closest('[data-rule-step],[data-rule-next],[data-rule-back]');
    if(ruleNavigation&&ruleDraft){
      const index=ruleSteps.findIndex(([key])=>key===ruleStep);
      const next=ruleNavigation.dataset.ruleStep||ruleSteps[index+(ruleNavigation.hasAttribute('data-rule-next')?1:-1)]?.[0];
      setRuleStep(next,ruleNavigation.hasAttribute('data-rule-next'));return;
    }
    const rulePreset=event.target.closest('[data-rule-interval],[data-rule-days]');
    if(rulePreset&&ruleDraft){
      if(rulePreset.hasAttribute('data-rule-interval'))ruleDraft.interval=rulePreset.dataset.ruleInterval;
      else ruleDraft.days=rulePreset.dataset.ruleDays==='all'?[0,1,2,3,4,5,6]:[0,1,2,3,4];
      ruleDraftRevision++;ruleError='';render();return;
    }
    const automationFilter=event.target.closest('[data-rule-filter]');
    if(automationFilter){ruleFilter=automationFilter.dataset.ruleFilter;render();return;}
    const period = event.target.closest("[data-dashboard-period]");
    if (period && Object.hasOwn(dashboardPeriods,period.dataset.dashboardPeriod)) {
      if (analyticsPeriod!==period.dataset.dashboardPeriod) {
        analyticsPeriod = period.dataset.dashboardPeriod;
        chartSelectedDate = null;
        await loadAnalytics();
      }
      return;
    }
    const metric = event.target.closest("[data-dashboard-metric]");
    if (metric && Object.hasOwn(dashboardMetrics,metric.dataset.dashboardMetric)) {
      analyticsMetric = metric.dataset.dashboardMetric;
      analyticsMetricChosen = true;
      updateDashboard(".af-dashboard-performance",dashboardChart(dashboardSnapshot()));
      return;
    }
    const point = event.target.closest("[data-dashboard-point]");
    if (point) { selectDashboardPoint(point.dataset.dashboardPoint);return; }
    const step = event.target.closest("[data-dashboard-step]");
    if (step) { selectDashboardPoint(dashboardChartModel().selected+Number(step.dataset.dashboardStep),true);return; }
    const publications = event.target.closest("[data-dashboard-publications]");
    if (publications && ["upcoming","recent"].includes(publications.dataset.dashboardPublications)) {
      publicationTab = publications.dataset.dashboardPublications;
      updateDashboard(".af-dashboard-publications",dashboardPublications(dashboardSnapshot()));
      return;
    }
    const phrase=event.target.closest("[data-offer-phrase]");
    if (phrase) {
      const suggestion=offerSuggestions.find(row=>String(row.id)===phrase.dataset.offerPhrase);
      if (suggestion) await applyOfferPhrase(suggestion.text);
      return;
    }
    const tab=event.target.closest("[data-offer-tab]");
    const filter=event.target.closest("[data-history-filter]");
    if (tab) {
      offerTab=tab.dataset.offerTab;
      for (const button of content.querySelectorAll("[data-offer-tab]")) {
        const active=button===tab;
        button.setAttribute("aria-pressed",String(active));
        const panel = content.querySelector("#"+CSS.escape(button.getAttribute("aria-controls")));
        if (panel) panel.hidden=!active;
      }
      return;
    }
    if (filter) { historyFilter=filter.dataset.historyFilter;filterHistory();return; }
    const button=event.target.closest("[data-act]");
    if (!button || !Object.hasOwn(actions,button.dataset.act)) return;
    const keepFocus=!!button.closest(".af-offers-workspace,.af-automations-workspace")&&document.activeElement===button;
    if (!button.closest(".af-offers-workspace,.af-automations-workspace")) button.disabled=true;
    try { await actions[button.dataset.act](button.dataset.value); }
    catch(error) { notify(error.message,true); }
    finally {
      button.disabled=false;
      if (button.closest(".af-offers-workspace")) syncOfferControls();
      if (button.closest(".af-automations-workspace")) syncRuleControls();
      if (keepFocus&&document.activeElement===document.body) {
        if (button.isConnected&&!button.disabled) button.focus({preventScroll:true});
        else if (route==="ofertas") content.querySelector("[data-offer-send-result]")?.focus({preventScroll:true});
      }
    }
  }, {signal: controller.signal});

  content.addEventListener("focusin",event=>{
    const point = event.target.closest("[data-dashboard-point]");
    if (point && state) selectDashboardPoint(point.dataset.dashboardPoint);
  }, {signal:controller.signal});
  content.addEventListener("keydown",event=>{
    const point = event.target.closest("[data-dashboard-point]");
    if (!point || !state || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const model = dashboardChartModel();
    const next = event.key==="Home"?0:event.key==="End"?model.points.length-1:Number(point.dataset.dashboardPoint)+(event.key==="ArrowLeft"?-1:1);
    selectDashboardPoint(next,true);
  }, {signal:controller.signal});

  document.addEventListener("visibilitychange",()=>{
    if (document.hidden) cancelAnalyticsPoll();
    else if (route==="dashboard" && !analyticsLoading) scheduleAnalyticsPoll(analyticsVersion);
    if (document.hidden) cancelOfferSendPoll();
    else if (route==="ofertas") scheduleOfferSendPoll();
  }, {signal:controller.signal});

  content.addEventListener("change",async event=>{
    if(event.target.closest('[data-form="rule"]')&&ruleDraft){
      const field=event.target;
      if(field.hasAttribute('data-select-niches')){ruleDraft.niches=field.checked?state.niches.map(row=>row.id):[];ruleDraftRevision++;}
      else if(field.hasAttribute('data-rule-all-destinations')){if(field.checked&&ruleDestinations().length>5){ruleError='Selecione até 5 destinos por automação.';render();return;}ruleDraft.destinations=field.checked?ruleDestinations().map(row=>row.id):[];ruleDraftRevision++;}
      else if(field.hasAttribute('data-rule-all-day')){
        if(field.checked){ruleWindowBeforeAllDay={opens:ruleDraft.opens,closes:ruleDraft.closes};ruleDraft.opens='00:00';ruleDraft.closes='00:00';}
        else Object.assign(ruleDraft,ruleWindowBeforeAllDay);
        ruleDraftRevision++;
      }
      else changeRuleDraft(field,false);
      ruleError='';render();return;
    }
    if (event.target.id==="af-whatsapp-group") {
      const group=whatsappGroups?.find(row=>row.address===event.target.value);
      const form=event.target.form;
      if (group&&form) {
        form.elements.address.value=group.address;
        if (!form.elements.name.value.trim()) form.elements.name.value=group.title;
      }
      return;
    }
    if (event.target.matches("[data-offer-destination], [data-offer-all]")) {
      if (offerSendBusy) return;
      const allowed=new Set(eligibleOfferDestinations().map(row=>row.id));
      if (event.target.matches("[data-offer-all]")) offerDestinations=event.target.checked?allowed:new Set();
      else if (allowed.has(event.target.value)) {
        if (event.target.checked) offerDestinations.add(event.target.value);
        else offerDestinations.delete(event.target.value);
      }
      resetOfferSend();
      updateOffers('[data-offer-section="targets"]',offerTargets());
      return;
    }
    if (event.target.matches("[data-select-niches],input[name=niches]")) {
      const form=event.target.form;
      const all=form.querySelector("[data-select-niches]");
      const items=[...form.querySelectorAll("input[name=niches]")];
      if (event.target===all) items.forEach(input=>input.checked=all.checked);
      const count=items.filter(input=>input.checked).length;
      all.checked=count===items.length;
      all.indeterminate=count>0&&count<items.length;
      form.querySelector("[data-niche-count]").textContent=`${count} de ${items.length} selecionadas`;
      return;
    }
    if (event.target.matches("form[data-form=destination] select[name=mode]")) {
      const form=event.target.form;
      const [label,hint] = {
        telegram: ["ID numérico ou @ do Telegram","@canal_publico ou ID -100…"],
        whatsapp_channel: ["Link do canal do WhatsApp","https://whatsapp.com/channel/…"],
        whatsapp_group: ["Link ou identificação do grupo (opcional)","Informe a identificação do grupo"],
      }[event.target.value];
      form.querySelector("[data-address-label]").textContent=label;
      form.elements.address.placeholder=hint;
      const assisted=event.target.value!=="telegram";
      const group=event.target.value==="whatsapp_group";
      form.querySelector("[data-destination-address]").hidden=group;
      form.querySelector("[data-wa-group-picker]").hidden=!group;
      const groupSelect=form.querySelector("#af-whatsapp-group");
      if (groupSelect) {groupSelect.required=group;groupSelect.disabled=!group;}
      form.querySelector(".af-test-destination").hidden=assisted;
      form.elements.test.disabled=assisted;
      form.querySelector("[data-assisted-destination]").hidden=event.target.value!=="whatsapp_channel";
      return;
    }
    if (event.target.id!=="preview-template"||!opened) return;
    if (offerSendBusy) return;
    chosenTemplate=event.target.value;
    resetOfferSend();
    try { await openOffer(opened,false); }
    catch(error) { notify(error.message,true); }
  }, {signal: controller.signal});

  content.addEventListener("paste",event=>{
    const field=event.target;
    if (!field.matches('[data-offer-input="url"]')) return;
    const text=event.clipboardData?.getData("text/plain");
    if (!text) return;
    event.preventDefault();
    // Text inputs otherwise remove line breaks and can join two separate URLs.
    const pasted=text.replace(/\r\n?|\n/g," ");
    const start=field.selectionStart??field.value.length,end=field.selectionEnd??start;
    if (field.value.length-(end-start)+pasted.length>8192) {
      offerError="A mensagem pode ter até 8.192 caracteres. Cole um trecho menor ou apenas o link.";
      offerFailure=null;
      offerLinkChoices=null;
      updateOffers('[data-offer-prepare-feedback]',offerPrepareFeedback());
      return;
    }
    field.setRangeText(pasted,start,end,"end");
    field.dispatchEvent(new Event("input",{bubbles:true}));
  }, {signal: controller.signal});

  content.addEventListener("input",event=>{
    const field=event.target;
    if(field.closest('[data-form="rule"]')&&ruleDraft){
      if(field.hasAttribute('data-niche-filter')){ruleNicheFilter=field.value;syncRuleControls();}
      else if(!['checkbox','radio'].includes(field.type))changeRuleDraft(field);
      return;
    }
    if (field.matches("[data-offer-input]")) {
      offerInput[field.dataset.offerInput]=field.value;
      if (field.dataset.offerInput==="url" && (offerError||offerFailure||offerLinkChoices)) {
        offerError="";
        offerFailure=null;
        offerLinkChoices=null;
        updateOffers('[data-offer-prepare-feedback]',offerPrepareFeedback());
      }
      return;
    }
    if (field.matches("[data-offer-search]")) {
      searchQuery[field.dataset.offerSearch]=field.value;
      return;
    }
    if (field.matches("[data-offer-detail]")) {
      offerDetails[field.dataset.offerDetail]=field.value;
      offerDetailsDirty=true;
      resetOfferSend();
      syncOfferControls();
      return;
    }
    if (field.matches("[data-offer-phrase-input]")) {
      offerPhraseValue=field.value;
      const count=content.querySelector("[data-offer-phrase-count]");
      if (count) count.textContent=offerPhraseValue.length+"/155";
      resetOfferSend();
      syncOfferControls();
      return;
    }
    if (field.id==="filter-history") { historySearch=field.value;filterHistory();return; }
    if (!field.matches("[data-niche-filter]")) return;
    const term=normalize(field.value);
    let visible=0;
    for (const row of field.form.querySelectorAll("[data-niche-name]")) {
      row.hidden=!normalize(row.dataset.nicheName).includes(term);
      if (!row.hidden) visible++;
    }
    field.form.querySelector("[data-niche-empty]").hidden=visible>0;
  }, {signal: controller.signal});
  content.addEventListener("toggle",event=>{
    if(event.target.dataset?.ruleDisclosure==='advanced')ruleAdvancedOpen=event.target.open;
    const key=event.target.dataset?.offerDisclosure;
    if (key==="drafts") offerDraftsOpen=event.target.open;
    else if (key==="stores") offerStoresOpen=event.target.open;
    else if (key==="details") offerDetailsOpen=event.target.open;
    else if (key==="phrase") offerPhraseOpen=event.target.open;
    else if (key==="note") offerNoteOpen=event.target.open;
  }, {capture:true,signal:controller.signal});
  void reload().catch(showError);
  return {
    setView(next) {
      if (Object.hasOwn(views,next) && route!==next) {
        viewVersion++;
        navVersion++;
        offerPreviewRequest?.abort();
        offerPreviewRequest=null;
        offerPreviewBusy=false;
        offerBusy=false;
        offerPhraseBusy=false;
        offerSuggestionsBusy=false;
        offerDetailsBusy=false;
        offerSendBusy=false;
        cancelOfferSendPoll();
        if (state) stateVersion++;
        stateLoading=false;
        cancelAnalyticsPoll(true);
        analyticsRequest?.abort();
        analyticsVersion++;
        analyticsLoading = false;
        route=next;
        render();
        if (route==="dashboard" && state) void loadAnalytics();
        else if (route==="ofertas") scheduleOfferSendPoll();
      }
    },
    unmount() {
      viewVersion++;
      navVersion++;
      offerPreviewRequest?.abort();
      offerPreviewRequest=null;
      cancelOfferSendPoll(true);
      stateVersion++;
      cancelAnalyticsPoll(true);
      analyticsRequest?.abort();
      analyticsVersion++;
      const dialog = $("#osAffiliateDialog");
      if (dialog?.open) dialog.close("cancel");
      controller.abort();
      host.replaceChildren();
    },
  };
}
