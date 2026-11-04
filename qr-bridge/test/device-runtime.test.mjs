import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createChannelResumeStore} from '../src/channel-resume.mjs';
import {deviceStorage} from '../src/device-storage.mjs';
import {channelDirectory} from '../src/channel-storage.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',self='5567999999902';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function until(predicate){for(let i=0;i<200;i++){if(predicate())return;await tick();}assert.fail('Expected device pipeline boundary was not reached');}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
function snapshot(){return {id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),
  capabilities:{proxy:true,webhook:true,transcription_available:true,transcription_max_bytes:25_000_000,transcription_max_seconds:1800,transcription_max_text_chars:120000}};}
function temp(t,prefix){const dir=fs.mkdtempSync(path.join(os.tmpdir(),prefix));fs.chmodSync(dir,0o700);t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}

async function fixture(t){
  const root=temp(t,'orion-device-runtime-'),directory=path.join(root,'auth'),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  let state=snapshot(),alive=true,serial=0,current,client,config;
  const events=[],sends=[],finished=[],authorizations=[],audioCalls=[],downloads=[],rejections=[],webhooks=[],tasks=new Map(),permits=new Map(),instances=[];
  const behavior={event:async()=>[],transcribe:async()=>({text:'voz',truncated:false,effects:[]}),send:async()=>{},finish:async()=>{},
    pending:async()=>({effects:[],reconcile:[]}),webhook:async()=>{},lookupCall:async()=>null,prepare:async()=>{}};
  const context={channelId:randomUUID(),tenantId:randomUUID(),directory,deviceDirectory,secret:'synthetic-device-secret-'.repeat(3),
    deviceSnapshot:()=>state,isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,notifySelf:false,
    onInbound:()=>assert.fail('Device must not reach interaction/journal'),onMessage:()=>assert.fail('Device must not reach AI'),
    onReceipt:()=>assert.fail('Device must not reach legacy receipts'),transcribe:()=>assert.fail('Device must not reach legacy audio'),
    onConnected(){},onDisconnected(){},
    deviceEvent:async payload=>{events.push(payload);return {effects:await behavior.event(payload),recorded:true};},
    deviceEffectAuthorize:async payload=>{
      authorizations.push(payload);const data=permits.get(payload.operation_id);if(!data)throw Error('not authorized');
      return {authorized:true,...payload,...data};
    },
    deviceEffectFinish:async payload=>{await behavior.finish(payload);finished.push(payload);return {recorded:true};},
    deviceEffectsPending:async()=>behavior.pending(),
    deviceTranscribe:async payload=>{audioCalls.push(payload);return behavior.transcribe(payload);}};
  const schedule=(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel=id=>tasks.delete(id);
  async function open(){
    client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net',name:'Synthetic business'},end(){this.ended=true;},
      onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],sendMessage:()=>assert.fail('No direct send'),
      signalRepository:{lidMapping:{getPNForLID:lid=>behavior.lookupCall(lid)}},
      rejectCall:async(id,from)=>{rejections.push({id,from});},logout:async()=>{},
      relayMessage:async(jid,body)=>{sends.push({jid,text:body.conversation});await behavior.send();}};
    const mod={default:options=>{config=options;return client;},BufferJSON:{},initAuthCreds:()=>({registered:true}),
      Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}},
      generateMessageIDV2:()=> 'provider-'+(++serial),generateWAMessage:async(jid,{text},options)=>{await behavior.prepare();return {key:{id:options.messageId,remoteJid:jid},message:{conversation:text}};},
      downloadMediaMessage:async()=>{const buffer=Buffer.from('OggS synthetic audio');downloads.push(buffer);return buffer;}};
    current=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,now:()=>1000000,schedule,cancel,
      deviceOptions:{runtimeOptions:{capacity:{acquire:async()=>{}},postWebhook:async payload=>{webhooks.push(payload);return behavior.webhook(payload);}}}});
    instances.push(current);client.ev.emit('connection.update',{connection:'open'});await until(()=>events.some(e=>e.kind==='status'));return current;
  }
  function effect(type,payload,extra={}){const operation_id=randomUUID();permits.set(operation_id,{type,payload,...extra});return {operation_id,type};}
  function receive(id,body='hello',key={}){client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id,fromMe:false,remoteJid:phone+'@s.whatsapp.net',...key},
    messageTimestamp:1000,message:typeof body==='string'?{conversation:body}:body,pushName:'Synthetic contact'}]});}
  await open();t.after(()=>instances.forEach(session=>session.close()));
  return {context,events,sends,finished,authorizations,audioCalls,downloads,rejections,webhooks,behavior,effect,receive,open,permits,
    get session(){return current;},get client(){return client;},get config(){return config;},get snapshot(){return state;},
    update(patch={}){state={...structuredClone(state),...patch,settings_revision:state.settings_revision+1};current.deviceSettingsChanged();},
    revoke(){alive=false;current.close();},async poll(){const task=[...tasks.values()].find(item=>item.ms===5000);assert.ok(task);await task.fn();}};
}

