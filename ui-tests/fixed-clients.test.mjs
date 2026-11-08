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
function reply({conflict=false}={}){
  const series=sample();
  return (path,method='GET')=>{
    if(path.includes('/appointments/series/preview')&&method==='POST')return {
      count:12,available_count:conflict?11:12,conflict_count:conflict?1:0,can_create:!conflict,
      duration_min:30,price_cents:10000,
      occurrences:[{index:1,day:'2030-04-05',start_at:next,available:!conflict,
                    reason:conflict?'Horário já reservado':null}]};
    if(path.endsWith('/appointments/series')&&method==='POST')return series;
    if(path.endsWith('/appointments/series?limit=100'))return {items:[series]};
    if(path.endsWith('/appointments/series/'+seriesId)&&method==='GET')return series;
    if(path.endsWith('/appointments/series/'+seriesId+'/stop')&&method==='POST')return {cancelled_count:12};
    if(path.includes('/appointments?'))return {items:[],total:0,limit:50,offset:0,has_more:false};
    if(path.endsWith('/services'))return [{id:service,name:'Corte sintético',duration_min:30,price_cents:10000,active:true}];
    if(path.endsWith('/professionals'))return [{id:professional,name:'Equipe sintética',active:true,accepts_appointments:true}];
    if(path.includes('/management/customers?'))return {items:[{id:customerId,name:'João sintético',phone:'+5511999990060',active:true}]};
    if(path.endsWith('/google/status'))return {configured:false,connected:false,sync_enabled:false,sync_status:'not_configured',capabilities:{}};
    return defaultResponse(path);
  };
}

async function openForm(h){
  h.click('[data-os-agenda-mode=fixed]');await flush();
  h.click('[data-os-action=new-fixed-client]');await flush();
  assert.ok(h.document.querySelector('[data-os-form=fixed-client]'),'must reuse Orion form');
  h.fill('input[name=customer_name]','João sintético');
  h.fill('input[name=customer_phone]','+5511999990060');
  h.click('[data-os-fixed-service="'+service+'"]');
  const select=h.document.querySelector('select[name=professional_id]');
  select.value=professional;select.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
}

test('fixed clients remain a tab inside Orion agenda and do not create a new page',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());await flush();
  assert.ok(h.document.querySelector('[data-os-action=new-fixed-client]'));
  h.click('[data-os-agenda-mode=fixed]');await flush();
  assert.equal(h.document.querySelectorAll('[data-os-fixed-series]').length,1);
  assert.match(h.document.getElementById('osPageBody').textContent,/João sintético/);
  assert.equal(h.document.querySelector('[data-os-agenda-mode=fixed]').getAttribute('aria-selected'),'true');
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

test('series details show each visit and require confirmation before stopping the future',async t=>{
  const h=await makeSuite({initialRoute:'agenda',respond:reply()});
  t.after(()=>h.close());await flush();
  h.click('[data-os-agenda-mode=fixed]');await flush();
  h.click('[data-os-fixed-series]');await flush();
  assert.match(h.document.getElementById('osDialog').textContent,/Atendimentos da série/);
  assert.ok(h.document.querySelector('[data-os-fixed-appointment="'+appointmentId+'"]'));
  h.click('[data-os-action=fixed-stop]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/stop')).length,0);
  h.submit('[data-os-form=confirm]');await flush();
  assert.equal(h.calls.filter(x=>x.path.endsWith('/stop')&&x.method==='POST').length,1);
});
