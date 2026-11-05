import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {EventEmitter} from 'node:events';
import {Boom} from '@hapi/boom';
import {getErrorCodeFromStreamError,getCodeFromWSError} from '@whiskeysockets/baileys';
import {disconnectEvidence,safeDisconnectEvidence} from '../src/disconnect-evidence.mjs';
import {disconnectPolicy,makeConnectionHealth} from '../src/connection-health.mjs';

const privateValue='private-phone-5511999999901@lid/private-token/customer-body';
const unknown={source:'unknown',condition:'unknown',provider_reason_numeric:null,websocket_code:null};
const source=fs.readFileSync(new URL('../node_modules/@whiskeysockets/baileys/lib/Socket/socket.js',import.meta.url),'utf8');
function nativeFailures(){
  const events=new EventEmitter(),errors=[];
  const context=vm.createContext({ws:events,end:error=>errors.push(error),Boom,getErrorCodeFromStreamError,getCodeFromWSError,logger:{error(){}}});
  for(const marker of ["ws.on('CB:stream:error',", "ws.on('CB:failure',"]){
    const begin=source.indexOf(marker),end=source.indexOf('\n    });',begin);assert.ok(begin>0&&end>begin);
    vm.runInContext(source.slice(begin,end+'\n    });'.length),context);
  }
  const mapper=source.slice(source.indexOf('function mapWebSocketError(handler)'),source.indexOf('//# sourceMappingURL'));
  assert.ok(mapper.startsWith('function mapWebSocketError'));vm.runInContext(mapper,context);
  return {stream:node=>{events.emit('CB:stream:error',node);return errors.at(-1);},
    failure:attrs=>{events.emit('CB:failure',{tag:'failure',attrs});return errors.at(-1);},
    websocket:error=>{context.mapWebSocketError(value=>errors.push(value))(error);return errors.at(-1);}};
}

test('the actual pinned SDK yields distinct structured500 origins without asserting Signal/session corruption',()=>{
  const native=nativeFailures();
  const stream=native.stream({tag:'stream:error',attrs:{code:'500',id:privateValue,from:privateValue},
    content:[{tag:privateValue,attrs:{token:privateValue},content:Buffer.from(privateValue)}]});
  const provider=native.failure({reason:'500',id:privateValue,location:privateValue});
  const websocket=native.websocket(Object.assign(Error(privateValue),{code:'WS_ERR_INVALID_UTF8',private:privateValue}));
  const unspecified=new Boom(privateValue,{statusCode:500,data:{stack:privateValue}});
  assert.ok([stream,provider,websocket,unspecified].every(error=>error.output.statusCode===500));
  assert.deepEqual(disconnectEvidence(stream),{source:'stream_error',condition:'unknown',provider_reason_numeric:500,websocket_code:null});
  assert.deepEqual(disconnectEvidence(provider),{source:'provider_failure',condition:'unknown',provider_reason_numeric:500,websocket_code:null});
  assert.deepEqual(disconnectEvidence(websocket),{source:'websocket_error',condition:'unknown',provider_reason_numeric:null,websocket_code:'WS_ERR_INVALID_UTF8'});
  assert.deepEqual(disconnectEvidence(unspecified),unknown);
  assert.ok([stream,provider,websocket,unspecified].every(error=>disconnectPolicy(error.output.statusCode).reason==='session_recovery'));
  assert.doesNotMatch(JSON.stringify([stream,provider,websocket,unspecified].map(disconnectEvidence)),/private|5511999999901|Signal|bad_session/);
});

test('only SDK-supported conflict/restart stream conditions leave the helper, never arbitrary node tags or attrs',()=>{
  const native=nativeFailures();
  const conflict=native.stream({tag:'stream:error',attrs:{},content:[{tag:'conflict',attrs:{type:privateValue}}]});
  assert.equal(conflict.output.statusCode,440);assert.equal(disconnectEvidence(conflict).condition,'conflict');
  const restart=native.stream({tag:'stream:error',attrs:{code:'515'},content:[{tag:privateValue,attrs:{}}]});
  assert.equal(disconnectEvidence(restart).condition,'restart_required');
  const absent=native.stream({tag:'stream:error',attrs:{},content:[]});assert.equal(absent.output.statusCode,500);
  assert.deepEqual(disconnectEvidence(absent),{...unknown,source:'stream_error'});
});

