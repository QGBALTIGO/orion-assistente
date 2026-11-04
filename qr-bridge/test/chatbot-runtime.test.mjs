import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import {randomUUID,createHash} from 'node:crypto';
import * as sdk from '@whiskeysockets/baileys';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import {normalizeChatbotInput,createOrderedIngress} from '../src/chatbot-input.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',self='5567999999902',other='5567999999903';
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const defer=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
async function until(predicate){for(let i=0;i<400;i++){if(predicate())return;await tick();}assert.fail('Expected Chatbot boundary was not reached');}

async function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-chatbot-runtime-'));fs.chmodSync(root,0o700);
  const directory=path.join(root,'auth'),deviceDirectory=path.join(root,'device');fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  let state={id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),chatbot:{revision:1},
    capabilities:{proxy:true,webhook:true,transcription_available:true,transcription_max_bytes:25_000_000,transcription_max_seconds:1800,transcription_max_text_chars:120000}},
    alive=true,clock=1_000_000,serial=0,client,config,session;
  const instances=[],timers=new Map(),events=[],sends=[],finished=[],authorizations=[],audioCalls=[],downloads=[],httpCalls=[],httpResults=[],permits=new Map();
  const behavior={event:async()=>[],finish:async()=>({effects:[]}),authorize:async()=>true,send:async()=>{},capacity:async()=>{},
    pending:async()=>({effects:[],reconcile:[]}),lookup:async()=>null,onWhatsApp:async pn=>[{exists:true,jid:pn+'@s.whatsapp.net'}],
    audio:async()=>({text:'Voz',truncated:false,effects:[]}),http:async()=>({json:{reply:'resposta secreta'}}),httpResult:async payload=>({recorded:true,operation_id:payload.operation_id,effects:[]}),
    asset:()=>assert.fail('This fixture has no granted media'),prepareImage:()=>assert.fail('This fixture has no granted carousel'),fetchImage:()=>assert.fail('No ungranted image fetch')};
  const context={channelId:randomUUID(),tenantId:randomUUID(),directory,deviceDirectory,secret:'synthetic-chatbot-runtime-key-'.repeat(3),
    deviceSnapshot:()=>state,isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,notifySelf:false,
    onInbound:()=>assert.fail('Chatbot must not reach legacy intake'),onMessage:()=>assert.fail('Chatbot must not activate legacy AI'),
    onReceipt:()=>assert.fail('Chatbot must not use legacy receipts'),transcribe:()=>assert.fail('Chatbot must not use legacy ASR'),onConnected(){},onDisconnected(){},
    deviceEvent:async payload=>{events.push(payload);return {recorded:true,effects:await behavior.event(payload)};},
    deviceEffectAuthorize:async payload=>{
      authorizations.push(payload);if(await behavior.authorize(payload)!==true)return {authorized:false,retry_after_ms:1000};
      const grant=permits.get(payload.operation_id);if(!grant)return {authorized:false};return {authorized:true,...payload,...grant};
    },
    deviceEffectFinish:async payload=>{const result=await behavior.finish(payload);finished.push(payload);return result;},
    deviceEffectsPending:async()=>behavior.pending(),
    deviceTranscribe:async payload=>{audioCalls.push(payload);return behavior.audio(payload);},
    deviceChatbotHttpResult:async payload=>{httpResults.push(structuredClone(payload));return behavior.httpResult(payload);},
    deviceChatbotAsset:payload=>behavior.asset(payload),deviceChatbotCarouselImage:payload=>behavior.prepareImage(payload)};
  const schedule=(fn,ms)=>{const id=++serial;timers.set(id,{fn,at:clock+ms,ms});return id;},cancel=id=>timers.delete(id);
  async function open(){
    client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net',name:'Synthetic business'},end(){},
      onWhatsApp:pn=>behavior.onWhatsApp(pn),signalRepository:{lidMapping:{getPNForLID:lid=>behavior.lookup(lid)}},
      sendMessage:()=>assert.fail('No direct or automatic send'),rejectCall:async()=>{},
      waUploadToServer:async()=>({mediaUrl:'https://mmg.whatsapp.net/synthetic',directPath:'/synthetic'}),
      relayMessage:async(jid,proto,options)=>{
        assert.deepEqual(await config.getMessage({id:options.messageId}),JSON.parse(JSON.stringify(proto,sdk.BufferJSON.replacer),sdk.BufferJSON.reviver));
        const item={jid,proto:sdk.proto.Message.decode(sdk.proto.Message.encode(proto).finish()),id:options.messageId};sends.push(item);await behavior.send(item);
      }};
    const mod={...sdk,default:options=>{config=options;return client;},downloadMediaMessage:async()=>{const buffer=Buffer.from('OggS synthetic');downloads.push(buffer);return buffer;}};
    session=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,now:()=>clock,schedule,cancel,
      deviceOptions:{runtimeOptions:{capacity:{acquire:options=>behavior.capacity(options)},chatbotOptions:{httpRequest:async options=>{httpCalls.push(options);return behavior.http(options);},fetchImage:options=>behavior.fetchImage(options)}}}});
    instances.push(session);const before=events.length;client.ev.emit('connection.update',{connection:'open'});await until(()=>events.slice(before).some(row=>row.kind==='status'));return session;
  }
  function effect(action,{target=phone,delay=0,due=clock,extra={}}={}){
    const operation_id=randomUUID();permits.set(operation_id,{type:'chatbot_action',chatbot_revision:state.chatbot.revision,rule_id:randomUUID(),rule_revision:1,
      payload:{phone:target,delay_seconds:delay,action},...extra});
    return {operation_id,type:'chatbot_action',action_type:action.type,chatbot_revision:state.chatbot.revision,delay_seconds:delay,due_at:new Date(due).toISOString()};
  }
  function deviceEffect(type,payload){const operation_id=randomUUID();permits.set(operation_id,{type,payload});return {operation_id,type};}
  function receive(id,body='Oi',key={}){client.ev.emit('messages.upsert',{type:'notify',messages:[{key:{id,fromMe:false,remoteJid:phone+'@s.whatsapp.net',...key},
    messageTimestamp:clock/1000,pushName:'Synthetic contact',message:typeof body==='string'?{conversation:body}:body}]});}
  function revise(){state={...state,chatbot:{revision:state.chatbot.revision+1}};session.deviceSettingsChanged();}
  function advance(ms){clock+=ms;for(const [id,task]of [...timers])if(task.at<=clock){timers.delete(id);void Promise.resolve().then(task.fn).catch(()=>{});}}
  async function poll(){const task=[...timers.values()].find(row=>row.ms===5000);assert.ok(task);await task.fn();}
  await open();t.after(()=>{instances.forEach(item=>item.close());fs.rmSync(root,{recursive:true,force:true});});
  return {root,context,events,sends,finished,authorizations,audioCalls,downloads,httpCalls,httpResults,permits,behavior,effect,deviceEffect,receive,open,revise,advance,poll,
    get state(){return state;},get client(){return client;},get session(){return session;},get now(){return clock;}};
}

