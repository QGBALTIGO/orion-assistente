/** A temporary broker outage is not a revocation. No caller may extend a
 * lease or authorize an operation from this classification alone. */
export function isTransientLeaseFailure(error){
  if(error?.code==='CHANNEL_BROKER_TEMPORARILY_UNAVAILABLE')return true;
  return error?.message==='channel_broker_refused'&&[429,500,502,503,504].includes(error.status);
}
