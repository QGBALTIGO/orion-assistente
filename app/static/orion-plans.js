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
 {code:'essencial',name:'Orion Essencial',intro:'Tudo o que você precisa para organizar e gerenciar seu negócio.',features:[
 'Dashboard com indicadores essenciais','Agenda e agendamentos online','Clientes e histórico de atendimentos',
 'Serviços, combos e adicionais','Site de agendamento personalizado','Financeiro básico: receitas e despesas',
 'Meu desempenho: atendimentos e faturamento','Confirmações e lembretes pelo WhatsApp*','Convites para avaliação no Google*','Suporte padrão']},
 {code:'profissional',name:'Orion Profissional',intro:'Mais ferramentas para fidelizar clientes e acelerar o crescimento da sua empresa.',features:[
 'Tudo do Essencial','Central de atendimento e WhatsApp da empresa','Financeiro avançado, DRE e relatórios','Comissões, vales e pagamentos de funcionários',
 'Metas e análises por profissional','Fidelização, pontos e recompensas','Cupons e benefícios personalizados',
 'Clientes inativos e ações de retorno','Campanhas de aniversário e recuperação*',
 'Marketing IA para campanhas e mensagens*','Consultor IA de gestão*']},
 {code:'elite',name:'Orion Elite',intro:'Automatize seu atendimento com uma recepcionista inteligente no WhatsApp da sua empresa.',features:[
 'Tudo do Profissional','Recepcionista IA no WhatsApp da empresa*','Respostas sobre serviços, preços e horários',
 'Consulta de horários disponíveis*','Agendar, reagendar e cancelar via conversa*',
 'Atendimento fora do horário comercial*','Nome e estilo personalizados da assistente',
 'Assumir, pausar ou retomar atendimento*','Transferência para atendimento humano*',
 'Configuração da recepcionista conforme os recursos disponíveis']}
];
const comparisons=[
 ['Gestão e agenda',[['Dashboard','Essencial','Avançado','Avançado'],['Agenda e agendamento online',true,true,true],['Horários, intervalos e folgas',true,true,true]]],
 ['Clientes e serviços',[['Cadastro e histórico de clientes',true,true,true],['Serviços, combos e adicionais',true,true,true],['Identificação de clientes inativos',false,true,true]]],
 ['Site de agendamento',[['Página própria personalizada',true,true,true],['Agendamento público',true,true,true]]],
 ['Financeiro e relatórios',[['Receitas, despesas e saldo','Básico','Avançado','Avançado'],['DRE e relatórios detalhados',false,true,true],['Comissões, vales e pagamentos',false,true,true]]],
 ['Central de atendimento',[['Conversas e respostas manuais',false,true,true],['Conectar WhatsApp da empresa',false,true,true],['Atendimento e agendamento pela recepcionista IA',false,false,true]]],
 ['Automações e lembretes',[['Confirmação e lembretes WhatsApp*',true,true,true],['Convite para avaliação no Google*',true,true,true],['Agendamento automático por conversa*',false,false,true]]],
 ['Marketing e fidelização',[['Pontos e recompensas',false,true,true],['Cupons e benefícios',false,true,true],['Campanhas de aniversário e retorno*',false,true,true]]],
 ['Inteligência artificial',[['Marketing IA e Consultor IA de gestão*',false,true,true],['Recepcionista IA no WhatsApp*',false,false,true]]],
 ['Equipe e profissionais',[['Profissionais permitidos',null,null,null],['Relatórios por profissional',true,true,true]]],
 ['Suporte',[['Suporte padrão',true,true,true]]]
];
function compareCell(value){return value===true?'<span class="op-yes" aria-label="Incluído">'+ic('check')+'<span class="op-cell-word">Incluído</span></span>':value===false?'<span class="op-no" aria-label="Não incluído">—</span>':'<span class="op-comparison-value">'+escapeHTML(value)+'</span>';}
function canonical(code){const s=String(code||'').toLowerCase();return s.includes('essencial')?'essencial':s.includes('profissional')||s==='pro'||s.includes('_pro')?'profissional':s.includes('elite')?'elite':'';}
function statusOf(sub){if(!sub)return 'Sem assinatura';return ({active:'Ativa',trialing:'Em teste',trial:'Em teste',past_due:'Pagamento pendente',grace:'Em carência',suspended:'Suspensa',canceled:'Cancelada',cancelled:'Cancelada',expired:'Encerrada'})[sub.status]||'Estado não confirmado';}
let mounted=null;
function catalogPlan(plans,code){
 if(!Array.isArray(plans)||!defs.some(plan=>plan.code===code))return null;
 const rows=plans.filter(plan=>plan&&plan.code===code),plan=rows[0];
 return rows.length===1&&typeof plan.name==='string'&&plan.name.trim()&&plan.name.length<=120&&
  Number.isSafeInteger(plan.monthly_cents)&&plan.monthly_cents>0&&
  Number.isSafeInteger(plan.max_professionals)&&plan.max_professionals>0?plan:null;
}
function renderCard(def, server, currentCode, currentActive, allowed, ctx, source, checkoutConfigured, embedded) {
 const chosen=catalogPlan(server,def.code);
 const same=currentActive&&currentCode===def.code;
 const useEmbedded=embedded&&!currentActive;
 const badge=def.code==='profissional'?'<span class="op-recommended">Recomendado</span>':'';
 let actionLabel='Assinar '+(def.code==='profissional'?'Profissional':def.code==='essencial'?'Essencial':'Elite');
 let disabled='';
 if(!allowed){actionLabel='Somente proprietário';disabled='disabled';}
 else if(!chosen){actionLabel='Plano indisponível';disabled='disabled';}
 else if(same){actionLabel=source==='pix_prepaid'?'Renovar com Pix':'Plano atual';disabled=source==='pix_prepaid'?'':'disabled';}
 else if(currentActive){actionLabel=source==='manual'?'Gerenciado pela administração':'Troca de plano pelo suporte';disabled='disabled';}
 const features=def.features.map(feature=>'<li>'+ic('check')+'<span>'+escapeHTML(feature)+'</span></li>').join('');
 return '<article class="op-plan op-'+def.code+(same?' op-current':'')+'" aria-label="'+escapeHTML(def.name)+'">'
   +'<header class="op-plan-head"><div class="op-plan-heading"><h3>'+escapeHTML(def.name)+'</h3>'+badge+'</div><p>'+escapeHTML(def.intro)+'</p></header>'
   +'<div class="op-price"><strong>'+(chosen?price(chosen.monthly_cents):'Preço indisponível')+'</strong><span>'+(useEmbedded?'/mês':'/30 dias')+'</span></div>'
   +'<p class="op-per-company">por empresa · '+(chosen?'até '+chosen.max_professionals+' profissionais':'limite indisponível')+'</p>'
   +'<div class="op-plan-divider"></div><ul class="op-plan-features">'+features+'</ul>'
   +'<div class="op-plan-bottom"><button class="op-btn '+(def.code==='profissional'?'op-btn-primary':'op-btn-secondary')+'" type="button" data-op-checkout="'+def.code+'"'+(useEmbedded?' data-op-embedded':'')+' '+disabled+'>'+escapeHTML(actionLabel)+'</button>'
   +(useEmbedded&&checkoutConfigured&&!disabled?'<button class="op-btn op-btn-secondary" type="button" data-op-checkout="'+def.code+'">Pagar 30 dias com Pix</button>':'')
   +'<span class="op-plan-caption">'+(same?'Seu plano vigente':useEmbedded?'Assinatura mensal pela Cakto.':checkoutConfigured?'Pagamento protegido, sujeito à verificação.':'Pagamento temporariamente indisponível; nenhuma cobrança será gerada.')+'</span></div></article>';
}
function hasPlanDifference(feature){
 return feature[1]!==feature[2]||feature[2]!==feature[3];
}
function renderComparison(plans){
 const matrix=comparisons.map(([category,features])=>[category,features.map(feature=>feature[0]==='Profissionais permitidos'?
  [feature[0],...defs.map(def=>{const plan=catalogPlan(plans,def.code);return plan?'Até '+plan.max_professionals:'Não informado';})]:feature)]);
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
 const sections=matrix.map(([category,features],groupIndex)=>{
  const header='<tr class="op-matrix-category"><th scope="rowgroup" colspan="4"><span class="op-matrix-category-label">'+escapeHTML(category)+'</span></th></tr>';
  const body=features.map(feature=>{
   const [name,essencial,profissional,elite]=feature;
   return '<tr class="op-matrix-row" data-op-different="'+String(hasPlanDifference(feature))+'"><th scope="row">'+escapeHTML(name)+'</th>'
    +[essencial,profissional,elite].map((value,i)=>'<td'+(i===1?' class="op-matrix-popular"':'')+'>'+compareCell(value)+'</td>').join('')+'</tr>';
  }).join('');
  return '<tbody data-op-category="'+groupIndex+'">'+header+body+'</tbody>';
 }).join('');
 const mobileSections=matrix.map(([category,features],groupIndex)=>{
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

function effectiveBanner(data){
 const p=data.effective_plan;if(!p)return '<p class="os-notice" role="status">Não foi possível confirmar o acesso da empresa. Atualize o estado antes de continuar.</p>';
 const origins={manual:'Liberação manual',cakto:'Assinatura Cakto',pix_prepaid:'Pix pré-pago (renovação manual)',trial:'Teste grátis',none:'Sem acesso ativo',platform_admin:'Administração Orion'};
 const canceledPaid=p.active&&p.source==='cakto'&&p.commercial_subscription?.status==='canceled'&&p.valid_until;
 const description=canceledPaid?'Renovação cancelada. Acesso pago até '+localDate(p.valid_until):
  (origins[p.source]||'Origem não confirmada')+(p.valid_until?' · até '+localDate(p.valid_until):'');
 return '<section class="op-account-state" data-op-effective-plan aria-label="Acesso liberado à empresa"><div><span class="op-eyebrow">SEU ACESSO</span><h2>'+escapeHTML(p.active?p.name||'Plano Orion':'Sem plano ativo')+'</h2><p>'+escapeHTML(description)+'</p></div><div class="op-current-metrics"><span><small>Profissionais ativos</small><strong>'+escapeHTML(p.limits?.active_professionals??data.active_professionals??0)+' de '+escapeHTML(p.limits?.max_professionals??'—')+'</strong></span></div>'+(p.source==='manual'&&p.active?'<p class="os-help">A liberação manual tem prioridade sobre compras. Para que uma contratação determine o acesso, a administração deve remover esta atribuição. A assinatura comercial permanece preservada.</p>':'')+(Number(p.limits?.excess_professionals)>0?'<p class="os-notice">Há '+escapeHTML(p.limits.excess_professionals)+' profissional(is) acima do limite. Os dados e agendamentos foram preservados. Novas ativações ficam bloqueadas até a regularização.</p>':'')+'</section>';
}
function subscriptionBanner(data){
 const sub=data.subscription,trial=data.trial;
 if(sub){
   const canceledPaid=sub.status==='canceled'&&data.effective_plan?.active&&data.effective_plan?.source==='cakto'&&data.effective_plan?.valid_until;
   const status=canceledPaid?'Renovação cancelada':statusOf(sub);
   const details=[
     '<span><small>'+(canceledPaid?'Valor do período':'Mensalidade')+'</small><strong>'+price(sub.monthly_cents||0)+'</strong></span>',
     '<span><small>Profissionais</small><strong>'+(data.active_professionals||0)+' de '+(sub.max_professionals||'—')+'</strong></span>',
   ];
   if(canceledPaid)details.push('<span><small>Acesso pago até</small><strong>'+escapeHTML(localDate(data.effective_plan.valid_until))+'</strong></span>');
   if(sub.next_renewal_at&&sub.status!=='canceled')details.push('<span><small>Próxima cobrança</small><strong>'+escapeHTML(localDate(sub.next_renewal_at))+'</strong></span>');
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
 const eligible=allowed&&!sub&&!trial&&!data.effective_plan?.active&&!data.effective_plan?.manual_assignment?.active;
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

function checkoutField(name,label,type='text',attributes=''){
 return '<label class="op-payment-field"><span>'+label+'</span><input name="'+name+'" type="'+type+'" '+attributes+'></label>';
}
function checkoutDocumentDigits(value){
 return String(value||'').replace(/\D/g,'').slice(0,14);
}
function checkoutDocumentKind(value){
 const count=checkoutDocumentDigits(value).length;
 return count===11?'cpf':count===14?'cnpj':null;
}
function checkoutDocumentDisplay(value){
 const digits=checkoutDocumentDigits(value);
 if(digits.length>11)return digits.replace(/^(\d{2})(\d)/,'$1.$2')
  .replace(/^(\d{2}\.\d{3})(\d)/,'$1.$2')
  .replace(/^(\d{2}\.\d{3}\.\d{3})(\d)/,'$1/$2')
  .replace(/^(\d{2}\.\d{3}\.\d{3}\/\d{4})(\d)/,'$1-$2');
 return digits.replace(/^(\d{3})(\d)/,'$1.$2')
  .replace(/^(\d{3}\.\d{3})(\d)/,'$1.$2')
  .replace(/^(\d{3}\.\d{3}\.\d{3})(\d)/,'$1-$2');
}
function checkoutDocumentData(field){
 const digits=checkoutDocumentDigits(field.value);
 const kind=checkoutDocumentKind(field.value);
 let error=kind?'':'Informe CPF com 11 dígitos ou CNPJ com 14 dígitos.';
 if(kind&&window.OrionFields?.documentValue){
  try{window.OrionFields.documentValue(digits,kind);}
  catch{error='Confira os dígitos do '+kind.toUpperCase()+'.';}
 }
 field.setCustomValidity(error);
 return {kind,digits};
}
/* Pix único: cobrança voluntária, acesso de 30 dias, sem cartão/recorrência. */
async function pixCheckout(root,code,ctx,data,{resumeOnly=false,renewal=false,manualRenewal=false}={}){
 const action=root.querySelector('[data-op-checkout="'+code+'"]');
 const selected=catalogPlan(data?.plans,code);
 if(!action||action.disabled||!selected||data?.can_manage_plan!==true||ctx.canManage!==true||root.querySelector('.op-checkout-dialog'))return;
 const modal=document.createElement('dialog');
 modal.className='os-dialog op-dialog op-checkout-dialog';
 modal.setAttribute('aria-label','Pagamento Pix único Orion');
 const advanceOn=manualRenewal?barberDate(data.effective_plan?.manual_assignment?.expires_at,data.barbershop?.timezone):'';
 const title=resumeOnly?'Acompanhar pagamento':manualRenewal&&canonical(data.effective_plan?.code)!==code?
  'Mudar para '+selected.name.replace(/^Orion /,''):renewal?'Renovar '+selected.name.replace(/^Orion /,''):'Pagar 30 dias de acesso';
 const terms=advanceOn?'Seu plano atual segue até '+advanceOn+'. O próximo período começa nessa data após a confirmação do pagamento.':
  renewal?'Os próximos 30 dias são somados ao seu período atual.':'Pagamento único. Sem débito automático e sem cartão.';
 modal.innerHTML='<div class="op-payment-top">'
 +'<div class="op-payment-heading"><span class="op-payment-symbol">'+ic('shield')+'</span><div class="op-payment-heading-copy">'
 +(renewal?'':'<span class="op-eyebrow">ORION · PIX PRÉ-PAGO</span>')+'<h2>'+escapeHTML(title)+'</h2>'
 +'<p data-pix-terms>'+escapeHTML(terms)+'</p></div></div>'
 +'<button class="op-dialog-close" type="button" data-pix-close aria-label="Fechar pagamento">'+ic('close')+'</button></div>'
 +'<div class="op-payment-summary"><div class="op-payment-plan"><span>'+escapeHTML(selected.name)+'</span>'
 +'<small>Acesso por 30 dias · Renovação manual</small></div><strong>'+price(selected.monthly_cents)+'<small> / 30 dias</small></strong></div>'
 +'<div class="op-payment-banner" data-pix-status role="status" aria-live="polite">Verificando a disponibilidade do Pix…</div>'
 +'<form class="op-payment-form" data-pix-form hidden><div class="op-payment-fields">'
 +'<h3 class="op-payment-section-title">Dados do pagador</h3>'
 +checkoutField('name','Nome completo','text','autocomplete="name" required minlength="3" maxlength="120"')
 +checkoutField('email','E-mail','email','autocomplete="email" required maxlength="254"')
 +checkoutField('phone','Telefone com DDD','tel','autocomplete="tel" inputmode="tel" required maxlength="20"')
 +checkoutField('doc_number','CPF ou CNPJ','text','required inputmode="numeric" autocomplete="off" minlength="11" maxlength="18"')
 +'</div><div class="op-payment-confirmations">'
 +'<label class="op-payment-consent"><input type="checkbox" name="one_time" required>'
 +'<span>Confirmo o Pix único de <strong>'+price(selected.monthly_cents)+'</strong> por 30 dias de '+escapeHTML(selected.name)+'.</span></label>'
 +'<label class="op-payment-consent"><input type="checkbox" name="understood" required>'
 +'<span>Entendo que não há renovação automática. O próximo período precisa de outro Pix, por minha decisão.</span></label>'
 +'</div><p class="op-payment-error" data-pix-error role="alert" aria-live="polite"></p>'
 +'<div class="op-payment-actions"><button class="op-btn op-btn-primary" data-pix-submit type="submit">Gerar Pix</button>'
 +'<p class="op-payment-secure">'+ic('shield')+' Seu acesso só será liberado após o pagamento ser confirmado pela Cakto.</p></div>'
 +'</form><div class="op-payment-result" data-pix-result hidden></div>';
 root.appendChild(modal);modal.showModal();
 const form=modal.querySelector('[data-pix-form]');
 const status=modal.querySelector('[data-pix-status]');
 const feedback=modal.querySelector('[data-pix-error]');
 const submit=modal.querySelector('[data-pix-submit]');
 const resultBox=modal.querySelector('[data-pix-result]');
 form.elements.namedItem('name').value=ctx.user?.name||'';
 form.elements.namedItem('email').value=ctx.user?.email||'';
 form.elements.namedItem('phone').value=window.OrionFields?.phoneDisplay(ctx.user?.phone||ctx.tenant?.contact_phone||'')||'';
 let busy=false,finished=false,intent=null,ticker=null,ready=false,manualExpectedStart=null;
 const close=()=>{
  if(busy)return;
  if(ticker)window.clearInterval(ticker);
  modal.close();modal.remove();action.focus({preventScroll:true});
 };
 const showPix=(result,resumed=false)=>{
  if(result?.status!=='pending_confirmation'||!defs.some(plan=>plan.code===result.plan_code)||
    !Number.isSafeInteger(result.monthly_cents)||result.monthly_cents<=0||
    typeof result.intent_id!=='string'||!result.intent_id||
    result.pix_code!=null&&(typeof result.pix_code!=='string'||!result.pix_code||result.pix_code.length>32768))
   throw Error('O Pix exige verificação. Não tente gerar outro.');
  const hasCode=typeof result.pix_code==='string';
  intent=result;ready=false;form.hidden=true;resultBox.hidden=false;
  // A resumed order keeps its original agreed amount, even if the current
  // catalogue changes. It never becomes permission to create another charge.
  const label=catalogPlan(data.plans,result.plan_code)?.name||result.plan_code;
  modal.querySelector('.op-payment-plan span').textContent=label;
  modal.querySelector('.op-payment-summary > strong').innerHTML=price(result.monthly_cents)+'<small> / 30 dias</small>';
  resultBox.innerHTML='<div class="op-payment-pix"><span class="op-eyebrow">'+(resumed?'PIX EM ANDAMENTO':'PIX GERADO')+'</span>'
    +(hasCode?'<h3>Pague pelo seu banco</h3><p>Pagamento único, sem cobrança mensal automática. O QR Code tem validade limitada.</p>':
      '<h3>Aguardando confirmação</h3><p>O pagamento existente está sendo verificado. Não gere outro Pix.</p>')
    +(hasCode&&String(result.pix_qr_data_uri||'').startsWith('data:image/png;base64,')
      ?'<img class="op-payment-qr" alt="QR Code Pix Cakto" src="'+escapeHTML(result.pix_qr_data_uri)+'">':'')
    +(hasCode?'<label class="op-payment-field"><span>Pix copia e cola</span>'
      +'<textarea data-pix-code readonly rows="3">'+escapeHTML(result.pix_code)+'</textarea></label>'
      +'<button type="button" class="op-btn op-btn-primary" data-pix-copy>Copiar código Pix</button>':'')
    +'<button type="button" class="op-btn op-btn-secondary" data-pix-check>Verificar pagamento</button></div>';
  status.textContent=!hasCode?'Aguardando a confirmação do pagamento existente. Nenhuma nova cobrança foi criada.':
   resumed?'Retomamos seu Pix existente. Nenhuma nova cobrança foi criada.':'Aguardando pagamento. A confirmação libera 30 dias de acesso.';
  if(ticker)window.clearInterval(ticker);
  ticker=window.setInterval(()=>{if(!document.hidden)void poll();},12000);
 };
 const refreshAccess=async answer=>{
  finished=true;ready=false;form.hidden=true;if(ticker)window.clearInterval(ticker);
  const scheduled=answer.status==='scheduled';
  status.textContent=scheduled?'Pagamento confirmado. Seu próximo período está garantido.':'Pagamento confirmado. Seu plano está ativo.';
  resultBox.hidden=false;resultBox.innerHTML='<div class="op-payment-success">'+ic('check')+'<h3>Pagamento aprovado!</h3>'
    +'<p>Seu acesso vai até '+escapeHTML(localDate(answer.paid_until))+'. A renovação é manual.</p></div>';
  const fresh=await ctx.request('/plan-overview');
  if(modal.isConnected){busy=false;close();render(root,fresh,ctx);
    window.dispatchEvent(new CustomEvent('orion:plan-changed',{detail:{preservePage:true}}));}
 };
 const recoverCurrent=async()=>{
  ready=false;
  const existing=await ctx.request('/plan-pix-current');if(!modal.isConnected)return true;
  if(existing?.status==='none')return false;
  if(existing?.status==='pending_confirmation'){showPix(existing,true);return true;}
  if(['active','scheduled'].includes(existing?.status)){await refreshAccess(existing);return true;}
  if(['review_required','declined','refunded','expired'].includes(existing?.status)){
   form.hidden=true;
   status.textContent=existing.status==='expired'?'Este período encerrou. Feche esta janela e confira as opções de renovação.':
    existing.status==='review_required'?'Pagamento em análise. Não gere outro Pix; procure o suporte.':
    'O pagamento anterior precisa de verificação. Consulte o suporte antes de iniciar outro Pix.';
   return true;
  }
  throw Error('Não foi possível confirmar o pagamento existente. Nenhuma nova cobrança será iniciada.');
 };
 const poll=async()=>{
  if(!intent||finished||!modal.isConnected)return;
  try{
   const answer=await ctx.request('/plan-pix-status?intent_id='+encodeURIComponent(intent.intent_id));
   if(!modal.isConnected)return;
   if(['active','scheduled'].includes(answer.status)){
    await refreshAccess(answer);
   }else if(answer.status==='review_required'){
    if(ticker)window.clearInterval(ticker);
    status.textContent='Pagamento em análise. Não gere outro Pix; procure o suporte.';
   }else if(['refunded','declined','expired'].includes(answer.status)){
    if(ticker)window.clearInterval(ticker);
    status.textContent=answer.status==='expired'?'Este período encerrou. Feche esta janela e confira as opções de renovação.':
     'Pagamento não concluído ou estornado. Consulte o suporte.';
   }else status.textContent='Aguardando pagamento no seu banco.';
  }catch{status.textContent='Sem atualização de pagamento. Verifique novamente.';}
 };
 modal.addEventListener('click',event=>{
  if(event.target.closest('[data-pix-close]')){event.preventDefault();close();}
  const copy=event.target.closest('[data-pix-copy]');
  if(copy){
   event.preventDefault();
   const value=resultBox.querySelector('[data-pix-code]')?.value;
   if(!value)return;
   if(navigator.clipboard?.writeText)navigator.clipboard.writeText(value)
     .then(()=>{copy.textContent='Copiado!';})
     .catch(()=>resultBox.querySelector('[data-pix-code]')?.select());
   else resultBox.querySelector('[data-pix-code]')?.select();
  }
  if(event.target.closest('[data-pix-check]')){event.preventDefault();void poll();}
 });
 modal.addEventListener('cancel',event=>{event.preventDefault();close();});
 form.addEventListener('input',event=>{
  if(event.target.name==='phone')try{
   event.target.value=window.OrionFields?.phoneDisplay(event.target.value)||event.target.value;
  }catch{}
  if(event.target.name==='doc_number'){
   event.target.value=checkoutDocumentDisplay(event.target.value);
   event.target.setCustomValidity('');
  }
 });
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(resumeOnly||busy||finished||!ready||form.hidden)return;
  const doc=checkoutDocumentData(form.elements.namedItem('doc_number'));
  if(!form.reportValidity())return;
  const value=name=>form.elements.namedItem(name).value.trim();
  busy=true;submit.disabled=true;feedback.textContent='';
  status.textContent='Criando Pix na Cakto…';
  try{
   const payer={name:value('name'),email:value('email'),phone:value('phone'),
     doc_type:doc.kind,doc_number:doc.digits,
     fingerprint:'orion_'+(window.crypto?.randomUUID?.()||String(Date.now())+'_prepaid_pix_12345')};
   const result=await ctx.request('/plan-pix-charge','POST',{
     plan_code:code,payer,consent_one_time:true,expected_cents:selected.monthly_cents,
     ...(manualRenewal?{manual_renewal:true,manual_expected_start:manualExpectedStart}:{})
   });
   if(result.status!=='pending_confirmation'||result.plan_code!==code
      ||result.monthly_cents!==selected.monthly_cents||!result.intent_id||!result.pix_code)
      throw Error('O Pix exige verificação. Não tente gerar outro.');
   showPix(result);
  }catch(err){
   ready=false;
   feedback.textContent=(err.message||'Não foi possível gerar o Pix.')
      +' Não gere outro pagamento enquanto houver uma cobrança em verificação.';
   status.textContent='Pagamento não confirmado.';
   // A lost POST response may still have created the original payment. Only
   // the authenticated recovery GET may restore that order, never a second POST.
   try{
    const recovered=await recoverCurrent();
    if(!recovered&&[400,422].includes(err.status)&&modal.isConnected){
     // A definitive rejection plus an authoritative empty recovery permits
     // corrected input. Only another explicit submit can start a new attempt.
     ready=true;submit.disabled=false;form.hidden=false;
     feedback.textContent=(err.message||'Confira os dados do pagamento.')+' Corrija os dados e confirme novamente.';
     status.textContent='Esta tentativa não gerou cobrança. Confira os dados e confirme novamente.';
    }
   }catch{}
  }finally{busy=false;}
 });
 try{
  if(await recoverCurrent())return;
  if(resumeOnly){status.textContent='Este pagamento não está mais pendente. Atualize a página para conferir seus planos.';return;}
  const config=await ctx.request(manualRenewal?'/plan-pix-config?manual_renewal=1&plan_code='+encodeURIComponent(code):'/plan-pix-config');
  if(!modal.isConnected)return;
  if(!config?.available||config.mode!=='manual_30_days'){
   status.textContent='Pix temporariamente indisponível. Nenhuma cobrança criada.';
   return;
  }
  if(manualRenewal){
   const newStart=barberDate(config.starts_at,data.barbershop?.timezone);
   if(!newStart){status.textContent='Não foi possível confirmar quando começa o novo período.';return;}
   manualExpectedStart=config.starts_at;
   modal.querySelector('[data-pix-terms]').textContent='Seu plano atual segue até '+newStart+'. O próximo período começa nessa data após a confirmação do pagamento.';
  }
  status.textContent=renewal?'Pagamento único por Pix.':'Pix comum: 30 dias de acesso, renovação manual.';
  ready=true;form.hidden=false;
 }catch{status.textContent='Não foi possível validar o Pix. Nenhuma cobrança criada.';}
}

async function openEmbeddedCheckout(root,button,ctx,data){
 const code=button.dataset.opCheckout,plan=catalogPlan(data.plans,code);
 const maintenance=data.barbershop?.maintenance===true;
 const publicUrl=maintenance?data.barbershop.maintenance_checkout_urls?.[code]:null;
 if(!plan||data.can_manage_plan!==true||ctx.canManage!==true||root.querySelector('.op-checkout-dialog')||
    data.embedded_checkout_enabled!==true||(maintenance?!publicUrl:ctx.canPurchase?.()===false))return;
 const modal=document.createElement('dialog');
 modal.className='os-dialog op-dialog op-checkout-dialog op-embedded-checkout';
 modal.setAttribute('aria-label','Assinatura mensal pela Cakto');
 modal.innerHTML='<header class="op-embedded-header"><div><h2>'+escapeHTML(plan.name)+'</h2>'
   +'<p>'+price(plan.monthly_cents)+' a cada 30 dias</p></div>'
   +'<button type="button" class="op-btn op-btn-secondary" data-embedded-close>Voltar aos planos</button></header>'
   +'<p class="op-payment-banner" data-embedded-status role="status" aria-live="polite">Carregando pagamento…</p>';
 root.append(modal);
 const status=modal.querySelector('[data-embedded-status]');
 let timer,intent,frame,busy=false,finished=false;
 const current=()=>modal.isConnected&&mounted?.root===root&&mounted?.ctx===ctx;
 const cleanup=()=>{window.clearInterval(timer);modal.remove();};
 const close=()=>{cleanup();button.focus({preventScroll:true});};
 mounted.closeCheckout=cleanup;
 modal.addEventListener('cancel',event=>{event.preventDefault();close();});
 modal.querySelector('[data-embedded-close]').addEventListener('click',close);
 modal.showModal();

 async function poll(){
  if(!current()||!intent||busy||finished)return;
  busy=true;
  try{
   const answer=await ctx.request('/plan-checkout?intent_id='+encodeURIComponent(intent));
   if(!current()||answer.plan_code!==code)return;
   if(answer.status==='verified'){
    window.clearInterval(timer);finished=true;frame.remove();status.hidden=false;
    status.textContent='Pagamento confirmado. Seu plano está ativo.';
    window.dispatchEvent(new CustomEvent('orion:plan-changed',{detail:{preservePage:true}}));
    const fresh=await ctx.request('/plan-overview');
    if(current()){close();render(root,fresh,ctx);}
   }else if(answer.status==='expired'){
    window.clearInterval(timer);finished=true;frame.remove();status.hidden=false;
    status.textContent='O pagamento expirou. Volte aos planos para tentar novamente.';
   }
  }catch{/* Keep the checkout open and check again on the next interval. */}
  finally{busy=false;}
 }
 try{
  const result=maintenance?{checkout_url:publicUrl,plan_code:code,monthly_cents:plan.monthly_cents}:
   await ctx.request('/plan-checkout','POST',{plan_code:code,embedded:true,expected_cents:plan.monthly_cents});
  if(!current())return;
  const url=new URL(result.checkout_url),callback=url.searchParams.get('callback');
  const validAccess=maintenance?!url.search:result.status==='pending_payment'&&
   /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(result.intent_id||'')&&
   /^orion_[A-Za-z0-9_-]{43}$/.test(callback||'')&&url.searchParams.get('sck')===callback&&[...url.searchParams].length===2;
  if(result.plan_code!==code||result.monthly_cents!==plan.monthly_cents||!validAccess||
     url.origin!=='https://pay.cakto.com.br'||url.username||url.password||url.hash||!/^\/[A-Za-z0-9_-]+$/.test(url.pathname))
    throw Error('O Orion não conseguiu validar este checkout.');
  intent=result.intent_id;
  frame=document.createElement('iframe');frame.className='op-cakto-frame';frame.title='Checkout seguro da Cakto';
  frame.referrerPolicy='no-referrer';
  frame.addEventListener('load',()=>{if(current()&&!finished)status.hidden=true;},{once:true});
  frame.setAttribute('sandbox',maintenance?'allow-scripts allow-same-origin':
   'allow-forms allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox');
  frame.src=url.href;
  if(maintenance){
   const scroll=document.createElement('div');
   scroll.className='op-cakto-inspection';scroll.tabIndex=0;
   scroll.setAttribute('aria-label','Visualização do checkout da Cakto');
   frame.setAttribute('inert','');
   frame.tabIndex=-1;
   scroll.append(frame);modal.append(scroll);
   return;
  }
  frame.setAttribute('allow','payment https://pay.cakto.com.br');
  modal.append(frame);
  timer=window.setInterval(()=>{if(!document.hidden)void poll();},12000);
 }catch(error){
  if(current())status.textContent='Não foi possível abrir o pagamento agora. Volte aos planos e tente novamente.';
 }
}

function trialRemaining(detail){
 const seconds=detail.trial_remaining_seconds;
 if(!Number.isSafeInteger(seconds)||seconds<0)return '';
 const days=Math.min(detail.trial_days||7,Math.ceil(seconds/86400));
 return days===1?'1 dia':days+' dias';
}

function barberDate(value,zone) {
 if(!value)return '';
 try{return new Intl.DateTimeFormat('pt-BR',{dateStyle:'medium',timeStyle:'short',timeZone:zone||timezone}).format(new Date(value));}
 catch{return '';}
}

function openPlanChange(root, button, data) {
 const plan=catalogPlan(data.plans,button.dataset.opChangePlan);
 if(!plan||root.querySelector('.op-checkout-dialog'))return;
 const state=data.effective_plan||{},detail=data.barbershop;
 const end=barberDate(state.valid_until||detail.period_ends_at,detail.timezone);
 const automatic=data.subscription&&state.commercial_subscription?.cakto_bound===true;
 const over=Math.max(0,(state.limits?.active_professionals??0)-plan.max_professionals);
 const notes=[];
 if(end&&state.active)notes.push('Seu plano atual continua até '+end+'.');
 if(over)notes.push('Para usar este plano, reduza a equipe para '+plan.max_professionals+' profissionais ativos.');
 if(detail.maintenance)notes.push('A mudança pode ser concluída na conta do titular.');
 else if(automatic&&data.subscription.status!=='canceled')
  notes.push('Gerencie a renovação da assinatura atual na Cakto. Quando ela estiver encerrada e o período pago terminar, este plano poderá ser contratado aqui.');
 else if(automatic)notes.push('O novo plano poderá ser contratado quando o período pago da assinatura atual terminar.');
 else if(detail.manual_pix?.upcoming)notes.push('Já existe um próximo plano pago para esta empresa.');
 else if(end&&!over)notes.push('Escolha este plano no fim do período atual.');
 else if(!over)notes.push('A contratação deste plano ainda não está disponível para a empresa.');
 const modal=document.createElement('dialog');
 modal.className='os-dialog op-dialog op-checkout-dialog op-change-dialog';
 modal.setAttribute('aria-label','Mudar para '+plan.name);
 modal.innerHTML='<h2>Mudar para '+escapeHTML(plan.name.replace(/^Orion /,''))+'</h2><p>'+price(plan.monthly_cents)+' / 30 dias</p>'
  +notes.map(note=>'<p>'+escapeHTML(note)+'</p>').join('')
  +'<div class="op-payment-actions">'
  +(automatic&&!detail.maintenance&&data.subscription.status!=='canceled'?
    '<a class="op-btn op-btn-primary" href="https://assinatura.cakto.com.br/" target="_blank" rel="noopener noreferrer">Gerenciar assinatura na Cakto</a>':'')
  +'<button type="button" class="op-btn op-btn-secondary" data-op-close-change>Voltar aos planos</button></div>';
 const close=()=>{modal.remove();button.focus({preventScroll:true});};
 mounted.closeCheckout=()=>modal.remove();
 modal.addEventListener('cancel',event=>{event.preventDefault();close();});
 modal.querySelector('[data-op-close-change]').addEventListener('click',close);
 root.append(modal);modal.showModal();
}

function renderBarbershop(data, canManage) {
 const state=data.effective_plan||{}, sub=data.subscription, trial=data.trial, detail=data.barbershop;
 const source=state.source, limits=state.limits||{}, current=canonical(state.code);
 const automatic=source==='cakto'||(source==='none'&&state.commercial_subscription?.cakto_bound===true);
 const manual=source==='manual', prepaid=source==='pix_prepaid', evaluating=source==='trial';
 const endedManual=source==='none'&&state.manual_assignment?.expires_at&&!sub;
 const pending=detail.payment;
 const recurCanceled=automatic&&sub?.status==='canceled';
 const locked=(!!sub&&!detail.can_recontract)||manual||prepaid;
 const find=code=>catalogPlan(data.plans,code);
 const date=value=>barberDate(value,detail.timezone);
 const metric=(label,value)=>value===null||value===undefined||value===''?'':'<span><small>'+escapeHTML(label)+'</small><strong>'+escapeHTML(value)+'</strong></span>';
 let title=state.active?state.name:'Sem acesso ativo', status='Escolha um dos planos disponíveis para sua barbearia.';
 if(manual)status='Acesso liberado pela equipe Orion';
 else if(evaluating)status='Teste grátis em andamento';
 else if(prepaid)status='Período pago com Pix';
 else if(recurCanceled)status=state.active?'Renovação cancelada. O período pago continua disponível.':'Assinatura cancelada';
 else if(automatic)status=state.active?'Assinatura com renovação automática':statusOf(sub);
 else if(state.status==='expired'||trial?.status==='expired')status='Seu período de acesso terminou.';
 else if(trial?.status==='pending')status='Sua solicitação de teste está em análise.';
 const hold=state.billing?.hold;
 if(hold)status=({refund_requested:'Reembolso em análise',refunded:'Período reembolsado',chargeback:'Contestação financeira em análise'})[hold]||'Acesso suspenso';
 if(automatic&&!state.active&&sub?.name)title=sub.name;
 if(endedManual)title=find(canonical(state.manual_assignment.plan_code))?.name||title;
 const cents=prepaid?detail.amount_cents:automatic?sub?.monthly_cents:null;
 const remaining=trialRemaining(detail);
 const summary='<section class="op-account-state" data-op-effective-plan aria-label="Seu plano"><div><span class="op-eyebrow">SEU PLANO</span><h2>'+escapeHTML(title)+'</h2><p>'+escapeHTML(status)+'</p></div><div class="op-current-metrics">'
  +metric('Profissionais',limits.max_professionals==null?null:`${limits.active_professionals??0} de ${limits.max_professionals}`)
  +metric(manual||endedManual?'Liberado em':'Confirmação inicial',date(manual||endedManual?state.manual_assignment?.started_at:detail.contract_confirmed_at))
  +metric('Último pagamento',date(detail.last_payment_at))
  +metric(evaluating?'Início do teste':'Início do período',date(evaluating?trial?.started_at:detail.period_started_at))
  +metric(evaluating?'Fim do teste':'Acesso até',date(evaluating?trial?.ends_at:endedManual?state.manual_assignment.expires_at:state.valid_until))
  +metric('Troca programada',state.scheduled_manual_assignment?find(state.scheduled_manual_assignment.plan_code)?.name+' em '+date(state.scheduled_manual_assignment.started_at):'')
  +metric('Próximo plano',detail.manual_pix?.upcoming?find(detail.manual_pix.upcoming.plan_code)?.name+' a partir de '+date(detail.manual_pix.upcoming.starts_at):'')
  +(evaluating&&remaining?'<span><small>Tempo restante</small><strong data-op-trial-remaining>'+escapeHTML(remaining)+'</strong></span>':'')
  +metric('Próxima cobrança',automatic&&!recurCanceled&&!hold?date(detail.next_payment_at):'')
  +metric('Valor do período',Number.isSafeInteger(cents)&&cents>0?price(cents):'')
  +'</div></section>';
 const paymentNotice=pending?.state==='needs_review'?'O pagamento precisa de revisão. Consulte o suporte para acompanhar a tentativa.':pending?.state==='declined'?'O pagamento anterior foi recusado. Escolha um plano para abrir o checkout.':pending?'Aguardando confirmação da contratação. Acompanhe o pagamento existente.':'';
 const notice=!canManage||detail.maintenance?'Somente o proprietário pode contratar ou alterar o plano na própria conta.'
  :hold?'O acesso está suspenso. Use o suporte para acompanhar a análise e a regularização.'
  :manual?(detail.manual_pix?.available?'Renove ou escolha o próximo plano antes do vencimento.':'')
  :detail.can_recontract?'Escolha o plano que quer ativar.'
  :automatic?(state.active&&!recurCanceled?'Sua assinatura é renovada automaticamente na data da próxima cobrança.':'Consulte sua assinatura para regularizar o pagamento.')
  :prepaid?(data.checkout_enabled===true?'A renovação soma 30 dias ao seu acesso atual.':'A renovação está temporariamente indisponível.')
  :!data.embedded_checkout_enabled?'O checkout está temporariamente indisponível.'
  :evaluating?'Você pode contratar um plano agora. O acesso pago começa após a confirmação do pagamento.'
  :trial?.status==='expired'?'Seu teste terminou. Escolha um plano para voltar a usar a agenda e os demais recursos.':'';
 const manageLink=canManage&&automatic&&!detail.maintenance&&!detail.can_recontract&&(!recurCanceled||!state.active)?
  '<a class="op-btn op-btn-secondary" href="https://assinatura.cakto.com.br/" target="_blank" rel="noopener noreferrer">'
   +(state.active?'Gerenciar assinatura':'Regularizar assinatura')+'</a>':'';
 const catalog=['essencial','profissional','elite'].map(code=>{
  const plan=find(code);
  const name=plan?.name||'Plano indisponível';
  const active=state.active===true&&current===code&&!evaluating;
  const existing=pending&&!['declined','needs_review'].includes(pending.state);
  const conflict=pending?.state==='needs_review'||existing&&pending.plan_code!==code;
  const blocked=hold==='refund_requested'||hold==='chargeback';
  const resumePix=existing&&!blocked&&!conflict&&pending.channel==='pix';
  const recurring=!locked&&!blocked&&!conflict&&(!existing||pending.channel==='cakto')&&data.embedded_checkout_enabled===true;
  const renew=prepaid&&active&&data.checkout_enabled===true&&!existing&&!blocked&&!conflict;
  const manualAdvance=manual&&detail.manual_pix?.available===true&&!existing&&!blocked&&!conflict&&
   !detail.manual_pix.blocked_plan_codes?.includes(code);
  const inspect=!!(detail.maintenance&&detail.maintenance_checkout_urls?.[code]&&recurring&&!existing);
  const allowed=canManage&&!!plan&&(detail.maintenance?inspect:(recurring||resumePix||renew||manualAdvance));
  const shortName=name.replace(/^Orion /,'');
  const oldPlan=canonical(sub?.code);
  const label=existing?'Acompanhar pagamento':renew||manualAdvance&&active?'Renovar antecipadamente':manualAdvance?'Mudar para '+shortName:
   detail.can_recontract?(code===oldPlan?'Renovar '+shortName:'Mudar para '+shortName):'Assinar '+shortName;
  const action=allowed?'<button class="op-btn '+(plan.recommended||active?'op-btn-primary':'op-btn-secondary')+'" type="button" data-op-checkout="'+code+'"'
   +(resumePix?' data-op-resume-pix':manualAdvance?' data-op-renew-pix data-op-manual-renewal':renew?' data-op-renew-pix':' data-op-embedded')+'>'
   +escapeHTML(label)+'</button>':active?'<span class="op-plan-caption">Plano atual</span>':
   canManage&&plan&&locked&&!existing&&!blocked&&!conflict?
    '<button class="op-btn op-btn-secondary" type="button" data-op-change-plan="'+code+'">Mudar para '+escapeHTML(shortName)+'</button>':'';
  const features=(Array.isArray(plan?.benefits)?plan.benefits:[]).map(feature=>'<li>'+ic('check')+'<span>'+escapeHTML(feature)+'</span></li>').join('');
  return '<article class="op-plan op-'+code+(active?' op-current':'')+'"><header class="op-plan-head"><div class="op-plan-heading"><h3>'+escapeHTML(name)+'</h3>'
   +(plan?.recommended?'<span class="op-recommended">Recomendado</span>':'')+'</div><p>'+escapeHTML(plan?.description)+'</p></header>'
   +'<div class="op-price"><strong>'+escapeHTML(plan?price(plan.monthly_cents):'—')+'</strong><span>/ '+escapeHTML(plan?.period_days||detail.period_days)+' dias</span></div>'
   +'<p class="op-per-company">'+(plan?'Até '+escapeHTML(plan.max_professionals)+' profissionais por empresa':'')+'</p>'
   +'<div class="op-plan-divider"></div><ul class="op-plan-features">'+(code==='profissional'?'<li>'+ic('check')+'<span>Tudo do Essencial</span></li>':code==='elite'?'<li>'+ic('check')+'<span>Tudo do Profissional</span></li>':'')+features+'</ul>'
   +'<div class="op-plan-bottom">'+action+'</div></article>';
 }).join('');
 const trialFeatures=find('essencial')?.benefits;
 const trialCard=detail.trial_eligible&&canManage?
  '<article class="op-plan op-trial-plan"><header class="op-plan-head"><div class="op-plan-heading"><h3>Teste grátis</h3></div><p>Experimente os recursos do Essencial.</p></header>'
  +'<div class="op-price"><strong>'+escapeHTML(price(0))+'</strong><span>/ 7 dias</span></div><p class="op-per-company">Sem cartão ou cobrança automática</p>'
  +'<div class="op-plan-divider"></div><ul class="op-plan-features">'+(Array.isArray(trialFeatures)?trialFeatures:[]).map(feature=>'<li>'+ic('check')+'<span>'+escapeHTML(feature)+'</span></li>').join('')+'</ul>'
  +'<div class="op-plan-bottom"><button type="button" class="op-btn op-btn-secondary" data-op-start-trial>Iniciar teste grátis</button></div></article>':'';
 const welcome=detail.welcome;
 const preferences=welcome&&!welcome.revoked&&(welcome.status==='queued'||welcome.status==='sending')
  ?'<div class="op-recovery-note"><p class="op-billing-notice">Você autorizou o contato de boas-vindas pelo WhatsApp comercial.</p>'
   +(welcome.phone_changed&&welcome.status==='queued'?'<button type="button" class="op-btn op-btn-secondary" data-op-welcome="authorize">Autorizar no WhatsApp atualizado</button>':'')
   +'<button type="button" class="op-btn op-btn-secondary" data-op-welcome="revoke">Retirar autorização</button></div>':'';
 return '<div class="op-page op-barbershop"><header class="op-heading"><h2>Planos da barbearia</h2></header>'+summary
  +'<div id="opPlanFeedback" class="op-feedback" role="status" aria-live="polite" hidden></div>'
  +(notice||manageLink||paymentNotice?'<div class="op-recovery-note">'+(notice||paymentNotice?'<p class="op-billing-notice">'+escapeHTML([notice,paymentNotice].filter(Boolean).join(' '))+'</p>':'')+manageLink+'</div>':'')
  +'<section class="op-catalogue" aria-label="Escolha seu plano"><div class="op-plans-grid'+(trialCard?' op-plans-with-trial':'')+'">'+trialCard+catalog+'</div>'
  +'<p class="op-footnote">A recepcionista IA do Elite requer um WhatsApp conectado e a configuração do atendimento.</p></section>'
  +preferences+'<footer class="op-support" aria-label="Suporte Orion"><button type="button" class="op-support-link" data-op-support>Falar com o suporte</button><div class="op-support-result" aria-live="polite"></div></footer></div>';
}

async function barberAction(root, ctx, button, trial) {
 const preference=button.dataset.opWelcome;
 const feedback=trial||preference?root.querySelector('#opPlanFeedback'):root.querySelector('.op-support-result');
 if(!feedback)return;
 button.disabled=true;
 feedback.hidden=false;
 feedback.textContent=trial?'Ativando o teste…':preference?'Salvando preferência…':'Consultando o canal de suporte…';
 try{
  if(trial||preference){
   if(trial)await ctx.request('/plan-trial/activate','POST',{confirmed:true});
   else await ctx.request('/plan-welcome','PUT',{authorized:preference==='authorize'});
   const next=await ctx.request('/plan-overview');
   if(mounted?.root===root){
    render(root,next,ctx);
    if(trial)window.dispatchEvent(new CustomEvent('orion:plan-changed',{detail:{preservePage:true}}));
   }
   return;
  }
  const result=await ctx.request('/plan-support');
  const url=result?.url;
  if(typeof url!=='string'||!(/^(https:\/\/wa\.me\/55\d{10,11}\?text=|mailto:[^?]+\?body=)/.test(url))){
   feedback.textContent='O suporte está temporariamente indisponível.';
   return;
  }
  const link=document.createElement('a');
  link.className='op-btn op-btn-secondary';
  link.href=url;
  link.target='_blank';
  link.rel='noopener noreferrer';
  link.textContent=url.startsWith('mailto:')?'Abrir e-mail de suporte':'Abrir conversa no WhatsApp';
  feedback.replaceChildren(link);
  link.focus();
 }catch(error){
  feedback.textContent=error.message||(trial?'Não foi possível confirmar o teste.':'O suporte está temporariamente indisponível.');
 }finally{button.disabled=false;}
}

function render(root,data,ctx){
 if(!root.isConnected)return;
 if(mounted?.root===root)mounted.data=data;
 const plans=Array.isArray(data.plans)?data.plans:[],sub=data.subscription,
 currentCode=canonical(data.effective_plan?.code),currentActive=data.effective_plan?.active===true;
 const canManage=!!data.effective_plan&&!!data.can_manage_plan&&!!ctx.canManage;
 if(data.barbershop){
  root.innerHTML=renderBarbershop(data,canManage);
  return;
 }
 const embedded=data.embedded_checkout_enabled===true;
 root.innerHTML='<div class="op-page"><header class="op-heading"><span class="op-eyebrow">PLANOS ORION</span><h2>Escolha o plano ideal para sua empresa</h2><p>Tudo o que você precisa para organizar seus agendamentos, gerenciar seu negócio e oferecer uma experiência melhor aos seus clientes.</p></header>'
 +effectiveBanner(data)+subscriptionBanner(data)
 +'<div id="opPlanFeedback" class="op-feedback" role="status" aria-live="polite" hidden></div>'
 +(!data.checkout_enabled&&!embedded?'<p class="op-billing-notice" role="status">As contratações estão temporariamente indisponíveis enquanto a integração de pagamentos é validada. Nenhuma cobrança será gerada.</p>':'')
 +'<section aria-label="'+(embedded?'Planos de Barbearia':'Planos pré-pagos de 30 dias')+'"><div class="op-plans-grid">'+defs.map(def=>renderCard(def,plans,currentCode,currentActive,canManage,ctx,data.effective_plan?.source,!!data.checkout_enabled,embedded)).join('')+'</div>'
 +'<p class="op-footnote">*Recursos de mensagens, automações e IA exigem configuração, canal conectado e disponibilidade da funcionalidade. O Marketing IA e o Consultor IA não realizam agendamentos pelo WhatsApp; essa recepcionista é exclusiva do Elite.</p></section>'
 +(Array.isArray(data.feature_catalog)&&data.feature_catalog.some(item=>item.implemented===false)?'<p class="os-help" data-op-unavailable>'+data.feature_catalog.filter(item=>item.implemented===false).map(item=>escapeHTML(item.name)+': '+escapeHTML(item.description||'Indisponível nesta versão.')).join(' ')+'</p>':'')
 +renderTrial(data,canManage)
 +renderComparison(plans)
 +'</div>';
 applyComparisonFilter(root,root.dataset.opCompareOnly!=='false');
}
function mount(root,data,ctx){
 if(!root||!ctx?.request)return;
 unmount();
 mounted={root,ctx,data};
 timezone=ctx.tenant?.timezone||'America/Sao_Paulo';
 render(root,data,ctx);
 if(data.barbershop&&!data.barbershop.maintenance){
  const wanted=new URLSearchParams(location.search).get('plano');
  if(defs.some(plan=>plan.code===wanted)){
   const selected=root.querySelector('[data-op-checkout="'+wanted+'"],[data-op-change-plan="'+wanted+'"]');
   selected?.focus();
  }
 }
 mounted.trialRefresh=setInterval(async()=>{
  if(!root.isConnected||mounted?.root!==root||mounted.data.barbershop==null||
     mounted.data.effective_plan?.source!=='trial'||root.querySelector('dialog[open]')||
     document.visibilityState==='hidden')return;
  try{
   const refreshed=await ctx.request('/plan-overview');
   if(!root.isConnected||mounted?.root!==root||root.querySelector('dialog[open]'))return;
   const node=root.querySelector('[data-op-trial-remaining]');
   if(node&&refreshed.effective_plan?.source==='trial'){
    node.textContent=trialRemaining(refreshed.barbershop);
   }else{
    render(root,refreshed,ctx);
    window.dispatchEvent(new CustomEvent('orion:plan-changed',{detail:{preservePage:true}}));
   }
  }catch{}
 },60000);
 root.addEventListener('click',root._orionPlansClick=async event=>{
  const action=event.target.closest('[data-op-start-trial],[data-op-support],[data-op-welcome]');
  if(action&&!action.disabled){event.preventDefault();await barberAction(root,ctx,action,action.hasAttribute('data-op-start-trial'));return;}
  const trial=event.target.closest('[data-op-trial]'),checkoutButton=event.target.closest('[data-op-checkout]');
  const change=event.target.closest('[data-op-change-plan]');
  if(change){event.preventDefault();openPlanChange(root,change,mounted?.root===root?mounted.data:data);return;}
  if(trial&&!trial.disabled){event.preventDefault();renderTrialDialog(root,data,ctx);}
  if(checkoutButton&&!checkoutButton.disabled){
   event.preventDefault();
   const currentData=mounted?.root===root?mounted.data:data;
   if(checkoutButton.hasAttribute('data-op-embedded'))await openEmbeddedCheckout(root,checkoutButton,ctx,currentData);
   else if(!currentData.barbershop||checkoutButton.hasAttribute('data-op-resume-pix')||checkoutButton.hasAttribute('data-op-renew-pix'))
    await pixCheckout(root,checkoutButton.dataset.opCheckout,ctx,currentData,{
     resumeOnly:checkoutButton.hasAttribute('data-op-resume-pix'),
     renewal:checkoutButton.hasAttribute('data-op-renew-pix'),
     manualRenewal:checkoutButton.hasAttribute('data-op-manual-renewal')});
  }
 });
 root.addEventListener('change',root._orionPlansChange=event=>{
  if(!event.target.matches('[data-op-only-differences]'))return;
  const only=event.target.checked;
  root.dataset.opCompareOnly=String(only);
  applyComparisonFilter(root,only);
 });
}
function unmount(){
 clearInterval(mounted?.trialRefresh);
 mounted?.closeCheckout?.();
 if(mounted?.root?._orionPlansClick)mounted.root.removeEventListener('click',mounted.root._orionPlansClick);
 if(mounted?.root)delete mounted.root._orionPlansClick;
 if(mounted?.root?._orionPlansChange)mounted.root.removeEventListener('change',mounted.root._orionPlansChange);
 if(mounted?.root)delete mounted.root._orionPlansChange;
 mounted=null;
}
window.OrionPlans=Object.freeze({mount,unmount});
})();
