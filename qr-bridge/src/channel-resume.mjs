/** Encrypted restart intent. Only authenticated, previously connected sockets
 * are remembered; the SQL lease still decides whether they may run now. */
import path from 'node:path';
import {channelId,encryptedChannelStore} from './channel-storage.mjs';

const validDeviceScope=row=>row.device_id===undefined&&row.generation===undefined&&row.device_proxy_fingerprint===undefined||
  channelId(row.device_id)&&channelId(row.generation)&&/^[0-9a-f]{64}$/.test(row.device_proxy_fingerprint||'');

export function createChannelResumeStore({root,secret}){
  if(typeof secret!=='string'||secret.length<32)throw Error('invalid_resume_secret');
  const store=encryptedChannelStore({directory:path.join(root,'resume'),secret});
  function read(){
    const value=store.get('connected-devices')??[];
    if(!Array.isArray(value)||value.length>50||value.some(row=>!channelId(row?.id)||
        !channelId(row.tenant)||typeof row.key!=='string'||row.key.length<32||row.key.length>256||!validDeviceScope(row))||
        new Set(value.map(row=>row.id)).size!==value.length)throw Error('invalid_resume_state');
    return value;
  }
  return {
    list:read,
    remember(row){
      if(!channelId(row?.id)||!channelId(row.tenant)||typeof row.key!=='string'||row.key.length<32||row.key.length>256||!validDeviceScope(row))
        throw Error('invalid_resume_device');
      const entries=read().filter(item=>item.id!==row.id);
      if(entries.length>=50)throw Error('resume_capacity');
      store.set('connected-devices',[...entries,{id:row.id,tenant:row.tenant,key:row.key,
        ...(row.device_id?{device_id:row.device_id,generation:row.generation,device_proxy_fingerprint:row.device_proxy_fingerprint}:{})}]);
    },
    forget(id,key){
      const entries=read(),next=entries.filter(item=>item.id!==id||item.key!==key);
      if(next.length!==entries.length)store.set('connected-devices',next);
    }
  };
}