test('ingress to a native list uses a current effect, exact encrypted proto and only the authorized contact',async t=>{
  const f=await fixture(t),action=f.effect({type:'list',title:'Menu',description:'Escolha',button_text:'Abrir',sections:[{title:'Opções',rows:[{id:'orion:one',title:'Uma',description:''}]}]});
  f.behavior.event=async row=>row.kind==='text'?[action]:[];f.receive('native-list');await until(()=>f.finished.length===1);
  assert.equal(f.sends.length,1);assert.equal(f.sends[0].jid,phone+'@s.whatsapp.net');assert.equal(f.sends[0].proto.listMessage.sections[0].rows[0].rowId,'orion:one');
  assert.equal(f.finished[0].state,'accepted');assert.ok(f.authorizations.length>=4);assert.ok(f.authorizations.slice(1).every(row=>row.recheck===true));
  const incoming=f.events.find(row=>row.message_id==='native-list');assert.equal(incoming.origin,'contact');assert.equal(incoming.chatbot_revision,1);assert.ok(Number.isSafeInteger(incoming.ingress_order));
});

test('carousel image decoder envelope is unwrapped and every normalized asset remains scoped to the effect',{timeout:10_000},async t=>{
  const f=await fixture(t),completed=defer(),original=Buffer.from('synthetic normalized image'),thumbnail=Buffer.from('synthetic normalized thumbnail'),prepared=[],loaded=[],raw=[];
  const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
  const descriptors=Array.from({length:3},()=>({id:randomUUID(),kind:'image',mime:'image/jpeg',size:original.length,sha256:hash(original),width:512,height:512,
    thumbnail:{mime:'image/jpeg',size:thumbnail.length,sha256:hash(thumbnail)}}));
  const action=f.effect({type:'carousel',body:'Cards',cards:descriptors.map((_,i)=>({title:'Card '+i,body:i?'':'Primeiro',image_url:'https://images.example.com/'+i,
    button_text:'Selecionar',selection_id:'ocb:card-'+i}))});
  f.behavior.fetchImage=async()=>{const buffer=Buffer.from('untrusted image input');raw.push(buffer);return {buffer};};
  f.behavior.prepareImage=async payload=>{prepared.push(payload.card_index);assert.equal(payload.operation_id,action.operation_id);assert.equal(payload.chatbot_revision,1);
    assert.equal(payload.buffer.toString(),'untrusted image input');return {asset:descriptors[payload.card_index]};};
  f.behavior.asset=async payload=>{loaded.push(payload);assert.ok(descriptors.some(asset=>asset.id===payload.asset.id));assert.equal(payload.operation_id,action.operation_id);
    assert.equal(payload.device_id,f.state.id);return Buffer.from(payload.variant==='thumbnail'?thumbnail:original);};
  // The real SDK encrypts media through asynchronous files. Wait for its
  // explicit effect receipt, not a number of event-loop turns under CI load.
  f.behavior.finish=async receipt=>{completed.resolve(receipt);return {effects:[]};};
  f.behavior.event=async row=>row.kind==='text'?[action]:[];f.receive('carousel-envelope');const receipt=await completed.promise;
  assert.equal(receipt.operation_id,action.operation_id);assert.equal(receipt.state,'accepted');assert.deepEqual(prepared,[0,1,2]);assert.equal(loaded.length,6);assert.equal(f.sends.length,1);
  assert.equal(f.sends[0].proto.viewOnceMessage.message.interactiveMessage.carouselMessage.cards.length,3);
  assert.ok(raw.every(buffer=>buffer.every(byte=>byte===0)));
});

