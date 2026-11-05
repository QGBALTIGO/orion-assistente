/* Subscription schedules: explicit review, frozen audience and same-request recovery. */
(function () {
  'use strict';
  let active = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const positive = value => integer(value) && value > 0;
  const hash = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
  const instant = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  const day = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
  const clock = value => typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);
  const zone = value => typeof value === 'string' && value.length <= 64 && /^[A-Za-z0-9._+-]+(?:\/[A-Za-z0-9._+-]+)*$/.test(value);
  const contactKey = value => typeof value === 'string' && /^phone:\d{8,15}$/.test(value);
  const nullableText = value => value === null || typeof value === 'string';
  const copy = value => JSON.parse(JSON.stringify(value));
  const number = value => new Intl.NumberFormat('pt-BR').format(value);
  const intervals = [10,20,30,40,50,60];
  const tabs = {active:'Ativo',past_due:'Pendência passada',history:'Histórico',all:'Todos'};
  const activeFilters = {'':'Todos ativos',attention:'Necessita de atenção',queued:'Enfileirado',processing:'Enviando',upcoming:'Próximo'};
  const runStatuses = {pending:'Pendente',running:'Em andamento',completed:'Concluída',failed:'Falhou',uncertain:'Resultado incerto',cancelled:'Cancelada'};
  const recipientStatuses = {'':'Todos',pending:'Pendentes',running:'Em processamento',sent:'Enviados',failed:'Falharam',uncertain:'Incertos',cancelled:'Cancelados'};
  const templateKinds = {text:'Texto',image:'Imagem',video:'Vídeo',audio:'Áudio',document:'Documento',sticker:'Figurinha',ptv:'PTV',location:'Localização'};
  const partStatuses = {accepted:'Aceito pelo transporte',sent:'Aceito pelo transporte',not_sent:'Não enviado',pending:'Pendente',running:'Em processamento',failed:'Falhou',uncertain:'Resultado incerto',cancelled:'Cancelado'};
  const kinds = new Set(['review_create','review_edit','pending_review','cancel_run','cancel_series']);
  const historyKey = 'orionScheduleRecovery';
  const recoveryFields = ['tenant_id','account_scope','idempotency_key','kind','schedule_id','run_id','confirming'];
  const button = (label,action,attrs='',classes='os-outline') => '<button type="button" class="os-button '+classes+'" data-sc-action="'+action+'" '+attrs+'>'+esc(label)+'</button>';
  const field = (label,name,value,type='text',attrs='') => '<label class="os-field"><span>'+esc(label)+'</span><input data-sc-field="'+name+'" name="'+name+'" type="'+type+'" value="'+esc(value)+'" '+attrs+'></label>';
  const select = (label,name,values,value,attrs='') => '<label class="os-field"><span>'+esc(label)+'</span><select data-sc-field="'+name+'" name="'+name+'" '+attrs+'>'+values.map(([key,label,disabled])=>'<option value="'+esc(key)+'"'+(String(key)===String(value)?' selected':'')+(disabled?' disabled':'')+'>'+esc(label)+'</option>').join('')+'</select></label>';
  const loading = label => '<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>'+esc(label)+'</div>';
  const partType = value => templateKinds[String(value || '').replace(/^send_/,'')] || 'Mensagem';
  function date(value,timezone) {
    if (!instant(value)) return 'Não informado';
    try { return new Intl.DateTimeFormat('pt-BR',{timeZone:timezone,dateStyle:'short',timeStyle:'short'}).format(new Date(value)); }
    catch { return new Intl.DateTimeFormat('pt-BR',{timeZone:'UTC',dateStyle:'short',timeStyle:'short'}).format(new Date(value))+' UTC (fuso '+String(timezone)+' sem suporte neste navegador)'; }
  }
  function pageDTO(value) {
    if (!object(value) || !Array.isArray(value.items) || !integer(value.total) || !positive(value.page) || value.page_size !== 30 || value.items.length > 30 || !integer(value.pages) || !zone(value.timezone) || !instant(value.generated_at)) throw Error('invalid_page');
    return value;
  }
  function groupDTO(value) {
    if (!object(value) || !uuid(value.id) || typeof value.name !== 'string' || !positive(value.version) || !integer(value.member_count)) throw Error('invalid_group');
    return value;
  }
  function deviceDTO(value) {
    if (!object(value) || !uuid(value.id) || typeof value.name !== 'string') throw Error('invalid_device');
    return value;
  }
  function progressDTO(value) {
    if (!object(value) || !['total','processed','sent','pending','failed','uncertain','cancelled'].every(key=>integer(value[key])) || !Number.isFinite(value.percent) || value.percent < 0 || value.percent > 100) throw Error('invalid_progress');
    return value;
  }
  function definitionDTO(value) {
    if (!object(value) || typeof value.title !== 'string' || value.title.length > 120 || !uuid(value.device_id) || !Array.isArray(value.group_ids) || value.group_ids.length > 100 || value.group_ids.some(id=>!uuid(id)) || !Array.isArray(value.contact_keys) || value.contact_keys.length > 10000 || value.contact_keys.some(key=>!contactKey(key)) || typeof value.all_contacts !== 'boolean' || !day(value.start_date) || !clock(value.start_time) || !intervals.includes(value.interval_seconds) || typeof value.repeat_every_30_days !== 'boolean' || !['text','template'].includes(value.message_type) || value.message_type === 'text' && (typeof value.text !== 'string' || value.text.length > 1000 || value.template_id !== null) || value.message_type === 'template' && (!uuid(value.template_id) || value.text !== null) || value.timezone !== undefined && !zone(value.timezone)) throw Error('invalid_definition');
    return value;
  }
  function itemDTO(value,expectedSchedule,expectedRun) {
    if (!object(value) || !uuid(value.schedule_id) || !uuid(value.run_id) || expectedSchedule && value.schedule_id !== expectedSchedule || expectedRun && value.run_id !== expectedRun || !positive(value.version) || !positive(value.run_version) || !integer(value.occurrence_index) || typeof value.title !== 'string' || !['active','cancelled','completed'].includes(value.series_status) || !Object.hasOwn(runStatuses,value.status) || typeof value.status_label !== 'string' || !['active','past_due','history'].includes(value.bucket) || value.active_filter !== null && !Object.hasOwn(activeFilters,value.active_filter) || !nullableText(value.attention_reason) || value.scheduled_at !== null && !instant(value.scheduled_at) || value.scheduled_at === null && (!value.repeat_every_30_days || value.active_filter !== 'attention' || !value.attention_reason) || !day(value.local_date) || !clock(value.local_time) || !zone(value.timezone) || !intervals.includes(value.interval_seconds) || typeof value.repeat_every_30_days !== 'boolean' || value.estimated_end_at !== null && !instant(value.estimated_end_at) || !Array.isArray(value.actions) || value.actions.some(action=>!['edit','cancel_run','cancel_series'].includes(action)) || !instant(value.created_at)) throw Error('invalid_schedule');
    if(value.attention_message!==undefined && !nullableText(value.attention_message))throw Error('invalid_attention_message');
    deviceDTO(value.device); progressDTO(value.progress); return value;
  }
  function listDTO(value,counts=true,expectedSchedule=null) {
    pageDTO(value);
    value.items.forEach(item=>itemDTO(item,expectedSchedule));
    if (new Set(value.items.map(item=>item.run_id)).size !== value.items.length) throw Error('duplicate_runs');
    if (counts && (!object(value.counts) || !['active','past_due','history','all','attention','queued','processing','upcoming'].every(key=>integer(value.counts[key])))) throw Error('invalid_counts');
    return value;
  }
  function optionsDTO(value) {
    if (!object(value) || !['devices','templates','groups'].every(key=>Array.isArray(value[key])) || !zone(value.timezone) || !day(value.today) || !object(value.defaults) || !day(value.defaults.start_date) || !clock(value.defaults.start_time) || !intervals.includes(value.defaults.interval_seconds) || typeof value.defaults.repeat_every_30_days !== 'boolean' || value.defaults.message_type !== 'text' || !object(value.limits) || value.limits.title !== 120 || value.limits.text_utf16 !== 1000 || !instant(value.generated_at)) throw Error('invalid_options');
    value.devices.forEach(device=>{deviceDTO(device);if(typeof device.available !== 'boolean' || !nullableText(device.blocked_reason))throw Error('invalid_device_option');});
    value.groups.forEach(groupDTO);
    value.templates.forEach(template=>{if(!uuid(template?.id) || typeof template.name !== 'string' || !Object.hasOwn(templateKinds,template.kind) || !positive(template.revision) || typeof template.available !== 'boolean' || !nullableText(template.blocked_reason))throw Error('invalid_template');});
    return value;
  }
  function contactsDTO(value) {
    pageDTO(value);
    for (const contact of value.items) if (!contactKey(contact?.key) || typeof contact.name !== 'string' || !/^\+\d{8,15}$/.test(contact.phone_e164) || contact.key !== 'phone:'+contact.phone_e164.slice(1) || typeof contact.available !== 'boolean' || !nullableText(contact.blocked_reason) || !hash(contact.version)) throw Error('invalid_contact');
    if (new Set(value.items.map(item=>item.key)).size !== value.items.length) throw Error('duplicate_contacts');
    return value;
  }
  function audienceDTO(value) {
    if (!object(value) || !['total','direct','from_groups','duplicates','blocked','eligible'].every(key=>integer(value[key])) || value.eligible + value.blocked !== value.total || !instant(value.generated_at) || !zone(value.timezone) || value.start_at !== null && !instant(value.start_at) || value.estimated_end_at !== null && !instant(value.estimated_end_at)) throw Error('invalid_audience');
    return value;
  }
  function messageDTO(value) {
    if (!object(value) || !Object.hasOwn(templateKinds,value.type) || !nullableText(value.text) || !nullableText(value.caption) || !nullableText(value.followup) || !nullableText(value.template_name) || !Array.isArray(value.parts) || value.parts.length < 1 || value.parts.length > 3 || value.parts.some(part=>!object(part) || typeof part.type !== 'string' || part.text !== undefined && !nullableText(part.text))) throw Error('invalid_message');
    return value;
  }
  function recipientsDTO(value) {
    pageDTO(value);
    for (const row of value.items) if (!uuid(row?.id) || !positive(row.ordinal) || typeof row.name !== 'string' || !/^\+\d{8,15}$/.test(row.phone) || !row.status || !Object.hasOwn(recipientStatuses,row.status) || typeof row.status_label !== 'string' || !nullableText(row.error_code) || row.error_message!==undefined && !nullableText(row.error_message) || !Array.isArray(row.parts) || row.parts.some(part=>!uuid(part?.id) || typeof part.type !== 'string' || typeof part.status !== 'string' || !nullableText(part.error_code))) throw Error('invalid_recipient');
    return value;
  }
  function detailDTO(value,scheduleId,runId) {
    if (!object(value) || !Array.isArray(value.groups)) throw Error('invalid_detail');
    itemDTO(value.schedule,scheduleId,runId); definitionDTO(value.definition); messageDTO(value.message_preview); value.groups.forEach(groupDTO); recipientsDTO(value.recipients); return value;
  }
  function previewDTO(value,kind,createdAt) {
    if (!object(value) || !hash(value.review_version) || !instant(value.expires_at) || !integer(value.total)) throw Error('invalid_review');
    if (kind === 'pending_review') {
      if (!Array.isArray(value.runs) || value.runs.length !== value.total || value.total > 100 || new Set(value.runs.map(run=>run.run_id)).size !== value.total) throw Error('invalid_pending_review');
      value.runs.forEach(run=>{itemDTO(run);if(run.status !== 'pending' || run.series_status !== 'active' || !instant(run.scheduled_at) || Date.parse(run.scheduled_at)>Date.parse(createdAt))throw Error('invalid_pending_run');});
    } else {
      if (!['eligible','blocked','duplicates','direct','from_groups'].every(key=>integer(value[key])) || value.eligible + value.blocked !== value.total || typeof value.title !== 'string' || !Array.isArray(value.groups) || !object(value.schedule) || !instant(value.schedule.start_at) || !zone(value.schedule.timezone) || value.schedule.estimated_end_at !== null && !instant(value.schedule.estimated_end_at) || typeof value.schedule.repeat_every_30_days !== 'boolean') throw Error('invalid_schedule_review');
      definitionDTO(value.definition); deviceDTO(value.device); value.groups.forEach(groupDTO); messageDTO(value.message_preview);
    }
    return value;
  }
  function operationDTO(value,intent) {
    if (!object(value) || !uuid(value.request_id) || value.idempotency_key !== intent.idempotency_key || value.kind !== intent.kind || !['prepared','succeeded','expired'].includes(value.status) || typeof value.replayed !== 'boolean' || !instant(value.created_at) || value.expires_at !== null && !instant(value.expires_at) || value.schedule_id !== null && !uuid(value.schedule_id) || value.run_id !== null && !uuid(value.run_id) || intent.schedule_id && value.schedule_id !== intent.schedule_id || intent.run_id && value.run_id !== intent.run_id) throw Error('invalid_operation');
    const reviewing = ['review_create','review_edit','pending_review'].includes(value.kind);
    if (value.status === 'prepared') {
      if (!reviewing || value.result !== null) throw Error('invalid_prepared');
      previewDTO(value.preview,value.kind,value.created_at);
      if (value.preview.expires_at !== value.expires_at) throw Error('invalid_expiry');
    } else if (value.preview !== null) throw Error('invalid_completed_preview');
    if (value.status === 'succeeded') {
      if (!object(value.result)) throw Error('invalid_result');
      if (value.kind === 'pending_review') {
        if (!Array.isArray(value.result.released) || !integer(value.result.total) || value.result.total !== value.result.released.length || value.result.released.some(run=>!uuid(run?.schedule_id) || !uuid(run.run_id))) throw Error('invalid_released');
      } else if (value.kind === 'cancel_series' && value.result.cancelled === true) {
        if (value.result.schedule_id !== intent.schedule_id) throw Error('invalid_cancelled_series');
      } else {
        itemDTO(value.result.schedule,value.schedule_id,value.run_id);
        if (!value.schedule_id || !value.run_id) throw Error('invalid_result_target');
      }
    }
    return value;
  }
  function recoveryRecord(value) {
    return object(value) && uuid(value.tenant_id) && hash(value.account_scope) && uuid(value.idempotency_key) && kinds.has(value.kind) && (value.schedule_id === null || uuid(value.schedule_id)) && (value.run_id === null || uuid(value.run_id)) && typeof value.confirming === 'boolean' && (value.kind !== 'review_edit' && value.kind !== 'cancel_run' || uuid(value.schedule_id) && uuid(value.run_id)) && (value.kind !== 'cancel_series' || uuid(value.schedule_id) && value.run_id === null) && (value.kind !== 'pending_review' || value.schedule_id === null && value.run_id === null);
  }
  function navigationState() {
    try {const value=history.state?.[historyKey];return recoveryRecord(value)?{[historyKey]:Object.fromEntries(recoveryFields.map(key=>[key,value[key]]))}:{};} catch {return {};}
  }
  function clearRecovery() {try {const value={...history.state};delete value[historyKey];history.replaceState(value,'',location.href);} catch {}}
  function clearContext() {
    clearRecovery();
    try {const url=new URL(location.href);if(url.searchParams.get('aba')==='programadas'){for(const key of ['schedule_id','run_id','q','contact_q','status','tab','page'])url.searchParams.delete(key);history.replaceState({...history.state},'',url.pathname+url.search+url.hash);}} catch {}
  }
  function errorMessage(error,reading=false) {
    const status=Number(error?.status);
    if(status===401)return 'Sua sessão expirou. Entre novamente no Orion.';
    if(status===403)return 'O acesso às mensagens programadas desta empresa não está disponível.';
    if(status===404)return 'Este registro não está disponível. Atualize a consulta.';
    if(status===409)return 'Os dados ou a autorização mudaram. Consulte o resultado do mesmo pedido antes de continuar.';
    if(status===410)return 'Esta revisão expirou. Consulte o resultado e prepare uma nova revisão.';
    if(status===429)return 'Aguarde um momento antes de consultar novamente.';
    if([400,422].includes(status))return 'Confira os campos, os destinatários e o horário no fuso da empresa. O horário deve existir e ser inequívoco nesse fuso.';
    return reading?'Não foi possível consultar os dados. Tente novamente.':'Não foi possível confirmar a resposta. Consulte o mesmo pedido; nenhuma nova tentativa será iniciada automaticamente.';
  }
  function staleRejection(error,intent,payload) {
    const proof=error?.scheduleRejection;
    return !intent.confirming && error?.status===409 && error?.code==='SCHEDULE_CONFLICT' && object(proof) && proof.reason==='stale_version' && proof.idempotency_key===intent.idempotency_key && proof.kind===intent.kind && proof.schedule_id===intent.schedule_id && proof.run_id===intent.run_id && positive(payload?.expected_version) && proof.expected_version===payload.expected_version && proof.expected_run_version===(payload.expected_run_version??null) && ['review_edit','cancel_run','cancel_series'].includes(intent.kind);
  }

  function mount(bridge) {
    unmount();
    const host=bridge?.host;
    if(!host || !uuid(bridge.tenantId) || typeof bridge.request!=='function')throw Error('Não foi possível confirmar a empresa desta conta.');
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/scheduled-messages';
    const state={alive:true,epoch:0,read:0,view:'list',target:null,runId:null,options:null,data:null,detail:null,editor:null,contacts:null,contactFilters:{q:'',page:1},contactLabels:new Map(),contactsRead:0,contactsLoading:false,contactsError:'',audience:null,audienceRead:0,audienceLoading:false,audienceError:'',recipients:null,recipientFilters:{status:'',q:'',page:1},recipientsRead:0,recipientsLoading:false,recipientsError:'',runs:null,runsPage:1,runsRead:0,runsLoading:false,runsError:'',filters:{tab:'active',status:'',q:'',page:1},loading:false,error:'',notice:'',busy:false,pending:null,operation:null,unresolved:false,missing:false,correcting:false,dialog:null,accountScope:null,revoked:false};
    const fields=window.OrionFields,releaseFields=fields?.bind(host)||(()=>{});
    const controllers=new Set(),readControllers=new Map();
    let contactTimer,audienceTimer,lastTrigger=null;
    const current=(epoch=state.epoch)=>state.alive && epoch===state.epoch && host.isConnected && !state.revoked && (typeof bridge.canAccess!=='function' || bridge.canAccess());
    const writable=()=>current() && !!state.accountScope && typeof crypto.randomUUID==='function' && (typeof bridge.canWrite==='function'?bridge.canWrite():bridge.canWrite!==false);
    const locked=()=>state.busy || !!state.pending;
    const mutationAllowed=()=>writable() && !locked();
    function persist() {
      if(!state.accountScope || !state.pending)return;
      const value={tenant_id:bridge.tenantId,account_scope:state.accountScope,...state.pending};
      try {history.replaceState({...history.state,[historyKey]:Object.fromEntries(recoveryFields.map(key=>[key,value[key]??null]))},'',location.href);} catch {}
    }
    function syncURL() {
      try {const url=new URL(location.href);url.searchParams.set('aba','programadas');for(const key of ['schedule_id','run_id','q','contact_q','status','tab','page'])url.searchParams.delete(key);if(state.target && ['detail','editor'].includes(state.view)){url.searchParams.set('schedule_id',state.target);if(state.runId)url.searchParams.set('run_id',state.runId);}history.replaceState({...history.state},'',url.pathname+url.search+url.hash);} catch {}
    }
    async function request(suffix,method='GET',payload,scope=null) {
      if(!current())throw Object.assign(Error('Acesso indisponível.'),{status:403});
      // A dispatched mutation keeps its server identity even if this view closes.
      if(!scope)return bridge.request(base+suffix,method,payload);
      const abort=new AbortController();
      if(scope){readControllers.get(scope)?.abort();readControllers.set(scope,abort);}
      controllers.add(abort);
      try {return await bridge.request(base+suffix,method,payload,{signal:abort.signal});}
      finally {controllers.delete(abort);if(scope && readControllers.get(scope)===abort)readControllers.delete(scope);}
    }
    function abortReads() {for(const abort of readControllers.values())abort.abort();readControllers.clear();}
    function revoke() {
      if(state.revoked || !state.alive)return;
      state.revoked=true;state.epoch++;state.read++;state.busy=false;state.loading=false;
      clearTimeout(contactTimer);clearTimeout(audienceTimer);for(const abort of controllers)abort.abort();controllers.clear();readControllers.clear();
      state.pending=null;state.operation=null;state.editor=null;state.data=null;state.detail=null;state.options=null;state.contacts=null;state.audience=null;state.recipients=null;state.runs=null;state.contactLabels.clear();state.notice='';state.correcting=false;clearContext();closeDialog(true);render();
    }
    function readError(error) {if([401,403].includes(error?.status))revoke();state.error=errorMessage(error,true);}
    function query(filters) {const value=new URLSearchParams();for(const [key,item] of Object.entries(filters))if(item!=='' && item!==null)value.set(key,String(item));return value.toString();}
    function badge(item) {return '<span class="os-sc-badge os-sc-badge--'+esc(item.active_filter || (item.bucket==='past_due'?'past_due':item.status))+'">'+esc(item.status_label)+'</span>';}
    function attention(item) {return item.attention_message || item.attention_reason;}
    function progress(value) {return '<div class="os-sc-progress"><progress max="100" value="'+value.percent+'" aria-label="Progresso desta ocorrência"></progress><span>'+number(value.processed)+' / '+number(value.total)+' contatos processados · '+number(value.percent)+'%</span></div>';}
    function pagination(value,scope='list',busy=state.loading) {
      return '<nav class="os-pagination" data-sc-pagination="'+scope+'" aria-label="Páginas de '+({list:'agendamentos',contacts:'contatos',recipients:'destinatários',runs:'ocorrências'})[scope]+'"><span>'+number(value.total)+' registro(s) · página '+number(value.page)+' de '+number(Math.max(1,value.pages))+'</span><div class="os-actions">'+button('Anterior','prev','data-sc-scope="'+scope+'" '+(busy || locked() || value.page<=1?'disabled':''),'os-outline os-small')+button('Próxima','next','data-sc-scope="'+scope+'" '+(busy || locked() || value.page>=value.pages?'disabled':''),'os-outline os-small')+'</div></nav>';
    }
    function rowActions(item) {
      const attrs='data-sc-id="'+item.schedule_id+'" data-sc-run="'+item.run_id+'" '+(locked()?'disabled':'');
      return '<div class="os-sc-row-actions">'+button('Visualizar','detail',attrs,'os-ghost os-small')+(writable()?item.actions.map(action=>button(({edit:'Editar',cancel_run:'Cancelar ocorrência',cancel_series:'Cancelar série'})[action],action,attrs,'os-ghost os-small')).join(''):'')+'</div>';
    }
    function table(items) {
      return '<div class="os-table-wrap"><table class="os-table os-sc-table"><thead><tr><th scope="col">Título</th><th scope="col">Dispositivo</th><th scope="col">Agendamento</th><th scope="col">Estado</th><th scope="col">Progresso</th><th scope="col">Ações</th></tr></thead><tbody>'+items.map(item=>'<tr data-sc-row="'+item.run_id+'"><td><strong>'+esc(item.title)+'</strong><small>'+(item.repeat_every_30_days?'Repete a cada 30 dias · ocorrência '+number(item.occurrence_index+1):'Envio único')+'</small></td><td>'+esc(item.device.name)+'</td><td>'+esc(date(item.scheduled_at,item.timezone))+'<small>'+esc(item.timezone)+'</small></td><td>'+badge(item)+(attention(item)?'<small>'+esc(attention(item))+'</small>':'')+'</td><td>'+progress(item.progress)+'</td><td>'+rowActions(item)+'</td></tr>').join('')+'</tbody></table></div>';
    }
    function listHTML() {
      const data=state.data,counts=data?.counts;
      let html='<div class="os-sc-metrics" aria-label="Resumo dos agendamentos">'+[['attention','Necessita de atenção','Devido agora ou problema no dispositivo'],['queue','Na fila / Enviando','Já reivindicado pelo agendador'],['upcoming','Próximo','Agendado para mais tarde'],['past_due','Pendência passada','Agendado antes de hoje']].map(([key,label,help])=>'<article class="os-card" data-sc-metric="'+key+'"><span>'+label+'</span><strong>'+(counts?number(key==='queue'?counts.queued+counts.processing:counts[key]):'—')+'</strong><small>'+help+'</small></article>').join('')+'</div>';
      html+='<section class="os-card os-sc-list"><div class="os-sc-tabs"><div class="os-tabs" role="tablist" aria-label="Agendamentos">'+Object.entries(tabs).map(([key,label])=>'<button type="button" class="os-tab" role="tab" aria-selected="'+(state.filters.tab===key)+'" data-sc-tab="'+key+'" '+(locked()?'disabled':'')+'>'+label+(counts?' ('+number(counts[key])+')':'')+'</button>').join('')+'</div>'+(state.filters.tab==='active'?'<div class="os-sc-active-filters" aria-label="Filtrar agendamentos ativos">'+Object.entries(activeFilters).map(([key,label])=>button(label,'active-filter','data-sc-status="'+key+'" aria-pressed="'+(state.filters.status===key)+'" '+(locked()?'disabled':''),'os-ghost os-small')).join('')+'</div>':'')+'</div>';
      html+='<form class="os-sc-filters" data-sc-form="filters">'+field('Pesquisar por título','q',state.filters.q,'search','maxlength="255" '+(locked()?'disabled':''))+'<div class="os-actions">'+button('Pesquisar','filter',locked()?'disabled':'','')+button('Limpar filtros','reset',locked()?'disabled':'')+'</div></form>';
      if(state.loading)return html+loading('Carregando mensagens programadas…')+'</section>';
      if(!data)return html+'<p class="os-sc-empty">A consulta não está disponível. Use Atualizar para tentar novamente.</p></section>';
      if(!data.items.length)return html+'<div class="os-sc-empty" data-sc-empty><h2>Nenhum agendamento encontrado</h2><p>'+(state.filters.q || state.filters.tab!=='active' || state.filters.status?'Revise as abas e os filtros para consultar outras ocorrências.':'Crie um agendamento e revise os destinatários antes de confirmar.')+'</p></div></section>';
      return html+table(data.items)+pagination(data)+'</section>';
    }
    function messageHTML(message) {
      const shown=new Set(message.parts.flatMap(part=>[part.text,part.caption]).filter(Boolean));
      return '<div class="os-sc-review-message" data-sc-preview><h3>'+esc(message.template_name || partType(message.type))+'</h3>'+message.parts.map((part,index)=>'<div><strong>'+(message.parts.length>1?'Parte '+number(index+1)+' · ':'')+esc(partType(part.type))+'</strong>'+(part.text?'<p class="os-sc-message">'+esc(part.text)+'</p>':'')+(part.caption?'<p class="os-sc-message">'+esc(part.caption)+'</p>':'')+'</div>').join('')+[message.text,message.caption,message.followup].filter(value=>value && !shown.has(value)).map(value=>{shown.add(value);return '<p class="os-sc-message">'+esc(value)+'</p>';}).join('')+'</div>';
    }
    function scheduleFacts(item) {
      return '<dl class="os-sc-facts"><div><dt>Início</dt><dd>'+esc(date(item.scheduled_at,item.timezone))+'<small>'+esc(item.timezone)+'</small></dd></div><div><dt>Fim estimado</dt><dd>'+esc(date(item.estimated_end_at,item.timezone))+'</dd></div><div><dt>Intervalo</dt><dd>'+number(item.interval_seconds)+' segundos</dd></div><div><dt>Repetição</dt><dd>'+(item.repeat_every_30_days?'A cada 30 dias':'Não repete')+'</dd></div></dl>';
    }
    function recipientsHTML() {
      const data=state.recipients;
      let html='<section class="os-card os-sc-recipients"><header><h2>Destinatários desta ocorrência</h2><p class="os-help">Enviado significa aceito pelo transporte; não comprova entrega ou leitura. Resultados incertos permanecem separados.</p><div class="os-tabs" role="tablist" aria-label="Situação dos destinatários">'+Object.entries(recipientStatuses).map(([key,label])=>'<button type="button" class="os-tab" role="tab" aria-selected="'+(state.recipientFilters.status===key)+'" data-sc-recipient-tab="'+key+'" '+(locked()?'disabled':'')+'>'+label+'</button>').join('')+'</div><form class="os-sc-recipient-search" data-sc-form="recipients">'+field('Pesquisar destinatários','recipient_q',state.recipientFilters.q,'search','maxlength="255" '+(locked()?'disabled':''))+button('Pesquisar','recipient-search',locked()?'disabled':'')+'</form></header>';
      if(state.recipientsLoading)return html+loading('Carregando destinatários…')+'</section>';
      if(!data)return html+'<p class="os-sc-empty" role="alert">'+esc(state.recipientsError || 'Não foi possível consultar esta seção. Use Pesquisar para tentar novamente.')+'</p></section>';
      if(!data.items.length)return html+'<p class="os-sc-empty" data-sc-recipient-empty>Nenhum destinatário nesta seção.</p>'+pagination(data,'recipients',false)+'</section>';
      return html+'<div class="os-table-wrap"><table class="os-table os-sc-recipient-table"><thead><tr><th scope="col">Ordem</th><th scope="col">Contato</th><th scope="col">WhatsApp</th><th scope="col">Estado</th><th scope="col">Partes da mensagem</th></tr></thead><tbody>'+data.items.map(row=>'<tr data-sc-recipient="'+row.id+'"><td>'+number(row.ordinal)+'</td><td>'+esc(row.name || 'Não informado')+'</td><td>'+esc(row.phone)+'</td><td>'+esc(row.status_label)+(row.error_message || row.error_code?'<small>'+esc(row.error_message || row.error_code)+'</small>':'')+'</td><td>'+(row.parts.length?'<ul>'+row.parts.map(part=>'<li>'+esc(partType(part.type))+' · '+esc(partStatuses[part.status] || part.status)+(part.error_code?'<small>'+esc(part.error_code)+'</small>':'')+'</li>').join('')+'</ul>':'Aguardando processamento')+'</td></tr>').join('')+'</tbody></table></div>'+pagination(data,'recipients',false)+'</section>';
    }
    function runsHTML() {
      let html='<section class="os-card os-sc-runs"><header><h2>Ocorrências da série</h2></header>';
      if(state.runsLoading)return html+loading('Carregando ocorrências…')+'</section>';
      if(!state.runs)return html+'<div class="os-sc-empty"><p role="alert">'+esc(state.runsError || 'Não foi possível consultar as ocorrências.')+'</p>'+button('Consultar ocorrências','runs-refresh')+'</div></section>';
      return html+(state.runs.items.length?table(state.runs.items):'<p class="os-sc-empty">Nenhuma ocorrência encontrada.</p>')+pagination(state.runs,'runs',false)+'</section>';
    }
    function detailHTML() {
      if(state.loading)return loading('Carregando agendamento…');
      if(!state.detail)return '<section class="os-card os-sc-empty">O agendamento não está disponível nesta consulta.</section>';
      const {schedule:item,message_preview:message,groups}=state.detail;
      return '<section class="os-card os-sc-summary"><header><div><h2>'+esc(item.title)+'</h2>'+badge(item)+'</div>'+rowActions(item)+'</header>'+scheduleFacts(item)+progress(item.progress)+(attention(item)?'<p role="status">'+esc(attention(item))+'</p>':'')+'<p><strong>Dispositivo:</strong> '+esc(item.device.name)+'</p>'+(groups.length?'<p><strong>Grupos:</strong> '+groups.map(group=>esc(group.name)).join(', ')+'</p>':'')+messageHTML(message)+'</section>'+recipientsHTML()+runsHTML();
    }
    function contactSelectionHTML() {
      const editor=state.editor;
      if(editor.all_contacts)return '<p class="os-help" data-sc-all-contacts>Todos os contatos do catálogo serão resolvidos na revisão.</p>';
      return '<div class="os-sc-contact-selection" aria-label="Contatos selecionados">'+editor.contact_keys.map(key=>button((state.contactLabels.get(key)?.name || key.slice(6))+' · remover','contact-remove','data-sc-key="'+esc(key)+'"','os-ghost os-small')).join('')+'</div><p class="os-help">'+number(editor.contact_keys.length)+' contato(s) selecionado(s) diretamente.</p>';
    }
    function contactsHTML() {
      if(state.contactsLoading)return loading('Pesquisando contatos…');
      if(!state.contacts)return '<p class="os-help" role="alert">'+esc(state.contactsError || 'Use Pesquisar contatos para consultar o catálogo.')+'</p>';
      if(!state.contacts.items.length)return '<p class="os-help" data-sc-contact-empty>Nenhum contato encontrado.</p>';
      const editor=state.editor;
      return '<div class="os-sc-choice-list">'+state.contacts.items.map(contact=>'<label class="os-check"><input type="checkbox" name="contact_keys" value="'+esc(contact.key)+'" data-sc-contact="'+esc(contact.key)+'" '+(editor.all_contacts || editor.contact_keys.includes(contact.key)?'checked ':'')+(!contact.available || editor.all_contacts?'disabled':'')+'><span>'+esc(contact.name || contact.phone_e164)+'<small>'+esc(contact.phone_e164)+(contact.blocked_reason?' · '+esc(contact.blocked_reason):'')+'</small></span></label>').join('')+'</div>'+pagination(state.contacts,'contacts',false);
    }
    function estimateHTML() {
      if(state.audienceLoading)return '<span role="status">Calculando destinatários e estimativa…</span>';
      if(state.audienceError)return '<span role="status">'+esc(state.audienceError)+'</span>';
      if(!state.audience)return 'Fim estimado<strong>—</strong>';
      const a=state.audience;
      return 'Fim estimado<strong>'+esc(a.total && a.estimated_end_at?date(a.estimated_end_at,a.timezone):'—')+'</strong><span>'+number(a.total)+' destinatário(s) distinto(s)'+(a.duplicates?' · '+number(a.duplicates)+' repetição(ões) removida(s)':'')+(a.blocked?' · '+number(a.blocked)+' com restrição':'')+'</span><p class="os-help">Estimativa: início + destinatários distintos × intervalo. O tempo real também depende do processamento de cada mensagem.</p>';
    }
    function editorHTML() {
      if(state.loading)return loading('Carregando formulário…');
      if(!state.options || !state.editor)return '<section class="os-card os-sc-empty"><p>O formulário não está disponível nesta consulta. Atualize os dados ou volte aos agendamentos.</p>'+button('Atualizar dados','refresh',locked()?'disabled':'')+'</section>';
      const e=state.editor,o=state.options,disabled=state.busy||(locked()&&!state.correcting)||!writable();
      return '<form class="os-card os-sc-editor" data-sc-form="schedule"><fieldset '+(disabled?'disabled':'')+'><div class="os-sc-form-grid">'+field('Título','title',e.title,'text','required maxlength="120"')+select('Dispositivo','device_id',[['','Selecione um dispositivo'],...o.devices.map(device=>[device.id,device.name+(device.blocked_reason?' · '+device.blocked_reason:''),!device.available])],e.device_id,'required')+'</div><section><h2>Destinatários</h2><p class="os-help">Selecione os grupos e os contatos. Telefones repetidos entram uma única vez na revisão.</p><h3>Grupos</h3><div class="os-actions">'+button('Selecionar todos os grupos','groups-all')+button('Limpar grupos','groups-none')+'</div>'+(o.groups.length?'<div class="os-sc-choice-list">'+o.groups.map(group=>'<label class="os-check"><input type="checkbox" name="group_ids" value="'+group.id+'" data-sc-group="'+group.id+'" '+(e.group_ids.includes(group.id)?'checked':'')+'><span>'+esc(group.name)+'<small>'+number(group.member_count)+' contato(s)</small></span></label>').join('')+'</div>':'<p class="os-help">Nenhum grupo disponível. Os grupos cadastrados em Campanhas aparecem aqui.</p>')+'<h3>Contatos</h3><div class="os-actions">'+button('Selecionar todos os contatos','contacts-all')+button('Limpar contatos','contacts-none')+'</div>'+contactSelectionHTML()+'<div class="os-sc-recipient-search">'+field('Pesquisar contatos','contact_q',state.contactFilters.q,'search','maxlength="255"')+button('Pesquisar contatos','contacts-search')+'</div><div class="os-sc-contacts" aria-live="polite">'+contactsHTML()+'</div></section><section><h2>Quando enviar</h2><div class="os-sc-form-grid">'+field('Data','start_date',e.start_date,'date','required min="'+o.today+'"')+field('Horário','start_time',e.start_time,'time','required step="60"')+field('Fuso horário da empresa','timezone',o.timezone,'text','readonly aria-readonly="true"')+select('Intervalo entre destinatários','interval_seconds',intervals.map(value=>[value,value+' segundos']),e.interval_seconds,'required')+select('Repetir a cada 30 dias','repeat_every_30_days',[['false','Não'],['true','Sim']],String(e.repeat_every_30_days))+'</div><div class="os-sc-estimate" data-sc-estimate aria-live="polite">'+estimateHTML()+'</div>'+(e.repeat_every_30_days?'<p class="os-help">A repetição mantém a data e o horário de origem como referência, a cada 30 dias. A audiência confirmada permanece a mesma; autorizações continuam sendo verificadas.</p>':'')+'</section><section><h2>Mensagem</h2>'+select('Tipo de mensagem','message_type',[['text','Texto'],['template','Template']],e.message_type)+(e.message_type==='text'?'<label class="os-field"><span>Texto da mensagem</span><textarea name="text" data-sc-field="text" required maxlength="1000" rows="6" aria-describedby="osScTextHelp osScTextCount">'+esc(e.text || '')+'</textarea></label><p class="os-help" id="osScTextHelp">Use {name} para personalizar com o nome do contato.</p><p class="os-help" id="osScTextCount" data-sc-text-count>'+number((e.text || '').length)+' / 1.000 caracteres</p>':select('Template da biblioteca','template_id',[['','Selecione um template'],...o.templates.map(template=>[template.id,template.name+' · '+templateKinds[template.kind]+(template.blocked_reason?' · '+template.blocked_reason:''),!template.available])],e.template_id || '','required')+(!o.templates.some(template=>template.available)?'<p class="os-help">Cadastre um template disponível na biblioteca para usá-lo aqui.</p>':''))+'</section><div class="os-form-actions">'+button(state.correcting?'Voltar ao pedido':'Voltar','back')+'<button type="submit" class="os-button" data-sc-action="review">'+(state.correcting?'Revisar correção com o mesmo pedido':'Revisar agendamento')+'</button></div></fieldset></form>';
    }
    function operationBanner() {
      if(!state.pending)return '';
      const message=state.busy?'Aguardando a resposta deste pedido.':state.unresolved?'O resultado deste pedido ainda precisa ser consultado. Novas alterações aguardam sua resolução.':state.operation?.status==='prepared'?'A revisão deste pedido está pronta para ser conferida.':'Confira o resultado antes de iniciar outra alteração.';
      return '<section class="os-card os-sc-summary os-sc-operation"><div><strong>Pedido em acompanhamento</strong><p>'+message+'</p></div>'+button('Consultar pedido','recover',state.busy?'disabled':'')+'</section>';
    }
    function render() {
      if(!state.alive)return;
      if(!state.revoked && !current()){revoke();return;}
      const focused=document.activeElement,focusedField=host.contains(focused)?focused.dataset?.scField:null,selection=focusedField && typeof focused.selectionStart==='number'?[focused.selectionStart,focused.selectionEnd]:null;
      const focusedChoice=host.contains(focused)?['scGroup','scContact'].find(key=>focused.dataset?.[key]):null;
      const title=state.view==='editor'?(state.target?'Editar agendamento':'Criar agendamento'):state.view==='detail'?'Detalhes do agendamento':'Mensagens Programadas';
      host.classList.add('os-scheduled-messages');host.setAttribute('aria-busy',String(state.loading || state.busy));
      let actions='';
      if(!state.revoked){actions=state.view!=='list'?button('Voltar aos agendamentos','back',locked()?'disabled':''):button('Atualizar','refresh',locked()?'disabled':'');if(state.view==='list' && writable())actions+=button('Enviar pendentes agora','pending-review',locked()?'disabled':'')+button('Criar agendamento','new',locked()?'disabled':'','');}
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">'+title+'</h1><p class="os-subtitle">Organize os envios de WhatsApp da sua empresa.</p></div><div class="os-actions">'+actions+'</div></div><p class="os-sc-feedback os-error" role="alert">'+esc(state.error)+'</p><p class="os-sc-feedback os-notice" role="status">'+esc(state.notice)+'</p>'+(state.revoked?'<section class="os-card os-sc-empty">O acesso desta empresa não está disponível. Selecione uma empresa autorizada para continuar.</section>':operationBanner()+(state.view==='editor'?editorHTML():state.view==='detail'?detailHTML():listHTML()));
      fields?.refresh(host);
      if(focusedField){const target=host.querySelector('[data-sc-field="'+focusedField+'"]');if(target && !target.disabled){target.focus({preventScroll:true});if(selection)try{target.setSelectionRange(...selection);}catch{}}}
      else if(focusedChoice){const name=focusedChoice==='scGroup'?'group':'contact',target=host.querySelector('[data-sc-'+name+'="'+focused.dataset[focusedChoice]+'"]');if(target && !target.disabled)target.focus({preventScroll:true});}
    }

    function closeDialog(force=false) {
      const entry=state.dialog;if(!entry || state.busy && !force)return;
      state.dialog=null;clearTimeout(entry.timer);entry.release?.();for(const [name,handler] of [['click',click],['change',change],['cancel',entry.cancel]])entry.element.removeEventListener(name,handler);entry.element.close?.();entry.element.remove();
      if(!force && current()){const target=lastTrigger?.isConnected?lastTrigger:host.querySelector('[data-sc-action="recover"]') || host.querySelector('h1');target?.focus({preventScroll:true});}
    }
    function showDialog(type,data=null) {
      closeDialog(true);
      if(!current())return;
      const element=document.createElement('dialog');element.className='os-dialog os-sc-dialog';element.setAttribute('data-sc-dialog','');element.setAttribute('aria-labelledby','osScDialogTitle');
      const entry={element,type,data,checked:false,timer:null,release:null,cancel:null};state.dialog=entry;
      entry.cancel=event=>{event.preventDefault();if(state.busy)return;if(type==='operation' && state.operation?.status==='prepared' && !state.unresolved && !state.pending?.confirming)cancelReview();else closeDialog();};
      element.addEventListener('click',click);element.addEventListener('change',change);element.addEventListener('cancel',entry.cancel);document.body.appendChild(element);entry.release=fields?.bind(element);renderDialog();element.showModal?.();element.querySelector('h2')?.focus();
    }
    function reviewBody(preview,kind) {
      if(kind==='pending_review')return '<p>Confira as ocorrências devidas que serão liberadas para o agendador.</p><p><strong>'+number(preview.total)+' ocorrência(s)</strong></p><ul class="os-sc-pending-list">'+preview.runs.map(run=>'<li><strong>'+esc(run.title)+'</strong><small>'+esc(date(run.scheduled_at,run.timezone))+' · '+esc(run.timezone)+' · '+esc(run.device.name)+'</small>'+esc(attention(run) || run.status_label)+'</li>').join('')+'</ul><p class="os-help">Esta ação libera somente as ocorrências listadas. O dispositivo e as autorizações continuam sendo verificados antes do envio.</p>';
      return '<p><strong>'+esc(preview.title)+'</strong></p><div class="os-sc-review-counts">'+[['total','Distintos'],['eligible','Elegíveis'],['blocked','Com restrição'],['duplicates','Repetidos removidos']].map(([key,label])=>'<div><strong>'+number(preview[key])+'</strong><span>'+label+'</span></div>').join('')+'</div><dl class="os-sc-facts"><div><dt>Início</dt><dd>'+esc(date(preview.schedule.start_at,preview.schedule.timezone))+'<small>'+esc(preview.schedule.timezone)+'</small></dd></div><div><dt>Fim estimado</dt><dd>'+esc(date(preview.schedule.estimated_end_at,preview.schedule.timezone))+'</dd></div><div><dt>Intervalo</dt><dd>'+number(preview.definition.interval_seconds)+' segundos</dd></div><div><dt>Repetição</dt><dd>'+(preview.schedule.repeat_every_30_days?'A cada 30 dias':'Não repete')+'</dd></div></dl><p><strong>Dispositivo:</strong> '+esc(preview.device.name)+'</p>'+(preview.groups.length?'<p><strong>Grupos:</strong> '+preview.groups.map(group=>esc(group.name)).join(', ')+'</p>':'')+messageHTML(preview.message_preview)+'<p class="os-help">Os destinatários e a mensagem desta revisão ficam definidos na confirmação. O agendador verifica as autorizações novamente antes de cada envio.</p>';
    }
    function renderDialog() {
      const entry=state.dialog;if(!entry || !current())return;
      const focused=document.activeElement,keepConsent=entry.element.contains(focused) && focused.hasAttribute('data-sc-consent'),keepAction=entry.element.contains(focused)?focused.dataset.scAction:null,scroll=entry.element.querySelector('.os-sc-dialog-body')?.scrollTop || 0;
      clearTimeout(entry.timer);
      let title='Acompanhar pedido',body='',footer='',view=entry.type;
      if(entry.type==='cancel') {
        const {item,action}=entry.data;title=action==='cancel_series'?'Cancelar série':'Cancelar ocorrência';
        body='<p><strong>'+esc(item.title)+'</strong></p><p>'+(action==='cancel_series'?'O cancelamento impede novas ocorrências e interrompe o trabalho que ainda estiver pendente.':'O cancelamento interrompe o trabalho ainda pendente desta ocorrência. A repetição da série permanece configurada.')+'</p><p>O histórico e os resultados já registrados serão preservados.</p>';
        footer=button('Voltar','close-dialog',state.busy?'disabled':'')+button('Confirmar cancelamento','commit-cancel',state.busy?'disabled':'','');
      } else {
        const op=state.operation,intent=state.pending;
        if(state.busy){view='loading';body=loading(intent?.confirming?'Confirmando o mesmo pedido…':'Consultando o pedido…');}
        else if(state.unresolved || !op){view='unknown';body='<p role="alert">'+esc(state.error || 'O resultado ainda não pôde ser confirmado.')+'</p><p>Consultar usa a mesma identificação e não repete a ação.</p>';if(state.missing && intent?.retry)body+='<p>O pedido não foi localizado nesta consulta. Você pode repetir explicitamente o mesmo pedido, com a mesma chave.</p>';if(state.missing && !intent?.retry)body+='<p>Os dados do formulário não foram guardados neste navegador. Continue consultando este pedido para preservar sua identificação.</p>';footer=button('Fechar','close-dialog')+button('Consultar novamente','recover')+(state.missing && intent?.retry?button('Repetir o mesmo pedido','retry-same'):'')+(state.missing && intent?.correctable && state.editor?button('Corrigir com a mesma chave','correct'):'');}
        else if(op.status==='prepared') {
          const preview=op.preview,expired=Date.parse(preview.expires_at)<=Date.now();view=expired?'expired':'review';title=intent.kind==='pending_review'?'Revisar pendentes':'Revisar agendamento';
          body=reviewBody(preview,intent.kind)+'<p class="os-help">Revisão válida até '+esc(date(preview.expires_at,state.options?.timezone || 'UTC'))+'.</p>';
          if(expired)body+='<p role="alert">Esta revisão expirou. Encerre e prepare uma nova revisão antes de confirmar.</p>';
          else body+='<label class="os-check os-sc-attestation"><input type="checkbox" data-sc-consent '+(entry.checked?'checked':'')+'><span>Confirmo que os destinatários autorizaram estas mensagens e que revisei o conteúdo e o agendamento.</span></label>';
          footer=(intent.confirming?button('Consultar pedido','recover'):button('Encerrar revisão','cancel-review'))+button(intent.kind==='pending_review'?'Confirmar envio de pendentes':'Confirmar agendamento','confirm',expired || !entry.checked || preview.total===0 || intent.kind!=='pending_review' && preview.eligible===0?'disabled':'','');
          if(!expired)entry.timer=setTimeout(()=>{if(state.dialog===entry){entry.checked=false;renderDialog();}},Math.min(2147483647,Math.max(1,Date.parse(preview.expires_at)-Date.now()+10)));
        } else if(op.status==='expired') {view='expired';title='Revisão expirada';body='<p>A revisão expirou sem uma confirmação concluída. Encerre este pedido para revisar os dados novamente.</p>';footer=button('Encerrar pedido','acknowledge','','');}
        else {view='result';title='Pedido concluído';body=op.kind==='pending_review'?'<p>'+number(op.result.total)+' ocorrência(s) liberada(s) para o agendador.</p>':op.kind.startsWith('cancel_')?'<p>O cancelamento foi registrado. O histórico permanece disponível.</p>':'<p>O agendamento foi confirmado e está disponível para acompanhamento.</p>';footer=button('Conferir resultado','acknowledge','','');}
      }
      entry.element.setAttribute('data-sc-view',view);entry.element.setAttribute('aria-busy',String(state.busy));entry.element.innerHTML='<header class="os-sc-dialog-header"><h2 id="osScDialogTitle" tabindex="-1">'+title+'</h2></header><div class="os-sc-dialog-body">'+body+'</div><footer class="os-sc-dialog-footer">'+footer+'</footer>';fields?.refresh(entry.element);
      entry.element.querySelector('.os-sc-dialog-body').scrollTop=scroll;
      const focusTarget=keepConsent?entry.element.querySelector('[data-sc-consent]'):keepAction?entry.element.querySelector('[data-sc-action="'+keepAction+'"]'):null;
      if(focusTarget && !focusTarget.disabled)focusTarget.focus({preventScroll:true});
    }

    async function load({refresh=false}={}) {
      if(!current()){revoke();return;}
      const read=++state.read,epoch=state.epoch;state.loading=true;state.error='';
      if(state.view==='list')state.data=null;
      else if(state.view==='detail'){state.detail=null;state.recipients=null;state.runs=null;}
      render();
      try {
        if(!state.options || refresh){const options=optionsDTO(await request('/options','GET',undefined,'options'));if(!current(epoch) || read!==state.read)return;state.options=options;}
        if(state.view==='list'){const data=listDTO(await request('?'+query(state.filters),'GET',undefined,'main'));if(!current(epoch) || read!==state.read)return;state.data=data;}
        else if(state.view==='editor' && !state.target){if(!state.editor)state.editor={title:'',device_id:'',group_ids:[],contact_keys:[],all_contacts:false,...copy(state.options.defaults),text:'',template_id:null};}
        else {
          const detail=detailDTO(await request('/'+state.target+(state.runId?'?run_id='+state.runId:''),'GET',undefined,'main'),state.target,state.runId);if(!current(epoch) || read!==state.read)return;
          state.detail=detail;state.runId=detail.schedule.run_id;
          if(state.view==='editor'){if(!detail.schedule.actions.includes('edit'))throw Object.assign(Error('changed'),{status:409});state.editor={...copy(detail.definition),start_date:detail.schedule.local_date,start_time:detail.schedule.local_time};delete state.editor.timezone;}
          else {state.recipients=detail.recipients;state.recipientFilters={status:'',q:'',page:1};state.runsPage=1;}
        }
      } catch(error){if(current(epoch) && read===state.read && error?.name!=='AbortError')readError(error);}
      finally {
        if(current(epoch) && read===state.read){state.loading=false;render();if(state.view==='editor' && state.editor){void loadContacts();void loadAudience();}if(state.view==='detail' && state.detail)void loadRuns();}
      }
    }
    async function loadContacts() {
      if(!current() || state.view!=='editor' || !state.editor)return;
      const read=++state.contactsRead,epoch=state.epoch,editor=state.editor;state.contacts=null;state.contactsLoading=true;state.contactsError='';render();
      try {const value=contactsDTO(await request('/contacts?'+query(state.contactFilters),'GET',undefined,'contacts'));if(!current(epoch) || read!==state.contactsRead || state.editor!==editor)return;state.contacts=value;for(const row of value.items)state.contactLabels.set(row.key,{name:row.name,phone:row.phone_e164});}
      catch(error){if(current(epoch) && read===state.contactsRead && state.editor===editor && error?.name!=='AbortError'){if([401,403].includes(error?.status))revoke();else state.contactsError=errorMessage(error,true);}}
      finally {if(current(epoch) && read===state.contactsRead && state.editor===editor){state.contactsLoading=false;render();}}
    }
    async function loadAudience() {
      if(!current() || state.view!=='editor' || !state.editor)return;
      clearTimeout(audienceTimer);
      const read=++state.audienceRead,epoch=state.epoch,editor=state.editor;
      state.audience=null;state.audienceError='';
      if(!editor.all_contacts && !editor.group_ids.length && !editor.contact_keys.length){state.audienceLoading=false;render();return;}
      state.audienceLoading=true;render();
      const payload={group_ids:[...editor.group_ids],contact_keys:editor.all_contacts?[]:[...editor.contact_keys],all_contacts:editor.all_contacts};
      if(day(editor.start_date) && clock(editor.start_time) && intervals.includes(editor.interval_seconds))Object.assign(payload,{start_date:editor.start_date,start_time:editor.start_time,interval_seconds:editor.interval_seconds});
      try {const value=audienceDTO(await request('/audience','POST',payload,'audience'));if(!current(epoch) || read!==state.audienceRead || state.editor!==editor)return;state.audience=value;}
      catch(error){if(current(epoch) && read===state.audienceRead && state.editor===editor && error?.name!=='AbortError'){if([401,403].includes(error?.status))revoke();else state.audienceError=errorMessage(error,true);}}
      finally {if(current(epoch) && read===state.audienceRead && state.editor===editor){state.audienceLoading=false;render();}}
    }
    async function loadRecipients() {
      if(!current() || state.view!=='detail' || !state.detail)return;
      const read=++state.recipientsRead,epoch=state.epoch,target=state.target,run=state.runId;state.recipients=null;state.recipientsLoading=true;state.recipientsError='';render();
      try {const value=recipientsDTO(await request('/'+target+'/recipients?'+query({...state.recipientFilters,run_id:run}),'GET',undefined,'recipients'));if(!current(epoch) || read!==state.recipientsRead || target!==state.target || run!==state.runId)return;state.recipients=value;}
      catch(error){if(current(epoch) && read===state.recipientsRead && target===state.target && run===state.runId && error?.name!=='AbortError'){if([401,403].includes(error?.status))revoke();else state.recipientsError=errorMessage(error,true);}}
      finally {if(current(epoch) && read===state.recipientsRead && target===state.target && run===state.runId){state.recipientsLoading=false;render();}}
    }
    async function loadRuns() {
      if(!current() || state.view!=='detail' || !state.detail)return;
      const read=++state.runsRead,epoch=state.epoch,target=state.target;state.runs=null;state.runsLoading=true;state.runsError='';render();
      try {const value=listDTO(await request('/'+target+'/runs?page='+state.runsPage,'GET',undefined,'runs'),false,target);if(!current(epoch) || read!==state.runsRead || target!==state.target)return;state.runs=value;}
      catch(error){if(current(epoch) && read===state.runsRead && target===state.target && error?.name!=='AbortError'){if([401,403].includes(error?.status))revoke();else state.runsError=errorMessage(error,true);}}
      finally {if(current(epoch) && read===state.runsRead && target===state.target){state.runsLoading=false;render();}}
    }
    function newIntent(kind,scheduleId=null,runId=null) {
      if(typeof crypto.randomUUID!=='function')throw Error('Não foi possível criar a identificação segura deste pedido. Atualize o navegador.');
      return {idempotency_key:crypto.randomUUID(),kind,schedule_id:scheduleId,run_id:runId,confirming:false,retry:null,correctable:false};
    }
    async function mutate(intent,suffix,payload,{confirming=false}={}) {
      if(!writable() || state.busy || state.pending && state.pending!==intent)return;
      const epoch=state.epoch;state.pending=intent;state.operation=null;state.unresolved=true;state.missing=false;state.correcting=false;state.busy=true;state.error='';intent.confirming=confirming || intent.confirming;intent.retry={suffix,payload:copy(payload),confirming};persist();showDialog('operation');render();renderDialog();
      try {const value=operationDTO(await request(suffix,'POST',payload),intent);if(!current(epoch) || state.pending!==intent)return;state.operation=value;state.unresolved=false;state.missing=false;intent.retry=null;intent.correctable=false;}
      catch(error){if(!current(epoch) || state.pending!==intent)return;if([401,403].includes(error?.status)){state.error=errorMessage(error);revoke();}else if(staleRejection(error,intent,payload)){state.pending=null;state.operation=null;state.unresolved=false;state.missing=false;state.correcting=false;state.busy=false;intent.retry=null;clearRecovery();closeDialog(true);state.notice='Os dados mudaram e este pedido não foi registrado. Confira os dados atuais antes de iniciar outra ação.';await load({refresh:true});}else {intent.correctable=!intent.confirming && ['review_create','review_edit'].includes(intent.kind);state.error=errorMessage(error);}}
      finally {if(current(epoch) && state.pending===intent){state.busy=false;render();renderDialog();}}
    }
    async function recover(show=true) {
      const intent=state.pending;if(!intent || !current() || state.busy)return;
      const epoch=state.epoch;state.busy=true;state.error='';if(show && !state.dialog)showDialog('operation');render();renderDialog();
      try {const value=operationDTO(await request('/requests/'+intent.idempotency_key,'GET',undefined,'recover'),intent);if(!current(epoch) || state.pending!==intent)return;state.operation=value;state.unresolved=false;state.missing=false;state.correcting=false;intent.retry=null;intent.correctable=false;if(state.dialog)state.dialog.checked=false;}
      catch(error){if(!current(epoch) || state.pending!==intent)return;state.unresolved=true;state.missing=error?.status===404;if([401,403].includes(error?.status))revoke();else state.error=state.missing?'O pedido ainda não foi localizado. Consulte novamente para acompanhar a mesma identificação.':errorMessage(error);}
      finally {if(current(epoch) && state.pending===intent){state.busy=false;render();renderDialog();}}
    }
    function cancelReview() {
      if(state.busy || state.unresolved || state.operation?.status!=='prepared' || state.pending?.confirming)return;
      state.pending=null;state.operation=null;state.correcting=false;clearRecovery();closeDialog(true);state.notice='Revisão encerrada nesta tela. Nenhuma confirmação foi enviada.';render();(host.querySelector(state.view==='editor'?'[data-sc-action="review"]':'[data-sc-action="pending-review"]') || host.querySelector('h1'))?.focus({preventScroll:true});
    }
    async function acknowledge() {
      if(state.busy || state.unresolved || !['succeeded','expired'].includes(state.operation?.status))return;
      const op=state.operation;state.pending=null;state.operation=null;state.missing=false;state.correcting=false;clearRecovery();closeDialog(true);state.notice='Resultado conferido.';
      if(op.status==='succeeded' && op.result?.schedule){state.view='detail';state.target=op.result.schedule.schedule_id;state.runId=op.result.schedule.run_id;state.editor=null;}
      else if(op.status==='succeeded'){state.view='list';state.target=null;state.runId=null;state.editor=null;}
      syncURL();await load({refresh:true});host.querySelector('h1')?.focus();
    }
    async function correctReview() {
      const intent=state.pending;
      if(!state.missing || !intent?.correctable || intent.confirming || !state.editor || state.busy || !writable())return;
      const epoch=state.epoch;state.correcting=true;state.loading=true;closeDialog(true);state.error='Confira os dados e revise a correção com a mesma identificação do pedido.';render();
      try {const options=optionsDTO(await request('/options','GET',undefined,'options'));if(!current(epoch) || state.pending!==intent)return;state.options=options;}
      catch(error){if(current(epoch) && state.pending===intent)readError(error);}
      finally {if(current(epoch) && state.pending===intent){state.loading=false;render();host.querySelector('[data-sc-field="title"]')?.focus();void loadContacts();void loadAudience();}}
    }
    async function navigate(view,target=null,runId=null) {
      if(locked())return;abortReads();state.read++;state.contactsRead++;state.audienceRead++;state.recipientsRead++;state.runsRead++;clearTimeout(contactTimer);clearTimeout(audienceTimer);state.view=view;state.target=target;state.runId=runId;state.editor=null;state.detail=null;state.contacts=null;state.audience=null;state.recipients=null;state.runs=null;state.contactLabels.clear();state.contactFilters={q:'',page:1};state.error='';state.notice='';syncURL();await load({refresh:view==='editor'});host.querySelector('h1')?.focus();
    }
    function collectSchedule(form) {
      const values=new FormData(form),schedule=copy(state.editor);
      for(const name of ['title','device_id','start_date','start_time','message_type'])schedule[name]=String(values.get(name)||'');
      schedule.title=schedule.title.trim();schedule.interval_seconds=Number(values.get('interval_seconds'));schedule.repeat_every_30_days=values.get('repeat_every_30_days')==='true';schedule.contact_keys=schedule.all_contacts?[]:schedule.contact_keys;
      schedule.text=schedule.message_type==='text'?String(values.get('text')||''):null;schedule.template_id=schedule.message_type==='template'?String(values.get('template_id')||''):null;delete schedule.timezone;
      return schedule;
    }
    function validateSchedule(schedule) {
      if(!schedule.title || schedule.title.length>120 || !state.options.devices.some(device=>device.id===schedule.device_id && device.available))throw Error('Informe o título e selecione um dispositivo disponível.');
      if(schedule.group_ids.some(id=>!state.options.groups.some(group=>group.id===id)))throw Error('Um grupo selecionado não está mais disponível. Limpe os grupos e selecione novamente.');
      if(schedule.group_ids.length>100)throw Error('Selecione no máximo 100 grupos.');
      if(schedule.contact_keys.length>10000)throw Error('Selecione no máximo 10.000 contatos.');
      if(!schedule.all_contacts && !schedule.group_ids.length && !schedule.contact_keys.length)throw Error('Selecione ao menos um grupo ou contato disponível.');
      if(!day(schedule.start_date) || schedule.start_date<state.options.today || !clock(schedule.start_time) || !intervals.includes(schedule.interval_seconds))throw Error('Confira a data, o horário e o intervalo de envio.');
      if(schedule.message_type==='text' && (!schedule.text?.trim() || schedule.text.length>1000))throw Error('Informe um texto com até 1.000 caracteres.');
      if(schedule.message_type==='text' && [...schedule.text.matchAll(/\{([^{}]+)\}/g)].some(match=>match[1]!=='name'))throw Error('Use somente {name} para personalizar o texto.');
      if(schedule.message_type==='template' && !state.options.templates.some(template=>template.id===schedule.template_id && template.available))throw Error('Selecione um template disponível da biblioteca.');
      definitionDTO(schedule);
    }
    async function submit(event) {
      const form=event.target.closest('[data-sc-form]');if(!form || !host.contains(form))return;event.preventDefault();
      const kind=form.dataset.scForm;
      if(kind==='filters'){if(locked())return;state.filters.q=String(form.elements.q.value).trim().slice(0,255);state.filters.page=1;await load();return;}
      if(kind==='recipients'){if(locked())return;state.recipientFilters.q=String(form.elements.recipient_q.value).trim().slice(0,255);state.recipientFilters.page=1;await loadRecipients();return;}
      if(kind!=='schedule' || !writable() || state.busy || locked()&&!state.correcting || !form.reportValidity())return;
      state.error='';
      try {
        const schedule=collectSchedule(form);validateSchedule(schedule);state.editor=schedule;
        const editing=!!state.target,item=state.detail?.schedule,intent=state.correcting?state.pending:newIntent(editing?'review_edit':'review_create',editing?state.target:null,editing?state.runId:null);
        const payload={idempotency_key:intent.idempotency_key,schedule,...(editing?{run_id:state.runId,expected_version:item.version,expected_run_version:item.run_version}:{})};
        await mutate(intent,editing?'/'+state.target+'/review':'/review',payload);
      } catch(error){state.error=error?.message || 'Confira os dados informados.';render();}
    }
    function input(event) {
      const target=event.target,name=target.dataset.scField;if(!host.contains(target))return;
      if(name==='q'){state.filters.q=target.value.slice(0,255);return;}
      if(name==='recipient_q'){state.recipientFilters.q=target.value.slice(0,255);return;}
      if(name==='contact_q' && state.editor){state.contactFilters.q=target.value.slice(0,255);clearTimeout(contactTimer);contactTimer=setTimeout(()=>{state.contactFilters.page=1;void loadContacts();},300);return;}
      if(!state.editor || locked()&&!state.correcting || !name || !Object.hasOwn(state.editor,name))return;
      if(['interval_seconds','repeat_every_30_days','message_type','template_id','device_id'].includes(name))return;
      state.editor[name]=target.value;
      if(name==='text'){const count=host.querySelector('[data-sc-text-count]');if(count)count.textContent=number(target.value.length)+' / 1.000 caracteres';}
      if(['start_date','start_time'].includes(name)){state.audience=null;state.audienceRead++;readControllers.get('audience')?.abort();clearTimeout(audienceTimer);audienceTimer=setTimeout(()=>void loadAudience(),250);}
    }
    function change(event) {
      const target=event.target;
      if(target.matches('[data-sc-consent]') && state.dialog){state.dialog.checked=target.checked;renderDialog();return;}
      if(!state.editor || !host.contains(target) || locked()&&!state.correcting || !writable())return;
      if(target.dataset.scGroup){const id=target.dataset.scGroup;state.editor.group_ids=target.checked?[...new Set([...state.editor.group_ids,id])]:state.editor.group_ids.filter(value=>value!==id);void loadAudience();return;}
      if(target.dataset.scContact){const key=target.dataset.scContact;state.editor.contact_keys=target.checked?[...new Set([...state.editor.contact_keys,key])]:state.editor.contact_keys.filter(value=>value!==key);void loadAudience();return;}
      const name=target.dataset.scField;
      if(name==='message_type'){state.editor.message_type=target.value;state.editor.text=target.value==='text'?'':null;state.editor.template_id=null;render();}
      else if(name==='interval_seconds'){state.editor.interval_seconds=Number(target.value);void loadAudience();}
      else if(name==='repeat_every_30_days'){state.editor.repeat_every_30_days=target.value==='true';render();}
      else if(['template_id','device_id'].includes(name))state.editor[name]=target.value || null;
      else if(['start_date','start_time'].includes(name)){state.editor[name]=target.value;void loadAudience();}
    }
    async function readForAction(action,scheduleId,runId) {
      if(!mutationAllowed() || !uuid(scheduleId) || !uuid(runId))return;
      if(action==='edit'){await navigate('editor',scheduleId,runId);return;}
      const read=++state.read,epoch=state.epoch;state.loading=true;render();
      try {const detail=detailDTO(await request('/'+scheduleId+'?run_id='+runId,'GET',undefined,'action'),scheduleId,runId);if(!current(epoch) || read!==state.read || locked())return;if(!detail.schedule.actions.includes(action))throw Object.assign(Error('changed'),{status:409});showDialog('cancel',{action,item:detail.schedule});}
      catch(error){if(current(epoch) && read===state.read)readError(error);}
      finally {if(current(epoch) && read===state.read){state.loading=false;render();}}
    }
    async function click(event) {
      const target=event.target.closest('[data-sc-action],[data-sc-tab],[data-sc-recipient-tab]');if(!target || target.disabled || !host.contains(target) && !state.dialog?.element.contains(target))return;
      if(!current()){revoke();return;}
      const action=target.dataset.scAction;
      if(target.closest('dialog'))event.preventDefault();else lastTrigger=target;
      if(target.dataset.scTab){if(locked())return;state.filters.tab=target.dataset.scTab;state.filters.status='';state.filters.page=1;await load();return;}
      if(target.hasAttribute('data-sc-recipient-tab')){if(locked())return;state.recipientFilters.status=target.dataset.scRecipientTab;state.recipientFilters.page=1;await loadRecipients();return;}
      if(action==='review')return;
      if(action==='close-dialog'){closeDialog();return;}
      if(action==='recover'){await recover();return;}
      if(action==='cancel-review'){cancelReview();return;}
      if(action==='acknowledge'){await acknowledge();return;}
      if(action==='retry-same' && state.missing && state.pending?.retry && !state.busy){const intent=state.pending,retry=copy(intent.retry);await mutate(intent,retry.suffix,retry.payload,{confirming:retry.confirming});return;}
      if(action==='correct'){await correctReview();return;}
      if(action==='confirm') {
        const intent=state.pending,preview=state.operation?.preview;
        if(!writable() || state.busy || state.unresolved || state.operation?.status!=='prepared' || !state.dialog?.checked || !preview || Date.parse(preview.expires_at)<=Date.now() || preview.total===0 || intent.kind!=='pending_review' && preview.eligible===0)return;
        const pending=intent.kind==='pending_review';await mutate(intent,pending?'/pending/confirm':'/confirm',{idempotency_key:intent.idempotency_key,review_version:preview.review_version,confirmation:pending?'ENVIAR PENDENTES':'AGENDAR',consent_confirmed:true},{confirming:true});return;
      }
      if(action==='commit-cancel' && state.dialog?.type==='cancel' && mutationAllowed()) {
        const {action:verb,item}=state.dialog.data,intent=newIntent(verb,item.schedule_id,verb==='cancel_run'?item.run_id:null);
        await mutate(intent,'/'+item.schedule_id+'/actions',{idempotency_key:intent.idempotency_key,action:verb,run_id:verb==='cancel_run'?item.run_id:null,expected_version:item.version,expected_run_version:verb==='cancel_run'?item.run_version:null,confirmation:'CANCELAR'});return;
      }
      if(action==='back' && state.correcting){state.correcting=false;showDialog('operation');render();return;}
      if(locked() && !(state.correcting && ['contacts-search','groups-all','groups-none','contacts-all','contacts-none','contact-remove'].includes(action)))return;
      if(action==='new' && writable()){await navigate('editor');return;}
      if(action==='back'){await navigate('list');return;}
      if(action==='refresh'){await load({refresh:true});return;}
      if(action==='detail'){await navigate('detail',target.dataset.scId,target.dataset.scRun);return;}
      if(['edit','cancel_run','cancel_series'].includes(action)){await readForAction(action,target.dataset.scId,target.dataset.scRun);return;}
      if(action==='pending-review' && writable()){const intent=newIntent('pending_review');await mutate(intent,'/pending/review',{idempotency_key:intent.idempotency_key});return;}
      if(action==='filter'){host.querySelector('[data-sc-form="filters"]')?.requestSubmit();return;}
      if(action==='reset'){state.filters={tab:'active',status:'',q:'',page:1};await load();return;}
      if(action==='active-filter'){state.filters.status=target.dataset.scStatus;state.filters.page=1;await load();return;}
      if(action==='recipient-search'){host.querySelector('[data-sc-form="recipients"]')?.requestSubmit();return;}
      if(action==='runs-refresh'){await loadRuns();return;}
      if(action==='prev' || action==='next') {
        const delta=action==='next'?1:-1,scope=target.dataset.scScope;
        if(scope==='list'){state.filters.page+=delta;await load();}
        if(scope==='contacts'){state.contactFilters.page+=delta;await loadContacts();}
        if(scope==='recipients'){state.recipientFilters.page+=delta;await loadRecipients();}
        if(scope==='runs'){state.runsPage+=delta;await loadRuns();}
        return;
      }
      if(!state.editor || !writable())return;
      if(action==='contacts-search'){clearTimeout(contactTimer);state.contactFilters.page=1;await loadContacts();return;}
      if(action==='groups-all')state.editor.group_ids=state.options.groups.map(group=>group.id);
      else if(action==='groups-none')state.editor.group_ids=[];
      else if(action==='contacts-all'){state.editor.all_contacts=true;state.editor.contact_keys=[];}
      else if(action==='contacts-none'){state.editor.all_contacts=false;state.editor.contact_keys=[];}
      else if(action==='contact-remove')state.editor.contact_keys=state.editor.contact_keys.filter(key=>key!==target.dataset.scKey);
      else return;
      await loadAudience();
    }
    const listeners=[['click',click],['input',input],['change',change],['submit',submit]];
    for(const [name,handler] of listeners)host.addEventListener(name,handler);
    const controller={ready:null,load,getState:()=>({view:state.view,busy:state.busy,pending:!!state.pending,loading:state.loading,editor:!!state.editor,correcting:state.correcting}),unmount(){
      if(!state.alive)return;state.alive=false;state.epoch++;state.read++;clearTimeout(contactTimer);clearTimeout(audienceTimer);for(const abort of controllers)abort.abort();controllers.clear();readControllers.clear();closeDialog(true);releaseFields();for(const [name,handler] of listeners)host.removeEventListener(name,handler);state.data=null;state.detail=null;state.editor=null;state.options=null;state.pending=null;state.operation=null;state.contacts=null;state.audience=null;state.recipients=null;state.runs=null;state.contactLabels.clear();host.replaceChildren();host.classList.remove('os-scheduled-messages');host.removeAttribute('aria-busy');
    }};
    active=controller;
    controller.ready=(async()=>{
      try {if(typeof bridge.accountKey==='string' && bridge.accountKey && crypto.subtle){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(bridge.accountKey));if(!current())return;state.accountScope=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');}} catch {}
      if(!current())return;
      const saved=navigationState()[historyKey];
      if(saved && saved.tenant_id===bridge.tenantId && saved.account_scope===state.accountScope){state.pending=Object.fromEntries(['idempotency_key','kind','schedule_id','run_id','confirming'].map(key=>[key,saved[key]]));state.unresolved=true;persist();}else clearRecovery();
      try {const params=new URL(location.href).searchParams;if(uuid(params.get('schedule_id')) && uuid(params.get('run_id'))){state.view='detail';state.target=params.get('schedule_id');state.runId=params.get('run_id');}} catch {}
      syncURL();await load();if(current() && state.pending){showDialog('operation');await recover(false);}
    })();render();return controller;
  }
  function unmount() {active?.unmount();active=null;}
  window.OrionSubscriptionScheduledMessages=Object.freeze({mount,unmount,navigationState,clearRecovery,clearContext});
})();
