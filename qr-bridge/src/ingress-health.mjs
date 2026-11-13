/** Compose admission readiness with transport observations without exposing
 * journal payloads, peer identities, or filesystem/provider errors. */
const count=value=>Number.isSafeInteger(value)&&value>=0&&value<=20000?value:0;
export function withIngressHealth(transport,diagnostics){
  if(!diagnostics||typeof diagnostics!=='object')return transport;
  const pending=count(diagnostics.pending),blocked=count(diagnostics.blocked);
  const unavailable=['full','unavailable'].includes(diagnostics.state)||!!diagnostics.error_code;
  const oldest=diagnostics.oldest_at;
  const result={...transport,ingress_pending:pending,ingress_blocked:blocked,
    ingress_oldest_at:Number.isFinite(oldest)&&oldest>0?oldest:null,
    ingress_storage_unavailable:unavailable};
  // A terminal provider/ownership state keeps its specific intervention.
  // A healthy websocket alone cannot make a blocked admission queue ready.
  if(!['connected','degraded'].includes(transport?.state))return result;
  if(unavailable)return {...result,state:'needs_attention',reason:'ingress_storage_unavailable',action:'inspect_ingress_storage'};
  if(blocked)return {...result,state:'needs_attention',reason:'ingress_review_required',action:'review_incoming_messages'};
  if(pending&&transport.state==='connected')return {...result,state:'degraded',reason:'ingress_pending',action:'wait_ingress_recovery'};
  return result;
}
