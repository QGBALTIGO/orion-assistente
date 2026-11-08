import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse,customerId} from './harness.mjs';

const professional='50000000-0000-4000-8000-000000000005';
const service='60000000-0000-4000-8000-000000000006';
const seriesId='70000000-0000-4000-8000-000000000007';
const appointmentId='80000000-0000-4000-8000-000000000008';
const next='2030-04-05T13:00:00Z';

function sample(){
  return {id:seriesId,customer_id:customerId,customer_name:'João sintético',customer_phone:'+5511999990060',
    professional_id:professional,professional_name:'Equipe sintética',
    services_label:'Corte sintético',service_ids:[service],first_date:'2030-04-05',
    weekday:4,local_time:'10:00:00',interval_weeks:1,occurrence_count:12,
    confirmed_count:12,cancelled_count:0,next_start_at:next,closed_at:null,
    appointments:[{id:appointmentId,series_index:1,start_at:next,
      end_at:'2030-04-05T13:30:00Z',status:'confirmed',protocol:'AG-AB12CD'}]};
}
function visit(){
  return {id:appointmentId,series_id:seriesId,series_index:1,customer_id:customerId,
    customer_name:'João sintético',customer_phone:'+5511999990060',
    professional_id:professional,professional_name:'Equipe sintética',professional_color:'#2e7bf6',
    start_at:next,end_at:'2030-04-05T13:30:00Z',status:'confirmed',
    timezone:'America/Sao_Paulo',protocol:'AG-AB12CD',price_mode:'fixed',
    total_cents:10000,original_total_cents:10000,discount_cents:0,
    created_at:next,updated_at:next,allowed_actions:[],history:[],
    items:[{name_snapshot:'Corte sintético',duration_snapshot_min:30,
      price_snapshot_cents:10000,price_mode_snapshot:'fixed'}]};
}
function reply({conflict=false,existing=false}={}){
  const series=sample();let created=existing;
  return (path,method='GET')=>{
    if((path.includes('/appointments/series/preview')||path.endsWith('/appointments/series/'+seriesId+'/preview'))&&method==='POST')return {
      count:12,available_count:conflict?11:12,conflict_count:conflict?1:0,can_create:!conflict,
      duration_min:30,price_cents:10000,
      occurrences:[{index:1,day:'2030-04-05',start_at:next,available:!conflict,
                    reason:conflict?'Horário já reservado':null}]};
    if(path.endsWith('/appointments/series')&&method==='POST'){created=true;return series;}
    if(path.includes('/appointments/series?limit='))return {items:created?[series]:[]};
    if(path.endsWith('/appointments/series/'+seriesId+'/replace')&&method==='POST')return {...series,id:'90000000-0000-4000-8000-000000000009',replayed:false,supersedes_id:seriesId};
    if(path.endsWith('/appointments/series/'+seriesId)&&method==='GET')return series;
    if(path.endsWith('/appointments/series/'+seriesId+'/stop')&&method==='POST')return {cancelled_count:12};
    if(path.endsWith('/appointments/'+appointmentId)&&method==='GET')return visit();
    if(path.includes('/appointments?')){
      const url=new URL(path,'https://orion.test'),day=url.searchParams.get('day');
      const items=created&&day===series.first_date?[visit()]:[];
      return {items,total:items.length,limit:Number(url.searchParams.get('limit')||50),
        offset:0,has_more:false};
    }
    if(path.endsWith('/services'))return [{id:service,name:'Corte sintético',duration_min:30,price_cents:10000,active:true}];
    if(path.endsWith('/professionals'))return [{id:professional,name:'Equipe sintética',active:true,accepts_appointments:true}];
    if(path.includes('/management/customers?'))return {items:[{id:customerId,name:'João sintético',phone:'+5511999990060',active:true}]};
    if(path.endsWith('/google/status'))return {configured:false,connected:false,sync_enabled:false,sync_status:'not_configured',capabilities:{}};
    if(path.includes('/google/appointments/'+appointmentId+'/status'))return {status:'not_configured'};
    return defaultResponse(path);
  };
}

