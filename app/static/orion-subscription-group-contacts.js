/* Native Orion group-contact extraction. Reads never import or send messages. */
(()=>{
  'use strict';
  const historyKey='orionGroupContactsRecovery',recoveryFields=['tenant_id','account_scope','idempotency_key'];
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
  const errorCode=error=>error?.code||error?.detail?.code;
  const roleLabels={owner:'Proprietário',admin:'Administrador',member:'Membro'};
  const shortName=value=>{let result='';for(const char of value){if(result.length+char.length>100)break;result+=char;}return result;};
  const button=(label,action,disabled=false,kind='os-outline')=>'<button type="button" class="os-button '+kind+'" data-gc-action="'+action+'" '+(disabled?'disabled':'')+'>'+esc(label)+'</button>';
  const notice=(message,error=false)=>'<div class="os-notice'+(error?' os-error':'')+'" '+(error?'role="alert" data-gc-error':'role="status"')+'><p>'+esc(message)+'</p></div>';
  let active=null;

  function deviceDTO(value,expected){
    if(!object(value)||!uuid(value.id)||expected&&value.id!==expected||!clean(value.name,200,true)||!clean(value.status,80,true)||typeof value.available!=='boolean'||value.blocked_reason!==null&&!clean(value.blocked_reason,1000)||!hash(value.version))throw Error('invalid_device');
    return value;
  }
  function groupDTO(value){
    if(!object(value)||!uuid(value.id)||!clean(value.name,100,true)||!integer(value.version)||value.version<1||!integer(value.member_count)||value.member_count>5000||!integer(value.blocked_count)||value.blocked_count>value.member_count||typeof value.archived!=='boolean')throw Error('invalid_local_group');
    return value;
  }
  function remoteDTO(value,expected){
    if(!object(value)||!remoteId(value.id)||expected&&value.id!==expected||!clean(value.name,100,true)||!integer(value.member_count)||value.member_count>5000)throw Error('invalid_remote_group');return value;
  }
  function optionsDTO(value){
    if(!object(value)||!Array.isArray(value.devices)||value.devices.length>100||!Array.isArray(value.groups)||value.groups.length>100||!instant(value.generated_at)||value.limits?.groups!==100||value.limits.members_per_group!==5000||value.limits.remote_groups!==1000||value.limits.preview_seconds!==300||value.limits.group_name_utf16!==100)throw Error('invalid_options');
    value.devices.forEach(row=>deviceDTO(row));value.groups.forEach(groupDTO);
    if(new Set(value.devices.map(row=>row.id)).size!==value.devices.length||new Set(value.groups.map(row=>row.id)).size!==value.groups.length)throw Error('duplicate_options');return value;
  }
  function groupsDTO(value,deviceId){
    if(!object(value)||!Array.isArray(value.items)||value.items.length>1000||value.total!==value.items.length)throw Error('invalid_groups');deviceDTO(value.device,deviceId);value.items.forEach(row=>remoteDTO(row));
    if(new Set(value.items.map(row=>row.id)).size!==value.items.length)throw Error('duplicate_groups');return value;
  }
  function membersDTO(value,deviceId,groupId){
    if(!object(value)||!clean(value.preview_token,360000,true)||value.preview_token.length<100||!instant(value.expires_at)||!Array.isArray(value.members)||value.members.length>5000||!['total','unavailable_count','duplicate_count'].every(key=>integer(value[key]))||value.total!==value.members.length+value.unavailable_count+value.duplicate_count)throw Error('invalid_members');
    deviceDTO(value.device,deviceId);remoteDTO(value.group,groupId);
    if(value.total>5000||value.group.member_count!==value.total)throw Error('invalid_member_total');
    for(const row of value.members)if(!object(row)||!phone(row.phone)||!clean(row.name,100)||!Object.hasOwn(roleLabels,row.role))throw Error('invalid_member');
    if(new Set(value.members.map(row=>row.phone)).size!==value.members.length)throw Error('duplicate_members');return value;
  }
  function operationDTO(value,intent){
    if(!object(value)||!uuid(value.request_id)||value.idempotency_key!==intent.idempotency_key||!['group_create','group_update'].includes(value.kind)||value.status!=='succeeded'||value.campaign_id!==null||!uuid(value.group_id)||typeof value.replayed!=='boolean'||!instant(value.created_at)||value.expires_at!==null||value.preview!==null||!object(value.result)||value.result.origin!=='group_contacts')throw Error('invalid_operation');
    const result=value.result;groupDTO(result.group);
    if(result.group.archived||result.group.id!==value.group_id||!['selected_count','added_count','existing_count','restored_count'].every(key=>integer(result[key]))||!result.selected_count||result.selected_count>5000||result.selected_count!==result.added_count+result.existing_count+result.restored_count)throw Error('invalid_result');
    if(intent.payload&&(result.selected_count!==intent.payload.phones.length||value.kind!==(intent.payload.target_group_id?'group_update':'group_create')||intent.payload.target_group_id&&value.group_id!==intent.payload.target_group_id))throw Error('mismatched_result');
    return value;
  }
  function recoveryRecord(value){return object(value)&&uuid(value.tenant_id)&&hash(value.account_scope)&&uuid(value.idempotency_key);}
  function navigationState(){try{const value=history.state?.[historyKey];return recoveryRecord(value)?{[historyKey]:Object.fromEntries(recoveryFields.map(key=>[key,value[key]]))}:{};}catch{return {};}}
  function clearRecovery(){try{const next={...history.state};delete next[historyKey];history.replaceState(next,'',location.href);}catch{}}
  function clearContext(){active?.revoke();clearRecovery();}
  function unmount(){const previous=active;active=null;previous?.destroy();}

  function mount(bridge){
    unmount();
    const host=bridge.host,base='/api/v1/tenants/'+bridge.tenantId+'/subscription-whatsapp/group-contacts';
    if(!host||!uuid(bridge.tenantId))throw Error('invalid_group_contacts_context');
    const state={alive:true,revoked:false,epoch:0,memberRead:0,scope:null,options:null,groups:[],deviceId:'',groupId:'',preview:null,selected:new Set(),target:'new',name:'',loading:'',busy:false,error:'',identityError:'',pending:null,operation:null,missing:false};
    const controllers=new Map();let expiryTimer=null,controller;
    const allowed=()=>{try{return bridge.canAccess?.()!==false;}catch{return false;}};
    const current=(epoch=state.epoch)=>state.alive&&!state.revoked&&active===controller&&host.isConnected&&allowed()&&epoch===state.epoch;
    const writable=()=>current()&&!!state.scope&&bridge.canWrite?.()!==false;
    const editable=()=>current()&&!state.busy&&!state.pending;
    const expired=()=>!state.preview||Date.parse(state.preview.expires_at)<=Date.now();
    function valid(epoch){if(current(epoch))return true;if(active===controller&&state.alive&&!allowed())revoke();return false;}
    function abortReads(){for(const abort of controllers.values())abort.abort();controllers.clear();}
    function clearPreview(){clearTimeout(expiryTimer);expiryTimer=null;state.preview=null;state.selected.clear();state.memberRead++;}
    function discard(){abortReads();clearPreview();state.options=null;state.groups=[];state.deviceId='';state.groupId='';state.target='new';state.name='';state.pending=null;state.operation=null;state.scope=null;}
    function revoke(){if(state.revoked||!state.alive||active!==controller)return;state.revoked=true;state.epoch++;state.loading='';state.busy=false;discard();clearRecovery();host.setAttribute('aria-busy','false');host.innerHTML=notice('O acesso a esta empresa mudou. Abra o painel novamente para continuar.');}
    async function request(path,method='GET',payload,scope=path){
      if(!current()||method!=='GET'&&!writable())throw Object.assign(Error('Acesso indisponível'),{status:403});
      let abort;if(method==='GET'){controllers.get(scope)?.abort();abort=new AbortController();controllers.set(scope,abort);}
      try{return await bridge.request(base+path,method,payload,abort?{signal:abort.signal}:undefined);}
      finally{if(abort&&controllers.get(scope)===abort)controllers.delete(scope);}
    }
    function remember(intent){const next={...history.state,[historyKey]:{tenant_id:bridge.tenantId,account_scope:state.scope,idempotency_key:intent.idempotency_key}};history.replaceState(next,'',location.href);}
    function errorText(error){
      const labels={GROUP_CONTACTS_PREVIEW_EXPIRED:'A consulta de membros expirou. Carregue os membros novamente.',GROUP_CONTACTS_CONTEXT_CHANGED:'A conexão mudou. Atualize os grupos e carregue os membros novamente.',GROUP_CONTACTS_DEVICE_UNAVAILABLE:'O dispositivo não está disponível. Confira a conexão e atualize os grupos.',GROUP_CONTACTS_BRIDGE_UNAVAILABLE:'Não foi possível consultar o WhatsApp agora. Tente carregar novamente.',GROUP_CONTACTS_CONFLICT:'A lista de destino mudou. Atualize os dados e carregue os membros novamente.',GROUP_CONTACTS_LIMIT:'A seleção ou a lista de destino ultrapassa o limite disponível.',GROUP_CONTACTS_PREVIEW_INVALID:'A consulta de membros não é mais válida. Carregue os membros novamente.',GROUP_CONTACTS_NOT_FOUND:'O grupo ou dispositivo não está mais disponível. Atualize os dados.'};
      return labels[errorCode(error)]||'Não foi possível concluir esta consulta. Atualize os dados e tente novamente.';
    }
    function handleReadError(error){if([401,403].includes(error?.status)){revoke();return;}if(error?.name!=='AbortError')state.error=errorText(error);}
    function canSave(){return writable()&&editable()&&!state.loading&&!expired()&&state.preview.device.available&&state.selected.size>0&&(state.target==='new'?clean(state.name.trim(),100,true):state.options?.groups.some(row=>row.id===state.target&&!row.archived));}
    function select(label,name,items,value,disabled=false){return '<div class="os-field"><label for="osGc'+name+'">'+esc(label)+'</label><select id="osGc'+name+'" data-gc-field="'+name+'" data-orion-field="native" '+(disabled?'disabled':'')+'>'+items.map(row=>'<option value="'+esc(row[0])+'" '+(row[0]===value?'selected':'')+' '+(row[2]?'disabled':'')+'>'+esc(row[1])+'</option>').join('')+'</select></div>';}
    function formHTML(){
      const options=state.options,off=!editable(),devices=options.devices,groups=state.groups;
      let html='<form class="os-form os-section-stack" data-gc-form><section class="os-card os-form"><div class="os-form-grid">'+select('Dispositivo','device_id',[['','Selecione um dispositivo'],...devices.map(row=>[row.id,row.name+(row.available?'':' · indisponível'),!row.available])],state.deviceId,off||state.loading==='options')+select('Grupo do WhatsApp','group_id',[['','Selecione um grupo'],...groups.map(row=>[row.id,row.name])],state.groupId,off||!state.deviceId||state.loading==='groups')+'</div><div class="os-form-actions">'+button('Carregar membros','load-members',off||!state.groupId||!!state.loading,'')+'</div>'+
        (!devices.length?'<p class="os-help">Nenhum dispositivo disponível. Confira Dispositivos antes de carregar os grupos.</p>':state.deviceId&&!groups.length&&!state.loading?'<p class="os-help" data-gc-empty>Nenhum grupo disponível neste dispositivo.</p>':'')+'</section>';
      if(state.loading==='groups'||state.loading==='members')html+=notice(state.loading==='groups'?'Carregando grupos…':'Carregando membros…');
      if(state.preview){const preview=state.preview,disabled=off||expired();
        html+='<section class="os-card os-form" aria-labelledby="osGcMembers"><header class="os-card-header"><div><h2 id="osGcMembers">'+esc(preview.group.name)+'</h2><p data-gc-count>'+state.selected.size+' de '+preview.members.length+' contatos selecionados.</p><p class="os-help">'+preview.total+' membros encontrados · '+preview.unavailable_count+' sem número disponível · '+preview.duplicate_count+' repetidos.</p></div></header>'+
          (expired()?notice('Esta consulta expirou. Carregue os membros novamente para salvar.'):'')+
          '<div class="os-table-scroll"><table class="os-table"><thead><tr><th scope="col"><input type="checkbox" data-gc-select-all aria-label="Selecionar todos os membros" '+(disabled||!preview.members.length?'disabled':'')+'></th><th scope="col">WhatsApp</th><th scope="col">Nome</th><th scope="col">Função</th></tr></thead><tbody>'+preview.members.map(row=>'<tr><td><input type="checkbox" data-gc-member="'+esc(row.phone)+'" aria-label="Selecionar '+esc(row.phone)+'" '+(state.selected.has(row.phone)?'checked':'')+' '+(disabled?'disabled':'')+'></td><td>'+esc(row.phone)+'</td><td>'+esc(row.name||'Não informado')+'</td><td>'+esc(roleLabels[row.role])+'</td></tr>').join('')+(!preview.members.length?'<tr><td colspan="4">Nenhum número disponível para salvar.</td></tr>':'')+'</tbody></table></div>'+
          select('Grupo de contatos','target_group_id',[['new','Novo grupo'],...options.groups.filter(row=>!row.archived).map(row=>[row.id,row.name])],state.target,disabled)+
          (state.target==='new'?'<div class="os-field"><label for="osGcGroupName">Nome do grupo</label><input id="osGcGroupName" data-gc-field="new_group_name" type="text" maxlength="100" value="'+esc(state.name)+'" '+(disabled?'disabled':'')+' required><small class="os-help">Até 100 caracteres.</small></div>':'')+
          '<p class="os-help">Salvar acrescenta os contatos à lista, preserva as restrições já registradas e não envia mensagens.</p><div class="os-form-actions">'+button('Salvar '+state.selected.size+' contatos','import',!canSave(),'')+'</div></section>';
      }
      return html+'</form>';
    }
    function pendingHTML(){
      if(state.operation){const result=state.operation.result;return '<section class="os-card" data-gc-result><header class="os-card-header"><div><h2>Contatos salvos</h2><p>'+esc(result.group.name)+'</p></div></header><ul class="os-feature-list"><li>Selecionados: <strong>'+result.selected_count+'</strong></li><li>Adicionados: <strong>'+result.added_count+'</strong></li><li>Já existentes: <strong>'+result.existing_count+'</strong></li><li>Restaurados: <strong>'+result.restored_count+'</strong></li></ul><div class="os-form-actions">'+button('Nova extração','new',state.busy,'')+'</div></section>';}
      const canRetry=state.missing&&state.pending?.payload&&Date.parse(state.pending.expiresAt)>Date.now();
      return '<section class="os-card" data-gc-pending><header class="os-card-header"><div><h2>'+esc(state.busy?'Salvamento em conferência':'Consultar salvamento')+'</h2><p>'+esc(state.missing?'O pedido ainda não foi localizado. Consulte novamente para acompanhar a mesma identificação.':'A confirmação do salvamento ainda não está disponível. Consulte o resultado antes de iniciar outra importação.')+'</p></div></header><div class="os-actions">'+button('Consultar resultado','recover',state.busy)+(canRetry?button('Tentar salvar o mesmo pedido','retry',state.busy):'')+'</div></section>';
    }
    function render(){
      if(!current())return;
      const previous=host.contains(document.activeElement)?document.activeElement:null;
      const focus=previous?['gcField','gcMember','gcAction','gcSelectAll'].find(key=>Object.hasOwn(previous.dataset,key)):null;
      const focusValue=focus?previous.dataset[focus]:null;
      host.setAttribute('aria-busy',String(!!state.loading||state.busy));
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Extrator de contatos de grupos</h1><p>Carregue os membros de um grupo do WhatsApp e escolha quais contatos salvar no Orion.</p></div>'+(!state.pending?button('Atualizar','refresh',state.busy||state.loading==='options'):'')+'</div><div class="os-section-stack">'+(state.error||state.identityError?notice(state.error||state.identityError,true):'')+(state.pending?pendingHTML():state.options?formHTML():notice(state.loading?'Carregando dispositivos e listas…':'Não foi possível carregar os dados. Use Atualizar para tentar novamente.'))+'</div>';
      const all=host.querySelector('[data-gc-select-all]');if(all){const count=state.preview?.members.length||0;all.checked=!!count&&state.selected.size===count;all.indeterminate=state.selected.size>0&&state.selected.size<count;}
      window.OrionFields?.refresh(host);
      if(focus){const next=[...host.querySelectorAll('[data-gc-field],[data-gc-member],[data-gc-action],[data-gc-select-all]')].find(node=>Object.hasOwn(node.dataset,focus)&&node.dataset[focus]===focusValue);if(next&&!next.disabled)next.focus({preventScroll:true});}
    }
    async function loadOptions(){
      if(!editable())return;state.epoch++;abortReads();clearPreview();state.options=null;state.groups=[];state.deviceId='';state.groupId='';state.target='new';state.name='';state.error='';state.loading='options';const epoch=state.epoch;render();
      try{const value=optionsDTO(await request('/options','GET',undefined,'options'));if(!valid(epoch))return;state.options=value;if(value.devices.length===1)state.deviceId=value.devices[0].available?value.devices[0].id:'';}
      catch(error){if(valid(epoch))handleReadError(error);}
      finally{if(valid(epoch)){state.loading='';render();}}
      if(current(epoch)&&state.deviceId)await loadGroups();
    }
    async function loadGroups(){
      if(!editable()||!state.deviceId)return;const epoch=state.epoch,id=state.deviceId;state.loading='groups';state.error='';render();
      try{const value=groupsDTO(await request('/groups?'+new URLSearchParams({device_id:id}),'GET',undefined,'groups'),id);if(!valid(epoch)||id!==state.deviceId)return;if(!value.device.available)throw Object.assign(Error('Unavailable'),{code:'GROUP_CONTACTS_DEVICE_UNAVAILABLE'});state.groups=value.items;}
      catch(error){if(valid(epoch)&&id===state.deviceId)handleReadError(error);}
      finally{if(valid(epoch)&&id===state.deviceId){state.loading='';render();}}
    }
    async function loadMembers(){
      if(!editable()||!state.deviceId||!state.groups.some(group=>group.id===state.groupId)||state.loading)return;clearPreview();state.error='';state.loading='members';const epoch=state.epoch,read=state.memberRead,deviceId=state.deviceId,groupId=state.groupId;render();
      try{const value=membersDTO(await request('/members?'+new URLSearchParams({device_id:deviceId,group_id:groupId}),'GET',undefined,'members'),deviceId,groupId);if(!valid(epoch)||read!==state.memberRead)return;if(!value.device.available)throw Object.assign(Error('Unavailable'),{code:'GROUP_CONTACTS_DEVICE_UNAVAILABLE'});state.preview=value;state.selected=new Set(value.members.map(row=>row.phone));if(state.target==='new')state.name=shortName('Grupo: '+value.group.name);expiryTimer=setTimeout(()=>{if(current(epoch)&&state.preview===value)render();},Math.min(2147483647,Math.max(0,Date.parse(value.expires_at)-Date.now()+10)));}
      catch(error){if(valid(epoch)&&read===state.memberRead)handleReadError(error);}
      finally{if(valid(epoch)&&read===state.memberRead){state.loading='';render();}}
    }
    function importPayload(){
      if(!canSave())throw Error('Confira a seleção e o nome do grupo antes de salvar.');
      const phones=state.preview.members.filter(row=>state.selected.has(row.phone)).map(row=>row.phone).sort();
      if(phones.length!==state.selected.size||!phones.length)throw Error('Selecione ao menos um contato disponível.');
      const group=state.target==='new'?null:state.options.groups.find(row=>row.id===state.target&&!row.archived);
      return {idempotency_key:crypto.randomUUID(),preview_token:state.preview.preview_token,phones,target_group_id:group?.id||null,expected_version:group?.version||null,new_group_name:group?null:state.name.trim()};
    }
    function rejected(error,intent){
      if(errorCode(error)==='GROUP_CONTACTS_CONFLICT'){
        const proof=error.groupContactsRejection;return error.status===409&&object(proof)&&proof.reason==='stale_group_version'&&proof.idempotency_key===intent.idempotency_key&&proof.group_id===intent.payload?.target_group_id&&proof.expected_version===intent.payload?.expected_version;
      }
      return [['GROUP_CONTACTS_INVALID',422],['GROUP_CONTACTS_LIMIT',422],['GROUP_CONTACTS_LIMIT',413],['GROUP_CONTACTS_PREVIEW_INVALID',422],['GROUP_CONTACTS_PREVIEW_EXPIRED',410],['GROUP_CONTACTS_CONTEXT_CHANGED',409],['GROUP_CONTACTS_DEVICE_UNAVAILABLE',409],['GROUP_CONTACTS_NOT_FOUND',404]].some(([code,status])=>errorCode(error)===code&&error?.status===status);
    }
    function succeeded(value,intent){state.operation=value;state.missing=false;intent.payload=null;clearPreview();state.name='';state.groups=[];state.options=null;state.target='new';state.error='';}
    async function save(retry=false){
      if(!writable()||state.busy)return;let intent=state.pending;
      if(retry){if(!state.missing||!intent?.payload||Date.parse(intent.expiresAt)<=Date.now())return;}
      else{if(intent)return;try{const payload=importPayload();intent={idempotency_key:payload.idempotency_key,payload,expiresAt:state.preview.expires_at};remember(intent);state.pending=intent;}catch(error){state.error=error?.message||'Não foi possível preparar o salvamento.';render();return;}}
      const epoch=state.epoch;state.busy=true;state.missing=false;state.error='';render();let uncertain=false;
      try{const value=operationDTO(await request('/import','POST',copy(intent.payload)),intent);if(!valid(epoch)||state.pending!==intent)return;succeeded(value,intent);}
      catch(error){if(!valid(epoch)||state.pending!==intent)return;if([401,403].includes(error?.status)){revoke();return;}if(rejected(error,intent)){clearRecovery();state.pending=null;clearPreview();state.error=errorText(error);}else{uncertain=true;state.error='Não foi possível confirmar o salvamento. A mesma identificação será consultada.';}}
      finally{if(valid(epoch)){state.busy=false;render();}}
      if(uncertain&&current(epoch)&&state.pending===intent)await recover();
    }
    async function recover(){
      const intent=state.pending;if(!intent||!current()||state.busy)return;const epoch=state.epoch;state.busy=true;state.error='';render();
      try{const value=operationDTO(await request('/requests/'+intent.idempotency_key,'GET',undefined,'recover'),intent);if(!valid(epoch)||state.pending!==intent)return;succeeded(value,intent);}
      catch(error){if(!valid(epoch)||state.pending!==intent)return;if([401,403].includes(error?.status)){revoke();return;}state.missing=error?.status===404;state.error=state.missing?'O pedido ainda não foi localizado. Nenhuma nova importação foi iniciada.':'Não foi possível confirmar o resultado. Consulte novamente o mesmo salvamento.';}
      finally{if(valid(epoch)&&state.pending===intent){state.busy=false;render();}}
    }
    async function change(event){
      if(!current()){revoke();return;}if(!editable())return;const node=event.target,name=node.dataset.gcField;
      if(name==='device_id'){
        if(node.value&&!state.options?.devices.some(row=>row.id===node.value&&row.available))return;
        state.epoch++;abortReads();clearPreview();state.deviceId=node.value;state.groupId='';state.groups=[];state.target='new';state.name='';state.loading='';state.error='';render();if(state.deviceId)await loadGroups();return;
      }
      if(name==='group_id'){
        if(node.value&&!state.groups.some(row=>row.id===node.value))return;
        state.epoch++;abortReads();clearPreview();state.groupId=node.value;state.target='new';state.name='';state.loading='';state.error='';render();return;
      }
      if(!state.preview||expired())return;
      if(name==='target_group_id'){if(node.value!=='new'&&!state.options.groups.some(row=>row.id===node.value&&!row.archived))return;state.target=node.value;if(state.target==='new'&&!state.name)state.name=shortName('Grupo: '+state.preview.group.name);render();}
      else if(node.hasAttribute('data-gc-select-all')){state.selected=node.checked?new Set(state.preview.members.map(row=>row.phone)):new Set();render();}
      else if(node.hasAttribute('data-gc-member')&&state.preview.members.some(row=>row.phone===node.dataset.gcMember)){if(node.checked)state.selected.add(node.dataset.gcMember);else state.selected.delete(node.dataset.gcMember);render();}
    }
    function input(event){if(!current()){revoke();return;}if(!editable()||event.target.dataset.gcField!=='new_group_name'||state.target!=='new')return;state.name=event.target.value;const saveButton=host.querySelector('[data-gc-action="import"]');if(saveButton)saveButton.disabled=!canSave();}
    async function click(event){
      const node=event.target.closest('[data-gc-action]');if(!node||!host.contains(node)||node.disabled)return;if(!current()){revoke();return;}
      const action=node.dataset.gcAction;
      if(action==='refresh')await loadOptions();else if(action==='load-members')await loadMembers();else if(action==='import')await save();else if(action==='recover')await recover();else if(action==='retry')await save(true);
      else if(action==='new'&&state.operation&&!state.busy){state.pending=null;state.operation=null;state.missing=false;clearRecovery();await loadOptions();host.querySelector('h1')?.focus();}
    }
    function submit(event){if(!event.target.matches('[data-gc-form]'))return;event.preventDefault();}
    async function start(){
      try{if(!bridge.accountKey||!crypto.subtle)throw Error('missing_account_scope');const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(bridge.accountKey));if(!valid(state.epoch))return;state.scope=[...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');}
      catch{if(current())state.identityError='Não foi possível preparar a identificação da conta para salvar. Atualize o painel.';}
      if(!current())return;const remembered=navigationState()[historyKey];
      if(remembered&&remembered.tenant_id===bridge.tenantId&&remembered.account_scope===state.scope){state.pending={idempotency_key:remembered.idempotency_key,payload:null};try{remember(state.pending);}catch{}await recover();}
      else{clearRecovery();await loadOptions();}
    }
    function destroy(){state.alive=false;state.epoch++;discard();host.removeEventListener('click',click);host.removeEventListener('change',change);host.removeEventListener('input',input);host.removeEventListener('submit',submit);host.replaceChildren();}
    controller={ready:null,revoke,destroy,getState:()=>({alive:state.alive,revoked:state.revoked,busy:state.busy,loading:state.loading,selectedCount:state.selected.size,deviceId:state.deviceId,groupId:state.groupId,pending:!!state.pending,result:!!state.operation})};
    active=controller;host.addEventListener('click',click);host.addEventListener('change',change);host.addEventListener('input',input);host.addEventListener('submit',submit);controller.ready=start();return controller;
  }
  window.OrionSubscriptionGroupContacts=Object.freeze({mount,unmount,clearContext,navigationState,clearRecovery});
})();
