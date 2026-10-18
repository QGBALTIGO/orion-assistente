import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { tenantA,tenantB,flush,deferred } from './harness.mjs';
const channel='30000000-0000-4000-8000-000000000003',campaign='40000000-0000-4000-8000-000000000004',job='50000000-0000-4000-8000-000000000005';
async function make({available=true,owner=true,respond}={}){
 const dom=new JSDOM('<section id="host"></section>',{url:'https://orion.test',runScripts:'outside-only'}),{window}=dom,calls=[];
 Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
 window.eval(await readFile(new URL('../app/static/orion-automations.js',import.meta.url),'utf8'));
 const request=async(path,method='GET',payload)=>{calls.push({path,method,payload});const result=await respond?.(path,method,payload);if(result!==undefined)return result;
  if(method!=='GET')throw Error('A mutation needs an explicit synthetic response');
  if(path.endsWith('/execution'))return {available,enabled:false,version:2,preferences_version:7,daily_limit:20};
  if(path.endsWith('/jobs'))return {items:[{id:job,status:'sent',delivery_status:'unknown',recipient_suffix:'9901'}]};
  if(path.endsWith('/qr-channels'))return {items:[{id:channel,label:'WhatsApp sintético',enabled:true}]};
  if(path.includes('/consent?'))return {available:true,marketing_opt_in:false,operational_opt_in:false,updated_at:null,human_paused:false};
  if(path.endsWith('/review'))return {id:campaign,version:1,snapshot:'a'.repeat(64),status:'draft',message:'Olá <script>teste</script>',eligible_count:2};
  throw Error('Unexpected read '+path);
 };
 const mount=(id=tenantA)=>window.OrionAutomations.mount({host:window.document.querySelector('#host'),request,tenantId:id,canManage:owner,campaigns:[{id:campaign,name:'Campanha sintética',status:'draft'}]});
 mount();await flush();return {window,dom,calls,mount,host:window.document.querySelector('#host'),close(){window.OrionAutomations.unmount();window.close();},
  submit(kind){window.document.querySelector('[data-oa-form='+kind+']').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));},
  change(selector,value){const element=window.document.querySelector(selector);if(element.type==='checkbox')element.checked=value;else element.value=value;element.dispatchEvent(new window.Event('change',{bubbles:true}));},
  fill(name,value){window.document.querySelector('[name='+name+']').value=value;},click(action){window.document.querySelector('[data-oa-action='+action+']').click();}};
}
test('loading reviewed execution is read-only and a disabled rollout is labelled as draft',async t=>{const h=await make({available:false});t.after(()=>h.close());assert.match(h.host.textContent,/continuam em rascunho/);assert.equal(h.host.querySelector('form'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);});
test('accepted transport is never presented as delivered or physically readable',async t=>{const h=await make();t.after(()=>h.close());assert.match(h.host.textContent,/Aceito pelo conector/);assert.match(h.host.textContent,/Entrega ainda não comprovada/);assert.doesNotMatch(h.host.textContent,/Mensagem entregue com sucesso/);});
test('a viewer has monitoring but no consent, activation or campaign actions',async t=>{const h=await make({owner:false});t.after(()=>h.close());assert.equal(h.host.querySelector('form'),null);assert.equal(h.host.querySelector('[data-oa-action=reconcile]'),null);});
test('execution needs an explicit reviewed submit and carries CAS and a stable request identity',async t=>{const h=await make({respond(path,method){if(method==='PUT')return {recorded:true};}});t.after(()=>h.close());h.change('[name=channel_id]',channel);h.change('[name=enabled]',true);h.change('[name=acknowledged]',true);h.submit('execution');await flush();const change=h.calls.find(c=>c.method==='PUT');assert.equal(change.payload.expected_version,2);assert.equal(change.payload.channel_id,channel);assert.equal(change.payload.acknowledged,true);assert.match(change.payload.idempotency_key,/^[0-9a-f-]{36}$/);});
test('an uncertain settings response offers only the same idempotent request, without automatic retry',async t=>{let count=0;const h=await make({respond(path,method){if(method==='PUT'){count++;if(count===1)throw Error('Sem resposta');return {recorded:true};}}});t.after(()=>h.close());h.change('[name=channel_id]',channel);h.submit('execution');await flush();assert.equal(count,1);assert.ok(h.host.querySelector('[data-oa-action=retry]'));h.click('retry');await flush();const writes=h.calls.filter(c=>c.method==='PUT');assert.deepEqual(writes[0].payload,writes[1].payload);});
test('consent is loaded separately and cannot be inferred from an existing customer phone',async t=>{const h=await make({respond(path,method){if(method==='PUT')return {recorded:true};}});t.after(()=>h.close());assert.equal(h.host.querySelector('[data-oa-form=consent]'),null);h.fill('phone','+5567999999901');h.submit('load-consent');await flush();assert.equal(h.host.querySelector('[name=marketing_opt_in]').checked,false);assert.equal(h.host.querySelector('[name=operational_opt_in]').checked,false);h.change('[name=operational_opt_in]',true);h.fill('evidence','Autorização sintética recebida');h.submit('consent');await flush();const write=h.calls.find(c=>c.method==='PUT');assert.equal(write.payload.marketing_opt_in,false);assert.equal(write.payload.operational_opt_in,true);assert.equal(write.payload.expected_updated_at,null);});
test('campaign preview escapes text and does not approve or send',async t=>{const h=await make();t.after(()=>h.close());h.change('[data-oa-campaign]',campaign);await flush();assert.match(h.host.textContent,/<script>teste<\/script>/);assert.equal(h.host.querySelector('script'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);assert.equal(h.host.querySelector('[data-oa-form=approve] button').disabled,true);});
test('delivery reconciliation is an explicit read of the prior operation and carries no message text',async t=>{const h=await make({respond(path,method){if(method==='POST')return {result:'unknown',resent:false};}});t.after(()=>h.close());h.click('reconcile');await flush();const write=h.calls.find(c=>c.method==='POST');assert.match(write.path,new RegExp('/jobs/'+job+'/reconcile$'));assert.deepEqual(Object.keys(write.payload),[]);assert.equal(h.calls.filter(c=>/\/approve$/.test(c.path)).length,0);});
test('a late prior-company response cannot populate the current monitoring screen',async t=>{const waiting=deferred();const h=await make({respond(path){if(path.includes(tenantA)&&path.endsWith('/execution'))return waiting.promise;}});t.after(()=>h.close());h.mount(tenantB);await flush();waiting.resolve({available:true,enabled:true,version:99});await flush();assert.match(h.host.textContent,/Envios pausados/);assert.doesNotMatch(h.host.textContent,/Envios autorizados/);});

test('failed authorization refresh discards previously enabled execution instead of permitting stale writes',async t=>{
 let reads=0;const h=await make({respond(path,method){if(path.endsWith('/execution')&&method==='GET'){if(++reads===1)return {available:true,enabled:true,version:2,preferences_version:7};throw Error('Autorização indisponível');}}});t.after(()=>h.close());
 assert.match(h.host.textContent,/Envios autorizados/);h.click('refresh');await flush();
 assert.doesNotMatch(h.host.textContent,/Envios autorizados/);assert.equal(h.host.querySelector('form'),null);assert.match(h.host.textContent,/não pôde ser consultada/);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('failed job or channel reads never become a usable empty result',async t=>{
 for(const failedPath of ['/jobs','/qr-channels']){const h=await make({respond(path){if(path.endsWith(failedPath))throw Error('Consulta indisponível');}});try{
  assert.match(h.host.textContent,/Autorização a verificar/);assert.equal(h.host.querySelector('[data-oa-form=execution] fieldset').disabled,true);
  if(failedPath==='/jobs'){assert.match(h.host.textContent,/Acompanhamento indisponível/);assert.doesNotMatch(h.host.textContent,/Nenhum envio registrado/);}
  h.submit('execution');await flush();assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
 }finally{h.close();}}
});

test('reconciliation-only mode allows checking prior delivery but cannot offer execution or contact writes',async t=>{
 const h=await make({respond(path,method){if(path.endsWith('/execution'))return {available:true,enabled:false,reconciliation_only:true,version:3};if(method==='POST')return {result:'unknown',resent:false};}});t.after(()=>h.close());
 assert.match(h.host.textContent,/Somente acompanhamento/);assert.equal(h.host.querySelector('form'),null);h.click('reconcile');await flush();
 assert.equal(h.calls.filter(c=>c.method!=='GET').length,1);assert.match(h.calls.find(c=>c.method==='POST').path,/\/reconcile$/);
});

test('execution authorization binds to the displayed preferences version and needs current explicit review',async t=>{
 const h=await make({respond(path,method){if(method==='PUT')return {recorded:true};}});t.after(()=>h.close());
 h.change('[name=channel_id]',channel);h.change('[name=enabled]',true);h.submit('execution');await flush();assert.equal(h.calls.filter(c=>c.method==='PUT').length,0);
 h.change('[name=channel_id]',channel);h.change('[name=enabled]',true);h.change('[name=acknowledged]',true);h.submit('execution');await flush();
 assert.equal(h.calls.find(c=>c.method==='PUT').payload.expected_preferences_version,7);
});

test('a missing preferences version cannot be replaced with a guessed version when authorizing',async t=>{
 const h=await make({respond(path){if(path.endsWith('/execution'))return {available:true,enabled:false,version:2};}});t.after(()=>h.close());
 h.change('[name=channel_id]',channel);h.change('[name=enabled]',true);h.change('[name=acknowledged]',true);h.submit('execution');await flush();assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('a failed second contact read clears the first contacts permissions and pause controls',async t=>{
 const h=await make({respond(path){if(path.includes('/consent?')&&path.includes('9902'))throw Error('Contato indisponível');}});t.after(()=>h.close());
 h.fill('phone','+5567999999901');h.submit('load-consent');await flush();assert.ok(h.host.querySelector('[data-oa-form=consent]'));
 h.fill('phone','+5567999999902');h.submit('load-consent');await flush();assert.equal(h.host.querySelector('[data-oa-form=consent]'),null);assert.equal(h.host.querySelector('[data-oa-action=contact-review]'),null);assert.match(h.host.textContent,/Contato indisponível/);
});

test('pausing a loaded contact is separate reviewed intent, strict boolean, and re-reads actual pause',async t=>{
 let paused=false;const h=await make({respond(path,method,payload){if(path.includes('/consent?'))return {available:true,marketing_opt_in:true,operational_opt_in:false,updated_at:null,human_paused:paused};if(path.endsWith('/contact-pause')&&method==='PUT'){paused=payload.paused;return {recorded:true,paused,changed:true};}}});t.after(()=>h.close());
 h.fill('phone','+5567999999901');h.submit('load-consent');await flush();h.click('contact-review');h.submit('contact-pause');await flush();assert.equal(h.calls.filter(c=>c.method==='PUT').length,0);
 h.change('[name=pause_acknowledged]',true);h.submit('contact-pause');await flush();
 const writes=h.calls.filter(c=>c.method!=='GET');assert.equal(writes.length,1);assert.deepEqual(JSON.parse(JSON.stringify(writes[0].payload)),{contact_e164:'+5567999999901',paused:true});
 assert.equal(h.calls.filter(c=>c.path.includes('/consent?')).length,2);assert.match(h.host.textContent,/Envios deste contato pausados/);assert.equal(h.host.querySelector('[name=marketing_opt_in]').checked,true);
});

test('resume rechecks human handoff and never claims messages were resent or contact already unpaused',async t=>{
 const h=await make({respond(path,method){if(path.includes('/consent?'))return {available:true,marketing_opt_in:false,operational_opt_in:false,updated_at:null,human_paused:true};if(path.endsWith('/contact-pause')&&method==='PUT')return {recorded:true,paused:false,changed:true};}});t.after(()=>h.close());
 h.fill('phone','+5567999999901');h.submit('load-consent');await flush();h.click('contact-review');h.change('[name=pause_acknowledged]',true);h.submit('contact-pause');await flush();
 assert.equal(h.calls.find(c=>c.method==='PUT').payload.paused,false);assert.match(h.host.textContent,/Envios deste contato pausados/);assert.match(h.host.textContent,/não recria envios antigos e não reenvia mensagens/);assert.doesNotMatch(h.host.textContent,/Contato retomado|Mensagens reenviadas/);assert.equal(h.calls.filter(c=>/\/approve$/.test(c.path)).length,0);
});

test('an unknown contact pause is retried only by the identical explicit setter',async t=>{
 let writes=0;const h=await make({respond(path,method){if(path.endsWith('/contact-pause')&&method==='PUT'){if(++writes===1)throw Error('Resposta incerta');return {recorded:true,paused:true,changed:false};}}});t.after(()=>h.close());
 h.fill('phone','+5567999999901');h.submit('load-consent');await flush();h.click('contact-review');h.change('[name=pause_acknowledged]',true);h.submit('contact-pause');await flush();assert.equal(writes,1);
 h.click('retry');await flush();const operations=h.calls.filter(c=>c.method==='PUT');assert.equal(writes,2);assert.deepEqual(operations[0].payload,operations[1].payload);
});

test('unknown human-pause status is not shown as unpaused or given a resume control',async t=>{
 const h=await make({respond(path){if(path.includes('/consent?'))return {available:true,marketing_opt_in:false,operational_opt_in:false,updated_at:null};}});t.after(()=>h.close());h.fill('phone','+5567999999901');h.submit('load-consent');await flush();assert.match(h.host.textContent,/Pausa deste contato a verificar/);assert.equal(h.host.querySelector('[data-oa-action=contact-review]'),null);
});

test('a foreign campaign review cannot authorize the selected campaign',async t=>{
 const h=await make({respond(path){if(path.endsWith('/review'))return {id:tenantB,version:1,snapshot:'a'.repeat(64),status:'draft',message:'Outro alvo',eligible_count:4};}});t.after(()=>h.close());h.change('[data-oa-campaign]',campaign);await flush();assert.equal(h.host.querySelector('[data-oa-form=approve]'),null);assert.match(h.host.textContent,/revisão desta campanha está indisponível/);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});
