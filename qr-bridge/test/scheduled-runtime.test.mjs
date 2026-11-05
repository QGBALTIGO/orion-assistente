import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {randomUUID,createHash} from 'node:crypto';
import * as sdk from '@whiskeysockets/baileys';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',self='5567999999902';
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const defer=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return{promise,resolve};};
async function until(predicate){for(let i=0;i<600;i++){if(predicate())return;await tick();}assert.fail('Expected scheduled boundary was not reached');}

async function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-scheduled-runtime-'));fs.chmodSync(root,0o700);
  const directory=path.join(root,'auth'),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  let state={id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),chatbot:{revision:1},
    capabilities:{proxy:true,webhook:true,transcription_available:false,transcription_max_bytes:25_000_000,transcription_max_seconds:1800}},
    alive=true,clock=1_000_000,serial=0,client,config,session;
  const instances=[],timers=new Map(),events=[],sends=[],finished=[],authorizations=[],lookups=[],assets=[],permits=new Map();
  const behavior={pending:async()=>({effects:[],reconcile:[]}),finish:async()=>({effects:[]}),authorize:async()=>true,
    send:async()=>{},capacity:async()=>{},lookup:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],
    asset:()=>assert.fail('No scheduled asset was granted')};
  const context={channelId:randomUUID(),tenantId:randomUUID(),directory,deviceDirectory,secret:'synthetic-scheduled-key-'.repeat(3),
    deviceSnapshot:()=>state,isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,notifySelf:false,
    onInbound:()=>assert.fail('Scheduled messages cannot activate legacy intake'),onMessage:()=>assert.fail('Scheduled messages cannot activate legacy AI'),
    onReceipt:()=>assert.fail('Scheduled messages cannot use legacy receipts'),transcribe:()=>assert.fail('Scheduled messages cannot transcribe'),
    onConnected(){},onDisconnected(){},
    deviceEvent:async payload=>{events.push(payload);return {recorded:true,effects:[]};},
    deviceEffectsPending:async()=>behavior.pending(),
    deviceEffectAuthorize:async payload=>{authorizations.push(payload);if(await behavior.authorize(payload)!==true)return{authorized:false};
      const grant=permits.get(payload.operation_id);return grant?{authorized:true,...payload,...grant}:{authorized:false};},
    deviceEffectFinish:async payload=>{const result=await behavior.finish(payload);finished.push(payload);return result;},
    deviceScheduledAsset:async payload=>{assets.push(payload);return behavior.asset(payload);},
    deviceChatbotAsset:()=>assert.fail('Scheduled asset must not use a Chatbot run'),
    deviceCampaignAsset:()=>assert.fail('Scheduled asset must not use a campaign run'),
    deviceChatbotHttpResult:()=>assert.fail('Scheduled messages cannot execute HTTP'),
    deviceChatbotCarouselImage:()=>assert.fail('Scheduled messages cannot fetch remote carousel images')};
  const schedule=(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms,at:clock+ms});return id;},cancel=id=>timers.delete(id);
  async function open(){
    client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net',name:'Synthetic business'},end(){},
      onWhatsApp:async pn=>{lookups.push(pn);return behavior.lookup(pn);},signalRepository:{lidMapping:{getPNForLID:async()=>null}},
      sendMessage:()=>assert.fail('No direct or automatic send'),rejectCall:()=>assert.fail('Scheduled messages cannot reject calls'),
      waUploadToServer:async()=>({mediaUrl:'https://mmg.whatsapp.net/synthetic',directPath:'/synthetic'}),
      relayMessage:async(jid,proto,options)=>{
        assert.equal(await config.getMessage({id:options.messageId}),undefined);
        assert.deepEqual(await config.getMessage({id:options.messageId,remoteJid:jid,fromMe:true}),
          JSON.parse(JSON.stringify(proto,sdk.BufferJSON.replacer),sdk.BufferJSON.reviver));
        const item={jid,proto:sdk.proto.Message.decode(sdk.proto.Message.encode(proto).finish()),id:options.messageId};sends.push(item);await behavior.send(item);
      }};
    session=await createBaileysChannelSession(context,{loadBaileys:async()=>({...sdk,default:options=>{config=options;return client;}}),
      now:()=>clock,schedule,cancel,deviceOptions:{runtimeOptions:{capacity:{acquire:options=>behavior.capacity(options)}}}});
    instances.push(session);const before=events.length;client.ev.emit('connection.update',{connection:'open'});
    await until(()=>events.slice(before).some(row=>row.kind==='status'));return session;
  }
  function effect(action,{target=phone,part=0,schedule=randomUUID(),runId=randomUUID(),recipient=randomUUID(),run='a'.repeat(64),extra={}}={}){
    const operation_id=randomUUID();permits.set(operation_id,{type:'scheduled_action',purpose:'scheduled_action',schedule_id:schedule,run_id:runId,
      recipient_id:recipient,run_version:run,part_index:part,payload:{phone:target,action},...extra});
    return {operation_id,type:'scheduled_action',action_type:action.type,device_id:state.id,generation:state.generation,
      settings_revision:state.settings_revision,due_at:new Date(clock).toISOString()};
  }
  async function poll(){const task=[...timers.values()].find(row=>row.ms===5000);assert.ok(task);await task.fn();}
  function reviseDevice(){state={...state,settings_revision:state.settings_revision+1};session.deviceSettingsChanged();}
  function reviseChatbot(){state={...state,chatbot:{revision:state.chatbot.revision+1}};session.deviceSettingsChanged();}
  await open();t.after(()=>{instances.forEach(item=>item.close());fs.rmSync(root,{recursive:true,force:true});});
  return {root,events,sends,finished,authorizations,lookups,assets,permits,behavior,effect,open,poll,reviseDevice,reviseChatbot,
    revoke(){alive=false;},get state(){return state;},get session(){return session;},get client(){return client;}};
}

