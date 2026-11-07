import test from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';
import {Readable} from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {DEFAULT_CONNECTION_CONFIG,initAuthCreds} from '@whiskeysockets/baileys';
import {createDeviceGroupContacts,GROUP_CONTACTS_LIMITS,GROUP_CONTACTS_READ_TIMEOUT_MS,validGroupContactsRequest} from '../src/device-group-contacts.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {createChannelRoutes} from '../src/channel-routes.mjs';
import {createBaileysChannelSession} from '../src/channel-session.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const GROUP='120363000000000001@g.us',OTHER_GROUP='120363000000000002@g.us',PHONE='5567999999901';
const defaults=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/subscription_devices_contract.json',import.meta.url),'utf8')).settings_defaults;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function snapshot(){return {id:randomUUID(),generation:randomUUID(),settings_revision:1,settings:structuredClone(defaults),capabilities:{}};}
function scope(device){return {device_id:device.id,generation:device.generation,settings_revision:device.settings_revision};}
function metadata(participants=[],id=GROUP){return {id,subject:'Grupo sintético',size:participants.length,participants};}
function temp(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-group-read-'));fs.chmodSync(root,0o700);t.after(()=>fs.rmSync(root,{recursive:true,force:true}));return root;}
function fixture(options={}){
  let device=snapshot(),alive=true,connected=true,authorizations=0;
  const value=metadata([{id:PHONE+'@s.whatsapp.net'}]),calls=[],effects=[];
  const behavior={groups:async()=>({[GROUP]:value}),members:async()=>value,authorize:async()=>{}};
  const client={groupFetchAllParticipating:async()=>{calls.push('groups');return behavior.groups();},
    groupMetadata:async id=>{calls.push(['members',id]);return behavior.members(id);}};
  for(const method of ['sendMessage','relayMessage','groupParticipantsUpdate','groupCreate','groupLeave','groupUpdateSubject','requestPairingCode','onWhatsApp'])
    client[method]=()=>{effects.push(method);assert.fail('Read must not call '+method);};
  client.signalRepository={lidMapping:{getPNForLID:()=>assert.fail('No LID contact-store lookup')}};
  const read=createDeviceGroupContacts({client,isCurrent:()=>alive,isConnected:()=>connected,deviceSnapshot:()=>device,...options});
  const authorize=async()=>{authorizations++;await behavior.authorize(authorizations);};
  return {read,client,behavior,calls,effects,authorize,value,get device(){return device;},get input(){return scope(device);},
    get authorizations(){return authorizations;},revoke(){alive=false;},disconnect(){connected=false;},
    update(patch){device={...device,...patch};}};
}

test('group list projects only bounded labels and counts from the current socket',async()=>{
  const f=fixture();f.value.desc='private description';f.value.owner='private owner';
  const value=await f.read('groups',f.input,{authorize:f.authorize});
  assert.deepEqual(value,{...f.input,groups:[{id:GROUP,name:'Grupo sintético',member_count:1}]});
  assert.deepEqual(f.calls,['groups']);assert.equal(f.authorizations,2);assert.deepEqual(f.effects,[]);
  assert.equal(JSON.stringify(value).includes('private'),false);
});

test('members use verified PN fields, keep missing names empty and account for duplicates and unavailable identities',async()=>{
  const f=fixture(),rows=[
    {id:PHONE+'@s.whatsapp.net',name:'Dona',admin:'superadmin'},
    {id:'100001@lid',jid:'5567999999902@s.whatsapp.net',admin:'admin'},
    {id:'5567999999902:4@s.whatsapp.net',name:'Ana'},
    {id:'555666777888999@lid',phoneNumber:'5567999999904'},
    {id:'5567999999903@s.whatsapp.net',jid:'5567999999904@s.whatsapp.net'},
    {id:'5567999999905@newsletter'},
    {id:'15555555555@s.whatsapp.net'},
  ];
  Object.assign(f.value,metadata(rows));
  const value=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.deepEqual(value.members,[{phone:'+'+PHONE,name:'Dona',role:'owner'},
    {phone:'+5567999999902',name:'Ana',role:'admin'},{phone:'+15555555555',name:'',role:'member'}]);
  assert.equal(value.total,7);assert.equal(value.group.member_count,7);
  assert.equal(value.unavailable_count,3);assert.equal(value.duplicate_count,1);
  assert.equal(value.total,value.members.length+value.unavailable_count+value.duplicate_count);
  assert.deepEqual(f.calls,['groups',['members',GROUP],'groups']);assert.equal(f.authorizations,4);assert.deepEqual(f.effects,[]);
  assert.equal(JSON.stringify(value).includes('@lid'),false);
});

test('owner PN and LID metadata affects only the role of an independently verified phone',async()=>{
  const f=fixture();Object.assign(f.value,metadata([{id:'123456789@lid',jid:PHONE+'@s.whatsapp.net'}]),{owner:'123456789@lid'});
  let result=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.deepEqual(result.members,[{phone:'+'+PHONE,name:'',role:'owner'}]);
  delete f.value.participants[0].jid;
  result=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.equal(result.members.length,0);assert.equal(result.unavailable_count,1);
});

test('installed Baileys metadata supplies PN explicitly and leaves a LID-only participant unavailable',async()=>{
  // Load through the same public entry as the real session first: deep-first
  // imports of this Baileys version hit its circular type initialization.
  await import('@whiskeysockets/baileys');
  const {extractGroupMetadata}=await import('@whiskeysockets/baileys/lib/Socket/groups.js');
  const f=fixture();
  const parsed=extractGroupMetadata({tag:'iq',attrs:{},content:[{tag:'group',attrs:{
    id:GROUP.split('@')[0],subject:'Grupo sintético',size:'2',creator:'123456789@lid',creator_pn:PHONE+'@s.whatsapp.net'
  },content:[
    {tag:'participant',attrs:{jid:'123456789@lid',phone_number:PHONE+'@s.whatsapp.net',type:'superadmin'}},
    {tag:'participant',attrs:{jid:'555666777888999@lid'}}
  ]}]});
  assert.equal(parsed.participants[0].jid,PHONE+'@s.whatsapp.net');
  assert.equal(parsed.participants[1].jid,'');
  Object.assign(f.value,parsed);
  const result=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.deepEqual(result.members,[{phone:'+'+PHONE,name:'',role:'owner'}]);
  assert.equal(result.total,2);assert.equal(result.unavailable_count,1);assert.equal(result.duplicate_count,0);
  assert.deepEqual(f.effects,[]);
});

test('labels remove controls and preserve complete Unicode within 100 UTF-16 units',async()=>{
  const f=fixture();f.value.subject='\u0000  Grupo\n\u202e seguro  ';
  Object.assign(f.value, {participants:[{id:PHONE+'@s.whatsapp.net',name:'A'.repeat(99)+'🙂'},
    {id:'5567999999902@s.whatsapp.net',name:'\ud800 Ana\r\n Teste \u2066'}],size:2});
  const value=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.equal(value.group.name,'Grupo seguro');assert.equal(value.members[0].name,'A'.repeat(99));
  assert.equal(value.members[1].name,'Ana Teste');assert.ok(value.members.every(row=>row.name.length<=100));
});

for(const mode of ['missing_authorize','disconnected','revoked','other_device','other_generation','stale_settings'])
  test('read refuses '+mode+' before a provider query',async()=>{
    const f=fixture(),input=f.input;
    if(mode==='disconnected')f.disconnect();if(mode==='revoked')f.revoke();
    if(mode==='other_device')input.device_id=randomUUID();if(mode==='other_generation')input.generation=randomUUID();
    if(mode==='stale_settings')input.settings_revision++;
    await assert.rejects(f.read('groups',input,mode==='missing_authorize'?{}:{authorize:f.authorize}),/group_contacts_unavailable/);
    assert.equal(f.calls.length,0);assert.deepEqual(f.effects,[]);
  });

test('strict private input rejects extra fields, invalid revisions and non-group destinations',async()=>{
  const f=fixture();
  for(const input of [{...f.input,extra:true},{...f.input,settings_revision:0},{...f.input,settings_revision:'1'},
    {...f.input,device_id:'not-a-device'},null,[],{...f.input,group_id:GROUP}]){
    await assert.rejects(f.read('groups',input,{authorize:f.authorize}),/group_contacts_unavailable/);
  }
  for(const group_id of ['1234@g.us','1'.repeat(21)+'@g.us',PHONE+'@s.whatsapp.net','123456789@lid',GROUP+'\n','12345-@g.us'])
    assert.equal(validGroupContactsRequest('members',{...f.input,group_id}),false);
  assert.equal(validGroupContactsRequest('members',{...f.input,group_id:'5567999999901-1700000000@g.us'}),true);
  assert.equal(f.calls.length,0);
});

for(const phase of [1,2,3,4])test('authorization loss at boundary '+phase+' discards all private data',async()=>{
  const f=fixture();f.behavior.authorize=async call=>{if(call===phase)throw Error('private credential and provider detail');};
  await assert.rejects(f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize}),error=>error.message==='group_contacts_unavailable');
  assert.equal(f.calls.length,phase===1?0:phase===2?1:phase===3?2:3);assert.deepEqual(f.effects,[]);
});

