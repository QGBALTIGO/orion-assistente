/** Per-logical-device auth epochs. Only explicit relink/remove rotate or erase
 * this namespace; the existing channel and legacy auth roots stay untouched. */
import fs from 'node:fs';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {channelId,privateDirectory,encryptedChannelStore,sendLedger} from './channel-storage.mjs';

export function deviceStorage(channelDirectory,snapshot,secret,{readOnly=false}={}){
  if(!channelId(snapshot?.id)||!channelId(snapshot?.generation))throw Error('invalid_device_storage');
  const options={create:!readOnly};
  const devices=privateDirectory(path.join(channelDirectory,'devices'),options);
  const logical=privateDirectory(path.join(devices,snapshot.id),options);
  const directory=privateDirectory(path.join(logical,snapshot.generation),options);
  const manifest=encryptedChannelStore({directory,secret,readOnly});
  function authDirectory(){
    let state=manifest.get('auth-epoch');
    if(!state&&!readOnly){state={epoch:randomUUID()};manifest.set('auth-epoch',state);}
    if(!channelId(state?.epoch))throw Error('invalid_device_auth_epoch');
    const epochs=privateDirectory(path.join(directory,'epochs'),options);
    return privateDirectory(path.join(epochs,state.epoch),options);
  }
  function reset(){
    if(readOnly)throw Error('device_storage_read_only');
    const old=manifest.get('auth-epoch');
    const epoch=randomUUID();manifest.set('auth-epoch',{epoch});
    // Persist the new epoch before removing old keys. An interrupted cleanup
    // can leave encrypted old bytes, but can never reconnect using old auth.
    if(channelId(old?.epoch)){
      const oldDirectory=path.join(directory,'epochs',old.epoch);
      try{privateDirectory(oldDirectory,{create:false});fs.rmSync(oldDirectory,{recursive:true});}catch(error){if(error.code!=='ENOENT')throw error;}
    }
    return authDirectory();
  }
  return {directory,authDirectory,reset,
    remove(){if(readOnly)throw Error('device_storage_read_only');privateDirectory(logical,{create:false});fs.rmSync(logical,{recursive:true});}};
}

export function deviceControlLedger(channelDirectory,secret,{readOnly=false}={}){
  const directory=privateDirectory(path.join(channelDirectory,'device-controls'),{create:!readOnly});
  return sendLedger(directory,secret,{readOnly});
}
