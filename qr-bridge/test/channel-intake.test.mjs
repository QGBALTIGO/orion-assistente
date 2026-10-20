import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {randomUUID} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',self='5567999999902',tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
async function until(predicate){for(let i=0;i<100;i++){if(predicate())return;await tick();}assert.fail('Expected pipeline boundary was not reached');}
async function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-managed-intake-'));fs.chmodSync(root,0o700);
  const rows=new Map(),clients=new Map(),contexts=new Map(),notices=[],forwards=[],sent=[],revisions=new Map(),ids=new Map();let asr=0,serial=0;
  const scope=(id,pn)=>id+':'+pn;
  const behavior={asr:async()=> 'Pode confirmar',forward:async()=>({answer:'Resumo atualizado'})};
  const broker={
    async lease(id,key,operation,owner,fence){
      const row=rows.get(id);if(!row||row.key!==key||row.revoked)throw Error('denied');
      if(operation==='claim'){row.owner=owner;row.fence++;}
      else if(operation!=='inspect'&&(row.owner!==owner||row.fence!==fence))throw Error('fenced');
      return {channel_id:id,tenant_id:row.tenant,provider:'baileys',owner,fence:row.fence,expires_in_ms:30000,
        assistant_enabled:row.assistant,allowed_senders:[phone]};
    },
    async interaction(id,_key,_lease,payload){
      const key=scope(id,payload.phone),dedupe=key+':'+payload.message_id;
      if(!ids.has(dedupe)){revisions.set(key,(revisions.get(key)||0)+1);ids.set(dedupe,revisions.get(key));}
      const revision=ids.get(dedupe);notices.push({id,...payload,revision});return {recorded:true,revision};
    },
    async transcribe(id,_key,_lease,payload){asr++;return {text:await behavior.asr(id,payload),truncated:false};},
    async forward(id,_key,_lease,payload){forwards.push({id,...payload});return behavior.forward(id,payload);}
  };
  const manager=createChannelManager({root,broker,clock:()=>0,audioEnabled:()=>true,
    createSession:async context=>{
      contexts.set(context.channelId,context);
      const client={ev:new EventEmitter(),user:{id:self+'@s.whatsapp.net'},end(){},
        onWhatsApp:async value=>[{exists:true,jid:value+'@s.whatsapp.net'}],
        sendMessage:async()=>assert.fail('Direct send is forbidden'),
        relayMessage:async(jid,body)=>{sent.push({id:context.channelId,jid,text:body.conversation});}};
      clients.set(context.channelId,client);
      const mod={default:()=>client,BufferJSON:{},initAuthCreds:()=>({}),Browsers:{windows:()=>['synthetic']},
        DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}},
        downloadMediaMessage:async()=>Buffer.from('OggS synthetic'),generateMessageIDV2:()=> 'sent-'+(++serial),
        generateWAMessage:async(jid,{text})=>({key:{id:'sent-'+(++serial),remoteJid:jid},message:{conversation:text}})};
      const session=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,intakeOptions:{settleMs:0,maxGroupMs:0}});
      client.ev.emit('connection.update',{connection:'open'});return session;
    }});
  t.after(()=>{manager.close();fs.rmSync(root,{recursive:true,force:true});});
  async function add(){const id=randomUUID(),key='synthetic-private-'+randomUUID();rows.set(id,{key,tenant:randomUUID(),fence:0,assistant:true});
    await manager.start(id,key);return {id,key};}
  function receive(id,messageId,body,extra={}){clients.get(id).ev.emit('messages.upsert',{type:'notify',messages:[{
    key:{id:messageId,remoteJid:phone+'@s.whatsapp.net',...extra},messageTimestamp:Date.now()/1000,
    message:typeof body==='string'?{conversation:body}:body}]});}
  return {manager,add,receive,contexts,clients,rows,behavior,notices,forwards,sent,revisions,scope,get asr(){return asr;}};
}
const audio={audioMessage:{fileLength:18,seconds:1,mimetype:'audio/ogg'}};

test('managed audio and a correction during ASR register both revisions before forwarding one ordered batch',async t=>{
  const f=await fixture(t),a=await f.add(),speech=deferred();f.behavior.asr=()=>speech.promise;
  f.receive(a.id,'audio-first',audio);await until(()=>f.asr===1);
  f.receive(a.id,'text-correction','Pera, é quarta');await until(()=>f.notices.length===2);
  assert.equal(f.forwards.length,0);assert.deepEqual(f.notices.map(item=>item.message_id),['audio-first','text-correction']);
  speech.resolve('Pode confirmar');await until(()=>f.sent.length===1);
  assert.equal(f.asr,1);assert.equal(f.forwards.length,1);
  assert.equal(f.forwards[0].text,'Pode confirmar\nPera, é quarta');
  assert.deepEqual(f.forwards[0].message_ids,['audio-first','text-correction']);assert.equal(f.forwards[0].intake_revision,2);
});

