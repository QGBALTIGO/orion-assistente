/* Orion professional workspace: no company-wide management APIs or selectable professional IDs. */
(()=>{'use strict';
const $=id=>document.getElementById(id);
const sharedFields=window.OrionFields;
sharedFields?.bind(document);
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
 if(!r.ok){const error=Error(typeof result.detail==='string'?result.detail:result.detail?.message||'Não foi possível concluir esta ação.');error.status=r.status;error.detail=result.detail;throw error;}
 return result;
}
function currentDay(){return new Intl.DateTimeFormat('en-CA',{timeZone:state.profile?.timezone||'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());}
function localStart(day,clock){
 const wall=Date.parse(day+'T'+clock+':00Z');let utc=wall;
 for(let i=0;i<3;i++){
   const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:state.profile.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(utc)).map(p=>[p.type,p.value]));
   const correction=wall-Date.UTC(+parts.year,+parts.month-1,+parts.day,+parts.hour,+parts.minute,+parts.second);utc+=correction;if(!correction)break;
 }
 return new Date(utc).toISOString();
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
   (a.allowed_actions?.includes('add_services')?'<button class="ghost-button small" type="button" data-action="extras" data-id="'+esc(a.id)+'">Adicionar serviços</button>':'')+
   '<button class="ghost-button small" type="button" data-action="reschedule" data-id="'+esc(a.id)+'">Remarcar</button>'+
   '<button class="ghost-button small danger" type="button" data-action="cancel" data-id="'+esc(a.id)+'">Cancelar</button>'+
   (allowed?'<button class="button small" type="button" data-action="complete" data-id="'+esc(a.id)+'">Concluir</button><button class="ghost-button small" type="button" data-action="no-show" data-id="'+esc(a.id)+'">Falta</button>':'')+
   '</div>');
 return '<article class="appointment"><div class="hour">'+esc(shortTime(a.start_at))+'<small>'+esc(shortTime(a.end_at))+'</small></div>'+
   '<div><strong>'+esc(a.customer_name||'Cliente')+'</strong><p>'+esc((a.items||a.services||[]).map(item=>(item.name_snapshot||item.name)+(item.item_origin==='extra'?' (extra)':'')).join(' • ')||'Atendimento')+'</p>'+
   '<p>'+esc((a.price_mode==='from'&&a.status!=='completed'?'A partir de ':'')+money(a.total_cents))+'</p>'+
   (a.booking_mode==='exceptional'?'<small class="muted">Horário excepcional</small>':a.booking_mode==='retroactive'?'<small class="muted">Registrado depois'+(a.created_at?' · '+esc(shortDate(a.created_at)):'')+'</small>':'')+
   (a.actual_duration_min?'<small class="muted appointment-duration">Duração realizada: '+Number(a.actual_duration_min)+' min</small>':'')+
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
       (b.can_remove?' <button type="button" data-action="remove-block" data-id="'+esc(b.id)+'" aria-label="Remover bloqueio">×</button>':
        '<small class="muted">Bloqueio da empresa</small>')+'</div>').join('')+'</div>':
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
 const titles={'new-appointment':'Novo agendamento','new-block':'Bloquear horário','remove-block':'Remover bloqueio',extras:'Adicionar serviços',cancel:'Cancelar agendamento',reschedule:'Remarcar agendamento',complete:'Concluir atendimento','no-show':'Registrar ausência'};
 $('modalTitle').textContent=titles[action]||'Confirmar';$('modalError').textContent='';
 let fields='';
 if(action==='new-appointment'){
   fields='<div class="field"><label for="f_booking_mode">Tipo de registro</label><select id="f_booking_mode" name="booking_mode"><option value="regular">Agendamento normal</option><option value="exceptional">Horário excepcional</option><option value="retroactive">Registrar atendimento realizado</option></select></div>'+
     '<p id="modalBookingHelp" class="muted">Escolha um horário disponível na sua agenda.</p>'+
     inputField('Nome do cliente','customer_name','text','','required maxlength="120" minlength="2"')+
     '<div id="modalWalkIn" hidden><label class="service-choice"><input type="checkbox" name="walk_in"><span>Cliente avulso, sem WhatsApp</span></label></div>'+
     '<div id="modalBookingPhone">'+inputField('WhatsApp do cliente','customer_phone','tel','',
        'required placeholder="(67) 99999-9999" autocomplete="tel-national" inputmode="tel" data-orion-field="phone" data-orion-allow-international="true"')+'</div>'+
     '<div id="modalBookingMessages"><label class="service-choice"><input type="checkbox" name="appointment_messages_opt_in"><span>O cliente autorizou mensagens deste agendamento pelo WhatsApp<small>Inclui avisos, lembrete 1 hora antes e pedido de avaliação. Marque apenas com autorização expressa do cliente; é opcional.</small></span></label></div>'+
     '<div id="modalBookingWhen">'+inputField('Data','day','date',state.day,'required')+
     '<div id="modalBookingClock" hidden>'+inputField('Horário','clock','time','','disabled')+'</div></div>'+
     '<fieldset class="services-select"><legend>Serviços (até 6)</legend><div id="modalServices" class="muted">Carregando serviços…</div></fieldset>'+
     '<div id="modalCompletedFields" hidden>'+inputField('Valor final (R$)','final_total','text','','disabled inputmode="decimal" data-orion-field="money" data-field-min="0" data-field-max="1000000000" placeholder="0,00"')+
     inputField('Duração realizada (min, opcional)','actual_duration_min','number','','disabled min="1" max="1440" step="1"')+'</div>'+
     '<div id="modalSlots" class="slots-list" aria-live="polite"></div>'+
     '<div id="modalReview" hidden aria-live="polite"></div>';
 }else if(action==='extras'){
   fields='<p class="muted" role="status">Carregando serviços disponíveis…</p>';
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
 $('modalSubmit').disabled=action==='extras';$('modalForm').querySelector('input')?.focus();
 sharedFields?.refresh($('modalFields'));
 if(action==='extras')void loadProfessionalExtras(state.modal);
 if(action==='new-appointment'){
   syncProfessionalBookingMode();
   api('/api/v1/professional/services').then(result=>{
     if($('modal').hidden||state.modal?.key!==recordKey)return;
     const items=Array.isArray(result.items)?result.items:[];
     state.modal.services=items;
     $('modalServices').innerHTML=items.length?items.map(item=>
       '<label class="service-choice"><input type="checkbox" name="service_ids" value="'+esc(item.id)+'">'+
       '<span><strong>'+esc(item.name)+'</strong><small>'+esc(item.duration_min)+' min · '+money(item.price_cents)+'</small></span></label>'
     ).join(''):'<p>Não há serviços vinculados ao seu cadastro. Fale com o proprietário.</p>';
     updateProfessionalBookingTotal();
   }).catch(err=>{if(state.modal?.key===recordKey)$('modalServices').textContent=err.message});
 }
}
function closeModal(force=false){
 if(state.modal?.submitting&&!force)return;
 $('modal').hidden=true;state.modal=null;
}
function updateProfessionalBookingTotal(){
 const d=state.modal,form=$('modalForm');if(d?.action!=='new-appointment'||d.bookingPayload)return;
 const ids=[...form.querySelectorAll('[name=service_ids]:checked')].map(input=>input.value);
 const selected=(d.services||[]).filter(item=>ids.includes(item.id));
 if(!d.finalTotalTouched&&form.elements.final_total)form.elements.final_total.value=selected.length?sharedFields.moneyDisplay(selected.reduce((sum,item)=>sum+Number(item.price_cents||0),0)):'';
}
function resetProfessionalBookingReview(){
 const d=state.modal;if(d?.action!=='new-appointment'||d.bookingPayload)return;
 d.manualReviewSignature=null;d.reviewSlot=null;
 $('modalReview').hidden=true;
 $('modalSubmit').textContent=$('modalForm').elements.booking_mode.value==='regular'?(d.slots?'Revisar agendamento':'Consultar horários'):'Revisar atendimento';
}
function syncProfessionalBookingMode(){
 const d=state.modal,form=$('modalForm');if(d?.action!=='new-appointment'||d.bookingPayload)return;
 const mode=form.elements.booking_mode.value,manual=mode!=='regular',retro=mode==='retroactive';
 $('modalBookingWhen').classList.toggle('field-row',manual);
 $('modalBookingHelp').textContent=retro?'Registre um atendimento que já terminou. Nenhum aviso ou lembrete será enviado.':manual?'Informe o horário. Os conflitos e bloqueios da sua agenda continuam sendo verificados.':'Escolha um horário disponível na sua agenda.';
 $('modalWalkIn').hidden=!manual;form.elements.walk_in.disabled=!manual;if(!manual)form.elements.walk_in.checked=false;
 const walkIn=manual&&form.elements.walk_in.checked;
 $('modalBookingPhone').hidden=walkIn;form.elements.customer_phone.disabled=walkIn;form.elements.customer_phone.required=!walkIn;
 $('modalBookingMessages').hidden=retro||walkIn;form.elements.appointment_messages_opt_in.disabled=retro||walkIn;
 if(retro||walkIn)form.elements.appointment_messages_opt_in.checked=false;
 $('modalBookingClock').hidden=!manual;form.elements.clock.disabled=!manual;form.elements.clock.required=manual;
 $('modalCompletedFields').hidden=!retro;form.elements.final_total.disabled=!retro;form.elements.final_total.required=retro;form.elements.actual_duration_min.disabled=!retro;
 form.elements.day.max=retro?currentDay():'';
 $('modalSlots').hidden=manual;if(manual){$('modalSlots').replaceChildren();d.slots=null;d.checkedKey=null;}
 resetProfessionalBookingReview();updateProfessionalBookingTotal();
}
async function submitProfessionalManual(form,d,val){
 const mode=form.elements.booking_mode.value,retro=mode==='retroactive',walkIn=form.elements.walk_in.checked;
 const service_ids=[...form.querySelectorAll('[name=service_ids]:checked')].map(input=>input.value);
 if(!service_ids.length||service_ids.length>6)throw Error('Selecione de um a seis serviços.');
 const customer_phone=walkIn?null:sharedFields?.phoneValue(val.customer_phone,{required:true,allowInternational:true});
 if(!walkIn&&!customer_phone)throw Error('Informe o WhatsApp ou marque Cliente avulso.');
 if(!val.day||!/^\d{2}:\d{2}$/.test(val.clock||''))throw Error('Informe a data e o horário do atendimento.');
 const actual=retro&&val.actual_duration_min?Number(val.actual_duration_min):null;
 if(actual!==null&&(!Number.isInteger(actual)||actual<1||actual>1440))throw Error('Informe uma duração entre 1 e 1440 minutos.');
 const payload={mode,service_ids,customer_name:val.customer_name.trim(),customer_phone,walk_in:walkIn,
   start_at:localStart(val.day,val.clock),final_total_cents:retro?sharedFields.parseMoney(val.final_total,{required:true,min:0,max:1000000000}):null,
   actual_duration_min:actual,appointment_messages_opt_in:!retro&&!walkIn&&form.elements.appointment_messages_opt_in.checked};
 const signature=JSON.stringify(payload);
 if(d.manualReviewSignature!==signature){
   d.manualReviewSignature=signature;
   const selected=(d.services||[]).filter(item=>service_ids.includes(item.id)),total=retro?payload.final_total_cents:selected.reduce((sum,item)=>sum+Number(item.price_cents||0),0);
   $('modalReview').hidden=false;
   $('modalReview').innerHTML='<div class="booking-step"><span>Revisão</span><strong>Confira o registro</strong></div><div class="booking-summary">'+
     '<div><span>Cliente</span><strong>'+esc(payload.customer_name)+(walkIn?' · Avulso':'')+'</strong></div>'+
     '<div><span>Serviços</span><strong>'+esc(selected.map(item=>item.name).join(' • '))+'</strong></div>'+
     '<div><span>Data e horário</span><strong>'+esc(shortDate(payload.start_at)+' às '+shortTime(payload.start_at))+'</strong></div>'+
     '<div><span>Valor'+(retro?' final':' previsto')+'</span><strong>'+esc(money(total))+'</strong></div>'+
     '<div><span>Situação</span><strong>'+(retro?'Concluído':'Confirmado · Excepcional')+'</strong></div></div>'+
     (retro?'<p class="notice">O atendimento ficará na data informada, com recebimento pendente no Financeiro. Não serão enviados avisos ou lembretes.</p>':'');
   $('modalSubmit').textContent=retro?'Registrar atendimento':'Confirmar agendamento';$('modalSubmit').disabled=false;return false;
 }
 d.bookingPayload={...payload,idempotency_key:d.key};d.bookingPath='/api/v1/professional/appointments/manual';d.bookingDay=val.day;
 d.successNotice=retro?'Atendimento registrado como concluído. Recebimento pendente no Financeiro.':'Agendamento excepcional registrado.';
 freezeBookingForm(form,true);
 const result=await api(d.bookingPath,'POST',d.bookingPayload);
 if(!result?.id||result.status!==(retro?'completed':'confirmed'))throw Error('Não foi possível confirmar o registro. Tente novamente para conferir a mesma solicitação.');
 return true;
}
async function loadProfessionalExtras(d){
 if(d?.action!=='extras'||!d.record?.id)return;
 const key=d.key;$('modalSubmit').disabled=true;$('modalError').textContent='';
 try{
   const data=await api('/api/v1/professional/appointments/'+encodeURIComponent(d.record.id)+'/extras-catalog');
   if(state.modal!==d||d.key!==key||$('modal').hidden)return;
   if(!data?.appointment||String(data.appointment.id)!==String(d.record.id)||!Array.isArray(data.services))throw Error('Não foi possível confirmar os serviços deste atendimento.');
   d.record=data.appointment;d.extraServices=data.services;d.extraPreview=null;d.extraPreviewSignature=null;d.extrasPayload=null;
   d.extraLimit=Math.max(0,Math.min(6,Number(data.max_items)||6)-(d.record.items||[]).length);
   const available=d.record.allowed_actions?.includes('add_services')&&d.extraLimit>0&&d.extraServices.length>0;
   const choices=(items,label)=>items.length?'<fieldset class="services-select"><legend>'+esc(label)+'</legend>'+items.map(item=>'<label class="service-choice"><input type="checkbox" name="extra_service_ids" value="'+esc(item.id)+'"><span><strong>'+esc(item.name)+'</strong><small>'+esc(money(item.price_cents))+(item.price_mode==='from'?' · A partir de':'')+' · '+(item.add_duration===false?'sem tempo extra':Number(item.duration_min)+' min')+'</small></span></label>').join('')+'</fieldset>':'';
   $('modalFields').innerHTML='<div class="booking-summary"><div><span>Cliente</span><strong>'+esc(d.record.customer_name||'Cliente')+'</strong></div><div><span>Horário reservado</span><strong>'+esc(shortTime(d.record.start_at)+' a '+shortTime(d.record.end_at))+'</strong></div><div><span>Total atual</span><strong>'+esc(money(d.record.total_cents))+'</strong></div></div>'+
     '<h3>Serviços registrados</h3><ul class="recorded-services">'+(d.record.items||[]).map(item=>'<li><span><strong>'+esc(item.name_snapshot||'Serviço')+'</strong><small>'+(item.item_origin==='extra'?'Adicionado no atendimento':'Serviço original')+'</small></span><span>'+esc(money(item.price_snapshot_cents))+'</span></li>').join('')+'</ul>'+
     (available?choices(d.extraServices.filter(item=>item.recommended),'Adicionais deste atendimento')+
       choices(d.extraServices.filter(item=>!item.recommended),d.extraServices.some(item=>item.recommended)?'Outros serviços':'Serviços disponíveis')+
       '<p class="muted">Selecione até '+d.extraLimit+' serviço'+(d.extraLimit===1?'':'s')+'. Os itens já registrados serão preservados.</p>'+
       (Date.parse(d.record.start_at)<=Date.now()?inputField('Duração realizada (min, opcional)','actual_duration_min','number','','min="1" max="1440" step="1"'):'')+
       '<div id="modalExtraPreview" class="booking-summary" aria-live="polite" hidden></div>'+
       '<div id="modalExtraConflict" class="notice" hidden><p id="modalExtraConflictMessage"></p><label class="service-choice"><input type="checkbox" name="preserve_reserved_time" disabled><span>Manter o horário reservado e registrar os extras<small>Os horários dos demais clientes permanecem como estão.</small></span></label></div>':
       '<p class="muted">'+(d.record.status!=='confirmed'?'Este atendimento não pode receber serviços extras.':d.extraLimit?'Não há outros serviços disponíveis no seu cadastro.':'Este atendimento já contém o limite de serviços.')+'</p>');
   $('modalSubmit').textContent='Conferir serviços';$('modalSubmit').disabled=!available;sharedFields?.refresh($('modalFields'));
 }catch(error){
   if(state.modal!==d)return;
   $('modalFields').innerHTML='<p class="muted">Não foi possível carregar os serviços.</p><button type="button" class="ghost-button small" data-action="extras-retry">Tentar novamente</button>';
   $('modalError').textContent=error.message;
 }
}
function resetProfessionalExtrasPreview(){
 const d=state.modal;if(d?.action!=='extras'||d.extrasPayload)return;
 d.extraPreview=null;d.extraPreviewSignature=null;
 if($('modalExtraPreview'))$('modalExtraPreview').hidden=true;
 if($('modalExtraConflict'))$('modalExtraConflict').hidden=true;
 const preserve=$('modalForm').elements.preserve_reserved_time;
 if(preserve){preserve.checked=false;preserve.required=false;preserve.disabled=true;}
 $('modalSubmit').textContent='Conferir serviços';
}
function renderProfessionalExtrasPreview(d){
 const preview=d.extraPreview,target=$('modalExtraPreview');if(!preview||!target)return;
 target.hidden=false;
 target.innerHTML='<div><span>Tempo dos serviços</span><strong>'+Number(preview.services_duration_min)+' min</strong></div>'+
   '<div><span>Horário reservado</span><strong>'+esc(shortTime(d.record.start_at)+' a '+shortTime(preview.duration_conflict?d.record.end_at:preview.new_end_at||d.record.end_at))+'</strong></div>'+
   '<div><span>Total atualizado</span><strong>'+esc(money(preview.total_cents))+'</strong></div>';
 $('modalExtraConflict').hidden=!preview.duration_conflict;
 $('modalExtraConflictMessage').textContent=preview.conflict_message||'Não é possível ampliar o horário. Você pode registrar os serviços mantendo o horário já reservado.';
 const preserve=$('modalForm').elements.preserve_reserved_time;preserve.disabled=!preview.duration_conflict;preserve.required=!!preview.duration_conflict;
 $('modalSubmit').textContent='Adicionar serviços';$('modalSubmit').disabled=false;
}
async function submitProfessionalExtras(form,d,val){
 if(!d.record.allowed_actions?.includes('add_services'))throw Error('Este atendimento não pode receber serviços extras.');
 let payload=d.extrasPayload,previewOnly=false,signature=null;
 if(!payload){
   const ids=[...form.querySelectorAll('[name=extra_service_ids]:checked')].map(input=>input.value);
   if(!ids.length||ids.length>d.extraLimit)throw Error('Selecione de 1 a '+d.extraLimit+' serviços disponíveis.');
   const actual=Date.parse(d.record.start_at)<=Date.now()&&val.actual_duration_min?Number(val.actual_duration_min):null;
   if(actual!==null&&(!Number.isInteger(actual)||actual<1||actual>1440))throw Error('Informe uma duração entre 1 e 1440 minutos.');
   const intent={service_ids:ids,expected_updated_at:d.record.updated_at,actual_duration_min:actual};
   signature=JSON.stringify(intent);previewOnly=!d.extraPreview||d.extraPreviewSignature!==signature;
   const preserve=!previewOnly&&d.extraPreview.duration_conflict&&form.elements.preserve_reserved_time.checked;
   if(!previewOnly&&d.extraPreview.duration_conflict&&!preserve)throw Error('Confirme se deseja manter o horário reservado.');
   const operation={...intent,preserve_reserved_time:preserve===true},keySignature=JSON.stringify(operation);
   if(d.extraRequestSignature!==keySignature){d.extraRequestSignature=keySignature;d.extraRequestKey=crypto.randomUUID();}
   payload={...operation,idempotency_key:d.extraRequestKey};
 }
 if(!previewOnly)d.extrasPayload=payload;
 freezeBookingForm(form,true);
 try{
   const result=await api('/api/v1/professional/appointments/'+encodeURIComponent(d.record.id)+'/extras'+(previewOnly?'/preview':''),'POST',payload);
   if(state.modal!==d)return false;
   if(previewOnly){
     if(!result?.appointment||String(result.appointment.id)!==String(d.record.id)||!Number.isFinite(result.total_cents))throw Error('Não foi possível conferir os valores deste atendimento.');
     d.extraPreview=result;d.extraPreviewSignature=signature;freezeBookingForm(form,false);renderProfessionalExtrasPreview(d);return false;
   }
   if(!result?.id||String(result.id)!==String(d.record.id))throw Error('Não foi possível confirmar a atualização. Tente novamente para conferir a mesma solicitação.');
   d.extrasPayload=null;d.successNotice=payload.preserve_reserved_time?'Serviços adicionados. O horário reservado foi mantido.':'Serviços adicionados ao atendimento.';return true;
 }catch(error){
   if(state.modal!==d)return false;
   if(previewOnly)freezeBookingForm(form,false);
   if(error.status>=400&&error.status<500&&![408,429].includes(error.status)){d.extrasPayload=null;freezeBookingForm(form,false);}
   if(error.status===409&&error.detail?.code==='extra_duration_conflict'&&d.extraPreview){
     d.extraPreview={...d.extraPreview,duration_conflict:true,conflict_message:error.message};form.elements.preserve_reserved_time.checked=false;renderProfessionalExtrasPreview(d);return false;
   }
   if(d.extrasPayload)$('modalSubmit').textContent='Verificar atualização';
   throw error;
 }
}
function freezeBookingForm(form,pending){
 form.querySelectorAll('input,select,textarea,[data-action=booking-back]').forEach(control=>{
   if(pending){if(control.dataset.bookingWasDisabled===undefined)control.dataset.bookingWasDisabled=String(control.disabled);control.disabled=true;}
   else if(control.dataset.bookingWasDisabled!==undefined){control.disabled=control.dataset.bookingWasDisabled==='true';delete control.dataset.bookingWasDisabled;}
 });
}
async function submitModal(event){
 event.preventDefault();
 const d=state.modal;if(!d||d.submitting)return;
 const form=$('modalForm');if(!form.reportValidity())return;
 const val=Object.fromEntries(new FormData(form));const base='/api/v1/professional';
 const button=$('modalSubmit');button.disabled=true;$('modalError').textContent='';d.submitting=true;form.setAttribute('aria-busy','true');
 try{
   if(d.action==='new-appointment'&&d.bookingPayload){
     const result=await api(d.bookingPath||base+'/appointments','POST',d.bookingPayload);
     if(d.bookingPath&&(!result?.id||result.status!==(d.bookingPayload.mode==='retroactive'?'completed':'confirmed')))throw Error('Não foi possível confirmar o registro. Tente novamente para conferir a mesma solicitação.');
   }else if(d.action==='new-appointment'&&form.elements.booking_mode.value!=='regular'){
     if(!await submitProfessionalManual(form,d,val))return;
   }else if(d.action==='extras'){
     if(!await submitProfessionalExtras(form,d,val))return;
   }else if(d.action==='new-appointment'){
     const service_ids=[...form.querySelectorAll('[name=service_ids]:checked')].map(e=>e.value);
     if(!service_ids.length||service_ids.length>6)throw Error('Selecione de um a seis serviços.');
     const keys=[val.day,service_ids.join('|'),val.customer_name,val.customer_phone].join('~');
     const slotsNode=$('modalSlots');
     if(d.checkedKey!==keys||!d.slots){
       d.reviewSlot=null;
       $('modalReview').hidden=true;
       $('modalSlots').hidden=false;
       const params=new URLSearchParams({day:val.day});
       service_ids.forEach(id=>params.append('service_ids',id));
       const available=await api(base+'/availability?'+params.toString());
       if(!available.slots?.length)throw Error('Não há horários disponíveis para esses serviços nesta data.');
       d.checkedKey=keys;d.slots=available.slots;
       slotsNode.innerHTML='<div class="booking-step"><span>2 de 3</span><strong>Escolha um horário livre</strong></div>'+
         '<p class="muted">Selecione um horário disponível:</p><div class="slot-grid">'+
         available.slots.map(slot=>'<label class="slot-choice"><input required type="radio" name="selected_slot" value="'+esc(slot)+'">'+
           '<span>'+esc(shortTime(slot))+'</span></label>').join('')+'</div>';
       button.textContent='Revisar agendamento';
       button.disabled=false;
       return;
     }
     const slot=form.querySelector('[name=selected_slot]:checked')?.value;
     if(!slot||!d.slots.includes(slot))throw Error('Selecione um horário válido.');
     const international=sharedFields?.phoneValue(val.customer_phone,{required:true,allowInternational:true});
     if(!international)throw Error('Informe um WhatsApp válido com DDD.');
     if(d.reviewSlot!==slot||d.reviewKey!==keys){
       const selected=(d.services||[]).filter(item=>service_ids.includes(item.id));
       const amount=selected.reduce((sum,item)=>sum+Number(item.price_cents||0),0);
       const minutes=selected.reduce((sum,item)=>sum+Number(item.duration_min||0),0);
       d.reviewSlot=slot;d.reviewKey=keys;
       $('modalSlots').hidden=true;
       const review=$('modalReview');review.hidden=false;
       review.innerHTML='<div class="booking-step"><span>3 de 3</span><strong>Revise antes de confirmar</strong></div>'+
         '<div class="booking-summary">'+
         '<div><span>Cliente</span><strong>'+esc(val.customer_name)+'</strong></div>'+
         '<div><span>Serviços</span><strong>'+esc(selected.map(item=>item.name).join(' • '))+'</strong></div>'+
         '<div><span>Data e horário</span><strong>'+esc(shortDate(slot)+' às '+shortTime(slot))+'</strong></div>'+
         '<div><span>Duração</span><strong>'+esc(minutes+' min')+'</strong></div>'+
         '<div><span>Valor mínimo</span><strong>'+esc(money(amount))+
         (selected.some(item=>item.price_mode==='from')?' · A partir de':'')+'</strong></div></div>'+
         '<button type="button" class="ghost-button small" data-action="booking-back">Alterar horário</button>';
       button.textContent='Confirmar agendamento';
       button.disabled=false;
       return;
     }
     d.bookingPayload={
       service_ids,customer_name:val.customer_name,customer_phone:international,
       start_at:slot,idempotency_key:d.key,
       appointment_messages_opt_in:form.elements.appointment_messages_opt_in.checked,
     };
     freezeBookingForm(form,true);
     await api(base+'/appointments','POST',d.bookingPayload);
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
   if(state.modal!==d)return;
   if(d.bookingDay)state.day=d.bookingDay;
   closeModal(true);notice(d.successNotice||'Alteração registrada com sucesso.');await changePage(state.page);
 }catch(error){
   if(state.modal!==d)return;
   if(d.bookingPayload&&error.status>=400&&error.status<500&&![408,429].includes(error.status)){d.bookingPayload=null;d.key=crypto.randomUUID();freezeBookingForm(form,false);}
   if(d.action==='extras'&&error.status===409&&error.detail?.code!=='extra_duration_conflict'){await loadProfessionalExtras(d);$('modalError').textContent='O atendimento mudou. Confira os dados atualizados antes de adicionar serviços.';}
   else $('modalError').textContent=error.message+(d.bookingPayload||d.extrasPayload?' Os dados foram preservados. Tente novamente para verificar a mesma solicitação.':'');
   button.disabled=false;
 }finally{d.submitting=false;if(state.modal===d)form.setAttribute('aria-busy','false');}
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
 if(state.modal?.submitting)return;
 if(action==='extras-retry'&&state.modal?.action==='extras'){await loadProfessionalExtras(state.modal);return;}
 if(action==='booking-back'&&state.modal?.action==='new-appointment'){
   if(state.modal.bookingPayload)return;
   state.modal.reviewSlot=null;
   $('modalReview').hidden=true;$('modalSlots').hidden=false;
   $('modalSubmit').textContent='Revisar agendamento';
   return;
 }
 const id=event.target.closest('[data-id]')?.dataset.id;
 const record=action==='remove-block'?state.blocks.find(b=>b.id===id):state.appointments.find(a=>a.id===id);
 if(!['new-block','new-appointment'].includes(action)&&!record)return;
 openModal(action,record);
});
$('modalClose').addEventListener('click',()=>closeModal());$('modalCancel').addEventListener('click',()=>closeModal());
$('modal').addEventListener('click',event=>{if(event.target===$('modal'))closeModal()});
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('modal').hidden)closeModal()});
$('modalForm').addEventListener('submit',submitModal);
$('modalForm').addEventListener('input',event=>{
 if(state.modal?.action==='new-appointment'&&!state.modal.bookingPayload&&event.target.name==='final_total'){state.modal.finalTotalTouched=true;resetProfessionalBookingReview();}
 if(state.modal?.action==='extras'&&event.target.name==='actual_duration_min')resetProfessionalExtrasPreview();
 if(state.modal?.action==='new-appointment'&&!state.modal.bookingPayload&&['customer_name','customer_phone'].includes(event.target.name)){
   $('modalForm').elements.appointment_messages_opt_in.checked=false;
 }
});
document.addEventListener('change',event=>{
 const d=state.modal,el=event.target;if(d?.submitting)return;
 if(d?.action==='new-appointment'&&!d.bookingPayload){
   if(['booking_mode','walk_in'].includes(el.name))syncProfessionalBookingMode();
   else if(['day','clock','service_ids','actual_duration_min'].includes(el.name)){resetProfessionalBookingReview();updateProfessionalBookingTotal();}
 }
 if(d?.action==='extras'&&!d.extrasPayload){
   if(el.name==='extra_service_ids'){const selected=$('modalForm').querySelectorAll('[name=extra_service_ids]:checked');if(selected.length>d.extraLimit){el.checked=false;notice('Este atendimento permite mais '+d.extraLimit+' serviços.');}resetProfessionalExtrasPreview();}
   else if(el.name==='actual_duration_min')resetProfessionalExtrasPreview();
 }
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
