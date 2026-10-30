import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {Readable} from 'node:stream';
import vm from 'node:vm';
import {createChannelManager,reconnectDelay} from '../src/channel-manager.mjs';
import {createChannelRoutes} from '../src/channel-routes.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';
import {channelDirectory,encryptedChannelStore} from '../src/channel-storage.mjs';
import {createChannelResumeStore} from '../src/channel-resume.mjs';

const phone='5567999999901',other='5567999999902';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('an explicitly empty private policy connects with assistant off but permits no inbound or outbound recipient',async t=>{
  const f=fixture(t),a=f.add();Object.assign(f.rows.get(a.id),{allowed_senders:[],assistant_enabled:false});
  const m=f.manager();await m.start(a.id,a.key);
  assert.equal(f.made[0].ctx.assistantEnabled(),false);assert.equal(f.made[0].ctx.allowedSender(phone),false);
  await assert.rejects(f.made[0].ctx.onInbound({phone,message_id:'deny-all-1',text:'synthetic'}),/sender_denied/);
  await assert.rejects(m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:randomUUID()}),/sender_denied/);
  assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);assert.equal(f.interactions.length,0);
});
test('an empty private policy with assistant enabled is rejected before socket construction',async t=>{
  const f=fixture(t),a=f.add();f.rows.get(a.id).allowed_senders=[];
  await assert.rejects(f.manager().start(a.id,a.key),/invalid_channel_lease/);assert.equal(f.made.length,0);
});
test('channel status discloses only a connected verified PN, never a LID or a prefilled form number',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  f.made[0].phone=()=>phone;let value=await m.status(a.id,a.key);
  assert.equal(value.phone,phone);assert.equal(value.phone_verified,true);
  f.made[0].phone=()=> '123456789@lid';value=await m.status(a.id,a.key);
  assert.equal(value.phone,null);assert.equal(value.phone_verified,false);
  f.made[0].phone=()=>phone;f.made[0].status=()=> 'qr_ready';value=await m.status(a.id,a.key);
  assert.equal(value.phone,null);assert.equal(value.phone_verified,false);
});
test('assistant-off inbound is journaled before interaction and never forwarded',async t=>{
  const f=fixture(t),a=f.add(),calls=[];Object.assign(f.rows.get(a.id),{inbox_control:true,assistant_enabled:false});
  f.broker.journal=async(id,key,lease,payload)=>{calls.push('journal');assert.equal(id,a.id);assert.equal(payload.text,'Mensagem humana');return {recorded:true};};
  const interaction=f.broker.interaction;f.broker.interaction=async(...args)=>{calls.push('interaction');return interaction(...args);};
  const m=f.manager();await m.start(a.id,a.key);
  await f.made[0].ctx.onInbound({phone,message_id:'journal-1',text:'Mensagem humana',kind:'text'});
  assert.deepEqual(calls,['journal','interaction']);assert.equal(f.forwards.length,0);assert.equal(f.made[0].ctx.assistantEnabled(),false);
});

test('manual send carries the committed revision and real receipts return only to the API',async t=>{
  const f=fixture(t),a=f.add(),permits=[],finished=[],receipts=[];f.rows.get(a.id).inbox_control=true;
  f.broker.authorizeSend=async(id,key,lease,payload)=>{permits.push(payload);return {authorized:true,operation_id:payload.operation_id};};
  f.broker.finishSend=async(...args)=>{finished.push(args[3]);return {released:true};};f.broker.receipt=async(...args)=>{receipts.push(args[3]);return {recorded:true};};
  const m=f.manager();await m.start(a.id,a.key);const operation=randomUUID();
  const result=await m.send(a.id,a.key,{to:phone,text:'Mensagem humana',operation_id:operation,source:'manual',control_revision:7});
  assert.equal(result.accepted,true);assert.equal('provider_id' in result,false);assert.equal(permits[0].source,'manual');assert.equal(permits[0].control_revision,7);
  assert.equal(permits[0].text,'Mensagem humana');await f.made[0].ctx.onReceipt(finished[0].provider_id,'read');
  assert.equal(receipts[0].operation_id,operation);assert.equal(receipts[0].phone,phone);assert.equal(receipts[0].delivery,'read');
});

