/* Orion Público. Server-backed contacts and the same groups used by campaigns. */
(()=>{
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value);
  const number=value=>Number.isSafeInteger(value)&&value>=0;
  const countries=[['BR','Brasil (+55)'],['PY','Paraguai (+595)'],['US','Estados Unidos / Canadá (+1)'],['PT','Portugal (+351)'],['AR','Argentina (+54)'],['UY','Uruguai (+598)'],['CL','Chile (+56)'],['MX','México (+52)'],['GB','Reino Unido (+44)'],['ES','Espanha (+34)'],['INT','Todos já têm DDI']];
  const icons={plus:'M12 5v14M5 12h14',refresh:'M20 11a8 8 0 0 0-15-3M4 4v5h5M4 13a8 8 0 0 0 15 3m1 4v-5h-5',upload:'M12 17V3m-5 5 5-5 5 5M4 15v6h16v-6',download:'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6',close:'m6 6 12 12M6 18 18 6',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-4M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8',edit:'m16 3 5 5L8 21H3v-5L16 3Z',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15',layers:'m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 17l9 5 9-5'};
  const icon=name=>'<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'+(icons[name]||icons.users)+'"/></svg>';
  const button=(label,action,{kind='os-outline',symbol='',disabled=false,extra=''}={})=>'<button type="button" class="os-button '+kind+'" data-pa-action="'+action+'"'+(disabled?' disabled':'')+' '+extra+'>'+(symbol?icon(symbol):'')+esc(label)+'</button>';
  const loading=label=>window.OrionLoading?.markup?.(label,{compact:true})||'<p class="os-help" role="status">'+esc(label)+'</p>';
  const pageDTO=value=>{
    if(!value||!Array.isArray(value.items)||!number(value.total)||!number(value.page)||value.page<1||!number(value.page_size)||value.page_size<1||value.items.length>value.page_size)throw Error('Não foi possível conferir a lista retornada. Atualize a página.');
    return {...value,pages:Math.max(1,value.pages||Math.ceil(value.total/value.page_size))};
  };
  const message=error=>typeof error?.detail?.message==='string'?error.detail.message:typeof error?.message==='string'&&!/^(invalid_|Failed to fetch|NetworkError)/.test(error.message)?error.message:'Não foi possível concluir a consulta. Confira sua conexão e tente novamente.';
  let active=null;
  function unmount(){const old=active;active=null;old?.destroy();}
  function clearContext(){unmount();}

  async function mount(bridge){
    unmount();
    if(!bridge.host||!uuid(bridge.tenantId))throw Error('Contexto de empresa inválido.');
    const host=bridge.host,base='/api/v1/tenants/'+bridge.tenantId+'/subscription-whatsapp/audience';
    const state={alive:true,epoch:0,view:bridge.view==='groups'?'groups':'contacts',data:null,groups:[],query:'',group:bridge.initialGroupId||'',page:1,pageSize:30,loading:false,busy:false,stale:true,feedback:'',error:false,dialog:null,pending:null};
    const reads=new Set(),urls=new Set(),fields=window.OrionFields,releaseFields=fields?.bind(host)||(()=>{});
    const allowed=()=>{try{return state.alive&&bridge.canAccess?.()!==false;}catch{return false;}};
    const writable=()=>allowed()&&bridge.canWrite?.()!==false&&state.data!==null&&!state.stale&&!state.loading&&!state.busy&&!state.pending;
    const current=epoch=>allowed()&&state.epoch===epoch;
    const request=(suffix,method='GET',payload,signal)=>bridge.request(base+suffix,method,payload,signal?{signal}:undefined);
    function feedback(text,error=false){state.feedback=text;state.error=error;const target=host.querySelector('[data-pa-feedback]');if(target){target.hidden=!text;target.className='os-notice'+(error?' os-error':'');target.setAttribute('role',error?'alert':'status');target.textContent=text;}}
    function busy(value){state.busy=value;for(const root of [host,state.dialog?.element].filter(Boolean))for(const node of root.querySelectorAll('button,input,select,textarea')){if(value){node.dataset.paDisabled=String(node.disabled);node.disabled=true;}else if(node.dataset.paDisabled!==undefined){node.disabled=node.dataset.paDisabled==='true';delete node.dataset.paDisabled;}}host.setAttribute('aria-busy',String(value||state.loading));}
    function filters(){const params=new URLSearchParams();if(state.query)params.set('q',state.query);if(state.group&&state.view==='contacts')params.set('group_id',state.group);return params;}
    function groupOptions(selected=''){return '<option value="">Todos os grupos</option><option value="none"'+(selected==='none'?' selected':'')+'>Sem grupo</option>'+state.groups.map(g=>'<option value="'+esc(g.id)+'"'+(selected===g.id?' selected':'')+'>'+esc(g.name)+'</option>').join('');}
    const groupChecks=(selected=[],attr='data-pa-group')=>state.groups.length?'<fieldset class="os-audience-group-options"><legend>Grupos de contatos</legend>'+state.groups.map(g=>'<label class="os-audience-check"><input type="checkbox" '+attr+' value="'+esc(g.id)+'"'+(selected.includes(g.id)?' checked':'')+'><span>'+esc(g.name)+'</span></label>').join('')+'</fieldset>':'<p class="os-help">Nenhum grupo criado. Você pode organizar este contato em grupos depois.</p>';
    function render(){
      if(!allowed())return;
      const groups=state.view==='groups',data=state.data,disabled=!writable();
      host.innerHTML='<div class="os-title-row"><div><h1>'+(groups?'Grupos de Contatos':'Contatos')+'</h1><p>'+(groups?'Organize seus contatos nos grupos usados pelas campanhas e pelos envios.':'Gerencie o público e os dados das assinaturas da sua empresa.')+'</p></div><div class="os-actions os-audience-actions">'+button('Atualizar','refresh',{symbol:'refresh',disabled:state.busy})+(!groups?button('Exportar','export',{symbol:'download',disabled:disabled||!data?.total})+button('Importar','import',{symbol:'upload',disabled}):'')+button(groups?'Criar grupo':'Criar contato','create',{kind:'',symbol:'plus',disabled})+'</div></div><div data-pa-feedback class="os-notice'+(state.error?' os-error':'')+'" role="'+(state.error?'alert':'status')+'"'+(!state.feedback?' hidden':'')+'>'+esc(state.feedback)+'</div><form class="os-card os-form os-audience-filter" data-pa-form="search" role="search"><div class="os-form-grid"><div class="os-field"><label for="pa-search">'+(groups?'Pesquisar grupos':'Pesquisar por nome, telefone ou e-mail')+'</label><input id="pa-search" type="search" name="q" maxlength="120" value="'+esc(state.query)+'"></div>'+(!groups?'<div class="os-field"><label for="pa-group-filter">Grupo de contatos</label><select id="pa-group-filter" name="group_id">'+groupOptions(state.group)+'</select></div>':'')+'</div><div class="os-actions"><button type="submit" class="os-button"'+(state.busy?' disabled':'')+'>Pesquisar</button>'+button('Limpar filtros','clear',{disabled:state.busy})+'</div></form><div data-pa-results aria-busy="'+state.loading+'">'+(state.loading||!data?loading(groups?'Carregando grupos…':'Carregando contatos…'):data.items.length?renderList(data,groups):'<div class="os-empty os-card"><div class="os-square-icon">'+icon(groups?'layers':'users')+'</div><h3>'+esc(state.query||state.group?'Nenhum resultado encontrado':groups?'Nenhum grupo ainda':'Nenhum contato ainda')+'</h3><p>'+esc(state.query||state.group?'Revise a pesquisa ou limpe os filtros.':groups?'Crie um grupo para organizar seus contatos.':'Cadastre um contato ou importe sua lista em CSV ou TXT.')+'</p></div>')+'</div>';
      fields?.refresh(host);host.setAttribute('aria-busy',String(state.loading||state.busy));
    }
    function renderList(data,groups){
      const rows=data.items.map(row=>groups?'<tr><td><strong>'+esc(row.name)+'</strong></td><td>'+row.member_count.toLocaleString('pt-BR')+'</td><td><div class="os-actions">'+button('Ver contatos','group-contacts',{extra:'data-pa-id="'+esc(row.id)+'"'})+button('Editar','edit',{extra:'data-pa-id="'+esc(row.id)+'"',disabled:!writable()})+button('Excluir','delete',{extra:'data-pa-id="'+esc(row.id)+'"',disabled:!writable()})+'</div></td></tr>':'<tr><td><strong>'+esc(row.name)+'</strong>'+(row.email?'<small class="os-audience-secondary">'+esc(row.email)+'</small>':'')+'</td><td><span>'+esc(row.phone_e164||'Sem WhatsApp')+'</span>'+(row.telegram?'<small class="os-audience-secondary">Telegram: '+esc(row.telegram)+'</small>':'')+'</td><td><div class="os-audience-tags">'+(row.groups?.length?row.groups.map(g=>'<span class="os-badge os-neutral">'+esc(typeof g==='string'?g:g.name)+'</span>').join(''):'<span class="os-help">Sem grupo</span>')+'</div></td><td><span class="os-badge '+(row.chatbot_enabled?'os-success':'os-neutral')+'">'+(row.chatbot_enabled?'Ativo':'Inativo')+'</span></td><td><div class="os-actions">'+button('Editar','edit',{extra:'data-pa-key="'+esc(row.key)+'"',disabled:!writable()})+button('Excluir','delete',{extra:'data-pa-key="'+esc(row.key)+'"',disabled:!writable()})+'</div></td></tr>').join('');
      return '<div class="os-card os-table-scroll os-audience-table"><table class="os-table"><caption class="os-audience-caption">'+data.total.toLocaleString('pt-BR')+(groups?' grupo(s)':' contato(s)')+'</caption><thead><tr>'+(groups?'<th scope="col">Nome</th><th scope="col">Contatos</th>':'<th scope="col">Nome</th><th scope="col">Contato</th><th scope="col">Grupos</th><th scope="col">Chatbot</th>')+'<th scope="col">Ações</th></tr></thead><tbody>'+rows+'</tbody></table></div><nav class="os-audience-pagination" aria-label="Paginação"><span>Página '+data.page+' de '+data.pages+'</span><div class="os-actions">'+button('Anterior','previous',{disabled:data.page<=1||state.busy})+button('Próxima','next',{disabled:data.page>=data.pages||state.busy})+'</div></nav>';
    }
    async function load(){
      if(!allowed()||state.busy)return;
      const epoch=++state.epoch,controller=new AbortController();reads.add(controller);state.loading=true;render();
      try{
        const params=filters();params.set('page',String(state.page));params.set('page_size',String(state.pageSize));
        const [data,groups]=await Promise.all([request('/'+state.view+'?'+params,'GET',undefined,controller.signal),request('/groups?page=1&page_size=100','GET',undefined,controller.signal)]);
        if(!current(epoch))return;state.data=pageDTO(data);state.groups=pageDTO(groups).items;state.stale=false;
        if(state.page>state.data.pages){state.page=state.data.pages;state.loading=false;return load();}
      }catch(error){if(current(epoch)&&error.name!=='AbortError'){state.stale=true;feedback(message(error),true);}}
      finally{reads.delete(controller);if(current(epoch)){state.loading=false;render();}}
    }
    function dialog(kind,title,body,extra={}){
      if(state.dialog||!writable())return null;
      const element=document.createElement('dialog'),titleId='pa-'+crypto.randomUUID();element.className='os-dialog os-audience-dialog'+(kind==='contact'?' os-sheet':'');element.setAttribute('aria-labelledby',titleId);
      element.innerHTML='<header class="os-audience-dialog-header"><div><h2 id="'+titleId+'">'+esc(title)+'</h2></div><button class="os-icon-button os-close" type="button" data-pa-action="close" aria-label="Fechar">'+icon('close')+'</button></header><form class="os-form" data-pa-form="'+kind+'" novalidate><div class="os-audience-dialog-body">'+body+'</div><footer class="os-audience-dialog-footer"><div class="os-form-message" data-pa-dialog-error role="alert"></div><div class="os-actions">'+button('Cancelar','close')+'<button type="submit" class="os-button" data-pa-confirm>'+esc(kind==='import'?'Conferir arquivo':kind==='delete'?'Excluir':'Salvar')+'</button></div></footer></form>';
      const entry={kind,element,trigger:document.activeElement,releaseFields:fields?.bind(element)||(()=>{}),dirty:false,...extra};state.dialog=entry;
      for(const [event,handler] of listeners)element.addEventListener(event,handler);
      document.body.append(element);element.showModal();fields?.refresh(element);element.querySelector('input:not([type=checkbox]),textarea,button:not(.os-close)')?.focus({preventScroll:true});return entry;
    }
    function close(force=false){const entry=state.dialog;if(!entry||!force&&(state.busy||state.pending))return;entry.read?.abort();entry.releaseFields();for(const [event,handler] of listeners)entry.element.removeEventListener(event,handler);entry.element.close();entry.element.remove();state.dialog=null;if(entry.trigger?.isConnected)entry.trigger.focus({preventScroll:true});}
    function dialogError(text){const entry=state.dialog,target=entry?.element.querySelector('[data-pa-dialog-error]');if(target){target.textContent=text;target.tabIndex=-1;target.focus({preventScroll:true});}else feedback(text,true);}
    const input=(label,name,value='',type='text',attrs='')=>'<div class="os-field"><label for="pa-'+name+'">'+esc(label)+'</label><input id="pa-'+name+'" name="'+name+'" type="'+type+'" value="'+esc(value??'')+'" '+attrs+'></div>';
    const countryInput=(value='BR')=>'<div class="os-field"><label for="pa-default-country">País dos números sem DDI</label><select id="pa-default-country" name="default_country">'+countries.map(([key,label])=>'<option value="'+key+'"'+(key===value?' selected':'')+'>'+label+'</option>').join('')+'</select></div>';
    function contactEditor(row){
      const value=row||{name:'',chatbot_enabled:true,audio_transcription_enabled:true,group_ids:[]};
      const body='<div class="os-form-grid">'+input('Nome','name',value.name,'text','required maxlength="120" autocomplete="name"')+input('WhatsApp','phone_e164',value.phone_e164,'tel','maxlength="80" placeholder="+55 (67) 99999-9999" autocomplete="tel"')+countryInput(value.phone_e164?'INT':'BR')+input('Telegram (opcional)','telegram',value.telegram,'text','maxlength="64"')+input('E-mail (opcional)','email',value.email,'email','maxlength="320" autocomplete="email"')+'</div><p class="os-help">Informe um WhatsApp ou um identificador do Telegram. Números com + e DDI mantêm o país informado.</p><div class="os-field"><label for="pa-notes">Notas</label><textarea id="pa-notes" name="notes" maxlength="5000" rows="3">'+esc(value.notes)+'</textarea></div>'+groupChecks(value.group_ids)+'<fieldset class="os-audience-preferences"><legend>Preferências de atendimento</legend><label class="os-audience-check"><input type="checkbox" name="chatbot_enabled"'+(value.chatbot_enabled?' checked':'')+'><span>Chatbot ativo</span></label><label class="os-audience-check"><input type="checkbox" name="audio_transcription_enabled"'+(value.audio_transcription_enabled?' checked':'')+'><span>Transcrição de áudio</span></label></fieldset><details class="os-audience-details"'+(value.plan_name||value.due_date?' open':'')+'><summary>Dados da assinatura</summary><div class="os-form-grid">'+input('Plano','plan_name',value.plan_name,'text','maxlength="120"')+input('Preço (R$)','price',value.price_cents==null?'':fields?.moneyDisplay?.(value.price_cents)||(value.price_cents/100).toFixed(2).replace('.',','),'text','inputmode="decimal" data-orion-field="money"')+input('Vencimento','due_date',value.due_date,'date')+input('Usuário do serviço','service_username',value.service_username,'text','maxlength="255" autocomplete="off"')+input(value.password_configured?'Substituir senha do serviço':'Senha do serviço','service_password','','password','maxlength="1024" autocomplete="new-password"')+'</div>'+(value.password_configured?'<p class="os-help">Uma senha já está salva. Deixe o campo vazio para mantê-la.</p><label class="os-audience-check"><input type="checkbox" name="clear_password"><span>Remover a senha salva</span></label>':'')+'</details>';
      dialog('contact',row?'Editar contato':'Criar contato',body,{record:row});
    }
    async function readContact(key){
      if(!writable())return;const epoch=state.epoch;busy(true);
      try{const result=await request('/contacts/'+encodeURIComponent(key));if(current(epoch)){busy(false);contactEditor(result.contact||result);}}
      catch(error){if(current(epoch))feedback(message(error),true);}finally{if(current(epoch))busy(false);}
    }
    async function groupEditor(group){
      if(!writable())return;
      let detail={group:null,contact_keys:[]};const epoch=state.epoch;
      if(group){busy(true);try{detail=await request('/groups/'+encodeURIComponent(group.id));}catch(error){if(current(epoch))feedback(message(error),true);return;}finally{if(current(epoch))busy(false);}}
      if(!current(epoch))return;
      const entry=dialog('group',group?'Editar grupo':'Criar grupo',input('Nome do grupo','name',detail.group?.name||'','text','required maxlength="100"')+'<section class="os-audience-picker"><div class="os-field"><label for="pa-picker-query">Pesquisar contatos para o grupo</label><input id="pa-picker-query" name="picker_query" type="search" maxlength="120"></div><div class="os-actions">'+button('Pesquisar contatos','picker-search')+'</div><div data-pa-picker>'+loading('Carregando contatos…')+'</div></section>',{record:detail.group,selected:new Set(detail.contact_keys),pickerPage:1,pickerQuery:'',pickerTotal:0,pickerPages:1,pickerItems:[],pickerRead:0});
      if(entry)await loadPicker(entry);
    }
    async function loadPicker(entry){
      const serial=++entry.pickerRead,epoch=state.epoch;entry.read?.abort();const controller=new AbortController();entry.read=controller;reads.add(controller);
      const target=entry.element.querySelector('[data-pa-picker]');target.innerHTML=loading('Carregando contatos…');
      try{const data=pageDTO(await request('/contacts?'+new URLSearchParams({q:entry.pickerQuery,page:String(entry.pickerPage),page_size:'50'}),'GET',undefined,controller.signal));if(!current(epoch)||state.dialog!==entry||serial!==entry.pickerRead)return;entry.pickerItems=data.items;entry.pickerPages=data.pages;entry.pickerTotal=data.total;renderPicker(entry);}
      catch(error){if(current(epoch)&&state.dialog===entry&&error.name!=='AbortError')dialogError(message(error));}finally{reads.delete(controller);}
    }
    function renderPicker(entry){
      entry.element.querySelector('[data-pa-picker]').innerHTML='<p class="os-help" data-pa-picker-count>'+entry.selected.size.toLocaleString('pt-BR')+' contato(s) selecionado(s) · até 5.000 por grupo</p><div class="os-audience-picker-list">'+(entry.pickerItems.length?entry.pickerItems.map(row=>'<label class="os-audience-check"><input type="checkbox" data-pa-contact-key="'+esc(row.key)+'"'+(entry.selected.has(row.key)?' checked':'')+'><span><strong>'+esc(row.name)+'</strong><small>'+esc(row.phone_e164||row.telegram||'')+'</small></span></label>').join(''):'<p class="os-help">Nenhum contato encontrado.</p>')+'</div><div class="os-audience-pagination"><span>Página '+entry.pickerPage+' de '+entry.pickerPages+'</span><div class="os-actions">'+button('Anterior','picker-previous',{disabled:entry.pickerPage<=1})+button('Próxima','picker-next',{disabled:entry.pickerPage>=entry.pickerPages})+'</div></div>';
    }
    function importEditor(){dialog('import','Importar contatos','<p>Envie um arquivo CSV ou TXT com telefone e nome. Você confere os resultados antes de salvar.</p><div class="os-field"><label for="pa-import-file">Arquivo CSV ou TXT</label><input id="pa-import-file" type="file" name="file" accept=".csv,.txt,text/csv,text/plain"><small class="os-help">Até 20.000 linhas e 8 MB por arquivo. Contatos repetidos são identificados na conferência.</small></div>'+button('Baixar exemplo','example',{symbol:'download'})+countryInput()+groupChecks([], 'data-pa-import-group')+'<div data-pa-import-preview></div>',{preview:null,file:null});}
    async function readFile(file){
      if(!file||!/[.](csv|txt)$/i.test(file.name)||file.size>8*1024*1024||file.size===0)throw Error('Escolha um arquivo CSV ou TXT com até 8 MB e pelo menos um contato.');
      const bytes=new Uint8Array(await file.arrayBuffer());
      if(bytes[0]===255&&bytes[1]===254)return new TextDecoder('utf-16le',{fatal:true}).decode(bytes);
      if(bytes[0]===254&&bytes[1]===255)return new TextDecoder('utf-16be',{fatal:true}).decode(bytes);
      try{return new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{return new TextDecoder('windows-1252',{fatal:true}).decode(bytes);}
    }
    function renderPreview(entry){
      const value=entry.preview,counts=value.counts,target=entry.element.querySelector('[data-pa-import-preview]');
      if(!counts||!['total','valid','invalid','duplicates','existing','new'].every(k=>number(counts[k])))throw Error('Não foi possível conferir as contagens do arquivo.');
      const token=typeof value.preview_token==='string'&&value.preview_token.length>0,skipAllowed=token&&value.can_skip_invalid===true;
      target.innerHTML='<section class="os-card os-audience-import-review"><h3>Conferência do arquivo</h3><dl class="os-audience-import-counts">'+[['total','Linhas lidas'],['new','Novos contatos'],['existing','Já cadastrados'],['duplicates','Repetidos no arquivo'],['invalid','Linhas inválidas']].map(([key,label])=>'<div><dt>'+label+'</dt><dd>'+counts[key].toLocaleString('pt-BR')+'</dd></div>').join('')+'</dl>'+(value.sample?.length?'<div class="os-table-scroll"><table class="os-table"><caption>Prévia dos contatos</caption><thead><tr><th>Linha</th><th>Nome</th><th>WhatsApp</th></tr></thead><tbody>'+value.sample.map(row=>'<tr><td>'+row.line+'</td><td>'+esc(row.name)+'</td><td>'+esc(row.phone_e164)+'</td></tr>').join('')+'</tbody></table></div>':'')+(counts.invalid?'<div class="os-notice os-error"><p>'+esc(skipAllowed?'O arquivo contém '+counts.invalid+' linha(s) inválida(s). Corrija o arquivo ou escolha abaixo importar apenas as válidas.':'Não é possível importar este arquivo. Corrija a estrutura ou os erros indicados e envie o arquivo novamente.')+'</p></div><ul class="os-audience-import-errors">'+(value.errors||[]).map(row=>'<li>Linha '+row.line+': '+esc(row.message)+'</li>').join('')+'</ul>'+(skipAllowed?'<label class="os-audience-check"><input type="checkbox" name="skip_invalid"><span>Importar apenas os contatos válidos e ignorar as '+counts.invalid+' linha(s) inválida(s)</span></label>':''):'')+'</section>';
      const confirm=entry.element.querySelector('[data-pa-confirm]');confirm.textContent='Importar contatos';confirm.disabled=!token||value.can_commit!==true||counts.valid===0||counts.invalid>0;
    }
    async function prepareImport(entry,form){
      const epoch=state.epoch;busy(true);
      try{
        const file=form.elements.file.files[0],content=await readFile(file);
        const result=await request('/imports/preview','POST',{file_name:file.name,content,default_country:form.elements.default_country.value,group_ids:[...form.querySelectorAll('[data-pa-import-group]:checked')].map(node=>node.value)});
        if(!current(epoch)||state.dialog!==entry)return;entry.preview=result;entry.file=file;busy(false);renderPreview(entry);
      }catch(error){if(current(epoch))dialogError(message(error));}finally{if(current(epoch))busy(false);}
    }
    function download(content,name,mime){const blob=content instanceof Blob?content:new Blob([content],{type:mime}),url=URL.createObjectURL(blob),link=document.createElement('a');urls.add(url);link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>{URL.revokeObjectURL(url);urls.delete(url);},1000);}
    async function exportContacts(){
      if(!writable())return;const epoch=state.epoch,controller=new AbortController();reads.add(controller);busy(true);
      try{const response=await fetch(base+'/contacts/export?'+filters(),{credentials:'same-origin',cache:'no-store',headers:{Accept:'text/csv'},signal:controller.signal});if(!response.ok||!response.headers.get('content-type')?.includes('text/csv'))throw Error('Não foi possível exportar os contatos. Atualize a lista e tente novamente.');const blob=await response.blob();if(current(epoch)){download(blob,'orion-contatos.csv','text/csv;charset=utf-8');feedback('Exportação concluída com todos os contatos dos filtros atuais.');}}
      catch(error){if(current(epoch)&&error.name!=='AbortError')feedback(message(error),true);}finally{reads.delete(controller);if(current(epoch))busy(false);}
    }
    function importSummary(result){const c=result.counts;if(!c||!number(c.added))return 'Importação concluída. A lista foi atualizada.';return 'Importação concluída: '+c.added.toLocaleString('pt-BR')+' novo(s), '+(c.unchanged||0).toLocaleString('pt-BR')+' já cadastrado(s)'+(c.duplicates?', '+c.duplicates+' repetido(s) no arquivo':'')+(c.archived_skipped?', '+c.archived_skipped+' excluído(s) anteriormente e mantido(s) fora da lista':'')+(c.invalid_skipped?', '+c.invalid_skipped+' linha(s) inválida(s) ignorada(s) com sua confirmação':'')+'.';}
    async function mutate(path,method,payload,kind){
      if(!allowed()||state.busy)return;const epoch=state.epoch;
      if(!state.pending)state.pending={path,method,payload:{...payload,idempotency_key:crypto.randomUUID()},kind};
      const pending=state.pending;busy(true);
      try{
        const value=await request(pending.path,pending.method,pending.payload);
        if(!current(epoch))return;if(value?.idempotency_key!==pending.payload.idempotency_key||!value.result)throw Error('Não foi possível confirmar o resultado. Tente novamente para conferir a mesma operação.');
        state.pending=null;busy(false);close(true);feedback(pending.kind==='import'?importSummary(value.result):pending.kind==='delete'?'Registro excluído.':pending.kind==='group'?'Grupo salvo.':'Contato salvo.');await load();
      }catch(error){
        if(!current(epoch))return;
        if(Number(error.status)>=400&&Number(error.status)<500&&![408,429].includes(Number(error.status)))state.pending=null;
        busy(false);dialogError(state.pending?'Não foi possível confirmar a operação. Clique em “Tentar novamente” para conferir o mesmo pedido.':message(error));
        if(state.pending){const confirm=state.dialog?.element.querySelector('[data-pa-confirm]');if(confirm){confirm.disabled=false;confirm.textContent='Tentar novamente';}for(const node of state.dialog?.element.querySelectorAll('input,textarea,select')||[])node.disabled=true;}
      }finally{if(current(epoch)&&state.busy)busy(false);}
    }
    async function submit(event){
      const form=event.target.closest('[data-pa-form]');if(!form)return;event.preventDefault();if(!allowed()||state.busy)return;
      if(form.dataset.paForm==='search'){state.query=form.elements.q.value.trim();state.group=form.elements.group_id?.value||'';state.page=1;feedback('');await load();return;}
      const entry=state.dialog;if(!entry||!entry.element.contains(form))return;
      if(state.pending){await mutate();return;}
      if(!writable()||!form.reportValidity())return;
      try{
        if(entry.kind==='contact'){
          const f=form.elements,value=name=>f[name]?.value.trim()||null;
          const contact={name:value('name'),phone_e164:value('phone_e164'),default_country:f.default_country.value,telegram:value('telegram'),email:value('email'),notes:value('notes'),group_ids:[...form.querySelectorAll('[data-pa-group]:checked')].map(node=>node.value),chatbot_enabled:f.chatbot_enabled.checked,audio_transcription_enabled:f.audio_transcription_enabled.checked,plan_name:value('plan_name'),price_cents:fields?.parseMoney?fields.parseMoney(f.price.value):f.price.value?Math.round(Number(f.price.value.replace(',','.'))*100):null,due_date:value('due_date'),service_username:value('service_username'),clear_password:!!f.clear_password?.checked};
          if(f.service_password.value)contact.service_password=f.service_password.value;
          if(!contact.phone_e164&&!contact.telegram)throw Error('Informe um WhatsApp ou um identificador do Telegram.');
          if(contact.price_cents!==null&&(!number(contact.price_cents)||contact.price_cents>100000000000))throw Error('Confira o preço da assinatura.');
          await mutate('/contacts'+(entry.record?'/'+encodeURIComponent(entry.record.key):''),entry.record?'PATCH':'POST',{contact,...(entry.record?{expected_version:entry.record.version}:{})},'contact');
        }else if(entry.kind==='group')await mutate('/groups'+(entry.record?'/'+entry.record.id:''),entry.record?'PATCH':'POST',{name:form.elements.name.value.trim(),contact_keys:[...entry.selected],...(entry.record?{expected_version:entry.record.version}:{})},'group');
        else if(entry.kind==='delete')await mutate('/'+entry.resource+'/'+encodeURIComponent(entry.record.key||entry.record.id),'DELETE',{expected_version:entry.record.version},'delete');
        else if(entry.kind==='import'){
          if(!entry.preview){await prepareImport(entry,form);return;}
          if(!entry.preview.preview_token||!(entry.preview.can_commit===true||entry.preview.can_skip_invalid===true))throw Error('Corrija os erros indicados e envie o arquivo novamente.');
          if(Date.parse(entry.preview.expires_at)<=Date.now()){entry.preview=null;throw Error('A conferência expirou. Confira o arquivo novamente.');}
          const skip=!!form.elements.skip_invalid?.checked;if(entry.preview.counts.invalid&&!skip)throw Error('Corrija o arquivo ou confirme a importação apenas das linhas válidas.');
          await mutate('/imports/commit','POST',{preview_token:entry.preview.preview_token,skip_invalid:skip},'import');
        }
      }catch(error){dialogError(message(error));}
    }
    async function click(event){
      const target=event.target.closest('[data-pa-action]');if(!target||target.disabled||!allowed())return;const action=target.dataset.paAction,entry=state.dialog;
      if(action==='close'){close();return;}if(state.busy||state.pending)return;
      if(action==='refresh')await load();
      else if(action==='clear'){state.query='';state.group='';state.page=1;feedback('');await load();}
      else if(action==='previous'||action==='next'){state.page+=action==='next'?1:-1;await load();}
      else if(action==='create'){if(state.view==='groups')await groupEditor();else contactEditor();}
      else if(action==='edit'){if(state.view==='groups')await groupEditor(state.data.items.find(row=>row.id===target.dataset.paId));else await readContact(target.dataset.paKey);}
      else if(action==='delete'){
        const row=state.data.items.find(item=>state.view==='groups'?item.id===target.dataset.paId:item.key===target.dataset.paKey);if(!row)return;
        dialog('delete',state.view==='groups'?'Excluir grupo?':'Excluir contato?','<p>'+esc(row.name)+'</p><p>'+(state.view==='groups'?'O grupo será removido da lista. Os contatos continuam cadastrados.':'O contato será removido da lista e de seus grupos. O histórico de envios permanece disponível.')+'</p>',{record:row,resource:state.view});
      }else if(action==='group-contacts'){if(bridge.openContacts){bridge.openContacts(target.dataset.paId);return;}state.view='contacts';state.group=target.dataset.paId;state.query='';state.page=1;await load();}
      else if(action==='import')importEditor();
      else if(action==='export')await exportContacts();
      else if(action==='example')download('\ufeffTelefone,Nome\r\n+5511999999999,Nome do contato\r\n','orion-exemplo-contatos.csv','text/csv;charset=utf-8');
      else if(action==='picker-search'&&entry){entry.pickerQuery=entry.element.querySelector('[name=picker_query]').value.trim();entry.pickerPage=1;await loadPicker(entry);}
      else if((action==='picker-previous'||action==='picker-next')&&entry){entry.pickerPage+=action==='picker-next'?1:-1;await loadPicker(entry);}
    }
    function change(event){
      const entry=state.dialog;if(!entry||state.busy||state.pending)return;
      if(event.target.hasAttribute('data-pa-contact-key')){
        const key=event.target.dataset.paContactKey;if(event.target.checked){if(entry.selected.size>=5000){event.target.checked=false;dialogError('Cada grupo aceita até 5.000 contatos.');return;}entry.selected.add(key);}else entry.selected.delete(key);
        entry.element.querySelector('[data-pa-picker-count]').textContent=entry.selected.size.toLocaleString('pt-BR')+' contato(s) selecionado(s) · até 5.000 por grupo';
      }
      if(entry.kind==='import'){
        if(event.target.name==='skip_invalid'){entry.element.querySelector('[data-pa-confirm]').disabled=!entry.preview?.preview_token||entry.preview.can_skip_invalid!==true||!entry.preview.counts.valid||!event.target.checked;return;}
        if(event.target.matches('[name=file],[name=default_country],[data-pa-import-group]')){entry.preview=null;entry.element.querySelector('[data-pa-import-preview]').replaceChildren();const confirm=entry.element.querySelector('[data-pa-confirm]');confirm.textContent='Conferir arquivo';confirm.disabled=false;}
      }
    }
    function keydown(event){if(event.key==='Enter'&&event.target.name==='picker_query'){event.preventDefault();if(state.dialog&&!state.busy){state.dialog.pickerQuery=event.target.value.trim();state.dialog.pickerPage=1;void loadPicker(state.dialog);}}}
    function cancel(event){event.preventDefault();close();}
    const listeners=[['click',click],['submit',submit],['change',change],['cancel',cancel],['keydown',keydown]];
    for(const [event,handler] of listeners)host.addEventListener(event,handler);
    const controller={destroy(){state.alive=false;state.epoch++;for(const controller of reads)controller.abort();reads.clear();close(true);for(const url of urls)URL.revokeObjectURL(url);urls.clear();releaseFields();for(const [event,handler] of listeners)host.removeEventListener(event,handler);host.replaceChildren();},getState:()=>({view:state.view,total:state.data?.total,page:state.page,busy:state.busy,pending:!!state.pending})};active=controller;
    await load();return controller;
  }
  window.OrionSubscriptionAudience={mount,unmount,clearContext};
})();
