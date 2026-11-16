import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import {createNativeTestCanary,NATIVE_TEST_AUDIO,loadNativeTestAsset} from '../src/native-test-canary.mjs';
const phone='5511999999901',other='5511999999903',jid=phone+'@s.whatsapp.net';
const defer=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function fixture(t){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-native-canary-'));fs.chmodSync(directory,0o700);
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const env={QR_NATIVE_TEST_ENABLED:'1',QR_NATIVE_TEST_PHONE:'+'+phone},sends=[];
  let time=1700000000000,alive=true,busy=false,allowed=true,serial=0,instance=randomUUID();
  const scope={tenant_id:randomUUID(),channel_id:randomUUID(),session_digest:'a'.repeat(64),owner:randomUUID(),fence:3,native_generation:7};
  const behavior={resolve:async()=>phone,send:async()=>{}};
  const options={directory,secret:'synthetic-native-canary-secret-'.repeat(3),env,now:()=>time,
    getScope:()=>({...scope}),isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},
    allowedSender:value=>allowed&&value===phone,resolvePhone:key=>behavior.resolve(key),isBusy:()=>busy,
    send:async(target,action,guards)=>{
      const id='provider-'+(++serial);await guards.assertAuthorized();
      assert.equal(guards.isCurrent(),true);await behavior.send({target,action,guards,id});
      guards.onPrepared({key:{id,remoteJid:jid,fromMe:true}});sends.push({target,action,id});return {accepted:true,id};
    }};
  const make=()=>createNativeTestCanary({...options,instance});let canary=make();
  const message=(id,text='ORIONTESTE LISTA',extra={})=>({key:{id,remoteJid:jid,fromMe:false,...extra.key},
    messageTimestamp:time/1000,message:typeof text==='string'?{conversation:text}:text,...extra});
  const receive=(value,extra={})=>{const ticket=canary.observe(value,{type:'notify',...extra});return canary.handle({message:value,type:'notify',ticket,...extra});};
  return {directory,env,scope,sends,behavior,message,receive,get canary(){return canary;},
    advance:ms=>{time+=ms;},revoke:()=>{alive=false;},busy:value=>{busy=value;},allowed:value=>{allowed=value;},
    restart(){instance=randomUUID();canary=make();},options,
    selection(menu,id='choice',slot=0,quote=menu.id){return message(id,{listResponseMessage:{singleSelectReply:{selectedRowId:menu.action.sections[0].rows[slot].id},contextInfo:{stanzaId:quote}}});}};
}
test('only three exact opted-in commands create fixed fictitious actions; oldORIONTESTE and ordinary text are untouched',async t=>{
  const f=fixture(t);
  for(const value of ['ORIONTESTE','lista','oi ORIONTESTE BOTAO'])assert.equal(f.canary.recognizes(f.message('unknown',value)),false);
  assert.equal((await f.receive(f.message('invalid-command','ORIONTESTE LISTA extra'))).state,'ignored');
  for(const [text,type]of [[' orionteste lista ','list'],['ORIONTESTE BOTAO','url_button'],['ORIONTESTE AUDIO','audio']]){
    const result=await f.receive(f.message('command-'+type,text));assert.equal(result.state,'accepted');assert.equal(f.sends.at(-1).action.type,type);
    assert.equal(f.sends.at(-1).target,phone);
  }
  const [menu,cta,audio]=f.sends;assert.match(menu.action.description,/Nenhum horário será reservado/);assert.equal(menu.action.sections[0].rows.length,2);
  assert.equal(cta.action.url,'https://orionagenda.pro/');assert.deepEqual(audio.action.asset,NATIVE_TEST_AUDIO);
});
for(const mode of ['disabled','unconfigured','unallowed','other_sender','fromMe','group','append','replay','old','future','busy'])
test(`native canary refuses ${mode} before any send or ledger claim`,async t=>{
  const f=fixture(t);let input=f.message('refused','ORIONTESTE BOTAO'),extra={};
  if(mode==='disabled')f.env.QR_NATIVE_TEST_ENABLED='0';if(mode==='unconfigured')delete f.env.QR_NATIVE_TEST_PHONE;
  if(mode==='unallowed')f.allowed(false);if(mode==='other_sender')f.behavior.resolve=async()=>other;
  if(mode==='fromMe')input.key.fromMe=true;if(mode==='group')input.key.remoteJid='123456789@g.us';
  if(mode==='append')extra.type='append';if(mode==='replay')extra.replay=true;
  if(mode==='old')input.messageTimestamp-=301;if(mode==='future')input.messageTimestamp+=61;if(mode==='busy')f.busy(true);
  const result=await f.receive(input,extra);assert.equal(result.handled,true);assert.equal(f.sends.length,0);
  assert.equal(fs.existsSync(path.join(f.directory,'native-test')),false);
});

