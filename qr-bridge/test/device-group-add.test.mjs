import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {Readable} from 'node:stream';
import {DEFAULT_CONNECTION_CONFIG} from '@whiskeysockets/baileys';
import {createGroupAddSocket,createDeviceGroupAdd,groupAddAdmin,groupAddLedger,validGroupAddRequest,GROUP_ADD_LIMITS} from '../src/device-group-add.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';
import {createChannelRoutes} from '../src/channel-routes.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';
import {createIngressJournal} from '../src/ingress-journal.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const GROUP='120363000000000001@g.us',OTHER_GROUP='120363000000000002@g.us';
const SELF='5567999999901',PHONE='+5567999999902',PN=PHONE.slice(1)+'@s.whatsapp.net';
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const scope=device=>({device_id:device.id,generation:device.generation,settings_revision:device.settings_revision});
const snapshot=()=>({id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),capabilities:{}});
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-group-add-'));fs.chmodSync(root,0o700);t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
function fixture(){
  let device=snapshot(),alive=true,connected=true,ready=true;const calls=[],forbidden=[];
  const value={id:GROUP,subject:'Grupo sintético',size:1,participants:[{id:SELF+'@s.whatsapp.net',admin:'admin'}]};
  const behavior={groups:async()=>({[GROUP]:value}),metadata:async()=>value,
    add:async(group,jids,action)=>{value.participants.push({id:jids[0]});value.size++;return [{jid:jids[0],status:'200'}];},
    authorize:async()=>{},mutationReady:async()=>{}};
  const client={user:{id:SELF+':2@s.whatsapp.net',lid:'123456789@lid'},
    groupFetchAllParticipating:async()=>{calls.push(['groups']);return behavior.groups();},
    groupMetadata:async id=>{calls.push(['metadata',id]);return behavior.metadata(id);},
    groupParticipantsUpdate:async(...args)=>{calls.push(['add',...args]);return behavior.add(...args);}};
  for(const method of ['sendMessage','relayMessage','groupInviteCode','groupAcceptInvite','groupCreate','groupLeave','requestPairingCode','onWhatsApp'])
    client[method]=()=>{forbidden.push(method);assert.fail('No '+method);};
  const assertMutationReady=async()=>{await behavior.mutationReady();if(!ready)throw Object.assign(Error('private ingress state'),{groupAddReason:'ingress_blocked'});};
  const mutationCurrent=()=>{if(!ready)throw Object.assign(Error('private ingress state'),{groupAddReason:'ingress_blocked'});return alive&&connected;};
  const socket=createGroupAddSocket({client,isCurrent:()=>alive,isConnected:()=>connected,deviceSnapshot:()=>device,assertMutationReady,mutationCurrent});
  const input={...scope(device),group_id:GROUP,operation_id:randomUUID(),phone:PHONE,claim_token:randomUUID()};
  return {client,socket,input,calls,forbidden,value,behavior,assertMutationReady,mutationCurrent,
    authorize:()=>behavior.authorize(),get device(){return device;},get alive(){return alive;},get connected(){return connected;},
    revoke(){alive=false;},disconnect(){connected=false;},block(){ready=false;},update(patch){device={...device,...patch};}};
}
function controllerFixture(t,{timers=false,ledgerFactory=groupAddLedger}={}){
  const f=fixture(),root=temp(t),id=randomUUID(),tenant=randomUUID(),owner=randomUUID(),epoch=randomUUID(),key='synthetic-group-add-key-'+randomUUID();
  let now=100000,fence=1,closed=false;const auth=[],receipts=[],tasks=new Map();let timerId=0;
  const behavior={grant:async value=>value,lease:async()=>{},receipt:async value=>value};
  const broker={deviceGroupAddReceipt:async(channel,credential,input)=>{
    assert.equal(channel,id);assert.equal(credential,key);receipts.push(input);
    return behavior.receipt({...input,channel_id:id,tenant_id:tenant,read_only:true});
  },deviceGroupAddAuthorize:async(channel,credential,lease,payload)=>{
    assert.equal(channel,id);assert.equal(credential,key);auth.push({lease,payload});
    return behavior.grant({...payload,channel_id:id,tenant_id:tenant,owner:lease.owner,fence,authorized:true,expires_in_ms:30000});
  }};
  const resolve=()=>{if(closed)throw Error('closed');return {session:{groupAdd:f.socket},tenantId:tenant,fence,epoch,
    current:()=>!closed&&f.alive&&f.connected&&f.input.device_id===f.device.id&&f.input.generation===f.device.generation&&f.input.settings_revision===f.device.settings_revision,
    authorize:async()=>{await behavior.lease();if(!f.alive||!f.connected)throw Error('stale session');}};};
  const options={broker,root,owner,resolve,now:()=>now,clock:()=>now,ledgerFactory,
    ...(timers?{schedule:(fn,ms)=>{const id=++timerId;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id)}:{})};
  const engine=createDeviceGroupAdd(options);t.after(()=>engine.close());
  return {...f,root,id,tenant,owner,epoch,key,engine,broker,auth,receipts,behavior,options,tasks,
    socketBehavior:f.behavior,get device(){return f.device;},advance(ms){now+=ms;},setFence(value){fence=value;},
    closeContext(){closed=true;},add:(input=f.input)=>engine.add(id,key,input),lookup:(input=f.input)=>engine.lookup(id,key,input),
    async fire(ms){const item=[...tasks].find(([,task])=>task.ms===ms);assert.ok(item,'timer '+ms);tasks.delete(item[0]);item[1].fn();await tick();}};
}

