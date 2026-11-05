/** Public transport observations only: never inspect frame bytes or send a
 * customer probe. An idle conversation is not an unhealthy connection. */
import {randomUUID} from 'node:crypto';

const processInstance=randomUUID();
const uuid=value=>typeof value==='string'&&/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(value)?value:null;
export function runtimeIdentity(env=process.env){
  return {build_commit:/^[a-f0-9]{40}$/.test(env.RAILWAY_GIT_COMMIT_SHA||'')?env.RAILWAY_GIT_COMMIT_SHA:null,
    deployment_id:uuid(env.RAILWAY_DEPLOYMENT_ID),instance_id:uuid(env.RAILWAY_REPLICA_ID)||processInstance,
    baileys_version:'6.7.24',node_version:process.versions.node};
}
export function sustainedRecovery(health,now=Date.now(),stableMs=60000){
  return health?.transport_observed===true&&health.sync_pending!==true&&Number.isFinite(health.connected_at)&&Number.isFinite(health.last_activity_at)&&
    now-health.connected_at>=stableMs&&health.last_activity_at-health.connected_at>=stableMs;
}
export function disconnectPolicy(value,attempts=0,{random=Math.random,sessionAttempts=attempts}={}){
  const code=value===null||value===undefined?NaN:Number(value),terminal={401:['logged_out','reauthenticate'],403:['forbidden','inspect_provider_restriction'],
    411:['multidevice_mismatch','inspect_client_compatibility'],440:['connection_replaced','inspect_session_ownership']};
  if(terminal[code])return {code,reason:terminal[code][0],action:terminal[code][1],retry:false,delayMs:null};
  // The SDK labels 500 badSession, but stream/server errors can also carry
  // that code. Try the intact persisted state twice; never infer corruption
  // or create a new identity from the numeric code alone.
  if(code===500&&sessionAttempts>=2)return {code,reason:'session_recovery_exhausted',action:'inspect_session_state',retry:false,delayMs:null};
  if(attempts>=8)return {code:Number.isFinite(code)?code:null,reason:'reconnect_exhausted',action:'retry_connection',retry:false,delayMs:null};
  return {code:Number.isFinite(code)?code:null,reason:code===500?'session_recovery':code===515?'restart_required':code===408?'network_timeout':code===428?'connection_closed':code===503?'provider_unavailable':'transport_interrupted',
    action:null,retry:true,delayMs:code===515?500:Math.min(30000,Math.round(1000*2**Math.min(Math.max(0,attempts),5)*(.8+.4*random())))};
}

