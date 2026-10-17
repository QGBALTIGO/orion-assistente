import test from 'node:test';
import assert from 'node:assert/strict';
import {resolvePilotRecipient,isVerifiedPilotLid,makePilotDeviceCache} from '../src/pilot-recipient.mjs';
import {sendStoredText} from '../src/reliable-send.mjs';
import {makeMessageRetryStore} from '../src/message-retry-store.mjs';

const phone='5511999999999',otherPhone='5511888888888',ownPhone='5511777777777';
const lid='100000000000002@lid',ownLid='100000000000001:4@lid';
const match=()=>({jid:phone+'@s.whatsapp.net',exists:true,lid});
function setup(results=[match()]){
  let lookups=0;
  const socket={user:{id:ownPhone+':4@s.whatsapp.net',lid:ownLid},
    async onWhatsApp(value){assert.equal(this,socket);assert.equal(value,phone);lookups++;return results;},
    async getUSyncDevices(jids,cache,excludeZero){assert.deepEqual(jids,[this.user.id,lid]);
      assert.equal(cache,false);assert.equal(excludeZero,true);return [];}};
  return {socket,get lookups(){return lookups;},options:{phone,pilotPhone:phone,enabled:true,socket,deviceCache:makePilotDeviceCache()}};
}

test('only the opted-in pilot performs a contemporary identity lookup',async()=>{
  const fixture=setup();
  assert.equal((await resolvePilotRecipient({...fixture.options,enabled:false})).jid,phone+'@s.whatsapp.net');
  assert.equal((await resolvePilotRecipient({...fixture.options,pilotPhone:otherPhone})).kind,'pn');
  assert.equal(fixture.lookups,0);
  const proof=await resolvePilotRecipient(fixture.options);
  assert.equal(fixture.lookups,1);assert.deepEqual(proof,{jid:lid,phone,kind:'lid'});
  assert.equal(Object.isFrozen(proof),true);assert.equal(isVerifiedPilotLid(proof,lid),true);
  assert.equal(isVerifiedPilotLid({...proof},lid),false);
  assert.equal(isVerifiedPilotLid(proof,'100000000000003@lid'),false);
});

test('invalid or ambiguous identity results stop before building, caching or relaying',async()=>{
  const rows=[null,{},[],[match(),match()],[{...match(),jid:otherPhone+'@s.whatsapp.net'}],
    [{...match(),exists:'true'}],[{...match(),exists:false}],[{...match(),lid:undefined}],
    [{...match(),lid:'100000000000002:0@lid'}],[{...match(),lid:'100000000000002@g.us'}],
    [{...match(),lid:'100000000000002@lid.evil'}],[{...match(),lid:'100000000000001@lid'}],
    [{...match(),lid:ownPhone+'@lid'}]];
  for(const result of rows){
    const fixture=setup(result);let builds=0,caches=0,relays=0;
    await assert.rejects(async()=>{
      const proof=await resolvePilotRecipient(fixture.options);
      await sendStoredText({jid:proof.jid,recipientProof:proof,text:'Synthetic test',
        makeMessage:async()=>{builds++;return {};},store:{save(){caches++;return true;}},
        relay:async()=>{relays++;}});
    },/Nenhuma mensagem foi enviada/);
    assert.deepEqual([builds,caches,relays],[0,0,0]);
  }
});

test('own account, missing device identity, failed lookup or changed socket cannot authorize LID',async()=>{
  for(const user of [{id:phone+':4@s.whatsapp.net',lid:ownLid},
    {id:ownPhone+':4@s.whatsapp.net',lid:'100000000000001@lid'},
    {id:ownPhone+':4@s.whatsapp.net'}, {id:'malformed',lid:ownLid}]){
    const fixture=setup();fixture.socket.user=user;
    await assert.rejects(resolvePilotRecipient(fixture.options),/Nenhuma mensagem foi enviada/);
    assert.equal(fixture.lookups,0);
  }
  const rejected=setup();rejected.socket.onWhatsApp=async()=>{throw Error('private lookup failure');};
  await assert.rejects(resolvePilotRecipient(rejected.options),error=>!error.message.includes('private')&&error.message.includes('Nenhuma mensagem'));
  const changed=setup();let current=true;
  changed.socket.onWhatsApp=async()=>{current=false;return [match()];};
  await assert.rejects(resolvePilotRecipient({...changed.options,isCurrent:()=>current}),/Nenhuma mensagem foi enviada/);
});

