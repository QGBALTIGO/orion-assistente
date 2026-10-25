/* CRM tools use the existing authenticated request/membership bridge. No sends. */
(function(){
  'use strict';
  let active=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const button=(label,action,extra='')=>'<button type="button" class="os-button os-outline" data-inbox-action="'+action+'" '+extra+'>'+esc(label)+'</button>';
  function unmount(){if(active){window.OrionConversations?.unmount();window.OrionFlows?.unmount();active.controller.abort();active.alive=false;active.host.inert=false;active.host.removeAttribute('aria-busy');active=null;}}
  async function mount({host,request,tenantId,canWrite=false}){
    unmount();if(!host)return;
    const state={host,request,tenantId,canWrite,alive:true,controller:new AbortController(),labels:[],replies:[],customers:[],customer:null,
      selected:'',editor:null,tab:'customers',crmView:'customers',replyQuery:'',query:'',label:'',offset:0,hasMore:false,busy:false,confirmation:null,sequence:0,central:false,tools:true,crmLoaded:false};
    active=state;
    const current=()=>active===state&&state.alive&&host.isConnected;
    const path=value=>'/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/inbox'+value;
    const query=selector=>host.querySelector(selector);
    const initials=value=>String(value||'?').trim().split(/\s+/).slice(0,2).map(item=>Array.from(item)[0]||'').join('').toLocaleUpperCase('pt-BR');
    const readableDate=(value,zone)=>{try{const date=new Date(value);if(!Number.isFinite(date.getTime()))return 'Data indisponível';return new Intl.DateTimeFormat('pt-BR',{timeZone:zone||'America/Sao_Paulo',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}).format(date);}catch{return 'Data indisponível';}};
    const appointmentStatus={confirmed:'Confirmado',cancelled:'Cancelado',completed:'Concluído',no_show:'Não compareceu',hold:'Reserva temporária',expired:'Reserva expirada'};
    const notice=(value,error=false)=>{const area=query('[data-inbox-notice]');if(area){area.textContent=value;area.setAttribute('role',error?'alert':'status');}};
    function render(){
      if(!current())return;
      state.sequence++;
      window.OrionConversations?.unmount();
      window.OrionFlows?.unmount();
      const disabled=state.busy?'disabled':'',write=canWrite&&!state.busy?'':'disabled',e=state.editor;
      const tabButton=(id,label)=>'<button type="button" class="os-inbox-nav-tab" role="tab" aria-selected="'+(state.tab===id)+'" data-inbox-tab="'+id+'" '+disabled+'>'+label+'</button>';
      const header='<nav class="os-inbox-topbar os-inbox-primary-nav" role="tablist" aria-label="Seções da Central de Atendimento">'+
        (state.central?tabButton('conversations','Conversas'):'')+
        (state.tools?tabButton('customers','Clientes')+tabButton('replies','Respostas rápidas')+tabButton('flows','Simulações'):'')+
        '</nav>';
      const confirmation=state.confirmation?'<div class="os-inbox-delete-confirm" role="group" aria-label="Confirmar exclusão" tabindex="-1">'+
        '<div><strong>Excluir '+esc(state.confirmation.name)+'?</strong><p>'+
        (state.confirmation.collection==='/labels'?'A etiqueta será retirada dos clientes associados.':'O modelo será removido da biblioteca.')+
        '</p></div><div class="os-actions">'+button('Cancelar','cancel-delete',disabled)+
        button('Excluir definitivamente','confirm-delete',write)+'</div></div>':'';
      const people=state.customers.length?state.customers.map(c=>'<button type="button" class="os-inbox-person" data-inbox-customer="'+esc(c.id)+'" '+disabled+' aria-pressed="'+(state.selected===c.id)+'">'+
        '<span class="os-inbox-person-avatar" aria-hidden="true">'+esc(initials(c.name||c.phone))+'</span>'+
        '<span class="os-inbox-person-copy"><strong>'+esc(c.name)+'</strong><small>'+esc(c.phone||'Sem telefone')+'</small></span>'+
        '<span class="os-inbox-person-arrow" aria-hidden="true">›</span></button>').join(''):
        '<div class="os-inbox-empty"><strong>Nenhum cliente encontrado</strong><p>Tente outro nome, telefone ou etiqueta.</p></div>';
      const subnav='<nav class="os-inbox-subnav" role="group" aria-label="Ferramentas de clientes">'+
        '<button type="button" data-inbox-view="customers" aria-pressed="'+(state.crmView==='customers')+'">Clientes cadastrados</button>'+
        '<button type="button" data-inbox-view="labels" aria-pressed="'+(state.crmView==='labels')+'">Etiquetas</button></nav>';
      const contacts='<div class="os-inbox-grid'+(state.selected?' os-inbox-show-customer':'')+'">'+
        '<section class="os-card os-inbox-contacts" aria-label="Lista de clientes"><div class="os-inbox-heading"><div><h2>Clientes cadastrados</h2><p>Pesquise os contatos da empresa.</p></div></div>'+
        '<form class="os-inbox-form" data-inbox-form="search"><div class="os-inbox-search-row">'+
        '<label class="os-inbox-search-input" for="inboxQuery"><span>Nome ou telefone</span><input id="inboxQuery" name="q" type="search" maxlength="100" placeholder="Buscar cliente" value="'+esc(state.query)+'"></label>'+
        '<label class="os-inbox-search-filter" for="inboxFilter"><span>Etiqueta</span><select id="inboxFilter" name="label"><option value="">Todas</option>'+state.labels.map(l=>'<option value="'+esc(l.id)+'" '+(state.label===l.id?'selected':'')+'>'+esc(l.name)+'</option>').join('')+'</select></label></div>'+
        '<button class="os-button os-inbox-search-submit" '+disabled+'>Buscar clientes</button></form>'+
        '<div class="os-inbox-list os-inbox-people-list">'+people+'</div>'+
        '<div class="os-inbox-pagination">'+button('Anterior','previous',state.offset>0?disabled:'disabled')+button('Próxima página','next',state.hasMore?disabled:'disabled')+'</div></section>'+
        '<section class="os-card os-inbox-context" aria-label="Ficha de cliente"><h2 class="os-inbox-context-heading">Informações do cliente</h2>'+customerHTML()+'</section></div>';
      const labelsPanel='<section class="os-card os-inbox-labels-panel"><div class="os-inbox-heading"><div><h2>Etiquetas da empresa</h2>'+
        '<p>Organize seus clientes sem modificar consentimentos de marketing.</p></div>'+
        button('Nova etiqueta','new-label',write)+'</div>'+
        (state.confirmation?.collection==='/labels'?confirmation:'')+
        (e?.kind==='label'?labelForm(e):'')+
        '<div class="os-inbox-tags os-inbox-label-list">'+(state.labels.length?state.labels.map(l=>'<article class="os-inbox-tag"><span class="os-inbox-swatch" style="background-color:'+esc(/^#[0-9a-f]{6}$/i.test(l.color)?l.color:'#2e7bf6')+'"></span><strong>'+esc(l.name)+'</strong>'+
        '<span class="os-inbox-tag-actions">'+button('Editar','edit-label','data-id="'+esc(l.id)+'" '+write)+button('Excluir','delete-label','data-id="'+esc(l.id)+'" '+write)+'</span></article>').join(''):
        '<div class="os-inbox-empty"><strong>Nenhuma etiqueta cadastrada</strong><p>Use “Nova etiqueta” para organizar os contatos.</p></div>')+'</div></section>';
      const customers=subnav+(state.crmView==='labels'?labelsPanel:contacts);
      const visibleReplies=state.replies.filter(r=>(r.title+' '+r.category+' '+r.text).toLocaleLowerCase('pt-BR').includes(state.replyQuery.toLocaleLowerCase('pt-BR')));
      const replies='<section class="os-card os-inbox-replies-panel"><div class="os-inbox-heading"><div><h2>Respostas rápidas</h2>'+
        '<p>Crie modelos e revise o texto antes de enviar em uma conversa.</p></div>'+
        button('Nova resposta','new-reply',write)+'</div>'+
        (state.confirmation?.collection==='/quick-replies'?confirmation:'')+
        (e?.kind==='reply'?replyForm(e):'')+
        '<label for="inboxReplySearch">Buscar respostas</label><input id="inboxReplySearch" type="search" placeholder="Título, categoria ou texto" value="'+esc(state.replyQuery)+'">'+
        '<div class="os-inbox-list os-inbox-replies-list" data-inbox-replies>'+repliesHTML(visibleReplies,write)+'</div></section>';
      const panel=state.tab==='conversations'?'<div data-inbox-central></div>':state.tab==='flows'?'<div data-inbox-flows></div>':state.tab==='customers'?customers:replies;
      host.innerHTML='<div class="os-inbox">'+header+'<p data-inbox-notice class="os-inbox-notice" role="status" aria-live="polite"></p>'+panel+'</div>';
      if(state.tab==='flows')void window.OrionFlows?.mount({host:query('[data-inbox-flows]'),request,tenantId,canWrite});
      if(state.tab==='conversations')void window.OrionConversations?.mount({host:query('[data-inbox-central]'),request,tenantId,canWrite,toolsEnabled:state.tools,onCustomer:id=>{
        if(window.OrionConversations?.hasUnsentDraft?.()&&!window.confirm('Há uma mensagem não enviada. Sair da conversa e descartar o rascunho?'))return;
        state.selected=id;state.tab='customers';void perform(reload);
      }});
    }
    function customerHTML(){
      const data=state.customer;
      if(!data)return '<div class="os-inbox-empty os-inbox-select-prompt"><strong>Selecione um cliente</strong><p>Consulte os dados e o histórico sem sair desta área.</p></div>';
      const c=data.customer||{},selected=new Set((data.labels||[]).map(x=>x.id)),history=data.history||[],next=data.upcoming;
      const heading='<header class="os-inbox-customer-header">'+
        button('Voltar aos clientes','customer-back','aria-label="Voltar à lista de clientes"')+
        '<span class="os-inbox-person-avatar" aria-hidden="true">'+esc(initials(c.name||c.whatsapp_e164))+'</span>'+
        '<div><h3>'+esc(c.name||'Cliente')+'</h3><p>'+esc(c.whatsapp_e164||'Sem telefone')+'</p></div></header>';
      const upcoming='<section class="os-inbox-upcoming"><div class="os-inbox-section-title"><h3>Próximo agendamento</h3></div>'+
        (next?'<p><strong>'+esc(readableDate(next.start_at,data.timezone))+'</strong></p>'+
          '<p>'+esc(next.services||'Serviço não informado')+' · '+esc(next.professional||'Profissional a confirmar')+'</p>'+
          '<small>Protocolo '+esc(next.protocol||'—')+'</small>':
          '<p>Nenhum agendamento futuro confirmado.</p>')+'</section>';
      const labels='<section class="os-inbox-customer-labels"><div class="os-inbox-section-title"><h3>Etiquetas</h3>'+
        '<small>'+selected.size+' selecionada'+(selected.size===1?'':'s')+'</small></div>'+
        (state.labels.length?'<div class="os-inbox-tags">'+state.labels.map(l=>
          '<label class="os-inbox-check"><input type="checkbox" data-inbox-label="'+esc(l.id)+'" '+
          (selected.has(l.id)?'checked ':'')+(!canWrite||state.busy?'disabled':'')+'><span>'+esc(l.name)+'</span></label>').join('')+'</div>':
          '<p>Nenhuma etiqueta cadastrada. Organize-as na seção Etiquetas.</p>')+'</section>';
      const historyHTML='<details class="os-inbox-appointments"><summary>'+
        '<span><strong>Histórico de agendamentos</strong><small>'+history.length+' registro'+(history.length===1?'':'s')+
          (data.history_limit?' mais recentes':'')+'</small></span><span class="os-inbox-disclosure-arrow" aria-hidden="true">⌄</span></summary>'+
        '<div class="os-inbox-appointments-body">'+(history.length?
          '<ol class="os-inbox-appointments-list">'+history.map(a=>'<li><div><strong>'+esc(a.services||'Serviço não informado')+'</strong>'+
          '<small>'+esc(readableDate(a.start_at,data.timezone))+' · '+esc(a.professional||'')+'</small></div>'+
          '<span class="os-inbox-appointment-status os-inbox-appointment-status-'+esc(['confirmed','cancelled','completed','no_show'].includes(a.status)?a.status:'other')+'">'+
          esc(appointmentStatus[a.status]||'Em conferência')+'</span></li>').join('')+'</ol>':
          '<p>Este cliente ainda não tem agendamentos registrados.</p>')+
        '</div></details>';
      const notes=c.notes?'<details class="os-inbox-appointments os-inbox-notes"><summary><strong>Observações do cliente</strong>'+
        '<span class="os-inbox-disclosure-arrow" aria-hidden="true">⌄</span></summary><p class="os-inbox-text">'+esc(c.notes)+'</p></details>':'';
      const prepared='<section class="os-inbox-prepare"><div class="os-inbox-section-title"><h3>Preparar resposta rápida</h3></div>'+
        (state.replies.length?'<label for="inboxPreparedReply">Modelo</label><div class="os-inbox-prepare-row">'+
          '<select id="inboxPreparedReply"><option value="">Selecione uma resposta</option>'+
          state.replies.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.category+' · '+r.title)+'</option>').join('')+'</select>'+
          button('Preparar prévia','preview')+'</div>':
          '<p>Cadastre um modelo na aba Respostas rápidas para usá-lo aqui.</p>')+
        '<div data-inbox-preview class="os-inbox-preview" hidden><label for="inboxPreparedText">Texto preparado para revisão</label>'+
          '<textarea id="inboxPreparedText" readonly rows="5"></textarea>'+button('Copiar texto','copy')+
          '<p>O Orion apenas prepara o texto; nenhuma mensagem é enviada.</p></div></section>';
      return '<div class="os-inbox-customer-profile">'+heading+upcoming+labels+historyHTML+notes+prepared+'</div>';
    }
    function repliesHTML(items,write){return items.length?items.map(r=>'<article class="os-inbox-reply"><div><strong>'+esc(r.title)+'</strong><small>'+esc(r.category)+' · '+(r.visibility==='personal'?'Pessoal':'Compartilhada')+' · versão '+esc(r.version)+'</small></div><p class="os-inbox-text">'+esc(r.text)+'</p><div class="os-actions">'+button('Editar','edit-reply','data-id="'+esc(r.id)+'" '+write)+button('Duplicar','duplicate-reply','data-id="'+esc(r.id)+'" '+write)+button('Excluir','delete-reply','data-id="'+esc(r.id)+'" '+write)+'</div></article>').join(''):'<p>Nenhuma resposta cadastrada.</p>';}
    function labelForm(e){return '<form data-inbox-form="label" class="os-inbox-editor"><h3>'+ (e.id?'Editar etiqueta':'Nova etiqueta')+'</h3><label for="inboxLabelName">Nome</label><input id="inboxLabelName" name="name" maxlength="60" required value="'+esc(e.name)+'"><label for="inboxColor">Cor</label><input id="inboxColor" name="color" type="color" value="'+esc(e.color)+'"><div class="os-actions"><button class="os-button" '+(state.busy?'disabled':'')+'>Salvar etiqueta</button>'+button('Cancelar','cancel-editor',state.busy?'disabled':'')+'</div></form>';}
    function replyForm(e){return '<form data-inbox-form="reply" class="os-inbox-editor"><h3>'+ (e.id?'Editar resposta':'Nova resposta')+'</h3><label for="inboxReplyTitle">Título</label><input id="inboxReplyTitle" name="title" maxlength="100" required value="'+esc(e.title)+'"><label for="inboxCategory">Categoria</label><input id="inboxCategory" name="category" maxlength="60" required value="'+esc(e.category)+'"><label for="inboxVisibility">Visibilidade</label><select id="inboxVisibility" name="visibility"><option value="company" '+(e.visibility==='company'?'selected':'')+'>Compartilhada com a empresa</option><option value="personal" '+(e.visibility==='personal'?'selected':'')+'>Somente minha conta</option></select><label for="inboxReplyText">Texto</label><textarea id="inboxReplyText" name="text" maxlength="1200" rows="5" required>'+esc(e.text)+'</textarea><p class="os-help">Variáveis: {{cliente.nome}}, {{cliente.primeiro_nome}}, {{empresa.nome}}, {{agendamento.data}}, {{agendamento.horario}}, {{agendamento.protocolo}}, {{profissional.nome}}, {{servico.nome}}, {{servico.valor}}. Dados ausentes impedem o preparo.</p><div class="os-actions"><button class="os-button" '+(state.busy?'disabled':'')+'>Salvar resposta</button>'+button('Cancelar','cancel-editor',state.busy?'disabled':'')+'</div></form>';}
    async function reload(){
      const [tags,replies,people]=await Promise.all([request(path('/labels')),request(path('/quick-replies')),request(path('/customers?q='+encodeURIComponent(state.query)+'&offset='+state.offset+(state.label?'&label_id='+encodeURIComponent(state.label):'')))]);
      if(!current())return;state.labels=tags.items;state.replies=replies.items;state.customers=people.items;state.hasMore=people.has_more;
      if(state.selected){
        try{state.customer=await request(path('/customers/'+encodeURIComponent(state.selected)));}
        catch(error){
          if(!current())return;
          if(error?.status===404||error?.status_code===404){state.selected='';state.customer=null;}
          else throw error;
        }
      }
      if(current())state.crmLoaded=true;
    }
    async function perform(work,success){
      if(state.busy)return;
      state.busy=true;host.setAttribute('aria-busy','true');host.inert=true;
      try{
        await work();
        if(!current())return;
        if(work!==reload)await reload();
        if(!current())return;
        state.confirmation=null;
        state.busy=false;render();
        if(success)notice(success);
      }catch(error){
        if(current())notice(error?.message||'Não foi possível concluir. Seus dados foram preservados.',true);
      }finally{
        state.busy=false;
        if(current()){host.removeAttribute('aria-busy');host.inert=false;}
      }
    }
    host.addEventListener('submit',event=>{
      const form=event.target.closest('[data-inbox-form]');if(!form)return;event.preventDefault();if(state.busy)return;
      const values=Object.fromEntries(new FormData(form));
      if(form.dataset.inboxForm==='search'){state.query=values.q;state.label=values.label;state.offset=0;void perform(reload,'Pesquisa atualizada.');return;}
      if(!canWrite)return;
      const e=state.editor,kind=form.dataset.inboxForm,collection=kind==='label'?'/labels':'/quick-replies';
      void perform(async()=>{await request(path(collection+(e.id?'/'+e.id:'')),e.id?'PUT':'POST',{...values,expected_version:e.id?e.version:0});state.editor=null;},'Salvo com sucesso.');
    },{signal:state.controller.signal});
    host.addEventListener('input',event=>{
      if(event.target.id==='inboxReplySearch'){
        state.replyQuery=event.target.value;
        const target=query('[data-inbox-replies]');
        if(target)target.innerHTML=repliesHTML(state.replies.filter(r=>
          (r.title+' '+r.category+' '+r.text).toLocaleLowerCase('pt-BR').includes(state.replyQuery.toLocaleLowerCase('pt-BR'))),
          canWrite&&!state.busy?'':'disabled');
      }
    },{signal:state.controller.signal});
    host.addEventListener('change',event=>{
      const id=event.target.dataset.inboxLabel;if(!id||!state.selected||!canWrite||state.busy)return;
      const checked=event.target.checked;
      void perform(()=>request(path('/customers/'+state.selected+'/labels/'+id),checked?'PUT':'DELETE'),'Etiquetas atualizadas.');
    },{signal:state.controller.signal});
    host.addEventListener('click',async event=>{
      if(state.busy)return;
      const tab=event.target.closest('[data-inbox-tab]'),person=event.target.closest('[data-inbox-customer]'),action=event.target.closest('[data-inbox-action]');
      if(tab){
        if(state.tab===tab.dataset.inboxTab)return;
        if(state.editor&&!window.confirm('Há alterações de formulário não salvas. Deseja descartá-las?'))return;
        if(state.tab==='conversations'&&window.OrionConversations?.hasUnsentDraft?.()&&!window.confirm('Há uma mensagem não enviada. Sair sem salvar o rascunho?'))return;
        state.editor=null;state.confirmation=null;state.tab=tab.dataset.inboxTab;
        if(state.tools&&!state.crmLoaded&&state.tab!=='conversations')void perform(reload);
        else render();
        return;
      }
      const view=event.target.closest('[data-inbox-view]');
      if(view){
        if(state.crmView===view.dataset.inboxView)return;
        if(state.editor&&!window.confirm('Descartar as alterações de formulário ainda não salvas?'))return;
        state.crmView=view.dataset.inboxView;state.editor=null;state.confirmation=null;render();return;
      }
      if(person){
        const selected=state.customers.find(item=>item.id===person.dataset.inboxCustomer);
        if(!selected)return;
        state.selected=selected.id;state.customer=null;
        void perform(reload,'Dados do cliente carregados.');
        return;
      }
      if(!action)return;const kind=action.dataset.inboxAction,id=action.dataset.id;
      if(['preview','copy'].includes(kind)){
        if(kind==='copy'){try{await navigator.clipboard.writeText(query('#inboxPreparedText').value);notice('Texto copiado.');}catch{query('#inboxPreparedText').select();notice('Selecione o texto e copie pelo seu dispositivo.',true);}return;}
        const id=query('#inboxPreparedReply').value,r=state.replies.find(x=>x.id===id);if(!r){notice('Selecione uma resposta.',true);return;}
        const sequence=++state.sequence;notice('Consultando os dados reais…');
        try{const result=await request(path('/quick-replies/'+id+'/preview'),'POST',{customer_id:state.selected,expected_version:r.version});if(current()&&sequence===state.sequence){const target=query('[data-inbox-preview]');if(!target)return;target.hidden=false;query('#inboxPreparedText').value=result.text;notice('Texto preparado. Nenhuma mensagem enviada.');}}
        catch(error){if(current()&&sequence===state.sequence)notice(error.message,true);}return;
      }
      if(kind==='customer-back'){state.selected='';state.customer=null;render();return;}
      if(!canWrite&&!['previous','next','cancel-editor','cancel-delete'].includes(kind))return;
      if(kind==='previous'||kind==='next'){state.offset=Math.max(0,state.offset+(kind==='next'?50:-50));void perform(reload);return;}
      if(kind==='cancel-editor'){state.editor=null;render();return;}
      if(kind==='cancel-delete'){state.confirmation=null;render();return;}
      if(kind==='new-label')state.editor={kind:'label',name:'',color:'#18836b'};
      if(kind==='edit-label'){const row=state.labels.find(x=>x.id===id);if(!row)return;state.editor={...row,kind:'label'};}
      if(kind==='new-reply')state.editor={kind:'reply',title:'',category:'Geral',text:'',visibility:'company'};
      if(kind==='edit-reply'||kind==='duplicate-reply'){
        const row=state.replies.find(x=>x.id===id);if(!row)return;
        state.editor={...row,kind:'reply',...(kind==='duplicate-reply'?{id:null,title:(row.title+' (cópia)').slice(0,100)}:{})};
      }
      if(kind==='delete-label'||kind==='delete-reply'){
        const row=(kind==='delete-label'?state.labels:state.replies).find(x=>x.id===id);
        if(!row)return;
        state.confirmation={id,name:row.name||row.title,collection:kind==='delete-label'?'/labels':'/quick-replies',version:row.version};
      }
      if(kind==='confirm-delete'){const c=state.confirmation;if(c)void perform(()=>request(path(c.collection+'/'+c.id),'DELETE',{expected_version:c.version}),'Registro excluído.');return;}
      render();
      if(state.editor){const el=query('.os-inbox-editor input');el?.focus();el?.scrollIntoView?.({block:'nearest'});}
      if(state.confirmation){const el=query('.os-inbox-delete-confirm');el?.focus();el?.scrollIntoView?.({block:'nearest'});}
    },{signal:state.controller.signal});
    host.innerHTML='<p role="status">Carregando ferramentas de atendimento…</p>';
    try{const capability=await request(path('/capabilities'));if(!current())return;state.central=capability.central_enabled===true;state.tools=capability.enabled===true;if(state.central)state.tab='conversations';if(!capability.enabled&&!state.central){host.innerHTML='<section class="os-card"><h2>Ferramentas em preparação</h2><p>As ferramentas serão disponibilizadas após a configuração e a validação desta instalação.</p></section>';return;}if(state.tools&&state.tab!=='conversations')await reload();render();}
    catch(error){if(current())host.textContent=error.message||'Não foi possível carregar as ferramentas.';}
  }
  window.OrionInbox={mount,unmount};
})();
