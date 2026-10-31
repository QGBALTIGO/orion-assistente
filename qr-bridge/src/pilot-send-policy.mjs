/** Prefer pre-relay cached retries and refreshed devices for the approved pilot.
 * Exactly one relay attempt. No session/key deletion or automatic duplicate send.
 */
import {pilotNumber} from "./ai-pilot.mjs";

export function deliveryPolicy(phone,env=process.env,{source='automation'}={}){
  const pilot=pilotNumber(env);
  const approvedPilot=env.QR_AI_ENABLED==="1" && pilot!==null && phone===pilot;
  // A cancelled/rescheduled booking must not use a five-minute old device
  // listing. A phone re-link changes its Signal identities even if the number
  // stays the same. This only refreshes the public USync device directory;
  // it NEVER guesses a PN/LID, resets an E2EE key or resends a message.
  const transactional=source==='appointment';
  return {
    reliable:transactional || env.QR_RELIABLE_SEND_V2==="1" || approvedPilot,
    freshDevices:transactional || env.QR_FORCE_FRESH_DEVICES==="1" || approvedPilot,
    pilot:approvedPilot,
  };
}

// Transactional booking notifications must never reach Baileys' relay
// before a durable, encrypted copy is available for Signal retry requests.
// Ordinary sends retain the independently reviewed global rollout switch.
export function requiresPreRelayCache({reliable=false,source='automation'}={}){
  return reliable===true||source==='appointment';
}