export function makeConnectionHealth({now=Date.now,emit=()=>{},scope={},onStale=()=>{},
  schedule=setTimeout,cancel=clearTimeout,sampleMs=5000,staleMs=90000,syncTimeoutMs=120000,
  isSyncPending=()=>false,identity=runtimeIdentity(),previous}={}){
  let state='starting',lastActivityAt=null,interruptedAt=Number.isFinite(previous?.interrupted_at)?previous.interrupted_at:null,recoveredAt=null,connectedAt=null,
    disconnectCode=null,reason=null,reconnectAt=null,attempts=previous?.reconnect_attempts||0,action=null,stopped=false,
    socket=null,timer=null,lastTick=null,lastSampleAt=null,staleSamples=0,syncSamples=0,observedFrames=0,webVersion=null;
  const context={...identity,...Object.fromEntries(Object.entries(scope).filter(([name,value])=>
    ['channel_id','tenant_id','owner','generation','fence'].includes(name)&&
    (uuid(value)||Number.isSafeInteger(value)&&value>=0)))};
  function record(event,extra={}){try{emit({component:'orion-whatsapp-lifecycle',...context,event,at:now(),
    rss_bytes:process.memoryUsage().rss,uptime_seconds:Math.floor(process.uptime()),web_version:webVersion,...extra});}catch{}}
  function activity(){if(!stopped){lastActivityAt=now();observedFrames++;staleSamples=0;if(state==='degraded'){state='connected';reason=null;record('transport_responsive');}}}
  function tick(){
    timer=null;if(stopped||!socket)return;
    const at=now(),lag=lastTick===null?0:Math.max(0,at-lastTick-sampleMs),delayed=lag>sampleMs*2;lastTick=at;
    if(state==='connected'||state==='degraded'){
      if(socket.isOpen===false){state='degraded';reason='socket_not_open';record('transport_stale',{reason});onStale(reason);return;}
      // Ignore one late sampling turn after an event-loop stall: queued
      // keepalive responses must get a chance to run before deciding stale.
      if(delayed){state='degraded';reason='event_loop_delayed';staleSamples=0;syncSamples=0;record('sampling_delayed');}
      else if(Number.isFinite(connectedAt)&&at-connectedAt>syncTimeoutMs&&isSyncPending()===true){
        if(++syncSamples>=2){state='degraded';reason='sync_stalled';record('transport_stale',{reason});onStale(reason);return;}
      }
      else if(observedFrames>0&&lastActivityAt!==null&&at-lastActivityAt>staleMs){
        if(++staleSamples>=2){state='degraded';reason='transport_unresponsive';record('transport_stale',{reason});onStale(reason);return;}
      }
    }
    if(lastSampleAt===null||at-lastSampleAt>=60000){lastSampleAt=at;
      record('health_sample',{state,last_activity_age_ms:lastActivityAt===null?null:Math.max(0,at-lastActivityAt),
        ws_open:socket.isOpen===true,ev_buffering:isSyncPending()===true,reconnect_attempts:attempts,event_loop_lag_ms:lag});}
    timer=schedule(tick,sampleMs);timer?.unref?.();
  }
  function watch(value){
    if(socket)return;
    // 6.7.24 WebSocketClient forwards raw message/pong events and exposes
    // isOpen. Missing observer support is unknown, not a failure verdict.
    if(!value||typeof value.on!=='function'||typeof value.isOpen!=='boolean')return;
    socket=value;socket.on('message',activity);socket.on('pong',activity);
    lastTick=now();timer=schedule(tick,sampleMs);timer?.unref?.();
  }
  function unwatch(){cancel(timer);timer=null;socket?.off?.('message',activity);socket?.off?.('pong',activity);socket=null;}
  return {
    watch,
    protocolVersion(value){if(Array.isArray(value)&&value.length===3&&value.every(item=>Number.isSafeInteger(item)&&item>0)){
      webVersion=[...value];record('protocol_selected');}},
    connecting(){unwatch();stopped=false;observedFrames=0;lastActivityAt=null;lastTick=null;staleSamples=0;
      state='connecting';reconnectAt=null;action=null;syncSamples=0;record('connecting');},
    connected(){state='connected';connectedAt=now();lastSampleAt=now();recoveredAt=interruptedAt===null?null:now();
      const outageMs=interruptedAt===null?null:Math.max(0,now()-interruptedAt);interruptedAt=null;reason=null;action=null;reconnectAt=null;attempts=0;
      record('connected',{outage_ms:outageMs});},
    disconnected(policy,attempt){if(interruptedAt===null)interruptedAt=now();disconnectCode=policy.code??null;
      reason=policy.reason;action=policy.action??null;attempts=attempt;state=policy.retry?'reconnecting':'needs_attention';
      reconnectAt=policy.retry?now()+policy.delayMs:null;record('disconnected',{code:disconnectCode,reason,attempt,action,reconnect_at:reconnectAt});},
    snapshot(){return {state,last_activity_at:lastActivityAt,transport_observed:observedFrames>0,sync_pending:isSyncPending()===true,connected_at:connectedAt,
      interrupted_at:interruptedAt,recovered_at:recoveredAt,disconnect_code:disconnectCode,reason,reconnect_at:reconnectAt,
      reconnect_attempts:attempts,action,web_version:webVersion,...context};},
    stop(cause='stopped'){if(stopped)return;stopped=true;unwatch();
      if(!['reconnecting','needs_attention'].includes(state))state='stopped';record('stopped',{reason:cause});}
  };
}