test('connection and empty scheduled polls never claim or send a message',async t=>{
  const f=await fixture(t);await f.poll();assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);
  assert.equal(f.assets.length,0);assert.equal(f.finished.length,0);
});

test('scheduled text traverses the native protocol with final scoped rechecks and one receipt',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Mensagem programada\n'+'A'.repeat(980)});
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,phone+'@s.whatsapp.net');
  assert.equal(f.sends[0].proto.extendedTextMessage.text.length,1000);assert.equal(f.finished[0].state,'accepted');
  assert.ok(f.authorizations.length>=5);assert.ok(f.authorizations.slice(1).every(row=>row.recheck===true));
  assert.ok(f.authorizations.every(row=>row.device_id===f.state.id&&row.generation===f.state.generation&&row.chatbot_revision===undefined));
});

test('accepted receipt survives lost finish and restart without claim, lookup or send repetition',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Única tentativa'});let lose=true,finishCalls=0;
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});
  f.behavior.finish=async()=>{finishCalls++;if(lose)throw Error('lost acknowledgement');return{effects:[]};};
  await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished.length,0);assert.equal(finishCalls,1);
  const calls=f.authorizations.length,lookups=f.lookups.length;f.session.close();await f.open();lose=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[item]});await f.poll();
  assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,calls);assert.equal(f.lookups.length,lookups);
  assert.equal(f.finished[0].state,'accepted');assert.equal(f.finished[0].reconcile,true);
});

test('a repeated accepted descriptor never repeats its transport even before SQL reconciliation',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Persistida'});
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();const claims=f.authorizations.length;
  await f.poll();assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,claims);assert.equal(f.finished.at(-1).reconcile,true);
});

test('provider failure after relay remains uncertain even when its error claims preflight refusal',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Sem repetição'});
  f.behavior.send=async()=>{throw Object.assign(Error('private provider details'),{code:'recipient_identity_unverified'});};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,1);assert.equal(f.finished[0].state,'uncertain');assert.equal(f.finished[0].error_code,'MESSAGE_OUTCOME_UNCERTAIN');
  const claims=f.authorizations.length;await f.poll();assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,claims);
});

test('cancellation or contact revocation after recipient lookup blocks the final relay',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Revisão necessária'});let allowed=true;
  f.behavior.authorize=async()=>allowed;f.behavior.lookup=async pn=>{allowed=false;return[{exists:true,jid:pn+'@s.whatsapp.net'}];};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.lookups.length,1);assert.equal(f.sends.length,0);assert.equal(f.finished[0].state,'not_sent');
  assert.equal(f.finished[0].error_code,'SCHEDULE_REVOKED');
});

test('device revision change while preparing a recipient never becomes a send',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Contexto antigo'});
  f.behavior.lookup=async pn=>{f.reviseDevice();return[{exists:true,jid:pn+'@s.whatsapp.net'}];};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();assert.equal(f.sends.length,0);
  assert.equal(f.finished[0].state,'not_sent');assert.equal(f.finished[0].error_code,'SCHEDULE_REVOKED');
});

test('a Chatbot-only revision change does not grant or revoke an independently authorized scheduled message',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Programação independente'});
  f.behavior.lookup=async pn=>{f.reviseChatbot();return[{exists:true,jid:pn+'@s.whatsapp.net'}];};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished[0].state,'accepted');
});

test('an unavailable recipient has a durable definite refusal without automatic retry',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Destinatário não confirmado'});
  f.behavior.lookup=async pn=>[{exists:false,jid:pn+'@s.whatsapp.net'}];
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();await f.poll();
  assert.equal(f.sends.length,0);assert.equal(f.lookups.length,1);assert.equal(f.finished[0].error_code,'RECIPIENT_NOT_REGISTERED');
  assert.equal(f.finished[0].state,'not_sent');assert.equal(f.finished.at(-1).reconcile,true);
});

test('a scheduled message cannot borrow the diagnostic self-send exception',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Não enviar para si'},{target:self});
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,0);assert.equal(f.finished[0].state,'not_sent');
});

