// Manual, offline baseline: node qr-bridge/scripts/probe-domain-contract.mjs
// Requires the installed pinned package. No socket, account, or auth file is used.
// These assertions document conditional 6.7.24 defects, not a live root cause.
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';
import {makePilotDeviceCache,resolvePilotRecipient} from '../src/pilot-recipient.mjs';
import {sendStoredText} from '../src/reliable-send.mjs';

async function main(){
  const require=createRequire(import.meta.url);
  const entry=require.resolve('@whiskeysockets/baileys');
  const root=new URL('../',pathToFileURL(entry));
  assert.equal(JSON.parse(fs.readFileSync(new URL('package.json',root),'utf8')).version,'6.7.24');
  const sourceFile=new URL('lib/Socket/messages-send.js',root),resolve=createRequire(sourceFile);
  let replacements=0;
  const source=fs.readFileSync(sourceFile,'utf8').replace(/from '([^']+)'/g,(_,specifier)=>{
    let target;
    if(specifier==='./groups.js'){
      replacements++;target='data:text/javascript,export const makeGroupsSocket = config=>config.syntheticSocket';
    }else if(specifier==='./newsletter.js'){
      replacements++;target='data:text/javascript,export const makeNewsletterSocket = socket=>socket';
    }else target=pathToFileURL(resolve.resolve(specifier)).href;
    return `from '${target}'`;
  });
  assert.equal(replacements,2,'Both network factories must be replaced before executing the module');
  const {makeMessagesSocket}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const pn='5511000000001',lid='123456789012345',ownPn='5511000000002',ownLid='223456789012345';
  const quiet={trace(){},debug(){},info(){},warn(){},error(){},child(){return this}};
  async function relay(remote,{participant,usync,verified=false,stale=false}={}){
    const encryptedTo=[],wire=[],patched=[];
    let usyncQueries=0;
    const baseCache=new Map(stale?[[ownPn,[{user:ownPn,device:8}]],[lid,[{user:lid,device:9}]]]:[]);
    const deviceCache=makePilotDeviceCache(baseCache);
    const keys={transaction:async work=>work(),get:async(_,ids)=>Object.fromEntries(ids.map(id=>[id,{}])),set:async()=>{}};
    const syntheticSocket={
      authState:{creds:{me:{id:ownPn+':4@s.whatsapp.net',lid:ownLid+':4@lid'}},keys},ev:{on(){}},
      signalRepository:{jidToSignalProtocolAddress:jid=>jid,encryptMessage:async({jid})=>{
        encryptedTo.push(jid);return {type:'msg',ciphertext:Buffer.from('synthetic')};
      }},
      sendNode:async node=>wire.push({to:node.attrs.to,recipient:node.attrs.recipient}),
      executeUSyncQuery:async()=>{usyncQueries++;return {list:Array.isArray(usync)?usync:usync?[{id:usync,devices:{deviceList:[{id:2,keyIndex:1}]}}]:[]};},
      query:async()=>{throw Error('Unexpected network query in offline probe');}
    };
    const socket=makeMessagesSocket({syntheticSocket,userDevicesCache:deviceCache,logger:quiet,
      patchMessageBeforeSending:(message,jids)=>{patched.push({jids,ownCopy:!!message.deviceSentMessage});return message;},options:{}});
    if(verified){
      socket.user=syntheticSocket.authState.creds.me;
      socket.onWhatsApp=async()=>[{jid:pn+'@s.whatsapp.net',exists:true,lid:lid+'@lid'}];
      const proof=await resolvePilotRecipient({phone:pn,pilotPhone:pn,enabled:true,socket,deviceCache});
      await sendStoredText({jid:remote,recipientProof:proof,text:'SYNTHETIC_ONLY',store:{save:()=>true},
        makeMessage:async()=>({key:{id:'SYNTHETIC'},message:{conversation:'SYNTHETIC_ONLY'}}),
        relay:(jid,message,options)=>socket.relayMessage(jid,message,options)});
      assert.equal(usyncQueries,1,'Relay must not query a second unchecked device list');
      if(stale)assert.equal(deviceCache.get(ownPn)[0].device,8,'Scoped snapshot must not replace unrelated cache state');
    }else await socket.relayMessage(remote,{conversation:'SYNTHETIC_ONLY'},{messageId:'SYNTHETIC',
      useUserDevicesCache:false,...(participant?{participant:{jid:participant,count:1}}:{})});
    return {encryptedTo,wire,patched,usyncQueries};
  }
  const pnNormal=await relay(pn+'@s.whatsapp.net',{usync:pn+'@s.whatsapp.net'});
  assert.ok(pnNormal.encryptedTo.includes(pn+':2@s.whatsapp.net'));
  const mixed=await relay(pn+'@s.whatsapp.net',{usync:lid+'@lid'});
  assert.ok(mixed.encryptedTo.includes(lid+':2@s.whatsapp.net'));
  const ownRetry=await relay(pn+'@s.whatsapp.net',{participant:ownLid+':3@lid'});
  assert.deepEqual(ownRetry.encryptedTo,[ownLid+':3@s.whatsapp.net']);
  assert.equal(ownRetry.wire[0].to,ownLid+':3@lid');
  assert.equal(ownRetry.wire[0].recipient,undefined);
  const lidRetry=await relay(lid+':3@lid',{participant:lid+':3@lid'});
  assert.deepEqual(lidRetry.encryptedTo,[lid+':3@lid']);
  const lidInitial=await relay(lid+'@lid',{usync:[
    {id:ownPn+'@s.whatsapp.net',devices:{deviceList:[{id:0},{id:2,keyIndex:1},{id:4,keyIndex:1}]}},
    {id:lid+'@lid',devices:{deviceList:[{id:0},{id:3,keyIndex:1}]}}
  ]});
  assert.deepEqual([...lidInitial.encryptedTo].sort(),[
    ownLid+'@lid',ownLid+':2@lid',lid+'@lid',lid+':3@lid'].sort());
  assert.equal(lidInitial.wire[0].to,lid+'@lid');
  const ownCopy=lidInitial.patched.find(row=>row.ownCopy),recipientCopy=lidInitial.patched.find(row=>!row.ownCopy);
  assert.deepEqual([...ownCopy.jids].sort(),[ownLid+'@lid',ownLid+':2@lid'].sort());
  assert.deepEqual([...recipientCopy.jids].sort(),[lid+'@lid',lid+':3@lid'].sort());
  for(const stale of [false,true]){
    const verifiedEmpty=await relay(lid+'@lid',{verified:true,usync:[],stale});
    assert.deepEqual([...verifiedEmpty.encryptedTo].sort(),[ownLid+'@lid',lid+'@lid'].sort());
  }
  const verifiedCompanions=await relay(lid+'@lid',{verified:true,usync:[
    {id:ownPn+'@s.whatsapp.net',devices:{deviceList:[{id:2,keyIndex:1}]}},
    {id:lid+'@lid',devices:{deviceList:[{id:3,keyIndex:1}]}}
  ]});
  assert.deepEqual([...verifiedCompanions.encryptedTo].sort(),[
    ownLid+'@lid',ownLid+':2@lid',lid+'@lid',lid+':3@lid'].sort());
  console.log(JSON.stringify({version:'6.7.24',offline:true,networkCalls:0,scenarios:8,
    verifiedRelayConsumesExactSnapshotIncludingEmptyAndStaleCache:true,
    explicitLidInitialWithCheckedCompanionsConsistent:true,
    pnPathConsistent:true,lidRecipientRetryConsistent:true,
    conditionalDefects:{pnUsyncLidDomainLost:true,ownLidRetryDomainMismatch:true},
    liveCauseProven:false}));
}
main().catch(error=>{console.error('Offline domain probe failed: '+error.message);process.exitCode=1;});