test('native selection requires exact provider+token+participant/current scope then is durably consumed before one fictitious reply',async t=>{
  const f=fixture(t);await f.receive(f.message('menu'));const menu=f.sends[0],valid=f.selection(menu);
  for(const bad of [f.selection(menu,'wrong-quote',0,'other-provider'),f.message('text-token',menu.action.sections[0].rows[0].id),
    f.message('forged',{listResponseMessage:{singleSelectReply:{selectedRowId:'orion:test:forged'},contextInfo:{stanzaId:menu.id}}}),
    f.message('missing-quote',{listResponseMessage:{singleSelectReply:{selectedRowId:menu.action.sections[0].rows[0].id}}})]){
    assert.equal(f.canary.recognizes(bad),true);assert.equal((await f.receive(bad)).state,'selection_refused');
  }
  assert.equal((await f.receive(valid)).state,'accepted');assert.equal(f.sends.length,2);
  assert.equal(f.sends[1].action.type,'text');assert.match(f.sends[1].action.text,/Corte de teste.*Nenhum agendamento foi criado/);
  assert.equal((await f.receive(f.selection(menu,'second-choice',1))).state,'selection_refused');assert.equal(f.sends.length,2);
});

test('a known accepted menu is persisted before any extra await even when a newer input supersedes its original revision',async t=>{
  const f=fixture(t),gate=defer();let prepared,first=true,selected;
  const originalSend=f.options.send;
  const canary=createNativeTestCanary({...f.options,send:async(phone,action,guards)=>{
    const sent=await originalSend(phone,action,guards);
    if(first){first=false;prepared=f.sends[0];selected=f.selection(prepared,'fast-selection');canary.observe(selected,{type:'notify'});}
    return sent;
  }});
  const command=f.message('accepted-fast');const commandTicket=canary.observe(command,{type:'notify'});
  assert.equal((await canary.handle({message:command,type:'notify',ticket:commandTicket})).state,'accepted');
  assert.equal((await canary.handle({message:selected,type:'notify'})).state,'accepted');assert.equal(f.sends.length,2);
});

test('ingress readiness withdrawn after a known relay cannot erase its receipt or authorize another send',async t=>{
  const f=fixture(t);let ready=true;const originalSend=f.options.send;
  const canary=createNativeTestCanary({...f.options,isReady:()=>ready,send:async(...args)=>{const sent=await originalSend(...args);ready=false;return sent;}});
  const input=f.message('accepted-before-control');assert.equal((await canary.handle({message:input,type:'notify',ticket:canary.observe(input,{type:'notify'})})).state,'accepted');
  assert.equal(f.sends.length,1);const selected=f.selection(f.sends[0]);
  assert.equal((await canary.handle({message:selected,type:'notify',ticket:canary.observe(selected,{type:'notify'})})).state,'unavailable');
  assert.equal(f.sends.length,1);
});

for(const field of ['tenant_id','channel_id','owner','fence','native_generation','session_digest','process_instance'])
test(`old menu cannot authorize selection after ${field} changes`,async t=>{
  const f=fixture(t);await f.receive(f.message('menu'));const menu=f.sends[0];
  if(field==='process_instance')f.restart();else if(['fence','native_generation'].includes(field))f.scope[field]++;
  else f.scope[field]=field==='session_digest'?'b'.repeat(64):randomUUID();
  assert.equal((await f.receive(f.selection(menu,'changed'))).state,['tenant_id','channel_id','session_digest'].includes(field)?'unavailable':'selection_refused');assert.equal(f.sends.length,1);
});

test('media captions, view-once and edits cannot become commands; invalid wrapped namespace is consumed without a send',async t=>{
  const f=fixture(t);
  for(const [index,body]of [{imageMessage:{caption:'ORIONTESTE LISTA'}},{videoMessage:{caption:'ORIONTESTE BOTAO'}},
    {viewOnceMessage:{message:{conversation:'ORIONTESTE AUDIO'}}},{editedMessage:{message:{conversation:'ORIONTESTE LISTA'}}},
    {protocolMessage:{editedMessage:{conversation:'ORIONTESTE BOTAO'}}},
    {documentWithCaptionMessage:{message:{documentMessage:{caption:'ORIONTESTE LISTA'}}}},
    {viewOnceMessage:{message:{listResponseMessage:{singleSelectReply:{selectedRowId:'orion:test:forged'}}}}}].entries()){
    const value=f.message('invalid-envelope-'+index,body);assert.equal(f.canary.recognizes(value),true);
    assert.equal((await f.receive(value)).state,'ignored');
  }
  assert.equal(f.sends.length,0);assert.equal(fs.existsSync(path.join(f.directory,'native-test')),false);
});

