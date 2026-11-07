/** Durable, explicitly dispatched proxy diagnostics. This controller has no
 * session factory, connection lease, WhatsApp client, or message-send method. */
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {channelId,channelDirectory,privateDirectory,sendLedger,digest} from './channel-storage.mjs';
import {probeDeviceProxy,proxyProbeError,safeProxyError,PROXY_PROBE_TARGET} from './device-proxy-probe.mjs';

const hash=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const validScope=value=>channelId(value?.device_id)&&channelId(value?.generation)&&Number.isSafeInteger(value.settings_revision)&&value.settings_revision>=1;
const scope=value=>({request_id:value.request_id,device_id:value.device_id,generation:value.generation,settings_revision:value.settings_revision});
const sameScope=(a,b)=>a.request_id===b.request_id&&a.device_id===b.device_id&&a.generation===b.generation&&a.settings_revision===b.settings_revision;
function diagnosticsLedger(root,tenant,id,key,readOnly=false){
  const base=channelDirectory(root,tenant,id,{create:!readOnly});
  const directory=privateDirectory(path.join(base,'device-diagnostics'),{create:!readOnly});
  return sendLedger(directory,key,{readOnly});
}
const proxyFingerprint=grant=>digest(JSON.stringify([scope(grant),grant.configuration_version,
  grant.proxy?.enabled,grant.proxy?.url??null,grant.proxy?.username??null,grant.proxy_secret?.password??null]));