test('device ingress has no pilot allowlist, but defaults never invoke AI, ASR, sends or unrelated recipients',async t=>{
  const f=await fixture(t);f.receive('private1');await until(()=>f.events.some(e=>e.message_id==='private1'));
  f.receive('group','hello',{remoteJid:'120363000000000@g.us'});f.receive('self','hello',{remoteJid:self+'@s.whatsapp.net'});
  f.receive('mine','hello',{fromMe:true});f.receive('unknown-lid','hello',{remoteJid:'9999999999999999@lid'});
  f.receive('private1');await tick();await tick();
  assert.equal(f.events.filter(e=>e.kind==='text').length,1);assert.equal(f.sends.length,0);assert.equal(f.downloads.length,0);
  await assert.rejects(f.session.send(phone,'manual bypass'),/effect_grant/);
  const prefix='a'.repeat(99);
  f.client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id:'unicode-name',fromMe:false,remoteJid:phone+'@s.whatsapp.net'},
    messageTimestamp:1000,message:{conversation:'hello'},pushName:prefix+'🙂 trailing'}]});
  await until(()=>f.events.some(e=>e.message_id==='unicode-name'));
  assert.equal(f.events.find(e=>e.message_id==='unicode-name').name,prefix+'🙂','The schema limit counts Unicode characters, without splitting an emoji');
});

test('only the server-derived current effect may send outside the pilot policy, once across restart',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Mensagem de ausência'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];
  f.receive('inbound1');await until(()=>f.finished.some(e=>e.operation_id===effect.operation_id));
  assert.deepEqual(f.sends,[{jid:phone+'@s.whatsapp.net',text:'Mensagem de ausência'}]);
  f.session.close();await f.open();f.receive('inbound1');await until(()=>f.authorizations.length===2);await tick();
  assert.equal(f.sends.length,1);assert.ok(f.finished.every(row=>row.state==='accepted'));
  const forged=f.effect('send_text',{phone,text:'foreign'},{generation:randomUUID()});
  f.behavior.event=async e=>e.kind==='text'?[forged]:[];f.receive('inbound2');await until(()=>f.authorizations.length===3);await tick();
  assert.equal(f.sends.length,1);
});

test('ambiguous WhatsApp sends retain uncertainty and never repeat after process/session replacement',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Mensagem sintética'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];f.behavior.send=async()=>{throw Error('synthetic timeout after relay');};
  f.receive('uncertain1');await until(()=>f.finished.length===1);assert.equal(f.finished[0].state,'uncertain');
  f.session.close();await f.open();f.receive('uncertain1');await until(()=>f.finished.length===2);
  assert.equal(f.sends.length,1);assert.equal(f.finished[1].state,'uncertain');
});

