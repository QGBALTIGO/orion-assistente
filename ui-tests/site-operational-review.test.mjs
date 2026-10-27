import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,deferred,defaultResponse,tenantA} from './harness.mjs';

const prefix='/api/v1/tenants/'+tenantA,management=prefix+'/management';
const appointment='20000000-0000-4000-8000-000000000010';
const review=()=>({timezone:'America/Sao_Paulo',
  notices:[{code:'contact_numbers_differ',message:'Confira os números oficiais <img src=x onerror=alert(1)>.'}],
  timezone_review_count:1,timezone_policy:'Os instantes gravados das reservas foram preservados. A revisão registra ciência.',
  timezone_reviews:[{appointment_id:appointment,protocol:'SYNTHETIC-10',status:'confirmed',
    previous_local_start:'2030-01-07T09:00:00-04:00',current_local_start:'2030-01-07T10:00:00-03:00',
    from_timezone:'America/Campo_Grande',to_timezone:'America/Sao_Paulo'}]});
const record=()=>({id:appointment,protocol:'SYNTHETIC-10',status:'confirmed',start_at:'2030-01-07T13:00:00Z',
  end_at:'2030-01-07T14:00:00Z',total_cents:99999999,price_mode:'from',items:[],history:[],customer_name:'Cliente sintético'});
async function siteSuite(t,{role='owner',userRole='member',respond}={}){
  const server={review:review()};
  const h=await makeSuite({initialRoute:'site',role,bridge:{user:{role:userRole}},respond:async(path,method='GET',payload)=>{
    if(respond){const result=await respond(path,method,payload,server);if(result!==undefined)return result;}
    if(path===management+'/site')return {title:'Empresa sintética',slug:'empresa-sintetica',version:4,operational_review:server.review};
    if(path===management+'/site/publication')return {can_edit:role!=='viewer',can_publish:role==='owner',draft_version:4,state:'ready',ready:true,issues:[]};
    if(path===management+'/site/metrics?days=30')return {visits:0,daily:[]};
    if(path===prefix+'/appointments/'+appointment)return record();
    return defaultResponse(path);
  }});t.after(()=>h.close());await flush();return {h,server};
}
const writes=h=>h.calls.filter(call=>call.method!=='GET');
test('Meu Site painel e configurações exibem avisos escapados e as duas horas locais do mesmo instante',async t=>{
  const {h}=await siteSuite(t);
  const assertReview=()=>{
    const notice=h.document.getElementById('osSiteOperationalReview');assert.ok(notice);
    assert.match(notice.textContent,/Antes: 07\/01\/2030,? 09:00 → Agora: 07\/01\/2030,? 10:00/);
    assert.match(notice.textContent,/reservas foram preservados/);assert.equal(notice.querySelector('img'),null);
    assert.equal(notice.querySelector('[data-os-appointment]').dataset.osAppointment,appointment);
  };
  assertReview();h.click('[data-os-site-tab=settings]');assertReview();
  assert.equal(h.document.getElementById('osSiteForm').querySelector('#osSiteOperationalReview'),null,'aviso não faz parte da edição da reserva');
  assert.equal(writes(h).length,0);
  h.click('#osSiteOperationalReview [data-os-appointment]');await flush();
  assert.ok(h.calls.some(call=>call.path===prefix+'/appointments/'+appointment));assert.equal(writes(h).length,0);
  assert.equal(h.errors.length,0);
});
test('somente proprietário registra ciência, bloqueia repetição e preserva instantes e demais configurações',async t=>{
  const pending=deferred();
  const {h,server}=await siteSuite(t,{respond:(path,method)=>path.endsWith('/acknowledge')&&method==='POST'?pending.promise:undefined});
  const original=JSON.stringify(server.review.timezone_reviews[0]);
  h.click('[data-os-timezone-review-ack]');h.click('[data-os-timezone-review-ack]');await flush();
  assert.equal(writes(h).length,1);assert.equal(writes(h)[0].path,management+'/site/timezone-reviews/'+appointment+'/acknowledge');
  assert.deepEqual({...writes(h)[0].data},{});assert.equal(JSON.stringify(server.review.timezone_reviews[0]),original);
  server.review={...server.review,timezone_review_count:0,timezone_reviews:[]};
  pending.resolve({appointment_id:appointment,acknowledged_at:'2030-01-07T12:00:00Z'});await flush();
  assert.equal(h.document.querySelector('[data-os-timezone-review-ack]'),null);
  assert.match(h.document.getElementById('osToastRegion').textContent,/horário da reserva foi preservado/);
});
test('editor, leitor, suporte e IDs de outra reserva não podem forjar ciência pelo navegador',async t=>{
  for(const [role,userRole] of [['editor','member'],['viewer','member'],['owner','support']]){
    const {h}=await siteSuite(t,{role,userRole});
    assert.equal(h.document.querySelector('[data-os-timezone-review-ack]'),null);
    const forged=h.document.createElement('button');forged.dataset.osTimezoneReviewAck=appointment;h.document.body.append(forged);forged.click();await flush();
    assert.equal(writes(h).length,0);
  }
  const {h}=await siteSuite(t);const forged=h.document.createElement('button');
  forged.dataset.osTimezoneReviewAck='20000000-0000-4000-8000-000000000099';h.document.body.append(forged);forged.click();await flush();
  assert.equal(writes(h).length,0);
});
test('ciência não descarta alterações não salvas nem apaga aviso quando o servidor falha',async t=>{
  const {h}=await siteSuite(t,{respond:(_path,method)=>{if(method==='POST')throw Error('Falha temporária ao registrar ciência.');}});
  h.click('[data-os-site-tab=settings]');h.fill('#osSiteForm [name=title]','Rascunho ainda não salvo');
  h.click('[data-os-timezone-review-ack]');await flush();
  assert.equal(writes(h).length,0);assert.equal(h.document.querySelector('#osSiteForm [name=title]').value,'Rascunho ainda não salvo');
  assert.match(h.document.getElementById('osToastRegion').textContent,/Salve as configurações/);
  h.document.getElementById('osSiteForm').dataset.dirty='false';h.click('[data-os-timezone-review-ack]');await flush();
  assert.equal(writes(h).length,1);assert.ok(h.document.querySelector('[data-os-timezone-review-ack]'));
  assert.match(h.document.getElementById('osToastRegion').textContent,/Falha temporária/);
});