test('a refused permit proves not_sent without retrying the same operation',async t=>{
  const f=fixture(t),a=f.add();f.rows.get(a.id).inbox_control=true;let permits=0;
  f.broker.authorizeSend=async()=>{permits++;throw Error('current actor revoked');};
  const m=f.manager();await m.start(a.id,a.key);const payload={to:phone,text:'Sintética',operation_id:randomUUID(),source:'manual',control_revision:2};
  assert.deepEqual(await m.send(a.id,a.key,payload),{operation_id:payload.operation_id,state:'refused',accepted:false,delivery:'not_sent'});
  await m.send(a.id,a.key,payload);assert.equal(permits,1);assert.equal(f.made[0].sends.length,0);
});
function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-channel-test-'));fs.chmodSync(root,0o700);
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  let now=0,serial=0;const tasks=new Map(),rows=new Map(),made=[],forwards=[],interactions=[];
  const schedule=(fn,ms)=>{const id=++serial;tasks.set(id,{fn,at:now+ms});return id;};
  const cancel=id=>tasks.delete(id);
  const clock=()=>now;
  function add(tenant=randomUUID()){
    const id=randomUUID(),key='synthetic-channel-secret-'+randomUUID();
    rows.set(id,{channel_id:id,tenant_id:tenant,owner_account_id:randomUUID(),provider:'baileys',key,
      owner:null,fence:0,expires:0,allowed_senders:[phone],assistant_enabled:true});return {id,key,tenant};
  }
  const broker={
    async lease(id,key,op,owner,fence){
      const row=rows.get(id);if(!row||row.key!==key||row.revoked)throw Error('denied');
      if(op==='receipt')return {channel_id:id,tenant_id:row.tenant_id,provider:row.provider,read_only:true};
      if(row.disabled||row.paused)throw Error('disabled');
      if(op==='claim'){
        if(row.expires>now&&row.owner!==owner)throw Error('busy');
        if(row.expires<=now){row.owner=owner;row.fence++;row.expires=now+30000;}
      }else if(op!=='inspect'){
        if(row.owner!==owner||row.fence!==fence||row.expires<=now)throw Error('lost');
        if(op==='renew')row.expires=now+30000;
        if(op==='release'){row.owner=null;row.expires=0;}
      }
      return {...row,owner,expires_in_ms:Math.max(0,row.expires-now)};
    },
    async interaction(id,key,lease,payload){interactions.push({id,key,lease,payload});return {recorded:true,revision:interactions.length};},
    async forward(id,key,lease,payload){forwards.push({id,key,lease,payload});return {answer:null};}
  };
  const createSession=async ctx=>{const session={ctx,closed:false,sends:[],close(){this.closed=true;},status:()=> 'connected',qr:()=>null,
    async send(to,text){this.sends.push({to,text});return {id:randomUUID()};}};made.push(session);return session;};
  function manager(extra={}){const value=createChannelManager({broker,createSession,root,clock,schedule,cancel,...extra});t.after(()=>value.close());return value;}
  return {root,rows,add,broker,manager,made,forwards,interactions,tasks,
    async advance(ms){now+=ms;for(const [id,item] of [...tasks])if(item.at<=now){tasks.delete(id);item.fn();}await flush();},
    setTime:value=>{now=value;}};
}

function connectionNoticeFlag(t,value){
  const previous=process.env.ORION_NOTIFY_SELF_ON_LINK;
  t.after(()=>{if(previous===undefined)delete process.env.ORION_NOTIFY_SELF_ON_LINK;else process.env.ORION_NOTIFY_SELF_ON_LINK=previous;});
  if(value===undefined)delete process.env.ORION_NOTIFY_SELF_ON_LINK;else process.env.ORION_NOTIFY_SELF_ON_LINK=value;
}

test('code pairing is explicit and scoped to the current started channel, never the global alias',async t=>{
  const f=fixture(t),a=f.add(),b=f.add(),m=f.manager(),calls=[];
  assert.equal((await m.status(a.id,a.key)).capabilities.pair,false);
  await assert.rejects(m.pair(a.id,a.key,{phone}));assert.equal(f.made.length,0);
  await m.start(a.id,a.key);await m.start(b.id,b.key);
  assert.equal((await m.status(a.id,a.key)).capabilities.pair,false);
  f.made[0].supportsPairing=()=>true;f.made[0].pair=async value=>{calls.push(value);return {status:'pairing',code:'ABCD-EFGH',expires_in_ms:90000,secret:a.key,phone:value};};
  assert.equal((await m.status(a.id,a.key)).capabilities.pair,true);
  const value=await m.pair(a.id,a.key,{phone});
  assert.deepEqual(value,{channel_id:a.id,status:'pairing',code:'ABCD-EFGH',expires_in_ms:90000});
  assert.deepEqual(calls,[phone]);assert.equal(f.made.length,2);assert.equal(f.made[0].sends.length,0);
  await assert.rejects(m.pair(a.id,b.key,{phone}));assert.equal(f.made[0].closed,false);
  for(const invalid of [null,12345678,'+5567999999901','1234567',phone+'\n','0'.repeat(10)])
    await assert.rejects(m.pair(a.id,a.key,{phone:invalid}));
  assert.equal(calls.length,1);
  const legacy=f.add();f.rows.get(legacy.id).legacy_transport=true;await m.start(legacy.id,legacy.key);
  f.made[2].supportsPairing=()=>true;f.made[2].pair=async()=>assert.fail('legacy cannot request any new pairing code');
  assert.equal((await m.status(legacy.id,legacy.key)).capabilities.pair,false);
  await assert.rejects(m.pair(legacy.id,legacy.key,{phone}));assert.equal(f.made.length,3);
});

test('pairing rechecks lease, tenant and socket generation before returning a private code',async t=>{
  for(const mode of ['revoked','tenant','disabled','generation']){
    const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);const session=f.made[0];
    session.supportsPairing=()=>true;session.pair=async()=>{
      if(mode==='tenant')f.rows.get(a.id).tenant_id=randomUUID();
      else if(mode==='generation')session.ctx.onDisconnected('disconnected');
      else f.rows.get(a.id)[mode]=true;
      return {status:'pairing',code:'ABCD-EFGH',expires_in_ms:90000};
    };
    await assert.rejects(m.pair(a.id,a.key,{phone}));
    assert.equal(session.closed,true);assert.equal(session.sends.length,0);assert.equal(f.made.length,1);
  }
});

test('a delayed lease check cannot extend code validity and malformed session results are refused',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);const session=f.made[0];
  session.supportsPairing=()=>true;
  for(const result of [
    {status:'connected',code:'ABCD-EFGH',expires_in_ms:90000},
    ...[0,true,90001,'90000'].map(expires_in_ms=>({status:'pairing',code:'ABCD-EFGH',expires_in_ms})),
    ...['ABCD-EFGH\n','ABCDEFGH','<script>'].map(code=>({status:'pairing',code,expires_in_ms:90000}))
  ]){session.pair=async()=>result;await assert.rejects(m.pair(a.id,a.key,{phone}));}
  let checks=0;const lease=f.broker.lease;
  f.broker.lease=async(...args)=>{if(args[2]==='check'&&++checks===2)f.setTime(2000);return lease(...args);};
  session.pair=async()=>({status:'pairing',code:'ABCD-EFGH',expires_in_ms:1000});
  await assert.rejects(m.pair(a.id,a.key,{phone}),/expired/);assert.equal(session.sends.length,0);
});

