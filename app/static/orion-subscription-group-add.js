/* Native Orion group membership. Previews are read-only; only Add starts work. */
(()=>{
  'use strict';
  const historyKey='orionGroupAddRecovery',recoveryFields=['tenant_id','account_scope','idempotency_key'];
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const copy=value=>JSON.parse(JSON.stringify(value));
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
  const integer=value=>Number.isSafeInteger(value)&&value>=0;
  const instant=value=>typeof value==='string'&&/(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value));
  const phone=value=>typeof value==='string'&&/^\+[1-9]\d{7,14}$/.test(value);
  const clean=(value,max,required=false)=>typeof value==='string'&&value.length<=max&&!/[\u0000-\u001f\u007f]/.test(value)&&![...value].some(char=>{const point=char.codePointAt(0);return point>=0xd800&&point<=0xdfff;})&&(!required||!!value.trim());
  const remoteId=value=>clean(value,47,true)&&/^[0-9]{5,20}(?:-[0-9]{1,20})?@g\.us$/.test(value);
  const date=value=>typeof value==='string'&&/^\d{4}-\d\d-\d\d$/.test(value)&&Number.isFinite(Date.parse(value+'T00:00:00Z'))&&new Date(value+'T00:00:00Z').toISOString().slice(0,10)===value;
  const zone=value=>{try{return clean(value,100,true)&&!!new Intl.DateTimeFormat('pt-BR',{timeZone:value});}catch{return false;}};
  const nullable=(value,check)=>value===null||check(value);
  const errorCode=error=>error?.code||error?.detail?.code;
  const readableDevice=device=>device?.available===true&&device.status==='connected';
  const deviceStatuses={disabled:'Não conectado',stopped:'Desconectado',starting:'Preparando conexão',connecting:'Preparando conexão',reconnecting:'Reconectando',qr_ready:'Pronto para conectar',qr_delayed:'Aguardando QR Code',qr_expired:'QR Code expirou',pairing:'Aguardando confirmação no WhatsApp',pair_expired:'Código expirou',pair_failed:'Vínculo não confirmado',connected:'Conectado',disconnected:'Desconectado',logged_out:'Desvinculado',error:'Precisa de atenção',unavailable:'Estado indisponível',removing:'Exclusão em andamento'};
  const previewStatuses={ready:'Disponível',already_member:'Já está no grupo',blocked:'Indisponível'};
  const memberStatuses={pending:'Aguardando',running:'Adicionando',added:'Adicionado',already_member:'Já está no grupo',not_added:'Não adicionado',unknown:'Resultado incerto',cancelled:'Cancelado'};
  const batchStatuses={pending:'Aguardando processamento',running:'Adicionando contatos',completed:'Adição concluída',partial:'Concluído com ocorrências',unknown:'Resultado incerto',cancelled:'Adição cancelada'};
  const running=status=>status==='pending'||status==='running';
  const button=(label,action,disabled=false,kind='os-outline')=>'<button type="button" class="os-button '+kind+'" data-ga-action="'+action+'" '+(disabled?'disabled':'')+'>'+esc(label)+'</button>';
  const notice=(message,error=false)=>'<div class="os-notice'+(error?' os-error':'')+'" '+(error?'role="alert" data-ga-error':'role="status"')+'><p>'+esc(message)+'</p></div>';
  let active=null;

  function deviceDTO(value,expected){
    if(!object(value)||!uuid(value.id)||expected&&value.id!==expected||!clean(value.name,200,true)||!clean(value.status,80,true)||typeof value.available!=='boolean'||!nullable(value.blocked_reason,v=>clean(v,1000))||!hash(value.version))throw Error('invalid_device');return value;
  }
  function localGroupDTO(value){
    if(!object(value)||!uuid(value.id)||!clean(value.name,100,true)||!integer(value.version)||value.version<1||!integer(value.member_count)||value.member_count>5000||!integer(value.blocked_count)||value.blocked_count>value.member_count||typeof value.archived!=='boolean')throw Error('invalid_local_group');return value;
  }
  function remoteDTO(value,expected){
    if(!object(value)||!remoteId(value.id)||expected&&value.id!==expected||!clean(value.name,100,true)||!integer(value.member_count)||value.member_count>5000)throw Error('invalid_remote_group');return value;
  }
  function optionsDTO(value){
    if(!object(value)||!Array.isArray(value.devices)||value.devices.length>100||!Array.isArray(value.groups)||value.groups.length>100||!instant(value.generated_at)||!zone(value.timezone)||value.limits?.recipients!==5000||value.limits.preview_seconds!==300||value.limits.interval_seconds!==3)throw Error('invalid_options');
    value.devices.forEach(row=>deviceDTO(row));value.groups.forEach(localGroupDTO);
    if(new Set(value.devices.map(row=>row.id)).size!==value.devices.length||new Set(value.groups.map(row=>row.id)).size!==value.groups.length)throw Error('duplicate_options');return value;
  }
  function groupsDTO(value,deviceId){
    if(!object(value)||!Array.isArray(value.items)||value.items.length>1000||value.total!==value.items.length||!integer(value.unavailable_groups??0)||value.unavailable_groups>1000)throw Error('invalid_groups');deviceDTO(value.device,deviceId);
    for(const row of value.items){remoteDTO(row);if(typeof row.can_add!=='boolean'||!nullable(row.blocked_reason,v=>clean(v,1000)))throw Error('invalid_group_access');}
    if(new Set(value.items.map(row=>row.id)).size!==value.items.length)throw Error('duplicate_groups');return value;
  }
  function previewDTO(value,request){
    if(!object(value)||value.source!==request.source||!object(value.filters)||!Array.isArray(value.members)||value.members.length>5000||!object(value.counts)||!instant(value.generated_at))throw Error('invalid_preview');
    deviceDTO(value.device,request.device_id);
    for(const key of ['contact_group_id','without_group','search','created_from','created_to','source_group_id'])if(value.filters[key]!==request[key])throw Error('mismatched_filters');
    if(request.destination_group_id){remoteDTO(value.destination,request.destination_group_id);if(typeof value.destination.is_admin!=='boolean'||!nullable(value.destination.blocked_reason,v=>clean(v,1000)))throw Error('invalid_destination');}
    else if(value.destination!==null)throw Error('unexpected_destination');
    if(request.source==='group')remoteDTO(value.source_group,request.source_group_id);else if(value.source_group!==null)throw Error('unexpected_source');
    const counts={ready:0,already_member:0,blocked:0};
    for(const row of value.members){if(!object(row)||!phone(row.phone)||!clean(row.name,100)||!Object.hasOwn(previewStatuses,row.status)||!nullable(row.blocked_reason,v=>clean(v,1000)))throw Error('invalid_preview_member');counts[row.status]++;}
    if(new Set(value.members.map(row=>row.phone)).size!==value.members.length||!['total','ready','already_member','blocked','unavailable','duplicates'].every(key=>integer(value.counts[key]))||value.counts.total!==value.members.length||Object.keys(counts).some(key=>value.counts[key]!==counts[key]))throw Error('invalid_preview_counts');
    const executable=readableDevice(value.device)&&value.destination?.is_admin===true&&counts.ready>0;
    if(value.preview_token!==null&&(!executable||!clean(value.preview_token,360000,true)||value.preview_token.length<100||!instant(value.expires_at)))throw Error('invalid_preview_authority');
    if(value.preview_token===null&&value.expires_at!==null||executable&&value.preview_token===null)throw Error('missing_preview_authority');return value;
  }
  function batchDTO(value,intent,previous){
    const b=value?.batch;
    if(!object(value)||!object(b)||!uuid(b.id)||!uuid(b.idempotency_key)||b.idempotency_key!==intent.idempotency_key||!object(b.device)||!uuid(b.device.id)||!clean(b.device.name,200,true)||!object(b.destination)||!remoteId(b.destination.id)||!clean(b.destination.name,100,true)||!['contacts','group'].includes(b.source)||!Object.hasOwn(batchStatuses,b.status)||!integer(b.version)||b.version<1||b.interval_seconds!==3||!integer(b.total)||b.total<1||b.total>5000||!object(b.progress)||!instant(b.created_at)||!nullable(b.started_at,instant)||!nullable(b.finished_at,instant)||!nullable(b.error_code,v=>clean(v,100))||!Array.isArray(value.members)||value.members.length!==b.total||typeof value.replayed!=='boolean')throw Error('invalid_batch');
    if(previous&&(b.id!==previous.batch.id||b.version<previous.batch.version||b.device.id!==previous.batch.device.id||b.destination.id!==previous.batch.destination.id||b.source!==previous.batch.source||b.total!==previous.batch.total)||intent.expected&&(b.device.id!==intent.expected.device_id||b.destination.id!==intent.expected.destination_group_id||b.source!==intent.expected.source))throw Error('mismatched_batch');
    const counts=Object.fromEntries(Object.keys(memberStatuses).map(key=>[key,0]));
    for(const row of value.members){if(!object(row)||!uuid(row.id)||!integer(row.ordinal)||row.ordinal<1||row.ordinal>b.total||!phone(row.phone)||!clean(row.name,100)||!Object.hasOwn(memberStatuses,row.status)||!nullable(row.error_code,v=>clean(v,100))||!nullable(row.started_at,instant)||!nullable(row.finished_at,instant))throw Error('invalid_batch_member');counts[row.status]++;}
    if(new Set(value.members.map(row=>row.id)).size!==b.total||new Set(value.members.map(row=>row.phone)).size!==b.total||new Set(value.members.map(row=>row.ordinal)).size!==b.total||Object.keys(counts).some(key=>!integer(b.progress[key])||b.progress[key]!==counts[key])||!running(b.status)&&(counts.pending||counts.running))throw Error('invalid_batch_progress');
    const remaining=counts.pending+counts.running;
    const terminal=counts.unknown?'unknown':counts.added+counts.already_member===b.total?'completed':counts.cancelled===b.total?'cancelled':'partial';
    if(running(b.status)?!remaining||b.status==='pending'&&counts.running>0:b.status!==terminal)throw Error('incoherent_batch_status');
    if(previous){const identities=new Map(previous.members.map(row=>[row.id,row]));if(value.members.some(row=>{const old=identities.get(row.id);return !old||old.phone!==row.phone||old.ordinal!==row.ordinal;}))throw Error('mismatched_member_identity');}
    if(intent.expected&&JSON.stringify(value.members.map(row=>row.phone).sort())!==JSON.stringify(intent.expected.phones))throw Error('mismatched_batch_members');return value;
  }
  function recoveryRecord(value){return object(value)&&uuid(value.tenant_id)&&hash(value.account_scope)&&uuid(value.idempotency_key);}
  function navigationState(){try{const value=history.state?.[historyKey];return recoveryRecord(value)?{[historyKey]:Object.fromEntries(recoveryFields.map(key=>[key,value[key]]))}:{};}catch{return {};}}
  function clearRecovery(){try{const next={...history.state};delete next[historyKey];history.replaceState(next,'',location.href);}catch{}}
  function clearContext(){active?.revoke();clearRecovery();}
  function unmount(){const previous=active;active=null;previous?.destroy();}

  function mount(bridge){
    unmount();const host=bridge.host,base='/api/v1/tenants/'+bridge.tenantId+'/subscription-whatsapp/group-add';
    if(!host||!uuid(bridge.tenantId))throw Error('invalid_group_add_context');
    const emptyFilters=()=>({contact_group_id:'all',search:'',created_from:'',created_to:''});
    const state={alive:true,revoked:false,epoch:0,scope:null,options:null,groups:[],unavailableGroups:0,deviceId:'',destinationId:'',source:'contacts',sourceGroupId:'',filters:emptyFilters(),preview:null,previewRequest:null,selected:new Set(),loading:'',busy:false,error:'',identityError:'',pending:null,batch:null,missing:false};
    const controllers=new Map();let expiryTimer=null,pollTimer=null,controller;
    const allowed=()=>{try{return bridge.canAccess?.()!==false;}catch{return false;}};
    const current=(epoch=state.epoch)=>state.alive&&!state.revoked&&active===controller&&host.isConnected&&allowed()&&epoch===state.epoch;
    const writable=()=>current()&&!!state.scope&&bridge.canWrite?.()!==false;
    const editable=()=>current()&&!state.busy&&!state.pending;
    const expired=()=>!state.preview?.expires_at||Date.parse(state.preview.expires_at)<=Date.now();
    function valid(epoch){if(current(epoch))return true;if(active===controller&&state.alive&&!allowed())revoke();return false;}
    function abortReads(){for(const abort of controllers.values())abort.abort();controllers.clear();}
    function clearPreview(){clearTimeout(expiryTimer);expiryTimer=null;state.preview=null;state.previewRequest=null;state.selected.clear();}
    function invalidate(){state.epoch++;abortReads();state.loading='';clearTimeout(expiryTimer);expiryTimer=null;if(state.preview)state.preview={...state.preview,preview_token:null,expires_at:null};state.previewRequest=null;}
    function discard(){abortReads();clearTimeout(pollTimer);clearPreview();state.options=null;state.groups=[];state.unavailableGroups=0;state.deviceId='';state.destinationId='';state.sourceGroupId='';state.filters=emptyFilters();state.pending=null;state.batch=null;state.scope=null;}
    function revoke(){if(state.revoked||!state.alive||active!==controller)return;state.revoked=true;state.epoch++;state.loading='';state.busy=false;discard();clearRecovery();host.setAttribute('aria-busy','false');host.innerHTML=notice('O acesso a esta empresa mudou. Abra o painel novamente para continuar.');}
    async function request(path,method='GET',payload,scope){
      if(!current()||method!=='GET'&&path!=='/preview'&&!writable())throw Object.assign(Error('Acesso indisponível'),{status:403});
      let abort;if(scope){controllers.get(scope)?.abort();abort=new AbortController();controllers.set(scope,abort);}
      try{return await bridge.request(base+path,method,payload,abort?{signal:abort.signal}:undefined);}finally{if(abort&&controllers.get(scope)===abort)controllers.delete(scope);}
    }
    function remember(intent){history.replaceState({...history.state,[historyKey]:{tenant_id:bridge.tenantId,account_scope:state.scope,idempotency_key:intent.idempotency_key}},'',location.href);}
    function errorText(error){
      const labels={GROUP_ADD_PREVIEW_EXPIRED:'A seleção expirou. Carregue os contatos novamente.',GROUP_ADD_CONTEXT_CHANGED:'A conexão ou o grupo mudou. Atualize os dados e carregue os contatos novamente.',GROUP_ADD_SOURCE_CHANGED:'Os contatos de origem mudaram. Carregue a seleção novamente.',GROUP_ADD_NO_ELIGIBLE:'Não há contatos disponíveis nesta seleção.',GROUP_ADD_BUSY:'Já existe uma adição em andamento neste dispositivo. Aguarde a conclusão antes de preparar outra.',GROUP_ADD_DEVICE_UNAVAILABLE:'O dispositivo não está disponível. Confira a conexão em Dispositivos.',GROUP_ADD_BRIDGE_UNAVAILABLE:'O WhatsApp não respondeu à consulta de grupos. Confira a conexão.',GROUP_ADD_BRIDGE_TIMEOUT:'A leitura dos grupos demorou demais. Aguarde a sincronização do WhatsApp e atualize.',GROUP_ADD_READ_BUSY:'Uma consulta anterior dos grupos ainda está em andamento. Aguarde a conclusão.',GROUP_ADD_PROVIDER_DATA_UNAVAILABLE:'Os dados dos grupos recebidos estão incompletos. Confira o WhatsApp em Dispositivos.',GROUP_ADD_ADMIN_REQUIRED:'Seu número do WhatsApp não é administrador deste grupo.',GROUP_ADD_LIMIT:'A consulta ultrapassa o limite de contatos disponível. Reduza a seleção usando os filtros.',GROUP_ADD_NOT_FOUND:'O dispositivo, grupo ou pedido não está mais disponível.',GROUP_ADD_INVALID:'Confira os filtros e a seleção antes de continuar.'};
      return labels[errorCode(error)]||'Não foi possível concluir esta consulta. Atualize os dados e tente novamente.';
    }
    function readError(error){if([401,403].includes(error?.status)){revoke();return;}if(error?.name!=='AbortError')state.error=errorText(error);}
    function canAdd(){return writable()&&editable()&&!state.loading&&!expired()&&readableDevice(state.preview?.device)&&state.preview?.destination?.id===state.destinationId&&state.preview.destination.is_admin===true&&!!state.preview.preview_token&&!!state.previewRequest&&state.selected.size>0&&state.preview.members.filter(row=>row.status==='ready'&&state.selected.has(row.phone)).length===state.selected.size;}
    function select(label,name,items,value,disabled=false){return '<div class="os-field"><label for="osGa'+name+'">'+esc(label)+'</label><select id="osGa'+name+'" data-ga-field="'+name+'" data-orion-field="native" '+(disabled?'disabled':'')+'>'+items.map(row=>'<option value="'+esc(row[0])+'" '+(row[0]===value?'selected':'')+' '+(row[2]?'disabled':'')+'>'+esc(row[1])+'</option>').join('')+'</select></div>';}
    function field(label,name,value,type='text',disabled=false,extra=''){return '<div class="os-field"><label for="osGa'+name+'">'+esc(label)+'</label><input id="osGa'+name+'" data-ga-field="'+name+'" type="'+type+'" value="'+esc(value)+'" '+(disabled?'disabled':'')+' '+extra+'></div>';}
    function connectionNotice(device){return '<div class="os-notice" role="status" data-ga-device-state><p><strong>'+esc(device.name)+' · '+esc(deviceStatuses[device.status]||'Estado indisponível')+'</strong><br>'+esc(device.blocked_reason||'Conecte o dispositivo para consultar os grupos e adicionar contatos.')+'</p>'+(bridge.canOpenDevices?.()===true?'<div class="os-actions"><button type="button" class="os-button os-outline" data-os-route="dispositivos">Dispositivos</button></div>':'')+'</div>';}
    function formHTML(){
      const options=state.options,off=!editable(),device=options.devices.find(row=>row.id===state.deviceId),readable=readableDevice(device),selectedGroup=state.groups.find(row=>row.id===state.destinationId);
      const destination=state.preview?.destination?.id===state.destinationId?state.preview.destination:selectedGroup?{...selectedGroup,is_admin:selectedGroup.can_add}:null;
      let html='<form class="os-form os-section-stack" data-ga-form><section class="os-card os-form"><div class="os-form-grid">'+select('Dispositivo','device_id',[['','Selecione um dispositivo'],...options.devices.map(row=>[row.id,row.name+' · '+(deviceStatuses[row.status]||'Estado indisponível'),!readableDevice(row)])],state.deviceId,off||state.loading==='options')+select('Grupo de destino','destination_group_id',[['','Selecione um grupo'],...state.groups.map(row=>[row.id,row.name])],state.destinationId,off||!readable||state.loading==='groups')+'</div>'+(device&&!readable?connectionNotice(device):'')+(destination&&!destination.is_admin?'<div class="os-notice" role="status" data-ga-destination-state><p>'+esc(destination.blocked_reason||'Seu número do WhatsApp não é administrador deste grupo.')+'</p></div>':'')+(!options.devices.length?'<p class="os-help">Nenhum dispositivo disponível. Confira Dispositivos antes de continuar.</p>':'')+'</section>';
      if(state.unavailableGroups)html+=notice(state.unavailableGroups+' grupo(s) não puderam ser exibidos por dados incompletos do WhatsApp. Os grupos disponíveis continuam selecionáveis.');
      html+='<section class="os-card os-form">'+select('Fonte dos contatos','source',[['contacts','Contatos salvos'],['group','Outro grupo do WhatsApp']],state.source,off);
      if(state.source==='contacts')html+='<div class="os-form-grid">'+select('Grupo de contatos','contact_group_id',[['all','Todos os contatos'],['ungrouped','Sem grupo'],...options.groups.filter(row=>!row.archived).map(row=>[row.id,row.name])],state.filters.contact_group_id,off)+field('Pesquisar por nome ou WhatsApp','search',state.filters.search,'search',off,'maxlength="255"')+field('Cadastrados a partir de','created_from',state.filters.created_from,'date',off)+field('Cadastrados até','created_to',state.filters.created_to,'date',off)+'</div><p class="os-help">Datas consideradas no fuso '+esc(options.timezone)+'.</p>';
      else html+=select('Grupo de origem','source_group_id',[['','Selecione um grupo'],...state.groups.map(row=>[row.id,row.name,row.id===state.destinationId])],state.sourceGroupId,off||!readable||state.loading==='groups');
      html+='<div class="os-form-actions">'+button(state.source==='contacts'?'Carregar contatos':'Carregar membros','load',off||!readable||!!state.loading||state.source==='group'&&(!state.sourceGroupId||state.sourceGroupId===state.destinationId),'')+'</div></section>';
      if(state.loading==='groups'||state.loading==='preview')html+=notice(state.loading==='groups'?'Carregando grupos…':'Conferindo contatos e grupo de destino…');
      if(state.preview){const preview=state.preview,offSelection=off||!!state.loading;
        html+='<section class="os-card os-form" aria-labelledby="osGaMembers"><header class="os-card-header"><div><h2 id="osGaMembers">Contatos para adicionar ('+preview.members.length+')</h2><p data-ga-count>'+state.selected.size+(state.selected.size===1?' contato selecionado':' contatos selecionados')+' · '+preview.counts.already_member+' já no grupo · '+preview.counts.blocked+' indisponíveis.</p><p class="os-help">'+preview.counts.unavailable+' sem número disponível · '+preview.counts.duplicates+' repetidos.</p></div></header>'+
          (!state.destinationId?notice('Escolha o grupo de destino para conferir a adição.'):preview.preview_token&&expired()?notice('Esta seleção expirou. Carregue os contatos novamente.'):!state.previewRequest&&!state.loading?notice('Carregue os contatos novamente para conferir esta seleção.'): '')+
          '<label class="os-check"><input type="checkbox" data-ga-select-all '+(offSelection||!preview.counts.ready?'disabled':'')+'><span>Selecionar todos</span></label><div class="os-table-scroll"><table class="os-table"><thead><tr><th scope="col"><span class="os-sr-only">Seleção</span></th><th scope="col">WhatsApp</th><th scope="col">Nome</th><th scope="col">Status</th></tr></thead><tbody>'+preview.members.map(row=>'<tr><td><input type="checkbox" data-ga-member="'+esc(row.phone)+'" aria-label="Selecionar '+esc(row.phone)+'" '+(state.selected.has(row.phone)?'checked':'')+' '+(offSelection||row.status!=='ready'?'disabled':'')+'></td><td>'+esc(row.phone)+'</td><td>'+esc(row.name||'Não informado')+'</td><td><span class="os-badge">'+esc(previewStatuses[row.status])+'</span>'+(row.blocked_reason?'<p class="os-help">'+esc(row.blocked_reason)+'</p>':'')+'</td></tr>').join('')+(!preview.members.length?'<tr><td colspan="4">Nenhum contato corresponde a esses filtros.</td></tr>':'')+'</tbody></table></div><p class="os-help">Os membros são adicionados um por vez, com intervalo de 3 segundos. O processamento continua mesmo se você sair desta página.</p><p class="os-help">Alguns números aceitam apenas convites pelas configurações de privacidade. Convites não são enviados automaticamente.</p><div class="os-form-actions">'+button('Adicionar '+state.selected.size+(state.selected.size===1?' membro':' membros'),'add',!canAdd(),'')+'</div></section>';
      }
      return html+'</form>';
    }
    function resultHTML(){
      const {batch,members}=state.batch,processed=batch.total-batch.progress.pending-batch.progress.running;
      return '<section class="os-card os-form" data-ga-result><header class="os-card-header"><div><h2>'+esc(batchStatuses[batch.status])+'</h2><p>'+esc(batch.destination.name)+' · '+esc(batch.device.name)+'</p></div>'+button('Atualizar resultado','refresh',state.busy||!!state.loading)+'</header><progress max="'+batch.total+'" value="'+processed+'" aria-label="Progresso da adição"></progress><p data-ga-progress role="status">'+processed+' de '+batch.total+' contatos processados · '+batch.progress.added+' adicionados · '+batch.progress.not_added+' não adicionados · '+batch.progress.unknown+' incertos.</p><p class="os-help">O processamento continua depois de fechar esta página. Resultados incertos não são repetidos automaticamente; nenhum convite é enviado automaticamente.</p><div class="os-table-scroll"><table class="os-table"><thead><tr><th scope="col">WhatsApp</th><th scope="col">Nome</th><th scope="col">Status</th></tr></thead><tbody>'+[...members].sort((a,b)=>a.ordinal-b.ordinal).map(row=>'<tr data-ga-result-member="'+esc(row.id)+'"><td>'+esc(row.phone)+'</td><td>'+esc(row.name||'Não informado')+'</td><td><span class="os-badge">'+esc(memberStatuses[row.status])+'</span>'+(row.status==='unknown'?'<p class="os-help">O resultado desta tentativa não pôde ser confirmado.</p>':'')+'</td></tr>').join('')+'</tbody></table></div>'+(!running(batch.status)?'<div class="os-form-actions">'+button('Nova seleção','new',state.busy||!!state.loading,'')+'</div>':'')+'</section>';
    }
    function pendingHTML(){return '<section class="os-card" data-ga-pending><header class="os-card-header"><div><h2>Consultar adição</h2><p>'+esc(state.missing?'O pedido ainda não foi localizado. Consulte novamente para acompanhar a mesma identificação.':'A confirmação ainda não está disponível. Consulte o resultado antes de iniciar outra adição.')+'</p></div></header><div class="os-actions">'+button('Consultar resultado','recover',state.busy||!!state.loading)+'</div></section>';}
    function render(){
      if(!current())return;const previous=host.contains(document.activeElement)?document.activeElement:null,focus=previous?['gaField','gaMember','gaAction','gaSelectAll'].find(key=>Object.hasOwn(previous.dataset,key)):null,focusValue=focus?previous.dataset[focus]:null;
      host.setAttribute('aria-busy',String(!!state.loading||state.busy));
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Adicionar contatos ao grupo</h1><p>Escolha contatos salvos no Orion ou membros de outro grupo do WhatsApp para adicionar ao destino.</p></div>'+(!state.pending?button('Atualizar','refresh',state.busy||state.loading==='options'):'')+'</div><div class="os-section-stack">'+(state.error||state.identityError?notice(state.error||state.identityError,true):'')+(state.batch?resultHTML():state.pending?pendingHTML():state.options?formHTML():notice(state.loading?'Carregando dispositivos e listas…':'Não foi possível carregar os dados. Use Atualizar para tentar novamente.'))+'</div>';
      const all=host.querySelector('[data-ga-select-all]');if(all){const count=state.preview?.counts.ready||0;all.checked=!!count&&state.selected.size===count;all.indeterminate=state.selected.size>0&&state.selected.size<count;}
      window.OrionFields?.refresh(host);
      if(focus){const next=[...host.querySelectorAll('[data-ga-field],[data-ga-member],[data-ga-action],[data-ga-select-all]')].find(node=>Object.hasOwn(node.dataset,focus)&&node.dataset[focus]===focusValue);if(next&&!next.disabled)next.focus({preventScroll:true});}
    }
    async function loadOptions(){
      if(!editable())return;invalidate();clearPreview();state.options=null;state.groups=[];state.unavailableGroups=0;state.deviceId='';state.destinationId='';state.sourceGroupId='';state.filters=emptyFilters();state.error='';state.loading='options';const epoch=state.epoch;render();
      try{const value=optionsDTO(await request('/options','GET',undefined,'options'));if(!valid(epoch))return;state.options=value;if(value.devices.length===1)state.deviceId=value.devices[0].id;}
      catch(error){if(valid(epoch))readError(error);}finally{if(valid(epoch)){state.loading='';render();}}
      if(current(epoch)&&readableDevice(state.options?.devices.find(row=>row.id===state.deviceId)))await loadGroups();
    }
    async function loadGroups(){
      if(!editable()||!readableDevice(state.options?.devices.find(row=>row.id===state.deviceId)))return;const epoch=state.epoch,id=state.deviceId;state.loading='groups';state.error='';render();
      try{const value=groupsDTO(await request('/groups?'+new URLSearchParams({device_id:id}),'GET',undefined,'groups'),id);if(!valid(epoch)||id!==state.deviceId)return;if(!readableDevice(value.device))throw Object.assign(Error('Unavailable'),{code:'GROUP_ADD_DEVICE_UNAVAILABLE'});state.groups=value.items;state.unavailableGroups=value.unavailable_groups||0;}
      catch(error){if(valid(epoch)&&id===state.deviceId)readError(error);}finally{if(valid(epoch)&&id===state.deviceId){state.loading='';render();}}
    }
    function previewPayload(){
      if(!readableDevice(state.options?.devices.find(row=>row.id===state.deviceId)))throw Error('Selecione um dispositivo conectado.');
      if(state.destinationId&&!state.groups.some(row=>row.id===state.destinationId))throw Error('Selecione um grupo de destino disponível.');
      const payload={device_id:state.deviceId,destination_group_id:state.destinationId||null,source:state.source,contact_group_id:null,without_group:false,search:'',created_from:null,created_to:null,source_group_id:null};
      if(state.source==='group'){
        if(!state.groups.some(row=>row.id===state.sourceGroupId))throw Error('Escolha o grupo de origem.');if(state.sourceGroupId===state.destinationId)throw Error('Escolha um grupo de origem diferente do grupo de destino.');payload.source_group_id=state.sourceGroupId;
      }else{
        const f=state.filters;if(!clean(f.search,255))throw Error('Use uma pesquisa de até 255 caracteres.');
        for(const key of ['created_from','created_to'])if(f[key]&&!date(f[key]))throw Error('Informe datas válidas para filtrar os contatos.');
        if(f.created_from&&f.created_to&&f.created_from>f.created_to)throw Error('A data inicial deve ser anterior ou igual à data final.');
        if(!['all','ungrouped'].includes(f.contact_group_id)&&!state.options.groups.some(row=>row.id===f.contact_group_id&&!row.archived))throw Error('Escolha uma lista de contatos disponível.');
        payload.contact_group_id=['all','ungrouped'].includes(f.contact_group_id)?null:f.contact_group_id;payload.without_group=f.contact_group_id==='ungrouped';payload.search=f.search.trim();payload.created_from=f.created_from||null;payload.created_to=f.created_to||null;
      }
      return payload;
    }
    async function loadPreview(preserve=false){
      if(!editable()||state.loading)return;let payload;try{payload=previewPayload();}catch(error){state.error=error.message;render();return;}
      const selection=preserve?new Set(state.selected):null;invalidate();if(!preserve)clearPreview();state.error='';state.loading='preview';const epoch=state.epoch;render();
      try{const value=previewDTO(await request('/preview','POST',payload,'preview'),payload);if(!valid(epoch))return;if(!readableDevice(value.device))throw Object.assign(Error('Unavailable'),{code:'GROUP_ADD_DEVICE_UNAVAILABLE'});state.preview=value;state.previewRequest=copy(payload);state.selected=new Set(value.members.filter(row=>row.status==='ready'&&(!selection||selection.has(row.phone))).map(row=>row.phone));if(value.expires_at)expiryTimer=setTimeout(()=>{if(current(epoch)&&state.preview===value)render();},Math.min(2147483647,Math.max(0,Date.parse(value.expires_at)-Date.now()+10)));}
      catch(error){if(valid(epoch))readError(error);}finally{if(valid(epoch)){state.loading='';render();}}
    }
    function schedulePoll(){clearTimeout(pollTimer);if(current()&&state.batch&&running(state.batch.batch.status))pollTimer=setTimeout(()=>{if(current()&&!state.busy&&!state.loading)void loadBatch();},3000);}
    function acceptBatch(value){state.batch=value;state.missing=false;state.error='';clearPreview();state.options=null;state.groups=[];state.unavailableGroups=0;state.filters=emptyFilters();schedulePoll();}
    async function loadBatch(){
      if(!current()||!state.pending||!state.batch||state.busy||state.loading)return;const epoch=state.epoch,intent=state.pending,id=state.batch.batch.id;state.loading='progress';state.error='';clearTimeout(pollTimer);render();
      try{const value=batchDTO(await request('/batches/'+id,'GET',undefined,'batch'),intent,state.batch);if(!valid(epoch)||state.pending!==intent)return;acceptBatch(value);}
      catch(error){if(valid(epoch)&&state.pending===intent)readError(error);}finally{if(valid(epoch)&&state.pending===intent){state.loading='';render();}}
    }
    function knownRejection(error){return [['GROUP_ADD_INVALID',422],['GROUP_ADD_LIMIT',422],['GROUP_ADD_LIMIT',413],['GROUP_ADD_PREVIEW_EXPIRED',410],['GROUP_ADD_CONTEXT_CHANGED',409],['GROUP_ADD_SOURCE_CHANGED',409],['GROUP_ADD_NO_ELIGIBLE',422],['GROUP_ADD_DEVICE_UNAVAILABLE',409],['GROUP_ADD_ADMIN_REQUIRED',409],['GROUP_ADD_BUSY',409],['GROUP_ADD_NOT_FOUND',404]].some(([code,status])=>errorCode(error)===code&&error?.status===status);}
    async function add(){
      if(!canAdd())return;const phones=state.preview.members.filter(row=>row.status==='ready'&&state.selected.has(row.phone)).map(row=>row.phone).sort(),payload={idempotency_key:crypto.randomUUID(),preview_token:state.preview.preview_token,phones};
      const intent={idempotency_key:payload.idempotency_key,expected:{device_id:state.deviceId,destination_group_id:state.destinationId,source:state.source,phones}};
      try{remember(intent);}catch{state.error='Não foi possível guardar a identificação deste pedido. Atualize o painel antes de adicionar contatos.';render();return;}
      state.pending=intent;state.busy=true;state.error='';const epoch=state.epoch;render();let uncertain=false;
      try{const value=batchDTO(await request('/batches','POST',payload),intent);if(!valid(epoch)||state.pending!==intent)return;acceptBatch(value);}
      catch(error){if(!valid(epoch)||state.pending!==intent)return;if([401,403].includes(error?.status)){revoke();return;}if(knownRejection(error)){state.pending=null;clearRecovery();clearPreview();state.error=errorText(error);}else{uncertain=true;state.error='Não foi possível confirmar a adição. A mesma identificação será consultada.';}}
      finally{if(valid(epoch)){state.busy=false;render();}}
      if(uncertain&&current(epoch)&&state.pending===intent)await recover();
    }
    async function recover(){
      const intent=state.pending;if(!intent||!current()||state.busy||state.loading)return;const epoch=state.epoch;state.busy=true;state.error='';render();
      try{const value=batchDTO(await request('/requests/'+intent.idempotency_key,'GET',undefined,'recover'),intent,state.batch);if(!valid(epoch)||state.pending!==intent)return;acceptBatch(value);}
      catch(error){if(!valid(epoch)||state.pending!==intent)return;if([401,403].includes(error?.status)){revoke();return;}state.missing=error?.status===404;state.error=state.missing?'O pedido ainda não foi localizado. Nenhuma nova adição foi iniciada.':'Não foi possível confirmar o resultado. Consulte novamente a mesma adição.';}
      finally{if(valid(epoch)&&state.pending===intent){state.busy=false;render();}}
    }
    async function change(event){
      if(!current()){revoke();return;}if(!editable())return;const node=event.target,name=node.dataset.gaField;
      if(name==='device_id'){
        if(node.value&&!state.options?.devices.some(row=>row.id===node.value&&readableDevice(row)))return;invalidate();clearPreview();state.deviceId=node.value;state.destinationId='';state.sourceGroupId='';state.groups=[];state.unavailableGroups=0;state.error='';render();if(state.deviceId)await loadGroups();return;
      }
      if(name==='destination_group_id'){
        if(node.value&&!state.groups.some(row=>row.id===node.value))return;const preserve=!!state.preview;invalidate();state.destinationId=node.value;state.error='';render();if(preserve)await loadPreview(true);return;
      }
      if(name==='source'){
        if(!['contacts','group'].includes(node.value))return;invalidate();clearPreview();state.source=node.value;state.sourceGroupId='';state.filters=emptyFilters();state.error='';render();return;
      }
      if(name==='source_group_id'){
        if(node.value&&!state.groups.some(row=>row.id===node.value))return;invalidate();clearPreview();state.sourceGroupId=node.value;state.error='';render();return;
      }
      if(name==='contact_group_id'){
        if(!['all','ungrouped'].includes(node.value)&&!state.options.groups.some(row=>row.id===node.value&&!row.archived))return;invalidate();clearPreview();state.filters.contact_group_id=node.value;state.error='';render();return;
      }
      if(!state.preview||state.loading)return;
      if(node.hasAttribute('data-ga-select-all')){state.selected=node.checked?new Set(state.preview.members.filter(row=>row.status==='ready').map(row=>row.phone)):new Set();render();}
      else if(node.hasAttribute('data-ga-member')&&state.preview.members.some(row=>row.phone===node.dataset.gaMember&&row.status==='ready')){if(node.checked)state.selected.add(node.dataset.gaMember);else state.selected.delete(node.dataset.gaMember);render();}
    }
    function input(event){
      if(!current()){revoke();return;}if(!editable()||state.source!=='contacts')return;const name=event.target.dataset.gaField;if(!['search','created_from','created_to'].includes(name))return;
      const value=event.target.value;invalidate();clearPreview();state.filters[name]=value;state.error='';
      const result=host.querySelector('[aria-labelledby="osGaMembers"]');result?.remove();host.querySelectorAll('[data-ga-error]').forEach(node=>node.remove());host.setAttribute('aria-busy','false');const load=host.querySelector('[data-ga-action="load"]');if(load)load.disabled=!readableDevice(state.options?.devices.find(row=>row.id===state.deviceId));
    }
    async function click(event){
      const node=event.target.closest('[data-ga-action]');if(!node||!host.contains(node)||node.disabled)return;if(!current()){revoke();return;}const action=node.dataset.gaAction;
      if(action==='refresh'){if(state.batch)await loadBatch();else await loadOptions();}else if(action==='load')await loadPreview();else if(action==='add')await add();else if(action==='recover')await recover();
      else if(action==='new'&&state.batch&&!running(state.batch.batch.status)&&!state.busy&&!state.loading){state.pending=null;state.batch=null;state.missing=false;clearRecovery();await loadOptions();host.querySelector('h1')?.focus();}
    }
    function submit(event){if(event.target.matches('[data-ga-form]'))event.preventDefault();}
    async function start(){
      try{if(!bridge.accountKey||!crypto.subtle)throw Error('missing_account_scope');const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(bridge.accountKey));if(!valid(state.epoch))return;state.scope=[...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');}
      catch{if(current())state.identityError='Não foi possível preparar a identificação da conta. Atualize o painel antes de adicionar contatos.';}
      if(!current())return;const remembered=navigationState()[historyKey];
      if(remembered&&remembered.tenant_id===bridge.tenantId&&remembered.account_scope===state.scope){state.pending={idempotency_key:remembered.idempotency_key,expected:null};try{remember(state.pending);}catch{}await recover();}else{clearRecovery();await loadOptions();}
    }
    function destroy(){state.alive=false;state.epoch++;discard();for(const [name,handler]of [['click',click],['change',change],['input',input],['submit',submit]])host.removeEventListener(name,handler);host.replaceChildren();host.removeAttribute('aria-busy');}
    controller={ready:null,revoke,destroy,getState:()=>({alive:state.alive,revoked:state.revoked,busy:state.busy,loading:state.loading,selectedCount:state.selected.size,deviceId:state.deviceId,destinationId:state.destinationId,source:state.source,pending:!!state.pending,result:!!state.batch})};
    active=controller;for(const [name,handler]of [['click',click],['change',change],['input',input],['submit',submit]])host.addEventListener(name,handler);controller.ready=start();return controller;
  }
  window.OrionSubscriptionGroupAdd=Object.freeze({mount,unmount,clearContext,navigationState,clearRecovery});
})();