test('group destination permissions use the current raw participant, never the creator display label',async()=>{
  const f=fixture();let result=await f.socket('groups',scope(f.device),{authorize:f.authorize});
  assert.equal(result.groups[0].can_add,true);
  f.value.owner=SELF+'@s.whatsapp.net';f.value.ownerJid=SELF+'@s.whatsapp.net';f.value.participants[0].admin=null;
  result=await f.socket('groups',scope(f.device),{authorize:f.authorize});assert.equal(result.groups[0].can_add,false);
  const members=await f.socket('members',{...scope(f.device),group_id:GROUP},{authorize:f.authorize});
  assert.equal(members.is_admin,false);assert.equal(members.members[0].role,'owner');
  assert.ok(f.calls.every(row=>row[0]!=='add'));assert.deepEqual(f.forbidden,[]);
});

test('administrator proof accepts an authenticated LID but refuses conflicting PN association and duplicate self entries',()=>{
  const user={id:SELF+':4@s.whatsapp.net',lid:'123456789@lid'};
  const meta=participants=>({participants});
  assert.equal(groupAddAdmin(meta([{id:'123456789@lid',jid:SELF+'@s.whatsapp.net',admin:'superadmin'}]),user),true);
  assert.equal(groupAddAdmin(meta([{id:'123456789@lid',admin:'admin'}]),user),true);
  assert.equal(groupAddAdmin(meta([{id:'123456789@lid',jid:PN,admin:'admin'}]),user),false);
  assert.equal(groupAddAdmin(meta([{id:SELF+'@s.whatsapp.net',admin:'owner'}]),user),false);
  assert.equal(groupAddAdmin(meta([{id:SELF+'@s.whatsapp.net',admin:'admin'},{id:SELF+'@s.whatsapp.net',admin:'admin'}]),user),false);
  assert.equal(groupAddAdmin(meta([{id:'123456789@lid',admin:'admin'}]),{id:'123456789@lid'}),true);
});

test('installed Baileys parser preserves the exact PN/LID administrator proof consumed by stage 17',async()=>{
  const {extractGroupMetadata}=await import('@whiskeysockets/baileys/lib/Socket/groups.js');
  const raw=extractGroupMetadata({tag:'iq',attrs:{},content:[{tag:'group',attrs:{id:GROUP.split('@')[0],subject:'Synthetic',size:'2',creator:PN},content:[
    {tag:'participant',attrs:{jid:'123456789@lid',phone_number:SELF+'@s.whatsapp.net',type:'admin'}},
    {tag:'participant',attrs:{jid:'999888777@lid'}}]}]});
  assert.equal(groupAddAdmin(raw,{id:SELF+':5@s.whatsapp.net',lid:'123456789@lid'}),true);
  assert.equal(groupAddAdmin(raw,{id:PN}),false);assert.equal(raw.participants[1].jid,'');
});

test('private schemas reject arbitrary destinations, unscoped fields, noncanonical phones and mutable claims',()=>{
  const f=fixture();assert.equal(validGroupAddRequest('add',f.input),true);
  assert.equal(validGroupAddRequest('lookup',f.input),true);
  for(const patch of [{phone:PHONE.slice(1)},{phone:'123456@lid'},{group_id:PN},{group_id:'1234@g.us'},
    {operation_id:'x'},{claim_token:'x'},{generation:'x'},{settings_revision:0},{settings_revision:'1'},{extra:true}])
    assert.equal(validGroupAddRequest('add',{...f.input,...patch}),false);
  assert.equal(validGroupAddRequest('groups',f.input),false);
  assert.equal(validGroupAddRequest('members',{...scope(f.device),group_id:GROUP}),true);
});