test('device transcription supports complete long output in independently authorized ordered parts without activating chatbot',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription={...f.snapshot.settings.transcription,enabled:true,max_minutes:30,reply_enabled:true};
  const text='palavra '.repeat(14000),parts=[];
  for(let i=0;i<text.length;i+=32000)parts.push(f.effect('send_text',{phone,text:text.slice(i,i+32000)}));
  const audio=f.effect('transcribe',{phone,message_id:'voice1',max_bytes:25_000_000,max_seconds:1800});
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];
  f.behavior.transcribe=async()=>({text,truncated:false,duration_seconds:1800,effects:parts});
  f.receive('voice1',{audioMessage:{fileLength:100,seconds:1800,mimetype:'audio/ogg'}});
  await until(()=>f.sends.length===parts.length&&f.finished.some(e=>e.operation_id===parts.at(-1).operation_id));
  assert.equal(f.audioCalls.length,1);assert.ok(f.sends.map(e=>e.text).join('')===text,'Every transcript character and separator must survive the split');
  assert.ok(f.sends.every(e=>e.text.length<=32000));assert.ok(f.downloads[0].every(n=>n===0));
  assert.equal(f.audioCalls[0].operation_id,audio.operation_id);
});

test('revision change during ASR aborts the old signal, wipes buffers and discards every late reply',async t=>{
  const f=await fixture(t),gate=deferred();f.snapshot.settings.transcription.enabled=true;
  const audio=f.effect('transcribe',{phone,message_id:'voice2',max_bytes:25_000_000,max_seconds:120});
  const reply=f.effect('send_text',{phone,text:'late transcript'});
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];f.behavior.transcribe=()=>gate.promise;
  f.receive('voice2',{audioMessage:{fileLength:100,seconds:20,mimetype:'audio/ogg'}});await until(()=>f.audioCalls.length===1);
  f.update();assert.equal(f.audioCalls[0].signal.aborted,true);
  gate.resolve({text:'late',truncated:false,effects:[reply]});await until(()=>f.downloads[0].every(n=>n===0));
  assert.equal(f.sends.length,0);
});

test('polling cannot claim an audio effect without its source message or consume the live callback opportunity',async t=>{
  const f=await fixture(t);f.snapshot.settings.transcription.enabled=true;
  const audio=f.effect('transcribe',{phone,message_id:'live-audio',max_bytes:25_000_000,max_seconds:120});
  f.behavior.pending=async()=>({effects:[audio]});await f.poll();assert.equal(f.authorizations.length,0);assert.equal(f.downloads.length,0);
  f.behavior.event=async e=>e.kind==='audio'?[audio]:[];
  f.receive('live-audio',{audioMessage:{fileLength:100,seconds:10,mimetype:'audio/mpeg'}});await until(()=>f.audioCalls.length===1);
  assert.equal(f.audioCalls[0].mediaType,'audio/mpeg');
});

test('call rejection retains the original LID and never guesses a destination for a hidden phone',async t=>{
  const f=await fixture(t);const lid='9999999999999999@lid';
  f.behavior.event=async e=>e.kind==='call'?[f.effect('reject_call',{call_id:e.call_id,call_from:e.call_from})]:[];
  f.client.ev.emit('call',[{status:'offer',id:'hidden-call',from:lid,isGroup:false}]);
  await until(()=>f.rejections.length===1);const event=f.events.find(e=>e.kind==='call');
  assert.equal('phone' in event,false);assert.deepEqual(f.rejections,[{id:'hidden-call',from:lid}]);assert.equal(f.sends.length,0);
  f.client.ev.emit('call',[{status:'offer',id:'hidden-call',from:lid,isGroup:false},{status:'accept',id:'accept-call',from:lid},
    {status:'offer',id:'group-call',from:lid,isGroup:true}]);await tick();assert.equal(f.rejections.length,1);
});