test('original command/menu deadlines remain enforced after preparation and pacing, without renewal',async t=>{
  const f=fixture(t),old=f.message('near-expiry','ORIONTESTE BOTAO');f.advance(299000);
  f.behavior.send=async({guards})=>{f.advance(3000);assert.equal(guards.isCurrent(),false);await guards.assertAuthorized();};
  assert.equal((await f.receive(old)).state,'uncertain');assert.equal(f.sends.length,0);
  f.behavior.send=async()=>{};await f.receive(f.message('fresh-menu'));const menu=f.sends[0];f.advance(299000);
  f.behavior.send=async({guards})=>{f.advance(3000);assert.equal(guards.isCurrent(),false);await guards.assertAuthorized();};
  assert.equal((await f.receive(f.selection(menu,'selection-near-expiry'))).state,'uncertain');assert.equal(f.sends.length,1);
});

test('menu expiry is original and duplicate delivery cannot refresh it or resend after restart',async t=>{
  const f=fixture(t),input=f.message('original');await f.receive(input);const menu=f.sends[0];f.advance(300001);
  assert.equal((await f.receive(f.selection(menu))).state,'selection_refused');
  input.messageTimestamp+=301;f.restart();assert.equal((await f.receive(input)).state,'duplicate');assert.equal(f.sends.length,1);
});

test('ambiguous sends and failed selection replies retain durable claims and never blindly replay',async t=>{
  const f=fixture(t);f.behavior.send=async()=>{throw Error('ambiguous provider reply');};const command=f.message('uncertain','ORIONTESTE BOTAO');
  assert.equal((await f.receive(command)).state,'uncertain');f.restart();f.behavior.send=async()=>{};
  assert.equal((await f.receive(command)).state,'duplicate');assert.equal(f.sends.length,0);
  await f.receive(f.message('new-menu'));const menu=f.sends[0];f.behavior.send=async()=>{throw Error('unknown after attempt');};
  assert.equal((await f.receive(f.selection(menu,'uncertain-choice'))).state,'uncertain');f.behavior.send=async()=>{};
  assert.equal((await f.receive(f.selection(menu,'retry-new-id'))).state,'selection_refused');assert.equal(f.sends.length,1);
});

test('concurrent duplicate commands reserve once, a newer input cancels only the pending canary, and another PN does not',async t=>{
  const f=fixture(t),gate=defer();f.behavior.send=async({guards})=>{await gate.promise;await guards.assertAuthorized();};
  const first=f.receive(f.message('duplicate','ORIONTESTE BOTAO'));await new Promise(resolve=>setImmediate(resolve));
  assert.equal((await f.receive(f.message('duplicate','ORIONTESTE BOTAO'))).state,'duplicate');
  const unrelated=f.message('other','oi');unrelated.key.remoteJid=other+'@s.whatsapp.net';f.canary.observe(unrelated,{type:'notify'});
  gate.resolve();assert.equal((await first).state,'accepted');assert.equal(f.sends.length,1);
  const gate2=defer();f.behavior.send=async({guards})=>{await gate2.promise;await guards.assertAuthorized();};
  const waiting=f.receive(f.message('waiting','ORIONTESTE BOTAO'));await new Promise(resolve=>setImmediate(resolve));
  f.canary.observe(f.message('new-input','parar mensagens'),{type:'notify'});gate2.resolve();
  assert.equal((await waiting).state,'uncertain');assert.equal(f.sends.length,1);
});

test('a busy command duplicate retains its original ticket and cannot revive after newer input',async t=>{
  const f=fixture(t),original=f.message('busy-original','ORIONTESTE BOTAO');f.busy(true);
  assert.equal((await f.receive(original)).state,'assistant_busy');f.canary.observe(f.message('newer-human-input','Olá'),{type:'notify'});f.busy(false);
  assert.equal((await f.receive(original)).state,'unavailable');assert.equal(f.sends.length,0);
});

test('observer capacity is fail-closed only for the canary and never evicts a still-valid busy command',async t=>{
  const f=fixture(t),original=f.message('busy-before-observer-cap','ORIONTESTE BOTAO');f.busy(true);await f.receive(original);f.busy(false);
  for(let i=0;i<256;i++)f.canary.observe(f.message('observed-'+i,'Mensagem comum'),{type:'notify'});
  assert.equal((await f.receive(original)).state,'capacity_full');
  assert.equal((await f.receive(f.message('new-after-cap','ORIONTESTE BOTAO'))).state,'capacity_full');assert.equal(f.sends.length,0);
});

