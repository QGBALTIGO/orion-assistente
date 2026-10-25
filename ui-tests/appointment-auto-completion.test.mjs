import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,defaultResponse,flush} from './harness.mjs';

const readOnly=path=>path.includes('/appointment-auto-completion');
async function create(t,role='owner'){
  const calls=[];
  let version=0,enabled=false;
  const h=await makeSuite({initialRoute:'agenda',role,respond:(path,method='GET',data)=>{
    if(readOnly(path)){
      calls.push({path,method,data});
      if(method==='PUT'){
        version++;
        enabled=data.enabled;
      }
      return {configuration:{enabled,enabled_at:enabled?'2026-10-25T12:00:00+00:00':null},
        version,can_manage:role==='owner'};
    }
    if(path.includes('/appointments?'))return {items:[],total:0,limit:50,offset:0,has_more:false};
    if(path.includes('/google/status'))return {sync_status:'idle',connected:false};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  return {h,calls,get enabled(){return enabled}};
}

test('an owner has an explicit Agenda option; no automatic completion is enabled on page load',async t=>{
  const {h,calls}=await create(t);
  assert.ok(h.document.querySelector('[data-os-action=agenda-auto-completion]'));
  assert.equal(calls.length,0,'reading the agenda cannot enable auto-completion');
  h.click('[data-os-action=agenda-auto-completion]');await flush();
  const form=h.document.querySelector('[data-os-form=appointment-auto-completion]');
  assert.ok(form);
  assert.equal(form.elements.enabled.checked,false);
  assert.match(form.textContent,/pagamento recebido|pagamento/);
  assert.match(form.textContent,/falta|faltas/);
  assert.deepEqual(calls.map(c=>c.method),['GET']);
  assert.deepEqual(h.errors,[]);
});

test('an owner can opt in explicitly and uses versioned authenticated PUT',async t=>{
  const {h,calls}=await create(t);
  h.click('[data-os-action=agenda-auto-completion]');await flush();
  const form=h.document.querySelector('[data-os-form=appointment-auto-completion]');
  form.elements.enabled.checked=true;
  h.submit('[data-os-form=appointment-auto-completion]');await flush();
  const saved=calls.find(x=>x.method==='PUT');
  assert.ok(saved);
  assert.equal(saved.path.includes('/management/appointment-auto-completion'),true);
  assert.deepEqual(JSON.parse(JSON.stringify(saved.data)),{expected_version:0,enabled:true});
  assert.ok(h.document.getElementById('page-suite'));
  assert.deepEqual(h.errors,[]);
});

test('read-only members cannot toggle potentially billable completion',async t=>{
  const {h,calls}=await create(t,'viewer');
  assert.equal(h.document.querySelector('[data-os-action=agenda-auto-completion]'),null);
  assert.deepEqual(calls,[]);
});

test('Google Agenda still has its own control; enabling completion never changes Google connection',async t=>{
  const {h,calls}=await create(t);
  assert.ok(h.document.querySelector('[data-os-action=agenda-google]'));
  h.click('[data-os-action=agenda-auto-completion]');await flush();
  assert.deepEqual(calls.map(x=>x.method),['GET']);
  assert.equal(h.calls.some(x=>x.path.includes('/google/connect')&&x.method!=='GET'),false);
});