test('webhooks retry only after a fresh server grant; configuration revocation prevents a due attempt',async t=>{
  const f=await fixture(t);f.snapshot.settings.webhook.enabled=true;
  const hook=f.effect('webhook',{url:'https://hooks.example.com',event_id:'fixture-hook',body:{version:1}});
  f.behavior.event=async e=>e.kind==='text'?[hook]:[];f.behavior.webhook=async()=>{throw Error('synthetic external failure');};
  f.receive('webhook1');await until(()=>f.finished.length===1);assert.equal(f.finished[0].state,'failed');assert.equal(f.webhooks.length,1);
  f.behavior.pending=async()=>({effects:[hook]});await f.poll();assert.equal(f.webhooks.length,2);
  f.update({settings:{...f.snapshot.settings,webhook:{...f.snapshot.settings.webhook,enabled:false}}});
  await f.poll();assert.equal(f.webhooks.length,2);
});

test('lost finish acknowledgements reconcile durable accepted receipts without a second send or fresh send authorization',async t=>{
  const f=await fixture(t),effect=f.effect('send_text',{phone,text:'Texto já aceito'});
  f.behavior.event=async e=>e.kind==='text'?[effect]:[];let lose=true;
  f.behavior.finish=async()=>{if(lose)throw Error('lost finish response');};
  f.receive('lost-finish');await until(()=>f.sends.length===1);await tick();await tick();
  assert.equal(f.finished.length,0);lose=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[{...effect,device_id:f.snapshot.id,generation:f.snapshot.generation,settings_revision:1}]});
  await f.poll();assert.equal(f.finished.length,1);assert.equal(f.finished[0].reconcile,true);assert.equal(f.finished[0].state,'accepted');
  assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,1);
});

function managerFixture(t){
  const root=temp(t,'orion-device-control-'),id=randomUUID(),tenant=randomUUID(),key='synthetic-control-key-'.repeat(3);
  const device=snapshot(),made=[],operations=new Map(),finishes=[],leases=[],stored=createChannelResumeStore({root,secret:'synthetic-resume-key-'.repeat(3)});
  let fence=0,leaseOwner=null,removed=false,removing=false,block=false;
  const broker={
    async lease(_id,_key,action,owner,expectedFence){
      leases.push(action);if(_id!==id||_key!==key||block)throw Error('denied');
      if(action==='receipt')return {channel_id:id,tenant_id:tenant,provider:'baileys',read_only:true};
      if(removing||removed)throw Error('normal lease stopped for remove');
      if(action==='claim'){leaseOwner=owner;fence++;}
      if(!['claim','inspect'].includes(action)&&(owner!==leaseOwner||expectedFence!==fence))throw Error('stale');
      return {channel_id:id,tenant_id:tenant,provider:'baileys',owner,fence,expires_in_ms:30000,allowed_senders:[],assistant_enabled:false,device};
    },
    async deviceControlAuthorize(_id,_key,input){
      assert.equal(_id,id);assert.equal(_key,key);const action=operations.get(input.operation_id);if(!action||block)throw Error('denied');
      if(action==='remove')removing=true;
      return {authorized:true,...input,channel_id:id,tenant_id:tenant,action,payload:action==='pair'?{phone}:undefined};
    },
    async deviceControlFinish(_id,_key,payload){finishes.push(payload);if(operations.get(payload.operation_id)==='remove'&&payload.status==='succeeded')removed=true;return {};},
    deviceEvent:async()=>({effects:[]})};
  function manager(){const m=createChannelManager({root,broker,resumeStore:stored,clock:()=>0,schedule:()=>1,cancel:()=>{},
    createSession:async context=>{const item={context,closed:false,logoutCalls:0};made.push(item);
      const session={status:()=>item.closed?'stopped':'connected',phone:()=>self,close(){item.closed=true;},
        async logout({authorize}){await authorize();item.logoutCalls++;},send:()=>assert.fail('No arbitrary send'),
        deviceSettingsChanged(){},deviceDiagnostics:()=>({pending:0})};
      await context.onConnected();return session;}});t.after(()=>m.close());return m;}
  function action(name){const operation_id=randomUUID();operations.set(operation_id,name);return {operation_id,device_id:device.id,generation:device.generation,settings_revision:device.settings_revision,...(name==='pair'?{phone}:{})};}
  return {root,id,tenant,key,device,made,finishes,leases,stored,manager,action,block:()=>{block=true;},get removed(){return removed;}};
}