export function createDeviceDiagnostics({broker,root,owner=randomUUID(),clock=()=>performance.now(),now=Date.now,
  probe=probeDeviceProxy,appliedToSession=()=>null,maxConcurrent=4}={}){
  if(!channelId(owner)||!Number.isSafeInteger(maxConcurrent)||maxConcurrent<1||maxConcurrent>10)throw Error('invalid_diagnostic_owner');
  const running=new Map();let closed=false;
  const keyOf=(id,request)=>id+':'+request;
  async function identity(id,key,request_id){
    if(!channelId(id)||!channelId(request_id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_device_diagnostic');
    if(typeof broker.deviceProxyReceipt!=='function')throw Error('device_diagnostics_unavailable');
    const grant=await broker.deviceProxyReceipt(id,key,{request_id});
    if(grant?.read_only!==true||grant.kind!=='proxy_test'||grant.request_id!==request_id||grant.channel_id!==id||
      !channelId(grant.tenant_id)||!validScope(grant))throw Error('device_diagnostic_receipt_refused');
    return grant;
  }
  async function read(id,key,request_id){
    const grant=await identity(id,key,request_id);let record;
    try{record=diagnosticsLedger(root,grant.tenant_id,id,key,true).get(request_id);}
    catch(error){if(error.code!=='ENOENT')throw error;}
    if(record?.request&&!sameScope(record.request,grant))throw Error('device_diagnostic_receipt_refused');
    if(record?.result)return {...scope(grant),status:record.status,recorded:true,result:record.result};
    return {...scope(grant),status:record?(running.has(keyOf(id,request_id))?'running':'uncertain'):'unrecorded',recorded:!!record,result:null};
  }
  async function dispatch(id,key,input){
    if(closed||!channelId(input?.request_id)||!validScope(input))throw Error('invalid_device_diagnostic');
    // A repeated POST and a new process can only recover a reservation. They
    // never convert an interrupted/ambiguous probe into a second network run.
    const previous=await read(id,key,input.request_id);
    if(!sameScope(previous,input))throw Error('device_diagnostic_scope_changed');
    if(previous.recorded)return previous;
    if(closed||running.size>=maxConcurrent||[...running.values()].some(task=>task.id===id))throw Error('device_diagnostics_busy');
    const token=randomUUID(),request=scope(input),task={id,abort:new AbortController()};
    running.set(keyOf(id,input.request_id),task);
    let permit,ledger,reserved,deadline=Infinity,fingerprint,expiry;
    const fresh=()=>!closed&&!task.abort.signal.aborted&&clock()<deadline;
    const authorize=async(recheck=false)=>{
      if(!fresh())throw proxyProbeError('PROXY_AUTHORIZATION_REVOKED');
      let grant;const started=clock();
      try{grant=await broker.deviceProxyAuthorize(id,key,{...request,owner,claim_token:token,
        recheck,...(permit?{claim_fence:permit.claim_fence}:{})});}
      catch{throw proxyProbeError('PROXY_AUTHORIZATION_REVOKED');}
      if(!fresh()||grant?.authorized!==true||grant.kind!=='proxy_test'||grant.channel_id!==id||!channelId(grant.tenant_id)||
        !sameScope(grant,request)||grant.owner!==owner||grant.claim_token!==token||
        !Number.isSafeInteger(grant.claim_fence)||grant.claim_fence<1||!hash(grant.configuration_version)||
        !Number.isFinite(grant.expires_in_ms)||grant.expires_in_ms<=250||grant.expires_in_ms>30000||
        !Number.isFinite(Date.parse(grant.expires_at))||typeof grant.proxy?.enabled!=='boolean')throw proxyProbeError('PROXY_AUTHORIZATION_REVOKED');
      const next=proxyFingerprint(grant);
      if(permit&&(grant.tenant_id!==permit.tenant_id||grant.claim_fence!==permit.claim_fence||next!==fingerprint))throw proxyProbeError('PROXY_CONFIGURATION_CHANGED');
      deadline=Math.min(deadline,started+grant.expires_in_ms-100);
      if(!fresh())throw proxyProbeError('PROXY_AUTHORIZATION_REVOKED');
      clearTimeout(expiry);expiry=setTimeout(()=>task.abort.abort(),Math.max(0,deadline-clock()));expiry.unref?.();
      permit=grant;fingerprint=next;return grant;
    };
    try{
      await authorize();
      ledger=diagnosticsLedger(root,permit.tenant_id,id,key);
      reserved=ledger.reserve(input.request_id,digest(JSON.stringify([request,permit.configuration_version])));
      if(!reserved.fresh){clearTimeout(expiry);running.delete(keyOf(id,input.request_id));task.abort.abort();return await read(id,key,input.request_id);}
      ledger.save(input.request_id,{...reserved.record,request,owner,claim_token:token,claim_fence:permit.claim_fence,
        configuration_version:permit.configuration_version});
    }catch(error){clearTimeout(expiry);running.delete(keyOf(id,input.request_id));task.abort.abort();throw error;}
    const persist=async(status,result)=>{
      // Persist before ACK. Nothing about a lost API response can repeat DNS,
      // CONNECT, TLS, or a WhatsApp operation from this receipt path.
      ledger.save(input.request_id,{...reserved.record,request,owner,claim_token:token,claim_fence:permit.claim_fence,
        configuration_version:permit.configuration_version,status,result});
      try{await broker.deviceProxyFinish(id,key,{...request,owner,claim_token:token,claim_fence:permit.claim_fence,status,result});}
      catch{/* Read-only recovery projects this same encrypted receipt. */}
    };
    const run=async()=>{
      let result,status='failed';
      try{
        await authorize(true);
        let value,failure;
        try{value=await probe(permit.proxy,{secret:permit.proxy_secret?.password,signal:task.abort.signal,
          assertCurrent:()=>authorize(true)});}catch(error){failure=error;}
        await authorize(true);
        if(failure)throw failure;
        if(!['reachable','not_configured'].includes(value?.state)||value.target!==PROXY_PROBE_TARGET.id||
          (value.state==='reachable'&&(!Number.isSafeInteger(value.latency_ms)||value.latency_ms<0||value.latency_ms>15000)))throw proxyProbeError('PROXY_PROBE_FAILED');
        result={state:value.state,latency_ms:value.state==='reachable'?value.latency_ms:null,target:PROXY_PROBE_TARGET.id,
          error_code:value.state==='not_configured'?'PROXY_NOT_CONFIGURED':null};status=value.state==='reachable'?'succeeded':'failed';
      }catch(error){
        result={state:'failed',latency_ms:null,target:PROXY_PROBE_TARGET.id,error_code:safeProxyError(error?.code)||'PROXY_PROBE_FAILED'};
      }
      // The indication is about this process's applied configuration only. It
      // neither starts a session nor claims WhatsApp or recipient reachability.
      let applied=null;
      try{const value=appliedToSession(id,permit);if(typeof value==='boolean')applied=value;}catch{}
      await persist(status,{scope:'configured_proxy',configuration_version:permit.configuration_version,
        applied_to_session:applied,...result,observed_at:new Date(now()).toISOString()});
    };
    task.promise=run().catch(()=>{/* An unsaved/interrupted reservation stays uncertain. */})
      .finally(()=>{clearTimeout(expiry);task.abort.abort();running.delete(keyOf(id,input.request_id));});
    return {...request,status:'running',recorded:true,result:null};
  }
  return {dispatch,read,
    close(){closed=true;for(const task of running.values())task.abort.abort();},
    async settled(){await Promise.all([...running.values()].map(task=>task.promise).filter(Boolean));},
    get pending(){return running.size;}};
}
