/* Platform client directory. Uses the same DOM components/tokens as Orion Clientes.
   Account data comes only from the administrator API; no customer passwords.
*/
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const paths = {
    users:"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3.13a4 4 0 1 0 0 7.75",
    search:"m21 21-4.3-4.3",eye:"M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12",
    shield:"m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z",
    block:"m5 5 14 14",check:"m5 12 4 4L19 6",trash:"M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
    refresh:"M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5",
    chevron:"m9 5 7 7-7 7",left:"m15 5-7 7 7 7",clock:"M12 6v6l4 2",
    close:"m6 6 12 12M6 18 18 6",down:"m6 9 6 6 6-6",info:"M12 11v6M12 7h.01"
  };
  const circles = {
    users:'<circle cx="9" cy="7" r="4"/>',search:'<circle cx="10.5" cy="10.5" r="7.5"/>',
    eye:'<circle cx="12" cy="12" r="3"/>',clock:'<circle cx="12" cy="12" r="10"/>',
    info:'<circle cx="12" cy="12" r="10"/>'
  };
  const icon = name => '<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+(circles[name]||"")+'<path d="'+(paths[name]||paths.info)+'"/></svg>';
  const label = {active:"Ativa",suspended:"Suspensa",archived:"Arquivada",erased:"Excluída"};
  const planNames={essencial:'Essencial',profissional:'Profissional',elite:'Elite'},sourceNames={manual:'Manual',cakto:'Cakto',trial:'Teste grátis',none:'Sem acesso',platform_admin:'Administração'};
  const effective=a=>a.effective_plan||{};
  const subscriptionLabel=subscription=>({active:'Ativa',trialing:'Em teste',past_due:'Pagamento pendente',grace:'Em carência',suspended:'Suspensa',canceled:'Cancelada',expired:'Encerrada'})[subscription?.status]||(subscription?'Estado não confirmado':'Sem assinatura');
  const billingStatus=a=>subscriptionLabel(effective(a).commercial_subscription);
  const contactPhone=a=>{try{return a.phone?window.OrionFields?.phoneDisplay(a.phone,{allowInternational:true})||a.phone:'Telefone não informado';}catch{return 'Telefone não confirmado';}};
  const eventLabels={edit:"Nome atualizado",suspend:"Conta suspensa",reactivate:"Conta reativada",
    archive:"Exclusão solicitada",restore:"Conta restaurada",erase:"Exclusão definitiva",
    hold:"Retenção legal",release_hold:"Retenção encerrada",maintenance_start:"Manutenção iniciada",
    maintenance_end:"Manutenção encerrada",maintenance_request:"Ação em manutenção"};
  const names={suspend:"Suspender conta",reactivate:"Reativar conta",archive:"Excluir conta",
    restore:"Restaurar conta",erase_now:"Excluir permanentemente"};
  const warnings={suspend:"O cliente perderá o acesso imediatamente, inclusive nas sessões abertas.",
    reactivate:"O acesso à conta será restabelecido.",archive:"A conta ficará bloqueada durante 30 dias. Depois desse prazo, a identidade de acesso será eliminada, respeitando a retenção legal.",
    restore:"O prazo de exclusão será cancelado e o acesso restaurado.",
    hold:"A exclusão definitiva ficará pausada por obrigação legal.",
    erase_now:"Exclusão irreversível da conta de acesso agora, sem esperar 30 dias. O e-mail ficará disponível para um novo cadastro. Registros exigidos por lei poderão ser preservados."};
  const state={request:null,role:null,onMaintenanceStart:null,epoch:0,seq:0,
    query:"",status:"all",plan:'all',source:'all',subscriptionStatus:'all',page:1,items:[],stats:null,total:0,selected:null,planEnvelope:null,planDraft:null,planIntent:null,modalRead:0,
    owned:false,modalType:null,modalAction:null,trigger:null,returnTrigger:null,busy:false,timer:null};
  const isAdmin=()=>state.role==="admin"&&!$("page-member")?.classList.contains("hidden")&&!$("adminClientCenter")?.classList.contains("hidden");
  const btn=(title,key,opts={})=>'<button type="button" class="'+(opts.cls||"os-icon-button")+
    '" data-admin-control="'+esc(key)+'"'+(opts.id?' data-account-id="'+esc(opts.id)+'"':"")+
    (opts.action?' data-account-action="'+esc(opts.action)+'"':"")+
    ' aria-label="'+esc(title)+'" title="'+esc(title)+'">'+icon(opts.icon||"eye")+(opts.text?'<span>'+esc(opts.text)+'</span>':"")+'</button>';
  const initials=value=>String(value||"C").trim().split(/\s+/).slice(0,2).map(p=>[...p][0]||"").join("").toUpperCase();
  const when=value=>{const d=new Date(value);return value&&!Number.isNaN(d.getTime())?d.toLocaleDateString("pt-BR"):"—";};
  const statusBadge=a=>'<span class="os-badge '+(a.state==="active"?"os-success":a.state==="suspended"?"os-warning":a.state==="archived"?"os-danger":"os-neutral")+'">'+esc(label[a.state]||a.state)+'</span>';
  function isRecoverable(a){return a.state==="archived"&&a.purge_after&&Date.parse(a.purge_after)>Date.now();}
  function actions(a){
    let html=btn("Ver detalhes","view",{id:a.id,icon:"eye"});
    if(a.company_id&&a.can_manage_plan===true&&a.state!=='erased')html+=btn('Gerenciar plano','plan',{id:a.id,icon:'shield'});
    if(a.state==="active"&&!a.registration_pending)html+=btn("Acessar conta","maintenance",{id:a.id,icon:"shield"});
    if(a.state==="active")html+=btn("Suspender conta","account-action",{id:a.id,action:"suspend",icon:"block"});
    if(a.state==="suspended")html+=btn("Reativar conta","account-action",{id:a.id,action:"reactivate",icon:"check"});
    if(["active","suspended"].includes(a.state))html+=btn("Excluir (30 dias)","account-action",{id:a.id,action:"archive",icon:"trash",cls:"os-icon-button os-negative"});
    if(isRecoverable(a))html+=btn("Restaurar conta","account-action",{id:a.id,action:"restore",icon:"refresh"});
    return html;
  }
  function frame(){
    const el=$("adminClientCenter");
    el.innerHTML='<div class="os-title-row os-client-heading os-admin-heading"><div><h1>Administração de clientes</h1><p id="adminAccountsCount">Todas as contas cadastradas no Orion</p></div>'+
      '<div class="os-actions">'+btn("Atualizar lista","refresh",{cls:"os-button os-outline os-small",icon:"refresh",text:"Atualizar"})+'</div></div>'+
      '<div class="os-stats os-admin-stats" aria-label="Indicadores de clientes">'+
      [["total","Total"],["active","Ativas"],["suspended","Suspensas"],["archived","Arquivadas"]].map(([k,text])=>
        '<div class="os-stat"><strong data-admin-stat="'+k+'">—</strong><small>'+text+'</small></div>').join("")+'</div>'+
      '<div class="os-search os-client-search">'+icon("search")+
        '<input id="adminAccountsSearch" maxlength="120" type="search" aria-label="Buscar contas" placeholder="Buscar por nome, e-mail ou empresa..." value="'+esc(state.query)+'"></div>'+
      '<div class="os-client-filters os-admin-filters"><div class="os-pill-tabs" role="tablist" aria-label="Situação dos clientes">'+
      [["all","Todas"],["active","Ativas"],["suspended","Suspensas"],["archived","Arquivadas"],["erased","Excluídas"]].map(([k,text])=>
        '<button type="button" class="os-pill-tab" role="tab" data-admin-status="'+k+'" aria-selected="'+(state.status===k)+'">'+text+'</button>').join("")+
      '</div></div><div class="os-filter-row" aria-label="Filtros de plano">'+
      [['plan','Plano',[['all','Todos'],['none','Sem plano'],...Object.entries(planNames)]],['source','Origem',[['all','Todas'],['manual','Manual'],['cakto','Cakto'],['trial','Teste grátis'],['none','Sem acesso']]],['subscriptionStatus','Assinatura',[['all','Todas'],['active','Ativa'],['trialing','Em teste'],['past_due','Pagamento pendente'],['grace','Em carência'],['suspended','Suspensa'],['canceled','Cancelada'],['expired','Encerrada']]]].map(([key,title,options])=>'<div class="os-field"><label for="adminPlanFilter-'+key+'">'+title+'</label><select id="adminPlanFilter-'+key+'" data-admin-plan-filter="'+key+'">'+options.map(([value,text])=>'<option value="'+value+'">'+text+'</option>').join('')+'</select></div>').join('')+'</div><div id="adminAccountsResults" aria-live="polite">'+
      '<div class="os-loading" role="status">Consultando contas…</div></div>';
    updateStats();
  }
  function updateStats(){
    if(!state.stats)return;
    for(const key of ["total","active","suspended","archived"]){
      const n=document.querySelector("#adminClientCenter [data-admin-stat="+key+"]");
      if(n)n.textContent=String(Number(state.stats[key]||0));
    }
  }
  function renderRows(){
    const host=$("adminAccountsResults");if(!host)return;
    const list=state.items,total=state.total;
    $("adminAccountsCount").textContent=total+" conta"+(total===1?"":"s")+" encontrada"+(total===1?"":"s");
    if(!list.length){host.innerHTML='<div class="os-client-empty">'+
      (state.query?"Nenhuma conta corresponde a esta busca.":"Nenhuma conta encontrada neste filtro.")+
      '</div>';return;}
    const table='<div class="os-card os-no-pad os-hide-mobile"><div class="os-table-scroll"><table class="os-table"><thead>'+
      '<tr><th>Cliente</th><th>Empresa</th><th>Plano liberado</th><th>Situação</th><th>Cadastro</th><th><span class="os-sr-only">Ações</span></th></tr>'+
      '</thead><tbody>'+list.map(a=>'<tr><td><div class="os-person"><span class="os-avatar">'+esc(initials(a.name))+
      '</span><div><strong>'+esc(a.name)+'</strong><small>'+esc(a.email)+'</small><small>'+esc(contactPhone(a))+'</small></div></div></td>'+
      '<td>'+esc(a.company_name||"Sem empresa")+'</td><td><strong>'+esc(effective(a).name||planNames[effective(a).code]||'Sem plano ativo')+'</strong><br><small>'+esc(sourceNames[effective(a).source]||'Não informado')+(effective(a).valid_until?' · até '+when(effective(a).valid_until):'')+'</small><br><small>Assinatura: '+esc(billingStatus(a))+'</small></td><td>'+statusBadge(a)+'</td><td>'+when(a.created_at)+'<br><small>Último acesso: '+(a.last_access_at?when(a.last_access_at):'Não informado')+'</small></td>'+
      '<td><div class="os-table-actions">'+actions(a)+'</div></td></tr>').join("")+'</tbody></table></div></div>';
    const mobile='<p class="os-help os-only-mobile os-admin-swipe-tip">Toque para gerenciar. Deslize para a esquerda para revelar as ações.</p>'+
      '<div class="os-mobile-customers os-admin-mobile os-only-mobile">'+list.map(a=>
      '<div class="os-swipe-row"><div class="os-client-mobile-front"><button class="os-client-mobile-main" type="button" data-admin-control="view" data-account-id="'+esc(a.id)+'" aria-label="Ver conta de '+esc(a.name)+'">'+
      '<span class="os-avatar">'+esc(initials(a.name))+'</span><span><strong>'+esc(a.name)+'</strong><small>'+
      esc(a.company_name||"Sem empresa")+'</small><small>'+esc(a.email)+'</small><small>'+esc(contactPhone(a))+'</small><small>'+esc(effective(a).name||'Sem plano ativo')+' · '+esc(sourceNames[effective(a).source]||'Não informado')+'</small><small>Assinatura: '+esc(billingStatus(a))+'</small></span>'+icon("chevron")+
      '</button></div><div class="os-swipe-actions">'+actions(a)+'</div></div>').join("")+'</div>';
    const from=(state.page-1)*20;
    const pager=total>20?'<div class="os-pagination"><span>'+(from+1)+'–'+Math.min(from+list.length,total)+' de '+total+'</span>'+
      '<div class="os-actions">'+btn("Página anterior","previous",{cls:"os-button os-outline os-small",icon:"left",text:"Anterior"})+
      btn("Próxima página","next",{cls:"os-button os-outline os-small",icon:"chevron",text:"Próxima"})+'</div></div>':"";
    host.innerHTML=table+mobile+pager;
    const prev=host.querySelector('[data-admin-control="previous"]'),next=host.querySelector('[data-admin-control="next"]');
    if(prev)prev.disabled=state.page<=1;
    if(next)next.disabled=from+list.length>=total;
  }
  async function load(){
    if(!isAdmin())return;
    const epoch=state.epoch,seq=++state.seq;
    const params=new URLSearchParams({q:state.query,status:state.status,page:String(state.page),page_size:"20"});
    for(const [key,value] of [['plan',state.plan],['source',state.source],['subscription_status',state.subscriptionStatus]])if(value!=='all')params.set(key,value);
    try{
      const results=await Promise.all([
        state.request("/api/v1/admin/accounts?"+params),
        state.stats?Promise.resolve(state.stats):state.request("/api/v1/admin/accounts/stats")
      ]);
      if(epoch!==state.epoch||seq!==state.seq||!isAdmin())return;
      const [data,stats]=results;state.items=data.items||[];state.total=Number(data.total||0);state.stats=stats;
      updateStats();renderRows();
    }catch(error){
      if(epoch!==state.epoch||seq!==state.seq||!isAdmin())return;
      $("adminAccountsResults").innerHTML='<div class="os-notice os-error" role="alert">'+icon("info")+
        '<p>'+esc(error.message||"Falha ao consultar contas")+'</p></div>'+btn("Tentar novamente","refresh",{cls:"os-button os-outline os-small",icon:"refresh",text:"Tentar novamente"});
    }
  }
  function openDialog(type,title,html,record){
    const dialog=$("osDialog");if(!dialog)return;
    if(!state.owned||state.trigger?.closest('#adminClientCenter'))state.returnTrigger=state.trigger;
    state.owned=true;state.modalType=type;state.selected=record;state.busy=false;
    dialog.className="os-dialog os-customer-dialog os-admin-dialog";
    dialog.innerHTML='<button type="button" class="os-icon-button os-close" data-admin-control="close" aria-label="Fechar">'+icon("close")+'</button>'+
      '<h2 id="osDialogTitle">'+esc(title)+'</h2>'+html;
    window.OrionFields?.refresh(dialog);
    if(!dialog.open)dialog.showModal();
    dialog.querySelector('button:not(.os-close),input:not([type=checkbox]),textarea')?.focus({preventScroll:true});
  }
  function closeDialog(){
    if(state.busy)return;
    state.modalRead++;
    const dialog=$("osDialog");if(state.owned&&dialog?.open)dialog.close();
    state.owned=false;state.selected=null;state.modalType=null;state.modalAction=null;
    const trigger=state.returnTrigger||state.trigger;state.trigger=null;state.returnTrigger=null;
    if(trigger?.isConnected)trigger.focus({preventScroll:true});
  }
  function details(a,history){
    const summary=[["E-mail",a.email],["Empresa",a.company_name||"Sem empresa"],["Cadastrado em",when(a.created_at)],
      ['Telefone',a.phone||'Não informado'],['Último acesso',a.last_access_at?when(a.last_access_at):'Não informado'],['Plano liberado',effective(a).name||'Sem plano ativo'],['Origem',sourceNames[effective(a).source]||'Não informado'],['Vencimento do acesso',when(effective(a).valid_until)],
      ["E-mail confirmado",a.verified_at?"Sim":"Não"],
      ...(a.state==="archived"?[["Prazo de recuperação",when(a.purge_after)]]:[])];
    const canEdit=["active","suspended"].includes(a.state);
    const form=canEdit?'<form class="os-form os-admin-edit-form" data-admin-form="edit">'+
      '<div class="os-client-form"><div class="os-client-field"><label for="adminModalName">Nome do cliente</label>'+
      '<div class="os-client-input os-client-input-plain"><input id="adminModalName" type="text" name="name" required maxlength="120" value="'+esc(a.name)+'"></div></div>'+
      '<div class="os-client-field"><label for="adminModalReason">Motivo da alteração</label>'+
      '<div class="os-client-textarea"><textarea id="adminModalReason" name="reason" required minlength="5" maxlength="500" placeholder="Ex.: Correção solicitada pelo cliente"></textarea></div></div></div>'+
      '<div class="os-form-message" role="alert"></div><div class="os-form-actions"><button type="submit" class="os-button">'+icon("check")+"Salvar nome"+'</button></div></form>':"";
    const controls='<div class="os-admin-modal-controls">'+
      (a.company_id&&a.can_manage_plan===true?btn('Gerenciar plano','plan',{id:a.id,cls:'os-button os-outline',icon:'shield',text:'Gerenciar plano'}):'')+
      (a.state==="active"&&!a.registration_pending?btn("Acessar conta","maintenance",{id:a.id,cls:"os-button os-admin-access",icon:"shield",text:"Acessar conta"}):"")+
      (a.state==="active"?btn("Suspender","account-action",{id:a.id,action:"suspend",cls:"os-button os-outline",icon:"block",text:"Suspender"}):"")+
      (a.state==="suspended"?btn("Reativar","account-action",{id:a.id,action:"reactivate",cls:"os-button os-outline",icon:"check",text:"Reativar"}):"")+
      (["active","suspended"].includes(a.state)?btn("Excluir (30 dias)","account-action",{id:a.id,action:"archive",cls:"os-button os-outline os-negative",icon:"trash",text:"Excluir (30 dias)"}):"")+
      (isRecoverable(a)?btn("Restaurar","account-action",{id:a.id,action:"restore",cls:"os-button os-outline",icon:"refresh",text:"Restaurar"}):"")+
      (a.state==="archived"&&!a.legal_hold?btn("Excluir permanentemente","account-action",{id:a.id,action:"erase_now",cls:"os-button os-outline os-negative",icon:"trash",text:"Excluir permanentemente"}):"")+
      '</div>';
    const trace='<details class="os-client-history os-admin-history"><summary>'+
      '<span class="os-client-history-icon">'+icon("clock")+'</span><span class="os-client-history-title"><strong>Histórico de ações</strong>'+
      '<small>Alterações administrativas registradas</small></span>'+
      '<span class="os-client-history-count">'+history.length+'</span><span class="os-client-history-chevron">'+icon("down")+'</span></summary>'+
      '<div class="os-client-history-body">'+(history.length?history.map(item=>
      '<div class="os-admin-history-item"><strong>'+esc(eventLabels[item.action]||item.action)+'</strong>'+
      '<small>'+esc(new Date(item.occurred_at).toLocaleString("pt-BR"))+'</small><p>'+esc(item.reason||"Motivo não informado")+'</p></div>').join(""):'<p class="os-help">Nenhuma alteração registrada.</p>')+'</div></details>';
    return '<div class="os-admin-account-hero"><span class="os-avatar">'+esc(initials(a.name))+'</span><div><strong>'+esc(a.name)+'</strong><small>'+esc(a.email)+'</small></div>'+statusBadge(a)+'</div>'+
      '<div class="os-summary os-admin-summary">'+summary.map(([k,v])=>'<div class="os-summary-row"><span>'+esc(k)+'</span><strong>'+esc(v||"—")+'</strong></div>').join("")+'</div>'+
      (a.legal_hold?'<div class="os-notice os-warning">'+icon("shield")+'<p>Retenção legal ativa: a exclusão está pausada.</p></div>':"")+
      form+controls+trace;
  }
  async function view(id){
    const epoch=state.epoch;
    try{
      const result=await state.request("/api/v1/admin/accounts/"+encodeURIComponent(id));
      if(epoch!==state.epoch||!isAdmin()||!result?.account)return;
      openDialog("details","Conta do cliente",details(result.account,result.history||[]),result.account);
    }catch(e){if(epoch===state.epoch)feedback(e.message,true);}
  }
  async function showAction(id,action){
    if(!names[action])return;
    const epoch=state.epoch;
    try{
      const result=await state.request("/api/v1/admin/accounts/"+encodeURIComponent(id));
      if(epoch!==state.epoch||!isAdmin()||!result?.account)return;
      const a=result.account;
      openDialog("action",names[action],'<p class="os-help">'+esc(a.name)+' · '+esc(a.company_name||a.email)+'</p>'+
        '<div class="os-notice '+(["archive","erase_now"].includes(action)?"os-error":"os-warning")+'">'+icon("info")+'<p>'+esc(warnings[action])+'</p></div>'+
        '<form class="os-form" data-admin-form="action"><div class="os-field"><label for="adminModalReason">Motivo desta decisão</label>'+
        '<textarea id="adminModalReason" name="reason" rows="3" required minlength="5" maxlength="500" placeholder="Explique a finalidade da alteração"></textarea></div>'+
        (["archive","erase_now"].includes(action)?'<div class="os-field"><label for="adminModalConfirm">Digite '+(action==="erase_now"?"EXCLUIR PERMANENTEMENTE":"EXCLUIR")+' para confirmar</label><input id="adminModalConfirm" type="text" name="confirmation" required pattern="'+(action==="erase_now"?"EXCLUIR PERMANENTEMENTE":"EXCLUIR")+'" autocomplete="off" placeholder="'+(action==="erase_now"?"EXCLUIR PERMANENTEMENTE":"EXCLUIR")+'"></div>':"")+
        '<div class="os-form-message" role="alert"></div><div class="os-form-actions">'+
        btn("Cancelar","close",{cls:"os-button os-outline",icon:"close",text:"Cancelar"})+
        '<button type="submit" class="os-button'+(["archive","erase_now"].includes(action)?" os-danger":"")+'">'+icon("check")+esc(names[action])+'</button></div></form>',a);
      state.modalAction=action;
    }catch(e){if(epoch===state.epoch)feedback(e.message,true);}
  }
  async function showMaintenance(id){
    const epoch=state.epoch;
    try{
      const result=await state.request("/api/v1/admin/accounts/"+encodeURIComponent(id));
      if(epoch!==state.epoch||!isAdmin()||!result?.account)return;
      const a=result.account;
      if(a.state!=="active"||a.registration_pending){feedback("Esta conta não permite manutenção.",true);return;}
      openDialog("maintenance","Acesso de manutenção",'<div class="os-admin-account-hero"><span class="os-avatar">'+esc(initials(a.name))+
        '</span><div><strong>'+esc(a.name)+'</strong><small>'+esc(a.company_name||a.email)+'</small></div></div>'+
        '<div class="os-notice os-warning">'+icon("shield")+'<p>Você entrará no painel real deste cliente por até 30 minutos. Todas as alterações feitas durante esse acesso serão registradas como manutenção administrativa.</p></div>'+
        '<form class="os-form" data-admin-form="maintenance"><div class="os-field">'+
        '<label for="adminMaintenanceReason">Motivo do acesso</label><textarea id="adminMaintenanceReason" name="reason" required minlength="10" maxlength="500" rows="2">Manutenção interna solicitada pelo administrador</textarea></div>'+
        '<label class="os-check"><input type="checkbox" name="confirmation" value="ACESSAR" required>'+
        '<span>Confirmo que estou realizando manutenção nesta conta.</span></label>'+
        '<div class="os-form-message" role="alert"></div><div class="os-form-actions">'+
        btn("Cancelar","close",{cls:"os-button os-outline",icon:"close",text:"Cancelar"})+
        '<button type="submit" class="os-button os-admin-access">'+icon("shield")+'Acessar conta</button></div></form>',a);
    }catch(e){if(epoch===state.epoch)feedback(e.message,true);}
  }
  function planPath(a){return '/api/v1/admin/accounts/'+encodeURIComponent(a.id)+'/companies/'+encodeURIComponent(a.company_id)+'/plan';}
  function validPlanEnvelope(value,a){return value?.company?.id===a.company_id&&value.effective_plan&&Number.isInteger(value.effective_plan.version)&&/^[a-f0-9]{64}$/.test(value.effective_plan.revision);}
  function planSummary(value){
    const p=value.effective_plan,c=value.company,subscription=p.commercial_subscription;
    return '<div class="os-summary">'+[['Empresa',c.name],['Plano liberado',p.name||'Sem plano ativo'],['Origem',sourceNames[p.source]||p.source],['Situação do acesso',p.active?'Ativo':'Sem acesso ativo'],['Assinatura comercial',subscription?.name||subscription?.plan_code||'Sem assinatura'],['Situação da assinatura',subscriptionLabel(subscription)],['Profissionais ativos',String(p.limits?.active_professionals??c.active_professionals??0)+' / '+String(p.limits?.max_professionals??'—')]].map(([key,text])=>'<div class="os-summary-row"><span>'+esc(key)+'</span><strong>'+esc(text||'—')+'</strong></div>').join('')+'</div>';
  }
  function renderPlanForm(a,value,draft){
    const assignment=value.effective_plan.manual_assignment;
    const expiry=assignment?.expires_at?new Date(assignment.expires_at):null;
    const expiryInput=expiry&&!Number.isNaN(expiry.getTime())?new Date(expiry.getTime()-expiry.getTimezoneOffset()*60000).toISOString().slice(0,16):'';
    draft=draft||{plan_code:assignment?.plan_code||value.effective_plan.code||'essencial',access_type:assignment?.expires_at?'temporary':'indefinite',expires_at:expiryInput,reason:''};
    const history=value.history||[];
    openDialog('plan','Gerenciar plano',planSummary(value)+'<form class="os-form" data-admin-form="plan">'+
      '<div class="os-field"><label for="adminManualPlan">Plano</label><select id="adminManualPlan" name="plan_code" required>'+Object.entries(planNames).map(([code,name])=>'<option value="'+code+'"'+(code===draft.plan_code?' selected':'')+'>'+name+'</option>').join('')+'</select></div>'+
      '<div class="os-field"><label for="adminManualAccess">Tipo de liberação</label><select id="adminManualAccess" name="access_type"><option value="indefinite"'+(draft.access_type==='indefinite'?' selected':'')+'>Acesso manual sem prazo definido</option><option value="temporary"'+(draft.access_type==='temporary'?' selected':'')+'>Acesso manual temporário</option></select></div>'+
      '<div class="os-field" data-admin-expiration '+(draft.access_type==='temporary'?'':'hidden')+'><label for="adminManualExpires">Expiração (data e hora no seu fuso)</label><input id="adminManualExpires" type="datetime-local" name="expires_at" value="'+esc(draft.expires_at)+'" '+(draft.access_type==='temporary'?'required':'disabled')+'></div>'+
      '<div class="os-field"><label for="adminManualReason">Motivo (opcional)</label><textarea id="adminManualReason" name="reason" rows="2" maxlength="500">'+esc(draft.reason)+'</textarea></div>'+
      '<p class="os-help">Esta liberação não gera cobrança e não altera a assinatura comercial.</p><div class="os-form-message" role="alert"></div><div class="os-form-actions">'+btn('Cancelar','close',{cls:'os-button os-outline',text:'Cancelar',icon:'close'})+'<button type="submit" class="os-button">Salvar alteração</button></div></form>'+
      (assignment?'<div class="os-form-actions">'+btn('Remover atribuição manual','remove-plan',{cls:'os-button os-outline',text:'Remover atribuição manual',icon:'refresh'})+'</div>':'')+
      '<details class="os-more-fields"><summary>Histórico de planos</summary><div class="os-disclosure-body">'+(history.length?history.map(event=>'<div class="os-summary-row"><span>'+esc(event.action==='manual_remove'?'Atribuição removida':'Plano alterado')+' · '+esc(new Date(event.occurred_at).toLocaleString('pt-BR'))+'</span><strong>'+esc(event.previous_plan?.name||'Sem acesso')+' → '+esc(event.new_plan?.name||'Sem acesso')+'</strong></div><p class="os-help">'+esc(event.reason||'Sem motivo informado')+' · Administrador: '+esc(event.actor_name||'Administrador geral')+(event.expires_at?' · até '+esc(new Date(event.expires_at).toLocaleString('pt-BR')):'')+'</p>').join(''):'<p class="os-help">Nenhuma alteração manual registrada.</p>')+'</div></details>',a);
    state.planEnvelope=value;state.planDraft=draft;state.planIntent=null;
  }
  async function showPlan(id){
    const a=state.items.find(item=>item.id===id)||state.selected;
    if(!a?.company_id||a.can_manage_plan!==true||a.id!==id)return;
    const epoch=state.epoch,read=++state.modalRead;
    try{const value=await state.request(planPath(a));if(epoch!==state.epoch||read!==state.modalRead||!isAdmin())return;if(!validPlanEnvelope(value,a))throw Error('Não foi possível confirmar o plano da empresa selecionada.');renderPlanForm(a,value);}
    catch(error){if(epoch===state.epoch&&read===state.modalRead)feedback(error.message,true);}
  }
  function reviewPlan(remove=false){
    const a=state.selected,value=state.planEnvelope;if(!a||!validPlanEnvelope(value,a))return;
    const form=$('osDialog').querySelector('[data-admin-form=plan]');
    if(!remove&&!form?.reportValidity())return;
    const draft=form?Object.fromEntries(new FormData(form)):state.planDraft;
    let expires=null;
    if(!remove&&draft.access_type==='temporary'){
      const date=new Date(draft.expires_at);if(!Number.isFinite(date.getTime())||date.getTime()<=Date.now()){feedback('Escolha uma data de expiração futura.',true);return;}expires=date.toISOString();
    }
    state.planDraft=draft;
    const payload={expected_version:value.effective_plan.version,expected_revision:value.effective_plan.revision,operation_id:crypto.randomUUID(),confirmation:remove?'REMOVER ATRIBUIÇÃO':'ALTERAR PLANO',reason:(draft.reason||'').trim(),...(!remove?{plan_code:draft.plan_code,access_type:draft.access_type,expires_at:expires}:{})};
    openDialog('plan-review',remove?'Remover atribuição manual?':'Confirmar alteração de plano',planSummary(value)+'<div class="os-notice"><p>'+esc(remove?'O plano será recalculado pela assinatura comercial ou pelo teste grátis válido.':'A empresa receberá o plano '+planNames[draft.plan_code]+(expires?' até '+new Date(expires).toLocaleString('pt-BR'): ' sem prazo definido')+'.')+'</p></div><p class="os-help">Os dados da empresa e a assinatura comercial serão preservados. Nenhuma cobrança será gerada.</p><form class="os-form" data-admin-form="plan-confirm"><div class="os-form-message" role="alert"></div><div class="os-form-actions">'+btn('Voltar','plan-back',{cls:'os-button os-outline',icon:'left',text:'Voltar'})+'<button type="submit" class="os-button">'+(remove?'Confirmar remoção':'Confirmar alteração')+'</button></div></form>',a);
    state.planIntent={path:planPath(a),method:remove?'DELETE':'PUT',payload};
  }
  async function savePlan(form){
    const intent=state.planIntent,a=state.selected;if(!intent||state.busy)return;
    const epoch=state.epoch;state.busy=true;form.setAttribute('aria-busy','true');form.querySelectorAll('button').forEach(button=>button.disabled=true);
    try{
      const value=await state.request(intent.path,intent.method,intent.payload);
      if(epoch!==state.epoch||!isAdmin())return;if(!validPlanEnvelope(value,a))throw Error('Resultado ainda não confirmado. Confira o estado antes de repetir.');
      state.busy=false;closeDialog();state.stats=null;await load();window.dispatchEvent(new Event('orion:plan-changed'));if(typeof window.BroadcastChannel==='function'){try{const channel=new BroadcastChannel('orion-plan-access');channel.postMessage({type:'changed',tenantId:a.company_id});channel.close();}catch{}}
    }catch(error){
      if(epoch!==state.epoch||!isAdmin())return;state.busy=false;form.setAttribute('aria-busy','false');feedback(error.message||'Não foi possível salvar a alteração.',true);
      const status=Number(error.status);if(status&&status<500&&status!==409){form.querySelectorAll('button').forEach(button=>button.disabled=false);}
      else{state.planIntent=null;form.querySelector('.os-form-actions').insertAdjacentHTML('beforeend',btn('Conferir estado','plan',{id:a.id,cls:'os-button os-outline',icon:'refresh',text:'Conferir estado'}));}
    }
  }
  function feedback(message,error=false){
    const dialog=state.owned?$("osDialog"):null;
    const zone=dialog?.querySelector(".os-form-message");
    if(zone){zone.textContent=message;zone.setAttribute("role","alert");return;}
    const el=$("adminAccountsCount");if(el)el.textContent=message;
    if(error)console.warn("Orion admin center:",message);
  }
  async function submit(event){
    const form=event.target.closest("[data-admin-form]");
    if(!form||!state.owned||!state.selected||!isAdmin())return;
    event.preventDefault();
    if(state.busy||!form.reportValidity())return;
    if(form.dataset.adminForm==='plan'){reviewPlan();return;}
    if(form.dataset.adminForm==='plan-confirm'){await savePlan(form);return;}
    const a=state.selected,kind=form.dataset.adminForm,epoch=state.epoch;
    const values=Object.fromEntries(new FormData(form));
    let path,method,payload;
    if(kind==="edit"){
      path="/api/v1/admin/accounts/"+encodeURIComponent(a.id);
      method="PUT";payload={name:values.name.trim(),reason:values.reason.trim(),expected_version:a.auth_version};
    }else if(kind==="action"){
      path="/api/v1/admin/accounts/"+encodeURIComponent(a.id)+"/"+state.modalAction;
      method="POST";payload={reason:values.reason.trim(),expected_version:a.auth_version,
        ...(["archive","erase_now"].includes(state.modalAction)?{confirmation:values.confirmation}:{})};
    }else if(kind==="maintenance"){
      if(values.confirmation!=="ACESSAR")return;
      path="/api/v1/admin/accounts/"+encodeURIComponent(a.id)+"/maintenance";
      method="POST";payload={reason:values.reason.trim(),expected_version:a.auth_version,confirmation:"ACESSAR"};
    }else return;
    state.busy=true;form.setAttribute("aria-busy","true");
    form.querySelectorAll("button,input,textarea").forEach(node=>node.disabled=true);
    try{
      await state.request(path,method,payload);
      if(epoch!==state.epoch)return;
      if(kind==="maintenance"){
        state.onMaintenanceStart?.();
        return;
      }
      state.busy=false;closeDialog();state.stats=null;await load();
    }catch(e){
      if(epoch===state.epoch){
        state.busy=false;form.setAttribute("aria-busy","false");
        form.querySelectorAll("button,input,textarea").forEach(node=>node.disabled=false);
        feedback(e.message,true);
      }
    }
  }
  async function control(event){
    const button=event.target.closest("[data-admin-control]");
    if(!button||!isAdmin()||state.busy)return;
    const key=button.dataset.adminControl,id=button.dataset.accountId;
    event.preventDefault();state.trigger=button;
    if(key==="close"){closeDialog();return;}
    if(key==="view"){await view(id);return;}
    if(key==='plan'){await showPlan(id);return;}
    if(key==='remove-plan'){reviewPlan(true);return;}
    if(key==='plan-back'){renderPlanForm(state.selected,state.planEnvelope,state.planDraft);return;}
    if(key==="maintenance"){await showMaintenance(id);return;}
    if(key==="account-action"){await showAction(id,button.dataset.accountAction);return;}
    if(key==="refresh"){state.stats=null;await load();return;}
    if(key==="previous"&&state.page>1){state.page--;await load();return;}
    if(key==="next"&&state.page*20<state.total){state.page++;await load();return;}
  }
  function statusClick(event){
    const button=event.target.closest("[data-admin-status]");
    if(!button||!isAdmin())return;
    if(!["all","active","suspended","archived","erased"].includes(button.dataset.adminStatus))return;
    state.status=button.dataset.adminStatus;state.page=1;
    $("adminClientCenter").querySelectorAll("[data-admin-status]").forEach(tab=>
      tab.setAttribute("aria-selected",String(tab===button)));
    void load();
  }
  function open({request,role,onMaintenanceStart}){
    if(role!=="admin"||typeof request!=="function")return;
    state.request=request;state.role=role;state.onMaintenanceStart=onMaintenanceStart;
    state.epoch++;state.seq++;state.modalRead++;state.page=1;state.query="";state.status="all";state.plan='all';state.source='all';state.subscriptionStatus='all';state.stats=null;
    const dialog=$("osDialog");
    if(dialog&&!dialog.dataset.osAdminCloseBound){
      dialog.dataset.osAdminCloseBound="1";
      dialog.addEventListener('cancel',event=>{if(!state.owned)return;event.preventDefault();event.stopImmediatePropagation();if(!state.busy)closeDialog();},true);
      dialog.addEventListener("close",()=>{
        if(!state.owned)return;
        state.owned=false;state.busy=false;state.selected=null;
        state.modalType=null;state.modalAction=null;
      });
    }
    frame();void load();
  }
  function reset(){
    state.epoch++;state.seq++;state.modalRead++;state.planEnvelope=null;state.planDraft=null;state.planIntent=null;clearTimeout(state.timer);state.timer=null;
    if(state.owned){state.busy=false;closeDialog();}
    state.request=null;state.role=null;state.selected=null;state.items=[];state.stats=null;
  }
  document.addEventListener("click",event=>{
    if(event.target.closest("#adminClientCenter")||(state.owned&&event.target.closest("#osDialog")))
      void control(event);
    if(event.target.closest("#adminClientCenter"))statusClick(event);
  });
  document.addEventListener("submit",event=>{if(state.owned&&event.target.closest("#osDialog"))void submit(event);});
  document.addEventListener("input",event=>{
    const input=event.target;if(!isAdmin()||input.id!=="adminAccountsSearch")return;
    state.query=input.value;state.page=1;state.seq++;clearTimeout(state.timer);
    state.timer=setTimeout(()=>void load(),280);
  });
  document.addEventListener('change',event=>{
    if(!isAdmin())return;const el=event.target;
    if(el.matches('[data-admin-plan-filter]')){state[el.dataset.adminPlanFilter]=el.value;state.page=1;void load();}
    if(state.owned&&el.id==='adminManualAccess'){const area=$('osDialog').querySelector('[data-admin-expiration]'),input=area.querySelector('input'),temporary=el.value==='temporary';area.hidden=!temporary;input.disabled=!temporary;input.required=temporary;}
  });
  document.addEventListener("keydown",event=>{
    const tab=event.target.closest("[data-admin-status]");
    if(!tab||!["ArrowLeft","ArrowRight","Home","End"].includes(event.key))return;
    const all=[...$("adminClientCenter").querySelectorAll("[data-admin-status]")];
    const current=all.indexOf(tab);
    const next=event.key==="Home"?0:event.key==="End"?all.length-1:(current+(event.key==="ArrowRight"?1:-1)+all.length)%all.length;
    event.preventDefault();all[next].focus();all[next].click();
  });
  window.OrionAdminCenter=Object.freeze({open,reset});
})();
