/** Read-only production observation. Never emits tokens, numbers or message bodies. */
import fs from 'node:fs';
const env=process.env;
const flags=['QR_CHANNELS_ENABLED','QR_LEGACY_CENTRAL_ENABLED','QR_AI_ENABLED','QR_AI_AUDIO_ENABLED',
  'QR_AI_INTAKE_REVISION_ENABLED','QR_FORCE_FRESH_DEVICES','QR_PILOT_USE_VERIFIED_LID',
  'QR_RELIABLE_SEND_V2','QR_WA_VERSION_AUTO','ORION_NOTIFY_SELF_ON_LINK'];
const safeNumbers=value=>Object.fromEntries(Object.entries(value||{}).filter(([,v])=>
  typeof v==='boolean'||(typeof v==='number'&&Number.isFinite(v))));
const output={observed_at:new Date().toISOString(),node:process.version,
  build_commit:/^[a-f0-9]{40}$/.test(env.RAILWAY_GIT_COMMIT_SHA||'')?env.RAILWAY_GIT_COMMIT_SHA:null,
  deployment_id:env.RAILWAY_DEPLOYMENT_ID||null,replica_id:env.RAILWAY_REPLICA_ID||null,
  flags:Object.fromEntries(flags.map(k=>[k,env[k]==='1'?'1':env[k]==='0'?'0':'unset']))};
try{output.baileys=JSON.parse(fs.readFileSync('node_modules/@whiskeysockets/baileys/package.json','utf8')).version;}catch{}
try{
  const response=await fetch('http://127.0.0.1:'+(env.PORT||3000)+'/internal/status',{
    headers:{'X-Orion-Bridge-Key':env.QR_BRIDGE_TOKEN||''},signal:AbortSignal.timeout(8000),redirect:'error'});
  output.http_status=response.status;
  if(response.ok){
    const data=await response.json();
    output.session={status:data.status,phone_verified:data.phone_verified===true,
      messages_enabled:data.messagesEnabled===true,disconnect_code:data.disconnectCode??null,
      waiting_seconds:data.waitingSeconds,retained_messages:data.retainedMessages,
      delivery:safeNumbers(data.deliveryDiagnostics),transport:{counts:safeNumbers(data.transportDiagnostics?.counts)},
      config:data.transportConfig,connection_health:data.connection_health||null};
  }
}catch(error){output.probe_error=error?.name||'unavailable';}
if(/^[1-9][0-9]{7,14}$/.test(env.ORION_PROBE_RECIPIENT||'')){
  const headers={'X-Orion-Bridge-Key':env.QR_BRIDGE_TOKEN||''};
  output.recipient_check={};
  try{
    const base=new URL(env.QR_CHANNEL_BROKER_URL);
    if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||base.username||base.password)throw Error();
    const response=await fetch(new URL('/internal/qr/legacy/identity',base),{headers,redirect:'error',signal:AbortSignal.timeout(8000)});
    output.recipient_check.bootstrap_status=response.status;
    if(response.ok){const row=await response.json();output.recipient_check.allowed=Array.isArray(row.allowed_senders)&&row.allowed_senders.includes(env.ORION_PROBE_RECIPIENT);}
    const lookup=await fetch('http://127.0.0.1:'+(env.PORT||3000)+'/internal/recipient?phone='+env.ORION_PROBE_RECIPIENT,
      {headers,redirect:'error',signal:AbortSignal.timeout(8000)});
    output.recipient_check.lookup_status=lookup.status;
    if(lookup.ok){const row=await lookup.json();output.recipient_check.lookup_flags=safeNumbers(row);}
  }catch(error){output.recipient_check.error=error?.name||'unavailable';}
}
console.log(JSON.stringify(output));
