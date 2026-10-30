/** A bounded admission gate for appointment protocol replies, not AI access.
 * No persistence, no plaintext messages, no phone/JID logs and no provider sends.
 * The existing WhatsApp send limit remains the final backpressure boundary.
 */
export function makeBookingIntakeGuard({
  maxInFlight=48,perSenderLimit=8,windowMs=300000,maxTrackedSenders=512,now=Date.now
}={}){
  if(!Number.isSafeInteger(maxInFlight)||maxInFlight<1||maxInFlight>100||
     !Number.isSafeInteger(perSenderLimit)||perSenderLimit<1||perSenderLimit>50||
     !Number.isSafeInteger(windowMs)||windowMs<1000||windowMs>3600000||
     !Number.isSafeInteger(maxTrackedSenders)||maxTrackedSenders<1||maxTrackedSenders>5000||
     typeof now!=='function')throw Error('invalid_booking_admission_config');
  let active=0;
  const counts=new Map();
  return Object.freeze({
    reserve(){
      if(active>=maxInFlight)return null;
      active++;
      let released=false;
      return ()=>{
        if(released)return;
        released=true;
        active--;
      };
    },
    allowSender(phone){
      // An opaque LID is not a telephone number. Never infer one from it.
      if(typeof phone!=='string'||!/^(?:55[1-9][0-9]{9,10}|5959[0-9]{8})$/.test(phone))
        return false;
      const at=now();
      const existing=counts.get(phone)||[];
      const recent=existing.filter(ts=>at-ts>=0&&at-ts<windowMs);
      if(recent.length>=perSenderLimit){
        counts.delete(phone);counts.set(phone,recent);
        return false;
      }
      counts.delete(phone);
      counts.set(phone,[...recent,at]);
      // Bound the identities stored in memory: never persist or log a PN.
      while(counts.size>maxTrackedSenders)counts.delete(counts.keys().next().value);
      return true;
    },
    active:()=>active
  });
}