test('a correction during managed inference carries prepared audio only after an explicit zero-write result',async t=>{
  const f=await fixture(t),a=await f.add(),inference=deferred();let writes=0;
  f.behavior.forward=async(id,payload)=>{
    if(f.forwards.length===1){await inference.promise;
      assert.notEqual(payload.intake_revision,f.revisions.get(f.scope(id,phone)));
      return {answer:null,step:'superseded',not_committed:true};}
    assert.equal(payload.text,'Pode confirmar\nPera, é quarta');writes++;return {answer:'Correção incorporada; revise o novo resumo'};
  };
  f.receive(a.id,'audio-first',audio);await until(()=>f.forwards.length===1);
  f.receive(a.id,'text-correction','Pera, é quarta');await until(()=>f.notices.length===2);
  assert.equal(writes,0);inference.resolve();await until(()=>f.sent.length===1);
  assert.equal(writes,1);assert.equal(f.forwards.length,2);assert.equal(f.asr,1);
  assert.deepEqual(f.forwards[1].message_ids,['audio-first','text-correction']);assert.equal(f.forwards[1].intake_revision,2);
  assert.equal(f.forwards[1].require_summary_review,true);assert.equal(f.notices.length,2);
});

for(const result of ['unknown','after_write'])test(`managed ${result} never replays the prior operation or ASR`,async t=>{
  const f=await fixture(t),a=await f.add(),inference=deferred();
  f.behavior.forward=async()=>{if(f.forwards.length===1){await inference.promise;
      if(result==='unknown')throw Error('Possibly committed timeout');
      return {answer:null,step:'superseded_after_write',not_committed:false};}
    return {answer:'Confira o resultado anterior antes de alterar'};};
  f.receive(a.id,'old-audio',audio);await until(()=>f.forwards.length===1);
  f.receive(a.id,'new-correction','Pera, é quarta');await until(()=>f.notices.length===2);
  inference.resolve();await until(()=>f.sent.length===1);
  assert.equal(f.forwards.length,2);assert.equal(f.forwards[1].text,'Pera, é quarta');
  assert.deepEqual(f.forwards[1].message_ids,['new-correction']);assert.equal(f.forwards[1].require_summary_review,true);
  assert.equal(f.asr,1);assert.equal(f.sent.some(item=>item.text==='Pode confirmar'),false);
});

test('a known late response is suppressed and the next request requires summary review',async t=>{
  const f=await fixture(t),a=await f.add(),inference=deferred();
  f.behavior.forward=async()=>{if(f.forwards.length===1){await inference.promise;return {answer:'Resumo antigo invisível'};}
    return {answer:'Resumo atual para revisão'};};
  f.receive(a.id,'old-text','Pode confirmar');await until(()=>f.forwards.length===1);
  f.receive(a.id,'correction','Pera, é quarta');await until(()=>f.notices.length===2);inference.resolve();
  await until(()=>f.sent.length===1);assert.equal(f.sent[0].text,'Resumo atual para revisão');
  assert.equal(f.forwards[1].require_summary_review,true);assert.equal(f.forwards[1].text,'Pera, é quarta');
});

test('PN and trusted LID resolve to one ordered conversation while channels remain isolated',async t=>{
  const f=await fixture(t),a=await f.add(),b=await f.add(),speech=deferred();
  f.behavior.asr=id=>id===a.id?speech.promise:Promise.resolve('Outro áudio');
  f.receive(a.id,'shared-id',audio);await until(()=>f.asr===1);
  f.receive(a.id,'correction','Pera, é quarta',{remoteJid:'100000000000001@lid',senderPn:phone+'@s.whatsapp.net'});
  f.receive(b.id,'shared-id','Consulta independente');await until(()=>f.sent.some(item=>item.id===b.id));
  assert.equal(f.forwards.filter(item=>item.id===a.id).length,0);speech.resolve('Pode confirmar');await until(()=>f.sent.length===2);
  const joined=f.forwards.find(item=>item.id===a.id);assert.equal(joined.text,'Pode confirmar\nPera, é quarta');
  assert.equal(joined.intake_revision,2);assert.equal(f.forwards.find(item=>item.id===b.id).intake_revision,1);
  f.receive(a.id,'shared-id',audio);await tick();assert.equal(f.asr,1);assert.equal(f.notices.length,3);
});

test('disabled assistant still registers ingress but performs no audio download, ASR or forward',async t=>{
  const f=await fixture(t),a=await f.add();f.rows.get(a.id).assistant=false;
  f.receive(a.id,'audio-disabled',audio);await until(()=>f.notices.length===1);await tick();
  assert.equal(f.asr,0);assert.equal(f.forwards.length,0);assert.equal(f.sent.length,0);
});
