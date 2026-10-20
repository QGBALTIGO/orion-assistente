import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {tenantA,tenantB,customerId,flush,deferred} from './harness.mjs';
const appointment='40000000-0000-4000-8000-000000000004',notice='50000000-0000-4000-8000-000000000005';
async function make({canWrite=true,respond}={}){
 const dom=new JSDOM('<section id="host"></section>',{url:'https://orion.test',runScripts:'outside-only'}),w=dom.window,calls=[];
 w.eval(await readFile(new URL('../app/static/orion-communication.js',import.meta.url),'utf8'));
 const host=w.document.querySelector('#host');
 const custom={event:'booking_confirmed',requested_enabled:true,template:'Texto próprio {cliente}',template_source:'custom',template_revision:1};
 const request=async(path,method='GET',payload)=>{calls.push({path,method,payload});const response=await respond?.(path,method,payload);if(response!==undefined)return response;
  if(method!=='GET')throw Error('Synthetic response required for write');
  if(path.endsWith('/marketing/automations'))return {version:7,configuration:{rules:[custom],recovery_days:60,delivery_policy:{weekdays:[0,1,2,3,4],birthday_leap_day:'skip'}}};
  if(path.endsWith('/preferences'))return {version:7,business:{business_name:'Negócio preservado',approved_link_hosts:['review.example.com']}};
  if(path.includes('/customers?'))return {items:[{id:customerId,name:'Pessoa <script>synthetic</script>'}]};
  if(path.endsWith('/appointments'))return {timezone:'America/Cuiaba',items:[{id:appointment,protocol:'SYNTHETIC',start_at:'2026-10-30T14:00:00Z',professional:'Pessoa teste',status:'confirmed'}]};
  if(path.endsWith('/communication/pending'))return {items:[{id:notice,contact_e164:'+5567999999901',event:'rescheduled',reason:'human_paused',version:3,status:'pending'}]};
  throw Error('Unexpected synthetic route '+path);
 };
 const mount=t=>w.OrionCommunication.mount({host,request,tenantId:t,canWrite});await mount(tenantA);await flush();
 const change=(name,value)=>{const el=host.querySelector('[name="'+name+'"]');el.value=value;el.dispatchEvent(new w.Event('change',{bubbles:true}));};
 const submit=selector=>host.querySelector(selector).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
 return {w,host,calls,custom,mount,change,submit,close(){w.OrionCommunication.unmount();dom.window.close();}};
}
test('opening the review reads facts without any mutation or send and escapes customer text',async t=>{
 const h=await make();t.after(()=>h.close());assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
 assert.match(h.host.textContent,/Nenhuma ação aqui envia mensagens/);assert.equal(h.host.querySelector('script'),null);
 assert.match(h.host.textContent,/Pessoa <script>synthetic/);
});
test('preview uses a real customer and appointment, carries preference CAS and never enqueues',async t=>{
 const h=await make({respond(path,method){if(method==='POST')return {body:'Confirmado <b>literal</b>',presentation:{utf16_units:28},preferences_version:7,unsaved_draft:false,schedule_reason:'quiet_window_expired'};}});t.after(()=>h.close());
 h.change('customer',customerId);await flush();h.change('appointment',appointment);h.submit('[data-communication-preview]');await flush();
 const write=h.calls.find(c=>c.method==='POST');assert.match(write.path,/automations\/preview$/);assert.equal(write.payload.appointment_id,appointment);assert.equal(write.payload.expected_preferences_version,7);
 assert.match(h.host.textContent,/começa depois do prazo/);assert.equal(h.host.querySelector('b'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,1);assert.equal(h.host.querySelector('[data-communication-ack]').disabled,false);assert.equal(h.host.querySelector('[name=appointment]').value,appointment);
});
test('late preview from another tenant cannot populate the new screen',async t=>{
 const waiting=deferred();const h=await make({respond(path,method){if(method==='POST')return waiting.promise;}});t.after(()=>h.close());
 h.change('customer',customerId);await flush();h.submit('[data-communication-preview]');await flush();await h.mount(tenantB);
 waiting.resolve({body:'PRIVATE OLD TENANT',presentation:{utf16_units:18}});await flush();assert.doesNotMatch(h.host.textContent,/PRIVATE OLD TENANT/);
});
test('search and appointment requests remain tenant scoped and clear prior choices',async t=>{
 const h=await make();t.after(()=>h.close());h.change('customer',customerId);await flush();
 h.host.querySelector('[name=q]').value='Outra pessoa';h.submit('[data-communication-search]');await flush();
 assert.ok(h.calls.some(c=>c.path.endsWith('&q=Outra%20pessoa')));assert.equal(h.host.querySelector('[name=customer]').value,'');
});
test('saving send windows preserves custom rules, sends the weekday policy and does not activate',async t=>{
 const h=await make({respond(path,method){if(method==='PUT')return {version:8};}});t.after(()=>h.close());
 h.host.querySelector('[name=operational_start_hour]').value='7';h.submit('[data-communication-policy]');await flush();
 const write=h.calls.find(c=>c.method==='PUT');assert.equal(write.payload.configuration.rules[0].template,h.custom.template);
 assert.deepEqual([...write.payload.configuration.delivery_policy.weekdays],[0,1,2,3,4]);assert.equal(write.payload.configuration.delivery_policy.operational_start_hour,7);
 assert.equal('enabled' in write.payload,false);assert.match(h.host.textContent,/Nenhum envio foi ativado/);
});
test('acknowledgment carries only the pending notice CAS and never resumes AI',async t=>{
 const h=await make({respond(path,method){if(method==='POST')return {sent:false};}});t.after(()=>h.close());
 h.host.querySelector('[data-communication-ack]').click();await flush();const write=h.calls.find(c=>c.method==='POST');
 assert.match(write.path,/pending\/.+\/acknowledge$/);assert.deepEqual(Object.keys(write.payload),['expected_version']);assert.equal(write.payload.expected_version,3);
 assert.equal(h.host.querySelector('[data-communication-ack]').disabled,false);
});
test('viewer can review facts but cannot edit policies, links or acknowledge notices',async t=>{
 const h=await make({canWrite:false});t.after(()=>h.close());assert.equal(h.host.querySelector('[data-communication-ack]'),null);
 assert.ok(h.host.querySelector('[data-communication-policy] button').disabled);h.submit('[data-communication-policy]');h.submit('[data-communication-links]');await flush();
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
