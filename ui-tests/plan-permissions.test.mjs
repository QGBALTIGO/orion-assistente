import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,planState,flush,deferred,defaultResponse} from './harness.mjs';

const nav=h=>[...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute);
const route=h=>h.document.getElementById('page-suite').dataset.osCurrentRoute;

test('protected navigation and reads wait for authoritative permissions without flashing advanced resources',async t=>{
  const pending=deferred(),h=await makeSuite({initialRoute:'servicos',permissions:()=>pending.promise});t.after(()=>h.close());await flush();
  assert.equal(nav(h).includes('servicos'),false);assert.equal(nav(h).includes('marketing'),false);assert.equal(h.calls.some(call=>call.path.endsWith('/services')),false);
  pending.resolve({effective_plan:planState('essencial')});await flush();assert.equal(nav(h).includes('servicos'),true);assert.equal(nav(h).includes('marketing'),false);assert.equal(h.calls.some(call=>call.path.endsWith('/services')),true);
});

test('Essencial has a clean basic menu and direct advanced routes never fetch their data',async t=>{
  const h=await makeSuite({initialRoute:'clientes',permissions:planState('essencial')});t.after(()=>h.close());await flush();
  for(const name of ['dashboard','agenda','clientes','inbox','servicos','funcionarios','financeiro','site','plano','configuracoes'])assert.ok(nav(h).includes(name),name);
  for(const name of ['desempenho','marketing','fidelizacao','consultor'])assert.equal(nav(h).includes(name),false,name);
  assert.equal(h.document.getElementById('osConsultantFab').hidden||h.document.getElementById('osConsultantFab').classList.contains('hidden'),true);
  await h.window.OrionSuite.open('marketing');await flush();assert.equal(route(h),'plano');assert.equal(h.calls.some(call=>/\/marketing/.test(call.path)),false);
});

test('basic Financeiro does not query DRE, commissions, advances, settlements or payment reports',async t=>{
  const h=await makeSuite({initialRoute:'financeiro',permissions:planState('essencial')});t.after(()=>h.close());await flush();
  assert.equal(h.calls.some(call=>/finance\/(?:dre|commissions|advances|settlements)|entry_type=commission_payment/.test(call.path)),false);
  assert.equal(h.document.querySelector('[data-os-finance-tab=dre]'),null);assert.equal(h.document.querySelector('[data-os-finance-tab=commissions]'),null);assert.ok(h.document.querySelector('[data-os-finance-tab=entries]'));
});

test('Profissional enables management AI but does not enable reception; Elite enables both',async t=>{
  let value=planState('profissional');const h=await makeSuite({permissions:()=>({effective_plan:value})});t.after(()=>h.close());await flush();
  assert.ok(nav(h).includes('marketing'));assert.ok(nav(h).includes('consultor'));
  const section=h.document.createElement('section');section.dataset.qcAssistant='';h.document.getElementById('page-suite').append(section);await flush();assert.equal(section.hidden,true);
  value=planState('elite',{revision:'upgraded-2'});h.window.dispatchEvent(new h.window.Event('focus'));await flush();
  const next=h.document.createElement('section');next.dataset.qcAssistant='';h.document.getElementById('page-suite').append(next);await flush();assert.equal(next.hidden,false);
});

test('none preserves account, plan and basic setup without exposing workspace features',async t=>{
  const value=planState('none',{permissions:{'plan.manage':true,'account.manage':true,'business.setup':true}}),h=await makeSuite({permissions:value,initialRoute:'agenda'});t.after(()=>h.close());await flush();
  assert.equal(route(h),'plano');assert.ok(nav(h).includes('plano'));assert.ok(nav(h).includes('configuracoes'));assert.equal(nav(h).includes('agenda'),false);assert.equal(h.calls.some(call=>/\/appointments/.test(call.path)),false);
  await h.window.OrionSuite.open('configuracoes');await flush();assert.equal(route(h),'configuracoes');assert.equal(h.document.querySelector('[data-os-settings-tab=whatsapp]'),null);assert.equal(h.calls.some(call=>/\/qr-channels/.test(call.path)),false);
});

