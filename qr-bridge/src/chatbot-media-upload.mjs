/** Pinned Baileys 6.7.24 ends its encrypted file stream without awaiting finish.
 * Verify the complete ciphertext before its one authorized upload begins. */
import fs from 'node:fs';
import {open} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';

export async function awaitChatbotEncryptedMedia(file,metadata,{signal,isCurrent=()=>true,timeoutMs=2500}={}){
  if(typeof file!=='string'||path.dirname(path.resolve(file))!==path.resolve(os.tmpdir())||
      !/^(image|sticker|video|document)[A-F0-9]{16,80}-enc$/.test(path.basename(file))||
      typeof metadata?.fileEncSha256B64!=='string'||!/^[A-Za-z0-9+/]{43}=$/.test(metadata.fileEncSha256B64))throw Error('chatbot_upload_invalid');
  const deadline=Date.now()+timeoutMs;
  for(;;){
    if(signal?.aborted||!isCurrent())throw Error('chatbot_upload_revoked');
    let handle,bytes;
    try{
      handle=await open(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
      const stat=await handle.stat();
      if(!stat.isFile()||stat.size>16_000_128)throw Error('chatbot_upload_invalid');
      bytes=await handle.readFile();
      if(bytes.length>16_000_128)throw Error('chatbot_upload_invalid');
      if(createHash('sha256').update(bytes).digest('base64')===metadata.fileEncSha256B64){
        if(signal?.aborted||!isCurrent())throw Error('chatbot_upload_revoked');
        return;
      }
    }catch(error){if(error.code!=='ENOENT')throw error;}
    finally{bytes?.fill(0);await handle?.close();}
    if(Date.now()>=deadline)throw Error('chatbot_upload_incomplete');
    await delay(20,undefined,{signal});
  }
}
