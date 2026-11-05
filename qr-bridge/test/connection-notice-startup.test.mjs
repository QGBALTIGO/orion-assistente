/** Execute the real global connect/update handlers with a synthetic socket.
 * No provider, filesystem auth, API request or message is used by this harness.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {EventEmitter} from 'node:events';
import {normalizeFromJid} from '../src/core.mjs';
import {SMOKE_ID,APPOINTMENT_QA_ID} from '../src/consented-live-qa.mjs';
import {fenceSignalRepository} from '../src/signal-fence.mjs';
import {disconnectPolicy,sustainedRecovery} from '../src/connection-health.mjs';
import {createWaVersionPolicy} from '../src/wa-version-policy.mjs';
import {DEFAULT_CONNECTION_CONFIG,Curve,proto,initAuthCreds} from '@whiskeysockets/baileys';
import {classifyPairedAuth} from '../src/paired-auth-state.mjs';
import {makePairedAuthFixture} from './helpers/paired-auth-fixture.mjs';

const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const connectStart=source.indexOf('async function connect(){');
const connectSource=source.slice(connectStart,source.indexOf('async function pair(number){',connectStart));
const eventStart=source.indexOf('  client.ev.on("connection.update",update=>{');
const eventSource=source.slice(eventStart,source.indexOf('\n}\nasync function connect(){',eventStart));
assert.ok(connectStart>0&&eventStart>0&&eventSource.endsWith('  });'));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('global open preserves provider PN and never fabricates a phone from a LID',async()=>{
  const h=harness({registered:true});await h.connect();
  h.sockets[0].user.id='123456789:8@lid';h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();
  assert.equal(h.context.pairedNumber,'');assert.equal(h.notices.length,0);
  await h.connect();h.sockets[1].user.id='5567999999901:8@s.whatsapp.net';
  h.sockets[1].ev.emit('connection.update',{connection:'open'});await flush();assert.equal(h.context.pairedNumber,'5567999999901');
  assert.equal(h.notices.length,0);
});

function harness(options={}){
  const {registered=true}=options,flag=Object.hasOwn(options,'flag')?options.flag:'1';
  const sockets=[],notices=[],timers=new Map();let serial=0;
  const restored=registered?makePairedAuthFixture().creds:initAuthCreds();restored.registered=registered;
  const auth={creds:restored,keys:{async set(){}}};
  const context=vm.createContext({
    SMOKE_ID,APPOINTMENT_QA_ID,stopPrimaryIngress(){},replayAssistantIngress:async()=>{},
    ensureAssistantIngress:()=>({pending:()=>[]}),ingressFault:null,
    generation:0,socket:null,timer:null,attempts:0,sessionRecoveryAttempts:0,stopping:false,status:'starting',authDir:'/synthetic/preserved-auth',newLinkNoticeAuthDir:null,
    legacyCentral:{configured:()=>false,transportCurrent:()=>true,assertTransportCurrent:async()=>{}},
    primaryHealth:{connecting(){},connected(){},watch(){},disconnected(){},protocolVersion(){},snapshot:()=>({})},
    contactNames:{bindIdentity(){}},pauseContactNames(){},namesStore:{bindIdentity(){}},namesSync:{start(){}},
    connectedStore:{setIdentity(){}},refreshConnectedProfile:async()=>{},
    qr:'',qrAt:0,pairingCode:'',pairAt:0,connectStartedAt:0,pairingRequestedForGeneration:-1,
    pendingNotificationsReceived:false,inboundSinceConnect:false,everConnected:false,connectedSince:0,
    pairedNumber:'',lastError:null,lastDisconnectCode:null,notifiedGeneration:-1,
    clearTimeout:id=>timers.delete(id),setTimeout:(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id;},
    useDurableAuthState:()=>({state:auth,saveCreds(){}}),
    KEY:'synthetic-only-bridge-secret-1234567890',authBackup:{state:'verified_existing'},authBackupDirectory:'/synthetic/preserved-auth',
    BufferJSON:{},proto,Curve,classifyPairedAuth,initAuthCreds,makeBoundedCache:()=>({}),
    process:{env:{QR_WA_VERSION_AUTO:'0',...(flag===undefined?{}:{ORION_NOTIFY_SELF_ON_LINK:flag})}},
    transport:{record(){}},NodeCache:class {},makePilotDeviceCache:()=>({}),socketDeviceCaches:new WeakMap(),
    makeCacheableSignalKeyStore:value=>value,logger:{},DEFAULT_CONNECTION_CONFIG:{version:DEFAULT_CONNECTION_CONFIG.version,makeSignalRepository(){}},
    selectWaWebVersion:createWaVersionPolicy().select,fetchLatestWaWebVersion:async()=>({isLatest:false,version:DEFAULT_CONNECTION_CONFIG.version}),
    observeSignalRepository:fn=>fn,fenceSignalRepository,disconnectPolicy:(code,attempts,options)=>disconnectPolicy(code,attempts,{...options,random:()=>0.5}),sustainedRecovery,encryptionTargetDetails:()=>({}),retryStore:{get(){}},retryDiagnostics:{cacheHits:0,cacheMisses:0},
    Browsers:{windows:()=>['synthetic']},msgRetryCounterCache:{},
    makeWASocket:()=>{const socket={ev:new EventEmitter(),user:{id:'5567999999901@s.whatsapp.net'}};sockets.push(socket);return socket;},
    normalizeFromJid,Date,console:{info(){},warn(){}},saveQueue:Promise.resolve(),
    sendConnectionNoticeOnce:async args=>{assert.equal(args.isCurrent(),true);notices.push(args);return {state:'sent'};},
    DisconnectReason:{loggedOut:401,restartRequired:515},reconnectPlan:()=>({delayMs:0})
  });
  context.installListeners=(client,mine,_saveCreds,notifyNewLink)=>{
    Object.assign(context,{__noticeClient:client,__noticeMine:mine,__noticeFresh:notifyNewLink});
    vm.runInContext('(function(client,mine,notifyNewLink){'+eventSource+'})(__noticeClient,__noticeMine,__noticeFresh);',context);
  };
  vm.runInContext(connectSource,context);
  return {context,auth,sockets,notices,timers,
    connect:()=>context.connect(),
    async runTimers(ms){for(const [id,item] of [...timers])if(item.ms===ms){timers.delete(id);await item.fn();}await flush();await flush();}};
}

for(const flag of [undefined,'0','1'])test(`global startup with registered auth never calls the notice helper with flag ${flag??'default'} and a missing receipt`,async()=>{
  const h=harness({registered:true,flag});await h.connect();
  h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();
  assert.equal(h.timers.size,0);assert.equal(h.notices.length,0);assert.equal(h.context.newLinkNoticeAuthDir,null);
  assert.equal(h.auth.creds.registered,true);
});

test('a restored real SDK QR account with registered=false and no LID never re-arms the primary notice',async()=>{
  const h=harness({registered:true,flag:'1'});h.auth.creds=makePairedAuthFixture().creds;
  assert.equal(h.auth.creds.registered,false);assert.equal(h.auth.creds.me.lid,undefined);
  await h.connect();h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
  assert.equal(h.context.newLinkNoticeAuthDir,null);assert.equal(h.notices.length,0);assert.equal(h.timers.size,0);
});

test('provisional or incomplete primary auth never invents a new-link notice intent',async()=>{
  for(const creds of [{...initAuthCreds(),registered:true},{...initAuthCreds(),me:{id:'5567999999901@s.whatsapp.net',name:'~'},pairingCode:'SYNTHETIC'}]){
    const h=harness({registered:false,flag:'1'});h.auth.creds=creds;await h.connect();
    h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
    assert.equal(h.context.newLinkNoticeAuthDir,null);assert.equal(h.notices.length,0);
  }
});

for(const flag of [undefined,'0','1'])test(`a fresh global link preserves the existing confirmation opt-in ${flag??'default'}`,async()=>{
  const h=harness({registered:false,flag});await h.connect();h.auth.creds.registered=true;
  h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
  assert.equal(h.notices.length,flag==='1'?1:0);
  if(flag==='1')assert.equal(h.context.newLinkNoticeAuthDir,null);
});

test('a fresh global pairing retains its one notice across restartRequired, then ordinary reconnect stays silent',async()=>{
  const h=harness({registered:false});await h.connect();h.auth.creds.registered=true;
  h.sockets[0].ev.emit('connection.update',{connection:'close',lastDisconnect:{error:{output:{statusCode:515}}}});
  await h.runTimers(500);assert.equal(h.sockets.length,2);assert.equal(h.notices.length,0);
  h.sockets[1].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
  assert.equal(h.notices.length,1);assert.equal(h.context.newLinkNoticeAuthDir,null);
  await h.connect();h.sockets[2].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
  assert.equal(h.notices.length,1);
});

test('logout cancels a pending new-link notice and stale timers cannot announce another socket',async()=>{
  const h=harness({registered:false});await h.connect();h.auth.creds.registered=true;
  h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();
  h.sockets[0].ev.emit('connection.update',{connection:'close',lastDisconnect:{error:{output:{statusCode:401}}}});
  await h.runTimers(2500);assert.equal(h.notices.length,0);assert.equal(h.context.newLinkNoticeAuthDir,null);
});

test('a superseded socket cannot consume the fresh-link notice intent of its replacement',async()=>{
  const h=harness({registered:false});await h.connect();h.auth.creds.registered=true;
  h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();
  await h.connect();await h.runTimers(2500);assert.equal(h.notices.length,0);
  h.sockets[0].ev.emit('connection.update',{connection:'open'});await flush();assert.equal(h.timers.size,0);
  h.sockets[1].ev.emit('connection.update',{connection:'open'});await flush();await h.runTimers(2500);
  assert.equal(h.notices.length,1);assert.equal(h.context.newLinkNoticeAuthDir,null);
});
test('a configured primary acquires SQL ownership before touching auth or creating its socket',async()=>{
  const h=harness(),calls=[];let owned=false;
  h.context.legacyCentral={configured:()=>true,resume:async()=>{calls.push('reserve');owned=true;},
    transportCurrent:()=>owned,assertTransportCurrent:async()=>{assert.equal(owned,true);calls.push('check');}};
  const open=h.context.useDurableAuthState;h.context.useDurableAuthState=(...args)=>{calls.push('auth');return open(...args);};
  await h.connect();assert.deepEqual(calls,['reserve','check','auth','check']);assert.equal(h.sockets.length,1);
});
test('an unavailable owner broker never falls back to an unfenced primary socket',async()=>{
  const h=harness();let authCalls=0;
  h.context.legacyCentral={configured:()=>true,resume:async()=>{throw Error('unavailable');},transportCurrent:()=>false,assertTransportCurrent:async()=>{throw Error('unavailable');}};
  h.context.useDurableAuthState=()=>{authCalls++;assert.fail('No auth read without ownership');};
  await h.connect();assert.equal(authCalls,0);assert.equal(h.sockets.length,0);assert.equal(h.context.status,'error');assert.equal(h.timers.size,1);
});
test('primary backup reads are ordered after SQL ownership and before auth migration',async()=>{
  const h=harness(),calls=[];let owned=false;
  h.context.authBackupDirectory=null;
  h.context.legacyCentral={configured:()=>true,resume:async()=>{owned=true;calls.push('reserve');},transportCurrent:()=>owned,
    assertTransportCurrent:async()=>{assert.equal(owned,true);calls.push('check');}};
  h.context.backupAuth=()=>{assert.equal(owned,true);calls.push('backup');return {state:'verified_existing'};};
  const open=h.context.useDurableAuthState;h.context.useDurableAuthState=(...args)=>{calls.push('auth');return open(...args);};
  await h.connect();assert.deepEqual(calls,['reserve','check','backup','auth','check']);
});
test('a lease lost while fetching the protocol version cannot construct a late primary socket',async()=>{
  const h=harness();let owned=true;
  h.context.legacyCentral={configured:()=>true,resume:async()=>{},transportCurrent:()=>owned,
    assertTransportCurrent:async()=>{if(!owned)throw Error('lease lost');}};
  h.context.process.env.QR_WA_VERSION_AUTO='1';
  h.context.fetchLatestWaWebVersion=async()=>{owned=false;return {isLatest:true,version:[2,3000,123456789]};};
  await h.connect();assert.equal(h.sockets.length,0);assert.equal(h.context.status,'error');
});
test('rapid successful handshakes followed by repeated stream 500 exhaust the existing recovery budget',async()=>{
  const h=harness({registered:true});await h.connect();
  for(const [index,delay] of [[0,1000],[1,2000]]){
    h.sockets[index].ev.emit('connection.update',{connection:'open'});
    h.sockets[index].ev.emit('connection.update',{connection:'close',lastDisconnect:{error:{output:{statusCode:500}}}});
    await h.runTimers(delay);
  }
  h.sockets[2].ev.emit('connection.update',{connection:'open'});
  h.sockets[2].ev.emit('connection.update',{connection:'close',lastDisconnect:{error:{output:{statusCode:500}}}});
  assert.equal(h.sockets.length,3);assert.equal(h.context.status,'error');assert.equal(h.timers.size,0);
  assert.equal(h.auth.creds.registered,true);assert.equal(h.notices.length,0);
});
