/* CRM tools use the existing authenticated request/membership bridge. No sends. */
(function(){
  'use strict';
  let active=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const button=(label,action,extra='')=>'<button type="button" class="os-button os-outline" data-inbox-action="'+action+'" '+extra+'>'+esc(label)+'</button>';
  function unmount(){if(active){window.OrionConversations?.unmount();active.controller.abort();active.alive=false;active.host.inert=false;active.host.removeAttribute('aria-busy');active=null;}}
  async function mount({host,request,tenantId,canWrite=false}){
    unmount();if(!host)return;
    const state={host,request,tenantId,canWrite,alive:true,controller:new AbortController(),labels:[],replies:[],customers:[],customer:null,
      selected:'',editor:null,tab:'customers',query:'',label:'',offset:0,hasMore:false,busy:false,confirmation:null,sequence:0,central:false,tools:true};
    active=state;
    const current=()=>active===state&&state.alive&&host.isConnected;
    const path=value=>'/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/inbox'+value;
    const query=selector=>host.querySelector(selector);
    const notice=(value,error=false)=>{const area=query('[data-inbox-notice]');if(area){area.textContent=value;area.setAttribute('role',error?'alert':'status');}};
    function render(){
      if(!current())return;
      state.sequence++;window.OrionConversations?.unmount();
      const disabled=state.busy?'disabled':'',write=canWrite&&!state.busy?'':'disabled',e=state.editor;
      host.innerHTML='<div class="os-inbox"><p class="os-help">Organize clientes e prepare respostas com informações do seu cadastro. O preparo de uma resposta não envia mensagens.</p>'+
        '<div class="os-actions" role="tablist" aria-label="Ferramentas de atendimento">'+[...(state.central?[['conversations','Conversas']]:[]),...(state.tools?[['customers','Clientes e etiquetas'],['replies','Respostas rápidas']]:[])].map(([id,label])=>'<button type="button" class="os-button '+(state.tab===id?'':'os-outline')+'" role="tab" aria-selected="'+(state.tab===id)+'" data-inbox-tab="'+id+'" '+disabled+'>'+label+'</button>').join('')+'</div>'+
        '<p data-inbox-notice role="status" aria-live="polite"></p>'+
        (state.confirmation?'<div class="os-notice" role="group" aria-label="Confirmar exclusão"><p>Excluir '+esc(state.confirmation.name)+'? As associações desta etiqueta também serão removidas.</p>'+button('Voltar','cancel-delete',disabled)+button('Confirmar exclusão','confirm-delete',write)+'</div>':'')+
        (state.tab==='conversations'?'<div data-inbox-central></div>':state.tab==='customers'?'<div class="os-inbox-grid"><section class="os-card"><h2>Clientes cadastrados</h2><form data-inbox-form="search"><label for="inboxQuery">Nome ou telefone</label><input id="inboxQuery" name="q" maxlength="100" value="'+esc(state.query)+'"><label for="inboxFilter">Etiqueta</label><select id="inboxFilter" name="label"><option value="">Todas as etiquetas</option>'+state.labels.map(l=>'<option value="'+esc(l.id)+'" '+(state.label===l.id?'selected':'')+'>'+esc(l.name)+'</option>').join('')+'</select><button class="os-button" '+disabled+'>Pesquisar</button></form><div class="os-inbox-list">'+(state.customers.length?state.customers.map(c=>'<button type="button" class="os-inbox-person" data-inbox-customer="'+esc(c.id)+'" '+disabled+' aria-pressed="'+(state.selected===c.id)+'"><strong>'+esc(c.name)+'</strong><small>'+esc(c.phone)+'</small></button>').join(''):'<p>Nenhum cliente encontrado neste recorte.</p>')+'</div><div class="os-actions">'+button('Anterior','previous',state.offset>0?disabled:'disabled')+button('Próxima página','next',state.hasMore?disabled:'disabled')+'</div></section><section class="os-card"><h2>Contexto do cliente</h2>'+customerHTML()+'</section></div><section class="os-card"><h2>Etiquetas da empresa</h2><p class="os-help">Etiquetas organizam o atendimento. Autorizações comerciais continuam no registro de consentimento.</p><div class="os-inbox-tags">'+state.labels.map(l=>'<div class="os-inbox-tag"><span class="os-inbox-swatch" style="background-color:'+esc(/^#[0-9a-f]{6}$/i.test(l.color)?l.color:'#18836b')+'"></span><span>'+esc(l.name)+'</span>'+button('Editar','edit-label','data-id="'+esc(l.id)+'" '+write)+button('Excluir','delete-label','data-id="'+esc(l.id)+'" '+write)+'</div>').join('')+'</div>'+button('Nova etiqueta','new-label',write)+(e?.kind==='label'?labelForm(e):'')+'</section>':
        '<section class="os-card"><div class="os-actions"><h2>Biblioteca de respostas</h2>'+button('Nova resposta','new-reply',write)+'</div><label for="inboxReplySearch">Pesquisar título, categoria ou texto</label><input id="inboxReplySearch" type="search" placeholder="Pesquisar na biblioteca"><div class="os-inbox-list" data-inbox-replies>'+repliesHTML(state.replies,write)+'</div>'+(e?.kind==='reply'?replyForm(e):'')+'</section>')+'</div>';
      if(state.tab==='conversations')void window.OrionConversations?.mount({host:query('[data-inbox-central]'),request,tenantId,canWrite,toolsEnabled:state.tools,onCustomer:id=>{state.selected=id;state.tab='customers';void perform(reload);}});
    }
    function customerHTML(){
      const data=state.customer;if(!data)return '<p>Selecione um cliente para consultar os dados reais.</p>';
      const customer=data.customer,selected=new Set(data.labels.map(x=>x.id));
      const history=data.history||[],next=data.upcoming;
      return '<h3>'+esc(customer.name)+'</h3><p>'+esc(customer.whatsapp_e164)+'</p>'+(next?'<p>Próximo agendamento: <strong>'+esc(new Date(next.start_at).toLocaleString('pt-BR',{timeZone:data.timezone}))+'</strong></p><p>'+esc(next.services)+' · '+esc(next.professional)+' · '+esc(next.protocol)+'</p>':'<p>Nenhum agendamento futuro confirmado.</p>')+
        '<div class="os-inbox-tags">'+state.labels.map(l=>'<label class="os-inbox-check"><input type="checkbox" data-inbox-label="'+esc(l.id)+'" '+(selected.has(l.id)?'checked':'')+' '+(!canWrite||state.busy?'disabled':'')+'>'+esc(l.name)+'</label>').join('')+'</div>'+
        '<h3>Histórico recente</h3><p class="os-help">Até '+esc(data.history_limit)+' agendamentos disponíveis nesta consulta.</p><ul>'+history.map(a=>'<li>'+esc(a.services||'Serviço não informado')+' · '+esc(new Date(a.start_at).toLocaleString('pt-BR',{timeZone:data.timezone}))+' · '+esc({confirmed:'Confirmado',cancelled:'Cancelado',completed:'Concluído',no_show:'Não compareceu',hold:'Reserva temporária',expired:'Reserva expirada'}[a.status]||a.status)+'</li>').join('')+'</ul>'+
        (customer.notes?'<details><summary>Observações cadastradas</summary><p class="os-inbox-text">'+esc(customer.notes)+'</p></details>':'')+
        '<label for="inboxPreparedReply">Resposta rápida</label><select id="inboxPreparedReply"><option value="">Selecione uma resposta</option>'+state.replies.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.category+' · '+r.title)+'</option>').join('')+'</select>'+button('Preparar com dados reais','preview',state.busy?'disabled':'')+
        '<div data-inbox-preview class="os-inbox-preview" hidden><label for="inboxPreparedText">Texto preparado para revisão</label><textarea id="inboxPreparedText" readonly rows="5"></textarea>'+button('Copiar texto','copy')+'<p>Copie e revise no canal de atendimento autorizado. Nenhuma mensagem foi enviada.</p></div>';
    }
    function repliesHTML(items,write){return items.length?items.map(r=>'<article class="os-inbox-reply"><div><strong>'+esc(r.title)+'</strong><small>'+esc(r.category)+' · '+(r.visibility==='personal'?'Pessoal':'Compartilhada')+' · versão '+esc(r.version)+'</small></div><p class="os-inbox-text">'+esc(r.text)+'</p><div class="os-actions">'+button('Editar','edit-reply','data-id="'+esc(r.id)+'" '+write)+button('Duplicar','duplicate-reply','data-id="'+esc(r.id)+'" '+write)+button('Excluir','delete-reply','data-id="'+esc(r.id)+'" '+write)+'</div></article>').join(''):'<p>Nenhuma resposta cadastrada.</p>';}
    function labelForm(e){return '<form data-inbox-form="label" class="os-inbox-editor"><h3>'+ (e.id?'Editar etiqueta':'Nova etiqueta')+'</h3><label for="inboxLabelName">Nome</label><input id="inboxLabelName" name="name" maxlength="60" required value="'+esc(e.name)+'"><label for="inboxColor">Cor</label><input id="inboxColor" name="color" type="color" value="'+esc(e.color)+'"><div class="os-actions"><button class="os-button" '+(state.busy?'disabled':'')+'>Salvar etiqueta</button>'+button('Cancelar','cancel-editor',state.busy?'disabled':'')+'</div></form>';}
    function replyForm(e){return '<form data-inbox-form="reply" class="os-inbox-editor"><h3>'+ (e.id?'Editar resposta':'Nova resposta')+'</h3><label for="inboxReplyTitle">Título</label><input id="inboxReplyTitle" name="title" maxlength="100" required value="'+esc(e.title)+'"><label for="inboxCategory">Categoria</label><input id="inboxCategory" name="category" maxlength="60" required value="'+esc(e.category)+'"><label for="inboxVisibility">Visibilidade</label><select id="inboxVisibility" name="visibility"><option value="company" '+(e.visibility==='company'?'selected':'')+'>Compartilhada com a empresa</option><option value="personal" '+(e.visibility==='personal'?'selected':'')+'>Somente minha conta</option></select><label for="inboxReplyText">Texto</label><textarea id="inboxReplyText" name="text" maxlength="1200" rows="5" required>'+esc(e.text)+'</textarea><p class="os-help">Variáveis: {{cliente.nome}}, {{cliente.primeiro_nome}}, {{empresa.nome}}, {{agendamento.data}}, {{agendamento.horario}}, {{agendamento.protocolo}}, {{profissional.nome}}, {{servico.nome}}, {{servico.valor}}. Dados ausentes impedem o preparo.</p><div class="os-actions"><button class="os-button" '+(state.busy?'disabled':'')+'>Salvar resposta</button>'+button('Cancelar','cancel-editor',state.busy?'disabled':'')+'</div></form>';}
    async function reload(){
      const [tags,replies,people]=await Promise.all([request(path('/labels')),request(path('/quick-replies')),request(path('/customers?q='+encodeURIComponent(state.query)+'&offset='+state.offset+(state.label?'&label_id='+encodeURIComponent(state.label):'')))]);
      if(!current())return;state.labels=tags.items;state.replies=replies.items;state.customers=people.items;state.hasMore=people.has_more;
      if(state.selected)state.customer=await request(path('/customers/'+encodeURIComponent(state.selected)));
      if(current())render();
    }
    async function perform(work,success){
      if(state.busy)return;state.busy=true;host.setAttribute('aria-busy','true');host.inert=true;
      try{await work();if(current()){state.confirmation=null;await reload();state.busy=false;render();notice(success||'Alteração salva.');}}
      catch(error){if(current())notice(error.message||'Não foi possível concluir. Seu formulário foi preservado.',true);}
      finally{state.busy=false;if(current()){host.removeAttribute('aria-busy');host.inert=false;}}
    }
    host.addEventListener('submit',event=>{
      const form=event.target.closest('[data-inbox-form]');if(!form)return;event.preventDefault();if(state.busy)return;
      const values=Object.fromEntries(new FormData(form));
      if(form.dataset.inboxForm==='search'){state.query=values.q;state.label=values.label;state.offset=0;void perform(reload,'Pesquisa atualizada.');return;}
      if(!canWrite)return;
      const e=state.editor,kind=form.dataset.inboxForm,collection=kind==='label'?'/labels':'/quick-replies';
      void perform(async()=>{await request(path(collection+(e.id?'/'+e.id:'')),e.id?'PUT':'POST',{...values,expected_version:e.id?e.version:0});state.editor=null;},'Salvo com sucesso.');
    },{signal:state.controller.signal});
    host.addEventListener('input',event=>{if(event.target.id==='inboxReplySearch')query('[data-inbox-replies]').innerHTML=repliesHTML(state.replies.filter(r=>(r.title+' '+r.category+' '+r.text).toLocaleLowerCase().includes(event.target.value.toLocaleLowerCase())),canWrite?'':'disabled');},{signal:state.controller.signal});
    host.addEventListener('change',event=>{
      const id=event.target.dataset.inboxLabel;if(!id||!state.selected||!canWrite||state.busy)return;
      const checked=event.target.checked;
      void perform(()=>request(path('/customers/'+state.selected+'/labels/'+id),checked?'PUT':'DELETE'),'Etiquetas atualizadas.');
    },{signal:state.controller.signal});
    host.addEventListener('click',async event=>{
      if(state.busy)return;
      const tab=event.target.closest('[data-inbox-tab]'),person=event.target.closest('[data-inbox-customer]'),action=event.target.closest('[data-inbox-action]');
      if(tab){if(state.editor&&!window.confirm('Descartar as alterações ainda não salvas?'))return;state.editor=null;state.tab=tab.dataset.inboxTab;render();return;}
      if(person){state.selected=person.dataset.inboxCustomer;state.customer=null;void perform(async()=>{state.customer=await request(path('/customers/'+state.selected));},'Dados do cliente carregados.');return;}
      if(!action)return;const kind=action.dataset.inboxAction,id=action.dataset.id;
      if(['preview','copy'].includes(kind)){
        if(kind==='copy'){try{await navigator.clipboard.writeText(query('#inboxPreparedText').value);notice('Texto copiado.');}catch{query('#inboxPreparedText').select();notice('Selecione o texto e copie pelo seu dispositivo.',true);}return;}
        const id=query('#inboxPreparedReply').value,r=state.replies.find(x=>x.id===id);if(!r){notice('Selecione uma resposta.',true);return;}
        const sequence=++state.sequence;notice('Consultando os dados reais…');
        try{const result=await request(path('/quick-replies/'+id+'/preview'),'POST',{customer_id:state.selected,expected_version:r.version});if(current()&&sequence===state.sequence){const target=query('[data-inbox-preview]');if(!target)return;target.hidden=false;query('#inboxPreparedText').value=result.text;notice('Texto preparado. Nenhuma mensagem enviada.');}}
        catch(error){if(current()&&sequence===state.sequence)notice(error.message,true);}return;
      }
      if(!canWrite&& !['previous','next','cancel-editor','cancel-delete'].includes(kind))return;
      if(kind==='previous'||kind==='next'){state.offset=Math.max(0,state.offset+(kind==='next'?50:-50));void perform(reload);return;}
      if(kind==='cancel-editor'){state.editor=null;render();return;}
      if(kind==='cancel-delete'){state.confirmation=null;render();return;}
      if(kind==='new-label')state.editor={kind:'label',name:'',color:'#18836b'};
      if(kind==='edit-label')state.editor={...state.labels.find(x=>x.id===id),kind:'label'};
      if(kind==='new-reply')state.editor={kind:'reply',title:'',category:'Geral',text:'',visibility:'company'};
      if(kind==='edit-reply'||kind==='duplicate-reply'){const row=state.replies.find(x=>x.id===id);state.editor={...row,kind:'reply',...(kind==='duplicate-reply'?{id:null,title:(row.title+' (cópia)').slice(0,100)}:{})};}
      if(kind==='delete-label'||kind==='delete-reply'){const row=(kind==='delete-label'?state.labels:state.replies).find(x=>x.id===id);state.confirmation={id,name:row.name||row.title,collection:kind==='delete-label'?'/labels':'/quick-replies',version:row.version};}
      if(kind==='confirm-delete'){const c=state.confirmation;if(c)void perform(()=>request(path(c.collection+'/'+c.id),'DELETE',{expected_version:c.version}),'Registro excluído.');return;}
      render();if(state.editor)query('.os-inbox-editor input')?.focus();
    },{signal:state.controller.signal});
    host.innerHTML='<p role="status">Carregando ferramentas de atendimento…</p>';
    try{const capability=await request(path('/capabilities'));if(!current())return;state.central=capability.central_enabled===true;state.tools=capability.enabled===true;if(state.central)state.tab='conversations';if(!capability.enabled&&!state.central){host.innerHTML='<section class="os-card"><h2>Ferramentas em preparação</h2><p>As ferramentas serão disponibilizadas após a configuração e a validação desta instalação.</p></section>';return;}if(state.tools)await reload();else render();}
    catch(error){if(current())host.textContent=error.message||'Não foi possível carregar as ferramentas.';}
  }
  window.OrionInbox={mount,unmount};
})();