for(const flag of [undefined,'0','1'])for(const legacy of [false,true]){
  test(`${legacy?'legacy':'managed'} channel connection notice obeys ${flag===undefined?'existing default':'explicit global '+flag}`,async t=>{
    connectionNoticeFlag(t,flag);const f=fixture(t),a=f.add();f.rows.get(a.id).legacy_transport=legacy;
    const m=f.manager();await m.start(a.id,a.key);const context=f.made[0].ctx;
    assert.equal(context.notifySelf,!legacy&&flag!=='0');assert.equal(context.legacyTransport,legacy);
    assert.equal(context.assistantEnabled(),true,'notification preference must preserve the current assistant');
    assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);
  });
}
for(const flag of [undefined,'0','1'])test(`resuming a preserved managed session never arms a new-link notice with flag ${flag??'default'}`,async t=>{
  connectionNoticeFlag(t,flag);const f=fixture(t),a=f.add(),row={id:a.id,tenant:a.tenant,key:a.key};
  const resumeStore={list:()=>[row],remember(value){assert.deepEqual(value,row);},forget(){assert.fail('resume must preserve existing session intent');}};
  const m=f.manager({resumeStore});assert.deepEqual(await m.resume(),{resumed:1});const context=f.made[0].ctx;
  assert.equal(context.resumeOnly,true);assert.equal(context.notifySelf,false);assert.equal(context.secret,a.key);
  assert.equal(context.newLinkNotice.pending,false);
  assert.equal(context.assistantEnabled(),true);context.onConnected();
  assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);assert.equal(f.made.length,1);
});

test('API arbitration is mandatory from the lease even with no bridge flag; accepted sends release after receipt persistence',async t=>{
  const f=fixture(t),a=f.add(),permits=[],finished=[];f.rows.get(a.id).inbox_control=true;
  f.broker.authorizeSend=async(id,key,lease,payload)=>{permits.push(payload);return {authorized:true,operation_id:payload.operation_id};};
  f.broker.finishSend=async(id,key,lease,payload)=>{finished.push(payload);return {released:true};};
  const m=f.manager();await m.start(a.id,a.key);const operation=randomUUID();
  const result=await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:operation});
  assert.equal(result.accepted,true);assert.equal(f.made[0].sends.length,1);
  assert.equal(permits.length,1);assert.equal(permits[0].source,'automation');
  assert.equal(finished[0].operation_id,operation);assert.equal(finished[0].accepted,true);
  await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:operation});
  assert.equal(permits.length,1);assert.equal(f.made[0].sends.length,1);
});

test('a human takeover refuses transport and uncertain operations are never automatically replayed',async t=>{
  const f=fixture(t),a=f.add();f.rows.get(a.id).inbox_control=true;
  f.broker.authorizeSend=async()=>{throw Error('human_takeover');};
  f.broker.finishSend=async()=>{throw Error('must_not_finish');};
  const m=f.manager();await m.start(a.id,a.key);const operation=randomUUID();
  const result=await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:operation});
  assert.equal(result.accepted,false);assert.equal(f.made[0].sends.length,0);
  f.broker.authorizeSend=async()=>{throw Error('must_not_retry');};
  assert.equal((await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:operation})).accepted,false);
});

test('a provider exception after a durable grant never releases the uncertain slot',async t=>{
  const f=fixture(t),a=f.add();f.rows.get(a.id).inbox_control=true;let released=0;
  f.broker.authorizeSend=async(id,key,lease,payload)=>({authorized:true,operation_id:payload.operation_id});
  f.broker.finishSend=async()=>{released++;};
  const m=f.manager();await m.start(a.id,a.key);f.made[0].send=async()=>{throw Error('provider_timeout');};
  assert.equal((await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:randomUUID()})).accepted,false);
  assert.equal(released,0);
});

test('only a connected device is remembered; restart revalidates SQL and explicit stop cancels resume',async t=>{
  const f=fixture(t),a=f.add(),store=createChannelResumeStore({root:f.root,secret:'resume-master-'.repeat(4)}),m=f.manager({resumeStore:store});
  f.rows.get(a.id).assistant_enabled=false;
  await m.start(a.id,a.key);assert.deepEqual(store.list(),[]);
  await f.made[0].ctx.onConnected();assert.deepEqual(store.list(),[{id:a.id,tenant:a.tenant,key:a.key}]);
  m.close();f.setTime(31000);
  const restarted=f.manager({resumeStore:store});await restarted.resume();
  assert.equal(f.made.length,2);assert.equal(f.made[1].ctx.resumeOnly,true);assert.equal(f.made[1].ctx.assistantEnabled(),false);
  assert.equal(f.forwards.length,0);await restarted.stop(a.id,a.key);assert.deepEqual(store.list(),[]);
  restarted.close();f.setTime(62000);await f.manager({resumeStore:store}).resume();assert.equal(f.made.length,2);
});

test('restart cannot connect a disabled device, revoked owner or changed tenant',async t=>{
  for(const mode of ['disabled','revoked','tenant']){
    const f=fixture(t),a=f.add(),store=createChannelResumeStore({root:f.root,secret:'resume-master-'.repeat(4)});
    store.remember({id:a.id,tenant:a.tenant,key:a.key});
    if(mode==='tenant')f.rows.get(a.id).tenant_id=randomUUID();else f.rows.get(a.id)[mode]=true;
    await f.manager({resumeStore:store}).resume();assert.equal(f.made.length,0);
  }
});

