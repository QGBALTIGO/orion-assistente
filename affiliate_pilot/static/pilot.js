import {icon} from "./orion-icons.js";

const $ = selector => document.querySelector(selector);
const workspaceAPI = $('meta[name="orion-affiliate-api"]')?.content;
const apiRoot = workspaceAPI || "/api/";
const assetsRoot = workspaceAPI ? "/api/v1/afiliados-assets" : "/assets";
let orionCsrf;
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
const pageGroups = [
  ["Gestão", [
    ["dashboard","Início","home"],
    ["ofertas","Ofertas","box"],
    ["automacoes","Automações","repeat"],
    ["fila","Fila e histórico","fileCheck"],
  ]],
  ["Configuração", [
    ["plataformas","Plataformas","layers"],
    ["conexoes","Conexões e destinos","link"],
    ["templates","Templates","list"],
  ]],
];
const pages = new Map(pageGroups.flatMap(([, entries]) => entries.map(([key,label]) => [key,label])));
const actionIcons = {
  refresh:"refresh", demo:"eye", "open-offer":"eye", "new-template":"plus", "edit-template":"edit",
  "new-rule":"plus", "edit-rule":"edit", "close-rule":"close", copy:"copy", "copy-link":"link",
  "ai-phrase":"sparkles", "wa-start":"phone", "wa-channel":"shieldCheck", "start-rule":"check",
  "remove-dest":"trash", "cancel-job":"close", "prepare-result":"plus",
};

let state;
let route = "dashboard";
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
let messageTimer;
let whatsappState = null;
let whatsappGroups = null;
let whatsappPoll;
let whatsappRevision = 0;

function notify(text, error=false) {
  const toast = $("#toast");
  toast.innerHTML = `${icon(error?"info":"check")}<span>${html(text)}</span>`;
  toast.classList.toggle("os-error", error);
  toast.hidden = false;
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => toast.hidden = true, 5500);
}

