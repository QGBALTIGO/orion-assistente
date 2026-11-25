import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash,randomUUID} from 'node:crypto';
import {createAffiliateOfferSocket,validAffiliateRequest} from '../src/affiliate-offers.mjs';

function fixture(){
  const scope={device_id:randomUUID(),generation:randomUUID(),settings_revision:3};
  const address='120363400001234567@g.us',channel='120363400001234567@newsletter';
  const user={id:'5567999999999:2@s.whatsapp.net',lid:'998877665544@lid'};
  const metadata={id:address,subject:'Ofertas de café',participants:[{id:user.id,admin:'admin'}]};
  const calls=[];let connected=true,ack=true,admin=true;
  const client={user,
    async groupFetchAllParticipating(){calls.push('groups');return {[address]:metadata,'120363499999999999@g.us':
      {...metadata,id:'120363499999999999@g.us',participants:[{id:'5511999999999@s.whatsapp.net',admin:'admin'}]}};},
    async groupMetadata(id){calls.push('group');assert.equal(id,address);return {...metadata,participants:admin?metadata.participants:[]};},
    async newsletterMetadata(kind,id){calls.push('newsletter:'+kind);return {id:channel,
      state:{type:'ACTIVE'},viewer_metadata:{role:admin&&kind==='jid'?'ADMIN':'SUBSCRIBER'},thread_metadata:{name:{text:'Promoção'}}};},
    async waitForMessage(id){calls.push('wait');return ack?{tag:'ack',attrs:{id,from:f.target,server_id:'33'}}:null;},
    async relayMessage(jid,message,options){calls.push('relay');assert.equal(jid,f.target);assert.equal(options.messageId,'OFFER-001');},
    waUploadToServer:async()=>assert.fail('The fake SDK owns this test upload')};
  const sdk={generateMessageIDV2:()=> 'OFFER-001',prepareWAMessageMedia:async(content)=>{
    calls.push('upload');return {imageMessage:{directPath:'/synthetic-image',fileSha256:createHash('sha256').update(content.image).digest()}};}};
  const f={scope,address,channel,user,metadata,client,calls,sdk,target:address,
    setConnected:value=>connected=value,setAdmin:value=>admin=value,setAck:value=>ack=value};
  f.run=createAffiliateOfferSocket({client,sdk,isCurrent:()=>connected,isConnected:()=>connected,
    deviceSnapshot:()=>({id:scope.device_id,generation:scope.generation,settings_revision:scope.settings_revision})});
  f.input=(extra={})=>({...scope,mode:'whatsapp_group',address,operation_id:randomUUID(),
    text:'Oferta de café\nhttps://s.shopee.com.br/example',link:'https://s.shopee.com.br/example',media:null,...extra});
  f.guard={authorize:async()=>{calls.push('authorize');},beforeAttempt:async()=>{calls.push('attempt');}};
  return f;
}

test('only groups administered by the connected account are selectable',async()=>{
  const f=fixture(),result=await f.run('groups',f.scope,f.guard);
  assert.deepEqual(result.groups,[{address:f.address,title:'Ofertas de café',role:'ADMIN'}]);
  assert.equal(f.calls.includes('relay'),false);
});

test('newsletter permission is read from its canonical metadata and stays scoped',async()=>{
  const f=fixture(),input={...f.scope,mode:'whatsapp_channel',invite:'A'.repeat(24)};
  const result=await f.run('verify',input,f.guard);
  assert.equal(result.address,f.channel);assert.equal(result.title,'Promoção');assert.equal(result.role,'ADMIN');
  assert.equal(f.calls.filter(v=>v.startsWith('newsletter')).length,2);
  f.setAdmin(false);await assert.rejects(f.run('verify',input,f.guard));
  assert.equal(f.calls.includes('relay'),false);
});

test('the selected device generation and settings must remain current',async()=>{
  for(const key of ['device_id','generation','settings_revision']){
    const f=fixture(),input={...f.scope,[key]:key==='settings_revision'?4:randomUUID()};
    await assert.rejects(f.run('groups',input,f.guard));assert.deepEqual(f.calls,[]);
  }
});

test('revocation during metadata retrieval prevents the attempt boundary',async()=>{
  const f=fixture();f.client.groupMetadata=async()=>{f.setConnected(false);return f.metadata;};
  await assert.rejects(f.run('send',f.input(),f.guard));assert.equal(f.calls.includes('attempt'),false);
});

test('text relay requires the exact destination server acknowledgment',async()=>{
  const f=fixture();let result=await f.run('send',f.input(),f.guard);
  assert.equal(result.state,'sent');assert.equal(result.accepted,true);
  assert.ok(f.calls.indexOf('attempt')<f.calls.indexOf('relay'));
  f.setAck(false);result=await f.run('send',f.input(),f.guard);
  assert.equal(result.state,'unknown');assert.equal(result.accepted,false);
});

test('media rechecks administration after upload and before relay',async()=>{
  const f=fixture(),bytes=Buffer.concat([Buffer.from('ffd8ff','hex'),Buffer.alloc(100)]);
  const result=await f.run('send',f.input({media:bytes.toString('base64')}),f.guard);
  assert.equal(result.state,'sent');assert.equal(f.calls.filter(x=>x==='group').length,2);
  assert.ok(f.calls.indexOf('upload')<f.calls.lastIndexOf('group'));
  f.sdk.prepareWAMessageMedia=async(content)=>{f.setAdmin(false);return {imageMessage:{directPath:'/synthetic',
    fileSha256:createHash('sha256').update(content.image).digest()}};};
  f.calls.length=0;
  await assert.rejects(f.run('send',f.input({media:bytes.toString('base64')}),f.guard));
  assert.equal(f.calls.includes('attempt'),false);
});

test('unknown fields, credentials in URLs and unbounded media are refused',()=>{
  const f=fixture();assert.equal(validAffiliateRequest('send',f.input()),true);
  for(const edit of [{extra:true},{media:'x'.repeat(1400001)},{media:'invalid-base64'},
    {text:'Missing own link'},{link:'https://user:password@shop.example/a',text:'https://user:password@shop.example/a'},
    {link:'file:///etc/passwd',text:'file:///etc/passwd'},{operation_id:'not-an-id'},
    {text:'a'.repeat(4097)+'https://s.shopee.com.br/example'}]){
    assert.equal(validAffiliateRequest('send',f.input(edit)),false);
  }
});

test('an expired read cannot send after the provider eventually responds',async()=>{
  const f=fixture();let release;
  f.client.groupMetadata=()=>new Promise(resolve=>release=()=>resolve(f.metadata));
  const run=createAffiliateOfferSocket({client:f.client,sdk:f.sdk,timeoutMs:5,
    isCurrent:()=>true,isConnected:()=>true,deviceSnapshot:()=>({id:f.scope.device_id,generation:f.scope.generation,settings_revision:3})});
  // Keep the test process alive while the unreferenced deadline elapses.
  const live=setTimeout(()=>{},50);
  await assert.rejects(run('send',f.input(),f.guard));release();
  await new Promise(resolve=>setImmediate(resolve));clearTimeout(live);
  assert.equal(f.calls.includes('attempt'),false);assert.equal(f.calls.includes('relay'),false);
});
