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
  let historyFilter = "all";
  let historySearch = "";
  let editTemplate = "default";
  let editRule = null;
  let navVersion = 0;
  const dashboardPeriods = {today:"Hoje", "7d":"7 dias", "30d":"30 dias", month:"Este mês"};
  const dashboardMetrics = {
    commission_cents:{label:"Comissões", singular:"Comissão estimada", currency:true, glyph:"wallet"},
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
  let chartSelected = null;
  let publicationTab = "upcoming";
  let stateUpdatedAt = null;
  function notify(text, error=false) {
    if (!controller.signal.aborted) showNotice(text, error);
  }
  async function api(path, method="GET", payload) {
    return request(apiRoot + path, method, payload, {signal:controller.signal});
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
  const destinations = () => state.destination.filter(row => row.mode === "telegram" && row.test && row.verified);
  const ruleDestinations = () => state.destination.filter(row => row.verified &&
    (row.mode === "whatsapp_channel" || row.mode === "telegram" && row.test));
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
  const dashboardExternal = (label="Abrir relatórios Shopee") =>
    `<a class="af-dashboard-text-link" href="https://affiliate.shopee.com.br/" target="_blank" rel="noopener noreferrer">${html(label)}${icon("external")}</a>`;
  const dashboardEmpty = (title, detail, glyph="chart", action="") =>
    `<div class="af-dashboard-empty"><span class="af-dashboard-empty-icon" aria-hidden="true">${icon(glyph)}</span><strong>${html(title)}</strong><p>${html(detail)}</p>${action}</div>`;

  function dashboardSnapshot() {
    const data = analyticsCache.get(analyticsPeriod);
    const summary = data?.summary || {};
    const jobs = state.jobs || [];
    const published = jobs.filter(job=>job.status==="published").length;
    const pendingCount = pending().length;
    const failed = jobs.filter(job=>job.status==="failed").length;
    const source = data?.sources?.sales || {status:connected("shopee")?.status==="connected"?"unavailable":"not_connected"};
    const publications = data?.sources?.publications || {status:"partial",complete:false,scope:"latest_120"};
    const financial = [summary.commission_cents,summary.sales_cents ?? summary.gross_sales_cents,summary.orders].some(value=>dashboardNumber(value)!==null);
    return {
      data, summary, source, publications, financial,
      publishedSnapshot:dashboardNumber(data?.operation?.published_snapshot) ?? published,
      pendingSnapshot:dashboardNumber(data?.operation?.pending_snapshot) ?? dashboardNumber(summary.pending) ?? pendingCount,
      failedSnapshot:dashboardNumber(data?.operation?.failed_snapshot) ?? dashboardNumber(summary.failed) ?? failed,
      rules:dashboardNumber(summary.active_rules) ?? activeRules().length,
      verified:dashboardNumber(summary.verified_destinations) ?? state.destination.filter(row=>row.verified).length,
      sampleLimit:dashboardNumber(data?.operation?.sample_limit) ?? 120,
    };
  }

  function dashboardSourceMessage(snapshot) {
    if (snapshot.financial && snapshot.source.status==="partial") return "Relatório parcial da Shopee. Os valores podem mudar.";
    if (snapshot.financial && snapshot.source.status==="syncing") return "A Shopee está atualizando o relatório.";
    if (snapshot.financial) return dashboardNumber(snapshot.summary.commission_cents)===null?"A comissão ainda não foi informada no relatório de vendas.":"Comissões informadas pela Shopee, sujeitas à validação e a cancelamentos.";
    if (analyticsLoading) return "Consultando os resultados disponíveis…";
    if (snapshot.source.status==="not_connected") return "Conecte sua conta para preparar ofertas com seu link de afiliado.";
    if (snapshot.source.status==="error") return "Não foi possível consultar o relatório de vendas agora.";
    if (snapshot.source.status==="syncing") return "O primeiro relatório de vendas está sendo preparado.";
    return "Relatório de vendas ainda não integrado.";
  }

  function dashboardHero(snapshot) {
    const {summary,financial,source} = snapshot;
    const commission = dashboardNumber(summary.commission_cents);
    const validated = dashboardNumber(summary.validated_commission_cents ?? summary.approved_commission_cents);
    const first = snapshot.publishedSnapshot===0;
    const status = financial ? source.status==="partial"?"Relatório parcial":"Shopee Afiliados" : "Seu negócio de afiliados";
    return `<section class="af-dashboard-revenue" aria-labelledby="af-revenue-title">
      <header><span class="af-dashboard-eyebrow">${html(status)}</span><span class="af-dashboard-revenue-icon" aria-hidden="true">${icon("wallet")}</span></header>
      <div class="af-dashboard-revenue-main">
        <h2 id="af-revenue-title">Comissão estimada</h2>
        ${commission!==null?`<strong data-dashboard-kpi="commission_cents" class="af-dashboard-revenue-value${Math.abs(commission)>=1e9?" af-dashboard-long-value":""}">${dashboardMoney(commission)}</strong>
          <p>${html(dashboardPeriods[analyticsPeriod])} · sua comissão sobre as vendas atribuídas</p>`:
          `<strong class="af-dashboard-revenue-intro">${first?"Uma boa oferta.<br>Seu próximo resultado.":"Cada oferta abre<br>uma nova oportunidade."}</strong>
          <p>${html(dashboardSourceMessage(snapshot))}</p>`}
      </div>
      ${commission!==null?`<div class="af-dashboard-revenue-validation"><span>Comissão validada</span><strong data-dashboard-kpi="validated_commission_cents">${dashboardMoney(validated)}</strong>
        <small>${validated===null?"Ainda não informada pela Shopee":"Informada no relatório; não representa valor recebido."}</small></div>`:
        `<div class="af-dashboard-revenue-actions">${link(first?"Preparar primeira oferta":"Preparar oferta","ofertas","plus",true)}</div>`}
      <footer><span>${icon("info")}${commission!==null?"Estimativa sujeita à validação":financial?"Comissão ainda não informada":"Vendas e comissões: consulte o relatório"}</span>${dashboardExternal("Ver na Shopee")}</footer>
    </section>`;
  }

  function dashboardKpis(snapshot) {
    const {summary,financial,publications} = snapshot;
    const scopedPublications = dashboardNumber(summary.published);
    const publishedValue = scopedPublications ?? snapshot.publishedSnapshot;
    const publishedNote = scopedPublications!==null ? publications.complete?"Confirmadas no período":"Contagem parcial do período" : `Últimas ${snapshot.sampleLimit} publicações`;
    const metrics = financial ? [
      {key:"sales_cents",label:"Vendas geradas",value:dashboardMoney(summary.sales_cents ?? summary.gross_sales_cents),note:"Valor dos pedidos atribuídos",glyph:"chart"},
      {key:"orders",label:"Pedidos",value:dashboardCount(summary.orders),note:"Atribuídos pela Shopee",glyph:"box"},
      {key:"published",label:"Publicações",value:dashboardCount(publishedValue),note:publishedNote,glyph:"send",href:"fila"},
      {key:"clicks",label:"Cliques",value:dashboardNumber(summary.clicks)!==null?dashboardCount(summary.clicks):"Na Shopee",note:dashboardNumber(summary.clicks)!==null?"Informados pela fonte":"Disponíveis no relatório da Shopee",glyph:"external",external:true},
    ] : [
      {key:"published_snapshot",label:"Publicadas",value:dashboardCount(snapshot.publishedSnapshot),note:`Últimas ${snapshot.sampleLimit} publicações`,glyph:"send",href:"fila"},
      {key:"pending",label:"Na fila",value:dashboardCount(snapshot.pendingSnapshot),note:"Programadas ou em revisão",glyph:"clock",href:"fila"},
      {key:"active_rules",label:"Automações",value:dashboardCount(snapshot.rules),note:"Regras ativas",glyph:"repeat",href:"automacoes"},
      {key:"verified_destinations",label:"Destinos",value:dashboardCount(snapshot.verified),note:"Verificados para publicar",glyph:"shieldCheck",href:"conexoes"},
    ];
    return `<section class="af-dashboard-kpis" aria-label="${financial?"Indicadores do período":"Sua operação agora"}">${metrics.map(metric=>{
      const tag = metric.href || metric.external ? "a" : "article";
      const attributes = metric.external ? ' href="https://affiliate.shopee.com.br/" target="_blank" rel="noopener noreferrer"' : metric.href?` href="#${metric.href}"`:"";
      const valueClass = metric.value==="Na Shopee"?"af-dashboard-kpi-word":metric.key==="sales_cents"?`af-dashboard-kpi-currency${metric.value.length>13?" af-dashboard-kpi-long":""}`:"";
      return `<${tag} class="af-dashboard-kpi"${attributes}><span class="af-dashboard-kpi-label">${html(metric.label)}${icon(metric.glyph)}</span><strong data-dashboard-kpi="${metric.key}"${valueClass?` class="${valueClass}"`:""}>${html(metric.value)}</strong><small>${html(metric.note)}</small></${tag}>`;
    }).join("")}</section>`;
  }

  function dashboardChartModel(snapshot=dashboardSnapshot()) {
    const metric = analyticsMetric==="commission_cents" && !snapshot.financial && !analyticsMetricChosen ? "published" : analyticsMetric;
    const rows = Array.isArray(snapshot.data?.series) ? snapshot.data.series.filter(row=>/^\d{4}-\d{2}-\d{2}$/.test(String(row.date))).slice(0,366) : [];
    const points = rows.map(row=>({date:row.date,value:dashboardNumber(row[metric])}));
    const selected = Math.min(Math.max(chartSelected ?? Math.max(points.length-1,0),0),Math.max(points.length-1,0));
    return {metric,config:dashboardMetrics[metric],points,selected};
  }

  function dashboardChart(snapshot) {
    const {metric,config,points,selected} = dashboardChartModel(snapshot);
    const known = points.filter(point=>point.value!==null);
    const total = dashboardNumber(snapshot.summary[metric]);
    const range = snapshot.data?.period;
    const dates = range?.start_date && range?.end_date ? `${dashboardDay(range.start_date)} – ${dashboardDay(range.end_date)}` : dashboardPeriods[analyticsPeriod];
    const unavailable = metric!=="published" ? dashboardSourceMessage(snapshot) : snapshot.publications.complete?"Sem informações diárias neste período.":`O resumo considera as últimas ${snapshot.sampleLimit} publicações. O histórico completo por dia ainda não está disponível.`;
    let plot;
    if (!known.length) {
      const first = metric==="published" && snapshot.publishedSnapshot===0;
      plot = dashboardEmpty(first?"Sua primeira publicação começa aqui":metric==="published"?"Histórico diário ainda indisponível":"Seus resultados, direto da fonte",
        first?"Prepare uma oferta e acompanhe os próximos envios por aqui.":unavailable,
        config.glyph,first?link("Buscar uma oferta","ofertas","search"):metric==="published"?link("Ver fila e histórico","fila","arrow"):dashboardExternal());
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
        </svg><div class="af-dashboard-chart-hits" role="group" aria-label="Valores diários de ${html(config.label.toLowerCase())}">${points.map((point,index)=>`<button type="button" data-dashboard-point="${index}" tabindex="${index===selected?0:-1}" aria-pressed="${index===selected}" aria-label="${html(dashboardDay(point.date)+": "+format(point.value)+(point.value===null?" · sem informação":""))}" title="${html(dashboardDay(point.date)+": "+format(point.value))}" style="left:${(x(index)/640*100).toFixed(4)}%;width:${Math.min(100,100/points.length).toFixed(4)}%"><span class="os-sr-only">${dashboardDay(point.date)}</span></button>`).join("")}</div></div></div>
        <div class="af-dashboard-chart-axis" aria-hidden="true"><span>${dashboardDay(points[0].date)}</span>${points.length>2?`<span>${dashboardDay(points[Math.floor((points.length-1)/2)].date)}</span>`:""}<span>${points.length>1?dashboardDay(points.at(-1).date):""}</span></div>
        <div class="af-dashboard-chart-controls"><span>${known.every(point=>point.value===0)?"Nenhum resultado registrado neste período.":"Selecione um dia para ver os detalhes."}</span><div><button type="button" class="os-icon-button" data-dashboard-step="-1" aria-label="Dia anterior"${selected===0?" disabled":""}>${icon("left")}</button><button type="button" class="os-icon-button" data-dashboard-step="1" aria-label="Próximo dia"${selected===points.length-1?" disabled":""}>${icon("chevron")}</button></div></div>`;
    }
    return `<section class="af-dashboard-panel af-dashboard-performance" aria-labelledby="af-performance-title"><header class="af-dashboard-panel-head"><div><h2 id="af-performance-title">Desempenho no período</h2><p>${html(dates)}${snapshot.source.status==="partial" && metric!=="published"?" · relatório parcial":""}</p></div>${total!==null?`<span class="af-dashboard-chart-total"><small>${html(config.singular)}</small><strong>${config.currency?dashboardMoney(total):dashboardCount(total)}</strong></span>`:""}</header>
      <div class="af-dashboard-metric-tabs" role="group" aria-label="Métrica do gráfico">${Object.entries(dashboardMetrics).map(([key,item])=>`<button type="button" data-dashboard-metric="${key}" aria-pressed="${key===metric}">${icon(item.glyph)}${html(item.label)}</button>`).join("")}</div>
      ${plot}
      <p class="af-dashboard-source-note">${metric==="published"?(snapshot.publications.complete?"Fonte: publicações confirmadas pelo Orion.":"Histórico diário completo ainda indisponível."):"Fonte: relatório de afiliados Shopee. Comissão estimada não é valor recebido."}</p>
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
    return `<section class="af-dashboard-panel" aria-labelledby="af-products-title"><header class="af-dashboard-panel-head"><div><h2 id="af-products-title">Produtos em destaque</h2><p>Comissões atribuídas pela Shopee</p></div>${icon("box")}</header>
      ${products.length?`<ol class="af-dashboard-rank-list">${products.map((row,index)=>{
        const commission = dashboardNumber(row.commission_cents);
        const orders = dashboardNumber(row.orders);
        return `<li><span class="af-dashboard-rank-number">${index+1}</span><div class="af-dashboard-rank-copy"><strong>${html(row.title || row.name || "Produto sem título")}</strong><small>${orders!==null?`${dashboardCount(orders)} ${orders===1?"pedido":"pedidos"}`:"Pedidos ainda não informados"}</small>${commission!==null?`<span class="af-dashboard-rank-track" aria-hidden="true"><span style="width:${Math.max(0,Math.min(100,commission/max*100))}%"></span></span>`:""}</div><strong class="af-dashboard-rank-value">${dashboardMoney(commission)}</strong></li>`;
      }).join("")}</ol>`:dashboardEmpty("Seu próximo destaque está por vir","Os produtos aparecem quando houver vendas atribuídas no período.","box",link("Buscar ofertas","ofertas","search"))}
      <footer class="af-dashboard-panel-foot">${dashboardExternal("Conferir relatório completo")}</footer></section>`;
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
      <footer class="af-dashboard-panel-foot"><a href="#conexoes" class="af-dashboard-text-link">Gerenciar destinos${icon("arrow")}</a>${!complete?'<span>Vendas por destino dependem de atribuição no relatório.</span>':""}</footer></section>`;
  }

  function dashboardPublications(snapshot) {
    const upcoming = Array.isArray(snapshot.data?.upcoming)?snapshot.data.upcoming:pending().slice().sort((a,b)=>(a.due||Infinity)-(b.due||Infinity));
    const recent = Array.isArray(snapshot.data?.recent)?snapshot.data.recent:state.jobs;
    const jobs = (publicationTab==="upcoming"?upcoming:recent).slice(0,5);
    if (!upcoming.length && !recent.length) return "";
    return `<section class="af-dashboard-panel af-dashboard-publications" aria-labelledby="af-publications-title"><header class="af-dashboard-panel-head"><div><h2 id="af-publications-title">Publicações</h2><p>Fila atual e atividade mais recente</p></div><a href="#fila" class="af-dashboard-text-link">Ver histórico${icon("arrow")}</a></header>
      <div class="af-dashboard-publication-tabs" role="group" aria-label="Publicações exibidas"><button type="button" data-dashboard-publications="upcoming" aria-pressed="${publicationTab==="upcoming"}">Próximas <span>${dashboardCount(upcoming.length)}</span></button><button type="button" data-dashboard-publications="recent" aria-pressed="${publicationTab==="recent"}">Recentes</button></div>
      ${jobs.length?`<ul class="af-dashboard-job-list">${jobs.map(job=>{
        const destination = state.destination.find(row=>row.id===(job.destination_id||job.destination));
        const when = publicationTab==="upcoming"?(job.scheduled_at||job.due):(job.published_at||job.changed);
        const whenLabel = when?dashboardTime(when,undefined,job.doc?.rule?.timezone):publicationTab==="recent"?"Horário não informado":job.status==="approval"?"Aguardando revisão":"Sem horário definido";
        return `<li><span class="af-dashboard-row-icon" aria-hidden="true">${icon(job.status==="published"?"check":"clock")}</span><div class="af-dashboard-job-copy"><strong>${html(job.title||job.doc?.offer?.title||"Oferta preparada")}</strong><small>${html(job.destination_name||destination?.name||"Destino removido")}</small></div><span class="af-dashboard-job-time">${html(whenLabel)}</span>${jobPill(job)}</li>`;
      }).join("")}</ul>`:dashboardEmpty("Sua fila está livre","Prepare outra oferta ou configure uma automação para manter seus canais ativos.","calendarClock",link("Preparar oferta","ofertas","plus"))}
      <footer class="af-dashboard-panel-foot"><span>${snapshot.data?.sources?.publications?.complete?"Fila atual, independente do período selecionado.":`Últimas ${snapshot.sampleLimit} publicações registradas. A fila é independente do período.`}</span></footer></section>`;
  }

  function dashboard() {
    const snapshot = dashboardSnapshot();
    const products = dashboardProducts(snapshot);
    const updated = snapshot.data?.updated_at || stateUpdatedAt;
    const timeZone = snapshot.data?.period?.timezone || "America/Sao_Paulo";
    const attention = needingAttention().length;
    return `<div class="af-dashboard">
      <header class="af-dashboard-heading"><div><span class="af-dashboard-eyebrow">Afiliados</span><h1>Dashboard</h1></div><div class="af-dashboard-heading-actions">${link("Nova oferta","ofertas","plus",true)}</div><p>Suas ofertas, seus canais e seus próximos resultados.</p></header>
      <div class="af-dashboard-toolbar"><div class="af-dashboard-periods" role="group" aria-label="Período dos resultados">${Object.entries(dashboardPeriods).map(([key,label])=>`<button type="button" data-dashboard-period="${key}" aria-pressed="${key===analyticsPeriod}">${html(label)}</button>`).join("")}</div>
        <div class="af-dashboard-freshness"><span>${analyticsLoading?"Atualizando…":`Consultado ${dashboardTime(updated,{hour:"2-digit",minute:"2-digit"},timeZone)}`}</span><button class="os-icon-button" type="button" data-act="refresh" aria-label="Atualizar Dashboard" title="Atualizar os dados disponíveis"${analyticsLoading?" disabled":""}>${icon("refresh")}</button></div></div>
      ${analyticsError && snapshot.data?`<div class="af-dashboard-alert" role="status">${icon("info")}<p>Não foi possível atualizar os resultados. Mantivemos os últimos dados confirmados deste período.</p></div>`:""}
      <div class="af-dashboard-overview" aria-busy="${analyticsLoading}">${dashboardHero(snapshot)}${dashboardKpis(snapshot)}</div>
      ${attention?`<a href="#fila" class="af-dashboard-alert af-dashboard-alert-warning">${icon("shieldAlert")}<p><strong>${attention} ${attention===1?"publicação precisa":"publicações precisam"} de atenção</strong><span>Revise as ocorrências entre as últimas ${snapshot.sampleLimit} publicações.</span></p>${icon("chevron")}</a>`:""}
      <div class="af-dashboard-workspace">${dashboardChart(snapshot)}${dashboardOperation(snapshot)}</div>
      <div class="af-dashboard-rankings${products?"":" af-dashboard-rankings-single"}">${products}${dashboardDestinations(snapshot)}</div>
      ${dashboardPublications(snapshot)}
      <footer class="af-dashboard-footnote"><span>${icon("info")}Vendas geradas são o valor dos pedidos. Sua receita de afiliado é a comissão.</span><span>Cliques disponíveis no relatório da Shopee.${dashboardExternal("Abrir relatório")}</span></footer>
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
          <label><span data-address-label>ID numérico ou @ do Telegram</span> <input name="address" maxlength="180" placeholder="@canal_publico ou ID -100…"></label>
          <label class="os-check af-test-destination"><input name="test" type="checkbox"> É um destino de teste e autorizo validar a administração.</label>
          <p class="os-help" data-assisted-destination hidden>Para publicar, copie a legenda e baixe o card em Ofertas.</p>
          <div class="os-form-actions">${submit("Salvar destino")}</div></form>`,"plus")}
      </div></div>`;
  }


  function offerPreview() {
    const row = state.offer.find(o=>o.id===opened);
    if (!row) return card("Prévia da publicação",empty("Sua oferta aparece aqui",
      "Gere uma prévia por link, encontre um produto no catálogo ou abra um rascunho.","eye"),"eye","","af-preview");
    const image = `${apiRoot}offers/${encodeURIComponent(opened)}/card.png?template=${encodeURIComponent(chosenTemplate)}`;
    return card("Prévia da publicação",`<div class="af-preview-layout">
      <div class="af-form">${selectField("Template","template",`${templates(chosenTemplate)}`,"preview-template")}</div>
      <div class="af-preview-meta">${row.demo?pill("Dados de demonstração","warning"):pill("Link gerado pela Shopee","success")}
        ${row.fetched_at?`<span class="af-muted">Consulta em ${date(row.fetched_at)}</span>`:""}</div>
      ${!row.demo && !row.availability?`<div class="af-hint">${icon("info")} Estoque por variante não confirmado pela Shopee.</div>`:""}
      <div class="af-message" aria-label="Mensagem no WhatsApp">
        <div class="af-message-head"><img src="${assetsRoot}/brands/whatsapp.svg" alt="" width="20" height="20"><strong>Mensagem no WhatsApp</strong></div>
        <div class="af-message-body">
          ${preview?.media==="card"?`<img class="af-card-image" src="${image}" alt="Card gerado a partir do rascunho" width="900" height="970">`:""}
          <div class="af-pre" id="offer-text">${preview?.html ?? html(preview?.text || "Carregando prévia…")}</div>
        </div>
      </div>
      ${preview?`<div class="af-row">
        ${btn("Copiar texto","copy")}
        ${row.affiliate_url?btn("Copiar link","copy-link",opened):""}
        ${preview.media==="card"?`<a class="os-button os-outline" href="${image}" download="oferta-orion.png">${icon("download")}Baixar card</a>`:""}
      </div>`:""}
      <details class="af-disclosure" ${row.phrase?"open":""}>
        <summary>Editar frase da oferta</summary>
        <form class="os-form af-form" data-form="phrase">
          <label>Frase da oferta <textarea name="text" maxlength="155" rows="2">${html(row.phrase || "")}</textarea></label>
          <div class="af-row">${btn(state.phrase_ai?"Gerar frase com IA":"Usar sugestão padrão","ai-phrase",opened)}
          ${submit("Salvar frase")}
          ${row.phrase_source ? pill(row.phrase_source==="groq"?"Gerada pela Groq":row.phrase_source==="manual"?"Editada":"Sugestão padrão") : ""}</div>
        </form>
      </details>
      ${!row.demo && destinations().length?`<details class="af-disclosure"><summary>Enviar para revisão</summary>
        <form class="os-form af-form" data-form="queue">
          ${selectField("Destino de teste","destination",`${destinations().map(d=>option(d.id,d.name)).join("")}`)}
          <label>Agendar (horário deste dispositivo) <input name="due" type="datetime-local"></label>
          <div class="os-form-actions">${submit("Colocar para revisão","clock")}</div>
        </form></details>`:""}
    </div>`,"eye","","af-preview");
  }

  function offers() {
    const drafts = state.offer.map(o=>`<div class="af-item"><div><strong>${html(o.title)}</strong>
      <small>${o.demo?"Demonstração":html(money(o.price))}</small></div>${btn("Ver prévia","open-offer",o.id)}</div>`).join("");
    const results = catalog?.map(o=>{
      const flag=state.product_flag.find(f=>f.id===o.identity);
      return `<div class="af-item"><div><strong>${html(o.title)}</strong><small>${html(money(o.price))}</small>
        <div class="af-row af-subtext">${btn("Preparar","prepare-result",o.catalog_url)}
        ${btn(flag?.favorite?"Desfavoritar":"Favoritar","favorite",o.identity)}
        ${btn(flag?.blocked?"Desbloquear":"Bloquear","block",o.identity)}</div></div></div>`;
    }).join("");
    return heading("Ofertas","Encontre um produto da Shopee e prepare a publicação com seu link de afiliado.",
      btn("Ver exemplo","demo")) + `<div class="af-sheet af-offers"><div class="af-stack">
        ${card("Preparar oferta",`<div class="af-offer-panels">
          <div class="af-status-tabs af-switcher" role="group" aria-label="Como encontrar o produto">
            ${[["link","Por link","link"],["catalog","Catálogo Shopee","search"]].map(([key,label,glyph])=>`
            <button class="os-button os-outline" type="button" data-offer-tab="${key}" aria-pressed="${key===offerTab}" aria-controls="offer-${key}">${icon(glyph)}${label}</button>`).join("")}
          </div>
          <div id="offer-link" ${offerTab==="link"?"":"hidden"}>
            <form class="os-form af-form" data-form="offer">
              <label>Link do produto Shopee <input name="url" required type="url" placeholder="https://shopee.com.br/product/…"></label>
              <label>Observação (opcional) <input name="note" maxlength="300"></label>
              <div class="os-form-actions">${submit("Gerar link e prévia","arrow")}</div>
            </form>
          </div>
          <div id="offer-catalog" ${offerTab==="catalog"?"":"hidden"}>
            <form class="os-form af-form" data-form="search">
              <label>Palavra-chave <input name="keyword" placeholder="Ex.: mouse sem fio" maxlength="80" value="${html(searchQuery.keyword)}"></label>
              <div class="af-field"><label for="catalog-category">Categoria</label>
                <select data-orion-field="native" name="niche" id="catalog-category">${option("","Todas as categorias",searchQuery.niche)}${state.niches.map(n=>option(n.id,n.name,searchQuery.niche)).join("")}</select></div>
              ${selectField("Ordenar na Shopee","sort",`${Object.entries(state.sorts).map(([k,v])=>option(k,v,searchQuery.sort)).join("")}`)}
              <div class="os-form-actions">${submit("Pesquisar","search")}</div>
            </form>
            ${catalog?`<div class="af-list af-action-strip">${results||empty("Nenhuma oferta encontrada","Tente outra palavra-chave ou categoria.","search")}</div>`:""}
          </div>
        </div>`,"box")}
        ${card("Rascunhos",drafts?`<div class="af-list">${drafts}</div>`:empty("Sem rascunhos","As ofertas preparadas ficam aqui.","fileCheck"),"fileCheck")}
      </div>${offerPreview()}</div>`;
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

  function decisions() {
    return state.decision.length?`<div class="af-list">${state.decision.slice(0,6).map(d=>`<div class="af-item"><div><strong>${html(d.rule)}</strong>
      <small>${html(d.reason)}${d.niche?` · ${html(state.niches.find(n=>n.id===d.niche)?.name || d.niche)}`:""}</small></div></div>`).join("")}</div>`
      :empty("Sem execuções registradas","O resultado das próximas buscas aparecerá aqui.","activity");
  }


  function ruleEditor(rule, available) {
    const input = (name,label,value="",type="text",attributes="") =>
      `<label>${label}<input type="${type}" name="${name}" value="${html(rule[name] ?? value)}" ${attributes}></label>`;
    const chosen = state.niches.filter(n=>rule.niches?.includes(n.id)).length;
    const days = ["Seg","Ter","Qua","Qui","Sex","Sáb","Dom"];
    return card(editRule==="new"?"Nova automação":"Editar automação",`
      <form class="os-form af-form" data-form="rule">
        ${input("name","Nome da automação","","text","required maxlength=80")}
        <div class="af-grid">
          <div class="af-task-section">
            <h3>Seleção de produtos</h3>
            <fieldset class="af-niches"><legend>Nichos da Shopee</legend>
              <div class="af-row af-niche-head">
                <label class="os-check"><input type="checkbox" data-select-niches${chosen===state.niches.length?" checked":""}> Selecionar todos</label>
                <small data-niche-count role="status">${chosen} de ${state.niches.length} selecionadas</small>
              </div>
              <label>Buscar categoria <span class="af-search">${icon("search")}<input type="search" data-niche-filter placeholder="Ex.: Beleza"></span></label>
              <div class="af-cols af-niche-list">${state.niches.map(n=>`<label class="os-check af-check-tile" data-niche-name="${html(n.name)}">
                <input type="checkbox" name="niches" value="${html(n.id)}"${rule.niches?.includes(n.id)?" checked":""}> ${html(n.name)}</label>`).join("")}</div>
              <p class="af-copy" data-niche-empty role="status" hidden>Nenhuma categoria encontrada.</p>
            </fieldset>
            ${input("keyword","Palavra-chave adicional","","text","maxlength=80")}
            ${selectField("Ordenar na Shopee","sort",`${Object.entries(state.sorts).map(([key,value])=>option(key,value,String(rule.sort || 2))).join("")}`)}
            <details class="af-disclosure">
              <summary>Filtros avançados</summary>
              <div class="af-task-section">
                <label>Palavras bloqueadas <input name="blocked" maxlength="700" placeholder="Separe por vírgula" value="${html(rule.blocked?.join(", "))}"></label>
                <div class="af-cols">${input("min_price","Preço mínimo (R$)","","number","min=0 step=.01")}${input("max_price","Preço máximo (R$)","","number","min=0 step=.01")}</div>
                <div class="af-cols">${input("min_rating","Avaliação mínima","","number","min=0 max=5 step=.1")}${input("min_commission","Comissão mínima (%)","","number","min=0 max=100 step=.1")}</div>
                <div class="af-cols">${input("min_sales","Vendas mínimas","","number","min=0 max=1000000")}${input("min_discount","Desconto mínimo (%)","","number","min=0 max=99")}</div>
              </div>
            </details>
          </div>
          <div class="af-task-section">
            <h3>Publicação e horários</h3>
            <fieldset class="af-niches"><legend>Destinos</legend>
              ${available.length?available.map(d=>`<label class="os-check af-check-tile">
                <input type="checkbox" name="destinations" value="${html(d.id)}"${rule.destinations?.includes(d.id)?" checked":""}>
                ${html(d.name)} <small>${html(defaultMedia(d.mode))}</small></label>`).join("") :
                `<p>Verifique a administração de um canal do WhatsApp ou de um destino de teste no Telegram.</p>${link("Abrir conexões","conexoes","link")}`}
            </fieldset>
            ${selectField("Template","template_id",`${templates(rule.template_id || state.deal_template)}`)}
            <div class="af-cols">${input("opens","Início da janela","09:00","time","required")}${input("closes","Fim da janela","21:00","time","required")}</div>
            <small>Para publicar o dia inteiro, use 00:00 nos dois campos.</small>
            ${input("timezone","Fuso horário","America/Cuiaba","text","required")}
            <fieldset><legend>Dias da semana</legend><div class="af-row">${days.map((day,i)=>`<label class="os-check af-check-tile">
              <input type="checkbox" name="days" value="${i}"${!rule.days || rule.days.includes(i)?" checked":""}> ${day}</label>`).join("")}</div></fieldset>
            <div class="af-cols">${input("interval","Intervalo (min)","90","number","required min=10 max=1440")}${input("daily","Limite por dia","8","number","required min=1 max=48")}</div>
            ${input("repeat_hours","Não repetir produto (horas)","48","number","required min=1 max=720")}
            ${selectField("Modo","mode",`${option("review","Revisar antes de publicar",rule.mode)}${option("automatic","Envio automático",rule.mode)}`)}
            <p class="af-hint">${icon("info")}${state.promotech_automation?"A regra autorizada do PromoTech aceita envio automático a partir de 30 minutos.":"Os destinos só publicam automaticamente após autorização."}</p>
          </div>
        </div>
        <div class="os-form-actions af-action-strip">${btn("Fechar editor","close-rule")}${submit(rule.active?"Salvar e pausar":"Salvar regra pausada","check",!available.length)}</div>
      </form>`,"edit","","af-rule-editor");
  }

  function automations() {
    const rule = state.rule.find(r=>r.id===editRule) || {};
    const lists = state.rule.map(r=>`<article class="os-card af-card af-automation-card">
      <header class="af-card-head">${square("repeat")}<div><h2>${html(r.name)}</h2><p>${html(ruleDestNames(r) || "Sem destinos")}</p></div></header>
      <div class="af-row">${pill(r.active?"Ativa":"Pausada",r.active?"success":"neutral")}${pill(r.mode==="automatic"?"Envio automático":"Envio com revisão")}</div>
      <dl class="af-rule-stats">
      <div><dt>Categorias</dt><dd>${r.niches?.length ? `${r.niches.length} ${r.niches.length===1?"selecionada":"selecionadas"}` : html(r.keyword || "Todas")}</dd></div>
        <div><dt>Frequência</dt><dd>A cada ${r.interval} min</dd></div>
        <div><dt>Janela</dt><dd>${html(ruleWindow(r))}</dd></div>
      </dl>
      <p class="af-hint">${icon("clock")}${r.active?`Próxima execução: ${ruleDate(r)} (${html(r.timezone)})`:`Até ${r.daily} publicações por dia · ${html(r.timezone)}`}</p>
      <div class="af-row af-action-strip">${btn("Editar","edit-rule",r.id)}${btn(r.active?"Pausar":"Ativar",r.active?"pause-rule":"start-rule",r.id,!r.active)}</div>
    </article>`).join("");
    return heading("Automações","Defina o que publicar, onde enviar e com que frequência.",btn("Nova automação","new-rule","",true)) +
      `<div class="af-page">
        ${editRule?ruleEditor(rule,ruleDestinations()):""}
        <div class="af-sheet">
          <section class="af-stack" aria-label="Regras cadastradas">
            <h2>Minhas automações</h2>
            ${lists||`<div class="os-card af-card">${empty("Nenhuma automação",
              "Escolha as categorias e prepare a primeira rotina de publicação.","repeat")}</div>`}
          </section>
          ${card("Últimas execuções",decisions(),"activity","Resultado da busca de ofertas por regra")}
        </div>
      </div>`;
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
        <td>${jobPill(job)}${job.error||job.receipt?`<small>${html(job.error || `Confirmada pelo ${provider} · #${job.receipt}`)}</small>`:""}</td>
        <td>${date(when,job.doc.rule?.timezone)}</td>
        <td>${job.status==="approval"&&state.sending?btn("Aprovar teste","approve",job.id,true):""}
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
  function render() {
    if (!state || controller.signal.aborted) return;
    const focused = route==="dashboard" && content.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused && ["dashboardPeriod","dashboardMetric","dashboardPublications"].find(key=>focused.dataset[key]);
    const focusValue = focusKey ? focused.dataset[focusKey] : null;
    content.innerHTML = views[route]();
    const all = $("[data-select-niches]");
    if (all) all.indeterminate = !all.checked && !!all.form.querySelector("input[name=niches]:checked");
    filterHistory();
    window.OrionFields?.refresh(content);
    if (focusKey) {
      const target = [...content.querySelectorAll("button")].find(button=>button.dataset[focusKey]===focusValue);
      target?.focus({preventScroll:true});
    }
  }

  async function loadAnalytics() {
    if (!state || controller.signal.aborted) return;
    const period = analyticsPeriod;
    const version = ++analyticsVersion;
    analyticsLoading = true;
    analyticsError = false;
    if (route==="dashboard") render();
    try {
      const result = await api("analytics?period="+encodeURIComponent(period));
      if (controller.signal.aborted || version!==analyticsVersion || period!==analyticsPeriod) return;
      if (!result || !result.summary || !result.sources || result.period?.key!==period) throw new Error("Relatório indisponível.");
      if (result.sources.sales?.status==="error" && analyticsCache.has(period)) {
        analyticsError = true;
        return;
      }
      analyticsCache.set(period,result);
    } catch (error) {
      if (controller.signal.aborted || version!==analyticsVersion || period!==analyticsPeriod) return;
      analyticsError = true;
    } finally {
      if (!controller.signal.aborted && version===analyticsVersion && period===analyticsPeriod) {
        analyticsLoading = false;
        if (route==="dashboard") render();
      }
    }
  }

  function selectDashboardPoint(index, focus=false) {
    const model = dashboardChartModel();
    if (!model.points.length) return;
    chartSelected = Math.min(Math.max(Number(index)||0,0),model.points.length-1);
    const point = model.points[chartSelected];
    const dateNode = content.querySelector("[data-dashboard-point-date]");
    const valueNode = content.querySelector("[data-dashboard-point-value]");
    if (dateNode) dateNode.textContent = dashboardDay(point.date);
    if (valueNode) valueNode.textContent = model.config.currency?dashboardMoney(point.value):dashboardCount(point.value);
    for (const button of content.querySelectorAll("[data-dashboard-point]")) {
      const selected = Number(button.dataset.dashboardPoint)===chartSelected;
      button.setAttribute("aria-pressed",String(selected));
      button.tabIndex = selected?0:-1;
      if (selected && focus) button.focus({preventScroll:true});
    }
    for (const dot of content.querySelectorAll("[data-chart-dot]")) dot.setAttribute("r",Number(dot.dataset.chartDot)===chartSelected?"4.5":"2.4");
    for (const step of content.querySelectorAll("[data-dashboard-step]")) step.disabled = Number(step.dataset.dashboardStep)<0?chartSelected===0:chartSelected===model.points.length-1;
  }

  async function reload() {
    const result = await api("state");
    if (controller.signal.aborted) return;
    state = result;
    stateUpdatedAt = Date.now();
    if (!chosenTemplate) chosenTemplate=state.deal_template || "default";
    render();
    if (route==="dashboard") await loadAnalytics();
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
    opened=value;
    preview=null;
    const version=++navVersion;
    if (route!=="ofertas") await open("ofertas");
    render();
    const response=await api(`offers/${encodeURIComponent(value)}/preview?template=${encodeURIComponent(chosenTemplate)}`);
    if (version===navVersion) {
      preview=response;
      if (route!=="ofertas") return;
      render();
      if (focus) focusSection(".af-preview");
      else $("#preview-template")?.focus({preventScroll:true});
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
        name:form.get("name"),mode:form.get("mode"),address:form.get("address"),test:form.has("test"),
      });
      await reload();
      notify("Destino salvo.");
    },
    offer: async form=>{
      const saved=await api("offers","POST",{url:form.get("url"),note:form.get("note")});
      await reload();
      await openOffer(saved.id);
    },
    search: async form=>{
      const filters=Object.fromEntries(form);
      catalog=await api("catalog?"+new URLSearchParams(filters));
      searchQuery=filters;
      render();
      content.querySelector("[data-form=search] button")?.focus({preventScroll:true});
      notify(`${catalog.length} oferta(s) encontrada(s).`);
    },
    phrase: async form=>{
      const key=opened;
      await api("offers/"+key+"/phrase","PUT",{text:form.get("text")});
      await reload();
      await openOffer(key);
      notify("Frase atualizada na prévia.");
    },
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
    rule: async form=>{
      const current = state.rule.find(r=>r.id===editRule);
      const value=Object.fromEntries(form);
      value.days=form.getAll("days").map(Number);
      value.destinations=form.getAll("destinations");
      value.niches=form.getAll("niches");
      if (!value.destinations.length) throw new Error("Escolha pelo menos um destino.");
      if (!value.days.length) throw new Error("Escolha pelo menos um dia da semana.");
      value.blocked=value.blocked.split(",").map(s=>s.trim()).filter(Boolean);
      for (const key of ["interval","daily","repeat_hours","sort"]) value[key]=Number(value[key]);
      for (const key of ["min_price","max_price","min_rating","min_commission","min_sales","min_discount"]) {
        value[key]=value[key]===""?null:Number(value[key]);
      }
      if (current?.active && !await confirmAction("Salvar e pausar automação?",
        `${current.name} ficará pausada até você ativá-la novamente.`,"Salvar e pausar")) return;
      await api("rules/"+(current?.id||id()),"PUT",value);
      editRule=null;
      await reload();
      notify("Regra salva como pausada.");
    },
  };

  async function toggleRule(ident, active) {
    const rule = state.rule.find(r=>r.id===ident);
    const label = active?"Ativar automação":"Pausar automação";
    const detail = active?
      `${rule.name} · ${ruleWindow(rule)}, a cada ${rule.interval} minutos, até ${rule.daily} publicações por dia.`:
      `${rule.name} deixará de programar novas ofertas até ser reativada.`;
    if (!await confirmAction(label+"?",detail,label)) return;
    await api("rules/"+ident+"/activate","POST",{active});
    await reload();
    notify(active?"Automação ativada.":"Automação pausada.");
  }

  async function disconnect(name) {
    const provider = {shopee:"Shopee",telegram:"Telegram",groq:"Groq"}[name];
    if (!await confirmAction(`Desconectar ${provider}?`,"As funções que usam esta conta ficam indisponíveis até uma nova conexão.","Desconectar",true)) return;
    await api("connection/"+name,"DELETE");
    await reload();
    notify(`${provider} desconectada.`);
  }

  const actions = {
    refresh: async ()=>{await reload();notify(route==="dashboard"?"Consulta concluída. Os dados disponíveis estão no painel.":"Dados atualizados.");},
    demo: async ()=>{ const sample=await api("offers/demo","POST"); await reload(); await openOffer(sample.id); },
    "open-offer": openOffer,
    "ai-phrase": async ident=>{
      const row=await api("offers/"+ident+"/phrase","POST");
      await reload();
      await openOffer(ident);
      notify(row.phrase_source==="groq"?"Frase gerada pela Groq.":"Sugestão pronta para editar.");
    },
    "prepare-result": async url=>formActions.offer(new Map([["url",url],["note",""]])),
    copy: async ()=>{ if(preview) {await navigator.clipboard.writeText(preview.text); notify("Texto copiado.");} },
    "copy-link": async ident=>{
      const row=state.offer.find(o=>o.id===ident);
      if (row?.affiliate_url) {await navigator.clipboard.writeText(row.affiliate_url);notify("Link copiado.");}
    },
    "verify-tg": async ()=>{ await api("connection/telegram/verify","POST"); await reload(); notify("Telegram vinculado."); },

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
    "new-rule": async ()=>{ editRule="new"; render(); focusSection(".af-rule-editor"); },
    "edit-rule": async ident=>{ editRule=ident; render(); focusSection(".af-rule-editor"); },
    "close-rule": async ()=>{ editRule=null; render(); content.querySelector("[data-act=new-rule]").focus(); },
    "start-rule": ident=>toggleRule(ident,true),
    "pause-rule": ident=>toggleRule(ident,false),
    approve: async ident=>{
      if (!await confirmAction("Publicar teste?","O teste será enviado ao destino escolhido para esta publicação.","Publicar teste")) return;
      await api("jobs/"+ident,"POST",{action:"approve",confirm_test:true});
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
    if (button) button.disabled=true;
    form.setAttribute("aria-busy","true");
    try { await formActions[form.dataset.form](new FormData(form)); }
    catch(error){ notify(error.message,true); }
    finally { if (button) button.disabled=false; form.removeAttribute("aria-busy"); }
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
    const period = event.target.closest("[data-dashboard-period]");
    if (period && Object.hasOwn(dashboardPeriods,period.dataset.dashboardPeriod)) {
      if (analyticsPeriod!==period.dataset.dashboardPeriod) {
        analyticsPeriod = period.dataset.dashboardPeriod;
        chartSelected = null;
        await loadAnalytics();
      }
      return;
    }
    const metric = event.target.closest("[data-dashboard-metric]");
    if (metric && Object.hasOwn(dashboardMetrics,metric.dataset.dashboardMetric)) {
      analyticsMetric = metric.dataset.dashboardMetric;
      analyticsMetricChosen = true;
      chartSelected = null;
      render();
      return;
    }
    const point = event.target.closest("[data-dashboard-point]");
    if (point) { selectDashboardPoint(point.dataset.dashboardPoint);return; }
    const step = event.target.closest("[data-dashboard-step]");
    if (step) { selectDashboardPoint(dashboardChartModel().selected+Number(step.dataset.dashboardStep),true);return; }
    const publications = event.target.closest("[data-dashboard-publications]");
    if (publications && ["upcoming","recent"].includes(publications.dataset.dashboardPublications)) {
      publicationTab = publications.dataset.dashboardPublications;
      render();
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
    button.disabled=true;
    try { await actions[button.dataset.act](button.dataset.value); }
    catch(error) { notify(error.message,true); }
    finally { button.disabled=false; }
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

  content.addEventListener("change",async event=>{
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
      form.querySelector(".af-test-destination").hidden=assisted;
      form.elements.test.disabled=assisted;
      form.querySelector("[data-assisted-destination]").hidden=!assisted;
      return;
    }
    if (event.target.id!=="preview-template"||!opened) return;
    chosenTemplate=event.target.value;
    try { await openOffer(opened,false); }
    catch(error) { notify(error.message,true); }
  }, {signal: controller.signal});

  content.addEventListener("input",event=>{
    const field=event.target;
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
  void reload().catch(showError);
  return {
    setView(next) {
      if (Object.hasOwn(views,next) && route!==next) {
        route=next;
        render();
        if (route==="dashboard" && state) void loadAnalytics();
      }
    },
    unmount() {
      const dialog = $("#osAffiliateDialog");
      if (dialog?.open) dialog.close("cancel");
      controller.abort();
      host.replaceChildren();
    },
  };
}