test('permission failures fail closed while regularization remains available',async t=>{
  const h=await makeSuite({initialRoute:'consultor',permissions:()=>{throw Error('Falha sintética');}});t.after(()=>h.close());await flush();
  assert.equal(route(h),'plano');assert.equal(nav(h).includes('consultor'),false);assert.equal(h.calls.some(call=>/\/ai\//.test(call.path)),false);
});

test('a missing permissions envelope fails closed instead of guessing access from an unexpected payload',async t=>{
  const h=await makeSuite({permissions:()=>planState('essencial')});t.after(()=>h.close());await flush();assert.equal(nav(h).includes('clientes'),false);assert.equal(nav(h).includes('consultor'),false);
});

test('a downgrade refreshes the same session, closes restricted dialogs and discards an older response',async t=>{
  let value=planState('elite');const pending=deferred();
  const h=await makeSuite({initialRoute:'consultor',permissions:()=>({effective_plan:value}),respond:path=>path.endsWith('/ai/history')?pending.promise:defaultResponse(path)});t.after(()=>h.close());await flush();
  value=planState('essencial',{revision:'downgrade-2'});h.window.dispatchEvent(new h.window.Event('focus'));await flush();assert.equal(route(h),'plano');assert.equal(nav(h).includes('consultor'),false);
  pending.resolve({items:[{output:'Conteúdo antigo protegido'}]});await flush();assert.equal(h.document.body.textContent.includes('Conteúdo antigo protegido'),false);assert.equal(h.calls.some(call=>/auth\/logout/.test(call.path)),false);
});

test('changing companies discards an in-flight permissions response instead of importing the old plan',async t=>{
  const pending=deferred();let reads=0;
  const h=await makeSuite({permissions:()=>++reads===1?pending.promise:({effective_plan:planState('essencial')})});t.after(()=>h.close());await flush();
  h.window.OrionSuite.sync({tenants:[{id:'20000000-0000-4000-8000-000000000002',name:'Outra empresa',role:'owner'}]});await flush();
  pending.resolve({effective_plan:planState('elite')});await flush();assert.equal(nav(h).includes('marketing'),false);assert.ok(nav(h).includes('clientes'));
});

test('native admin keeps its platform workspace but maintenance uses the customer plan',async t=>{
  for(const maintenance of [false,true]){
    const h=await makeSuite({permissions:planState('essencial'),bridge:{user:{role:'admin',email:'admin@example.invalid',maintenance:{active:maintenance}}}});t.after(()=>h.close());await flush();
    assert.equal(nav(h).includes('consultor'),!maintenance);assert.equal(h.calls.some(call=>call.path.endsWith('/permissions')),maintenance);
  }
});

test('basic professional editing omits advanced commission settings and preserves normal fields',async t=>{
  const p={id:'d1000000-0000-4000-8000-000000000001',name:'Profissional existente',active:true,commission_mode:'per_service',commission_percent:25,commission_config_version:3,accepts_appointments:true,color:'#31ad52'};
  const h=await makeSuite({initialRoute:'funcionarios',permissions:planState('essencial'),respond:(path,method,data)=>{
    if(path.endsWith('/professionals'))return [p];if(path.endsWith('/configure')&&method==='PUT')return {...p,...data};return defaultResponse(path);
  }});t.after(()=>h.close());await flush();h.click('[data-os-professional]');await flush();
  assert.equal(h.document.querySelector('[name=commission_mode]'),null);assert.equal(h.calls.some(call=>call.path.endsWith('/commission-rules')),false);
  h.fill('[name=name]','Nome corrigido');h.submit('[data-os-form=professional]');await flush();const call=h.calls.find(call=>call.method==='PUT');assert.equal(call.data.name,'Nome corrigido');
  for(const key of ['commission_mode','commission_percent','commission_rules'])assert.equal(key in call.data,false,key);
});

test('professional quotas block reactivation without deleting existing staff or their appointments',async t=>{
  const p={id:'d1000000-0000-4000-8000-000000000001',name:'Profissional inativo',active:false,commission_config_version:1};
  const h=await makeSuite({initialRoute:'funcionarios',permissions:planState('essencial',{limits:{max_professionals:2,active_professionals:3,excess_professionals:1}}),respond:path=>path.endsWith('/professionals')?[p]:defaultResponse(path)});t.after(()=>h.close());await flush();
  h.click('[data-os-staff-status-filter=inactive]');await flush();h.click('[data-os-professional-status]');await flush();assert.equal(h.calls.some(call=>call.method!=='GET'),false);assert.match(h.document.body.textContent,/Você atingiu o limite de profissionais/);assert.match(h.document.body.textContent,/Profissional inativo/);
});

test('downgrade closes a real advanced form before it can submit, without logging the user out',async t=>{
  let value=planState('elite');const h=await makeSuite({permissions:()=>({effective_plan:value})});t.after(()=>h.close());await flush();
  const trigger=h.document.createElement('button');trigger.dataset.osAction='new-goal';h.document.getElementById('page-suite').append(trigger);trigger.click();await flush();assert.ok(h.document.querySelector('[data-os-form=goal]'));const form=h.document.querySelector('[data-os-form=goal]');
  value=planState('essencial',{revision:'downgrade-form'});h.window.dispatchEvent(new h.window.Event('focus'));await flush();assert.equal(h.document.getElementById('osDialog').open,false);form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(h.calls.some(call=>call.method!=='GET'),false);
});

test('Essencial settings preserve hidden advanced preferences by omitting them from the basic save',async t=>{
  const data={version:3,can_save:true,business:{contact_phone:'+5567999991111',monthly_goal_cents:500000,weekly_summary_requested:true,auto_review_requested:true,tax_document_kind:'none'}};
  const h=await makeSuite({initialRoute:'configuracoes',permissions:planState('essencial'),respond:(path,method,payload)=>path.endsWith('/preferences')?(method==='PUT'?{...data,business:{...data.business,...payload.business}}:data):defaultResponse(path)});t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('[name=monthly_goal]'),null);assert.equal(h.document.querySelector('[name=weekly_summary_requested]'),null);assert.ok(h.document.querySelector('[name=auto_review_requested]'));
  h.submit('[data-os-form=business-preferences]');await flush();const call=h.calls.find(call=>call.method==='PUT');assert.ok(call);assert.equal('monthly_goal_cents' in call.data.business,false);assert.equal('weekly_summary_requested' in call.data.business,false);assert.equal(call.data.business.auto_review_requested,true);
});

test('Essencial keeps scheduling communications and review settings without submitting advanced return invitations',async t=>{
  const data={available:true,canonical_channel_id:null,notifications:[],reviews:[],settings:{version:2,enabled:false,confirmation:true,professional_notice:true,cancellation:true,rescheduling:true,reschedule_acceptance:true,reminder:true,review:true,return_invitation:true,signature:'Empresa sintética',review_delay_min:60},return_services:[{id:'test-service',name:'Corte',days:30}]};
  const h=await makeSuite({initialRoute:'configuracoes',permissions:planState('essencial'),respond:(path,method)=>path.endsWith('/appointment-communications')?(method==='PUT'?{ok:true}:data):defaultResponse(path)});t.after(()=>h.close());await flush();
  h.click('[data-os-settings=notifications]');await flush();const host=h.document.querySelector('.os-appointment-comms');assert.ok(host);assert.match(host.textContent,/Confirmação|Notificar cancelamentos|Notificar remarcações|Pedir avaliação/);assert.doesNotMatch(host.textContent,/Convidar para agendar novamente|Intervalo de retorno por serviço/);
  const form=host.querySelector('form');form.dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));await flush();const call=h.calls.find(call=>call.method==='PUT');assert.ok(call);assert.equal(call.data.confirmation,true);assert.equal(call.data.cancellation,true);assert.equal(call.data.review,true);assert.equal('return_invitation' in call.data,false);assert.equal('return_intervals' in call.data,false);
});