test('device restart preserves auth epoch, relink rotates it, and remove uses exclusive control even after normal lease revocation',async t=>{
  const f=managerFixture(t),m=f.manager();
  const connect=f.action('connect');assert.equal((await m.deviceControl(f.id,f.key,'connect',connect)).status,'succeeded');
  const original=f.made[0].context.directory;
  await m.deviceControl(f.id,f.key,'connect',connect);assert.equal(f.made.length,1);
  assert.equal((await m.deviceControl(f.id,f.key,'restart',f.action('restart'))).status,'succeeded');assert.equal(f.made[1].context.directory,original);
  assert.equal((await m.deviceControl(f.id,f.key,'relink',f.action('relink'))).status,'succeeded');
  assert.notEqual(f.made[2].context.directory,original);assert.equal(f.made[1].logoutCalls,1);assert.equal(fs.existsSync(original),false);
  const remove=f.action('remove');assert.equal((await m.deviceControl(f.id,f.key,'remove',remove)).status,'succeeded');
  assert.equal(f.made[2].logoutCalls,1);assert.equal(f.removed,true);assert.equal(m.size,0);assert.equal(f.stored.list().length,0);
  const before=f.made.length,receipt=await m.deviceOperation(f.id,f.key,remove.operation_id);
  assert.equal(receipt.status,'succeeded');assert.equal(receipt.device_id,f.device.id);assert.equal(f.made.length,before);
});

test('device resume intent binds logical device and proxy config, never a later device on the reused channel',async t=>{
  const f=managerFixture(t),m=f.manager();await m.deviceControl(f.id,f.key,'connect',f.action('connect'));
  const row=f.stored.list()[0];assert.equal(row.device_id,f.device.id);assert.equal(row.generation,f.device.generation);
  m.close();f.device.id=randomUUID();f.device.generation=randomUUID();
  assert.deepEqual(await f.manager().resume(),{resumed:0});assert.equal(f.made.length,1);assert.equal(f.stored.list().length,0);
});

test('proxy edits close the running socket and cancel restart intent until an explicit connection action',async t=>{
  const f=managerFixture(t),m=f.manager();await m.deviceControl(f.id,f.key,'connect',f.action('connect'));
  f.device.settings.proxy={enabled:true,url:'https://proxy.example.com:8080'};f.device.settings_revision++;
  await assert.rejects(m.deviceStatus(f.id,f.key),/proxy_restart_required/);assert.equal(f.made[0].closed,true);assert.equal(f.stored.list().length,0);
  assert.deepEqual(await f.manager().resume(),{resumed:0});assert.equal(f.made.length,1);
});

test('auth namespaces and explicit deletion cannot touch another device or legacy keys',t=>{
  const root=temp(t,'orion-device-storage-'),tenant=randomUUID(),channel=randomUUID(),base=channelDirectory(root,tenant,channel),key='synthetic-key-'.repeat(4);
  const a=deviceStorage(base,snapshot(),key),b=deviceStorage(base,snapshot(),key),old=a.authDirectory(),other=b.authDirectory();
  fs.writeFileSync(path.join(other,'marker'),'preserve',{mode:0o600});const fresh=a.reset();assert.notEqual(fresh,old);assert.equal(fs.existsSync(old),false);
  a.remove();assert.equal(fs.readFileSync(path.join(other,'marker'),'utf8'),'preserve');
});