test('two channels bind their actual tenant, directory, queues and credential independently',async t=>{
  const f=fixture(t),a=f.add(),b=f.add(),m=f.manager();
  await Promise.all([m.start(a.id,a.key),m.start(b.id,b.key)]);
  assert.equal(m.size,2);assert.notEqual(f.made[0].ctx.directory,f.made[1].ctx.directory);
  const contexts=new Map(f.made.map(s=>[s.ctx.channelId,s.ctx]));
  assert.equal(contexts.get(a.id).tenantId,a.tenant);assert.equal(contexts.get(b.id).tenantId,b.tenant);
  await contexts.get(a.id).onMessage({phone,message_id:'synthetic-1',text:'same contact',tenant_id:b.tenant});
  assert.equal(f.forwards[0].id,a.id);assert.deepEqual(Object.keys(f.forwards[0].payload).sort(),['intake_revision','message_id','message_ids','phone','require_summary_review','text']);
});

test('concurrent starts coalesce; another credential cannot read or stop the existing session',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();
  await Promise.all([m.start(a.id,a.key),m.start(a.id,a.key)]);assert.equal(f.made.length,1);
  await assert.rejects(m.status(a.id,'wrong-'.repeat(10)));await assert.rejects(m.stop(a.id,'wrong-'.repeat(10)));
  assert.equal(f.made[0].closed,false);
});

test('connecting a channel does not enable its assistant',async t=>{
  const f=fixture(t),a=f.add();f.rows.get(a.id).assistant_enabled=false;const m=f.manager();await m.start(a.id,a.key);
  await f.made[0].ctx.onMessage({phone,message_id:'synthetic-off',text:'do not interpret'});
  assert.equal(f.made[0].closed,false);assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);
  assert.equal(f.interactions.length,1);
  assert.deepEqual(f.interactions[0].payload,{phone,message_id:'synthetic-off'});
});

test('failed interaction notice blocks inference and reply without falling back or resending',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  f.broker.interaction=async()=>{throw Error('notice unavailable');};
  await assert.rejects(f.made[0].ctx.onMessage({phone,message_id:'synthetic-notice',text:'hello'}));
  assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);
});

test('distributed lease rejects a second process and takeover fences every old operation',async t=>{
  const f=fixture(t),a=f.add(),old=f.manager(),next=f.manager();
  await old.start(a.id,a.key);await assert.rejects(next.start(a.id,a.key));
  f.setTime(31000);await next.start(a.id,a.key);
  await assert.rejects(old.send(a.id,a.key,{to:phone,text:'never',operation_id:randomUUID()}));
  assert.equal(f.made[0].sends.length,0);assert.equal(f.made[1].closed,false);
});

test('a renewal that never returns cannot keep a socket alive after its local deadline',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  const original=f.broker.lease;f.broker.lease=(...args)=>args[2]==='renew'?new Promise(()=>{}):original(...args);
  await f.advance(8001);assert.equal(f.made[0].closed,false);
  await f.advance(22000);assert.equal(f.made[0].closed,true);assert.equal(m.size,0);
});

test('late socket construction after lease expiry closes the obsolete socket',async t=>{
  const f=fixture(t),a=f.add();let resolve,constructed;
  const m=f.manager({createSession:()=>new Promise(r=>{resolve=r;})});
  const pending=m.start(a.id,a.key);await flush();await f.advance(31000);
  constructed={closed:false,close(){this.closed=true;}};resolve(constructed);
  await assert.rejects(pending);assert.equal(constructed.closed,true);assert.equal(m.size,0);
});

test('sender outside persisted pilot allowlist causes zero sends and no send ledger reservation',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  await assert.rejects(m.send(a.id,a.key,{to:other,text:'denied',operation_id:randomUUID()}));
  assert.equal(f.made[0].sends.length,0);
  assert.equal(fs.readdirSync(path.join(f.made[0].ctx.directory,'send-ledger')).length,0);
});

test('ambiguous send is durably reserved before network and never repeated after restart',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager(),operation=randomUUID();await m.start(a.id,a.key);
  let attempts=0;
  f.made[0].send=async()=>{attempts++;assert.equal(fs.readdirSync(path.join(f.made[0].ctx.directory,'send-ledger')).length,1);throw Error('timeout with sensitive content');};
  const payload={to:phone,text:'private synthetic body',operation_id:operation};
  const first=await m.send(a.id,a.key,payload);assert.deepEqual(first,{operation_id:operation,state:'uncertain',accepted:false,delivery:'unknown'});
  assert.deepEqual(await m.send(a.id,a.key,payload),first);await m.stop(a.id,a.key);
  const restarted=f.manager();await restarted.start(a.id,a.key);
  assert.deepEqual(await restarted.send(a.id,a.key,payload),first);
  assert.equal(attempts,1);assert.equal(f.made[1].sends.length,0);
  const file=path.join(f.made[0].ctx.directory,'send-ledger',fs.readdirSync(path.join(f.made[0].ctx.directory,'send-ledger'))[0]);
  assert.equal(fs.readFileSync(file).includes(Buffer.from('private synthetic body')),false);
});

test('same operation with different content conflicts; acceptance is separate from delivery',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  const operation=randomUUID(),payload={to:phone,text:'one',operation_id:operation};
  const first=await m.send(a.id,a.key,payload);assert.equal(first.accepted,true);assert.equal(first.delivery,'unknown');
  await assert.rejects(m.send(a.id,a.key,{...payload,text:'two'}));assert.equal(f.made[0].sends.length,1);
  assert.deepEqual(await m.send(a.id,a.key,payload),first);
});

test('actual receipt advances the ledger but cannot regress a read receipt',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);const id='provider-test-id';
  f.made[0].send=async()=>({id});const payload={to:phone,text:'hello',operation_id:randomUUID()};
  await m.send(a.id,a.key,payload);await f.made[0].ctx.onReceipt(id,'read');await f.made[0].ctx.onReceipt(id,'delivered');
  assert.equal((await m.send(a.id,a.key,payload)).delivery,'read');
});