test('temporary validity schedules its own server recheck and never creates a mutation at expiry',async t=>{
  const now=Date.parse('2030-01-01T00:00:00Z'),jobs=new Map();let clock=now,next=0,value=planState('elite',{valid_until:new Date(now+5000).toISOString()});
  const h=await makeSuite({permissions:()=>({effective_plan:value}),configureWindow:window=>{
    window.Date.now=()=>clock;window.setTimeout=(fn,delay)=>{const id=++next;jobs.set(id,{fn,delay});return id;};window.clearTimeout=id=>jobs.delete(id);
  }});t.after(()=>h.close());await flush();assert.ok(nav(h).includes('consultor'));
  const expiry=[...jobs.values()].find(job=>job.delay===5050);assert.ok(expiry);const reads=h.calls.length;value=planState('none',{revision:'expired'});clock+=5050;expiry.fn();await flush();
  assert.ok(h.calls.length>reads);assert.equal(nav(h).includes('consultor'),false);assert.equal(h.calls.some(call=>call.method!=='GET'),false);assert.equal(route(h),'plano');
});

test('same-origin invalidation signals requery the matching company and dispose cleanly on logout',async t=>{
  const channels=[];class Channel{constructor(name){this.name=name;channels.push(this);}postMessage(data){for(const channel of channels)if(channel!==this&&!channel.closed&&channel.name===this.name)queueMicrotask(()=>channel.onmessage?.({data}));}close(){this.closed=true;}}
  let value=planState('elite');const h=await makeSuite({permissions:()=>({effective_plan:value}),configureWindow:window=>window.BroadcastChannel=Channel});t.after(()=>h.close());await flush();const sender=new Channel('orion-plan-access'),before=h.calls.length;
  sender.postMessage({type:'changed',tenantId:'foreign-company'});await flush();assert.equal(h.calls.length,before);
  value=planState('essencial',{revision:'broadcast-change'});sender.postMessage({type:'changed',tenantId:h.bridge.tenantId});await flush();assert.equal(nav(h).includes('consultor'),false);assert.ok(h.calls.length>before);assert.equal(h.calls.some(call=>call.method!=='GET'),false);
  h.window.OrionSuite.destroy();assert.ok(channels[0].closed);const after=h.calls.length;sender.postMessage({type:'changed',tenantId:h.bridge.tenantId});await flush();assert.equal(h.calls.length,after);sender.close();
});