async function api(path, method="GET", payload) {
  const headers = {};
  if (workspaceAPI && method!=="GET") headers["X-Orion-CSRF"]=orionCsrf;
  if (payload!==undefined) headers["Content-Type"]="application/json";
  const response = await fetch(apiRoot + path, {
    method, credentials:"same-origin",
    headers, ...(payload === undefined ? {} : {body:JSON.stringify(payload)}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(new Error(data.detail || "O painel não conseguiu concluir a operação."),
    {status:response.status});
  return data;
}

function confirmAction(title, detail, label, danger=false) {
  const dialog = $("#dialog");
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


function whatsappContents() {
  if (!state.whatsapp_link_available) return `<p>Copie a legenda e baixe o card em Ofertas para publicar pelo WhatsApp.</p>`;
  if (!whatsappState) return `<p role="status">Consultando o aparelho vinculado…</p>`;
  const status = whatsappState?.status;
  if (status === "linked") {
    const groups = whatsappGroups?.groups.map(group=>`<div class="af-item"><strong>${html(group.title)}</strong>
      ${btn("Vincular grupo","wa-add",group.address)}</div>`).join("");
    return `<p>Conta ${html(whatsappState.account)}. Adicione um canal por link ou verifique seus grupos administrados.</p>
      <div class="af-row af-action-strip">${btn("Buscar grupos administrados","wa-groups","",true)}${btn("Desvincular","wa-stop")}</div>
      ${whatsappGroups?`<div class="af-list af-action-strip">${groups||empty("Nenhum grupo administrado","Confira se a conta vinculada é administradora do grupo.","phone")}</div>`:""}
      ${whatsappGroups?.truncated ? "<p>Exibindo até 100 grupos administrados.</p>" : ""}`;
  }
  if (status === "qr_ready" || status === "connecting") {
    const qr = /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(whatsappState?.image || "") ? whatsappState.image : "";
    return `<p>No celular, abra o WhatsApp e toque em <strong>Aparelhos conectados</strong> e <strong>Conectar um aparelho</strong>.</p>
      ${qr ? `<img class="af-qr" src="${qr}" width="230" height="230" alt="QR para conectar o WhatsApp">` : "<p>Solicitando código ao WhatsApp…</p>"}
      <div class="af-row af-action-strip">${btn("Cancelar vínculo","wa-stop")}</div>`;
  }
  return `<p>${status === "error" || status === "offline"?"WhatsApp indisponível no momento. Tente conectar novamente.":"Vincule o aparelho que administra seus grupos e canais."}</p>
    <div class="af-row af-action-strip">${btn("Conectar com QR","wa-start","",true)}</div>`;
}

function whatsappPill() {
  if (!state.whatsapp_link_available) return pill("Publicação assistida");
  return ({
    linked: pill("Vinculado","success"),
    qr_ready: pill("Aguardando leitura","warning"),
    connecting: pill("Conectando","warning"),
    offline: pill("Indisponível","warning"),
    error: pill("Precisa de atenção","warning"),
  })[whatsappState?.status] || pill(whatsappState?"Não vinculado":"Verificando…");
}

async function refreshWhatsapp() {
  clearTimeout(whatsappPoll);
  if (route !== "conexoes" || !state?.whatsapp_link_available) return;
  const version = ++whatsappRevision;
  let data;
  try { data = await api("connection/whatsapp"); }
  catch { data = {status:"offline"}; }
  if (route !== "conexoes" || version !== whatsappRevision) return;
  const changed = whatsappState?.status !== data.status || whatsappState?.image !== data.image ||
    whatsappState?.account !== data.account;
  whatsappState = data;
  if (changed && $("#whatsapp-connection")) {
    $("#whatsapp-connection").innerHTML = whatsappContents();
    $("#whatsapp-status").innerHTML = whatsappPill();
  }
  if (["qr_ready","connecting"].includes(data.status)) whatsappPoll = setTimeout(refreshWhatsapp, 2400);
}

function activityList(jobs) {
  if (!jobs.length) return `<p class="os-subdash-empty">As publicações preparadas aparecerão aqui.</p>`;
  return `<ul class="os-subdash-activity-list">${jobs.map(job=>`<li class="os-subdash-activity-item">
    <span class="os-subdash-activity-icon">${icon(job.status==="published"?"check":"clock")}</span>
    <span class="os-subdash-activity-copy"><strong>${html(job.doc.offer.title)}</strong>
    <small>${html(state.destination.find(d=>d.id===job.destination)?.name || "Destino removido")} · ${date(job.changed)}</small></span>
    ${jobPill(job)}</li>`).join("")}</ul>`;
}

function dashboard() {
  const shopeeConnected = connected("shopee")?.status==="connected";
  const active = activeRules();
  const verified = state.destination.filter(d=>d.verified);
  const next = active.filter(r=>r.next_at).sort((a,b)=>a.next_at-b.next_at)[0];
  const published = state.jobs.filter(job=>job.status==="published").length;
  const metrics = [
    ["Publicadas","send",published,"Entre as últimas 120 publicações registradas."],
    ["Na fila","clock",pending().length,"Programadas, em revisão ou em envio."],
    ["Automações ativas","repeat",active.length,"Regras habilitadas no painel."],
    ["Destinos verificados","shieldCheck",verified.length,"Canais e grupos com administração verificada."],
  ];
  const operation = [
    ["Shopee","box",shopeeConnected?"Conta de afiliados validada":"Conecte sua conta para buscar produtos e gerar links.",
      shopeeConnected?"Conectada":"Pendente","plataformas",shopeeConnected],
    ["Destinos","link",verified.length?verified.slice(0,2).map(d=>d.name).join(", "):"Cadastre um canal ou grupo administrado.",
      verified.length?`${verified.length} ${verified.length===1?"verificado":"verificados"}`:"Pendente","conexoes",verified.length>0],
    ["Automações","repeat",next?`${next.name} · ${ruleDate(next)}`:"Configure uma regra para programar suas próximas ofertas.",
      next?"Programada":"Sem próxima execução","automacoes",!!next],
  ];
  const shortcuts = [
    ["Buscar ofertas","Filtre por categoria ou cole um link","search","ofertas"],
    ["Editar templates","Personalize o card e a legenda","layers","templates"],
    ["Acompanhar a fila","Consulte publicações e erros","fileCheck","fila"],
  ];
  return `<div class="os-subdash af-overview">
    <header class="os-subdash-head">
      <div><h1>Início</h1><p>Acompanhe as ofertas, os canais e as publicações programadas.</p></div>
      <div class="af-heading-actions">${btn("Atualizar","refresh")}${link("Preparar oferta","ofertas","plus",true)}</div>
    </header>
    <div class="os-subdash-stats" aria-label="Indicadores de publicações">${metrics.map(([label,glyph,value,help])=>`
      <article class="os-card os-subdash-stat"><div class="os-subdash-stat-header">${square(glyph)}<span>${label}</span></div>
      <strong class="os-subdash-stat-number">${value.toLocaleString("pt-BR")}</strong><p>${help}</p></article>`).join("")}</div>
    <div class="os-subdash-panels">
      <section class="os-card os-subdash-panel">
        <div class="os-subdash-panel-heading">${square("activity")}<div><h2>Operação</h2><p>Conexões e próxima execução</p></div></div>
        <div class="os-subdash-health-list">${operation.map(([title,glyph,text,status,path,ok])=>`
          <div class="os-subdash-health-row"><span class="os-subdash-health-icon">${icon(glyph)}</span>
          <div class="os-subdash-health-main"><strong>${title}</strong><small>${html(text)}</small></div>
          <div class="os-subdash-health-side">${pill(status,ok?"success":"neutral")}<a class="os-icon-button" href="#${path}" aria-label="Abrir ${title}">${icon("chevron")}</a></div></div>`).join("")}
          ${needingAttention().length?`<p class="af-hint">${icon("info")}<a href="#fila">${needingAttention().length} publicação(ões) precisam de atenção</a></p>`:""}
        </div>
      </section>
      <section class="os-card os-subdash-panel">
        <div class="os-subdash-panel-heading">${square("send")}<div><h2>Atividade recente</h2><p>Últimas publicações registradas</p></div></div>
        ${activityList(state.jobs.slice(0,4))}
        <div class="os-subdash-panel-footer">${link("Ver histórico","fila","arrow")}</div>
      </section>
    </div>
    <section class="os-subdash-actions">
      <header class="os-subdash-section-title"><h2>Ações rápidas</h2></header>
      <div class="os-subdash-action-grid">${shortcuts.map(([label,note,glyph,path])=>`<a class="os-subdash-action os-card af-subdash-actions" href="#${path}">
        ${square(glyph)}<span class="os-subdash-action-copy"><strong>${label}</strong><small>${note}</small></span>${icon("chevron")}</a>`).join("")}</div>
    </section>
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
    <label class="os-check"><input name="dedicated" type="checkbox" required> Este bot é dedicado ao piloto.</label>
    <div class="af-row">${submit("Vincular bot","link")}${linked||pairing?btn("Desconectar","revoke","telegram"):""}</div>
    </form>`;
  const all = state.destination.map(d=>`<div class="af-item"><div><strong>${html(d.name)}</strong>
      <small>${html(defaultMedia(d.mode))}${d.issue?` · ${html(d.issue)}`:""}</small></div>
    <div class="af-row">${pill(d.verified?"Verificado":d.mode==="telegram"?"Verificação pendente":"Publicação assistida",d.verified?"success":"neutral")}
      ${d.mode==="whatsapp_channel" && state.whatsapp_link_available ? btn(d.verified?"Verificar novamente":"Verificar administração","wa-channel",d.id):""}
      ${btn("Remover","remove-dest",d.id)}</div></div>`).join("");
  return heading("Conexões e destinos","Vincule a conta que publica e confirme a administração de cada canal ou grupo.") + `
    <div class="af-page"><div class="af-grid">
      <section class="os-card af-card">
        <header class="af-platform">${brand("whatsapp")}<div><h2>WhatsApp</h2><div id="whatsapp-status">${whatsappPill()}</div></div></header>
        <div id="whatsapp-connection">${whatsappContents()}</div>
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
        <label>Tipo <select name="mode">${Object.keys({telegram:0,whatsapp_group:0,whatsapp_channel:0}).map(m=>option(m,defaultMedia(m),"telegram")).join("")}</select></label>
        <label><span data-address-label>ID numérico ou @ do Telegram</span> <input name="address" maxlength="180" placeholder="@canal_publico ou ID -100…"></label>
        <label class="os-check af-test-destination"><input name="test" type="checkbox"> É um destino de teste e autorizo validar a administração.</label>
        <div class="os-form-actions">${submit("Salvar destino")}</div></form>`,"plus")}
    </div></div>`;
}


function offerPreview() {
  const row = state.offer.find(o=>o.id===opened);
  if (!row) return card("Prévia da publicação",empty("Sua oferta aparece aqui",
    "Gere uma prévia por link, encontre um produto no catálogo ou abra um rascunho.","eye"),"eye","","af-preview");
  const image = `${apiRoot}offers/${encodeURIComponent(opened)}/card.png?template=${encodeURIComponent(chosenTemplate)}`;
  return card("Prévia da publicação",`<div class="af-preview-layout">
    <div class="af-form"><label>Template <select id="preview-template">${templates(chosenTemplate)}</select></label></div>
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
        <label>Destino de teste <select name="destination">${destinations().map(d=>option(d.id,d.name)).join("")}</select></label>
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
              <select name="niche" id="catalog-category">${option("","Todas as categorias",searchQuery.niche)}${state.niches.map(n=>option(n.id,n.name,searchQuery.niche)).join("")}</select></div>
            <label>Ordenar na Shopee <select name="sort">${Object.entries(state.sorts).map(([k,v])=>option(k,v,searchQuery.sort)).join("")}</select></label>
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
        <div class="af-cols"><label>Layout da imagem <select name="layout">${["simple","discount","coupon"].map((key,i)=>option(key,["Oferta simples","Com desconto confirmado","Com cupom"][i],template.layout)).join("")}</select></label>
        <label>Formato <select name="media">${option("card","Card e legenda",template.media)}${option("text","Somente texto",template.media)}</select></label></div>
        <label>Formatação <select name="style">${option("plain","Texto simples",template.style || "plain")}${option("whatsapp","Negrito, itálico e riscado",template.style)}</select></label>
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
          <label>Ordenar na Shopee <select name="sort">${Object.entries(state.sorts).map(([key,value])=>option(key,value,String(rule.sort || 2))).join("")}</select></label>
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
          <label>Template <select name="template_id">${templates(rule.template_id || state.deal_template)}</select></label>
          <div class="af-cols">${input("opens","Início da janela","09:00","time","required")}${input("closes","Fim da janela","21:00","time","required")}</div>
          <small>Para publicar o dia inteiro, use 00:00 nos dois campos.</small>
          ${input("timezone","Fuso horário","America/Cuiaba","text","required")}
          <fieldset><legend>Dias da semana</legend><div class="af-row">${days.map((day,i)=>`<label class="os-check af-check-tile">
            <input type="checkbox" name="days" value="${i}"${!rule.days || rule.days.includes(i)?" checked":""}> ${day}</label>`).join("")}</div></fieldset>
          <div class="af-cols">${input("interval","Intervalo (min)","90","number","required min=10 max=1440")}${input("daily","Limite por dia","8","number","required min=1 max=48")}</div>
          ${input("repeat_hours","Não repetir produto (horas)","48","number","required min=1 max=720")}
          <label>Modo <select name="mode">${option("review","Revisar antes de publicar",rule.mode)}${option("automatic","Envio automático",rule.mode)}</select></label>
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
  for (const control of document.querySelectorAll("[data-history-filter]")) {
    control.setAttribute("aria-pressed",String(control.dataset.historyFilter===historyFilter));
  }
}

const views = {dashboard,plataformas:platforms,conexoes:connections,ofertas:offers,templates:templatePage,automacoes:automations,fila:history};
const content = $("#page-suite");
const shell = $("#appView");

function navigation() {
  $("#nav").innerHTML = pageGroups.map(([group,entries])=>`<div class="os-nav-section">${group}</div>` +
    entries.map(([key,label,symbol])=>`<a class="os-nav-link" href="#${key}" title="${label}" aria-label="${label}">
      ${icon(symbol)}<span>${label}</span></a>`).join("")).join("");
}

function render() {
  if (!state) return;
  $("#company").textContent = state.company;
  $("#breadcrumb").textContent = pages.get(route);
  content.dataset.osCurrentRoute = route;
  for (const anchor of $("#nav").querySelectorAll("a")) {
    if (anchor.hash===`#${route}`) anchor.setAttribute("aria-current","page");
    else anchor.removeAttribute("aria-current");
  }
  content.innerHTML = views[route]();
  const all = $("[data-select-niches]");
  if (all) all.indeterminate = !all.checked && !!all.form.querySelector("input[name=niches]:checked");
  filterHistory();
}

async function reload() {
  const focus = document.activeElement;
  state = await api("state");
  if (!chosenTemplate) chosenTemplate=state.deal_template || "default";
  $("#loginView").hidden=true;
  shell.hidden=false;
  render();
  if (focus?.matches?.("[data-act=refresh]")) content.querySelector("[data-act=refresh]")?.focus();
  void refreshWhatsapp();
}

function showDrawer(open, restoreFocus=true) {
  if (!shell.classList.contains("os-menu-open") && !open) return;
  shell.classList.toggle("os-menu-open",open);
  $("#drawer").setAttribute("aria-expanded",String(open));
  $("#workspace").inert = open;
  if (open) $("#drawer-close").focus();
  else if (restoreFocus) $("#drawer").focus();
}

function setRoute() {
  const page = location.hash.slice(1);
  route = Object.hasOwn(views,page)?page:"dashboard";
  showDrawer(false,false);
  closeThemeMenus();
  render();
  if (state) {
    window.scrollTo(0,0);
    content.focus({preventScroll:true});
  }
  void refreshWhatsapp();
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
  if (route!=="ofertas") location.hash="ofertas";
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
    location.hash="fila";
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
  refresh: async ()=>{await reload();notify("Dados atualizados.");},
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
  "wa-start": async ()=>{
    if (!await confirmAction("Conectar WhatsApp?","Use o aparelho que administra seus canais ou grupos de teste.","Gerar QR")) return;
    await api("connection/whatsapp","POST",{active:true});
    await refreshWhatsapp();
  },
  "wa-stop": async ()=>{
    if (whatsappState?.status==="linked" && !await confirmAction("Desvincular WhatsApp?",
      "As automações que dependem da conta poderão deixar de publicar.","Desvincular",true)) return;
    await api("connection/whatsapp","DELETE");
    whatsappGroups=null;
    await refreshWhatsapp();
    await reload();
  },
  "wa-groups": async ()=>{
    whatsappGroups=await api("connection/whatsapp/groups");
    if ($("#whatsapp-connection")) $("#whatsapp-connection").innerHTML=whatsappContents();
  },
  "wa-channel": async ident=>{
    try { await api("destinations/"+ident+"/verify-channel","POST"); notify("Administração do canal confirmada."); }
    finally { await reload(); }
  },
  "wa-add": async address=>{
    const group=whatsappGroups?.groups.find(row=>row.address===address);
    if (!group || !await confirmAction("Vincular grupo?",`Verifique a administração e cadastre ${group.title} para publicação assistida.`,"Vincular")) return;
    await api("destinations/"+id(),"PUT",{name:group.title.slice(0,80),mode:"whatsapp_group",address,test:true});
    await reload();
    notify("Grupo vinculado para publicação assistida.");
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

$("#login").addEventListener("submit",async event=>{
  event.preventDefault();
  const form=event.currentTarget;
  const button=form.querySelector("[type=submit]");
  $("#login-feedback").textContent="";
  button.disabled=true;
  form.setAttribute("aria-busy","true");
  try {
    await api("login","POST",Object.fromEntries(new FormData(form)));
    form.reset();
    $("#password").type="password";
    $("#toggle-password").setAttribute("aria-pressed","false");
    $("#toggle-password").setAttribute("aria-label","Mostrar senha");
    await reload();
    content.focus({preventScroll:true});
    window.scrollTo(0,0);
  } catch(error) {
    $("#login-feedback").textContent=error.message;
  } finally {
    button.disabled=false;
    form.setAttribute("aria-busy","false");
  }
});
$("#toggle-password").addEventListener("click",event=>{
  const button=event.currentTarget;
  const field=$("#password");
  const visible=field.type==="password";
  field.type=visible?"text":"password";
  button.setAttribute("aria-pressed",String(visible));
  button.setAttribute("aria-label",visible?"Ocultar senha":"Mostrar senha");
});

content.addEventListener("submit",async event=>{
  const form=event.target;
  if (!form.dataset.form) return;
  event.preventDefault();
  const button=event.submitter;
  if (button) button.disabled=true;
  form.setAttribute("aria-busy","true");
  try { await formActions[form.dataset.form](new FormData(form)); }
  catch(error){ notify(error.message,true); }
  finally { if (button) button.disabled=false; form.removeAttribute("aria-busy"); }
});

content.addEventListener("click",async event=>{
  const tab=event.target.closest("[data-offer-tab]");
  const filter=event.target.closest("[data-history-filter]");
  if (tab) {
    offerTab=tab.dataset.offerTab;
    for (const button of content.querySelectorAll("[data-offer-tab]")) {
      const active=button===tab;
      button.setAttribute("aria-pressed",String(active));
      document.getElementById(button.getAttribute("aria-controls")).hidden=!active;
    }
    return;
  }
  if (filter) { historyFilter=filter.dataset.historyFilter;filterHistory();return; }
  const button=event.target.closest("[data-act]");
  if (!button) return;
  button.disabled=true;
  try { await actions[button.dataset.act](button.dataset.value); }
  catch(error) { notify(error.message,true); }
  finally { button.disabled=false; }
});

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
      whatsapp_group: ["ID do grupo do WhatsApp (opcional)","Use Buscar grupos administrados para verificar"],
    }[event.target.value];
    form.querySelector("[data-address-label]").textContent=label;
    form.elements.address.placeholder=hint;
    form.querySelector(".af-test-destination").hidden=event.target.value==="whatsapp_channel";
    form.elements.test.disabled=event.target.value==="whatsapp_channel";
    return;
  }
  if (event.target.id!=="preview-template"||!opened) return;
  chosenTemplate=event.target.value;
  try { await openOffer(opened,false); }
  catch(error) { notify(error.message,true); }
});

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
});