test('financeiro destaca valor final pendente sem alterar as seis métricas, abas ou somar estimativas à receita',async t=>{
  const h=await makeSuite({initialRoute:'financeiro',respond:path=>{
    if(path.includes('/management/finance/summary?'))return {summary:{income_cents:5000,expense_cents:1000,balance_cents:4000,count:2,average_ticket_cents:2500,pending_income_cents:1400,
      awaiting_final_price_count:2,awaiting_final_price:[{...record(),id:appointment}]}};
    if(path===prefix+'/appointments/'+appointment)return record();
    return defaultResponse(path);
  }});t.after(()=>h.close());await flush();
  const notice=h.document.getElementById('osAwaitingFinalPrice');assert.ok(notice);
  assert.match(notice.textContent,/Aguardando valor final: 2 atendimento/);
  assert.match(notice.textContent,/recebimento são acompanhadas separadamente/);
  assert.match(notice.textContent,/Mostrando 1 de 2/);
  assert.equal(h.document.querySelectorAll('.os-finance-metrics>.os-metric').length,6);
  assert.equal(h.document.querySelectorAll('[data-os-finance-tab]').length,5);
  const metrics=h.document.querySelector('.os-finance-metrics');assert.match(metrics.textContent,/50,00/);assert.doesNotMatch(metrics.textContent,/999\.999/);
  assert.doesNotMatch(notice.textContent,/999\.999/,'a estimativa não é apresentada como fechamento');
  h.click('#osAwaitingFinalPrice [data-os-appointment]');await flush();
  assert.ok(h.calls.some(call=>call.path===prefix+'/appointments/'+appointment));assert.equal(writes(h).length,0);
  assert.equal(h.errors.length,0);
});
