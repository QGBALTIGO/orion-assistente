import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {createChannelManager} from '../src/channel-manager.mjs';

function fixture(t){
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-affiliate-manager-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const id=randomUUID(),tenant=randomUUID(),key='synthetic-'+randomUUID(),device={id:randomUUID(),
    generation:randomUUID(),settings_revision:1,settings:{proxy:{enabled:false}},capabilities:{}};
  const row={channel_id:id,tenant_id:tenant,owner_account_id:randomUUID(),provider:'baileys',
    assistant_enabled:false,allowed_senders:[],device,fence:0};
  let expired=false,session,attempts=0,unknown=false;const made=[];
  const broker={lease:async(channel,secret,operation,owner,fence)=>{
    if(channel!==id||secret!==key||expired)throw Error('denied');
    if(operation==='claim')row.fence++;
    if(!['claim','inspect'].includes(operation)&&fence!==row.fence)throw Error('lost');
    return {...row,owner,expires_in_ms:30000};}};
  const createSession=async()=>{
    session={status:()=> 'connected',close(){},affiliate:async(action,input,{authorize,beforeAttempt})=>{
      await authorize();await beforeAttempt();attempts++;
      if(unknown)throw Error('Synthetic response lost after dispatch');
      return {state:'sent',accepted:true,provider_id:'synthetic-'+attempts};}};
    made.push(session);return session;
  };
  const manager=()=>{const m=createChannelManager({broker,createSession,root});t.after(()=>m.close());return m;};
  const input={device_id:device.id,generation:device.generation,settings_revision:1,mode:'whatsapp_group',
    address:'120363400001234567@g.us',operation_id:randomUUID(),text:'Oferta\nhttps://s.shopee.com.br/test',
    link:'https://s.shopee.com.br/test',media:null};
  return {id,key,row,input,manager,made,attempts:()=>attempts,expire:()=>expired=true,unknown:()=>unknown=true};
}

test('a repeated manual operation produces one attempt and survives a manager restart',async t=>{
  const f=fixture(t),m=f.manager();await m.start(f.id,f.key);
  const first=await m.affiliateOffer(f.id,f.key,'send',f.input);
  assert.equal(first.accepted,true);assert.equal(first.state,'sent');
  assert.deepEqual(await m.affiliateOffer(f.id,f.key,'send',f.input),first);
  assert.equal(f.attempts(),1);m.close();
  const again=f.manager();await again.start(f.id,f.key);
  assert.deepEqual(await again.affiliateOffer(f.id,f.key,'send',f.input),first);
  assert.equal(f.attempts(),1);
});

test('a lost result remains unknown and cannot be retried with the same operation',async t=>{
  const f=fixture(t),m=f.manager();await m.start(f.id,f.key);f.unknown();
  const result=await m.affiliateOffer(f.id,f.key,'send',f.input);
  assert.equal(result.state,'unknown');assert.equal(result.accepted,false);
  assert.deepEqual(await m.affiliateOffer(f.id,f.key,'send',f.input),result);
  assert.equal(f.attempts(),1);
});

test('an operation key cannot be reused for different content or destination',async t=>{
  const f=fixture(t),m=f.manager();await m.start(f.id,f.key);
  await m.affiliateOffer(f.id,f.key,'send',f.input);
  for(const update of [{text:f.input.text+' altered'},{address:'120363499999999999@g.us'}]){
    await assert.rejects(m.affiliateOffer(f.id,f.key,'send',{...f.input,...update}));
  }
  assert.equal(f.attempts(),1);
});

test('a foreign key, changed tenant, revoked lease or changed device cannot send',async t=>{
  for(const attack of ['key','tenant','lease','device']){
    const f=fixture(t),m=f.manager();await m.start(f.id,f.key);let key=f.key;
    if(attack==='key')key='foreign-'+randomUUID();
    if(attack==='tenant')f.row.tenant_id=randomUUID();
    if(attack==='lease')f.expire();
    if(attack==='device')f.row.device.generation=randomUUID();
    await assert.rejects(m.affiliateOffer(f.id,key,'send',f.input));assert.equal(f.attempts(),0);
  }
});