test('operation observation reads durable acceptance after restart without claiming lease or creating socket',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager(),id=randomUUID();await m.start(a.id,a.key);
  const first=await m.send(a.id,a.key,{to:phone,text:'one synthetic attempt',operation_id:id});
  await m.stop(a.id,a.key);const restarted=f.manager(),before=fs.readdirSync(f.root,{recursive:true});
  const original=f.broker.lease,operations=[];
  f.broker.lease=async(...args)=>{operations.push(args[2]);return original(...args);};
  assert.deepEqual(await restarted.operation(a.id,a.key,id),first);
  assert.deepEqual(operations,['receipt']);assert.equal(f.made.length,1);assert.equal(restarted.size,0);
  assert.deepEqual(fs.readdirSync(f.root,{recursive:true}),before);assert.equal(f.made[0].sends.length,1);
});

test('unrecorded operation stays unknown and read-only, never creating channel directories',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager(),id=randomUUID();
  assert.deepEqual(await m.operation(a.id,a.key,id),{operation_id:id,state:'unrecorded',accepted:false,delivery:'unknown'});
  assert.deepEqual(fs.readdirSync(f.root),[]);assert.equal(f.made.length,0);
  await assert.rejects(m.operation(a.id,'wrong-private-key',id));
  await assert.rejects(m.operation(a.id,a.key,'../../other'));
  assert.deepEqual(fs.readdirSync(f.root),[]);
});

test('operation status cannot cross channel or credential; provider IDs never leave ledger',async t=>{
  const f=fixture(t),a=f.add(),b=f.add(a.tenant),m=f.manager(),id=randomUUID();
  await m.start(a.id,a.key);f.made[0].send=async()=>({id:'synthetic-private-provider-id'});
  await m.send(a.id,a.key,{to:phone,text:'test',operation_id:id});
  assert.equal((await m.operation(a.id,a.key,id)).accepted,true);
  assert.equal(JSON.stringify(await m.operation(a.id,a.key,id)).includes('provider'),false);
  assert.equal((await m.operation(b.id,b.key,id)).state,'unrecorded');
  await assert.rejects(m.operation(a.id,b.key,id));
});

test('receipt lookup survives disabled connection and paused company but cannot send or start',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager(),id=randomUUID();await m.start(a.id,a.key);
  const first=await m.send(a.id,a.key,{to:phone,text:'before pause',operation_id:id});
  f.rows.get(a.id).disabled=true;f.rows.get(a.id).paused=true;
  assert.deepEqual(await m.operation(a.id,a.key,id),first);
  await assert.rejects(m.send(a.id,a.key,{to:phone,text:'forbidden',operation_id:randomUUID()}));
  await assert.rejects(m.start(a.id,a.key));
  assert.deepEqual(await m.operation(a.id,a.key,id),first);
  assert.equal(f.made.length,1);assert.equal(f.made[0].sends.length,1);
  f.rows.get(a.id).revoked=true;await assert.rejects(m.operation(a.id,a.key,id));
});

test('a normal lease grant cannot masquerade as receipt-only authorization',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();
  const original=f.broker.lease;
  f.broker.lease=(id,key,_op,owner)=>original(id,key,'inspect',owner);
  await assert.rejects(m.operation(a.id,a.key,randomUUID()));
  assert.equal(f.made.length,0);assert.deepEqual(fs.readdirSync(f.root),[]);
});

test('pilot rate admission is per channel and a refused operation never later sends',async t=>{
  const f=fixture(t),a=f.add(),b=f.add(),m=f.manager();await m.start(a.id,a.key);await m.start(b.id,b.key);
  for(let n=0;n<5;n++)assert.equal((await m.send(a.id,a.key,{to:phone,text:String(n),operation_id:randomUUID()})).accepted,true);
  const payload={to:phone,text:'rate-limited',operation_id:randomUUID()};
  const refused=await m.send(a.id,a.key,payload);assert.equal(refused.state,'refused');assert.equal(refused.delivery,'not_sent');
  assert.deepEqual(await m.send(a.id,a.key,payload),refused);assert.equal(f.made[0].sends.length,5);
  assert.equal((await m.send(b.id,b.key,{to:phone,text:'separate channel',operation_id:randomUUID()})).accepted,true);
});

test('POSIX auth and ledger files are private', {skip:process.platform==='win32'?'Windows ACLs are not POSIX mode bits':false},async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  await m.send(a.id,a.key,{to:phone,text:'synthetic',operation_id:randomUUID()});
  const directory=f.made[0].ctx.directory,ledger=path.join(directory,'send-ledger');
  assert.equal(fs.statSync(directory).mode&0o777,0o700);
  assert.equal(fs.statSync(path.join(ledger,fs.readdirSync(ledger)[0])).mode&0o777,0o600);
});

test('revoked owner abandons socket; old disconnect callback cannot affect a replacement',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);const old=f.made[0];
  f.rows.get(a.id).revoked=true;await assert.rejects(m.status(a.id,a.key));assert.equal(old.closed,true);
  f.rows.get(a.id).revoked=false;f.setTime(31000);await m.start(a.id,a.key);
  old.ctx.onDisconnected('disconnected');assert.equal(f.made[1].closed,false);
});

test('reconnect jitter is bounded and never chooses a fixed unbounded retry interval',()=>{
  assert.notEqual(reconnectDelay(2,()=>0),reconnectDelay(2,()=>1));
  for(let n=0;n<20;n++){assert.ok(reconnectDelay(n,()=>0)>=800);assert.ok(reconnectDelay(n,()=>1)<=30000);}
});

test('a broker cannot change tenant or fence under an existing session',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  f.rows.get(a.id).tenant_id=randomUUID();await assert.rejects(m.status(a.id,a.key));assert.equal(f.made[0].closed,true);
});