for(const mode of ['revoke','disconnect','generation','settings'])test('socket or device '+mode+' during query discards its late result',async()=>{
  const f=fixture(),pending=deferred();f.behavior.members=()=>pending.promise;
  const read=f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  await tick();assert.equal(f.calls.length,2);
  if(mode==='revoke')f.revoke();if(mode==='disconnect')f.disconnect();
  if(mode==='generation')f.update({generation:randomUUID()});if(mode==='settings')f.update({settings_revision:2});
  pending.resolve(f.value);await assert.rejects(read,/group_contacts_unavailable/);
  assert.equal(f.calls.length,2);assert.deepEqual(f.effects,[]);
});

test('foreign group is rejected before metadata and a departed group is rejected after metadata',async()=>{
  const f=fixture();
  await assert.rejects(f.read('members',{...f.input,group_id:OTHER_GROUP},{authorize:f.authorize}),/group_contacts_unavailable/);
  assert.deepEqual(f.calls,['groups']);f.calls.length=0;let calls=0;
  f.behavior.groups=async()=>++calls===1?{[GROUP]:f.value}:{};
  await assert.rejects(f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize}),/group_contacts_unavailable/);
  assert.deepEqual(f.calls,['groups',['members',GROUP],'groups']);
});

test('mismatched metadata identity or a partial participant list cannot be imported as complete',async()=>{
  for(const mode of ['identity','partial']){
    const f=fixture();f.behavior.members=async()=>({...f.value,...(mode==='identity'?{id:OTHER_GROUP}:{size:2})});
    await assert.rejects(f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize}),/group_contacts_unavailable/);
    assert.equal(f.calls.length,2);
  }
});

