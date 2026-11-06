/** Synthetic Signal clients only. Separate process: RSS never belongs to the app. */
import assert from 'node:assert/strict';
import {monitorEventLoopDelay,performance} from 'node:perf_hooks';
import * as sdk from '../../qr-bridge/node_modules/@whiskeysockets/baileys/lib/index.js';
import {protectSignalSessionLogs} from '../../qr-bridge/src/signal-log-safety.mjs';
protectSignalSessionLogs();
const peers=[];
const companyJid=i=>String(100000000000000+i)+'@s.whatsapp.net';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function peer(){
  const rows=new Map(),clone=v=>JSON.parse(JSON.stringify(v,sdk.BufferJSON.replacer),sdk.BufferJSON.reviver);
  const state={creds:sdk.initAuthCreds(),keys:{
    async get(type,ids){return Object.fromEntries(ids.map(id=>[id,clone(rows.get(type+':'+id)??null)]));},
    async set(data){for(const[type,entries]of Object.entries(data))for(const[id,value]of Object.entries(entries))rows.set(type+':'+id,clone(value));}
  }};
  return {state,repository:sdk.DEFAULT_CONNECTION_CONFIG.makeSignalRepository(state)};
}
let metric=null,globalPeakRss=process.memoryUsage().rss;
function startMetric(){
  if(metric)throw Error('metric_already_active');
  const lag=monitorEventLoopDelay({resolution:10});lag.enable();
  const m={lag,cpu:process.cpuUsage(),started:performance.now(),peak:process.memoryUsage().rss,last:performance.now(),stall:0,ticks:0};
  m.timer=setInterval(()=>{const at=performance.now();m.stall=Math.max(m.stall,at-m.last-10);m.last=at;m.ticks++;m.peak=Math.max(m.peak,process.memoryUsage().rss);globalPeakRss=Math.max(globalPeakRss,m.peak);},10);
  metric=m;
}
async function finishMetric(){
  if(!metric)throw Error('metric_not_active');await delay(20);
  const m=metric;metric=null;clearInterval(m.timer);m.lag.disable();
  const cpu=process.cpuUsage(m.cpu),rss=process.memoryUsage().rss;m.peak=Math.max(m.peak,rss);globalPeakRss=Math.max(globalPeakRss,m.peak);
  return {process:'synthetic_peer_helper',rss_bytes:rss,peak_rss_bytes:m.peak,cpu_user_ms:cpu.user/1000,cpu_system_ms:cpu.system/1000,
    metrics_window_ms:performance.now()-m.started,event_loop:{p99_ms:m.lag.percentile(99)/1e6,max_ms:m.lag.max/1e6,max_timer_stall_ms:Math.max(0,m.stall),timer_ticks:m.ticks}};
}
async function operation(name,data){
  if(name==='metric_start'){startMetric();await delay(20);return {started:true};}
  if(name==='metric_finish')return finishMetric();
  if(name==='initialize'){
    assert.equal(peers.length,0);assert.equal(data.customers,500);const bundles=[];
    for(let i=0;i<data.customers;i++){
      const p=peer(),preKey=sdk.Curve.generateKeyPair(),c=p.state.creds;
      await p.state.keys.set({'pre-key':{'1':preKey}});peers.push(p);
      bundles.push({registrationId:c.registrationId,identityKey:sdk.generateSignalPubKey(c.signedIdentityKey.public),
        signedPreKey:{keyId:c.signedPreKey.keyId,publicKey:sdk.generateSignalPubKey(c.signedPreKey.keyPair.public),signature:c.signedPreKey.signature},
        preKey:{keyId:1,publicKey:sdk.generateSignalPubKey(preKey.public)}});
      if(i%10===9)await tick();
    }
    return {bundles,customers:peers.length};
  }
  if(name==='roundtrip'){
    assert.ok(Number.isSafeInteger(data.index)&&data.index>=0&&data.index<peers.length);assert.ok(data.round===0||data.round===1);
    const p=peers[data.index],jid=companyJid(Math.floor(data.index/10)),started=performance.now();
    const plain=Buffer.from(await p.repository.decryptMessage({jid,...data.packet}));
    assert.deepEqual(plain,Buffer.from(data.expected));
    const decryptedAt=performance.now(),ackBytes=Buffer.from('synthetic-ack-'+data.round+'-'+data.index);
    const ack=await p.repository.encryptMessage({jid,data:ackBytes});
    return {ack,decrypted:true,helper_component_ms:performance.now()-started,helper_decrypt_ms:decryptedAt-started,
      helper_encrypt_ack_ms:performance.now()-decryptedAt};
  }
  if(name==='shutdown')return {global_peak_rss_bytes:Math.max(globalPeakRss,process.memoryUsage().rss)};
  throw Error('unknown_operation');
}
process.on('message',async message=>{
  const {id,name,data}=message??{};
  try{
    const result=await operation(name,data);
    process.send?.({id,ok:true,result},()=>{if(name==='shutdown')process.disconnect();});
  }catch{process.send?.({id,ok:false,error:'SYNTHETIC_PEER_OPERATION_FAILED'});}
});
process.on('disconnect',()=>process.exit(0));