test('one SDK add is followed by fresh membership proof and a durable receipt; retry and lost HTTP ACK never repeat it',async t=>{
  const f=controllerFixture(t),result=await f.add();assert.equal(result.status,'added');assert.equal(result.reason,null);
  assert.deepEqual(f.calls.filter(row=>row[0]==='add'),[['add',GROUP,[PN],'add']]);
  assert.deepEqual(await f.lookup(),result);assert.deepEqual(await f.add(),result);
  const restarted=createDeviceGroupAdd({...f.options,resolve:()=>assert.fail('Receipt must not resolve/start a socket')});
  assert.deepEqual(await restarted.add(f.id,f.key,f.input),result);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);assert.ok(f.auth.length>=2);
  assert.equal(f.auth[0].payload.recheck,false);assert.ok(f.auth.slice(1).every(row=>row.payload.recheck));
  assert.ok(f.auth.every(row=>row.lease.owner===f.owner&&row.lease.fence===1));assert.deepEqual(f.forbidden,[]);
  const directory=path.join(f.root,f.tenant,f.id,'device-group-add');
  for(const entry of fs.readdirSync(directory)){const bytes=fs.readFileSync(path.join(directory,entry));
    assert.equal(bytes.includes(Buffer.from(PHONE)),false);assert.equal(bytes.includes(Buffer.from(GROUP)),false);}
});