test('device audio uses only its fenced private route and own identity headers without QR_AI flags',async()=>{
  const calls=[],id=randomUUID(),key='synthetic-channel-key-'.repeat(3),owner=randomUUID(),device_id=randomUUID(),generation=randomUUID(),operation_id=randomUUID();
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',env:{},fetchImpl:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({text:'complete',truncated:false,effects:[]})};}});
  await broker.deviceTranscribe(id,key,{owner,fence:3},{device_id,generation,settings_revision:2,operation_id,phone,buffer:Buffer.from('OggS synthetic'),mediaType:'audio/ogg'});
  assert.equal(calls[0].url.pathname,`/internal/qr/channels/${id}/device/audio`);assert.equal(calls[0].options.redirect,'error');
  assert.equal(calls[0].options.headers['X-Orion-Device-Effect'],operation_id);assert.equal(calls[0].options.headers['X-Orion-Channel-Fence'],'3');
  await broker.deviceEffectsPending(id,key,{owner,fence:3},{device_id,generation,settings_revision:2});
  assert.equal(calls[1].options.method,'GET');assert.equal('body' in calls[1].options,false);
});

function diagnosticEffect(f,{recipient=self,mode='self',...extra}={}){
  return f.effect('send_text',{phone:recipient,text:'Teste confirmado sintético'},
    {purpose:'diagnostic_test',diagnostic_request_id:randomUUID(),confirmed:true,recipient_mode:mode,self_phone:self,...extra});
}
async function pollEffect(f,effect){f.behavior.pending=async()=>({effects:[effect],reconcile:[]});await f.poll();}

test('a confirmed diagnostic effect alone can address the verified current self PN, once across restart',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f);await pollEffect(f,effect);
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,self+'@s.whatsapp.net');
  assert.equal(f.finished.at(-1).state,'accepted');assert.ok(f.authorizations.filter(value=>value.recheck).length>=2);
  assert.equal(f.authorizations.at(-1).recheck,true);
  const claims=f.authorizations.filter(value=>!value.recheck).length;
  f.session.close();await f.open();await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished.at(-1).state,'accepted');
  assert.equal(f.authorizations.filter(value=>!value.recheck).length,claims+1,'A new session reconciles the durable reserved effect, without relaying it');
});

test('ordinary send_text and generic/public send options retain their self-send prohibition',async t=>{
  const f=await fixture(t);await pollEffect(f,f.effect('send_text',{phone:self,text:'ordinary self bypass'}));
  assert.equal(f.sends.length,0);assert.notEqual(f.finished.at(-1).state,'accepted');
  await assert.rejects(f.session.send(self,'forged',{allowSelf:true,diagnostic:{purpose:'diagnostic_test',confirmed:true}}),/effect_grant/);
  assert.equal(f.sends.length,0);
});

test('ordinary text cannot become a self-send if the active PN changes while preparing the message',async t=>{
  const f=await fixture(t);f.behavior.prepare=async()=>{f.client.user.id=phone+'@s.whatsapp.net';};
  await pollEffect(f,f.effect('send_text',{phone,text:'ordinary after identity change'}));assert.equal(f.sends.length,0);
});

for(const [label,extra] of [
  ['unconfirmed',{confirmed:false}],['missing request',{diagnostic_request_id:null}],['missing purpose',{purpose:undefined}],
  ['unknown mode',{recipient_mode:'phone'}],['missing verified self',{self_phone:null}],['wrong self',{self_phone:phone}],
  ['generic explicit self',{recipient_mode:'explicit'}],['another device',{device_id:randomUUID()}],
  ['another generation',{generation:randomUUID()}],['another revision',{settings_revision:2}]
]){
  test('diagnostic message refuses '+label+' before any relay',async t=>{
    const f=await fixture(t);await pollEffect(f,diagnosticEffect(f,extra));
    assert.equal(f.sends.length,0);assert.notEqual(f.finished.at(-1)?.state,'accepted');
  });
}

