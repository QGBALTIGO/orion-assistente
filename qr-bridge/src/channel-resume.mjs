/** Encrypted restart intent. Only authenticated, previously connected sockets
 * are remembered; the SQL lease still decides whether they may run now. */
import path from 'node:path';
import {channelId,encryptedChannelStore} from './channel-storage.mjs';

export function createChannelResumeStore({root,secret}){
  if(typeof secret!=='string'||secret.length<32)throw Error('invalid_resume_secret');
  const store=encryptedChannelStore({directory:path.join(root,'resume'),secret});
  function read(){
    const value=store.get('connected-devices')??[];
    if(!Array.isArray(value)||value.length>50||value.some(row=>!channelId(row?.id)||
        !channelId(row.tenant)||typeof row.key!=='string'||row.key.length<32||row.key.length>256)||
        new Set(value.map(row=>row.id)).size!==value.length)throw Error('invalid_resume_state');
    return value;
  }
  return {
    list:read,
    remember(row){
      if(!channelId(row?.id)||!channelId(row.tenant)||typeof row.key!=='string'||row.key.length<32||row.key.length>256)
        throw Error('invalid_resume_device');
      const entries=read().filter(item=>item.id!==row.id);
      if(entries.length>=50)throw Error('resume_capacity');
      store.set('connected-devices',[...entries,{id:row.id,tenant:row.tenant,key:row.key}]);
    },
    forget(id,key){
      const entries=read(),next=entries.filter(item=>item.id!==id||item.key!==key);
      if(next.length!==entries.length)store.set('connected-devices',next);
    }
  };
}
