/** One advertised WhatsApp Web policy for the pinned SDK's primary and managed
 * sockets. This never changes Baileys, auth files, or an already-open socket. */
const valid=value=>Array.isArray(value)&&value.length===3&&value.every(item=>Number.isSafeInteger(item)&&item>0);
const copy=value=>({...value,version:[...value.version]});

export function createWaVersionPolicy({now=Date.now,schedule=setTimeout,cancel=clearTimeout,timeoutMs=3500,cooldownMs=60000}={}){
  let selected=null,inflight=null,retryAt=0;
  async function select({bundledVersion,fetchVersion,auto=true}={}){
    if(!valid(bundledVersion))throw Error('invalid_bundled_web_version');
    const bundled=source=>({version:[...bundledVersion],source});
    if(auto!==true)return bundled('bundled_disabled');
    if(selected)return copy(selected);
    if(typeof fetchVersion!=='function'||now()<retryAt)return bundled('bundled_fallback');
    if(!inflight){
      inflight=(async()=>{
        let timer;
        try{
          // The installed helper uses axios options. Keep its timeout and an
          // independent bound so a stuck or replaced helper cannot stall lease
          // revalidation or create an unbounded connection backlog.
          const result=await Promise.race([Promise.resolve().then(()=>fetchVersion({timeout:timeoutMs})),
            new Promise(resolve=>{timer=schedule(()=>resolve(null),timeoutMs);})]);
          if(result?.isLatest===true&&valid(result.version)){
            selected={version:Object.freeze([...result.version]),source:'latest_web'};return selected;
          }
          retryAt=now()+cooldownMs;return bundled('bundled_fallback');
        }catch{
          retryAt=now()+cooldownMs;return bundled('bundled_fallback');
        }finally{cancel(timer);inflight=null;}
      })();
    }
    return copy(await inflight);
  }
  return {select};
}
const shared=createWaVersionPolicy();
export const selectWaWebVersion=options=>shared.select(options);