test('cardinality boundaries are explicit and never silently truncate groups or participants',async()=>{
  const f=fixture();
  f.behavior.groups=async()=>Object.fromEntries(Array.from({length:1000},(_,index)=>{const id=String(120363000000000000n+BigInt(index))+'@g.us';return [id,metadata([],id)];}));
  assert.equal((await f.read('groups',f.input,{authorize:f.authorize})).groups.length,1000);
  const groups=f.behavior.groups;f.behavior.groups=async()=>({...await groups(),[OTHER_GROUP.replace('2@g.us','9999@g.us')]:metadata([],OTHER_GROUP.replace('2@g.us','9999@g.us'))});
  await assert.rejects(f.read('groups',f.input,{authorize:f.authorize}),/group_contacts_unavailable/);
  f.behavior.groups=async()=>({[GROUP]:f.value});Object.assign(f.value,metadata(Array.from({length:5000},()=>({id:PHONE+'@s.whatsapp.net'}))));
  const value=await f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize});
  assert.equal(value.total,5000);assert.equal(value.members.length,1);assert.equal(value.duplicate_count,4999);
  f.value.participants.push({id:PHONE+'@s.whatsapp.net'});f.value.size++;
  await assert.rejects(f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize}),/group_contacts_unavailable/);
});