test('independent auth stores are encrypted, reject another key, and forbid the legacy root',t=>{
  const f=fixture(t),a=f.add(),dir=channelDirectory(f.root,a.tenant,a.id);
  const store=encryptedChannelStore({directory:dir,secret:a.key});store.set('creds',{secret:'synthetic-ratchet-key'});
  assert.deepEqual(store.get('creds'),{secret:'synthetic-ratchet-key'});
  assert.throws(()=>encryptedChannelStore({directory:dir,secret:'different-key'}).get('creds'));
  assert.equal(fs.readFileSync(path.join(dir,fs.readdirSync(dir)[0])).includes(Buffer.from('synthetic-ratchet-key')),false);
  assert.throws(()=>channelDirectory('/data/auth',a.tenant,a.id));
});

async function routeCall(route,{url,method='GET',headers={},value={}}){
  const req=Readable.from([Buffer.from(JSON.stringify(value))]);Object.assign(req,{url,method,headers});
  const res={writeHead(code,headers){this.code=code;this.headers=headers;},end(value){this.body=JSON.parse(value);}};
  const handled=await route(req,res);return {handled,...res};
}
test('default-disabled routes perform no network, storage or session construction',async()=>{
  const route=createChannelRoutes({env:{},managerFactory(){throw Error('must not initialize');}});
  const result=await routeCall(route,{url:`/internal/channels/${randomUUID()}/status`});assert.equal(result.code,423);
  assert.equal((await routeCall(route,{url:'/internal/status'})).handled,false);
});
test('legacy key is not a channel credential and authority payload fields are rejected',async()=>{
  let called=0;const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({start(){called++;}})});
  const url=`/internal/channels/${randomUUID()}/start`;
  assert.equal((await routeCall(route,{url,method:'POST',headers:{'x-orion-bridge-key':'legacy-'.repeat(10)}})).code,401);
  assert.equal((await routeCall(route,{url,method:'POST',headers:{'x-orion-channel-key':'channel-'.repeat(10)},value:{tenant_id:randomUUID()}})).code,409);
  assert.equal(called,0);
});

test('pair route accepts only a channel key, explicit canonical phone and POST, with no implicit start',async()=>{
  const id=randomUUID(),key='synthetic-private-'.repeat(3),calls=[];
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    pair:async(...args)=>{calls.push(args);return {channel_id:id,status:'pairing',code:'ABCD-EFGH',expires_in_ms:90000};},
    start:()=>assert.fail('pairing must never implicitly start'),send:()=>assert.fail('pairing never sends a message')} )});
  const url=`/internal/channels/${id}/pair`,headers={'x-orion-channel-key':key};
  assert.equal((await routeCall(route,{url,headers})).code,405);
  assert.equal((await routeCall(route,{url,method:'POST',headers:{'x-orion-bridge-key':key},value:{phone}})).code,401);
  for(const value of [{},{phone:null},{phone:12345678},{phone:phone+'\n'},{phone:'+'+phone},{phone,tenant_id:randomUUID()},
    {phone,key},{phone,expected_revision:1},{phone,source:'manual'}])
    assert.equal((await routeCall(route,{url,headers,method:'POST',value})).code,409);
  assert.equal(calls.length,0);
  const result=await routeCall(route,{url,headers,method:'POST',value:{phone}});
  assert.equal(result.code,200);assert.deepEqual(calls,[[id,key,{phone}]]);
  assert.equal(result.body.code,'ABCD-EFGH');assert.equal(result.headers['Cache-Control'],'no-store');
  assert.equal(JSON.stringify(result.body).includes(key),false);
});

