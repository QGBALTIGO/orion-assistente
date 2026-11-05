import test from 'node:test';
import assert from 'node:assert/strict';
import {makeDevices,device,listing,flush} from './subscription-devices-harness.mjs';

test('degraded connection shows recovery evidence without a false connected badge or a write',async t=>{
  const item=device({status:'degraded',connection_health:{state:'reconnecting',transport_observed:true,
    last_activity_at:'2026-11-05T16:00:00Z',interrupted_at:'2026-11-05T16:01:00Z',
    reconnect_at:'2026-11-05T16:01:10Z',reconnect_attempts:2}});
  const h=await makeDevices({data:listing([item])});t.after(()=>h.close());await flush();
  assert.match(h.document.body.textContent,/Conexão degradada/);
  assert.match(h.document.body.textContent,/Último sinal da conexão/);
  assert.match(h.document.body.textContent,/Recuperação automática em andamento \(tentativa 2\)/);
  assert.match(h.document.body.textContent,/Interrupção iniciada/);
  assert.equal(h.document.querySelector('.os-device-status-line .os-success'),null);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('stale state and intervention show a useful action without starting another session',async t=>{
  const item=device({status:'needs_attention',connection_health:{state:'needs_attention',stale:true,
    reason:'connection_replaced',action:'inspect_session_ownership',owner:'PRIVATE-OWNER'}});
  const h=await makeDevices({data:listing([item])});t.after(()=>h.close());await flush();
  assert.match(h.document.body.textContent,/estado está desatualizado/);
  assert.match(h.document.body.textContent,/Confira os dispositivos vinculados/);
  assert.doesNotMatch(h.document.body.textContent,/PRIVATE-OWNER/);
  h.click('[data-sd-action="open-connect"]');await flush();
  assert.match(h.document.body.textContent,/Precisa de atenção/);
  assert.equal(h.document.querySelector('[data-sd-action="connect"]'),null);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('confirmed logout offers an explicit relink while opening it never performs a write',async t=>{
  const original=device(),item=device({status:'needs_attention',capabilities:{...original.capabilities,relink:true},
    connection_health:{state:'needs_attention',action:'reauthenticate',reason:'logged_out'}});
  const h=await makeDevices({data:listing([item])});t.after(()=>h.close());await flush();
  h.click('[data-sd-action="open-connect"]');await flush();
  assert.match(h.document.body.textContent,/WhatsApp desvinculou/);
  assert.ok(h.document.querySelector('[data-sd-action="relink"]'));
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('retained incoming review shows paused replies and never offers an automatic relink',async t=>{
  const item=device({status:'needs_attention',connection_health:{state:'needs_attention',
    action:'review_incoming_messages',ingress_pending:2,ingress_blocked:1,journal:{body:'PRIVATE-MESSAGE'}}});
  const h=await makeDevices({data:listing([item])});t.after(()=>h.close());await flush();
  assert.match(h.document.body.textContent,/precisa de revisão pelo suporte/);
  assert.match(h.document.body.textContent,/respostas automáticas estão pausadas/);
  assert.doesNotMatch(h.document.body.textContent,/PRIVATE-MESSAGE/);
  assert.equal(h.document.querySelector('.os-device-status-line .os-success'),null);
  h.click('[data-sd-action="open-connect"]');await flush();
  assert.equal(h.document.querySelector('[data-sd-action="relink"]'),null);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('pending admission and unavailable storage have separate useful explanations',async t=>{
  for(const [item,expected] of [
    [device({status:'degraded',connection_health:{state:'degraded',ingress_pending:3,ingress_blocked:0}}),
      /3 mensagens recebidas aguardam processamento/],
    [device({status:'needs_attention',connection_health:{state:'needs_attention',ingress_storage_unavailable:true}}),
      /conferir o armazenamento desta conexão/]]){
    const h=await makeDevices({data:listing([item])});t.after(()=>h.close());await flush();
    assert.match(h.document.body.textContent,expected);
    assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  }
});