test('UTF-8 response overflow is rejected before another membership query, without a partial result',async()=>{
  const f=fixture();Object.assign(f.value,metadata(Array.from({length:2000},(_,index)=>({id:String(15550000000+index)+'@s.whatsapp.net',name:'界'.repeat(100)}))));
  await assert.rejects(f.read('members',{...f.input,group_id:GROUP},{authorize:f.authorize}),/group_contacts_unavailable/);
  assert.equal(f.calls.length,2);assert.equal(GROUP_CONTACTS_LIMITS.bytes,240000);
});

test('concurrent and timed-out reads retain one provider slot until the late query settles',async()=>{
  let timeout;const f=fixture({schedule:(fn,ms)=>{assert.equal(ms,GROUP_CONTACTS_READ_TIMEOUT_MS);timeout=fn;return 1;},cancel(){}}),pending=deferred();
  f.behavior.groups=()=>pending.promise;
  const read=f.read('groups',f.input,{authorize:f.authorize});await tick();assert.equal(f.calls.length,1);
  await assert.rejects(f.read('groups',f.input,{authorize:f.authorize}),
    e=>e.message==='group_contacts_unavailable'&&e.groupContactsReason==='read_busy');
  timeout();await assert.rejects(read,
    e=>e.message==='group_contacts_unavailable'&&e.groupContactsReason==='provider_timeout');
  await assert.rejects(f.read('groups',f.input,{authorize:f.authorize}),
    e=>e.message==='group_contacts_unavailable'&&e.groupContactsReason==='read_busy');
  assert.equal(f.calls.length,1);pending.resolve({[GROUP]:f.value});await tick();
  f.behavior.groups=async()=>({[GROUP]:f.value});assert.equal((await f.read('groups',f.input,{authorize:f.authorize})).groups.length,1);
  assert.equal(f.calls.length,2);
});

test('the same timeout covers authorization and prevents a query after late authorization',async()=>{
  let timeout;const f=fixture({schedule:fn=>{timeout=fn;return 1;},cancel(){}}),pending=deferred();
  f.behavior.authorize=()=>pending.promise;
  const read=f.read('groups',f.input,{authorize:f.authorize});await tick();timeout();
  await assert.rejects(read,/group_contacts_unavailable/);pending.resolve();await tick();assert.equal(f.calls.length,0);
});

test('provider errors never expose names, phone numbers or opaque IDs',async()=>{
  const f=fixture();f.behavior.groups=async()=>{throw Error('secret '+GROUP+' '+PHONE);};
  await assert.rejects(f.read('groups',f.input,{authorize:f.authorize}),
    error=>error.message==='group_contacts_unavailable'&&error.groupContactsReason==='provider_failed'
      &&!JSON.stringify(error).includes(GROUP)&&!JSON.stringify(error).includes(PHONE));
  assert.deepEqual(f.effects,[]);
});

function managerFixture(t){
  const root=temp(t),id=randomUUID(),key='synthetic-channel-key-'+randomUUID(),tenant=randomUUID();
  let device=snapshot(),revoked=false,fence=1,state='connected';const calls=[],made=[],tasks=new Map();let serial=0;
  const data=metadata([{id:PHONE+'@s.whatsapp.net'}]),behavior={groups:async()=>({[GROUP]:data}),members:async()=>data};
  const broker={async lease(channel,credential,operation,owner,expectedFence){
    calls.push({channel,credential,operation,owner,expectedFence});
    if(revoked||channel!==id||credential!==key)throw Object.assign(Error('private rejection'),{status:401});
    return {channel_id:id,tenant_id:tenant,provider:'baileys',owner,fence,expires_in_ms:30000,
      allowed_senders:[],assistant_enabled:false,device:structuredClone(device)};
  }};
  const manager=createChannelManager({broker,root,clock:()=>0,schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id),
    createSession:async context=>{
      const client={groupFetchAllParticipating:()=>behavior.groups(),groupMetadata:id=>behavior.members(id)};
      const session={context,closed:false,close(){this.closed=true;},status:()=>state,deviceSettingsChanged(){},
        send:()=>assert.fail('No send'),pair:()=>assert.fail('No pair'),logout:()=>assert.fail('No logout')};
      session.groupContacts=createDeviceGroupContacts({client,isCurrent:()=>!session.closed&&context.isCurrent(),
        isConnected:()=>state==='connected',deviceSnapshot:context.deviceSnapshot});made.push(session);return session;
    }});
  t.after(()=>manager.close());
  return {manager,id,key,tenant,calls,made,behavior,tasks,data,get input(){return scope(device);},
    revoke(){revoked=true;},replaceFence(){fence++;},update(patch){device={...device,...patch};},disconnect(){state='connecting';}};
}

