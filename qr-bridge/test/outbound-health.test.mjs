import test from 'node:test';
import assert from 'node:assert/strict';
import {makeOutboundHealth} from '../src/outbound-health.mjs';

test('two recent pre-relay transport failures expose degraded reply readiness until a successful reply',()=>{
  let now=1000;const health=makeOutboundHealth({now:()=>now});
  assert.equal(health.failure('recipient_lookup'),false);
  assert.equal(health.apply({state:'connected'}).state,'connected');
  now+=2000;assert.equal(health.failure('recipient_lookup'),true);
  assert.deepEqual(health.apply({state:'connected'}),{state:'degraded',reason:'outbound_preparation_failed',action:'inspect_outbound_preparation',
    outbound_preparation_failures:2,outbound_preparation_at:now,outbound_preparation_degraded:true});
  assert.equal(health.apply({state:'needs_attention',reason:'ingress_review_required'}).reason,'ingress_review_required');
  assert.equal(health.success(),true);
  assert.equal(health.apply({state:'connected'}).state,'connected');
});

test('invalid content and distant single failures do not claim a broken connection',()=>{
  let now=1000;const health=makeOutboundHealth({now:()=>now});
  assert.equal(health.failure('content'),false);
  assert.equal(health.failure('recipient_lookup'),false);
  now+=600001;assert.equal(health.failure('recipient_lookup'),false);
  assert.equal(health.apply({state:'connected'}).state,'connected');
});

test('degraded outbound state survives a socket replacement until a successful reply',()=>{
  const first=makeOutboundHealth({now:()=>2000});first.failure('message_generation');first.failure('message_generation');
  const replacement=makeOutboundHealth({now:()=>3000,previous:first.apply({state:'connected'})});
  assert.equal(replacement.apply({state:'connected'}).state,'degraded');
  assert.equal(replacement.success(),true);
  assert.equal(replacement.apply({state:'connected'}).state,'connected');
});
