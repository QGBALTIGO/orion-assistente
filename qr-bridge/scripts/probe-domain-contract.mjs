// Manual, offline baseline: node qr-bridge/scripts/probe-domain-contract.mjs
// Requires the installed pinned package. No socket, account, or auth file is used.
// These assertions document conditional 6.7.24 defects, not a live root cause.
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import assert from 'node:assert/strict';

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
  async function relay(remote,{participant,usync}={}){
    const encryptedTo=[],wire=[];
    const keys={transaction:async work=>work(),get:async(_,ids)=>Object.fromEntries(ids.map(id=>[id,{}])),set:async()=>{}};
    const syntheticSocket={
      authState:{creds:{me:{id:ownPn+':4@s.whatsapp.net',lid:ownLid+':4@lid'}},keys},ev:{on(){}},
      signalRepository:{jidToSignalProtocolAddress:jid=>jid,encryptMessage:async({jid})=>{
        encryptedTo.push(jid);return {type:'msg',ciphertext:Buffer.from('synthetic')};
      }},
      sendNode:async node=>wire.push({to:node.attrs.to,recipient:node.attrs.recipient}),
      executeUSyncQuery:async()=>({list:usync?[{id:usync,devices:{deviceList:[{id:2,keyIndex:1}]}}]:[]}),
      query:async()=>{throw Error('Unexpected network query in offline probe');}
    };
    const socket=makeMessagesSocket({syntheticSocket,userDevicesCache:new Map(),logger:quiet,
      patchMessageBeforeSending:x=>x,options:{}});
    await socket.relayMessage(remote,{conversation:'SYNTHETIC_ONLY'},{messageId:'SYNTHETIC',
      useUserDevicesCache:false,...(participant?{participant:{jid:participant,count:1}}:{})});
    return {encryptedTo,wire};
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
  console.log(JSON.stringify({version:'6.7.24',offline:true,networkCalls:0,scenarios:4,
    pnPathConsistent:true,lidRecipientRetryConsistent:true,
    conditionalDefects:{pnUsyncLidDomainLost:true,ownLidRetryDomainMismatch:true},
    liveCauseProven:false}));
}
main().catch(error=>{console.error('Offline domain probe failed: '+error.message);process.exitCode=1;});