test('self identity changing during recipient lookup cannot fall through into a regular external send',async t=>{
  for(const mode of ['self','explicit']){
    const f=await fixture(t),recipient=mode==='self'?self:phone;
    f.client.onWhatsApp=async pn=>{f.client.user.id='5567999999903@s.whatsapp.net';return [{exists:true,jid:pn+'@s.whatsapp.net'}];};
    await pollEffect(f,diagnosticEffect(f,{recipient,mode}));assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
  }
});

test('a LID, missing PN or changed own PN at message construction never grants the self exception',async t=>{
  for(const next of ['9999999999999999@lid',null,'5567999999903@s.whatsapp.net']){
    const f=await fixture(t);f.behavior.prepare=async()=>{f.client.user.id=next;};
    await pollEffect(f,diagnosticEffect(f));assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
  }
});

test('final SQL recheck after message construction blocks revoked intent immediately before relay',async t=>{
  const f=await fixture(t),original=f.context.deviceEffectAuthorize;let prepared=false,checkedAfterPreparation=false;
  f.behavior.prepare=async()=>{prepared=true;};
  f.context.deviceEffectAuthorize=async input=>{
    if(prepared&&input.recheck){checkedAfterPreparation=true;throw Error('confirmed intent revoked');}
    return original(input);
  };
  await pollEffect(f,diagnosticEffect(f));assert.equal(prepared,true);assert.equal(checkedAfterPreparation,true);
  assert.equal(f.sends.length,0);assert.equal(f.finished.at(-1).state,'not_sent');
});

test('lease/revision changes and altered recheck payloads cannot relay a previously confirmed diagnostic',async t=>{
  for(const phase of ['revision','lease','payload','request','mode']){
    const f=await fixture(t),original=f.context.deviceEffectAuthorize;let prepared=false;
    f.behavior.prepare=async()=>{prepared=true;if(phase==='revision')f.update();if(phase==='lease')f.revoke();};
    f.context.deviceEffectAuthorize=async input=>{
      const result=await original(input);
      if(prepared&&input.recheck){
        if(phase==='payload')result.payload={...result.payload,text:'Changed after confirmation'};
        if(phase==='request')result.diagnostic_request_id=randomUUID();
        if(phase==='mode')result.recipient_mode='explicit';
      }
      return result;
    };
    await pollEffect(f,diagnosticEffect(f));assert.equal(f.sends.length,0,phase);assert.equal(f.finished.at(-1).state,'not_sent',phase);
  }
});

test('confirmed explicit diagnostic preserves target and self identity; timeout after relay stays uncertain without retry',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f,{recipient:phone,mode:'explicit'});f.behavior.send=async()=>{throw Error('Lost provider acknowledgement');};
  await pollEffect(f,effect);assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,phone+'@s.whatsapp.net');
  assert.equal(f.finished.at(-1).state,'uncertain');
  f.session.close();await f.open();await f.poll();assert.equal(f.sends.length,1);assert.equal(f.finished.at(-1).state,'uncertain');
});

test('lost diagnostic finish ACK reconciles an accepted receipt without another grant or message',async t=>{
  const f=await fixture(t),effect=diagnosticEffect(f);let lost=true;
  f.behavior.finish=async()=>{if(lost)throw Error('ACK lost');};await pollEffect(f,effect);assert.equal(f.sends.length,1);
  const count=f.authorizations.length;lost=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[{operation_id:effect.operation_id,device_id:f.snapshot.id,
    generation:f.snapshot.generation,settings_revision:f.snapshot.settings_revision}]});
  await f.poll();assert.equal(f.authorizations.length,count);assert.equal(f.sends.length,1);
  assert.equal(f.finished.at(-1).state,'accepted');assert.equal(f.finished.at(-1).reconcile,true);
});
