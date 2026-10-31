/** Prefer pre-relay cached retries and refreshed devices for the approved pilot.
 * Exactly one relay attempt. No session/key deletion or automatic duplicate send.
 */
import {pilotNumber} from "./ai-pilot.mjs";

export function deliveryPolicy(phone,env=process.env){
  const pilot=pilotNumber(env);
  const approvedPilot=env.QR_AI_ENABLED==="1" && pilot!==null && phone===pilot;
  return {
    reliable:env.QR_RELIABLE_SEND_V2==="1" || approvedPilot,
    freshDevices:env.QR_FORCE_FRESH_DEVICES==="1" || approvedPilot,
    pilot:approvedPilot,
  };
}

// Transactional booking notifications must never reach Baileys' relay
// before a durable, encrypted copy is available for Signal retry requests.
// Ordinary sends retain the independently reviewed global rollout switch.
export function requiresPreRelayCache({reliable=false,source='automation'}={}){
  return reliable===true||source==='appointment';
}