test('LID cannot bypass reliable-send validation with a forged proof',async()=>{
  let calls=0;
  const options={jid:lid,text:'Synthetic test',makeMessage:async()=>{calls++;},
    relay:async()=>{calls++;},store:{save(){calls++;return true;}}};
  await assert.rejects(sendStoredText(options),/Invalid recipient JID/);
  await assert.rejects(sendStoredText({...options,recipientProof:{jid:lid,phone,kind:'lid'}}),/Invalid recipient JID/);
  assert.equal(calls,0);
});

for(const ambiguous of [false,true])test('verified LID caches before a single relay; ambiguous='+ambiguous,async()=>{
  const fixture=setup(),proof=await resolvePilotRecipient(fixture.options);
  const message={conversation:'Synthetic LID test'},key={id:'SYNTHETIC_LID_ID',remoteJid:lid};
  const saved=makeMessageRetryStore(),events=[];
  let relays=0;
  const options={jid:proof.jid,recipientProof:proof,text:'Synthetic LID test',freshDevices:true,
    makeMessage:async recipient=>{assert.equal(recipient,lid);events.push('build');return {key,message};},
    store:{save(receivedKey,proto){assert.equal(receivedKey,key);assert.equal(proto,message);events.push('cache');return saved.save(receivedKey,proto);}},
    relay:async(recipient,proto,opts)=>{relays++;events.push('relay');assert.equal(recipient,lid);
      assert.equal(proto,message);assert.equal(saved.get({id:opts.messageId,remoteJid:lid}),message);
      assert.equal(saved.get({id:opts.messageId,remoteJid:phone+'@s.whatsapp.net'}),message);assert.equal(opts.useUserDevicesCache,true);
      if(ambiguous)throw Error('ambiguous transport result');}};
  if(ambiguous)await assert.rejects(sendStoredText(options),/ambiguous transport result/);
  else assert.deepEqual(await sendStoredText(options),{key,message});
  assert.equal(relays,1);assert.deepEqual(events,['build','cache','relay']);assert.equal(saved.size(),1);
});

test('pinned relay receives only a checked fresh companion list with known namespaces',async()=>{
  const fixture=setup();
  fixture.socket.getUSyncDevices=async()=>[{user:ownPhone,device:2},{user:lid.split('@')[0],device:3}];
  assert.equal((await resolvePilotRecipient(fixture.options)).jid,lid);
  for(const devices of [null,{},[{user:'100000000000001',device:2}],
    [{user:phone,device:2}],[{user:ownPhone,device:4}],
    [{user:ownPhone,device:0}],[{user:ownPhone,device:'2'}]]){
    fixture.socket.getUSyncDevices=async()=>devices;
    await assert.rejects(resolvePilotRecipient(fixture.options),/Nenhuma mensagem foi enviada/);
  }
});

test('a socket change while generating stops before caching or relay',async()=>{
  const fixture=setup(),proof=await resolvePilotRecipient(fixture.options);
  let current=true,cached=0,relayed=0;
  await assert.rejects(sendStoredText({jid:lid,recipientProof:proof,text:'Synthetic',isCurrent:()=>current,
    makeMessage:async()=>{current=false;return {key:{id:'RACE'},message:{conversation:'Synthetic'}};},
    store:{save(){cached++;return true;}},relay:async()=>{relayed++;}}),/Nenhuma mensagem foi enviada/);
  assert.deepEqual([cached,relayed],[0,0]);
});

for(const ambiguous of [false,true])test('an empty checked snapshot overrides stale cache only during its relay; ambiguous='+ambiguous,async()=>{
  const recipientUser=lid.split('@')[0];
  const staleOwn=[{user:ownPhone,device:7}],staleRecipient=[{user:recipientUser,device:8}];
  const base=new Map([[ownPhone,staleOwn],[recipientUser,staleRecipient]]);
  const fixture=setup(),cache=makePilotDeviceCache(base);
  fixture.options.deviceCache=cache;
  const proof=await resolvePilotRecipient(fixture.options);
  assert.equal(cache.get(ownPhone),staleOwn);
  let uncachedQueries=0,relays=0;
  const work=sendStoredText({jid:lid,recipientProof:proof,text:'Synthetic empty snapshot',freshDevices:true,
    makeMessage:async()=>({key:{id:'EMPTY_'+ambiguous,remoteJid:lid},message:{conversation:'Synthetic'}}),
    store:{save:()=>true},relay:async(_jid,_message,options)=>{
      relays++;assert.equal(options.useUserDevicesCache,true);
      for(const user of [ownPhone,recipientUser]){
        // Match the pinned library's cache-hit branch: an empty array must be
        // present and truthy, otherwise it performs a second, unchecked query.
        const devices=cache.get(user);
        if(!devices)uncachedQueries++;
        assert.deepEqual(devices,[]);
        assert.equal(Object.isFrozen(devices),true);
      }
      await Promise.resolve();
      assert.deepEqual(cache.get(ownPhone),[]);
      if(ambiguous)throw Error('ambiguous snapshot relay');
    }});
  if(ambiguous)await assert.rejects(work,/ambiguous snapshot relay/);
  else await work;
  assert.equal(relays,1);assert.equal(uncachedQueries,0);
  assert.equal(cache.get(ownPhone),staleOwn);
  assert.equal(cache.get(recipientUser),staleRecipient);
});