async function openForm(h){
  h.click('[data-os-action=new-appointment]');await flush();
  const mode=h.document.querySelector('select[name=booking_mode]');
  mode.value='fixed';mode.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.ok(h.document.querySelector('[data-os-form=fixed-client]'),'must reuse Orion form');
  h.fill('input[name=customer_name]','João sintético');
  h.fill('input[name=customer_phone]','+5511999990060');
  h.click('[data-os-fixed-service="'+service+'"]');
  const select=h.document.querySelector('select[name=professional_id]');
  select.value=professional;select.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  const start=h.document.querySelector('input[name=first_date]');
  start.value=sample().first_date;
  start.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
}

test('the agenda shows exactly three views and recurring bookings use the same rows and calendar as other clients',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply({existing:true})});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('[data-os-action=new-fixed-client]'),null);
  assert.deepEqual([...h.document.querySelectorAll('[data-os-agenda-mode]')].map(el=>el.dataset.osAgendaMode),
    ['list','calendar','professional']);
  assert.equal(h.document.querySelector('[data-os-agenda-mode=fixed]'),null);
  assert.equal(h.document.querySelector('#osAgendaView-fixed'),null);
  const picker=h.document.querySelector('#osAgendaDatePicker');
  picker.value=sample().first_date;
  picker.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  for(const mode of ['list','calendar','professional']){
    if(mode!=='list'){h.click('[data-os-agenda-mode='+mode+']');await flush();}
    const item=h.document.querySelector('[data-os-appointment="'+appointmentId+'"]');
    assert.ok(item,mode+' should show recurring appointment as a normal booking');
    assert.match(item.textContent,/João sintético/);
    assert.equal(h.document.querySelector('[data-os-agenda-mode='+mode+']').getAttribute('aria-selected'),'true');
  }
  assert.equal(h.calls.some(call=>call.path.includes('/appointments/series?limit=')),false);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'agenda');
});

test('preview precedes commit, shows dates and confirms only on second submit',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());await flush();await openForm(h);
  h.submit('[data-os-form=fixed-client]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/appointments/series/preview')&&x.method==='POST').length,1);
  assert.equal(h.calls.filter(x=>x.path.endsWith('/appointments/series')&&x.method==='POST').length,0);
  assert.match(h.document.getElementById('osFixedPreview').textContent,/disponíveis/);
  h.submit('[data-os-form=fixed-client]');await flush();
  const creations=h.calls.filter(x=>x.path.endsWith('/appointments/series')&&x.method==='POST');
  assert.equal(creations.length,1);
  assert.equal(creations[0].data.service_ids[0],service);
  assert.equal(creations[0].data.occurrence_count,12);
  assert.equal(creations[0].data.customer_phone,'+5511999990060');
  assert.equal(h.document.querySelector('[data-os-agenda-mode=list]').getAttribute('aria-selected'),'true');
  assert.equal(h.document.querySelector('#osAgendaDatePicker').value,creations[0].data.first_date);
  assert.ok(h.document.querySelector('[data-os-appointment="'+appointmentId+'"]'));
  assert.match(h.document.getElementById('osPageBody').textContent,/João sintético/);
});

test('conflicting preview prevents all bookings without silent partial save',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply({conflict:true})});
  t.after(()=>h.close());await flush();await openForm(h);
  h.submit('[data-os-form=fixed-client]');await flush();
  assert.match(h.document.getElementById('osFixedPreview').textContent,/Conflitos encontrados/);
  h.submit('[data-os-form=fixed-client]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/appointments/series')&&x.method==='POST').length,0);
});

test('each recurring appointment provides a series management link without a separate tab',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply({existing:true})});
  t.after(()=>h.close());await flush();
  const picker=h.document.querySelector('#osAgendaDatePicker');
  picker.value=sample().first_date;
  picker.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  h.click('[data-os-appointment="'+appointmentId+'"]');await flush();
  assert.ok(h.document.querySelector('[data-os-action=fixed-open-from-appointment]'));
  h.click('[data-os-action=fixed-open-from-appointment]');await flush();
  assert.match(h.document.getElementById('osDialog').textContent,/Atendimentos da série/);
  assert.ok(h.document.querySelector('[data-os-fixed-appointment="'+appointmentId+'"]'));
  h.click('[data-os-action=fixed-stop]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/stop')).length,0);
  h.submit('[data-os-form=confirm]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/stop')&&x.method==='POST').length,1);
});