test('pairing provider errors are generic and never disclose phone or provider secrets',async()=>{
  const id=randomUUID(),key='synthetic-private-'.repeat(3);
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    pair:async()=>{throw Error('provider '+phone+' '+key+' ABCD-EFGH');}})});
  const result=await routeCall(route,{url:`/internal/channels/${id}/pair`,method:'POST',headers:{'x-orion-channel-key':key},value:{phone}});
  assert.equal(result.code,409);assert.deepEqual(result.body,{error:'channel_operation_unavailable'});
});
test('operation route authenticates and observes only, never dispatches a send',async()=>{
  const id=randomUUID(),op=randomUUID(),key='synthetic-private-'.repeat(3),calls=[];
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    operation(...args){calls.push(args);return {operation_id:op,state:'uncertain',accepted:false,delivery:'unknown'};},
    send(){throw Error('GET cannot send');},start(){throw Error('GET cannot start');}})});
  const url=`/internal/channels/${id}/operations/${op}`;
  assert.equal((await routeCall(route,{url,headers:{'x-orion-bridge-key':key}})).code,401);
  assert.equal((await routeCall(route,{url,method:'POST',headers:{'x-orion-channel-key':key}})).code,405);
  const result=await routeCall(route,{url,headers:{'x-orion-channel-key':key}});
  assert.equal(result.code,200);assert.deepEqual(calls,[[id,key,op]]);assert.equal(result.body.delivery,'unknown');
});
test('QR route renders a fresh private PNG only and never returns pairing contents',async()=>{
  const id=randomUUID(),key='synthetic-private-'.repeat(3),raw='private-pairing-payload';let rendered=0;
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    qr:async()=>({value:raw,expires_in_ms:30000}),start(){throw Error('QR cannot start');}}),
    renderQr:async value=>{assert.equal(value,raw);rendered++;return 'data:image/png;base64,synthetic';}});
  const result=await routeCall(route,{url:`/internal/channels/${id}/qr`,headers:{'x-orion-channel-key':key}});
  assert.deepEqual(result.body,{image:'data:image/png;base64,synthetic',expires_in_ms:30000});
  assert.equal(rendered,1);assert.equal(JSON.stringify(result.body).includes(raw),false);
});
test('QR rotated or revoked during rendering cannot be returned to browser',async()=>{
  for(const revoked of [false,true]){
    let calls=0;
    const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
      qr:async()=>{if(++calls===2){if(revoked)throw Error('revoked');return {value:'new',expires_in_ms:30000};}
        return {value:'old',expires_in_ms:30000};}}),renderQr:async()=> 'old-image'});
    const result=await routeCall(route,{url:`/internal/channels/${randomUUID()}/qr`,headers:{'x-orion-channel-key':'synthetic-'.repeat(6)}});
    assert.equal(result.code,revoked?409:200);assert.equal(JSON.stringify(result.body).includes('old-image'),false);
  }
});
test('real server handler dispatches new channel paths independently and preserves legacy authentication',async()=>{
  const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
  const handlerSource=source.slice(source.indexOf('const server=http.createServer('),source.indexOf('server.headersTimeout='));
  let handler,calls=0;
  vm.runInNewContext(handlerSource,{URL,http:{createServer(fn){handler=fn;return {};}},
    channelRoutes:async(req,res)=>{calls++;res.code=423;return true;},auth:()=>false,
    reply:(res,code)=>{res.code=code;}});
  const next={};await handler({url:`/internal/channels/${randomUUID()}/status`,method:'GET',headers:{}},next);
  assert.equal(next.code,423);assert.equal(calls,1);
  const legacy={};await handler({url:'/internal/status',method:'GET',headers:{}},legacy);
  assert.equal(legacy.code,401);assert.equal(calls,1);
});
test('broker forbids external URLs and redirects and keeps tenant out of authority payload',async()=>{
  for(const apiUrl of ['https://example.test','http://127.0.0.1:8000','http://api.railway.internal/extra','http://user:pass@api.railway.internal/'])
    assert.throws(()=>createChannelBroker({apiUrl}));
  let sent;const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',fetchImpl:async(url,options)=>{sent={url:String(url),options};return {ok:true,json:async()=>({})};}});
  await broker.lease(randomUUID(),'synthetic-'.repeat(6),'claim',randomUUID());
  assert.equal(sent.options.redirect,'error');assert.equal('tenant_id' in JSON.parse(sent.options.body),false);
});

test('managed audio transport uses only its fixed channel path and fenced raw-body headers',async()=>{
  const id=randomUUID(),owner=randomUUID(),key='synthetic-channel-secret-'.repeat(2),bytes=Buffer.from('OggS synthetic');let sent;
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',env:{QR_AI_AUDIO_ENABLED:'1'},fetchImpl:async(url,options)=>{
    sent={url:String(url),options};return {ok:true,json:async()=>({text:'Corte',truncated:false})};}});
  assert.deepEqual(await broker.transcribe(id,key,{owner,fence:7},{phone,buffer:bytes,mediaType:'audio/ogg'}),{text:'Corte',truncated:false});
  assert.equal(sent.url,`http://api.railway.internal/internal/qr/channels/${id}/audio`);
  assert.equal(sent.options.body,bytes);assert.equal(sent.options.redirect,'error');
  assert.deepEqual(sent.options.headers,{'Content-Type':'audio/ogg','X-Orion-Channel-Key':key,'X-Orion-Channel-Owner':owner,'X-Orion-Channel-Fence':'7','X-Orion-Phone':phone});
  assert.equal('X-Orion-Bridge-Key' in sent.options.headers,false);
});

test('disabled, foreign or malformed managed audio never starts an HTTP request',async()=>{
  const id=randomUUID(),owner=randomUUID(),key='synthetic-channel-secret-'.repeat(2),bytes=Buffer.from('OggS synthetic');
  for(const patch of [{env:{}},{phone:'1234'},{fence:0},{owner:'foreign'},{mediaType:'audio/mp3'},{buffer:Buffer.alloc(2_000_001)}]){
    let calls=0;const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',env:patch.env||{QR_AI_AUDIO_ENABLED:'1'},fetchImpl:async()=>{calls++;assert.fail('must not fetch');}});
    await assert.rejects(broker.transcribe(id,key,{owner:patch.owner||owner,fence:patch.fence??1},{phone:patch.phone||phone,buffer:patch.buffer||bytes,mediaType:patch.mediaType||'audio/ogg'}));
    assert.equal(calls,0);
  }
});

test('audio cancellation reaches the one HTTP attempt and never retries transcription',async()=>{
  const controller=new AbortController();let calls=0,observed;
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',env:{QR_AI_AUDIO_ENABLED:'1'},fetchImpl:async(_url,options)=>{
    calls++;observed=options.signal;return new Promise((_resolve,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted')),{once:true}));}});
  const pending=broker.transcribe(randomUUID(),'synthetic-channel-secret-'.repeat(2),{owner:randomUUID(),fence:1},{phone,buffer:Buffer.from('OggS synthetic'),mediaType:'audio/ogg',signal:controller.signal});
  controller.abort();await assert.rejects(pending,/aborted/);assert.equal(calls,1);assert.equal(observed.aborted,true);
});

test('one successful notice authorizes only its own message and is not duplicated before inference',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager({audioEnabled:()=>true});await m.start(a.id,a.key);
  const context=f.made[0].ctx,payload={phone,message_id:'audio-notice',text:'Corte'};
  const proof=await context.onInbound(payload);await context.onMessage(payload,{noticeProof:proof});assert.equal(f.interactions.length,1);assert.equal(f.forwards.length,1);
  await context.onMessage({...payload,message_id:'different-message'},{noticeProof:proof});assert.equal(f.interactions.length,2);
  assert.deepEqual(Object.keys(f.forwards[0].payload).sort(),['intake_revision','message_id','message_ids','phone','require_summary_review','text']);
});

test('managed transcription checks live allowlist, assistant and lease both before and after ASR',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager({audioEnabled:()=>true});await m.start(a.id,a.key);
  const context=f.made[0].ctx;let calls=0;
  f.broker.transcribe=async(id,key,lease,payload)=>{calls++;assert.equal(id,a.id);assert.equal(key,a.key);assert.equal(payload.phone,phone);return {text:'Quero corte',truncated:false};};
  assert.equal(await context.transcribe({phone,buffer:Buffer.from('OggS synthetic'),mediaType:'audio/ogg'}),'Quero corte');
  await assert.rejects(context.transcribe({phone:other}));assert.equal(calls,1);
  f.broker.transcribe=async()=>{calls++;f.rows.get(a.id).assistant_enabled=false;return {text:'late',truncated:false};};
  await assert.rejects(context.transcribe({phone}));assert.equal(calls,2);assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);
});