test('manager never starts a stopped channel and validates the private key and exact device before a read',async t=>{
  const f=managerFixture(t);
  await assert.rejects(f.manager.deviceGroupContacts(f.id,f.key,'groups',f.input),/group_contacts_unavailable/);
  assert.equal(f.made.length,0);assert.equal(f.calls.length,0);
  await f.manager.start(f.id,f.key);f.calls.length=0;let reads=0;
  f.behavior.groups=async()=>{reads++;return {[GROUP]:f.data};};
  for(const [key,input] of [['wrong-key-'.repeat(5),f.input],[f.key,{...f.input,device_id:randomUUID()}],
    [f.key,{...f.input,generation:randomUUID()}],[f.key,{...f.input,settings_revision:2}]])
    await assert.rejects(f.manager.deviceGroupContacts(f.id,key,'groups',input),/group_contacts_unavailable/);
  assert.equal(reads,0);assert.equal(f.calls.length,0);
  const value=await f.manager.deviceGroupContacts(f.id,f.key,'groups',f.input);
  assert.equal(value.groups.length,1);assert.equal(f.made.length,1);assert.equal(reads,1);
  assert.deepEqual(f.calls.map(call=>call.operation),['check','check']);
  assert.ok(f.calls.every(call=>call.expectedFence===1&&call.credential===f.key));
});

for(const mode of ['credential','fence','generation','settings','disconnected'])
  test('manager discards a read after '+mode+' changes without creating another session',async t=>{
    const f=managerFixture(t);await f.manager.start(f.id,f.key);f.calls.length=0;
    const pending=deferred();f.behavior.groups=()=>pending.promise;
    const read=f.manager.deviceGroupContacts(f.id,f.key,'groups',f.input);await tick();
    if(mode==='credential')f.revoke();if(mode==='fence')f.replaceFence();
    if(mode==='generation')f.update({generation:randomUUID()});if(mode==='settings')f.update({settings_revision:2});
    if(mode==='disconnected')f.disconnect();
    pending.resolve({[GROUP]:f.data});await assert.rejects(read,/group_contacts_unavailable/);assert.equal(f.made.length,1);
    assert.ok(f.calls.every(call=>call.operation==='check'));
  });

test('real session adapter exposes reads only after open and guards its socket independently of manager authorization',async t=>{
  const root=temp(t),directory=path.join(root,randomUUID()),deviceDirectory=path.join(root,'device');
  fs.mkdirSync(directory,{mode:0o700});fs.mkdirSync(deviceDirectory,{mode:0o700});
  const device=snapshot(),events=[],effects=[],tasks=new Map();let serial=0,alive=true,reads=0;
  const client={ev:new EventEmitter(),user:{id:PHONE+'@s.whatsapp.net'},end(){},
    groupFetchAllParticipating:async()=>{reads++;return {[GROUP]:metadata([])};},groupMetadata:async()=>metadata([]),
    sendMessage:()=>assert.fail('No sendMessage'),relayMessage:()=>assert.fail('No relay'),groupParticipantsUpdate:()=>assert.fail('No group mutation')};
  const context={directory,deviceDirectory,secret:'synthetic-device-secret-'.repeat(3),deviceSnapshot:()=>device,
    isCurrent:()=>alive,assertCurrent:async()=>{if(!alive)throw Error('revoked');},notifySelf:false,
    allowedSender:()=>false,assistantEnabled:()=>false,audioEnabled:()=>false,onConnected(){},onDisconnected(){},
    deviceEvent:async row=>{events.push(row);return {effects:[]};},deviceEffectsPending:async()=>({effects:[],reconcile:[]}),
    deviceEffectAuthorize:()=>{effects.push('authorize');assert.fail('No outbound effect');},deviceEffectFinish:()=>assert.fail('No effect finish')};
  const mod={DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version},
    default:()=>client,BufferJSON:{},initAuthCreds,Browsers:{windows:()=>['synthetic']},
    DisconnectReason:{loggedOut:401},proto:{Message:{AppStateSyncKeyData:{fromObject:value=>value}}}};
  const session=await createBaileysChannelSession(context,{loadBaileys:async()=>mod,schedule:(fn,ms)=>{const id=++serial;tasks.set(id,{fn,ms});return id;},cancel:id=>tasks.delete(id)});
  t.after(()=>session.close());
  await assert.rejects(session.groupContacts('groups',scope(device),{authorize:context.assertCurrent}),/group_contacts_unavailable/);
  assert.equal(reads,0);client.ev.emit('connection.update',{connection:'open'});await tick();
  assert.equal((await session.groupContacts('groups',scope(device),{authorize:context.assertCurrent})).groups.length,1);
  const pending=deferred();client.groupFetchAllParticipating=()=>pending.promise;
  const read=session.groupContacts('groups',scope(device),{authorize:async()=>{}});await tick();alive=false;
  pending.resolve({[GROUP]:metadata([])});await assert.rejects(read,/group_contacts_unavailable/);
  assert.deepEqual(effects,[]);
});