$("#logout").addEventListener("click",async ()=>{
  if (workspaceAPI) { location.assign("/painel"); return; }
  try { await api("logout","POST");location.reload(); }
  catch(error){ notify(error.message,true); }
});
$("#drawer").addEventListener("click",()=>showDrawer(true));
$("#drawer-close").addEventListener("click",()=>showDrawer(false));
$("#scrim").addEventListener("click",()=>showDrawer(false));
$("#nav").addEventListener("click",event=>{
  const anchor=event.target.closest("a");
  if (anchor?.getAttribute("aria-current")==="page" && shell.classList.contains("os-menu-open")) {
    showDrawer(false,false);
    content.focus({preventScroll:true});
  }
});
const mobile=matchMedia("(max-width: 767px)");
mobile.addEventListener("change",event=>{if (!event.matches) showDrawer(false,false);});

function getPreference(key, fallback) {
  try { return localStorage.getItem(key) || fallback; }
  catch { return fallback; }
}
function savePreference(key, value) {
  try { localStorage.setItem(key,value); }
  catch {}
}
function collapseSidebar(collapsed) {
  const control=$("#collapse");
  const label=collapsed?"Expandir menu":"Recolher menu";
  shell.classList.toggle("os-rail-collapsed",collapsed);
  control.setAttribute("aria-expanded",String(!collapsed));
  control.setAttribute("aria-label",label);
  control.title=label;
  control.innerHTML=icon(collapsed?"chevron":"left");
}
$("#collapse").addEventListener("click",()=>{
  const collapsed=!shell.classList.contains("os-rail-collapsed");
  savePreference("orion-affiliates-sidebar",collapsed?"collapsed":"expanded");
  collapseSidebar(collapsed);
});