test('a current verified PN member is a durable no-op even when represented by LID',async t=>{
  const f=controllerFixture(t);f.value.participants.push({id:'999888777@lid',jid:PN});f.value.size++;
  assert.equal((await f.add()).status,'already_member');assert.equal((await f.lookup()).status,'already_member');
  assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

test('the authenticated own PN is already_member when the unambiguous admin participant has only its own LID',async t=>{
  const f=controllerFixture(t);f.value.participants=[{id:'123456789@lid',admin:'admin'}];f.value.size=1;
  const result=await f.add({...f.input,phone:'+'+SELF});assert.equal(result.status,'already_member');assert.equal(result.settled,true);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

test('a numeric LID alone is never a telephone membership proof',async t=>{
  const f=controllerFixture(t);f.value.participants.push({id:PHONE.slice(1)+'@lid'});f.value.size++;
  assert.equal((await f.add()).status,'added');assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
});

test('explicit participant privacy refusal is not_added and never sends an invite',async t=>{
  const f=controllerFixture(t);f.socketBehavior.add=async()=>[{jid:PN,status:'403',content:{tag:'participant',attrs:{error:'403'},content:[{tag:'add_request',attrs:{code:'private-invite'}}]}}];
  const result=await f.add();assert.equal(result.status,'not_added');assert.equal(result.reason,'provider_refused');
  assert.deepEqual(await f.lookup(),result);assert.deepEqual(f.forbidden,[]);
  assert.equal(JSON.stringify(result).includes('private-invite'),false);
});

for(const kind of ['missing_membership','wrong_jid','missing_response','server_error','timeout_reply','contradictory_refusal'])
  test('provider '+kind+' cannot be presented as a confirmed addition',async t=>{
    const f=controllerFixture(t);f.socketBehavior.add=async()=>{
      if(kind==='contradictory_refusal'){f.value.participants.push({id:PN});f.value.size++;}
      return kind==='missing_response'?[]:[{jid:kind==='wrong_jid'?SELF+'@s.whatsapp.net':PN,
        status:kind==='server_error'?'500':kind==='timeout_reply'?'408':kind==='contradictory_refusal'?'403':'200'}];
    };
    const result=await f.add();assert.equal(result.status,'uncertain');assert.equal(result.reason,'provider_result_unverified');
    await f.add();assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
  });

for(const kind of ['not_admin','foreign_group','partial_metadata','oversized_metadata','ingress'])
  test(kind+' prevents any participant mutation',async t=>{
    const f=controllerFixture(t);
    if(kind==='not_admin')f.value.participants[0].admin=null;
    if(kind==='foreign_group')f.socketBehavior.groups=async()=>({});
    if(kind==='partial_metadata')f.value.size=2;
    if(kind==='oversized_metadata'){f.value.participants=Array.from({length:5001},()=>({id:SELF+'@s.whatsapp.net',admin:'admin'}));f.value.size=5001;}
    if(kind==='ingress')f.block();
    const result=await f.add();assert.equal(result.status,'not_attempted');assert.notEqual(result.reason,null);
    assert.equal(f.calls.filter(row=>row[0]==='add').length,0);assert.deepEqual(f.forbidden,[]);
  });

test('loss of current admin after SDK mutation stays uncertain even if the target is present',async t=>{
  const f=controllerFixture(t);f.socketBehavior.add=async()=>{
    f.value.participants[0].admin=null;f.value.participants.push({id:PN});f.value.size++;return [{jid:PN,status:'200'}];
  };
  const result=await f.add();assert.equal(result.status,'uncertain');assert.equal(result.reason,'not_admin');
});

for(const kind of ['generation','revision','lease','ingress'])test('late '+kind+' revocation fences a preflight without adding anyone',async t=>{
  const f=controllerFixture(t),pending=deferred();f.socketBehavior.metadata=()=>pending.promise;
  const result=f.add();await tick();assert.ok(f.calls.some(row=>row[0]==='metadata'));
  if(kind==='generation')f.update({generation:randomUUID()});if(kind==='revision')f.update({settings_revision:2});
  if(kind==='lease')f.revoke();if(kind==='ingress')f.block();pending.resolve(f.value);
  assert.equal((await result).status,'not_attempted');assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

test('provider throw after invocation is uncertain and a new process only reads its reservation',async t=>{
  const f=controllerFixture(t);f.socketBehavior.add=async()=>{throw Error('private provider response');};
  const result=await f.add();assert.equal(result.status,'uncertain');
  const restarted=createDeviceGroupAdd({...f.options,resolve:()=>assert.fail('No re-execution')});
  assert.deepEqual(await restarted.add(f.id,f.key,f.input),result);assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
});

for(const phase of ['preflight','provider'])test('timeout in '+phase+' retains the lane until the uncancellable SDK work settles',async t=>{
  const f=controllerFixture(t,{timers:true}),pending=deferred();
  if(phase==='preflight')f.socketBehavior.metadata=()=>pending.promise;else f.socketBehavior.add=()=>pending.promise;
  const result=f.add();await tick();await f.fire(12000);
  const timedOut=await result;assert.equal(timedOut.status,phase==='provider'?'uncertain':'not_attempted');
  assert.equal(timedOut.reason,'deadline_exceeded');assert.equal(timedOut.settled,false);assert.equal(f.engine.pending,1);
  const other={...f.input,operation_id:randomUUID(),claim_token:randomUUID(),phone:'+5567999999903'};
  assert.equal((await f.add(other)).reason,'device_busy');assert.deepEqual(await f.add(),timedOut);
  pending.resolve(phase==='preflight'?f.value:[{jid:PN,status:'200'}]);await f.engine.settled();
  assert.equal(f.engine.pending,0);assert.equal((await f.lookup()).settled,true);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,phase==='provider'?1:0);
});

test('concurrent duplicate requests reserve one SDK operation and never turn a pending receipt into another attempt',async t=>{
  const f=controllerFixture(t),pending=deferred();f.socketBehavior.add=()=>pending.promise;
  const first=f.add();await tick();const duplicate=await f.add();assert.equal(duplicate.status,'uncertain');
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
  f.value.participants.push({id:PN});f.value.size++;pending.resolve([{jid:PN,status:'200'}]);
  assert.equal((await first).status,'added');assert.equal((await f.lookup()).status,'added');
});

test('cadence persists across controller restarts and enforces three seconds before another participant',async t=>{
  const f=controllerFixture(t,{timers:true});assert.equal((await f.add()).status,'added');
  const other={...f.input,operation_id:randomUUID(),claim_token:randomUUID(),phone:'+5567999999903'};
  const restarted=createDeviceGroupAdd(f.options),result=restarted.add(f.id,f.key,other);await tick();
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);assert.ok([...f.tasks.values()].some(task=>task.ms===3000));
  f.advance(3000);await f.fire(3000);assert.equal((await result).status,'added');
  assert.equal(f.calls.filter(row=>row[0]==='add').length,2);assert.equal(GROUP_ADD_LIMITS.interval_ms,3000);
});

test('slow final authorization cannot consume the three-second gap before the provider mutation actually starts',async t=>{
  const f=controllerFixture(t);let invokedAt;
  f.socketBehavior.mutationReady=async()=>f.advance(100);
  f.socketBehavior.add=async()=>{invokedAt=f.options.now();f.value.participants.push({id:PN});f.value.size++;return [{jid:PN,status:'200'}];};
  assert.equal((await f.add()).status,'added');
  const persisted=groupAddLedger(f.root,f.tenant,f.id,f.key,{readOnly:true}).cadence();
  assert.ok(persisted>=invokedAt+3000);
});

test('the final SQL permission check is followed only by synchronous readiness and persistence',async()=>{
  const f=fixture();let sqlGranted=false,sourceAllowed=true;
  const socket=createGroupAddSocket({client:f.client,isCurrent:()=>true,isConnected:()=>true,deviceSnapshot:()=>f.device,
    assertMutationReady:async()=>{if(sqlGranted)sourceAllowed=false;},mutationCurrent:()=>true});
  f.behavior.add=async()=>{assert.equal(sourceAllowed,true,'No later readiness/lease await can bypass a revoked SQL source');
    f.value.participants.push({id:PN});f.value.size++;return [{jid:PN,status:'200'}];};
  const result=await socket('add',f.input,{authorize:async()=>{},beforeAttempt:async()=>{sqlGranted=true;},onAttempt(){}});
  assert.equal(result.status,'added');assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
});

for(const delay of [4000,13000])test('blocking durable write of '+delay+'ms cannot bypass the real cadence or local deadline',async t=>{
  let f,delayed=false;const started=[];
  const ledgerFactory=(...args)=>{const actual=groupAddLedger(...args);return {...actual,save:(id,value)=>{
    if(value.attempted&&!value.settled&&!delayed){delayed=true;f.advance(delay);}
    return actual.save(id,value);
  }};};
  f=controllerFixture(t,{timers:true,ledgerFactory});
  f.socketBehavior.add=async(group,jids)=>{started.push(f.options.now());f.value.participants.push({id:jids[0]});f.value.size++;return [{jid:jids[0],status:'200'}];};
  const first=await f.add();assert.equal(first.settled,true);
  if(delay===13000){assert.equal(first.status,'not_attempted');assert.equal(first.reason,'deadline_exceeded');assert.deepEqual(started,[]);return;}
  assert.equal(first.status,'added');assert.deepEqual(started,[104000]);
  const restarted=createDeviceGroupAdd(f.options),other={...f.input,operation_id:randomUUID(),claim_token:randomUUID(),phone:'+5567999999903'};
  const second=restarted.add(f.id,f.key,other);await tick();assert.equal(started.length,1);
  f.advance(3000);await f.fire(3000);assert.equal((await second).status,'added');
  assert.ok(started[1]-started[0]>=3000);
});

test('source permission revoked during durable persistence is rechecked in SQL before the SDK call',async t=>{
  let sourceAllowed=true;
  const ledgerFactory=(...args)=>{const actual=groupAddLedger(...args);return {...actual,save:(id,value)=>{
    const result=actual.save(id,value);if(value.attempted&&!value.settled)sourceAllowed=false;return result;
  }};};
  const f=controllerFixture(t,{ledgerFactory});f.behavior.grant=async grant=>({...grant,authorized:sourceAllowed});
  const result=await f.add();assert.equal(result.status,'not_attempted');assert.equal(result.settled,true);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

test('an unresolved durable lane survives another controller and cannot be cleared by a timeout or receipt read',async t=>{
  const f=controllerFixture(t,{timers:true}),pending=deferred();f.socketBehavior.add=()=>pending.promise;
  const first=f.add();await tick();await f.fire(12000);assert.equal((await first).settled,false);
  const restarted=createDeviceGroupAdd({...f.options,resolve:()=>({...f.options.resolve(),session:{groupAdd:()=>assert.fail('Durable unresolved lane must prevent provider access')}})});
  const other={...f.input,operation_id:randomUUID(),claim_token:randomUUID(),phone:'+5567999999903'};
  const result=await restarted.add(f.id,f.key,other);assert.equal(result.status,'not_attempted');assert.equal(result.reason,'device_busy');
  assert.equal((await restarted.lookup(f.id,f.key,f.input)).settled,false);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
  pending.resolve([{jid:PN,status:'200'}]);await f.engine.settled();
  assert.equal((await restarted.lookup(f.id,f.key,f.input)).settled,true);
});

test('an operation UUID cannot be reused with another phone, destination, claim, generation or revision',async t=>{
  const f=controllerFixture(t);await f.add();
  for(const patch of [{phone:'+5567999999903'},{group_id:OTHER_GROUP},{claim_token:randomUUID()},
    {generation:randomUUID()},{settings_revision:2}]){
    await assert.rejects(f.lookup({...f.input,...patch}),/group_add_unavailable/);
    await assert.rejects(f.add({...f.input,...patch}),/group_add_unavailable/);
  }
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
});

test('receipt-only grant works with no live session but cannot create a ledger or perform a network group read',async t=>{
  const f=controllerFixture(t);f.closeContext();const result=await f.lookup();assert.equal(result.status,'unrecorded');
  assert.equal(result.reason,null);assert.equal(f.calls.length,0);assert.equal(f.auth.length,0);
  assert.equal(fs.existsSync(path.join(f.root,f.tenant)),false);
  f.behavior.receipt=async value=>({...value,read_only:false});await assert.rejects(f.lookup(),/group_add_unavailable/);
});

test('lost terminal ledger write never makes an already attempted operation replayable',async t=>{
  let fail=true;
  const factory=(...args)=>{const actual=groupAddLedger(...args);return {...actual,save:(id,value)=>{
    if(fail&&value.status==='added')throw Error('synthetic disk failure');return actual.save(id,value);}};};
  const f=controllerFixture(t,{ledgerFactory:factory});const result=await f.add();assert.equal(result.status,'uncertain');
  fail=false;const recovered=await f.add();assert.equal(recovered.status,'uncertain');
  assert.equal(f.calls.filter(row=>row[0]==='add').length,1);
});

for(const field of ['owner','fence','tenant_id','operation_id','claim_token','phone','expires_in_ms'])
  test('SQL authorization '+field+' mismatch prevents SDK mutation',async t=>{
    const f=controllerFixture(t);f.behavior.grant=async grant=>({...grant,[field]:field==='fence'?2:field==='expires_in_ms'?30001:field==='phone'?'+5567999999903':randomUUID()});
    assert.equal((await f.add()).status,'not_attempted');assert.equal(f.calls.length,0);
  });

test('stage 17 read projections keep empty names and enforce the complete participant and byte bounds',async()=>{
  const f=fixture();f.value.participants.push({id:PN},{id:PN},{id:'777888999@lid'});f.value.size=4;
  let result=await f.socket('members',{...scope(f.device),group_id:GROUP},{authorize:f.authorize});
  assert.equal(result.is_admin,true);assert.equal(result.members.find(row=>row.phone===PHONE).name,'');
  assert.equal(result.total,4);assert.equal(result.unavailable_count,1);assert.equal(result.duplicate_count,1);
  f.value.participants=[f.value.participants[0],...Array.from({length:4999},()=>({id:PN}))];f.value.size=5000;
  result=await f.socket('members',{...scope(f.device),group_id:GROUP},{authorize:f.authorize});assert.equal(result.total,5000);
  f.value.participants=[{id:SELF+'@s.whatsapp.net',admin:'admin'},...Array.from({length:2000},(_,index)=>({id:String(15550000000+index)+'@s.whatsapp.net',name:'界'.repeat(100)}))];f.value.size=2001;
  await assert.rejects(f.socket('members',{...scope(f.device),group_id:GROUP},{authorize:f.authorize}));
  assert.ok(f.calls.every(row=>row[0]!=='add'));assert.deepEqual(f.forbidden,[]);
});

test('read timeout prevents late provider results from authorizing another call while the old socket query is unresolved',async()=>{
  const f=fixture(),pending=deferred();let timeout;f.behavior.groups=()=>pending.promise;
  const socket=createGroupAddSocket({client:f.client,isCurrent:()=>f.alive,isConnected:()=>f.connected,deviceSnapshot:()=>f.device,
    assertMutationReady:f.assertMutationReady,mutationCurrent:f.mutationCurrent,schedule:fn=>{timeout=fn;return 1;},cancel(){}});
  const result=socket('groups',scope(f.device),{authorize:f.authorize});await tick();timeout();
  await assert.rejects(result,/group_add_unavailable/);
  await assert.rejects(socket('add',f.input,{authorize:f.authorize,beforeAttempt:()=>assert.fail('Busy read cannot mutate')}),/group_add_unavailable/);
  pending.resolve({[GROUP]:f.value});await tick();assert.equal(f.calls.length,1);
});

function managerFixture(t){
  const f=fixture(),root=temp(t),id=randomUUID(),tenant=randomUUID(),key='synthetic-group-add-manager-'+randomUUID();
  let revoked=false,fence=1;const made=[],leaseCalls=[],grants=[];
  const broker={lease:async(channel,credential,operation,owner,expectedFence)=>{
    leaseCalls.push({operation,owner,expectedFence});
    if(revoked||credential!==key||channel!==id)throw Object.assign(Error('private lease rejected'),{status:401});
    return {channel_id:id,tenant_id:tenant,provider:'baileys',owner,fence,expires_in_ms:30000,allowed_senders:[],assistant_enabled:false,device:structuredClone(f.device)};
  },deviceGroupAddReceipt:async(channel,credential,input)=>{
    if(revoked||channel!==id||credential!==key)throw Error('receipt denied');
    return {...input,channel_id:id,tenant_id:tenant,read_only:true};
  },deviceGroupAddAuthorize:async(channel,credential,lease,input)=>{
    if(revoked||channel!==id||credential!==key||lease.fence!==fence)throw Error('claim refused');
    grants.push(input);return {...input,channel_id:id,tenant_id:tenant,authorized:true,owner:lease.owner,fence,expires_in_ms:30000};
  }};
  const manager=createChannelManager({broker,root,clock:()=>0,schedule:()=>1,cancel(){},
    createSession:async context=>{
      const session={context,closed:false,close(){this.closed=true;},status(){return this.closed?'stopped':'connected';},deviceSettingsChanged(){},
        pair:()=>assert.fail('No pairing'),send:()=>assert.fail('No send'),logout:()=>assert.fail('No logout')};
      session.groupAdd=createGroupAddSocket({client:f.client,isCurrent:()=>!session.closed&&context.isCurrent(),
        isConnected:()=>!session.closed&&f.connected,deviceSnapshot:context.deviceSnapshot,assertMutationReady:f.assertMutationReady,mutationCurrent:f.mutationCurrent});
      made.push(session);return session;
    }});t.after(()=>manager.close());
  return {...f,manager,id,key,tenant,made,leaseCalls,grants,get device(){return f.device;},
    revokeCredential(){revoked=true;},changeFence(){fence++;}};
}

test('manager private group reads require the selected current session and cannot create a connection',async t=>{
  const f=managerFixture(t);
  await assert.rejects(f.manager.deviceGroupAdd(f.id,f.key,'groups',scope(f.device)),/group_add_unavailable/);
  assert.equal(f.made.length,0);assert.equal(f.leaseCalls.length,0);
  await f.manager.start(f.id,f.key);f.leaseCalls.length=0;
  for(const [key,input] of [['wrong-key-'.repeat(5),scope(f.device)],[f.key,{...scope(f.device),device_id:randomUUID()}],
    [f.key,{...scope(f.device),generation:randomUUID()}],[f.key,{...scope(f.device),settings_revision:2}]])
    await assert.rejects(f.manager.deviceGroupAdd(f.id,key,'groups',input),/group_add_unavailable/);
  assert.equal(f.calls.length,0);
  const value=await f.manager.deviceGroupAdd(f.id,f.key,'groups',scope(f.device));assert.equal(value.groups[0].can_add,true);
  assert.equal(f.made.length,1);assert.ok(f.leaseCalls.every(call=>call.operation==='check'&&call.expectedFence===1));
});

for(const kind of ['credential','fence','generation','revision','socket'])test('manager fences '+kind+' loss during group-add preflight without replacing the socket',async t=>{
  const f=managerFixture(t);await f.manager.start(f.id,f.key);const pending=deferred();f.behavior.metadata=()=>pending.promise;
  const result=f.manager.deviceGroupAdd(f.id,f.key,'add',f.input);await tick();
  assert.ok(f.calls.some(row=>row[0]==='metadata'));
  if(kind==='credential')f.revokeCredential();if(kind==='fence')f.changeFence();
  if(kind==='generation')f.update({generation:randomUUID()});if(kind==='revision')f.update({settings_revision:2});
  if(kind==='socket')f.made[0].close();pending.resolve(f.value);
  const output=await result;assert.equal(output.status,'not_attempted');assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
  assert.equal(f.made.length,1);assert.deepEqual(f.forbidden,[]);
});

test('manager receipt lookup survives session close and contains no path to start or reconnect',async t=>{
  const f=managerFixture(t);await f.manager.start(f.id,f.key);
  const result=await f.manager.deviceGroupAdd(f.id,f.key,'add',f.input);assert.equal(result.status,'added');
  f.manager.close();const count=f.calls.length;
  assert.deepEqual(await f.manager.deviceGroupAdd(f.id,f.key,'lookup',f.input),result);
  assert.equal(f.calls.length,count);assert.equal(f.made.length,1);
});

async function realSession(t,{blocked=false}={}){
  const f=fixture(),root=temp(t),epoch=randomUUID(),directory=path.join(root,epoch),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  let activeEpoch=epoch,buffering=false;const secret='synthetic-session-key-'.repeat(3),channelId=randomUUID(),tenantId=randomUUID();
  const client={...f.client,ev:new EventEmitter(),end(){}};client.ev.isBuffering=()=>buffering;
  const context={directory,deviceDirectory,secret,channelId,tenantId,owner:randomUUID(),fence:1,
    deviceSnapshot:()=>f.device,isCurrent:()=>activeEpoch===epoch,assertCurrent:async()=>{if(activeEpoch!==epoch)throw Error('epoch changed');},
    notifySelf:false,allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,onConnected(){},onDisconnected(){},
    deviceEvent:async()=>({effects:[]}),deviceEffectsPending:async()=>({effects:[],reconcile:[]}),
    deviceEffectAuthorize:()=>assert.fail('Group add must not use generic effects'),deviceEffectFinish:()=>assert.fail('No generic finish')};
  if(blocked){
    const journal=createIngressJournal({directory:path.join(deviceDirectory,'incoming',epoch),secret});
    journal.append({id:'blocked-message',scope:{tenant_id:tenantId,channel_id:channelId,...scope(f.device)},at:Date.now(),order:1,message:{conversation:'synthetic'}});
    journal.block('blocked-message','business_started');
  }
  const mod={DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version},default:()=>client,BufferJSON:{},
    initAuthCreds:()=>({registered:true}),Browsers:{windows:()=>['synthetic']},DisconnectReason:{loggedOut:401},
    proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}}};
  const session=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,schedule:()=>1,cancel(){}});t.after(()=>session.close());
  client.ev.emit('connection.update',{connection:'open'});await tick();
  return {...f,session,client,context,setBuffering(){buffering=true;},replaceEpoch(){activeEpoch=randomUUID();}};
}

test('real session adapter supports a claimed addition without generic effects or legacy send methods',async t=>{
  const f=await realSession(t);let before=0;
  const result=await f.session.groupAdd('add',f.input,{authorize:f.context.assertCurrent,beforeAttempt:async()=>{},onAttempt:()=>{before++;}});
  assert.equal(result.status,'added');assert.equal(before,1);assert.deepEqual(f.forbidden,[]);
});

for(const kind of ['quarantine','sync_pending','epoch'])test('real session '+kind+' cannot acquire participant mutation authority',async t=>{
  const f=await realSession(t,{blocked:kind==='quarantine'});let before=0;
  if(kind==='sync_pending')f.setBuffering();if(kind==='epoch')f.replaceEpoch();
  await assert.rejects(f.session.groupAdd('add',f.input,{authorize:async()=>{},beforeAttempt:async()=>{},onAttempt:()=>{before++;}}),/group_add_unavailable|epoch changed/);
  assert.equal(before,0);assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

test('a health change while the SQL claim is rechecked still prevents the SDK call',async t=>{
  const f=await realSession(t);
  await assert.rejects(f.session.groupAdd('add',f.input,{authorize:f.context.assertCurrent,beforeAttempt:async()=>f.setBuffering(),onAttempt:()=>assert.fail('No mutation after loss of health')}),/group_add_unavailable/);
  assert.equal(f.calls.filter(row=>row[0]==='add').length,0);
});

async function http(route,url,input,{method='POST',key='synthetic-route-key-'.repeat(3),raw}={}){
  const req=Readable.from([Buffer.from(raw??JSON.stringify(input))]);req.url=url;req.method=method;req.headers=key?{'x-orion-channel-key':key}:{};
  const result={};result.handled=await route(req,{writeHead:(status,headers)=>Object.assign(result,{status,headers}),end:body=>{result.body=JSON.parse(body);}});return result;
}
test('private bridge routes expose only the four scoped stage 17 operations and suppress provider details',async()=>{
  const f=fixture(),id=randomUUID(),calls=[];
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    deviceGroupAdd:async(...args)=>{calls.push(args);if(args[3].phone==='+5567999999999')throw Error('private provider credential');return {ok:true};},
    start:()=>assert.fail('No start'),send:()=>assert.fail('No send'),pair:()=>assert.fail('No pair')})});
  for(const [action,input] of [['groups',scope(f.device)],['members',{...scope(f.device),group_id:GROUP}],['add',f.input],['lookup',f.input]]){
    const result=await http(route,`/internal/channels/${id}/device/group-add/${action}`,input);
    assert.equal(result.status,200);assert.equal(result.headers['Cache-Control'],'no-store');
  }
  assert.deepEqual(calls.map(row=>row[2]),['groups','members','add','lookup']);
  const url=`/internal/channels/${id}/device/group-add/add`;
  assert.equal((await http(route,url,f.input,{method:'GET'})).status,405);
  assert.equal((await http(route,url,f.input,{key:null})).status,401);
  assert.equal((await http(route,url+'?extra=1',f.input)).status,404);
  assert.equal((await http(route,url,{...f.input,extra:true})).status,409);
  assert.equal((await http(route,url,{}, {raw:'x'.repeat(8193)})).status,409);
  const refused=await http(route,url,{...f.input,phone:'+5567999999999'});
  assert.deepEqual(refused.body,{error:'group_add_unavailable'});
  const disabled=createChannelRoutes({env:{},managerFactory:()=>assert.fail('Disabled')});assert.equal((await http(disabled,url,f.input)).status,423);
});

