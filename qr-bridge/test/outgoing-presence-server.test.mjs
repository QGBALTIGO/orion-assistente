/** The real primary raw sender, with synthetic socket/clock only. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import * as sdk from '@whiskeysockets/baileys';
import {validPhone,normalizeFromJid} from '../src/core.mjs';
import {makeOutgoingPresence} from '../src/outgoing-presence.mjs';
import {makeMessageRetryStore} from '../src/message-retry-store.mjs';
import {sendStoredText} from '../src/reliable-send.mjs';
import {resolvePilotRecipient} from '../src/pilot-recipient.mjs';
import {deliveryPolicy,requiresPreRelayCache} from '../src/pilot-send-policy.mjs';
import {resolveTransactionalRecipient} from '../src/transactional-recipient.mjs';
import {presenceClock,presenceFlush} from './helpers/presence-clock.mjs';
import {createAssistantPresentationStore,relayAssistantPresentation,assistantNativeMode} from '../src/assistant-presentation.mjs';

const phone='5511999999901',self='5511999999902';
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
const begin=source.indexOf('async function sendLegacyRaw('),end=source.indexOf('\nconst server=http.createServer',begin);
assert.ok(begin>0&&end>begin);const sender=source.slice(begin,end);
function fixture({reliable=true}={}){
  const clock=presenceClock(),presence=[],relays=[],history=[],retryStore=makeMessageRetryStore({clock:clock.now});let valid=true,authorized=true;
  const logger={child(){return this;},trace(){},debug(){},info(){},warn(){},error(){}};
  const socket={user:{id:self+':7@s.whatsapp.net'},sendPresenceUpdate:async(type,jid)=>presence.push({type,jid,at:clock.now()}),
    onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],
    async relayMessage(jid,message,options){assert.deepEqual(retryStore.get({id:options.messageId,remoteJid:jid,fromMe:true}),message);relays.push({jid,message,options});},
    async sendMessage(jid,{text}){relays.push({jid,text});return sdk.generateWAMessage(jid,{text},{userJid:this.user.id,logger});}};
  const coordinator=makeOutgoingPresence({socket,isConnected:()=>valid,now:clock.now,schedule:clock.schedule,cancel:clock.cancel});
  const map=new WeakMap([[socket,coordinator]]),context=vm.createContext({validPhone,normalizeFromJid,socket,status:'connected',generation:1,
    sendCapacity:{acquire:async()=>{}},legacyCentral:{transportCurrent:()=>valid},pairedNumber:self,
    deliveryPolicy:reliable?deliveryPolicy:()=>({reliable:false,freshDevices:true}),
    requiresPreRelayCache:reliable?requiresPreRelayCache:()=>false,resolvePilotRecipient,resolveTransactionalRecipient,pilotNumber:()=>phone,
    process:{env:{ORION_RELIABLE_SEND:reliable?'1':'0',QR_PILOT_RELIABLE_SEND:reliable?'1':'0',QR_AI_PILOT_NUMBER:phone}},
    socketDeviceCaches:new WeakMap(),socketOutgoingPresence:map,sendStoredText,retryStore,
    generateWAMessage:sdk.generateWAMessage,generateMessageIDV2:sdk.generateMessageIDV2,logger,
    retryDiagnostics:{prepared:0,relayAccepted:0},deliveryReceipts:new Map(),deliveryStart:new Map(),
    transport:{record(){},tag:()=> 'synthetic'},pushMessage:row=>history.push(row),Date,Error});
  vm.runInContext(sender,context);
  return {clock,presence,relays,history,retryStore,coordinator,socket,context,
    send:(options={})=>context.sendLegacyRaw(phone,'Olá',{...options,isCurrent:()=>valid,assertAuthorized:async()=>{if(!authorized)throw Error('policy_denied');}}),
    revoke(){valid=false;},deny(){authorized=false;}};
}

for(const reliable of [true,false])test(`primary ${reliable?'reliable':'retained fallback under synthetic policy'} sender paces once and preserves its protocol/cache without duplicate network sends`,async()=>{
  const f=fixture({reliable});let done=false;const sent=f.send().then(value=>{done=true;return value;});
  await f.clock.advance(799);assert.equal(done,false);assert.equal(f.relays.length,0);
  assert.deepEqual(f.presence.map(item=>item.type),['composing']);
  await f.clock.advance(1);const result=await sent;assert.equal(result.accepted,true);assert.equal(f.relays.length,1);
  assert.equal(f.retryStore.size(),1);assert.deepEqual(f.presence.map(item=>item.type),['composing','paused']);f.coordinator.close();
});

test('primary AI preparation is credited by its eventual raw transport, with one composing lease',async()=>{
  const f=fixture(),lease=f.coordinator.begin({phone,chatJid:phone+'@s.whatsapp.net',isCurrent:()=>true});
  await f.clock.advance(2000);await lease.beforeReply('Olá');const result=await f.send();
  assert.equal(result.accepted,true);assert.equal(f.relays.length,1);assert.equal(f.clock.now(),2000);
  assert.deepEqual(f.presence.map(item=>item.type),['composing','paused']);lease.stop();
});

test('appointment presence follows only the PN normalized in the original provider lookup, while authorization/history keep the original recipient',async()=>{
  const f=fixture(),normalized=phone.slice(0,4)+phone.slice(5),lookups=[];let checks=0;
  f.socket.onWhatsApp=async candidate=>{lookups.push(candidate);return [{jid:normalized+'@s.whatsapp.net',exists:true}];};
  const sending=f.context.sendLegacyRaw(phone,'Olá',{source:'appointment',assertAuthorized:async()=>{checks++;}});
  await f.clock.advance(799);assert.equal(f.relays.length,0);assert.deepEqual(lookups,[phone]);
  assert.deepEqual(f.presence.map(value=>value.jid),[normalized+'@s.whatsapp.net']);
  await f.clock.advance(1);const result=await sending;assert.equal(result.accepted,true);assert.ok(checks>=4);
  assert.equal(f.relays.length,1);assert.equal(f.relays[0].jid,normalized+'@s.whatsapp.net');
  assert.ok(f.history.length>0&&f.history.every(value=>value.phone===phone));
  assert.deepEqual(f.presence.map(value=>value.type),['composing','paused']);
});

test('primary oldAPI pacing opt-out never calls an unsupported extra grant or adds a delay',async()=>{
  const f=fixture();const result=await f.context.sendLegacyRaw(phone,'Olá',{pacing:false,isCurrent:()=>true});
  assert.equal(result.accepted,true);assert.equal(f.clock.now(),0);assert.equal(f.relays.length,1);f.coordinator.close();
});

test('primary policy/owner revocation after composing prevents both cached and fallback relay',async()=>{
  for(const reliable of [true,false])for(const reason of ['owner','policy']){
    const f=fixture({reliable}),result=f.send(),rejected=assert.rejects(result,reason==='owner'?/superseded/:/policy_denied/);
    await f.clock.advance(200);if(reason==='owner')f.revoke();else f.deny();
    await f.clock.advance(800);await rejected;assert.equal(f.relays.length,0);assert.equal(f.retryStore.size(),0);f.coordinator.close();
  }
});

test('real PRIMARY raw sender caches one native assistant protocol, retains targeted presence and ACKs only acceptance',async t=>{
  const before=process.env.ORION_RETRY_TEST_MODE;process.env.ORION_RETRY_TEST_MODE='1';
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-primary-presentation-'));
  t.after(()=>{if(before===undefined)delete process.env.ORION_RETRY_TEST_MODE;else process.env.ORION_RETRY_TEST_MODE=before;
    assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir())+path.sep+'orion-primary-presentation-'));fs.rmSync(directory,{recursive:true,force:true});});
  const f=fixture(),acks=[],id='E'.repeat(32),tenant='11111111-1111-4111-8111-111111111111',channel='22222222-2222-4222-8222-222222222222';
  const presentation={version:1,id,action:{type:'reply_buttons',text:'Resumo fictício',footer:'',
    buttons:[{id:`oa1_${id}_0`,title:'Confirmar'},{id:`oa1_${id}_1`,title:'Corrigir'}]}};
  const store=createAssistantPresentationStore({directory,secret:'synthetic-primary-presentation-secret-'.repeat(2),scope:{registration:123}});
  f.context.socketOutgoingPresence.set(f.socket,{...f.coordinator,assistantPresentations:()=>store});
  Object.assign(f.context,{proto:sdk.proto,generateWAMessageFromContent:sdk.generateWAMessageFromContent,relayAssistantPresentation,
    assistantNativeMode:options=>assistantNativeMode({...options,env:f.context.process.env})});
  f.context.legacyCentral.ingressScope=()=>({tenant_id:tenant,channel_id:channel});
  f.context.process.env.QR_ASSISTANT_NATIVE_SCOPES=`${tenant}:${channel}:${phone}`;
  const sending=f.context.sendLegacyRaw(phone,presentation.action.text,{source:'assistant',presentation,isCurrent:()=>true,
    presentationAck:async payload=>{acks.push(payload);return {acknowledged:true};}});
  await f.clock.advance(3000);const result=await sending;
  assert.equal(result.accepted,true);assert.equal(f.relays.length,1);assert.equal(f.retryStore.size(),1);
  const protocol=f.relays[0].message.interactiveMessage;assert.ok(protocol);assert.equal(protocol.nativeFlowMessage.buttons.length,2);
  assert.ok(f.relays[0].options.additionalNodes);assert.equal(acks.length,1);assert.equal(acks[0].provider_id,f.relays[0].options.messageId);
  assert.deepEqual(f.presence.map(value=>value.type),['composing','paused']);f.coordinator.close();
});

test('pinned SDK encodes only targeted recording as composing with audio media, without global availability',async()=>{
  const source=fs.readFileSync(new URL('../node_modules/@whiskeysockets/baileys/lib/Socket/chats.js',import.meta.url),'utf8');
  const match=source.match(/const sendPresenceUpdate = async \(type, toJid\) => \{[\s\S]*?\n    \};/);assert.ok(match);
  const nodes=[],context=vm.createContext({authState:{creds:{me:{id:self+':7@s.whatsapp.net',lid:'100000000000001:7@lid'}}},
    jidDecode:sdk.jidDecode,sendNode:async node=>nodes.push(node),logger:{warn(){assert.fail('no global presence');}},ev:{emit(){assert.fail('no online event');}}});
  const send=vm.runInContext('('+match[0].replace('const sendPresenceUpdate = ','').replace(/;$/,'')+')',context);
  await send('recording',phone+'@s.whatsapp.net');await send('composing',phone+'@s.whatsapp.net');await send('paused',phone+'@s.whatsapp.net');
  const normalized=JSON.parse(JSON.stringify(nodes));assert.deepEqual(normalized.map(node=>node.tag),['chatstate','chatstate','chatstate']);
  assert.deepEqual(normalized.map(node=>node.content),[[{tag:'composing',attrs:{media:'audio'}}],[{tag:'composing',attrs:{}}],[{tag:'paused',attrs:{}}]]);
  assert.ok(normalized.every(node=>node.attrs.to===phone+'@s.whatsapp.net'));
});
