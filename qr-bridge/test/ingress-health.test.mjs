import test from 'node:test';
import assert from 'node:assert/strict';
import {withIngressHealth} from '../src/ingress-health.mjs';
import {makeConnectionHealth} from '../src/connection-health.mjs';
import {EventEmitter} from 'node:events';

test('transport remains observable while durable admission backlog prevents false readiness',()=>{
  const transport={state:'connected',transport_observed:true,last_activity_at:1000};
  const result=withIngressHealth(transport,{state:'ok',pending:2,blocked:0,oldest_at:900});
  assert.equal(result.state,'degraded');assert.equal(result.action,'wait_ingress_recovery');
  assert.equal(result.transport_observed,true);assert.equal(result.last_activity_at,1000);
  assert.equal(result.ingress_pending,2);assert.equal(result.ingress_oldest_at,900);
  assert.equal(transport.state,'connected');
  assert.equal(withIngressHealth(transport,{state:'ok',pending:0,blocked:0}).state,'connected');
});
test('retained review entries and failed storage require review without prompting a new QR',()=>{
  assert.equal(withIngressHealth({state:'connected'},{pending:0,blocked:1}).action,'review_incoming_messages');
  for(const state of ['full','unavailable']){
    const value=withIngressHealth({state:'connected'},{state,pending:1,blocked:0,error_code:'PRIVATE_PATH'});
    assert.equal(value.state,'needs_attention');assert.equal(value.action,'inspect_ingress_storage');
    assert.equal(value.ingress_storage_unavailable,true);assert.doesNotMatch(JSON.stringify(value),/PRIVATE_PATH/);
  }
});
test('provider or ownership intervention survives backlog and journal fields are allowlisted',()=>{
  const value=withIngressHealth({state:'needs_attention',action:'reauthenticate',reason:'logged_out'},
    {state:'full',pending:-1,blocked:Infinity,oldest_at:'private',message:'PRIVATE_CONTENT',phone:'PRIVATE_PHONE'});
  assert.equal(value.action,'reauthenticate');assert.equal(value.reason,'logged_out');
  assert.equal(value.ingress_pending,0);assert.equal(value.ingress_blocked,0);assert.equal(value.ingress_oldest_at,null);
  assert.doesNotMatch(JSON.stringify(value),/PRIVATE_/);
  const transport={state:'connected'};assert.equal(withIngressHealth(transport,null),transport);
});

test('passive monitoring reports admission backlog and recovery without changing transport timers',()=>{
  let time=1000,queued,diag={state:'ok',pending:1,blocked:0,oldest_at:900};const events=[];
  const socket=Object.assign(new EventEmitter(),{isOpen:true});
  const health=makeConnectionHealth({now:()=>time,emit:value=>events.push(value),
    schedule:fn=>{queued=fn;return 1;},cancel:()=>{},readiness:value=>withIngressHealth(value,diag)});
  health.connecting();health.watch(socket);health.connected();socket.emit('message');
  assert.equal(health.snapshot().state,'degraded');time+=60000;queued();
  const pending=events.findLast(value=>value.event==='health_sample');
  assert.equal(pending.state,'degraded');assert.equal(pending.ingress_pending,1);
  diag={state:'ok',pending:0,blocked:1};time+=60000;socket.emit('message');queued();
  assert.equal(health.snapshot().action,'review_incoming_messages');
  assert.equal(events.findLast(value=>value.event==='health_sample').state,'needs_attention');
  diag={state:'ok',pending:0,blocked:0};socket.emit('message');assert.equal(health.snapshot().state,'connected');health.stop();
});

test('unavailable journal diagnostic cannot stop passive sampling or leak its exception',()=>{
  let queued,time=1000;const events=[];
  const health=makeConnectionHealth({now:()=>time,emit:value=>events.push(value),
    readiness:()=>{throw Error('PRIVATE-FILESYSTEM-ERROR');},schedule:fn=>{queued=fn;return 1;},cancel:()=>{}});
  health.connecting();health.watch(Object.assign(new EventEmitter(),{isOpen:true}));health.connected();
  assert.equal(health.snapshot().action,'inspect_ingress_storage');time+=60000;queued();
  assert.equal(typeof queued,'function');assert.equal(events.findLast(value=>value.event==='health_sample').state,'needs_attention');
  assert.doesNotMatch(JSON.stringify(events),/PRIVATE-FILESYSTEM-ERROR/);health.stop();
});
