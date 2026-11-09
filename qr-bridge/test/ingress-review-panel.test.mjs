/** State-machine tests with a tiny in-memory DOM; no browser or live API. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element{
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.listeners={};this.disabled=false;this.value='';this.checked=false;
    this.classList={add(){},remove(){}};}
  append(...items){this.children.push(...items);}
  get options(){return this.children.filter(child=>child.tag==='option');}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(key,value){this[key]=value;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async fire(name='click'){if(this.disabled&&name==='click')return;await this.listeners[name]?.();}
}
class Option extends Element{constructor(text,value){super('option',text);this.value=value;}}
const scope={tenant_id:'a'.repeat(36),channel_id:'b'.repeat(36),session_digest:'c'.repeat(64),auth_epoch:'d'.repeat(64),owner:'e'.repeat(36),fence:7};
const trace='a'.repeat(20),fingerprint='b'.repeat(64),receipt='c'.repeat(64),build='d'.repeat(40);
const dry={version:1,mode:'dry_run',scope,snapshot_sha256:'e'.repeat(64),entries:[{trace,fingerprint,at:1794000000000,sequence:1,
  state:'blocked',blocked_reason:'business_started',phase:null,eligible_as:['operator_attested_prepare_failed']}],total_entries:1,truncated:false,mutation:false};
const find=(root,predicate)=>[root,...root.children.flatMap(child=>find(child,predicate))].filter(predicate);
const buttons=(root,text)=>find(root,item=>item.tag==='button'&&item.textContent===text);
const inputs=root=>find(root,item=>item.tag==='input');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject};};
function fixture({role='admin',request=async()=>structuredClone(dry)}={}){
  const root=new Element('section'),window={},calls=[];let active=true,count=0;
  vm.runInNewContext(fs.readFileSync(new URL('../../app/static/orion-ingress-review.js',import.meta.url),'utf8'),{
    window,document:{createElement:tag=>new Element(tag),createTextNode:text=>new Element('#text',text)},
    crypto:{randomUUID:()=>`3570002d-8506-42cf-b1d2-${String(++count).padStart(12,'0')}`},Date,RegExp,Number,Set,Object,Option});
  const mount=()=>window.OrionIngressReview.mount({root,role,isCurrent:()=>active,request:async(...args)=>{calls.push(structuredClone(args));return request(...args);}});
  mount();return {root,window,calls,mount,deactivate:()=>{active=false},operations:()=>count};
}
async function prepare(f){
  await buttons(f.root,'Consultar entradas')[0].fire();
  const all=inputs(f.root);all[1].checked=true;all[2].value=receipt;all[3].value=build;
  await buttons(f.root,'Revisar liberação')[0].fire();
}
test('panel is hidden for members/support and never requests privileged diagnostics',()=>{
  for(const role of ['member','support']){const f=fixture({role});assert.equal(f.root.children.length,0);assert.equal(f.calls.length,0);}
});
test('query is a read-only dry-run; preparing requires explicit valid selection and proof',async()=>{
  const f=fixture();await buttons(f.root,'Consultar entradas')[0].fire();
  assert.equal(f.calls.length,1);assert.equal(f.calls[0][0],'/api/v1/admin/whatsapp/ingress-review');
  assert.equal(f.calls[0][1],'POST');assert.equal(f.calls[0][2].mode,'dry_run');
  await buttons(f.root,'Revisar liberação')[0].fire();assert.equal(buttons(f.root,'Liberar sem reprocessar').length,0);
  const all=inputs(f.root);all[1].checked=true;all[2].value=receipt;all[3].value=build;
  all[4].value='2026-11-06T19:00:00';await buttons(f.root,'Revisar liberação')[0].fire();
  assert.equal(buttons(f.root,'Liberar sem reprocessar').length,0);
  all[4].value='2026-11-06T19:00:00Z';await buttons(f.root,'Revisar liberação')[0].fire();
  assert.equal(buttons(f.root,'Liberar sem reprocessar').length,1);assert.equal(f.calls.length,1);
});
test('uncertain apply keeps exactly the same operation and frozen body on explicit retry',async()=>{
  let apply=0;const f=fixture({request:async(_path,_method,payload)=>{
    if(payload.mode==='dry_run')return structuredClone(dry);
    if(++apply===1)throw Error('Synthetic transport result unavailable');
    return {replayed:true,operation_id:payload.operation_id,backup:{reference:payload.operation_id,sha256:'a'.repeat(64)}};
  }});
  await prepare(f);await buttons(f.root,'Liberar sem reprocessar')[0].fire();
  assert.equal(f.calls.length,2);assert.ok(inputs(f.root).every(input=>input.disabled));
  await buttons(f.root,'Conferir ou repetir a mesma operação')[0].fire();
  assert.equal(f.calls.length,3);assert.deepEqual(f.calls[1],f.calls[2]);assert.equal(f.operations(),1);
  assert.equal(f.calls[1][2].entries[0].disposition,'do_not_replay');
  assert.equal(f.calls[1][2].entries[0].evidence.receipt_sha256,receipt);
});
test('editing prepared evidence invalidates approval and stale click cannot apply',async()=>{
  const f=fixture();await prepare(f);const stale=buttons(f.root,'Liberar sem reprocessar')[0];
  const all=inputs(f.root);all[2].value='f'.repeat(64);await all[2].fire('input');
  assert.equal(buttons(f.root,'Liberar sem reprocessar').length,0);await stale.fire();assert.equal(f.calls.length,1);
});
test('reset and session changes discard delayed results without remounting privileged data',async()=>{
  const pending=deferred(),f=fixture({request:()=>pending.promise});
  const action=buttons(f.root,'Consultar entradas')[0].fire();f.window.OrionIngressReview.reset();pending.resolve(structuredClone(dry));await action;
  assert.equal(f.root.children.length,0);assert.equal(f.calls.length,1);
  const next=deferred(),g=fixture({request:()=>next.promise});const loading=buttons(g.root,'Consultar entradas')[0].fire();g.deactivate();next.resolve(structuredClone(dry));await loading;
  assert.equal(buttons(g.root,'Revisar liberação').length,0);
});
test('invalid filters and more than two selected entries cannot be submitted',async()=>{
  const f=fixture();inputs(f.root)[0].value=[trace,'b'.repeat(20),'c'.repeat(20)].join(' ');
  await buttons(f.root,'Consultar entradas')[0].fire();assert.equal(f.calls.length,0);
  const three=structuredClone(dry);three.entries=[0,1,2].map(i=>({...three.entries[0],trace:String(i).repeat(20)}));
  const g=fixture({request:async()=>three});await buttons(g.root,'Consultar entradas')[0].fire();
  const all=inputs(g.root);for(let i=0;i<3;i++){all[1+i*4].checked=true;all[2+i*4].value=receipt;all[3+i*4].value=build;}
  await buttons(g.root,'Revisar liberação')[0].fire();assert.equal(buttons(g.root,'Liberar sem reprocessar').length,0);assert.equal(g.calls.length,1);
});


test('subscription device operator selects a tenant-scoped path and never sends old messages',async()=>{
  const device='40000000-0000-4000-8000-000000000001',channel='20000000-0000-4000-8000-000000000001';
  const report={...structuredClone(dry),scope:{tenant_id:'10000000-0000-4000-8000-000000000001',
    channel_id:channel,device_id:device,generation:'50000000-0000-4000-8000-000000000001',
    settings_revision:3,auth_epoch:'f'.repeat(64),
    owner:'60000000-0000-4000-8000-000000000001',fence:8},
    entries:[{...structuredClone(dry.entries[0]),eligible_as:['operator_attested_uncertain']}]};
  const f=fixture({request:async(path,method,payload)=>{
    if(path.endsWith('/ingress-devices'))return {items:[{id:device,company:'Empresa sintética',
      name:'WhatsApp',blocked:4,stale:false}]};
    if(payload?.mode==='dry_run')return report;
    return {replayed:false,operation_id:payload.operation_id,backup:{reference:payload.operation_id,sha256:'f'.repeat(64)}};
  }});
  await buttons(f.root,'Buscar dispositivos com mensagens retidas')[0].fire();
  const selector=find(f.root,node=>node.tag==='select')[0];
  assert.equal(selector.options.length,2);
  selector.value=device;await selector.fire('change');
  await buttons(f.root,'Consultar entradas')[0].fire();
  assert.equal(f.calls[1][0],'/api/v1/admin/whatsapp/devices/'+device+'/ingress-review');
  assert.equal(f.calls[1][1],'POST');
  const all=inputs(f.root);
  all[1].checked=true;all[2].value=receipt;all[3].value=build;
  await buttons(f.root,'Revisar liberação')[0].fire();
  await buttons(f.root,'Liberar sem reprocessar')[0].fire();
  assert.equal(f.calls.length,3);
  assert.equal(f.calls[2][0],f.calls[1][0]);
  assert.equal(f.calls[2][2].entries[0].evidence.kind,'operator_attested_uncertain');
  assert.equal(f.calls[2][2].entries[0].disposition,'do_not_replay');
  assert.equal(f.calls.some(call=>/\/send|\/replay|\/relink|\/restart/.test(call[0])),false);
});

test('operator cannot switch device after query without invalidating prepared authorization',async()=>{
  const device='40000000-0000-4000-8000-000000000001';
  const f=fixture({request:async(path,_method,payload)=>path.endsWith('/ingress-devices')?
    {items:[{id:device,name:'W',company:'C',blocked:2}]}:structuredClone(dry)});
  await buttons(f.root,'Buscar dispositivos com mensagens retidas')[0].fire();
  await buttons(f.root,'Consultar entradas')[0].fire();
  const selector=find(f.root,node=>node.tag==='select')[0];
  selector.value=device;await selector.fire('change');
  assert.equal(buttons(f.root,'Liberar sem reprocessar').length,0);
  assert.equal(buttons(f.root,'Revisar liberação').length,0);
  assert.equal(f.calls.filter(call=>call[1]==='POST').length,1);
});
