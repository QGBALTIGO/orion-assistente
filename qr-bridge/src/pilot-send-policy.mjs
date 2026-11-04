/** Shared transport guarantees for every niche and automatic/manual sender.
 * Exactly one application relay attempt. Provider retries use the stored proto.
 * Pilot only selects independently verified addressing; never weakens storage.
 */
import {pilotNumber} from "./ai-pilot.mjs";
export function deliveryPolicy(phone,env=process.env,_options={}){
  const pilot=pilotNumber(env);
  return {reliable:true,freshDevices:true,pilot:env.QR_AI_ENABLED==="1"&&pilot!==null&&phone===pilot};
}
export function requiresPreRelayCache(_options={}){return true;}
