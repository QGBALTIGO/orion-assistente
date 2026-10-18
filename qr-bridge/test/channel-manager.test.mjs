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

const phone='5567999999901',other='5567999999902';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
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
    async interaction(id,key,lease,payload){interactions.push({id,key,lease,payload});return {recorded:true};},
    async forward(id,key,lease,payload){forwards.push({id,key,lease,payload});return {answer:null};}
  };
  const createSession=async ctx=>{const session={ctx,closed:false,sends:[],close(){this.closed=true;},status:()=> 'connected',qr:()=>null,
    async send(to,text){this.sends.push({to,text});return {id:randomUUID()};}};made.push(session);return session;};
  function manager(extra={}){const value=createChannelManager({broker,createSession,root,clock,schedule,cancel,...extra});t.after(()=>value.close());return value;}
  return {root,rows,add,broker,manager,made,forwards,interactions,tasks,
    async advance(ms){now+=ms;for(const [id,item] of [...tasks])if(item.at<=now){tasks.delete(id);item.fn();}await flush();},
    setTime:value=>{now=value;}};
}

test('two channels bind their actual tenant, directory, queues and credential independently',async t=>{
  const f=fixture(t),a=f.add(),b=f.add(),m=f.manager();
  await Promise.all([m.start(a.id,a.key),m.start(b.id,b.key)]);
  assert.equal(m.size,2);assert.notEqual(f.made[0].ctx.directory,f.made[1].ctx.directory);
  const contexts=new Map(f.made.map(s=>[s.ctx.channelId,s.ctx]));
  assert.equal(contexts.get(a.id).tenantId,a.tenant);assert.equal(contexts.get(b.id).tenantId,b.tenant);
  await contexts.get(a.id).onMessage({phone,message_id:'synthetic-1',text:'same contact',tenant_id:b.tenant});
  assert.equal(f.forwards[0].id,a.id);assert.deepEqual(Object.keys(f.forwards[0].payload).sort(),['message_id','phone','text']);
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
  assert.deepEqual(operations,['inspect']);assert.equal(f.made.length,1);assert.equal(restarted.size,0);
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