for(const extra of [{purpose:'diagnostic_test'},{schedule_id:'not-a-uuid'},{run_id:'not-a-uuid'},{campaign_id:randomUUID()},{run_version:'short'},{part_index:3},{chatbot_revision:1},{recipient_mode:'self'}]){
  test('malformed or cross-purpose grant cannot reach the scheduled provider: '+JSON.stringify(extra),async t=>{
    const f=await fixture(t),item=f.effect({type:'text',text:'Escopo estrito'},{extra});
    f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
    assert.equal(f.sends.length,0);assert.equal(f.lookups.length,0);assert.equal(f.finished[0].state,'not_sent');
    assert.equal(f.finished[0].error_code,'MESSAGE_CONTENT_INVALID');
  });
}

test('changing content on a final authorization cannot change the confirmed message',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Conteúdo confirmado'});let calls=0;
  f.behavior.authorize=async()=>{if(++calls===2)f.permits.get(item.operation_id).payload={phone,action:{type:'text',text:'Conteúdo alterado'}};return true;};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,0);assert.equal(f.finished[0].error_code,'SCHEDULE_REVOKED');
});

for(const [field,value] of [['schedule_id',randomUUID()],['run_id',randomUUID()],['recipient_id',randomUUID()],
  ['run_version','b'.repeat(64)],['part_index',1]]){
  test('a final recheck cannot transplant the claimed part to another '+field,async t=>{
    const f=await fixture(t),item=f.effect({type:'text',text:'Ocorrência confirmada'});let calls=0;
    f.behavior.authorize=async()=>{if(++calls===2)f.permits.get(item.operation_id)[field]=value;return true;};
    f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
    assert.equal(f.sends.length,0);assert.equal(f.lookups.length,0);
    assert.equal(f.finished[0].state,'not_sent');assert.equal(f.finished[0].error_code,'SCHEDULE_REVOKED');
  });
}

test('capacity waiting happens before durable claim and respects revocation',async t=>{
  const f=await fixture(t),item=f.effect({type:'text',text:'Aguardando capacidade'}),started=defer(),release=defer();
  f.behavior.capacity=async()=>{started.resolve();await release.promise;};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});const pending=f.poll();await started.promise;
  assert.equal(f.authorizations.length,0);f.revoke();release.resolve();await pending;
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);
});

test('audio, caption and followup use private scheduled assets and receipt order',{timeout:10_000},async t=>{
  const f=await fixture(t),bytes=Buffer.from('synthetic private audio'),loaded=[];
  const asset={id:randomUUID(),kind:'audio',mime:'audio/mpeg',size:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex'),
    width:null,height:null,thumbnail:null,duration_ms:1000,file_name:'audio.mp3'};
  const schedule=randomUUID(),runId=randomUUID(),recipient=randomUUID();
  const items=[f.effect({type:'audio',asset,caption:''},{schedule,runId,recipient}),
    f.effect({type:'text',text:'Legenda do áudio'},{schedule,runId,recipient,part:1}),
    f.effect({type:'text',text:'Mensagem complementar'},{schedule,runId,recipient,part:2})];
  let count=0;f.behavior.asset=async payload=>{assert.equal(payload.operation_id,items[0].operation_id);assert.equal(payload.variant,'original');
    assert.equal(payload.chatbot_revision,undefined);const buffer=Buffer.from(bytes);loaded.push(buffer);return buffer;};
  f.behavior.finish=async receipt=>{assert.equal(receipt.operation_id,items[count].operation_id);assert.equal(receipt.state,'accepted');count++;
    return{effects:count<items.length?[items[count]]:[]};};
  f.behavior.pending=async()=>({effects:[items[0]],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,3);assert.equal(f.sends[0].proto.audioMessage.mimetype,'audio/mpeg');
  assert.equal(f.sends[1].proto.extendedTextMessage.text,'Legenda do áudio');
  assert.equal(f.sends[2].proto.extendedTextMessage.text,'Mensagem complementar');assert.ok(loaded.every(b=>b.every(x=>x===0)));
});

test('a revoked asset read leaves no media or message and zeroes returned bytes',{timeout:10_000},async t=>{
  const f=await fixture(t),bytes=Buffer.from('synthetic private document');let allowed=true,loaded;
  const asset={id:randomUUID(),kind:'document',mime:'text/plain; charset=utf-8',size:bytes.length,
    sha256:createHash('sha256').update(bytes).digest('hex'),width:null,height:null,thumbnail:null,duration_ms:null,file_name:'arquivo.txt'};
  const item=f.effect({type:'document',asset,file_name:'arquivo.txt'});
  f.behavior.authorize=async()=>allowed;f.behavior.asset=async()=>{allowed=false;loaded=Buffer.from(bytes);return loaded;};
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,0);assert.ok(loaded.every(x=>x===0));assert.equal(f.finished[0].state,'not_sent');
});

test('native location remains a location without a text fallback',async t=>{
  const f=await fixture(t),item=f.effect({type:'location',latitude:-22.9,longitude:-43.2});
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].proto.locationMessage.degreesLatitude,-22.9);
  assert.equal(f.sends[0].proto.extendedTextMessage,null);
});

for(const type of ['http','list','carousel'])test('non-library '+type+' cannot be executed as a scheduled effect',async t=>{
  const f=await fixture(t),item=f.effect({type,url:'https://example.invalid/private'});
  f.behavior.pending=async()=>({effects:[item],reconcile:[]});await f.poll();assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);
});
