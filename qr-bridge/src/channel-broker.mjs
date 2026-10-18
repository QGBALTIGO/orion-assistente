/** Only fixed private API paths; no destination is taken from a request/model. */
import {channelId} from './channel-storage.mjs';

export function createChannelBroker({apiUrl,fetchImpl=fetch}){
  const base=new URL(apiUrl);
  if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||
      base.username||base.password||base.search||base.hash||base.pathname!=='/')throw Error('unsafe_channel_broker');
  async function request(id,key,operation,body,{owner,fence}={}){
    if(!channelId(id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_channel');
    const target=new URL(`/internal/qr/channels/${id}/${operation}`,base);
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),operation==='ai/reply'?19000:5000);
    try{
      const response=await fetchImpl(target,{method:'POST',redirect:'error',signal:controller.signal,
        headers:{'Content-Type':'application/json','X-Orion-Channel-Key':key,
          ...(owner?{'X-Orion-Channel-Owner':owner,'X-Orion-Channel-Fence':String(fence)}:{})},body:JSON.stringify(body)});
      if(!response.ok)throw Error('channel_broker_refused');
      return await response.json();
    }finally{clearTimeout(timer);}
  }
  return {
    lease:(id,key,operation,owner,fence)=>request(id,key,'lease/'+operation,{owner,...(fence?{fence}:{})}),
    interaction:(id,key,lease,payload)=>request(id,key,'interaction',payload,lease),
    forward:(id,key,lease,payload)=>request(id,key,'ai/reply',payload,lease)
  };
}
