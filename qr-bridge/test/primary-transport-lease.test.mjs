/** Real manager + primary adapter + Signal persistence; no network or account. */
import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {BufferJSON,initAuthCreds,proto} from '@whiskeysockets/baileys';
import {createLegacyCentral} from '../src/legacy-central.mjs';
import {createChannelManager} from '../src/channel-manager.mjs';
import {useDurableAuthState} from '../src/durable-auth.mjs';
import {fenceSignalRepository} from '../src/signal-fence.mjs';
import {encryptedChannelStore} from '../src/channel-storage.mjs';

function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-primary-lease-'));
  const id=randomUUID(),tenant=randomUUID(),key='synthetic-primary-transport-secret-'.repeat(2),phone='5567999999901';
  let now=0,serial=0,row={owner:null,fence:0,expires:0};const timers=new Map(),instances=[];
  const broker={async lease(channel,secret,operation,owner,fence){
    assert.equal(channel,id);assert.equal(secret,key);
    if(operation==='claim'){
      if(row.expires>now&&row.owner!==owner)throw Error('busy');
      if(row.expires<=now)row={owner,fence:row.fence+1,expires:now+30000};
    }else if(operation!=='inspect'){
      if(row.owner!==owner||row.fence!==fence||row.expires<=now)throw Error('lost');
      if(operation==='renew')row.expires=now+30000;
    }
    return {channel_id:id,tenant_id:tenant,provider:'baileys',owner:row.owner,fence:row.fence,expires_in_ms:row.expires-now,
      legacy_transport:true,allowed_senders:[phone],assistant_enabled:false};
  }};
  function instance(){
    let connected=false,leaseLosses=0,manager;
    const central=createLegacyCentral({env:{QR_LEGACY_CENTRAL_ENABLED:'1',QR_CHANNEL_BROKER_URL:'http://api.railway.internal',QR_BRIDGE_TOKEN:key},
      fetchImpl:async()=>({ok:true,json:async()=>({legacy_transport:true,channel_id:id,tenant_id:tenant,session_digest:'a'.repeat(64),allowed_senders:[phone]})}),
      status:()=>connected?'connected':'connecting',sessionDigest:()=>connected?'a'.repeat(64):null,
      start:(...args)=>manager.start(...args),send:(...args)=>manager.send(...args),rawSend:()=>assert.fail('No customer send'),
      onTransportLeaseLost:()=>leaseLosses++});
    manager=createChannelManager({broker,root:path.join(root,'channels'),createSession:context=>central.attach(context),clock:()=>now,
      schedule:(fn,ms)=>{const timer=++serial;timers.set(timer,{fn,at:now+ms});return timer;},cancel:timer=>timers.delete(timer)});
    const value={central,manager,connected:()=>{connected=true;},get leaseLosses(){return leaseLosses;}};instances.push(value);return value;
  }
  t.after(()=>{for(const item of instances)item.manager.close();const target=path.resolve(root);
    assert.ok(target.startsWith(path.resolve(os.tmpdir())+path.sep+'orion-primary-lease-'));fs.rmSync(target,{recursive:true,force:true});});
  return {root,key,instance,advance(ms){now+=ms;},expire(){for(const [timer,item] of [...timers])if(item.at<=now){timers.delete(timer);item.fn();}}};
}
test('SQL ownership is reserved before the physical primary session connects or reads Signal state',async t=>{
  const f=fixture(t),a=f.instance();assert.equal(a.central.transportCurrent(),false);
  await assert.rejects(a.central.assertTransportCurrent(),/lease_unavailable/);
  const result=await a.central.resume();assert.equal(result.owned,true);assert.equal(result.ready,false);assert.equal(result.connected,false);
  await a.central.assertTransportCurrent();a.connected();assert.equal(a.central.health().ready,true);
});
test('a second process cannot initialize the shared primary while its SQL owner is live',async t=>{
  const f=fixture(t),a=f.instance(),b=f.instance();await a.central.resume();
  await assert.rejects(b.central.resume(),/busy/);assert.equal(b.central.transportCurrent(),false);assert.equal(a.central.transportCurrent(),true);
  await assert.rejects(b.central.assertTransportCurrent(),/lease_unavailable/);assert.equal(a.leaseLosses,0);
});
test('takeover fences the old primary auth and SDK work while preserving its persisted identity',async t=>{
  const f=fixture(t),a=f.instance(),b=f.instance();await a.central.resume();
  const directory=path.join(f.root,'auth'),sdk={BufferJSON,initAuthCreds,proto};
  const auth=useDurableAuthState(directory,{secret:f.key,sdk,isCurrent:()=>a.central.transportCurrent()});
  await auth.state.keys.set({session:{kept:Buffer.from('synthetic ratchet')}});
  const identity=Buffer.from(auth.state.creds.signedIdentityKey.public),before=fs.readFileSync(path.join(directory,'creds.json'));
  let cryptoCalls=0;const repository=fenceSignalRepository(()=>({async encryptMessage(){cryptoCalls++;}}),()=>a.central.transportCurrent())();
  f.advance(31000);await b.central.resume();assert.equal(a.central.transportCurrent(),false);assert.equal(b.central.transportCurrent(),true);
  await assert.rejects(repository.encryptMessage({}),/owner_superseded/);assert.equal(cryptoCalls,0);
  await assert.rejects(auth.state.keys.set({session:{stale:Buffer.from('not persisted')}}),/auth_storage/);
  assert.deepEqual(fs.readFileSync(path.join(directory,'creds.json')),before);
  const restored=useDurableAuthState(directory,{secret:f.key,sdk,isCurrent:()=>b.central.transportCurrent()});
  assert.deepEqual(restored.state.creds.signedIdentityKey.public,identity);
  assert.deepEqual((await restored.state.keys.get('session',['kept'])).kept,Buffer.from('synthetic ratchet'));
  f.expire();assert.equal(a.leaseLosses,1,'Expired owner closes its physical transport once');
});
test('managed auth loss during fsync cannot rename a late credential or ratchet over the current state',t=>{
  const f=fixture(t),directory=path.join(f.root,'managed-auth');let alive=true;
  const store=encryptedChannelStore({directory,secret:f.key,isCurrent:()=>alive});store.set('creds',{synthetic:'original identity'});
  const file=fs.readdirSync(directory)[0],before=fs.readFileSync(path.join(directory,file)),sync=fs.fsyncSync;
  fs.fsyncSync=fd=>{sync(fd);alive=false;};
  try{assert.throws(()=>store.set('creds',{synthetic:'late stale identity'}),/owner_superseded/);}
  finally{fs.fsyncSync=sync;}
  assert.deepEqual(fs.readFileSync(path.join(directory,file)),before);assert.equal(fs.readdirSync(directory).some(name=>name.endsWith('.tmp')),false);
  assert.throws(()=>store.get('creds'),/owner_superseded/);
  assert.deepEqual(encryptedChannelStore({directory,secret:f.key}).get('creds'),{synthetic:'original identity'});
});