test('booking mode includes fixed client and switches within the same Orion dialog',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());await flush();
  h.click('[data-os-action=new-appointment]');await flush();
  const select=h.document.querySelector('select[name=booking_mode]');
  assert.deepEqual([...select.options].map(option=>option.value),['regular','fixed','exceptional','retroactive']);
  h.fill('input[name=customer_name]','João sintético');
  h.fill('input[name=customer_phone]','+5511999990060');
  select.value='fixed';select.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.ok(h.document.querySelector('[data-os-form=fixed-client]'));
  assert.equal(h.document.querySelector('#osDialogTitle').textContent,'Novo agendamento');
  assert.equal(h.document.querySelector('select[name=booking_mode]').value,'fixed');
  assert.equal(h.document.querySelector('input[name=customer_name]').value,'João sintético');
  assert.equal(h.document.querySelector('input[name=customer_phone]').value.replace(/\D/g,''),'11999990060');
  assert.ok(h.document.querySelector('#osDialog').classList.contains('os-booking-dialog'));
  const back=h.document.querySelector('select[name=booking_mode]');
  back.value='exceptional';back.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.ok(h.document.querySelector('[data-os-form=appointment]'));
  assert.equal(h.document.querySelector('select[name=booking_mode]').value,'exceptional');
  assert.equal(h.document.querySelector('input[name=customer_name]').value,'João sintético');
  assert.ok(h.document.querySelector('#osDialog').classList.contains('os-booking-dialog'));
});

test('new recurring clients are still created only through the normal Novo agendamento button',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('[data-os-action=new-fixed-client]').length,0);
  assert.equal(h.document.querySelectorAll('[data-os-agenda-mode=fixed]').length,0);
  assert.ok(h.document.querySelector('[data-os-action=new-appointment]'));
});

