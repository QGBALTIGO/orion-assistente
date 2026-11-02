/* Orion professional workspace: no company-wide management APIs or selectable professional IDs. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const state={csrf:'',profile:null,page:'inicio',day:'',month:'',appointments:[],blocks:[],modal:null,epoch:0,loading:false};
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const money=cents=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(cents||0)/100);
const fmt=(value,option={})=>new Intl.DateTimeFormat('pt-BR',{timeZone:state.profile?.timezone||'America/Campo_Grande',...option}).format(new Date(value));
const shortTime=value=>fmt(value,{hour:'2-digit',minute:'2-digit'});
const shortDate=value=>fmt(value,{day:'2-digit',month:'short'});
const statuses={confirmed:'Confirmado',completed:'Concluído',cancelled:'Cancelado',no_show:'Não compareceu',hold:'Reservado'};
const stateClass=value=>['confirmed','completed','cancelled'].includes(value)?value:'';
let noticeTimer;
const notice=(message)=>{const n=$('toast');n.textContent=message;n.hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>n.hidden=true,4500)};
async function api(path,method='GET',body){
 const r=await fetch(path,{method,credentials:'same-origin',cache:'no-store',
   headers:{'Accept':'application/json',...(method==='GET'?{}:{'Content-Type':'application/json','X-Orion-CSRF':state.csrf})},
   ...(body===undefined?{}:{body:JSON.stringify(body)})});
 const result=await r.json().catch(()=>({}));
 if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:result.detail?.message||'Não foi possível concluir esta ação.');
 return result;
}
function intro(title,description,eyebrow='MINHA ÁREA'){return '<div class="intro"><div class="eyebrow">'+esc(eyebrow)+'</div><h1>'+esc(title)+'</h1><p>'+esc(description)+'</p></div>'}
const card=(title,value,note='')=>'<section class="card"><p class="card-label">'+esc(title)+'</p><div class="card-value">'+esc(value)+'</div><small>'+esc(note)+'</small></section>';
function updateNav(){
 document.querySelectorAll('[data-page]').forEach(el=>{const selected=el.dataset.page===state.page;if(selected)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current')});
}
function appointmentsHtml(list){
 if(!list.length)return '<div class="empty">Não há atendimentos nesta data.</div>';
 return '<div class="appointments">'+list.map(a=>{
 const finished=a.status!=='confirmed';
 const allowed=a.status==='confirmed'&&new Date(a.start_at).getTime()<=Date.now();
 const action=finished?'':('<div class="actions">'+
   '<button class="ghost-button small" type="button" data-action="reschedule" data-id="'+esc(a.id)+'">Remarcar</button>'+
   '<button class="ghost-button small danger" type="button" data-action="cancel" data-id="'+esc(a.id)+'">Cancelar</button>'+
   (allowed?'<button class="button small" type="button" data-action="complete" data-id="'+esc(a.id)+'">Concluir</button><button class="ghost-button small" type="button" data-action="no-show" data-id="'+esc(a.id)+'">Falta</button>':'')+
   '</div>');
 return '<article class="appointment"><div class="hour">'+esc(shortTime(a.start_at))+'<small>'+esc(shortTime(a.end_at))+'</small></div>'+
   '<div><strong>'+esc(a.customer_name||'Cliente')+'</strong><p>'+esc((a.services||[]).map(s=>s.name).join(' • ')||'Atendimento')+'</p>'+
   '<span class="state '+stateClass(a.status)+'">'+esc(statuses[a.status]||a.status)+'</span></div>'+action+'</article>';
 }).join('')+'</div>';
}
async function changePage(page){
 if(!['inicio','agenda','financeiro','metas','perfil'].includes(page))return;
 state.page=page;updateNav();
 const epoch=++state.epoch;$('content').innerHTML='<div class="notice" role="status">Carregando informações…</div>';
 try{
   if(page==='inicio'){
    const [summary,agenda]=await Promise.all([api('/api/v1/professional/dashboard'),api('/api/v1/professional/agenda')]);
    if(epoch!==state.epoch)return;
    state.day=String(summary.date);state.appointments=agenda.appointments;state.blocks=agenda.blocks;
    const t=summary.today;
    $('content').innerHTML=intro('Olá, '+(summary.profile.name||'profissional').split(/\s+/)[0]+'!','Veja o que está programado para seu dia.')+
       '<div class="cards">'+card('Confirmados hoje',t.confirmed,'Agendamentos do dia')+
       card('Concluídos hoje',t.completed,'Atendimentos realizados')+
       card('Receita gerada hoje',money(t.revenue_cents),'Comissões: '+money(t.commission_cents))+'</div>'+
       '<div class="section-heading"><h2>Meus atendimentos</h2><button type="button" class="ghost-button small" data-page="agenda">Abrir agenda</button></div>'+
       appointmentsHtml(agenda.appointments);
   }else if(page==='agenda'){
    const today=state.day||new Intl.DateTimeFormat('en-CA',{timeZone:state.profile.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
    const agenda=await api('/api/v1/professional/agenda?day='+encodeURIComponent(today));
    if(epoch!==state.epoch)return;state.day=String(agenda.date);state.appointments=agenda.appointments;state.blocks=agenda.blocks;
    $('content').innerHTML=intro('Minha agenda','Seus atendimentos, horários e bloqueios.')+
      '<div class="page-controls"><div class="field" style="margin:0"><label for="agendaDate">Dia</label><input type="date" id="agendaDate" value="'+esc(state.day)+'"></div>'+
      '<div class="actions"><button type="button" class="button" data-action="new-appointment">Novo agendamento</button>'+
      '<button type="button" class="ghost-button" data-action="new-block">Bloquear horário</button></div></div>'+
      '<section class="card" style="margin-bottom:18px"><div class="section-heading"><h2>Horários bloqueados</h2></div>'+
      (state.blocks.length?'<div class="blocks">'+state.blocks.map(b=>'<div class="block">'+esc(shortTime(b.starts_at))+' às '+esc(shortTime(b.ends_at))+
       ' <button type="button" data-action="remove-block" data-id="'+esc(b.id)+'" aria-label="Remover bloqueio">×</button></div>').join('')+'</div>':
       '<p class="muted">Nenhum bloqueio neste dia.</p>')+'</section>'+
       '<div class="section-heading"><h2>Atendimentos</h2><span class="muted">'+state.appointments.length+' registro(s)</span></div>'+
       appointmentsHtml(state.appointments);
   }else if(page==='financeiro'){
    const selected=state.month||state.day.slice(0,7);
    const report=await api('/api/v1/professional/finance?month='+encodeURIComponent(selected));
    if(epoch!==state.epoch)return;state.month=selected;
    const s=report.summary;
    $('content').innerHTML=intro('Meu financeiro','Somente sua produção, comissões e valores registrados pela empresa.')+
     '<div class="page-controls"><label class="field" style="margin:0">Período <input type="month" id="financeMonth" value="'+esc(selected)+'"></label></div>'+
     '<div class="cards">'+card('Receita gerada',money(s.revenue_cents),'Serviços concluídos no período')+
       card('Comissões do período',money(s.earned_cents),'Valor registrado nos atendimentos')+
       card('Saldo de comissões',money(s.balance_cents),'Saldo histórico após pagamentos e adiantamentos')+
       card('Pagamentos',money(s.payments_cents),'Registros deste mês')+
       card('Adiantamentos',money(s.advances_cents),'Registros deste mês')+
       card('A receber',money(s.payable_cents),'Saldo líquido não negativo')+'</div>'+
       '<p class="notice">Os valores são apenas para consulta. Comissões, pagamentos e adiantamentos são administrados pelo proprietário.</p>';
   }else if(page==='metas'){
    const result=await api('/api/v1/professional/goals');
    if(epoch!==state.epoch)return;
    $('content').innerHTML=intro('Minhas metas','Acompanhe os objetivos definidos pela sua empresa.')+
      '<section class="card"><h2>Objetivos individuais</h2>'+
      (result.items.length?result.items.map(g=>{
       const revenue=g.metric==='revenue_cents';
       const percent=Number(g.progress_percent||0);
       return '<div class="goal"><div class="goal-top"><strong>'+(revenue?'Meta de faturamento':'Atendimentos')+'</strong><strong>'+esc(percent.toFixed(0))+'%</strong></div>'+
       '<progress max="100" value="'+Math.min(100,Math.max(0,percent))+'" aria-label="Progresso da meta"></progress>'+
       '<p>'+esc(revenue?money(g.actual_value)+' de '+money(g.target_value):g.actual_value+' de '+g.target_value+' atendimentos')+
       ' · '+esc(String(g.start_date).slice(0,10))+' a '+esc(String(g.end_date).slice(0,10))+'</p></div>';
      }).join(''):'<div class="empty">Sua empresa ainda não definiu metas individuais para este período.</div>')+
      '</section>';
   }else{
    $('content').innerHTML=intro('Meu perfil','Identificação e vínculo profissional no Orion.')+
      '<section class="card"><h2>'+esc(state.profile.name)+'</h2><p class="muted">'+esc(state.profile.specialty||'Profissional')+'</p>'+
      '<p><strong>Empresa:</strong> '+esc(state.profile.business_name)+'</p>'+
      '<p><strong>Acesso:</strong> Profissional individual</p>'+
      '<p class="notice">Sua empresa gerencia seus dados operacionais e as permissões de acesso. Para alterar e-mail, comissões ou expediente regular, solicite ao proprietário.</p></section>';
   }
 }catch(error){if(epoch===state.epoch)$('content').innerHTML=intro('Não foi possível carregar','Confira sua conexão e tente novamente.')+
   '<section class="card"><p class="form-error">'+esc(error.message)+'</p><button type="button" class="button" data-action="retry">Tentar novamente</button></section>';}
}
function inputField(label,name,type='text',value='',extra=''){
 return '<div class="field"><label for="f_'+name+'">'+esc(label)+'</label><input id="f_'+name+'" name="'+name+'" type="'+type+'" value="'+esc(value)+'" '+extra+'></div>';
}
function openModal(action,record){
 const modal=$('modal');state.modal={action,record,key:crypto.randomUUID()};
 const recordKey=state.modal.key;
 const titles={'new-appointment':'Novo agendamento','new-block':'Bloquear horário','remove-block':'Remover bloqueio',cancel:'Cancelar agendamento',reschedule:'Remarcar agendamento',complete:'Concluir atendimento','no-show':'Registrar ausência'};
 $('modalTitle').textContent=titles[action]||'Confirmar';$('modalError').textContent='';
 let fields='';
 if(action==='new-appointment'){
   fields='<p class="muted">Agende somente para seus clientes e horários disponíveis.</p>'+
     inputField('Nome do cliente','customer_name','text','','required maxlength="120" minlength="2"')+
     inputField('WhatsApp do cliente','customer_phone','tel','','required placeholder="(67) 99999-9999"')+
     inputField('Data','day','date',state.day,'required')+
     '<fieldset class="services-select"><legend>Serviços (até 6)</legend><div id="modalServices" class="muted">Carregando serviços…</div></fieldset>'+
     '<div id="modalSlots" class="slots-list" aria-live="polite"></div>';
 }else if(action==='new-block'){
   fields='<p class="muted">O bloqueio será aplicado somente à sua agenda.</p>'+
     inputField('Dia','day','date',state.day,'required')+
     '<div class="field-row">'+inputField('Início','start_time','time','','required')+
     inputField('Fim','end_time','time','','required')+'</div>'+
     inputField('Motivo','reason','text','Bloqueio de agenda','maxlength="120" required');
 }else if(action==='reschedule'){
   fields='<p class="muted">Seu atendimento permanece com você. Escolha um horário livre da sua agenda.</p>'+
     inputField('Nova data','day','date',state.day,'required')+
     inputField('Novo horário','clock','time','','required');
 }else if(action==='cancel'){
   fields='<p class="muted">Cancelar este atendimento? A operação será registrada no histórico.</p>'+
     inputField('Motivo (opcional)','reason','text','','maxlength="1000"');
 }else if(action==='complete'&&record?.price_mode==='from'){
   fields='<p class="muted">Informe o preço final combinado com o cliente.</p>'+
      inputField('Valor final (R$)','final_total','number',Number(record.total_cents||0)/100,'required step="0.01" min="0"');
 }else if(action==='remove-block'){
   fields='<p>Deseja remover este bloqueio da sua agenda?</p>';
 }else{
   fields='<p>Confirma esta operação no seu atendimento?</p>';
 }
 $('modalFields').innerHTML=fields;modal.hidden=false;
 $('modalSubmit').textContent=action==='new-appointment'?'Consultar horários':action==='remove-block'?'Remover':'Confirmar';
 $('modalSubmit').disabled=false;$('modalForm').querySelector('input')?.focus();
 if(action==='new-appointment'){
   api('/api/v1/professional/services').then(result=>{
     if($('modal').hidden||state.modal?.key!==recordKey)return;
     const items=Array.isArray(result.items)?result.items:[];
     $('modalServices').innerHTML=items.length?items.map(item=>
       '<label class="service-choice"><input type="checkbox" name="service_ids" value="'+esc(item.id)+'">'+
       '<span><strong>'+esc(item.name)+'</strong><small>'+esc(item.duration_min)+' min · '+money(item.price_cents)+'</small></span></label>'
     ).join(''):'<p>Não há serviços vinculados ao seu cadastro. Fale com o proprietário.</p>';
   }).catch(err=>{if(state.modal?.key===recordKey)$('modalServices').textContent=err.message});
 }
}
function closeModal(){
 $('modal').hidden=true;state.modal=null;
}
async function submitModal(event){
 event.preventDefault();
 const d=state.modal;if(!d)return;
 const form=$('modalForm');if(!form.reportValidity())return;
 const val=Object.fromEntries(new FormData(form));const base='/api/v1/professional';
 const button=$('modalSubmit');button.disabled=true;$('modalError').textContent='';
 try{
   if(d.action==='new-appointment'){
     const service_ids=[...form.querySelectorAll('[name=service_ids]:checked')].map(e=>e.value);
     if(!service_ids.length||service_ids.length>6)throw Error('Selecione de um a seis serviços.');
     const keys=[val.day,service_ids.join('|'),val.customer_name,val.customer_phone].join('~');
     const slotsNode=$('modalSlots');
     if(d.checkedKey!==keys||!d.slots){
       const params=new URLSearchParams({day:val.day});
       service_ids.forEach(id=>params.append('service_ids',id));
       const available=await api(base+'/availability?'+params.toString());
       if(!available.slots?.length)throw Error('Não há horários disponíveis para esses serviços nesta data.');
       d.checkedKey=keys;d.slots=available.slots;
       slotsNode.innerHTML='<p class="muted">Selecione um horário disponível:</p><div class="slot-grid">'+
         available.slots.map(slot=>'<label class="slot-choice"><input required type="radio" name="selected_slot" value="'+esc(slot)+'">'+
           '<span>'+esc(shortTime(slot))+'</span></label>').join('')+'</div>';
       button.textContent='Confirmar agendamento';
       button.disabled=false;
       return;
     }
     const slot=form.querySelector('[name=selected_slot]:checked')?.value;
     if(!slot||!d.slots.includes(slot))throw Error('Selecione um horário válido.');
     const onlyDigits=(val.customer_phone||'').replace(/\D/g,'');
     const international=onlyDigits.startsWith('55')&&[12,13].includes(onlyDigits.length)?'+'+onlyDigits:
       [10,11].includes(onlyDigits.length)?'+55'+onlyDigits:
       onlyDigits.startsWith('5959')&&onlyDigits.length===12?'+'+onlyDigits:null;
     if(!international)throw Error('Informe um WhatsApp válido com DDD.');
     await api(base+'/appointments','POST',{
       service_ids,customer_name:val.customer_name,customer_phone:international,
       start_at:slot,idempotency_key:d.key,
     });
   }else if(d.action==='new-block'){
     await api(base+'/blocks','POST',{day:val.day,start_time:val.start_time,end_time:val.end_time,reason:val.reason,idempotency_key:d.key});
   }else if(d.action==='remove-block'){
     await api(base+'/blocks/'+encodeURIComponent(d.record.id),'DELETE');
   }else{
     const url=base+'/appointments/'+encodeURIComponent(d.record.id);
     if(d.action==='cancel'){
       await api(url+'/cancel','POST',{expected_updated_at:d.record.updated_at,idempotency_key:d.key,reason:val.reason||null});
     }else if(d.action==='reschedule'){
       await api(url+'/reschedule','POST',{expected_updated_at:d.record.updated_at,idempotency_key:d.key,day:val.day,clock:val.clock});
     }else{
       const variable=d.action==='complete'&&d.record.price_mode==='from';
       const value=variable?Math.round(Number(val.final_total)*100):null;
       if(variable&&(!Number.isSafeInteger(value)||value<0))throw Error('Valor final inválido');
       await api(url+'/'+d.action,'POST',{expected_updated_at:d.record.updated_at,
         expected_status:'confirmed',idempotency_key:d.key,final_total_cents:value});
     }
   }
   closeModal();notice('Alteração registrada com sucesso.');await changePage(state.page);
 }catch(error){$('modalError').textContent=error.message;button.disabled=false;}
}
async function boot(){
 try{
   const auth=await api('/auth/me');
   if(!auth?.authenticated||auth.role!=='member'||!auth.csrf)throw Error('Entre na sua conta profissional para continuar.');
   state.csrf=auth.csrf;
   state.profile=await api('/api/v1/professional/me');
   $('companyName').textContent=state.profile.business_name;
   $('boot').hidden=true;$('portal').hidden=false;
   const first=await api('/api/v1/professional/dashboard');
   state.day=String(first.date);state.month=state.day.slice(0,7);
   await changePage('inicio');
 }catch(error){
   $('boot').innerHTML='<div class="card"><h1>Acesso profissional</h1><p>'+esc(error.message)+'</p><a class="button" href="/painel">Entrar no Orion</a></div>';
 }
}
document.addEventListener('click',async event=>{
 const page=event.target.closest('[data-page]')?.dataset.page;
 if(page){event.preventDefault();await changePage(page);return;}
 const action=event.target.closest('[data-action]')?.dataset.action;
 if(!action)return;
 if(action==='retry'){await changePage(state.page);return;}
 const id=event.target.closest('[data-id]')?.dataset.id;
 const record=action==='remove-block'?state.blocks.find(b=>b.id===id):state.appointments.find(a=>a.id===id);
 if(!['new-block','new-appointment'].includes(action)&&!record)return;
 openModal(action,record);
});
$('modalClose').addEventListener('click',closeModal);$('modalCancel').addEventListener('click',closeModal);
$('modal').addEventListener('click',event=>{if(event.target===$('modal'))closeModal()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('modal').hidden)closeModal()});
$('modalForm').addEventListener('submit',submitModal);
document.addEventListener('change',event=>{
 if(event.target.id==='agendaDate'){state.day=event.target.value;void changePage('agenda')}
 if(event.target.id==='financeMonth'){state.month=event.target.value;void changePage('financeiro')}
});
$('logoutButton').addEventListener('click',async()=>{
 try{await api('/auth/logout','POST',{})}catch{}
 location.assign('/painel');
});
function theme(value){
 document.documentElement.dataset.theme=value;
 $('themeButton').textContent=value==='dark'?'☀':'☾';
 $('brandLogo').src='/brand/orion-brand-'+(value==='dark'?'dark':'light')+'.svg';
}
try{const saved=localStorage.getItem('orion-professional-theme');theme(saved==='dark'?'dark':'light')}catch{theme('light')}
$('themeButton').addEventListener('click',()=>{
 const next=document.documentElement.dataset.theme==='dark'?'light':'dark';
 theme(next);try{localStorage.setItem('orion-professional-theme',next)}catch{}
});
void boot();
})();