test('broker uses fixed stage 17 claim and receipt paths, exact scoped payload and live lease headers only for claims',async()=>{
  const f=fixture(),id=randomUUID(),key='synthetic-broker-key-'.repeat(3),owner=randomUUID(),requests=[];
  const broker=createChannelBroker({apiUrl:'http://orion.railway.internal/',fetchImpl:async(url,options)=>{
    requests.push({url:String(url),...options});return {ok:true,json:async()=>({ok:true})};}});
  await broker.deviceGroupAddAuthorize(id,key,{owner,fence:4},{...f.input,recheck:false});
  await broker.deviceGroupAddReceipt(id,key,f.input);
  assert.equal(requests[0].url,`http://orion.railway.internal/internal/qr/channels/${id}/device/group-add/authorize`);
  assert.equal(requests[1].url,`http://orion.railway.internal/internal/qr/channels/${id}/device/group-add/receipt`);
  assert.equal(requests[0].headers['X-Orion-Channel-Owner'],owner);assert.equal(requests[0].headers['X-Orion-Channel-Fence'],'4');
  assert.equal(requests[1].headers['X-Orion-Channel-Owner'],undefined);assert.equal(requests[1].headers['X-Orion-Bulk-Protocol'],undefined);
  assert.deepEqual(JSON.parse(requests[0].body),{...f.input,recheck:false});assert.deepEqual(JSON.parse(requests[1].body),f.input);
  assert.throws(()=>broker.deviceGroupAddAuthorize(id,key,{owner,fence:0},{...f.input,recheck:true}),/invalid_group_add_request/);
  assert.throws(()=>broker.deviceGroupAddReceipt(id,key,{...f.input,extra:true}),/invalid_group_add_request/);
  assert.equal(requests.length,2);
});
