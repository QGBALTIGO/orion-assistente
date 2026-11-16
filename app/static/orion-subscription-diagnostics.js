/* Device diagnostics. Reads never admit work; every send requires a server preview. */
(function () {
  'use strict';
  let active = null;
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const version = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
  const instant = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  const nullableText = value => value === null || typeof value === 'string';
  const phone = value => typeof value === 'string' && /^\+[1-9][0-9]{7,14}$/.test(value);
  const latency = value => value === null || typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const statuses = {sent:'Enviado',failed:'Falhou',uncertain:'Resultado incerto',pending:'Pendente'};
  const terminal = value => ['succeeded','failed','uncertain','expired'].includes(value?.status);
  const historyKey = 'orionDiagnosticRecovery';
  const recoveryFields = ['tenant_id','account_scope','idempotency_key','device_id','kind','expected_version'];
  const button = (label, action, disabled=false, primary=false) => '<button type="button" class="os-button '+(primary?'':'os-outline')+'" data-dg-action="'+action+'"'+(disabled?' disabled':'')+'>'+esc(label)+'</button>';
  const loading = label => '<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>'+esc(label)+'</div>';
  const badge = (label, tone='neutral') => '<span class="os-dg-badge os-dg-badge-'+tone+'">'+esc(label)+'</span>';
  function timezone(value) { try { if(typeof value!=='string'||!value)return false;new Intl.DateTimeFormat('pt-BR',{timeZone:value}).format(0);return true; } catch { return false; } }
  function date(value,zone) { if(!instant(value)||!timezone(zone))return 'Não informado';return new Intl.DateTimeFormat('pt-BR',{timeZone:zone,dateStyle:'short',timeStyle:'medium'}).format(new Date(value)); }
  function milliseconds(value) { return value===null?'Não medida':new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(value)+' ms'; }
  function deviceDTO(value) { return object(value)&&uuid(value.id)&&typeof value.name==='string'&&version(value.version); }
  function listDTO(value) {
    if(!object(value)||!Array.isArray(value.devices)||!value.devices.every(deviceDTO)||new Set(value.devices.map(d=>d.id)).size!==value.devices.length||value.selected_device_id!==null&&!uuid(value.selected_device_id)||!instant(value.generated_at)||!timezone(value.timezone))throw Error('invalid_devices');
    return value;
  }
  function proxyDTO(value) {
    return object(value)&&['reachable','failed','uncertain','unavailable'].includes(value.status)&&value.scope==='configured'&&version(value.configuration_version)&&['yes','no','unknown'].includes(value.applied_to_session)&&latency(value.latency_ms)&&(value.observed_at===null||instant(value.observed_at))&&nullableText(value.error_code)&&nullableText(value.reason);
  }
  function reportDTO(value,id) {
    if(!object(value)||!deviceDTO(value.device)||value.device.id!==id||!instant(value.generated_at)||!timezone(value.timezone))throw Error('invalid_report');
    const c=value.connection,s=value.server,p=value.proxy,t=value.test_message,r=value.recent_messages;
    if(!object(c)||typeof c.state!=='string'||typeof c.label!=='string'||!['bridge','stored'].includes(c.source)||!['current','stale','unknown'].includes(c.freshness)||(c.observed_at!==null&&!instant(c.observed_at))||(c.phone_e164!==null&&!phone(c.phone_e164)))throw Error('invalid_connection');
    if(!object(s)||!['available','unavailable','unknown'].includes(s.state)||!latency(s.latency_ms)||(s.observed_at!==null&&!instant(s.observed_at))||s.measurement!=='api_to_bridge_health'||s.state!=='available'&&s.latency_ms!==null)throw Error('invalid_server');
    if(!object(p)||!['direct','configured','unknown'].includes(p.mode)||!nullableText(p.host)||(p.port!==null&&(!Number.isInteger(p.port)||p.port<1||p.port>65535))||!version(p.configuration_version)||!['yes','no','unknown'].includes(p.applied_to_session)||typeof p.can_test!=='boolean'||!nullableText(p.blocked_reason)||(p.last_test!==null&&!proxyDTO(p.last_test)))throw Error('invalid_proxy');
    if(!object(t)||typeof t.can_prepare!=='boolean'||(t.default_recipient_e164!==null&&!phone(t.default_recipient_e164))||typeof t.text!=='string'||t.requires_confirmation!==true||!nullableText(t.blocked_reason))throw Error('invalid_test');
    if(!object(r)||r.limit!==3||!Array.isArray(r.items)||r.items.length>3||new Set(r.items.map(item=>item.id)).size!==r.items.length)throw Error('invalid_recent');
    for(const item of r.items)if(!object(item)||!uuid(item.id)||!instant(item.created_at)||!object(item.recipient)||!nullableText(item.recipient.display_name)||!nullableText(item.recipient.phone_e164)||!object(item.sent)||!nullableText(item.sent.text)||!nullableText(item.sent.caption)||typeof item.sent.truncated!=='boolean'||typeof item.truncated!=='boolean'||!Object.hasOwn(statuses,item.status)||typeof item.status_label!=='string'||!['accepted','not_sent','uncertain','pending','running','failed'].includes(item.outcome))throw Error('invalid_message');
    return value;
  }
  function operationDTO(value,pending) {
    if(!object(value)||!uuid(value.request_id)||value.idempotency_key!==pending.idempotency_key||value.device_id!==pending.device_id||value.kind!==pending.kind||!['prepared','pending','running','succeeded','failed','uncertain','expired'].includes(value.status)||typeof value.replayed!=='boolean'||!instant(value.created_at)||!instant(value.expires_at)||(value.finished_at!==null&&!instant(value.finished_at)))throw Error('invalid_operation');
    const p=value.preview,m=value.message;
    if(p!==null&&(!object(p)||!version(p.confirmation_version)||!object(p.recipient)||!['self','explicit'].includes(p.recipient.mode)||!phone(p.recipient.phone_e164)||typeof p.text!=='string'||!instant(p.expires_at)))throw Error('invalid_preview');
    if(m!==null&&(!object(m)||!uuid(m.effect_id)||!Object.hasOwn(statuses,m.status)||!['accepted','not_sent','uncertain','pending','running','failed'].includes(m.outcome)||!nullableText(m.error_code)||!nullableText(m.reason)))throw Error('invalid_message_result');
    if(value.proxy!==null&&!proxyDTO(value.proxy))throw Error('invalid_proxy_result');
    if(value.kind==='message_test'&&(value.proxy!==null||value.status==='prepared'&&(!p||m)||value.status==='succeeded'&&m?.outcome!=='accepted'))throw Error('invalid_message_operation');
    if(value.kind==='proxy_test'&&(p!==null||m!==null||value.status==='prepared'||value.status==='succeeded'&&value.proxy?.status!=='reachable'))throw Error('invalid_proxy_operation');
    return value;
  }
  function recoveryRecord(value) { return object(value)&&uuid(value.tenant_id)&&version(value.account_scope)&&uuid(value.idempotency_key)&&uuid(value.device_id)&&['message_test','proxy_test'].includes(value.kind)&&version(value.expected_version); }
  function staleRejection(error,intent) {
    const value=error?.diagnosticRejection;
    return Number(error?.status)===409&&error.code==='DIAGNOSTIC_CONTEXT_CHANGED'&&object(value)&&value.reason==='stale_device_version'
      &&value.idempotency_key===intent.idempotency_key&&value.device_id===intent.device_id
      &&value.kind===intent.kind&&value.expected_version===intent.expected_version;
  }
  function navigationState() {
    try { const value=history.state?.[historyKey];return recoveryRecord(value)?{[historyKey]:Object.fromEntries(recoveryFields.map(key=>[key,value[key]]))}:{}; } catch { return {}; }
  }
  function clearRecovery() { try { const value={...history.state};delete value[historyKey];history.replaceState(value,'',location.href); } catch {} }
  function clearContext() { clearRecovery();try { const url=new URL(location.href);if(url.searchParams.get('aba')==='diagnosticos'){url.searchParams.delete('device_id');history.replaceState({...history.state},'',url.pathname+url.search+url.hash);} } catch {} }
  function errorMessage(error) {
    const status=Number(error?.status);
    if(status===401)return 'Sua sessão expirou. Entre novamente no Orion.';
    if(status===403)return 'O acesso aos diagnósticos desta empresa não está mais disponível.';
    if(status===404)return 'O dispositivo ou a solicitação não está disponível.';
    if(status===409)return 'O contexto do dispositivo mudou ou já existe uma operação em andamento. Consulte a solicitação antes de agir novamente.';
    if(status===410)return 'A confirmação expirou. Prepare uma nova mensagem para conferir os dados atuais.';
    if(status===429)return 'Aguarde um momento antes de consultar novamente.';
    if([400,422].includes(status))return 'Confira o telefone com código do país e os dados da solicitação.';
    return 'Não foi possível concluir a consulta. Tente novamente.';
  }
  function mount(bridge) {
    unmount();
    const host=bridge?.host;
    if(!host||!uuid(bridge.tenantId)||typeof bridge.request!=='function')throw Error('Não foi possível confirmar a empresa desta conta.');
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/diagnostics';
    const state={alive:true,read:0,list:null,report:null,selected:'',loading:true,busy:false,revoked:false,error:'',notice:'',phone:'',pending:null,operation:null,missing:false,retryPreparation:false,dialog:null,accountScope:null};
    const releaseFields=window.OrionFields?.bind(host)||(()=>{});
    try {const id=new URL(location.href).searchParams.get('device_id');if(uuid(id))state.selected=id;}catch{}
    const current=()=>state.alive&&host.isConnected&&(typeof bridge.canAccess!=='function'||bridge.canAccess());
    const writable=()=>current()&&!state.revoked&&!!state.accountScope&&(typeof bridge.canWrite==='function'?bridge.canWrite():bridge.canWrite!==false);
    const request=(suffix,method='GET',payload)=>{if(!current()||state.revoked)throw Object.assign(Error('Acesso indisponível.'),{status:403});return bridge.request(base+suffix,method,payload);};
    function persist() { if(!state.accountScope||!state.pending)return;try {history.replaceState({...history.state,[historyKey]:{tenant_id:bridge.tenantId,account_scope:state.accountScope,...state.pending}},'',location.href);}catch{} }
    function syncURL() {try {const url=new URL(location.href);url.searchParams.set('aba','diagnosticos');if(state.selected)url.searchParams.set('device_id',state.selected);else url.searchParams.delete('device_id');history.replaceState({...history.state},'',url.pathname+url.search+url.hash);}catch{} }
    function revoke() {state.revoked=true;state.list=null;state.report=null;state.pending=null;state.operation=null;state.phone='';state.notice='';state.selected='';clearRecovery();closeDialog(true);syncURL();}
    function detailRow(label,value) {return '<div><dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd></div>';}
    function recent() {
      const d=state.report;
      if(!d.recent_messages.items.length)return '<div class="os-empty" data-dg-empty-messages>Nenhuma mensagem registrada para este dispositivo.</div>';
      return '<div class="os-table-scroll os-dg-table-scroll" tabindex="0" role="region" aria-label="Últimas mensagens; role para consultar todas as colunas"><table class="os-table os-dg-table"><thead><tr><th scope="col">Hora</th><th scope="col">Destinatário</th><th scope="col">Mensagem</th><th scope="col">Status</th></tr></thead><tbody>'+d.recent_messages.items.map(item=>'<tr data-dg-row="'+item.id+'"><td><time datetime="'+esc(item.created_at)+'">'+esc(date(item.created_at,d.timezone))+'</time></td><td><strong>'+esc(item.recipient.display_name||item.recipient.phone_e164||'Contato sem nome')+'</strong>'+(item.recipient.display_name?'<small>'+esc(item.recipient.phone_e164||'Número não registrado')+'</small>':'')+'</td><td><span class="os-dg-kind">'+esc(item.sent.type_label||'Mensagem')+'</span><p class="os-dg-message">'+esc(item.sent.text||item.sent.media?.file_name||'Conteúdo sem texto.')+'</p>'+(item.sent.caption?'<p class="os-dg-message">'+esc(item.sent.caption)+'</p>':'')+(item.truncated||item.sent.truncated?'<small>Exibição parcial do conteúdo registrado.</small>':'')+'</td><td>'+badge(statuses[item.status],{sent:'success',failed:'danger',uncertain:'warning',pending:'neutral'}[item.status])+(item.status==='sent'?'<small>Aceito pelo transporte. Recebimento não confirmado.</small>':'')+'</td></tr>').join('')+'</tbody></table></div>';
    }
    function proxyResult(value) {
      if(!value)return '';
      const stale=value.configuration_version!==state.report?.proxy.configuration_version||value.freshness==='stale';
      const label={reachable:'Conexão verificada',failed:'Falha no teste',uncertain:'Resultado incerto',unavailable:'Teste indisponível'}[value.status];
      return '<div class="os-dg-proxy-result" data-dg-proxy-result>'+badge(label,stale?'neutral':value.status==='reachable'?'success':value.status==='failed'?'danger':'warning')+(stale?'<p>Este resultado pertence a uma configuração anterior.</p>':'')+'<p>'+esc(value.reason||'Consulte os dados registrados para este teste.')+'</p><dl class="os-dg-details">'+detailRow('Tempo do teste',milliseconds(value.latency_ms))+detailRow('Verificado em',date(value.observed_at,state.report?.timezone||state.list?.timezone))+'</dl><p class="os-help">O teste verifica a conexão pelo proxy. O estado do WhatsApp é apresentado separadamente.</p></div>';
    }
    function reportHTML() {
      const d=state.report,c=d.connection,s=d.server,p=d.proxy,t=d.test_message;
      const connectionLabel=c.freshness==='current'?c.label:c.freshness==='stale'?'Último estado: '+c.label:'Não confirmado';
      const freshLabel=c.freshness==='current'?'Consulta atual à conexão':c.freshness==='stale'?'Informação anterior, sem confirmação atual':'Sem observação atual disponível';
      const applied={yes:'Aplicada à sessão atual',no:'Ainda não aplicada à sessão',unknown:'Aplicação à sessão não confirmada'}[p.applied_to_session];
      return '<div class="os-dg-status-grid"><section class="os-card os-dg-card" data-dg-section="connection" aria-labelledby="orionDiagConnection"><h2 id="orionDiagConnection">Status do Dispositivo</h2><p class="os-dg-state">'+badge(connectionLabel,c.freshness==='current'&&c.state==='connected'?'success':c.freshness==='stale'?'warning':'neutral')+'</p><p>'+esc(freshLabel)+'</p><dl class="os-dg-details">'+detailRow('Número da conexão',c.phone_e164||'Não informado')+detailRow('Observado em',date(c.observed_at,d.timezone))+'</dl></section><section class="os-card os-dg-card" data-dg-section="server" aria-labelledby="orionDiagServer"><h2 id="orionDiagServer">Status do Servidor</h2><p class="os-dg-state">'+badge({available:'Disponível',unavailable:'Indisponível',unknown:'Não confirmado'}[s.state],s.state==='available'?'success':s.state==='unavailable'?'warning':'neutral')+'</p><dl class="os-dg-details">'+detailRow('Tempo de resposta',milliseconds(s.latency_ms))+detailRow('Consultado em',date(s.observed_at,d.timezone))+'</dl><p class="os-help">Tempo de resposta entre o Orion e o servidor de conexão.</p></section></div><section class="os-card os-dg-recent" data-dg-section="recent" aria-labelledby="orionDiagRecent"><header><h2 id="orionDiagRecent">Últimas Mensagens</h2><p>As três tentativas mais recentes deste dispositivo.</p></header>'+recent()+'</section><div class="os-dg-actions-grid"><section class="os-card os-dg-card" data-dg-section="proxy" aria-labelledby="orionDiagProxy"><h2 id="orionDiagProxy">Diagnósticos de Proxy</h2><p>'+badge(p.mode==='configured'?'Proxy configurado':p.mode==='direct'?'Conexão direta':'Configuração não confirmada')+'</p>'+(p.mode==='configured'?'<dl class="os-dg-details">'+detailRow('Host',p.host||'Não informado')+detailRow('Porta',p.port===null?'Não informada':p.port)+'</dl><p class="os-help">'+esc(applied)+'</p>':'<p class="os-help">'+esc(p.mode==='direct'?'Este dispositivo usa a conexão sem proxy.':'Consulte novamente para obter a configuração atual.')+'</p>')+(p.blocked_reason?'<p class="os-help">'+esc(p.blocked_reason)+'</p>':'')+button('Testar Conexão de Proxy','proxy-test',!writable()||!p.can_test||state.busy||!!state.pending)+proxyResult(state.operation?.kind==='proxy_test'?state.operation.proxy:p.last_test)+'</section><section class="os-card os-dg-card" data-dg-section="message" aria-labelledby="orionDiagMessage"><h2 id="orionDiagMessage">Mensagem de Teste</h2><p>Confira o destinatário e confirme o envio na próxima etapa.</p><form data-dg-form="test-message"><div class="os-field"><label for="orionDiagPhone">Número de Telefone <span>(opcional)</span></label><input id="orionDiagPhone" class="os-input" type="tel" inputmode="tel" autocomplete="off" maxlength="40" data-dg-field="phone" value="'+esc(state.phone)+'" aria-describedby="orionDiagPhoneHelp" placeholder="Ex.: +55 67 99999-9901"'+(state.busy||state.pending&&!state.retryPreparation?' disabled':'')+'><p id="orionDiagPhoneHelp" class="os-help">Inclua o código do país. Deixe vazio para usar o próprio número confirmado do dispositivo'+(t.default_recipient_e164?': '+esc(t.default_recipient_e164):', quando disponível')+'.</p></div>'+(t.blocked_reason?'<p class="os-help">'+esc(t.blocked_reason)+'</p>':'')+'<button type="submit" class="os-button" data-dg-action="prepare"'+(!writable()||!t.can_prepare||state.busy||state.pending&&!state.retryPreparation?' disabled':'')+'>'+esc(state.retryPreparation?'Preparar novamente com a mesma chave':'Enviar Mensagem de Teste')+'</button></form></section></div><p class="os-help os-dg-timestamp">Consulta de '+esc(date(d.generated_at,d.timezone))+'. Horários em '+esc(d.timezone)+'.</p>';
    }
    function operationMessage(operation) {
      if(!operation)return state.missing?'A solicitação ainda não foi encontrada. Consulte novamente ou repita explicitamente a mesma preparação.':'Aguardando confirmação do resultado. Consulte a solicitação antes de iniciar outro teste.';
      if(operation.status==='prepared')return 'A mensagem está preparada. Confira o destinatário e o texto antes de confirmar.';
      if(operation.status==='expired')return 'Esta solicitação expirou. Nenhuma nova tentativa será iniciada por ela.';
      if(operation.status==='uncertain')return 'O resultado deste teste permanece incerto. Consulte o registro novamente antes de decidir sobre outro teste.';
      if(operation.kind==='proxy_test')return operation.status==='succeeded'?'A conexão pelo proxy configurado foi verificada.':operation.proxy?.reason||'O teste de proxy está em andamento.';
      if(operation.message?.outcome==='accepted')return 'O transporte aceitou a mensagem de teste. O recebimento pelo contato não está confirmado.';
      if(operation.status==='failed')return operation.message?.reason||'A tentativa de teste apresentou falha.';
      return 'Mensagem de teste registrada. A tentativa aguarda o resultado do transporte.';
    }
    function operationHTML() {
      if(!state.pending&&!state.operation)return '';
      const op=state.operation;
      const title=op?.kind==='proxy_test'||state.pending?.kind==='proxy_test'?'Teste de proxy':'Mensagem de teste';
      return '<section class="os-notice os-dg-operation" data-dg-operation aria-label="'+title+'"><div><strong>'+title+'</strong><p>'+esc(operationMessage(op))+'</p></div><div class="os-actions">'+(state.pending?button(op?.status==='prepared'?'Revisar confirmação':'Consultar resultado','recover',state.busy):'')+(state.pending&&terminal(op)?button('Concluir consulta','acknowledge',state.busy):'')+'</div></section>';
    }
    function render() {
      if(!state.alive)return;
      const field=document.activeElement?.dataset?.dgField,position=document.activeElement?.selectionStart;
      host.className='os-diagnostics';host.setAttribute('aria-busy',String(state.loading||state.busy));
      const options=state.list?.devices||[],missing=state.selected&&!options.some(d=>d.id===state.selected);
      host.innerHTML='<header class="os-title-row"><div><h1>Diagnósticos</h1><p>Consulte a conexão e verifique o funcionamento do seu dispositivo.</p></div><div class="os-actions">'+button('Atualizar','refresh',state.busy||state.loading||state.revoked)+'</div></header><div class="os-notice" data-dg-feedback role="status"'+(!state.notice?' hidden':'')+'>'+esc(state.notice)+'</div><section class="os-card os-dg-selector"><div class="os-field"><label for="orionDiagDevice">Selecionar Dispositivo</label><select class="os-input" id="orionDiagDevice" data-dg-field="device"'+(state.busy||state.pending||state.revoked||!state.list?' disabled':'')+'><option value="">Selecione um dispositivo</option>'+options.map(d=>'<option value="'+d.id+'"'+(d.id===state.selected?' selected':'')+'>'+esc(d.name)+'</option>').join('')+(missing?'<option value="'+state.selected+'" selected>Dispositivo indisponível</option>':'')+'</select></div><p class="os-help">Selecione para consultar o status e as últimas mensagens.</p></section>'+operationHTML()+(state.error?'<div class="os-notice os-error" data-dg-error role="alert">'+esc(state.error)+(!state.revoked?button('Consultar novamente','refresh',state.busy):'')+'</div>':'')+(state.loading?loading(state.selected?'Consultando diagnósticos…':'Carregando dispositivos…'):state.report?reportHTML():state.list&&!state.error?'<div class="os-card os-empty" data-dg-empty>'+esc(options.length?'Selecione um dispositivo para consultar os diagnósticos.':'Nenhum dispositivo disponível para diagnóstico.')+'</div>':'');
      window.OrionFields?.refresh(host);
      if(field){const node=host.querySelector('[data-dg-field="'+field+'"]:not([disabled])');node?.focus({preventScroll:true});if(node&&typeof position==='number')try{node.setSelectionRange(position,position);}catch{}}
    }
    async function load() {
      if(!current()||state.revoked)return;
      const read=++state.read,selected=state.selected;state.loading=true;state.error='';state.report=null;render();
      try {
        const list=listDTO(await request(''));if(!current()||read!==state.read)return;state.list=list;
        if(selected){const data=reportDTO(await request('/'+selected),selected);if(!current()||read!==state.read||state.selected!==selected)return;state.report=data;}
      }catch(error){if(!current()||read!==state.read)return;if([401,403].includes(Number(error?.status)))revoke();state.error=errorMessage(error);}
      finally{if(current()&&read===state.read){state.loading=false;syncURL();render();}}
    }
    function closeDialog(force=false) {
      const entry=state.dialog;if(!entry||state.busy&&!force)return;state.dialog=null;entry.element.removeEventListener('click',click);entry.element.removeEventListener('cancel',cancel);try{entry.element.close();}catch{}entry.element.remove();if(!force&&entry.trigger?.isConnected)entry.trigger.focus({preventScroll:true});
    }
    function openDialog() {
      if(state.dialog){renderDialog();return;}
      const element=document.createElement('dialog'),title='orion-dg-'+crypto.randomUUID();element.className='os-dialog os-dg-dialog';element.setAttribute('data-dg-dialog','');element.setAttribute('aria-labelledby',title);state.dialog={element,title,trigger:document.activeElement};element.addEventListener('click',click);element.addEventListener('cancel',cancel);document.body.append(element);renderDialog();element.showModal();element.querySelector('h2')?.focus({preventScroll:true});
    }
    function renderDialog() {
      const entry=state.dialog;if(!entry||!current())return;
      const op=state.operation,preview=op?.preview,isPrepared=op?.status==='prepared',isTerminal=terminal(op),mode=isPrepared?'confirm':isTerminal?'result':state.missing?'missing':'pending';
      const focused=entry.element.contains(document.activeElement),action=document.activeElement?.dataset?.dgAction;
      entry.element.dataset.dgView=mode;entry.element.setAttribute('aria-busy',String(state.busy));
      let body='<p>'+esc(operationMessage(op))+'</p>';
      if(preview)body+='<dl class="os-dg-details os-dg-preview"><div><dt>Destinatário</dt><dd data-dg-preview-recipient>'+esc(preview.recipient.phone_e164)+'<small>'+esc(preview.recipient.mode==='self'?'Próprio dispositivo':'Número informado')+'</small></dd></div>'+detailRow('Dispositivo',state.report?.device.name||state.list?.devices.find(d=>d.id===state.pending?.device_id)?.name||'Dispositivo selecionado')+'</dl><section class="os-dg-preview-text"><h3>Mensagem</h3><p>'+esc(preview.text)+'</p></section>'+(isPrepared?'<p class="os-help">Confirmação válida até '+esc(date(preview.expires_at,state.report?.timezone||state.list?.timezone))+'.</p>':'');
      if(state.notice)body+='<div class="os-notice" data-dg-dialog-feedback role="status">'+esc(state.notice)+'</div>';
      let actions=button(isPrepared?'Cancelar':'Fechar',isPrepared?'cancel':'close',state.busy);
      if(isPrepared)actions+=button('Confirmar envio','confirm',state.busy||!writable(),true);
      else if(state.pending){actions+=button('Consultar resultado','recover',state.busy);if(isTerminal)actions+=button('Concluir consulta','acknowledge',state.busy,true);if(state.missing)actions+=button(state.pending.kind==='message_test'?'Preparar novamente com a mesma chave':'Repetir o mesmo teste de proxy',state.pending.kind==='message_test'?'repeat-prepare':'repeat-proxy',state.busy||!writable(),true);}
      entry.element.innerHTML='<header class="os-dg-dialog-header"><h2 id="'+entry.title+'" tabindex="-1">'+(isPrepared?'Confirmar mensagem de teste':isTerminal?'Resultado do teste':'Conferir solicitação')+'</h2></header><div class="os-dg-dialog-body">'+body+'</div><footer class="os-dg-dialog-footer">'+actions+'</footer>';
      if(focused)(action&&entry.element.querySelector('[data-dg-action="'+action+'"]:not([disabled])')||entry.element.querySelector('h2'))?.focus({preventScroll:true});
    }
    function receive(value,pending,show=false) {
      const operation=operationDTO(value,pending);state.operation=operation;state.missing=false;state.notice='';state.phone='';state.busy=false;render();if(show)openDialog();else renderDialog();
    }
    function pending(kind) {return {kind,device_id:state.selected,idempotency_key:crypto.randomUUID(),expected_version:state.report.device.version};}
    async function mutate(intent,suffix,payload,{show=false,admission=false}={}) {
      if(!writable()||state.busy)return;
      state.pending=intent;state.missing=false;state.retryPreparation=false;state.busy=true;state.notice='Registrando a solicitação…';persist();render();renderDialog();
      try {const value=await request(suffix,'POST',payload);if(!current()||state.pending!==intent)return;receive(value,intent,show);}
      catch(error){if(!current()||state.pending!==intent)return;state.busy=false;
        if([401,403].includes(Number(error?.status))){revoke();state.error=errorMessage(error);render();return;}
        // Only an identity-bound stale-version rejection proves that neither
        // this request nor an earlier delayed admission can create the intent.
        // A generic 409 can also happen after a proxy was already dispatched.
        if(admission&&staleRejection(error,intent)){
          state.pending=null;state.operation=null;state.missing=false;state.retryPreparation=false;state.phone='';
          state.notice='A configuração do dispositivo mudou e esta solicitação não foi criada. Confira os dados atualizados antes de iniciar outro teste.';
          clearRecovery();closeDialog(true);await load();return;
        }
        if([400,422].includes(Number(error?.status))&&!state.operation){state.pending=null;clearRecovery();state.notice=errorMessage(error);}
        else{state.operation=null;state.notice=Number(error?.status)>=400&&Number(error?.status)<500?errorMessage(error):'Não foi possível confirmar a solicitação. Consulte o resultado; nenhum novo teste será iniciado automaticamente.';}
        render();if(show&&state.pending)openDialog();else renderDialog();
      }finally{if(current()&&state.pending===intent){state.busy=false;render();renderDialog();}}
    }
    async function prepare(repeat=false) {
      if(!writable()||state.busy||!state.report?.test_message.can_prepare||state.pending&&!repeat)return;
      const intent=repeat?state.pending:pending('message_test');if(!intent||intent.kind!=='message_test'||intent.device_id!==state.selected)return;
      const number=state.phone.trim();state.operation=null;
      await mutate(intent,'/'+intent.device_id+'/test-messages/prepare',{idempotency_key:intent.idempotency_key,expected_version:intent.expected_version,phone:number||null},{show:true,admission:true});
    }
    async function confirm() {
      const op=state.operation,intent=state.pending;
      if(!op||op.status!=='prepared'||!op.preview||!intent||!writable()||state.busy)return;
      await mutate(intent,'/'+intent.device_id+'/test-messages/'+op.request_id+'/confirm',{idempotency_key:intent.idempotency_key,confirmation_version:op.preview.confirmation_version,confirmation:'ENVIAR'},{show:true});
    }
    async function proxyTest(repeat=false) {
      if(!writable()||state.busy||!state.report?.proxy.can_test||state.pending&&!repeat)return;
      const intent=repeat?state.pending:pending('proxy_test');if(!intent||intent.kind!=='proxy_test'||intent.device_id!==state.selected)return;state.operation=null;
      await mutate(intent,'/'+intent.device_id+'/proxy-tests',{idempotency_key:intent.idempotency_key,expected_version:intent.expected_version},{admission:true});
    }
    async function recover(show=true) {
      const intent=state.pending;if(!current()||!intent||state.busy||state.revoked)return;state.busy=true;state.notice='Consultando a solicitação registrada…';state.missing=false;state.retryPreparation=false;render();renderDialog();
      try {const value=await request('/requests/'+intent.idempotency_key);if(!current()||state.pending!==intent)return;receive(value,intent,show);}
      catch(error){if(!current()||state.pending!==intent)return;state.busy=false;
        if([401,403].includes(Number(error?.status))){revoke();state.error=errorMessage(error);render();return;}
        state.missing=Number(error?.status)===404;state.operation=null;state.notice=state.missing?'A solicitação ainda não foi encontrada. Nenhuma repetição foi iniciada. Se a preparação não chegou ao servidor, informe novamente o telefone antes de prepará-la com a mesma chave.':'Não foi possível consultar o resultado. Consulte novamente antes de iniciar outro teste.';render();if(show)openDialog();else renderDialog();
      }finally{if(current()){state.busy=false;render();renderDialog();}}
    }
    function cancel(event) {event?.preventDefault();if(state.busy)return;if(state.operation?.status==='prepared'){state.pending=null;state.operation=null;state.notice='Preparação cancelada. Nenhuma mensagem foi admitida.';state.phone='';clearRecovery();}closeDialog();render();}
    function input(event) {if(event.target.matches('[data-dg-field=phone]'))state.phone=event.target.value;}
    function change(event) {if(!event.target.matches('[data-dg-field=device]')||state.busy||state.pending)return;state.read++;state.selected=uuid(event.target.value)?event.target.value:'';state.report=null;state.operation=null;state.phone='';state.notice='';closeDialog();syncURL();void load();}
    function submit(event) {if(!event.target.matches('[data-dg-form=test-message]'))return;event.preventDefault();void prepare(state.retryPreparation&&state.missing);}
    function click(event) {
      const node=event.target.closest('[data-dg-action]');if(!node||node.disabled||!current())return;
      const action=node.dataset.dgAction;if(action==='prepare')return;event.preventDefault();if(state.busy)return;
      if(action==='refresh'){void load();return;}if(action==='proxy-test'){void proxyTest();return;}if(action==='confirm'){void confirm();return;}if(action==='recover'){void recover();return;}
      if(action==='cancel'){cancel();return;}if(action==='close'){closeDialog();return;}
      if(action==='acknowledge'&&terminal(state.operation)){state.pending=null;clearRecovery();closeDialog();render();return;}
      if(action==='repeat-proxy'&&state.missing){closeDialog();void proxyTest(true);return;}
      if(action==='repeat-prepare'&&state.missing){closeDialog();state.retryPreparation=true;state.notice='Informe novamente o telefone, ou deixe vazio para o próprio dispositivo. A mesma chave da solicitação será usada.';render();host.querySelector('[data-dg-field=phone]')?.focus();return;}
    }
    const controller={load,unmount(){if(!state.alive)return;state.alive=false;state.read++;closeDialog(true);releaseFields();for(const [name,fn] of [['click',click],['input',input],['change',change],['submit',submit]])host.removeEventListener(name,fn);state.list=null;state.report=null;state.operation=null;state.phone='';state.pending=null;host.replaceChildren();host.removeAttribute('aria-busy');}};
    active=controller;for(const [name,fn] of [['click',click],['input',input],['change',change],['submit',submit]])host.addEventListener(name,fn);
    render();controller.ready=(async()=>{
      try{if(bridge.accountKey&&crypto.subtle){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(bridge.accountKey));if(!current())return;state.accountScope=Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');}}catch{}
      if(!current())return;const saved=navigationState()[historyKey];if(saved&&saved.tenant_id===bridge.tenantId&&saved.account_scope===state.accountScope){const {tenant_id,account_scope,...intent}=saved;state.pending=intent;state.selected=intent.device_id;}else if(saved)clearRecovery();
      await load();if(current()&&state.pending&&!state.revoked)await recover(false);
    })();return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionSubscriptionDiagnostics=Object.freeze({mount,unmount,navigationState,clearRecovery,clearContext});
})();