test('concurrent verified relays and a normal PN relay keep their device contexts isolated',async()=>{
  function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
  const recipientUser=lid.split('@')[0],baseline=[{user:ownPhone,device:9}];
  const cache=makePilotDeviceCache(new Map([[ownPhone,baseline]]));
  const fixture=setup();fixture.options.deviceCache=cache;
  let companion=2;
  fixture.socket.getUSyncDevices=async()=>[{user:ownPhone,device:companion}];
  const firstProof=await resolvePilotRecipient(fixture.options);
  companion=3;
  const secondProof=await resolvePilotRecipient(fixture.options);
  const releaseFirst=deferred(),releaseSecond=deferred(),bothEntered=deferred();
  let entered=0;
  function run(proof,device,release){
    return sendStoredText({jid:lid,recipientProof:proof,text:'Synthetic concurrent '+device,
      makeMessage:async()=>({key:{id:'PARALLEL_'+device,remoteJid:lid},message:{conversation:'Synthetic'}}),
      store:{save:()=>true},relay:async()=>{
        assert.deepEqual(cache.get(ownPhone),[{user:ownPhone,device}]);
        assert.deepEqual(cache.get(recipientUser),[]);
        if(++entered===2)bothEntered.resolve();
        await release.promise;
        assert.deepEqual(cache.get(ownPhone),[{user:ownPhone,device}]);
        assert.deepEqual(cache.get(recipientUser),[]);
      }});
  }
  const first=run(firstProof,2,releaseFirst),second=run(secondProof,3,releaseSecond);
  try{
    await bothEntered.promise;
    assert.equal(cache.get(ownPhone),baseline);
    await sendStoredText({jid:otherPhone+'@s.whatsapp.net',text:'Synthetic normal PN',
      makeMessage:async()=>({key:{id:'NORMAL_PN',remoteJid:otherPhone+'@s.whatsapp.net'},message:{conversation:'Synthetic'}}),
      store:{save:()=>true},relay:async()=>{
        assert.equal(cache.get(ownPhone),baseline);
        await Promise.resolve();
        assert.equal(cache.get(ownPhone),baseline);
      }});
  }finally{
    releaseFirst.resolve();releaseSecond.resolve();
    await Promise.all([first,second]);
  }
  assert.equal(entered,2);assert.equal(cache.get(ownPhone),baseline);
});

test('a verified snapshot copies and freezes device records rather than retaining mutable lookup results',async()=>{
  const recipientUser=lid.split('@')[0],fixture=setup();
  const devices=[{user:ownPhone,device:2},{user:recipientUser,device:3}];
  fixture.socket.getUSyncDevices=async()=>devices;
  const proof=await resolvePilotRecipient(fixture.options);
  devices[0].device=99;devices[1].user='unrelated';devices.push({user:ownPhone,device:8});
  await sendStoredText({jid:lid,recipientProof:proof,text:'Synthetic immutable snapshot',
    makeMessage:async()=>({key:{id:'IMMUTABLE',remoteJid:lid},message:{conversation:'Synthetic'}}),
    store:{save:()=>true},relay:async()=>{
      const own=fixture.options.deviceCache.get(ownPhone),recipient=fixture.options.deviceCache.get(recipientUser);
      assert.deepEqual(own,[{user:ownPhone,device:2}]);
      assert.deepEqual(recipient,[{user:recipientUser,device:3}]);
      assert.equal(Object.isFrozen(own),true);assert.equal(Object.isFrozen(own[0]),true);
      assert.equal(Object.isFrozen(recipient),true);assert.equal(Object.isFrozen(recipient[0]),true);
      assert.throws(()=>{own[0].device=5;},TypeError);
    }});
});
