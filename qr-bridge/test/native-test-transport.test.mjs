/** The real PRIMARY sender + fixed canary coordinator + pinnedSDK, offline. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {randomUUID} from 'node:crypto';
import * as sdk from '@whiskeysockets/baileys';
import {createNativeTestCanary,loadNativeTestAsset} from '../src/native-test-canary.mjs';
import {buildChatbotContent} from '../src/chatbot-message.mjs';
import {sendStoredProtocol} from '../src/reliable-send.mjs';
import {validPhone,normalizeFromJid} from '../src/core.mjs';
import {makeOutgoingPresence} from '../src/outgoing-presence.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {resolvePilotRecipient,makePilotDeviceCache} from '../src/pilot-recipient.mjs';
import {presenceClock,presenceFlush} from './helpers/presence-clock.mjs';
process.env.ORION_RETRY_TEST_MODE='1';
const phone='5511999999901',self='5511999999902',lid='100000000000002@lid';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(fn){for(let i=0;i<300;i++){if(fn())return;await tick();}assert.fail('Native canary boundary not reached');}
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
const start=source.indexOf('async function sendLegacyProtocol('),end=source.indexOf('async function sendLegacyRaw(',start);
assert.ok(start>0&&end>start);
function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-native-transport-'));fs.chmodSync(directory,0o700);
  const clock=presenceClock(1700000000000),presence=[],relays=[],events=[],env={QR_NATIVE_TEST_ENABLED:'1',QR_NATIVE_TEST_PHONE:'+'+phone,QR_PILOT_USE_VERIFIED_LID:'1'};
  let alive=true,serial=0,generated=0,uploads=0,uploaded;const uploadReady=new Promise(resolve=>{uploaded=resolve;});
  const secret='synthetic-native-transport-secret-'.repeat(3),store=makeEncryptedRetryStore({directory:path.join(directory,'retry'),secret,
    now:clock.now,serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer),deserialize:value=>JSON.parse(value,sdk.BufferJSON.reviver)});
  const behavior={relay:async()=>{},lookup:async()=>[{exists:true,jid:phone+'@s.whatsapp.net',lid}]};
  const client={user:{id:self+':7@s.whatsapp.net',lid:'100000000000001:7@lid'},
    sendPresenceUpdate:async(type,jid)=>presence.push({type,jid,at:clock.now()}),onWhatsApp:()=>behavior.lookup(),
    getUSyncDevices:async()=>[],waUploadToServer:async()=>{uploads++;uploaded();return {mediaUrl:'https://mmg.whatsapp.net/synthetic',directPath:'/synthetic'};},
    sendMessage:()=>assert.fail('Native pilot must not use text fallback'),
    relayMessage:async(jid,message,options)=>{
      assert.deepEqual(store.get({id:options.messageId,remoteJid:jid,fromMe:true}),JSON.parse(JSON.stringify(message,sdk.BufferJSON.replacer),sdk.BufferJSON.reviver));
      relays.push({jid,message,options});await behavior.relay();
    }};
  const coordinator=makeOutgoingPresence({socket:client,isConnected:()=>alive,now:clock.now,schedule:clock.schedule,cancel:clock.cancel});
  const logger={child(){return this;},trace(){},debug(){},info(){},warn(){},error(){}};
  const context=vm.createContext({validPhone,normalizeFromJid,socket:client,generation:1,status:'connected',legacyCentral:{transportCurrent:()=>alive},
    sendCapacity:{acquire:async()=>{}},resolvePilotRecipient,socketDeviceCaches:new WeakMap([[client,makePilotDeviceCache()]]),
    socketOutgoingPresence:new WeakMap([[client,coordinator]]),process:{env},sendStoredProtocol,buildChatbotContent,proto:sdk.proto,
    prepareWAMessageMedia:sdk.prepareWAMessageMedia,loadNativeTestAsset,logger,retryStore:store,
    generateWAMessageFromContent:(...args)=>{generated++;return sdk.generateWAMessageFromContent(...args);},generateMessageIDV2:sdk.generateMessageIDV2,
    retryDiagnostics:{prepared:0,relayAccepted:0},deliveryReceipts:new Map(),deliveryStart:new Map(),transport:{record:(...args)=>events.push(args)}});
  vm.runInContext(source.slice(start,end),context);
  const scope={tenant_id:randomUUID(),channel_id:randomUUID(),owner:randomUUID(),fence:4,native_generation:1,session_digest:'a'.repeat(64)};
  const options={directory,secret,env,now:clock.now,getScope:()=>({...scope}),isCurrent:()=>alive,
    assertCurrent:async()=>{if(!alive)throw Error('revoked');},allowedSender:value=>value===phone,resolvePhone:async()=>phone,
    send:(target,action,guards)=>context.sendLegacyProtocol(target,action,guards)};
  let canary=createNativeTestCanary(options);
  const message=(text,id='source-'+(++serial))=>({key:{id,fromMe:false,remoteJid:phone+'@s.whatsapp.net'},messageTimestamp:clock.now()/1000,
    message:typeof text==='string'?{conversation:text}:text});
  const receive=value=>canary.handle({message:value,type:'notify',ticket:canary.observe(value,{type:'notify'})});
  t.after(()=>{coordinator.close();fs.rmSync(directory,{recursive:true,force:true});});
  return {clock,presence,relays,events,env,client,context,store,behavior,message,receive,scope,uploadReady,
    revoke(){alive=false;},restart(){canary=createNativeTestCanary(options);},get generated(){return generated;},get uploads(){return uploads;}};
}

test('explicit primary list is native, cached before relay to verified LID; its typed choice produces one fictitious reliable response',async t=>{
  const f=fixture(t),outcome=f.receive(f.message('ORIONTESTE LISTA'));await until(()=>f.presence.length===1);
  assert.equal(f.generated,0);assert.equal(f.relays.length,0);await f.clock.advance(3000);assert.equal((await outcome).state,'accepted');
  const menu=f.relays[0],list=menu.message.listMessage;assert.ok(list);assert.equal(list.listType,1);assert.equal(list.sections[0].rows.length,2);
  assert.equal(menu.jid,lid);assert.equal(menu.options.useUserDevicesCache,true);
  assert.equal(f.context.deliveryReceipts.get(menu.options.messageId),'pending');assert.equal(f.context.retryDiagnostics.prepared,1);
  const selected=f.message({listResponseMessage:{singleSelectReply:{selectedRowId:list.sections[0].rows[0].rowId},contextInfo:{stanzaId:menu.options.messageId}}});
  const reply=f.receive(selected);await until(()=>f.presence.length>=3);await f.clock.advance(3000);assert.equal((await reply).state,'accepted');
  assert.equal(f.relays.length,2);assert.match(f.relays[1].message.extendedTextMessage.text,/Corte de teste.*Nenhum agendamento foi criado/);
  assert.equal(f.context.retryDiagnostics.relayAccepted,2);assert.ok(f.events.some(row=>row[0]==='native_test_prepared'&&row[2].type==='list'));
});

test('explicit primary URL button is real cta_url proto with fixed destination; no bare-link fallback or media fetch',async t=>{
  const f=fixture(t),outcome=f.receive(f.message('ORIONTESTE BOTAO'));await until(()=>f.presence.length===1);await f.clock.advance(3000);
  assert.equal((await outcome).state,'accepted');const message=f.relays[0].message;
  const button=message.interactiveMessage.nativeFlowMessage.buttons[0];assert.equal(button.name,'cta_url');
  assert.deepEqual(JSON.parse(button.buttonParamsJson),{display_text:'Acessar meu sistema',url:'https://orionagenda.pro/'});
  assert.equal(message.extendedTextMessage,null);assert.equal(f.uploads,0);assert.equal(f.relays[0].jid,lid);
});

test('fixed MPEG audio prepares through real SDK and records2088ms before native cache/relay',{timeout:15000},async t=>{
  const f=fixture(t),outcome=f.receive(f.message('ORIONTESTE AUDIO'));await until(()=>f.presence.length===1);
  assert.equal(f.presence[0].type,'recording');await f.uploadReady;await presenceFlush();
  await f.clock.advance(2087);assert.equal(f.generated,0);assert.equal(f.relays.length,0);
  await f.clock.advance(1);assert.equal((await outcome).state,'accepted');assert.ok(f.relays[0].message.audioMessage);
  assert.equal(f.relays[0].message.audioMessage.mimetype,'audio/mpeg');assert.equal(f.relays[0].message.audioMessage.ptt,false);
  assert.deepEqual(f.presence.map(value=>value.type),['recording','paused']);assert.equal(f.presence.at(-1).at-f.presence[0].at,2088);
});

test('owner loss during pacing refuses native construction/cache/relay and emits no stale paused',async t=>{
  const f=fixture(t),outcome=f.receive(f.message('ORIONTESTE BOTAO'));await until(()=>f.presence.length===1);
  f.revoke();await f.clock.advance(100);assert.equal((await outcome).state,'uncertain');
  assert.equal(f.generated,0);assert.equal(f.relays.length,0);assert.equal(f.context.retryDiagnostics.prepared,0);
  assert.deepEqual(f.presence.map(value=>value.type),['composing']);
});

test('unproved LID never falls back to PN; ambiguous native relay is not repeated after restart',async t=>{
  const f=fixture(t);f.behavior.lookup=async()=>[{exists:true,jid:phone+'@s.whatsapp.net'}];
  assert.equal((await f.receive(f.message('ORIONTESTE BOTAO'))).state,'uncertain');assert.equal(f.generated,0);assert.equal(f.relays.length,0);
  f.behavior.lookup=async()=>[{exists:true,jid:phone+'@s.whatsapp.net',lid}];f.behavior.relay=async()=>{throw Error('unknown provider acceptance');};
  const input=f.message('ORIONTESTE BOTAO'),outcome=f.receive(input);await until(()=>f.presence.length===1);await f.clock.advance(3000);
  assert.equal((await outcome).state,'uncertain');assert.equal(f.relays.length,1);
  const prepared=f.relays[0];assert.ok(f.store.get({id:prepared.options.messageId,remoteJid:lid,fromMe:true}));
  f.restart();assert.equal((await f.receive(input)).state,'ignored');
  input.messageTimestamp=f.clock.now()/1000;assert.equal((await f.receive(input)).state,'duplicate');assert.equal(f.relays.length,1);assert.equal(f.generated,1);
});
