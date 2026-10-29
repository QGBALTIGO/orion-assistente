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
   +(same?'<span class="op-plan-caption">Sua assinatura verificada</span>':'<span class="op-plan-caption">Pagamento protegido. Confirmação após validação.</span>')+'</div></article>';
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

const checkoutPrices={essencial:2990,profissional:8990,elite:13990};
const checkoutNames={essencial:'Essencial',profissional:'Profissional',elite:'Elite'};
let checkoutSDKPromise=null;

function loadCheckoutSDK(){
 if(window.Cakto?.CaktoSDK)return Promise.resolve(window.Cakto.CaktoSDK);
 if(checkoutSDKPromise)return checkoutSDKPromise;
 checkoutSDKPromise=new Promise((resolve,reject)=>{
  const script=document.createElement('script');
  script.src='https://cakto-sdk.pages.dev/cakto-sdk.min.js';
  script.async=true;
  script.referrerPolicy='no-referrer';
  script.onload=()=>window.Cakto?.CaktoSDK?resolve(window.Cakto.CaktoSDK):reject(Error('Tokenização segura indisponível.'));
  script.onerror=()=>reject(Error('Não foi possível carregar o sistema seguro de cartão.'));
  document.head.appendChild(script);
 }).catch(error=>{checkoutSDKPromise=null;throw error;});
 return checkoutSDKPromise;
}
function checkoutField(name,label,type='text',attributes=''){
 return '<label class="op-payment-field"><span>'+label+'</span><input name="'+name+'" type="'+type+'" '+attributes+'></label>';
}
function renderCheckoutDialog(root,code,ctx,amount){
 const title=checkoutNames[code];
 const modal=document.createElement('dialog');
 modal.className='os-dialog op-dialog op-checkout-dialog';
 modal.setAttribute('aria-label','Assinatura Orion '+title);
 modal.innerHTML='<button type="button" class="op-dialog-close" data-op-checkout-close aria-label="Fechar">'+ic('close')+'</button>'
  +'<div class="op-payment-heading"><div class="op-payment-symbol">'+ic('shield')+'</div><div><span class="op-eyebrow">ASSINATURA ORION</span>'
  +'<h2>Finalizar assinatura</h2><p>'+escapeHTML(title)+' · Até '+({essencial:2,profissional:6,elite:10}[code])+' profissionais</p></div></div>'
  +'<div class="op-payment-summary"><span>Orion '+escapeHTML(title)+'<small>Mensalidade por empresa</small></span><strong>'+price(amount)+'<small>/mês</small></strong></div>'
  +'<div class="op-payment-banner" role="status" aria-live="polite" data-op-payment-info>Verificando a disponibilidade do pagamento…</div>'
  +'<div class="op-payment-body" data-op-payment-body hidden>'
  +'<div class="op-payment-methods" role="group" aria-label="Forma de pagamento">'
  +'<button type="button" class="op-payment-method active" data-op-payment-method="pix" aria-pressed="true">'+ic('credit')+' Pix</button>'
  +'<button type="button" class="op-payment-method" data-op-payment-method="card" aria-pressed="false">'+ic('shield')+' Cartão de crédito</button></div>'
  +'<form class="op-payment-form" data-op-payment-form novalidate>'
  +'<div class="op-payment-fields">'
  +checkoutField('name','Nome completo','text','autocomplete="name" required minlength="3" maxlength="120" placeholder="Nome e sobrenome"')
  +checkoutField('email','E-mail','email','autocomplete="email" required maxlength="254" placeholder="seuemail@exemplo.com"')
  +checkoutField('phone','Celular com DDD','tel','autocomplete="tel" inputmode="tel" required maxlength="20" placeholder="(00) 00000-0000"')
  +'<div class="op-payment-document">'
  +'<label class="op-payment-field"><span>Tipo de documento</span><select name="doc_type"><option value="cpf">CPF</option><option value="cnpj">CNPJ</option></select></label>'
  +checkoutField('doc_number','CPF ou CNPJ','text','required inputmode="numeric" minlength="11" maxlength="18" placeholder="Somente números"')+'</div></div>'
  +'<div class="op-payment-card-fields" data-op-card-fields hidden>'
  +'<div class="op-payment-card-title">'+ic('shield')+' Dados do cartão (tokenizados no navegador)</div>'
  +checkoutField('holder','Nome no cartão','text','autocomplete="cc-name" minlength="3" maxlength="120" placeholder="Como está no cartão"')
  +checkoutField('card_number','Número do cartão','text','autocomplete="cc-number" inputmode="numeric" maxlength="23" placeholder="0000 0000 0000 0000"')
  +'<div class="op-payment-card-row">'+checkoutField('expiry','Validade (MM/AA)','text','autocomplete="cc-exp" inputmode="numeric" maxlength="7" placeholder="MM/AA"')
  +checkoutField('cvv','CVV','password','autocomplete="cc-csc" inputmode="numeric" maxlength="4" placeholder="•••"')+'</div>'
  +'<p class="op-payment-subtitle">Endereço do titular para autenticação com o banco</p>'
  +'<div class="op-payment-card-row">'+checkoutField('zip','CEP','text','autocomplete="postal-code" inputmode="numeric" maxlength="9" placeholder="00000-000"')
  +checkoutField('state','UF','text','autocomplete="address-level1" maxlength="2" placeholder="MS"')+'</div>'
  +'<div class="op-payment-card-row">'+checkoutField('city','Cidade','text','autocomplete="address-level2" maxlength="70" placeholder="Cidade"')
  +checkoutField('street','Rua','text','autocomplete="address-line1" maxlength="120" placeholder="Logradouro"')+'</div>'
  +'<div class="op-payment-card-row">'+checkoutField('number','Número','text','maxlength="12" placeholder="Número"')
  +checkoutField('complement','Complemento','text','autocomplete="address-line2" maxlength="90" placeholder="Opcional"')+'</div></div>'
  +'<label class="op-payment-consent"><input type="checkbox" name="monthly_ack" required><span>Confirmo o valor de <strong>'+price(amount)+'</strong> pela assinatura mensal Orion '+escapeHTML(title)+'.</span></label>'
  +'<label class="op-payment-consent"><input type="checkbox" name="processor_ack" required><span>Autorizo o processamento seguro desta transação para pagamento e verificação.</span></label>'
  +'<div class="op-payment-error" data-op-payment-error role="alert" aria-live="polite"></div>'
  +'<button type="submit" class="op-btn op-btn-primary op-payment-submit" data-op-payment-submit>Gerar Pix</button>'
  +'<p class="op-payment-secure">'+ic('shield')+' A assinatura é confirmada somente após verificação bancária. Seus dados de cartão não são armazenados pelo Orion.</p>'
  +'</form></div><div class="op-payment-result" data-op-payment-result hidden></div>';
 root.appendChild(modal);
 modal.showModal();
 modal.querySelector('[name=name]').value=ctx.user?.name||'';
 modal.querySelector('[name=email]').value=ctx.user?.email||'';
 modal.querySelector('[name=phone]').value=window.OrionFields?.phoneDisplay(ctx.user?.phone||ctx.tenant?.contact_phone||'')||'';
 return modal;
}
async function checkout(root,code,ctx){
 const action=root.querySelector('[data-op-checkout="'+code+'"]');
 if(!action||action.disabled||root.dataset.busy==='true'||!checkoutPrices[code])return;
 if(root.querySelector('.op-checkout-dialog'))return;
 const modal=renderCheckoutDialog(root,code,ctx,checkoutPrices[code]);
 const status=modal.querySelector('[data-op-payment-info]');
 const body=modal.querySelector('[data-op-payment-body]');
 const feedback=modal.querySelector('[data-op-payment-error]');
 const resultBox=modal.querySelector('[data-op-payment-result]');
 const form=modal.querySelector('[data-op-payment-form]');
 const submit=modal.querySelector('[data-op-payment-submit]');
 let method='pix',busy=false,finished=false,config=null,intent=null,ticker=null;
 const close=()=>{
  if(busy)return;
  if(ticker)window.clearInterval(ticker);
  modal.close();modal.remove();
  action.focus({preventScroll:true});
 };
 modal.addEventListener('click',event=>{
  if(event.target.closest('[data-op-checkout-close]')){event.preventDefault();close();return;}
  const methodButton=event.target.closest('[data-op-payment-method]');
  if(methodButton&&!busy){
   method=methodButton.dataset.opPaymentMethod;
   modal.querySelectorAll('[data-op-payment-method]').forEach(button=>{
    button.classList.toggle('active',button===methodButton);
    button.setAttribute('aria-pressed',String(button===methodButton));
   });
   const card=modal.querySelector('[data-op-card-fields]');
   card.hidden=method!=='card';
   card.querySelectorAll('input').forEach(input=>{input.disabled=method!=='card';input.required=method==='card'&&input.name!=='complement';});
   submit.textContent=method==='pix'?'Gerar Pix':'Pagar com cartão';
  }
  const copy=event.target.closest('[data-op-copy-pix]');
  if(copy){
   event.preventDefault();
   const text=resultBox.querySelector('[data-op-pix-code]')?.value;
   if(!text)return;
   if(navigator.clipboard?.writeText){
    navigator.clipboard.writeText(text).then(()=>{copy.textContent='Copiado!';}).catch(()=>{
     resultBox.querySelector('[data-op-pix-code]').select();
    });
   }else resultBox.querySelector('[data-op-pix-code]').select();
  }
  if(event.target.closest('[data-op-check-status]')){event.preventDefault();void pollStatus();}
 });
 modal.addEventListener('cancel',event=>{event.preventDefault();close();});
 modal.querySelector('[data-op-card-fields]').querySelectorAll('input').forEach(el=>{el.disabled=true;});
 const pollStatus=async()=>{
  if(!intent||finished||!modal.isConnected)return;
  try{
   const answer=await ctx.request('/plan-direct-status?intent_id='+encodeURIComponent(intent.intent_id));
   if(!modal.isConnected)return;
   if(answer.status==='active'){
    finished=true;if(ticker)window.clearInterval(ticker);
    resultBox.innerHTML='<div class="op-payment-success">'+ic('check')+'<h3>Assinatura confirmada</h3><p>Seu acesso está sendo atualizado.</p></div>';
    const data=await ctx.request('/plan-overview');
    if(modal.isConnected){close();render(root,data,ctx);}
   }else if(answer.status==='review_required'){
    if(ticker)window.clearInterval(ticker);
    status.textContent='Pagamento em verificação. Não tente pagar novamente enquanto conferimos sua solicitação.';
   }else if(answer.status==='declined'){
    if(ticker)window.clearInterval(ticker);
    status.textContent='Pagamento recusado. Entre em contato com o suporte antes de outra tentativa.';
   }else status.textContent='Aguardando confirmação do pagamento.';
  }catch{status.textContent='Aguardando confirmação. Você pode verificar novamente pelo botão abaixo.';}
 };
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(busy||finished||!config?.available)return;
  feedback.textContent='';
  if(!form.reportValidity())return;
  const v=name=>form.elements.namedItem(name).value.trim();
  let cardToken=null,threeDSecure=null,antifraud_reference=null;
  busy=true;submit.disabled=true;modal.querySelectorAll('[data-op-payment-method]').forEach(b=>b.disabled=true);
  status.textContent='Preparando seu pagamento seguro…';
  try{
   // All network calls are scoped to the logged-in business and verified owner.
   const prepared=await ctx.request('/plan-direct-prepare','POST',{plan_code:code});
   if(prepared.plan_code!==code||prepared.monthly_cents!==checkoutPrices[code]||prepared.status!=='ready')throw Error('Não foi possível confirmar o valor da assinatura.');
   intent=prepared;
   const payer={name:v('name'),email:v('email'),phone:v('phone'),doc_type:v('doc_type'),
    doc_number:v('doc_number'),fingerprint:'orion_'+(window.crypto?.randomUUID?.()||String(Date.now())+'_checkout_012345')};
   if(method==='card'){
    status.textContent='Validando o cartão com segurança…';
    const SDK=await loadCheckoutSDK();
    const sdk=new SDK({client_id:config.sdk_client_id});
    await sdk.initAntifraud();
    const expiry=v('expiry').split('/');
    if(expiry.length!==2||!/^(0[1-9]|1[0-2])$/.test(expiry[0])||!/^[0-9]{2,4}$/.test(expiry[1]))
     throw Error('Informe a validade no formato MM/AA.');
    const card={holderName:v('holder'),cardNumber:v('card_number').replace(/\D/g,''),
     cvv:v('cvv'),expMonth:expiry[0],expYear:expiry[1]};
    const token=await sdk.createToken(card);
    cardToken=token?.cardToken;
    // Sensitive inputs are cleared as soon as tokenization finishes.
    for(const name of ['card_number','cvv'])form.elements.namedItem(name).value='';
    if(!cardToken)throw Error('Não foi possível tokenizar o cartão.');
    status.textContent='Aguardando autenticação do seu banco…';
    const auth=await sdk.authenticate3DS({card,customer:{amount:checkoutPrices[code],currency:'BRL',
     email:payer.email,name:payer.name,phone:payer.phone.slice(2),paymentMethod:'credit',
     address:{street:v('street'),number:v('number'),complement:v('complement'),
      city:v('city'),state:v('state').toUpperCase(),zipcode:v('zip')}}});
    if(!auth?.success)throw Error('A autenticação bancária não foi concluída.');
    await sdk.completeAntifraudProfile();
    antifraud_reference=sdk.getAntifraudReference();
    threeDSecure={cavv:auth.cavv,eci:auth.eci,xid:auth.xid,
     referenceId:auth.referenceId,version:auth.version};
   }
   status.textContent=method==='pix'?'Gerando código Pix…':'Encaminhando pagamento protegido…';
   const payload={intent_id:prepared.intent_id,method,payer,
    confirmed_monthly:true,consent_to_processor:true,
    card_token:cardToken,antifraud_reference,three_ds:threeDSecure};
   const result=await ctx.request('/plan-direct-charge','POST',payload);
   if(!modal.isConnected)return;
   if(result.intent_id!==prepared.intent_id||result.status!=='pending_confirmation')
    throw Error('Pagamento em análise. Consulte o suporte antes de tentar novamente.');
   form.hidden=true;body.hidden=true;
   resultBox.hidden=false;
   if(method==='pix'&&result.pix_code){
    resultBox.innerHTML='<div class="op-payment-pix"><span class="op-eyebrow">PIX GERADO</span><h3>Pague usando seu banco</h3>'
     +'<p>Escaneie o QR Code ou copie o código Pix abaixo.</p>'
     +(String(result.pix_qr_data_uri||'').startsWith('data:image/png;base64,')?'<img class="op-payment-qr" alt="QR Code Pix do seu pagamento" src="'+escapeHTML(result.pix_qr_data_uri)+'">':'')
     +'<label class="op-payment-field"><span>Pix copia e cola</span><textarea data-op-pix-code readonly rows="3">'+escapeHTML(result.pix_code)+'</textarea></label>'
     +'<button type="button" class="op-btn op-btn-primary" data-op-copy-pix>Copiar código Pix</button>'
     +'<button type="button" class="op-btn op-btn-secondary" data-op-check-status>Verificar pagamento</button></div>';
   }else{
    resultBox.innerHTML='<div class="op-payment-pending">'+ic('shield')+'<h3>Pagamento em análise</h3>'
      +'<p>A confirmação será exibida no Orion após validação bancária.</p>'
      +'<button type="button" class="op-btn op-btn-secondary" data-op-check-status>Verificar pagamento</button></div>';
   }
   status.textContent='Aguardando confirmação do pagamento.';
   // Keep the result visible; do not start or grant subscriptions on the client.
   ticker=window.setInterval(()=>{if(!document.hidden)void pollStatus();},12000);
  }catch(error){
   if(!modal.isConnected)return;
   feedback.textContent=error.message||'Não foi possível preparar o pagamento.';
   status.textContent='Pagamento não concluído.';
   // A timed-out provider call may already have created a charge. Do not enable retry.
   if(intent&&/não gere outra cobrança|revisão|em análise|não repita/i.test(feedback.textContent)){
    submit.disabled=true;modal.dataset.paymentLocked='true';
   }
  }finally{
   busy=false;
   if(modal.isConnected&&!modal.dataset.paymentLocked){
    submit.disabled=finished||form.hidden;
    modal.querySelectorAll('[data-op-payment-method]').forEach(b=>b.disabled=form.hidden);
   }
  }
 });
 try{
  config=await ctx.request('/plan-direct-config');
  if(!modal.isConnected)return;
  if(!config?.available||!config.methods?.includes('pix')||!config.methods?.includes('card')){
   status.textContent='A contratação integrada está temporariamente indisponível. Nenhuma cobrança será feita.';
   return;
  }
  status.textContent='Escolha como deseja pagar. A confirmação ocorrerá após verificação.';
  body.hidden=false;
 }catch{
  if(modal.isConnected)status.textContent='Não foi possível verificar o pagamento integrado. Nenhuma cobrança foi criada.';
 }
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