const systemTheme=matchMedia("(prefers-color-scheme: dark)");
const themeChoices=[["light","Claro","sun"],["dark","Escuro","moon"],["auto","Sistema","monitor"]];
let themePreference=getPreference("orion-theme","auto");

function updateTheme() {
  const name=themePreference==="dark"||(themePreference==="auto"&&systemTheme.matches)?"dark":"light";
  document.documentElement.dataset.theme=name;
  for (const image of document.querySelectorAll("[data-orion-logo]")) image.src=`/brand/orion-brand-${name}.svg`;
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-pressed",String(button.dataset.themeChoice===themePreference));
  }
  for (const button of document.querySelectorAll("[data-theme-toggle]")) button.innerHTML=icon(name==="dark"?"moon":"sun");
}
function closeThemeMenus(restoreFocus=false) {
  for (const toggle of document.querySelectorAll("[data-theme-toggle][aria-expanded=true]")) {
    toggle.setAttribute("aria-expanded","false");
    document.getElementById(toggle.getAttribute("aria-controls")).hidden=true;
    if (restoreFocus) toggle.focus();
  }
}
for (const menu of document.querySelectorAll(".os-theme-menu")) {
  menu.innerHTML=themeChoices.map(([key,label,glyph])=>`<button type="button" data-theme-choice="${key}">${icon(glyph)}${label}</button>`).join("");
  menu.addEventListener("click",event=>{
    const button=event.target.closest("[data-theme-choice]");
    if (!button) return;
    themePreference=button.dataset.themeChoice;
    savePreference("orion-theme",themePreference);
    updateTheme();
    closeThemeMenus(true);
  });
}
for (const button of document.querySelectorAll("[data-theme-toggle]")) {
  button.addEventListener("click",()=>{
    const opening=button.getAttribute("aria-expanded")==="false";
    closeThemeMenus();
    if (opening) {
      const menu=document.getElementById(button.getAttribute("aria-controls"));
      button.setAttribute("aria-expanded","true");
      menu.hidden=false;
      (menu.querySelector("[aria-pressed=true]") || menu.querySelector("button")).focus();
    }
  });
}
document.addEventListener("click",event=>{if (!event.target.closest(".os-theme")) closeThemeMenus();});
addEventListener("keydown",event=>{
  if (event.key!=="Escape" || $("#dialog").open) return;
  closeThemeMenus(true);
  if (shell.classList.contains("os-menu-open")) showDrawer(false);
});