test('native transport codes have a finite allowlist; unknown error codes and messages are never emitted',()=>{
  const native=nativeFailures();
  for(const code of ['ETIMEDOUT','ECONNRESET','ENOTFOUND']){
    const error=native.websocket(Object.assign(Error(privateValue),{code}));assert.equal(error.output.statusCode,408);
    assert.deepEqual(disconnectEvidence(error),{...unknown,source:'websocket_error',websocket_code:code});
  }
  const error=native.websocket(Object.assign(Error(privateValue),{code:privateValue}));
  assert.deepEqual(disconnectEvidence(error),{...unknown,source:'websocket_error'});
  for(const key of ['message','stack'])Object.defineProperty(error,key,{get(){assert.fail('exception text must not be read');}});
  for(const key of ['message','stack'])Object.defineProperty(error.data,key,{get(){assert.fail('native error text must not be read');}});
  assert.deepEqual(disconnectEvidence(error),{...unknown,source:'websocket_error'});
});

test('numeric status is never a crypto diagnosis;408/428 retain deliberately broad SDK categories',()=>{
  assert.deepEqual(disconnectEvidence(new Boom('Invalid account signature')),{...unknown});
  assert.deepEqual(disconnectEvidence(new Boom('Timed Out',{statusCode:408,data:{stack:privateValue}})),
    {...unknown,source:'sdk_status',condition:'timeout_or_lost'});
  assert.deepEqual(disconnectEvidence(new Boom('Connection Terminated by Server',{statusCode:428})),
    {...unknown,source:'sdk_status',condition:'connection_closed'});
});

test('malformed provider reason, invalid types, hostile getters and arbitrary error shapes fail closed',()=>{
  for(const reason of [privateValue,'500\n','0500','5e2',true,500.5,5511999999901,{},null])
    assert.deepEqual(disconnectEvidence(new Boom(privateValue,{statusCode:500,data:{reason}})),unknown);
  assert.deepEqual(disconnectEvidence({output:{statusCode:500},data:{reason:'500'}}),unknown);
  assert.deepEqual(disconnectEvidence({get isBoom(){throw Error(privateValue);}}),unknown);
  assert.deepEqual(disconnectEvidence(undefined),unknown);
  assert.deepEqual(disconnectEvidence(new Boom(privateValue,{statusCode:500,data:{reason:'401'}})),unknown);
  let reads=0;assert.deepEqual(safeDisconnectEvidence({get source(){return ++reads===1?'stream_error':privateValue;},
    get condition(){return ++reads===2?'conflict':privateValue;}}),{...unknown,source:'stream_error',condition:'conflict'});
  reads=0;assert.deepEqual(safeDisconnectEvidence({source:'websocket_error',get websocket_code(){return ++reads===1?'ECONNRESET':privateValue;}}),
    {...unknown,source:'websocket_error',websocket_code:'ECONNRESET'});
});

test('health/log boundary independently sanitizes fields and clones snapshots without altering recovery policy',()=>{
  const events=[],health=makeConnectionHealth({emit:event=>events.push(event)}),policy=disconnectPolicy(500,1,{random:()=>.5});
  const evidence={source:'stream_error',condition:'conflict',provider_reason_numeric:500,websocket_code:privateValue,raw:privateValue};
  const before=JSON.stringify(policy);health.disconnected(policy,2,evidence);
  const expected={source:'stream_error',condition:'conflict',provider_reason_numeric:500,websocket_code:null};
  assert.deepEqual(events.at(-1).disconnect_evidence,expected);assert.deepEqual(health.snapshot().disconnect_evidence,expected);
  assert.equal(JSON.stringify(policy),before);evidence.source=privateValue;
  const view=health.snapshot();view.disconnect_evidence.source=privateValue;
  assert.deepEqual(health.snapshot().disconnect_evidence,expected);assert.doesNotMatch(JSON.stringify(events),/private-phone|private-token|customer-body/);
  health.disconnected(policy,3,{source:privateValue,condition:privateValue,provider_reason_numeric:5511999999901});
  assert.deepEqual(health.snapshot().disconnect_evidence,unknown);
  assert.deepEqual(safeDisconnectEvidence({get source(){throw Error(privateValue);}}),unknown);
  health.stop();
});