async function request(route,url,input,{method='POST',key='synthetic-key-'.repeat(4),raw}={}){
  const req=Readable.from([Buffer.from(raw??JSON.stringify(input))]);req.url=url;req.method=method;req.headers=key?{'x-orion-channel-key':key}:{};
  const output={};const res={writeHead:(status,headers)=>Object.assign(output,{status,headers}),end:body=>{output.body=JSON.parse(body);}};
  output.handled=await route(req,res);return output;
}
test('private routes accept only the scoped read actions and never dispatch lifecycle or message operations',async()=>{
  const device=snapshot(),id=randomUUID(),calls=[];
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    deviceGroupContacts:async(...args)=>{calls.push(args);return {...scope(device),groups:[]};},
    start:()=>assert.fail('No start'),send:()=>assert.fail('No send'),pair:()=>assert.fail('No pair')})});
  let value=await request(route,`/internal/channels/${id}/device/group-contacts/groups`,scope(device));
  assert.equal(value.status,200);assert.equal(value.headers['Cache-Control'],'no-store');
  assert.deepEqual(calls[0].slice(2),['groups',scope(device)]);
  value=await request(route,`/internal/channels/${id}/device/group-contacts/members`,{...scope(device),group_id:GROUP});
  assert.equal(value.status,200);assert.equal(calls[1][2],'members');
  assert.equal((await request(route,`/internal/channels/${id}/device/group-contacts/groups`,scope(device),{method:'GET'})).status,405);
  assert.equal((await request(route,`/internal/channels/${id}/device/group-contacts/groups`,scope(device),{key:null})).status,401);
  assert.equal((await request(route,`/internal/channels/${id}/device/group-contacts/groups?extra=1`,scope(device))).status,404);
  for(const input of [{...scope(device),unknown:true},{...scope(device),settings_revision:0}])
    assert.deepEqual((await request(route,`/internal/channels/${id}/device/group-contacts/groups`,input)).body,{error:'group_contacts_unavailable'});
  assert.equal((await request(route,`/internal/channels/${id}/device/group-contacts/groups`,{}, {raw:'x'.repeat(8193)})).status,409);
  assert.equal(calls.length,2);
});

test('private route errors never echo provider failures or instantiate a socket for a disabled transport',async()=>{
  const device=snapshot(),id=randomUUID(),url=`/internal/channels/${id}/device/group-contacts/groups`;
  const route=createChannelRoutes({env:{QR_CHANNELS_ENABLED:'1'},brokerFactory:()=>({}),managerFactory:()=>({
    deviceGroupContacts:async()=>{throw Error('private credentials '+PHONE+' '+GROUP);}})});
  const result=await request(route,url,scope(device));assert.equal(result.status,409);
  assert.deepEqual(result.body,{error:'group_contacts_unavailable'});
  const disabled=createChannelRoutes({env:{},managerFactory:()=>assert.fail('Disabled route must not construct a manager')});
  assert.equal((await request(disabled,url,scope(device))).status,423);
});
