/** Small NodeCache-compatible cache with TTL and a hard memory entry bound.
 * No timers survive closed sockets; expiry is checked on every read/write.
 */
export function makeBoundedCache({max=5000,ttlMs=300000,now=Date.now}={}){
  if(!Number.isSafeInteger(max)||max<1||max>50000||!Number.isSafeInteger(ttlMs)||ttlMs<1)throw Error('invalid_cache_limit');
  const rows=new Map();
  function prune(){const at=now();for(const [key,row] of rows)if(row.expires<=at)rows.delete(key);while(rows.size>max)rows.delete(rows.keys().next().value);}
  const cache={
    get(key){const row=rows.get(String(key));if(!row)return undefined;if(row.expires<=now()){rows.delete(String(key));return undefined;}return row.value;},
    set(key,value,ttlSeconds){prune();const id=String(key);if(id.length>2048)return false;
      const life=ttlSeconds===undefined?ttlMs:Number(ttlSeconds)*1000;
      if(!Number.isFinite(life)||life<=0)return false;
      rows.delete(id);rows.set(id,{value,expires:now()+Math.min(life,ttlMs)});prune();return true;},
    del(key){return rows.delete(String(key))?1:0;},
    has(key){return cache.get(key)!==undefined;},
    flushAll(){rows.clear();},close(){rows.clear();},keys(){prune();return [...rows.keys()];},
    get size(){prune();return rows.size;}
  };
  return cache;
}