test('booking dialog design is scoped to appointment flows and stays rounded on mobile',async()=>{
  const {readFileSync}=await import('node:fs');
  const css=readFileSync(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.match(css,/#osDialog\.os-booking-dialog\s*\{/);
  assert.match(css,/border-radius:28px/);
  assert.match(css,/border-radius:26px/);
  assert.match(css,/#osDialog\.os-booking-dialog \.os-service-option/);
  assert.match(css,/@media\(max-width:639px\)/);
});


test('Clientes da barbearia mostra Ativos, Fixos, Em risco e recorrências cadastradas',async t=>{
  const h=await makeSuite({initialRoute:'clientes',respond:reply({existing:true})});
  t.after(()=>h.close());await flush();
  assert.deepEqual([...h.document.querySelectorAll('[data-os-customer-status]')].map(el=>el.dataset.osCustomerStatus),
    ['active','fixed','at_risk']);
  h.click('[data-os-customer-status=fixed]');await flush();
  const row=h.document.querySelector('[data-os-fixed-series="'+seriesId+'"]');
  assert.ok(row,'recorrência ativa deve aparecer na aba Fixos');
  assert.match(row.textContent,/João sintético/);
  assert.match(row.textContent,/Toda semana/);
  assert.match(h.document.getElementById('osCustomersCount').textContent,/1 cliente fixo/);
  assert.ok(h.calls.some(call=>call.path.includes('/appointments/series?limit=200')));
});

test('Editar fixo permite mudar sexta para quarta e duas para quatro visitas por mês',async t=>{
  const h=await makeSuite({initialRoute:'clientes',respond:reply({existing:true})});
  t.after(()=>h.close());await flush();
  h.click('[data-os-customer-status=fixed]');await flush();
  h.click('[data-os-fixed-series="'+seriesId+'"]');await flush();
  assert.ok(h.document.querySelector('[data-os-action=fixed-edit]'));
  h.click('[data-os-action=fixed-edit]');await flush();
  const form=h.document.querySelector('[data-os-form=fixed-client]');
  assert.ok(form,'usa o formulário da própria Agenda');
  assert.equal(form.elements.customer_name.readOnly,true);
  assert.equal(form.elements.customer_name.value,'João sintético');
  const weekday=form.elements.weekday;
  weekday.value='2';weekday.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  assert.equal(form.elements.weekday.value,'2');
  assert.equal(new Date(form.elements.first_date.value+'T12:00:00Z').getUTCDay(),3,'quarta-feira');
  const pattern=form.elements.repeat_pattern;
  assert.deepEqual([...pattern.options].map(option=>option.value),
    ['weekly1','weekly2','weekly4','monthly2','monthly4']);
  pattern.value='monthly4';pattern.dispatchEvent(new h.window.Event('change',{bubbles:true}));await flush();
  form.elements.occurrence_count.value='4';
  h.submit('[data-os-form=fixed-client]');await flush();
  const previews=h.calls.filter(call=>call.path.endsWith('/appointments/series/'+seriesId+'/preview'));
  assert.equal(previews.length,1);
  assert.equal(previews[0].data.monthly_visits,4);
  assert.equal(previews[0].data.weekday,2);
  assert.equal(previews[0].data.occurrence_count,4);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/replace')).length,0,'prévia antes de alteração');
  h.submit('[data-os-form=fixed-client]');await flush();
  const replacements=h.calls.filter(call=>call.path.endsWith('/appointments/series/'+seriesId+'/replace'));
  assert.equal(replacements.length,1,'nova série salva uma vez');
  assert.equal(replacements[0].data.customer_phone,'+5511999990060');
  assert.equal(replacements[0].data.monthly_visits,4);
});

test('prévia de conflito ao editar impede modificar a recorrência existente',async t=>{
  const h=await makeSuite({initialRoute:'clientes',respond:reply({existing:true,conflict:true})});
  t.after(()=>h.close());await flush();
  h.click('[data-os-customer-status=fixed]');await flush();
  h.click('[data-os-fixed-series="'+seriesId+'"]');await flush();
  h.click('[data-os-action=fixed-edit]');await flush();
  h.submit('[data-os-form=fixed-client]');await flush();
  assert.match(h.document.querySelector('#osFixedPreview').textContent,/Conflitos encontrados/);
  h.submit('[data-os-form=fixed-client]');await flush();
  assert.equal(h.calls.filter(call=>call.path.endsWith('/replace')).length,0);
});


test('iPhone: primeiro atendimento e horário fixo não extrapolam a grade do modal',async t=>{
  const {readFileSync}=await import('node:fs');
  const css=readFileSync(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const selector='#osDialog.os-booking-dialog [data-os-form="fixed-client"] :is(input[type="date"],input[type="time"])';
  const start=css.indexOf(selector+'{');
  assert.ok(start>=0,'a correção dos campos nativos deve ser específica do agendamento fixo');
  const declaration=css.slice(start,css.indexOf('}',start));
  for(const rule of ['-webkit-appearance:none','appearance:none','box-sizing:border-box',
                     'width:100%','min-width:0','max-width:100%',
                     'min-inline-size:0','max-inline-size:100%']){
    assert.ok(declaration.includes(rule),'controles WebKit devem respeitar '+rule);
  }
  assert.match(css,/#osDialog\.os-booking-dialog \[data-os-form="fixed-client"\] \.os-form-grid\s*\{\s*grid-template-columns:minmax\(0,1fr\)/);
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());
  await flush();
  await openForm(h);
  const form=h.document.querySelector('[data-os-form="fixed-client"]');
  for(const [name,type] of [['first_date','date'],['local_time','time']]){
    const input=form.querySelector('input[name="'+name+'"]');
    assert.ok(input,name+' precisa continuar nativo e editável');
    assert.equal(input.type,type);
    assert.ok(input.closest('.os-form-grid'),'o campo deve permanecer na mesma grade Orion');
    assert.ok(input.closest('.os-field'),'o estilo reutiliza os controles do sistema');
    assert.equal(input.disabled,false);
  }
});