test('refused command duplicates never renew their timestamp and a restart cannot revive pre-open commands',async t=>{
  const f=fixture(t),original=f.message('busy-expiry','ORIONTESTE BOTAO');f.busy(true);await f.receive(original);f.busy(false);
  f.advance(300001);original.messageTimestamp+=301;
  assert.equal((await f.receive(original)).state,'ignored');assert.equal(f.sends.length,0);
  const prior=f.message('busy-prior-restart','ORIONTESTE BOTAO');f.busy(true);await f.receive(prior);f.busy(false);f.advance(2001);f.restart();
  assert.equal((await f.receive(prior)).state,'ignored');assert.equal(f.sends.length,0);
});

test('missing LID proof, owner loss and flag/destination changes during await cannot send',async t=>{
  for(const mode of ['lid_unknown','owner','flag','destination']){
    const f=fixture(t),gate=defer();f.behavior.resolve=async()=>{await gate.promise;return mode==='lid_unknown'?null:phone;};
    const command=f.message('guard-'+mode,'ORIONTESTE BOTAO');command.key.remoteJid='100000000000001@lid';
    const waiting=f.receive(command);await new Promise(resolve=>setImmediate(resolve));
    if(mode==='owner')f.revoke();if(mode==='flag')f.env.QR_NATIVE_TEST_ENABLED='0';if(mode==='destination')f.env.QR_NATIVE_TEST_PHONE='+'+other;
    gate.resolve();await waiting;assert.equal(f.sends.length,0);
  }
});

test('bounded64-operation journal refuses capacity; corrupt storage is sticky fail-closed and never falls back to memory',async t=>{
  const f=fixture(t);for(let i=0;i<64;i++)assert.equal((await f.receive(f.message('bounded-'+i,'ORIONTESTE BOTAO'))).state,'accepted');
  assert.equal((await f.receive(f.message('bounded-overflow','ORIONTESTE BOTAO'))).state,'capacity_full');assert.equal(f.sends.length,64);
  const base=path.join(f.directory,'native-test'),folder=path.join(base,fs.readdirSync(base)[0]),file=path.join(folder,fs.readdirSync(folder)[0]);
  const original=fs.readFileSync(file);assert.doesNotMatch(original.toString(),new RegExp(phone+'|Acessar meu sistema|orionagenda'));
  fs.writeFileSync(file,'corrupt encrypted evidence');f.restart();
  assert.equal((await f.receive(f.message('corrupt','ORIONTESTE BOTAO'))).state,'storage_unavailable');
  fs.writeFileSync(file,original);assert.equal((await f.receive(f.message('still-corrupt','ORIONTESTE BOTAO'))).state,'storage_unavailable');assert.equal(f.sends.length,64);
});

test('a failed atomic disk write cannot send and remains failed closed, preserving auth bytes',async t=>{
  const f=fixture(t),auth=path.join(f.directory,'creds.enc');fs.writeFileSync(auth,'synthetic original auth bytes');
  const before=fs.readFileSync(auth),rename=fs.renameSync;
  fs.renameSync=()=>{throw Error('synthetic disk fault');};
  try{assert.equal((await f.receive(f.message('disk-error','ORIONTESTE BOTAO'))).state,'storage_unavailable');}finally{fs.renameSync=rename;}
  assert.equal((await f.receive(f.message('no-memory-fallback','ORIONTESTE BOTAO'))).state,'storage_unavailable');
  assert.equal(f.sends.length,0);assert.deepEqual(fs.readFileSync(auth),before);
});

test('audio loader accepts only the literal fixed2088ms MPEG fixture and refuses URLs/paths/other assets',async()=>{
  const buffer=await loadNativeTestAsset({...NATIVE_TEST_AUDIO},{variant:'original'});
  assert.equal(buffer.length,16704);assert.equal(createHash('sha256').update(buffer).digest('hex'),NATIVE_TEST_AUDIO.sha256);
  assert.equal(NATIVE_TEST_AUDIO.duration_ms,2088);
  for(const descriptor of [{...NATIVE_TEST_AUDIO,id:randomUUID()},{...NATIVE_TEST_AUDIO,url:'https://example.com/private.mp3'},
    {...NATIVE_TEST_AUDIO,file_name:'../../private'}])await assert.rejects(loadNativeTestAsset(descriptor,{variant:'original'}));
  await assert.rejects(loadNativeTestAsset({...NATIVE_TEST_AUDIO},{variant:'thumbnail'}));buffer.fill(0);
});