test('human owner !commands are admitted but generated echoes are excluded before acknowledgement and after restart',async t=>{
  const f=await fixture(t),action=f.effect({type:'text',text:'!automated content'});
  f.behavior.event=async row=>row.origin==='owner_command'?[action]:[];
  f.behavior.send=async row=>{f.receive(row.id,'!automated content',{fromMe:true});};
  f.receive('human-command','!menu',{fromMe:true});await until(()=>f.finished.length===1);
  assert.equal(f.events.filter(row=>row.origin==='owner_command').length,1);
  const botId=f.sends[0].id;f.session.close();await f.open();f.receive(botId,'!automated content',{fromMe:true});f.receive('human-chat','normal message',{fromMe:true});await tick();await tick();
  assert.equal(f.events.filter(row=>row.origin==='owner_command').length,1);assert.equal(f.sends.length,1);
});

test('provider selections preserve selected ID and quoted menu reference while malformed selections never fall back to visible text',()=>{
  const key={id:'selection',fromMe:false},cases=[
    {listResponseMessage:{title:'Visible',singleSelectReply:{selectedRowId:'orion:one'},contextInfo:{stanzaId:'MENU1'}}},
    {buttonsResponseMessage:{selectedButtonId:'orion:one',selectedDisplayText:'Visible',contextInfo:{stanzaId:'MENU1'}}},
    {templateButtonReplyMessage:{selectedId:'orion:one',selectedDisplayText:'Visible',contextInfo:{stanzaId:'MENU1'}}},
    {interactiveResponseMessage:{nativeFlowResponseMessage:{name:'quick_reply',paramsJson:'{"id":"orion:one"}'},contextInfo:{stanzaId:'MENU1'}}}];
  for(const message of cases)assert.deepEqual(normalizeChatbotInput({key,message}),{origin:'contact',selection_id:'orion:one',quoted_message_id:'MENU1'});
  assert.equal(normalizeChatbotInput({key,message:{listResponseMessage:{singleSelectReply:{selectedRowId:'orion:one'}}}}),null);
  assert.equal(normalizeChatbotInput({key,message:{interactiveResponseMessage:{nativeFlowResponseMessage:{name:'quick_reply',paramsJson:'{"id":"../bad"}'},contextInfo:{stanzaId:'MENU1'}}}}),null);
  assert.deepEqual(normalizeChatbotInput({key,message:{conversation:'orion:one'}}),{origin:'contact',text:'orion:one'});
});

