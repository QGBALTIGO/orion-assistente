/** A live socket does not prove that replies can be prepared. This records
 * only bounded, non-sensitive failures before relay; success clears the flag. */
const stages=new Set(['recipient_lookup','recipient_proof','presence','pacing','message_generation']);
const count=value=>Number.isSafeInteger(value)&&value>=0&&value<=2?value:0;
export function makeOutboundHealth({now=Date.now,previous}={}){
  let failures=count(previous?.outbound_preparation_failures),last=Number.isFinite(previous?.outbound_preparation_at)?previous.outbound_preparation_at:null;
  let degraded=previous?.outbound_preparation_degraded===true;
  return {
    failure(stage){
      if(!stages.has(stage))return false;
      const at=now();if(!degraded&&last!==null&&at-last>600000)failures=0;
      last=at;failures=Math.min(2,failures+1);
      if(failures<2||degraded)return false;
      degraded=true;return true;
    },
    success(){if(!degraded&&!failures)return false;const changed=degraded;failures=0;last=null;degraded=false;return changed;},
    apply(value){
      const result={...value,outbound_preparation_failures:failures,outbound_preparation_at:last,outbound_preparation_degraded:degraded};
      return degraded&&value?.state==='connected'?{...result,state:'degraded',reason:'outbound_preparation_failed',action:'inspect_outbound_preparation'}:result;
    }
  };
}