for (const element of document.querySelectorAll("[data-icon]")) element.innerHTML=icon(element.dataset.icon);
systemTheme.addEventListener("change",updateTheme);
updateTheme();
collapseSidebar(getPreference("orion-affiliates-sidebar","expanded")==="collapsed");
navigation();
addEventListener("hashchange",setRoute);
setRoute();

async function start() {
  if (workspaceAPI) {
    $("#logout").innerHTML=icon("left")+" Voltar ao Orion";
    $("#login").hidden=true;
    $(".auth-card-top h2").textContent="Abrindo Afiliados";
    $(".auth-card-top p").textContent="Conferindo o acesso ao seu negócio.";
    const response=await fetch("/auth/me",{credentials:"same-origin",cache:"no-store"});
    const session=response.ok?await response.json():null;
    if (!session?.authenticated || !session.csrf) { location.replace("/painel"); return; }
    orionCsrf=session.csrf;
  }
  await reload();
}

start().catch(error=>{
  if (workspaceAPI) {
    if (error.status===401) { location.replace("/painel"); return; }
    $(".auth-card-top h2").textContent="Afiliados indisponível";
    $(".auth-card-top p").textContent=error.message;
    $(".auth-card-top").insertAdjacentHTML("beforeend",'<a class="os-button os-outline af-subtext" href="/painel">Voltar ao Orion</a>');
  } else if (error.status!==401) $("#login-feedback").textContent=error.message;
});