test('arrival order is fixed before delayed PN resolution; one contact waits for ASR while another proceeds',async t=>{
  const f=await fixture(t),lookup=defer(),audio=defer();f.state.settings.transcription.enabled=true;
  const transcribe=f.deviceEffect('transcribe',{phone,message_id:'audio-first',max_seconds:120,max_bytes:25_000_000});
  f.behavior.lookup=()=>lookup.promise;f.behavior.audio=()=>audio.promise;f.behavior.event=async row=>row.kind==='audio'?[transcribe]:[];
  f.receive('audio-first',{audioMessage:{fileLength:100,seconds:12,mimetype:'audio/ogg'}},{remoteJid:'9999999999999999@lid'});
  f.receive('text-second','Depois');f.receive('different-contact','Outro contato',{remoteJid:other+'@s.whatsapp.net'});
  await tick();assert.equal(f.events.some(row=>row.message_id==='text-second'),false);
  lookup.resolve(phone+'@s.whatsapp.net');await until(()=>f.audioCalls.length===1&&f.events.some(row=>row.message_id==='different-contact'));
  assert.equal(f.events.some(row=>row.message_id==='text-second'),false);
  const first=f.events.find(row=>row.message_id==='audio-first');audio.resolve({text:'Reconhecido',truncated:false,effects:[]});
  await until(()=>f.events.some(row=>row.message_id==='text-second'));
  assert.ok(first.ingress_order<f.events.find(row=>row.message_id==='text-second').ingress_order);assert.equal(f.audioCalls.length,1);
});

test('configured delay is based on due_at; revision during the delay prevents even the initial claim',async t=>{
  const f=await fixture(t),action=f.effect({type:'text',text:'Ainda aguardando'},{delay:15,due:f.now+15000});
  f.behavior.event=async row=>row.kind==='text'?[action]:[];f.receive('delayed');await until(()=>f.session.deviceDiagnostics().inflight===1);
  f.revise();f.advance(15000);await until(()=>f.session.deviceDiagnostics().inflight===0);
  assert.equal(f.authorizations.length,0);assert.equal(f.sends.length,0);
});

test('new rule revision aborts only Chatbot; a running Devices transcription still returns its authorized transcript',async t=>{
  const f=await fixture(t),gate=defer();f.state.settings.transcription.enabled=true;
  const audio=f.deviceEffect('transcribe',{phone,message_id:'separate-audio',max_seconds:120,max_bytes:25_000_000}),part=f.deviceEffect('send_text',{phone,text:'Transcrição preservada'});
  f.behavior.event=async row=>row.kind==='audio'?[audio]:[];f.behavior.audio=()=>gate.promise;
  f.receive('separate-audio',{audioMessage:{fileLength:100,seconds:15,mimetype:'audio/ogg'}});await until(()=>f.audioCalls.length===1);
  f.revise();assert.equal(f.audioCalls[0].signal.aborted,false);gate.resolve({text:'Transcrição preservada',truncated:false,effects:[part]});
  await until(()=>f.sends.length===1);assert.equal(f.sends[0].proto.extendedTextMessage.text,'Transcrição preservada');
});

test('capacity and recipient lookup are followed by a fresh rule/hour check before any relay',async t=>{
  const f=await fixture(t),gate=defer(),action=f.effect({type:'text',text:'Não enviar após mudar'});
  let waiting=false;f.behavior.capacity=()=>{waiting=true;return gate.promise;};f.behavior.event=async row=>row.kind==='text'?[action]:[];f.receive('capacity');await until(()=>waiting);
  f.revise();gate.resolve();await until(()=>f.session.deviceDiagnostics().pending===0);assert.equal(f.authorizations.length,0);assert.equal(f.finished.length,0);assert.equal(f.sends.length,0);
  f.behavior.capacity=async()=>{};const next=f.effect({type:'text',text:'Fora de horário'});let open=true;
  f.behavior.event=async row=>row.kind==='text'?[next]:[];f.behavior.authorize=async()=>open;
  f.behavior.onWhatsApp=async pn=>{open=false;return [{exists:true,jid:pn+'@s.whatsapp.net'}];};f.receive('hours');await until(()=>f.finished.length===1);
  assert.equal(f.finished[0].state,'not_sent');assert.equal(f.sends.length,0);
});

