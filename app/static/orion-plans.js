/* Orion / Plano: standalone scoped commercial view. No external UI framework. */
(function(){
'use strict';
const escapeHTML = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const iconPaths={check:'m5 12 4 4L19 6',close:'M6 6l12 12M18 6 6 18',arrow:'M5 12h14m-6-6 6 6-6 6',clock:'M12 6v6l4 2',shield:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z',sparkles:'M12 3l2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6z',users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M18 3a4 4 0 0 1 0 8',credit:'M3 7h18v13H3zM3 11h18M7 16h4',info:'M12 11v6m0-11h.01',calendar:'M8 2v4m8-4v4M3 10h18M5 5h14v16H5z',chevron:'m6 9 6 6 6-6'};
const ic=name=>'<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="'+iconPaths[name]+'"/></svg>';
const price=cents=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(cents/100);
let timezone='America/Sao_Paulo';
const localDate=date=>{if(!date)return '—';try{return new Intl.DateTimeFormat('pt-BR',{dateStyle:'medium',timeZone:timezone}).format(new Date(date));}catch{return '—';}};
const defs=[
 {code:'essencial',name:'Orion Essencial',intro:'Tudo o que você precisa para organizar e gerenciar seu negócio.',professionals:2,features:[
 'Dashboard com indicadores essenciais','Agenda e agendamentos online','Clientes e histórico de atendimentos',
 'Serviços, combos e adicionais','Site de agendamento personalizado','Financeiro básico: receitas e despesas',
 'Central de conversas e respostas manuais','Confirmações e lembretes pelo WhatsApp*','Convites para avaliação no Google*','Suporte padrão']},
 {code:'profissional',name:'Orion Profissional',intro:'Mais ferramentas para fidelizar clientes e acelerar o crescimento da sua empresa.',professionals:6,features:[
 'Tudo do Essencial','Financeiro avançado, DRE e relatórios','Comissões, vales e pagamentos de funcionários',
 'Metas e análises por profissional','Fidelização, pontos e recompensas','Cupons e benefícios personalizados',
 'Clientes inativos e ações de retorno','Campanhas de aniversário e recuperação*',
 'Marketing IA para campanhas e mensagens*','Consultor IA de gestão*']},
 {code:'elite',name:'Orion Elite',intro:'Automatize seu atendimento com uma recepcionista inteligente no WhatsApp da sua empresa.',professionals:10,features:[
 'Tudo do Profissional','Recepcionista IA no WhatsApp da empresa*','Respostas sobre serviços, preços e horários',
 'Consulta de horários disponíveis*','Agendar, reagendar e cancelar via conversa*',
 'Atendimento fora do horário comercial*','Nome e estilo personalizados da assistente',
 'Assumir, pausar ou retomar atendimento*','Transferência para atendimento humano*',
 'Relatórios da recepcionista IA*','Configuração assistida e suporte prioritário']}
];
const comparisons=[
 ['Gestão e agenda',[['Dashboard','Essencial','Avançado','Avançado'],['Agenda e agendamento online',true,true,true],['Horários, intervalos e folgas',true,true,true]]],
 ['Clientes e serviços',[['Cadastro e histórico de clientes',true,true,true],['Serviços, combos e adicionais',true,true,true],['Identificação de clientes inativos',false,true,true]]],
 ['Site de agendamento',[['Página própria personalizada',true,true,true],['Agendamento público',true,true,true]]],
 ['Financeiro e relatórios',[['Receitas, despesas e saldo','Básico','Avançado','Avançado'],['DRE e relatórios detalhados',false,true,true],['Comissões, vales e pagamentos',false,true,true]]],
 ['Central de atendimento',[['Conversas e respostas manuais',true,true,true],['Atendimento e agendamento pela recepcionista IA',false,false,true]]],
 ['Automações e lembretes',[['Confirmação e lembretes WhatsApp*',true,true,true],['Convite para avaliação no Google*',true,true,true],['Agendamento automático por conversa*',false,false,true]]],
 ['Marketing e fidelização',[['Pontos e recompensas',false,true,true],['Cupons e benefícios',false,true,true],['Campanhas de aniversário e retorno*',false,true,true]]],
 ['Inteligência artificial',[['Marketing IA e Consultor IA de gestão*',false,true,true],['Recepcionista IA no WhatsApp*',false,false,true]]],
 ['Equipe e profissionais',[['Profissionais permitidos','Até 2','Até 6','Até 10'],['Relatórios por profissional',false,true,true]]],
 ['Suporte',[['Suporte padrão',true,true,true],['Suporte prioritário e configuração assistida',false,false,true]]]
];
function compareCell(value){return value===true?'<span class="op-yes" aria-label="Incluído">'+ic('check')+'<span class="op-cell-word">Incluído</span></span>':value===false?'<span class="op-no" aria-label="Não incluído">—</span>':'<span class="op-comparison-value">'+escapeHTML(value)+'</span>';}
function canonical(code){const s=String(code||'').toLowerCase();return s.includes('essencial')?'essencial':s.includes('profissional')||s==='pro'||s.includes('_pro')?'profissional':s.includes('elite')?'elite':'';}
function statusOf(sub){if(!sub)return 'Sem assinatura';return ({active:'Ativa',trialing:'Em teste',trial:'Em teste',past_due:'Pagamento pendente',grace:'Em carência',suspended:'Suspensa',canceled:'Cancelada',cancelled:'Cancelada',expired:'Encerrada'})[sub.status]||'Estado não confirmado';}
let mounted=null;
function renderCard(def, server, currentCode, currentActive, allowed, ctx) {
 const chosen=server.find(x=>x.code===def.code)||{};
 const same=currentActive&&currentCode===def.code;
 const badge=def.code==='profissional'?'<span class="op-recommended">Recomendado</span>':'';
 let actionLabel='Assinar '+(def.code==='profissional'?'Profissional':def.code==='essencial'?'Essencial':'Elite');
 let disabled='';
 if(same){actionLabel='Plano atual';disabled='disabled';}
 else if(currentActive){actionLabel='Regularização ou troca pelo suporte';disabled='disabled';}
 else if(!allowed){actionLabel='Somente proprietário';disabled='disabled';}
 else if(!Number.isInteger(chosen.monthly_cents)){actionLabel='Plano indisponível';disabled='disabled';}
 const features=def.features.map(feature=>'<li>'+ic('check')+'<span>'+escapeHTML(feature)+'</span></li>').join('');
 return '<article class="op-plan op-'+def.code+(same?' op-current':'')+'" aria-label="'+escapeHTML(def.name)+'">'
   +'<header class="op-plan-head"><div class="op-plan-heading"><h3>'+escapeHTML(def.name)+'</h3>'+badge+'</div><p>'+escapeHTML(def.intro)+'</p></header>'
   +'<div class="op-price"><strong>'+(Number.isInteger(chosen.monthly_cents)?price(chosen.monthly_cents):'Preço indisponível')+'</strong><span>/mês</span></div>'
   +'<p class="op-per-company">por empresa · até '+def.professionals+' profissionais</p>'
   +'<div class="op-plan-divider"></div><ul class="op-plan-features">'+features+'</ul>'
   +'<div class="op-plan-bottom"><button class="op-btn '+(def.code==='profissional'?'op-btn-primary':'op-btn-secondary')+'" type="button" data-op-checkout="'+def.code+'" '+disabled+'>'+escapeHTML(actionLabel)+'</button>'
   +(same?'<span class="op-plan-caption">Sua assinatura verificada</span>':'<span class="op-plan-caption">Pagamento pela Cakto. Confirmação após validação.</span>')+'</div></article>';
}
function hasPlanDifference(feature){
 return feature[1]!==feature[2]||feature[2]!==feature[3];
}
function renderComparison(plans){
 const tiers=[
  {code:'essencial',title:'Essencial',short:'Essencial'},
  {code:'profissional',title:'Profissional',short:'Pro'},
  {code:'elite',title:'Elite',short:'Elite'}
 ];
 const tierHead=tiers.map((tier,i)=>{
  const found=plans.find(plan=>plan.code===tier.code);
  const display=Number.isInteger(found?.monthly_cents)?price(found.monthly_cents):'—';
  return '<th class="op-matrix-tier '+(i===1?'op-matrix-popular':'')+'" scope="col" aria-label="Orion '+tier.title+'">'
   +'<span class="op-matrix-tier-name"><span class="op-matrix-full">'+tier.title+'</span><span class="op-matrix-short">'+tier.short+'</span></span>'
   +'<span class="op-matrix-tier-price">'+escapeHTML(display)+'</span></th>';
 }).join('');
 const sections=comparisons.map(([category,features],groupIndex)=>{
  const header='<tr class="op-matrix-category"><th scope="rowgroup" colspan="4"><span class="op-matrix-category-label">'+escapeHTML(category)+'</span></th></tr>';
  const body=features.map(feature=>{
   const [name,essencial,profissional,elite]=feature;
   return '<tr class="op-matrix-row" data-op-different="'+String(hasPlanDifference(feature))+'"><th scope="row">'+escapeHTML(name)+'</th>'
    +[essencial,profissional,elite].map((value,i)=>'<td'+(i===1?' class="op-matrix-popular"':'')+'>'+compareCell(value)+'</td>').join('')+'</tr>';
  }).join('');
  return '<tbody data-op-category="'+groupIndex+'">'+header+body+'</tbody>';
 }).join('');
 const mobileSections=comparisons.map(([category,features],groupIndex)=>{
  const groupId='opMobileCategory'+groupIndex;
  const items=features.map(feature=>{
   const [name,...values]=feature;
   const cells=tiers.map((tier,i)=>'<div class="op-mobile-value'+(tier.code==='profissional'?' op-mobile-value-pro':'')+'" role="listitem">'
    +'<span class="op-mobile-tier">'+escapeHTML(tier.short)+'</span>'+compareCell(values[i])+'</div>').join('');
   return '<div class="op-mobile-feature" data-op-different="'+String(hasPlanDifference(feature))+'">'
    +'<div class="op-mobile-feature-label">'+escapeHTML(name)+'</div>'
    +'<div class="op-mobile-values" role="list" aria-label="Comparação de '+escapeHTML(name)+'">'+cells+'</div></div>';
  }).join('');
  return '<section class="op-mobile-group" data-op-mobile-category="'+groupIndex+'" aria-labelledby="'+groupId+'">'
   +'<h3 id="'+groupId+'">'+escapeHTML(category)+'</h3><div class="op-mobile-features">'+items+'</div></section>';
 }).join('');
 return '<section class="op-compare" aria-labelledby="opCompareTitle">'
  +'<div class="op-compare-header"><div class="op-section-title"><h2 id="opCompareTitle">Compare os planos</h2>'
  +'<p>Veja o que muda entre Essencial, Pro e Elite.</p></div>'
  +'<label class="op-compare-switch"><input type="checkbox" data-op-only-differences checked>'
  +'<span>Mostrar apenas diferenças</span></label></div>'
  +'<p class="op-compare-basics">Todos os planos incluem agenda online, cadastro de clientes, serviços e página de agendamento.</p>'
  +'<p class="op-visually-hidden" role="status" aria-live="polite" data-op-compare-state></p>'
  +'<div class="op-matrix-frame"><table class="op-matrix" aria-label="Comparação de funcionalidades dos três planos do Orion">'
  +'<colgroup><col class="op-matrix-feature-col"><col class="op-matrix-tier-col"><col class="op-matrix-tier-col"><col class="op-matrix-tier-col"></colgroup>'
  +'<thead><tr><th scope="col">Funcionalidades</th>'+tierHead+'</tr></thead>'
  +sections+'</table></div>'
  +'<div class="op-mobile-comparison" aria-label="Comparação dos recursos por plano">'+mobileSections+'</div></section>';
}
function applyComparisonFilter(root,onlyDifferences){
 const toggle=root.querySelector('[data-op-only-differences]');
 if(toggle)toggle.checked=onlyDifferences;
 root.querySelectorAll('[data-op-category],[data-op-mobile-category]').forEach(group=>{
  const rows=[...group.querySelectorAll('[data-op-different]')];
  rows.forEach(row=>{row.hidden=onlyDifferences&&row.dataset.opDifferent!=='true';});
  group.hidden=!rows.some(row=>!row.hidden);
 });
 const state=root.querySelector('[data-op-compare-state]');
 if(state)state.textContent=onlyDifferences?'Exibindo somente diferenças entre os planos.':'Exibindo todos os recursos dos planos.';
}

function subscriptionBanner(data){
 const sub=data.subscription,trial=data.trial;
 if(sub){
   const status=statusOf(sub);
   const details=[
     '<span><small>Mensalidade</small><strong>'+price(sub.monthly_cents||0)+'</strong></span>',
     '<span><small>Profissionais</small><strong>'+(data.active_professionals||0)+' de '+(sub.max_professionals||'—')+'</strong></span>',
   ];
   if(sub.next_renewal_at)details.push('<span><small>Próxima cobrança</small><strong>'+escapeHTML(localDate(sub.next_renewal_at))+'</strong></span>');
   if(sub.trial_ends_at&&['trial','trialing'].includes(sub.status))details.push('<span><small>Seu teste termina</small><strong>'+escapeHTML(localDate(sub.trial_ends_at))+'</strong></span>');
   return '<section class="op-account-state" aria-label="Assinatura da empresa"><div><span class="op-eyebrow">SUA ASSINATURA</span><h2>'+escapeHTML(sub.name||'Plano Orion')+'</h2><p>Estado confirmado pelo servidor: <strong>'+escapeHTML(status)+'</strong></p></div><div class="op-current-metrics">'+details.join('')+'</div></section>';
 }
 if(trial&&trial.status==='active'&&trial.ends_at)return '<section class="op-account-state" role="status"><div><span class="op-eyebrow">TESTE EM ANDAMENTO</span><h2>Seu teste grátis termina em '+escapeHTML(localDate(trial.ends_at))+'</h2><p>Sem cobrança automática ao final. O acesso às funcionalidades depende das configurações habilitadas.</p></div></section>';
 if(trial&&trial.status==='pending')return '<section class="op-account-state" role="status"><div><span class="op-eyebrow">TESTE SOLICITADO</span><h2>Solicitação em análise</h2><p>Nossa equipe entrará em contato pelo WhatsApp informado. O prazo de 3 dias ainda não começou.</p></div></section>';
 if(trial&&trial.status==='expired')return '<section class="op-account-state" role="status"><div><span class="op-eyebrow">TESTE ENCERRADO</span><h2>Seu período de avaliação terminou</h2><p>Escolha um plano para continuar. Nenhuma cobrança foi feita automaticamente.</p></div></section>';
 return '';
}
function renderTrial(data,allowed){
 const trial=data.trial,sub=data.subscription;
 const eligible=allowed&&!sub&&!trial;
 const label=trial?.status==='pending'?'Solicitação enviada':trial?.status==='active'?'Teste em andamento':trial?.status==='expired'?'Teste encerrado':sub?'Assinatura vinculada':!allowed?'Disponível ao proprietário':'Testar grátis por 3 dias';
 return '<section class="op-free-trial" aria-labelledby="opTrialTitle"><div class="op-trial-icon">'+ic('sparkles')+'</div><div class="op-trial-copy"><span class="op-eyebrow">EXPERIMENTE NA PRÁTICA</span><h2 id="opTrialTitle">Experimente o Orion grátis por 3 dias</h2><p>Conheça o Orion na prática! Cadastre seus dados e nossa equipe entrará em contato pelo WhatsApp para ajudar na configuração da sua empresa.</p><small>Inclui agenda, clientes, serviços, site e financeiro básico. Conexões e automações dependem de configuração.</small></div><button type="button" class="op-btn op-btn-primary" data-op-trial '+(eligible?'':'disabled')+'>'+escapeHTML(label)+'</button></section>';
}
function renderTrialDialog(root,data,ctx){
 const name=ctx.user?.name||'',business=ctx.tenant?.name||'',contact=ctx.tenant?.contact_phone||ctx.user?.phone||'';
 const dialog=document.createElement('dialog');
 dialog.className='os-dialog op-dialog';
 dialog.setAttribute('aria-labelledby','opTrialModalTitle');
 dialog.innerHTML='<button class="op-dialog-close" type="button" data-op-close aria-label="Fechar">'+ic('close')+'</button>'
 +'<div class="op-dialog-symbol">'+ic('calendar')+'</div><h2 id="opTrialModalTitle">Solicitar teste grátis</h2>'
 +'<p class="op-dialog-description">Preencha os dados para nossa equipe ajudar na configuração inicial.</p>'
 +'<form class="op-trial-form" novalidate>'
 +'<div class="os-field"><label for="opFullName">Nome completo</label><input id="opFullName" type="text" name="full_name" value="'+escapeHTML(name)+'" autocomplete="name" required minlength="3" maxlength="120"></div>'
 +'<div class="os-field"><label for="opCompanyName">Nome da empresa</label><input id="opCompanyName" type="text" name="company_name" value="'+escapeHTML(business)+'" required minlength="2" maxlength="160"></div>'
 +'<div class="os-field"><label for="opWhatsapp">WhatsApp para contato</label><input id="opWhatsapp" type="tel" name="whatsapp" data-orion-field="phone" inputmode="tel" autocomplete="tel-national" placeholder="(00) 00000-0000" value="'+escapeHTML(window.OrionFields?.phoneDisplay(contact)||contact)+'" required maxlength="15"></div>'
 +'<p class="op-info-line">'+ic('info')+'Nossa equipe entrará em contato pelo WhatsApp para ajudar você a configurar o Orion.</p>'
 +'<label class="op-consent"><input type="checkbox" name="privacy_ack" required><span>Autorizo o contato pelo WhatsApp informado, exclusivamente para tratar do teste e da configuração do Orion. <a href="#opPrivacyDetails" data-op-privacy>Política de Privacidade</a>.</span></label>'
 +'<details id="opPrivacyDetails" class="op-privacy"><summary>Como usamos seus dados</summary><p>Os dados deste pedido ficam vinculados à conta e à empresa, acessíveis somente à equipe autorizada do Orion para analisar seu teste e entrar em contato. Não são enviados automaticamente ao WhatsApp nem usados para cobranças. Você pode solicitar esclarecimentos ou atualização à equipe responsável pelo Orion.</p></details>'
 +'<p class="op-modal-error" role="alert" aria-live="polite"></p>'
 +'<div class="op-modal-actions"><button class="op-btn op-btn-secondary" type="button" data-op-close>Cancelar</button><button class="op-btn op-btn-primary" type="submit">Solicitar teste grátis</button></div>'
 +'</form>';
 root.appendChild(dialog);
 const close=()=>{if(!dialog.open)return;dialog.close();dialog.remove();root.querySelector('[data-op-trial]')?.focus();};
 dialog.addEventListener('click',ev=>{
   if(ev.target.closest('[data-op-close]')){ev.preventDefault();close();}
   if(ev.target.closest('[data-op-privacy]')){ev.preventDefault();const detail=dialog.querySelector('#opPrivacyDetails');detail.open=true;detail.scrollIntoView({block:'nearest'});}
 });
 dialog.addEventListener('cancel',ev=>{if(dialog.dataset.busy==='true'){ev.preventDefault();return;}dialog.remove();});
 dialog.querySelector('[name=whatsapp]').addEventListener('input',event=>{
   const value=event.target.value;
   try{event.target.value=window.OrionFields?.phoneDisplay(value)||value;}catch{}
 });
 dialog.querySelector('form').addEventListener('submit',async ev=>{
   ev.preventDefault();
   const form=ev.currentTarget,feedback=dialog.querySelector('.op-modal-error');
   feedback.textContent='';
   if(!form.reportValidity())return;
   let phone;
   try{phone=window.OrionFields?.phoneValue(form.elements.whatsapp.value,{required:true});}
   catch(ex){feedback.textContent=ex.message||'Informe seu WhatsApp com DDD.';return;}
   if(!/^\+55\d{11}$/.test(String(phone||''))){feedback.textContent='Informe um celular brasileiro com DDD e 11 dígitos.';return;}
   const payload={full_name:form.elements.full_name.value.trim(),company_name:form.elements.company_name.value.trim(),whatsapp:phone,privacy_ack:form.elements.privacy_ack.checked};
   dialog.dataset.busy='true';
   form.querySelectorAll('button,input').forEach(el=>el.disabled=true);
   try{
     const result=await ctx.request('/plan-trial','POST',payload);
     if(root!==mounted?.root)return;
     close();
     const refreshed=await ctx.request('/plan-overview');
     if(root===mounted?.root){render(root,refreshed,ctx);const notification=root.querySelector('#opPlanFeedback');if(notification){notification.textContent=result.message||'Solicitação recebida. Nossa equipe entrará em contato pelo WhatsApp.';notification.hidden=false;}}
   }catch(ex){
     feedback.textContent=ex.message||'Não foi possível enviar a solicitação. Tente novamente.';
   }finally{
     dialog.dataset.busy='false';form.querySelectorAll('button,input').forEach(el=>el.disabled=false);
   }
 });
 dialog.showModal();
 dialog.querySelector('#opFullName')?.focus({preventScroll:true});
}
async function checkout(root,code,ctx){
 const action=root.querySelector('[data-op-checkout="'+code+'"]'),message=root.querySelector('#opPlanFeedback');
 if(!action||action.disabled||root.dataset.busy==='true')return;
 root.dataset.busy='true';action.disabled=true;message.hidden=false;message.textContent='Conferindo a oferta e os dados da assinatura…';
 try{
  const result=await ctx.request('/plan-checkout','POST',{plan_code:code});
  if(root!==mounted?.root)return;
  const url=new URL(result.checkout_url);
  if(url.protocol!=='https:'||url.hostname!=='pay.cakto.com.br'||result.plan_code!==code||result.status!=='pending_payment')throw Error('Checkout não verificado. Nenhuma contratação foi confirmada.');
  message.textContent='Abrindo checkout seguro da Cakto. A assinatura será ativada somente após o pagamento confirmado.';
  window.location.assign(url.href);
 }catch(error){if(root===mounted?.root)message.textContent=error.message||'A contratação ainda não está disponível para este plano.';}
 finally{root.dataset.busy='false';if(action.isConnected)action.disabled=false;}
}

function render(root,data,ctx){
 if(!root.isConnected)return;
 const plans=Array.isArray(data.plans)?data.plans:[],sub=data.subscription,
 currentCode=canonical(sub?.code),currentActive=!!sub;
 const canManage=!!data.can_manage_plan&&!!ctx.canManage;
 root.innerHTML='<div class="op-page"><header class="op-heading"><span class="op-eyebrow">PLANOS ORION</span><h2>Escolha o plano ideal para sua empresa</h2><p>Tudo o que você precisa para organizar seus agendamentos, gerenciar seu negócio e oferecer uma experiência melhor aos seus clientes.</p></header>'
 +subscriptionBanner(data)
 +'<div id="opPlanFeedback" class="op-feedback" role="status" aria-live="polite" hidden></div>'
 +'<section aria-label="Assinaturas mensais"><div class="op-plans-grid">'+defs.map(def=>renderCard(def,plans,currentCode,currentActive,canManage,ctx)).join('')+'</div>'
 +'<p class="op-footnote">*Recursos de mensagens, automações e IA exigem configuração, canal conectado e disponibilidade da funcionalidade. O Marketing IA e o Consultor IA não realizam agendamentos pelo WhatsApp; essa recepcionista é exclusiva do Elite.</p></section>'
 +renderTrial(data,canManage)
 +renderComparison(plans)
 +'</div>';
 applyComparisonFilter(root,root.dataset.opCompareOnly!=='false');
}
function mount(root,data,ctx){
 if(!root||!ctx?.request)return;
 unmount();
 mounted={root,ctx};
 timezone=ctx.tenant?.timezone||'America/Sao_Paulo';
 render(root,data,ctx);
 root.addEventListener('click',root._orionPlansClick=async event=>{
  const trial=event.target.closest('[data-op-trial]'),checkoutButton=event.target.closest('[data-op-checkout]');
  if(trial&&!trial.disabled){event.preventDefault();renderTrialDialog(root,data,ctx);}
  if(checkoutButton&&!checkoutButton.disabled){event.preventDefault();await checkout(root,checkoutButton.dataset.opCheckout,ctx);}
 });
 root.addEventListener('change',root._orionPlansChange=event=>{
  if(!event.target.matches('[data-op-only-differences]'))return;
  const only=event.target.checked;
  root.dataset.opCompareOnly=String(only);
  applyComparisonFilter(root,only);
 });
}
function unmount(){
 if(mounted?.root?._orionPlansClick)mounted.root.removeEventListener('click',mounted.root._orionPlansClick);
 if(mounted?.root)delete mounted.root._orionPlansClick;
 if(mounted?.root?._orionPlansChange)mounted.root.removeEventListener('change',mounted.root._orionPlansChange);
 if(mounted?.root)delete mounted.root._orionPlansChange;
 mounted=null;
}
window.OrionPlans=Object.freeze({mount,unmount});
})();