test('disabled audio exposes no capability and never invokes the transcription transport',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager({audioEnabled:()=>false});await m.start(a.id,a.key);
  f.broker.transcribe=async()=>assert.fail('must not transcribe');
  await assert.rejects(f.made[0].ctx.transcribe({phone}));
  assert.equal((await m.status(a.id,a.key)).capabilities.audio,false);
  const payload={phone,message_id:'audio-disabled'};await f.made[0].ctx.onInbound(payload);assert.equal(f.interactions.length,1);
});

test('revocation during the typing pause blocks the reply before any send reservation',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);f.broker.forward=async()=>({answer:'Synthetic response'});let pauses=0;
  await assert.rejects(f.made[0].ctx.onMessage({phone,message_id:'typing-revoked',text:'hello'},{beforeReply:async()=>{pauses++;f.rows.get(a.id).revoked=true;}}));
  assert.equal(pauses,1);assert.equal(f.made[0].sends.length,0);assert.equal(fs.readdirSync(path.join(f.made[0].ctx.directory,'send-ledger')).length,0);
});

test('interaction success without a strict server revision never authorizes processing',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  for(const revision of [undefined,0,true,1.5,'1',Number.MAX_SAFE_INTEGER+1]){
    f.broker.interaction=async()=>({recorded:true,revision});
    await assert.rejects(f.made[0].ctx.onMessage({phone,message_id:'invalid-revision',text:'Pode confirmar'}));
  }
  assert.equal(f.forwards.length,0);assert.equal(f.made[0].sends.length,0);
});

test('batch proofs cannot adopt another message or substitute a caller revision',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);const context=f.made[0].ctx;
  const proof=await context.onInbound({phone,message_id:'announced'});
  for(const payload of [
    {phone,message_id:'unknown',message_ids:['unknown'],text:'Sim',intake_revision:1},
    {phone,message_id:'announced',message_ids:['announced'],text:'Sim',intake_revision:999},
    {phone,message_id:'announced',message_ids:['announced','unknown'],text:'Sim',intake_revision:1}
  ])await assert.rejects(context.onMessage(payload,{noticeProofs:[proof]}));
  assert.equal(f.forwards.length,0);assert.equal(f.interactions.length,1);
});

test('only an accepted current response clears summary review; an uncertain send preserves it',async t=>{
  for(const accepted of [true,false]){
    const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
    f.broker.forward=async(_id,_key,_lease,payload)=>{f.forwards.push({payload});return {answer:'Novo resumo para revisão'};};
    if(!accepted)f.made[0].send=async()=>{throw Error('Unknown relay outcome');};
    const context=f.made[0].ctx;
    await context.onMessage({phone,message_id:'summary',text:'Quero corte'});
    await context.onMessage({phone,message_id:'next',text:'Sim'});
    assert.equal(f.forwards[0].payload.require_summary_review,true);
    assert.equal(f.forwards[1].payload.require_summary_review,!accepted);
  }
});

test('old mobile PN is allowed only as the explicitly verified self number of the preserved session',async t=>{
  const f=fixture(t),a=f.add(),old='556781234567';
  Object.assign(f.rows.get(a.id),{allowed_senders:[old],assistant_enabled:false});
  const m=f.manager();await assert.rejects(m.start(a.id,a.key),/invalid_channel_lease/);
  Object.assign(f.rows.get(a.id),{legacy_transport:true,verified_self_phone:old});
  await m.start(a.id,a.key);
  assert.equal(f.made[0].ctx.allowedSender(old),true);
  assert.equal(f.made[0].ctx.allowedSender('5567981234567'),false);
  const result=await m.send(a.id,a.key,{to:old,text:'synthetic self test',operation_id:randomUUID()});
  assert.equal(result.accepted,true);
  assert.equal(f.made[0].sends[0].to,old);
});

test('source automation requests transactional typing, manual sends do not',async t=>{
  const f=fixture(t),a=f.add(),m=f.manager();await m.start(a.id,a.key);
  const seen=[];
  f.made[0].send=async(to,text,opts)=>{
    seen.push({to,text,typing:opts.transactionalTyping});return {id:randomUUID()};
  };
  const auto=await m.send(a.id,a.key,{to:phone,text:'Aviso sintético',operation_id:randomUUID()});
  assert.equal(auto.accepted,true);
  assert.deepEqual(seen,[{to:phone,text:'Aviso sintético',typing:true}]);
  f.broker.authorizeSend=async (_id,_key,_lease,payload)=>({
    authorized:true,operation_id:payload.operation_id
  });
  f.broker.finishSend=async()=>({released:true});
  const manual=await m.send(a.id,a.key,{to:phone,text:'Mensagem manual',operation_id:randomUUID(),
    source:'manual',control_revision:1});
  assert.equal(manual.accepted,true);
  assert.equal(seen.at(-1).typing,false);
});