test('ambiguous native relay is reserved once and never retried after session restart',async t=>{
  const f=await fixture(t),action=f.effect({type:'text',text:'Uma tentativa'});
  f.behavior.event=async row=>row.kind==='text'?[action]:[];f.behavior.send=async()=>{throw Error('timeout after relay');};f.receive('uncertain');await until(()=>f.finished.length===1);
  assert.equal(f.finished[0].state,'uncertain');const before=f.authorizations.length;
  f.session.close();await f.open();f.receive('uncertain-replayed');await until(()=>f.session.deviceDiagnostics().pending===0);
  assert.equal(f.sends.length,1);assert.equal(f.authorizations.length,before);
});

test('HTTP response is encrypted before persistence and recovery repeats no external request even after rule revision changes',async t=>{
  const f=await fixture(t),action=f.effect({type:'http',url:'https://hooks.example.com/',method:'POST',data:{senderMessage:'synthetic'}});
  f.behavior.event=async row=>row.kind==='text'?[action]:[];let lose=true;
  f.behavior.httpResult=async payload=>{if(lose)throw Error('lost API response');return {recorded:true,operation_id:payload.operation_id,effects:[]};};
  f.receive('http-first');await until(()=>f.httpResults.length===1&&f.session.deviceDiagnostics().pending===0);
  assert.equal(f.httpCalls.length,1);assert.equal(f.sends.length,0);
  const ledgerDir=path.join(f.root,'device','effects','send-ledger');
  for(const file of fs.readdirSync(ledgerDir))assert.ok(!fs.readFileSync(path.join(ledgerDir,file)).includes(Buffer.from('resposta secreta')));
  f.session.close();f.revise();await f.open();lose=false;
  f.behavior.pending=async()=>({effects:[],reconcile:[{...action,device_id:f.state.id,generation:f.state.generation,settings_revision:1}]});
  await f.poll();assert.equal(f.httpCalls.length,1);assert.equal(f.httpResults.length,2);assert.equal(f.httpResults[1].reconcile,true);
  assert.equal(f.httpResults[1].chatbot_revision,1);assert.equal(f.sends.length,0);
});

test('successors wait from predecessor acceptance, and already-due recovered actions do not add the configured delay again',async t=>{
  const f=await fixture(t),first=f.effect({type:'text',text:'Primeira'}),second=f.effect({type:'text',text:'Segunda'},{delay:1,due:f.now+1000});
  f.behavior.event=async row=>row.kind==='text'?[first]:[];
  f.behavior.finish=async row=>({effects:row.operation_id===first.operation_id?[second]:[]});
  f.receive('sequence');await until(()=>f.sends.length===1);assert.equal(f.authorizations.some(row=>row.operation_id===second.operation_id),false);
  await tick();f.advance(1000);await until(()=>f.sends.length===2);assert.deepEqual(f.sends.map(row=>row.proto.extendedTextMessage.text),['Primeira','Segunda']);
  const due=f.effect({type:'text',text:'Já aguardou'},{delay:15,due:f.now-1000});f.behavior.pending=async()=>({effects:[due]});
  await f.poll();assert.equal(f.sends.length,3);
});

test('malformed scope or rule grants are refused before native transport',async t=>{
  const f=await fixture(t),forged=f.effect({type:'text',text:'Do not send'},{extra:{generation:randomUUID()}});
  f.behavior.event=async row=>row.kind==='text'?[forged]:[];f.receive('forged-scope');await until(()=>f.finished.length===1);
  assert.equal(f.finished[0].state,'not_sent');assert.equal(f.sends.length,0);
});

test('bounded ordered ingress drops overflow without calling identity resolution and closes pending work',async()=>{
  const first=defer(),resolved=[],processed=[];
  const ingress=createOrderedIngress({limit:2,resolveIdentity:async input=>{resolved.push(input);if(input===1)await first.promise;return 'same';},run:async input=>processed.push(input)});
  assert.equal(ingress.enqueue(1),true);assert.equal(ingress.enqueue(2),true);assert.equal(ingress.enqueue(3),false);
  await tick();assert.deepEqual(resolved,[1]);ingress.close();first.resolve();await until(()=>ingress.pending()===0);
  assert.deepEqual(processed,[]);assert.deepEqual(resolved,[1]);
});
