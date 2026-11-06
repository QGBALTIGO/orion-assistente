import {retryIdentity,retryIdentityMatches,sameRetryIdentity} from './retry-identity.mjs';
/**
 * Encrypted, bounded and automatically expiring Baileys retry cache.
 * Stores only retryable proto.IMessage objects, not chat exports.
 *
 * Encryption at rest: HKDF-SHA256(domain-separated bridge secret) +
 * AES-256-GCM with 12-byte random IV and authenticated version/AAD.
 * Never log plaintext, encryption material, phone or JID.
 */
import fs from "node:fs";
import path from "node:path";
import {createCipheriv,createDecipheriv,createHmac,hkdfSync,randomBytes} from "node:crypto";

export function makeEncryptedRetryStore({
 directory,secret,serialize=JSON.stringify,deserialize=JSON.parse,
 now=Date.now,ttlMs=48*60*60*1000,max=2000,maxPayloadBytes=96*1024,maxTotalBytes,
 minimumFreeBytes=64*1024*1024,availableBytes=dir=>{const stat=fs.statfsSync(dir);return Number(stat.bavail)*Number(stat.bsize);}
}){
  if(!directory?.startsWith("/data/") && !process.env.ORION_RETRY_TEST_MODE)
    throw Error("Encrypted cache directory must be private /data");
  if(typeof secret!=="string" || secret.length<32)throw Error("Retry store encryption secret too short");
  if(!Number.isSafeInteger(maxPayloadBytes)||maxPayloadBytes<1024||maxPayloadBytes>1024*1024)
    throw Error("Invalid encrypted cache payload limit");
  if(maxTotalBytes!==undefined&&(!Number.isSafeInteger(maxTotalBytes)||maxTotalBytes<1024))
    throw Error("Invalid encrypted cache total limit");
  if(!Number.isSafeInteger(max)||max<1||!Number.isSafeInteger(ttlMs)||ttlMs<1||
     !Number.isSafeInteger(minimumFreeBytes)||minimumFreeBytes<0||typeof availableBytes!=='function')
    throw Error('Invalid encrypted cache capacity');
  const key=Buffer.from(hkdfSync("sha256",Buffer.from(secret),
    Buffer.from("orion/baileys/message-retry/v1"),Buffer.from("aes-256-gcm"),32));
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const directoryStat=fs.lstatSync(directory);
  if(!directoryStat.isDirectory()||directoryStat.isSymbolicLink())throw Error('unsafe_retry_directory');
  fs.chmodSync(directory,0o700);
  let deniedSaves=0,lastFailure=null;
  const deny=reason=>{deniedSaves++;lastFailure=reason;return false;};
  function file(id){
    const hash=createHmac("sha256",key).update(String(id)).digest("hex");
    return path.join(directory,hash+".enc");
  }
  function prune(){
    const list=fs.readdirSync(directory).filter(x=>/^[a-f0-9]{64}\.enc$/.test(x));
    const sorted=list.map(name=>{const stat=fs.lstatSync(path.join(directory,name));
      if(!stat.isFile()||stat.isSymbolicLink())throw Error('unsafe_retry_file');
      return {name,mtime:stat.mtimeMs,size:stat.size};}).sort((a,b)=>a.mtime-b.mtime);
    for(const entry of [...sorted]){
      if(entry.mtime<now()-ttlMs){
        fs.rmSync(path.join(directory,entry.name),{force:true});
        sorted.splice(sorted.indexOf(entry),1);
      }
    }
    // Capacity is admission control, never permission to erase a still-valid
    // pre-relay copy that another customer's device may need for decryption.
    return {files:sorted,bytes:sorted.reduce((sum,entry)=>sum+entry.size,0)};
  }
  function save(keyData,message){
    if(!keyData?.id || !message || typeof message!=="object")return false;
    try{
      const id=String(keyData.id);
      const identity=retryIdentity(keyData),old=entry(keyData);
      if(old?.identity?.peer&&!sameRetryIdentity(old.identity,identity))return false;
      const payload=Buffer.from(serialize({expires:now()+ttlMs,message,identity}),"utf8");
      if(payload.length>maxPayloadBytes){payload.fill(0);return deny('payload_limit');}
      const iv=randomBytes(12);
      const cipher=createCipheriv("aes-256-gcm",key,iv);
      cipher.setAAD(Buffer.from("orion-retry-v2\0"+path.resolve(directory)+"\0"+id));
      const encrypted=Buffer.concat([cipher.update(payload),cipher.final()]);
      payload.fill(0);
      const record={v:2,iv:iv.toString("base64"),tag:cipher.getAuthTag().toString("base64"),
        ciphertext:encrypted.toString("base64")};
      const target=file(id),tmp=target+"."+randomBytes(8).toString("hex")+".tmp";
      const inventory=prune(),previous=inventory.files.find(row=>row.name===path.basename(target));
      const encoded=JSON.stringify(record),recordBytes=Buffer.byteLength(encoded);
      if(!previous&&inventory.files.length>=max)return deny('entry_limit');
      if(maxTotalBytes!==undefined&&inventory.bytes-(previous?.size||0)+recordBytes>maxTotalBytes)return deny('byte_limit');
      // The temporary atomic replacement needs its full size before rename.
      // Keep free space for auth/Signal commits. This check cannot replace a
      // correctly sized volume or a quota shared with other writers.
      const free=availableBytes(directory);
      if(!Number.isFinite(free)||free<0||free<minimumFreeBytes+recordBytes+4096)return deny('disk_reserve');
      try{
        // A process restart or abrupt Railway container termination must not
        // discard the only pre-relay retry copy while WhatsApp requests it.
        const fd=fs.openSync(tmp,"wx",0o600);
        try{
          fs.writeFileSync(fd,encoded);
          fs.fsyncSync(fd);
        }finally{fs.closeSync(fd)}
        fs.renameSync(tmp,target);
        // Persist the filename as well as the ciphertext on Linux volumes.
        if(process.platform!=='win32'){
          const directoryFd=fs.openSync(directory,"r");
          try{fs.fsyncSync(directoryFd)}finally{fs.closeSync(directoryFd)}
        }
      }finally{fs.rmSync(tmp,{force:true})}
      lastFailure=null;return fs.existsSync(target);
    }catch{
      return deny('storage_error');
    }
  }
  function entry(keyData){
    if(!keyData?.id)return undefined;
    const target=file(keyData.id);
    try{
      const fd=fs.openSync(target,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));let record;
      try{const stat=fs.fstatSync(fd);if(!stat.isFile()||stat.size>maxPayloadBytes*2+2048)return undefined;
        record=JSON.parse(fs.readFileSync(fd,'utf8'));
      }finally{fs.closeSync(fd);}
      if(![1,2].includes(record.v))return undefined;
      const decipher=createDecipheriv("aes-256-gcm",key,Buffer.from(record.iv,"base64"));
      decipher.setAAD(Buffer.from(record.v===2?"orion-retry-v2\0"+path.resolve(directory)+"\0"+String(keyData.id):"orion-retry-v1"));
      decipher.setAuthTag(Buffer.from(record.tag,"base64"));
      const plaintext=Buffer.concat([
        decipher.update(Buffer.from(record.ciphertext,"base64")),decipher.final()]);
      let parsed;try{parsed=deserialize(plaintext.toString("utf8"));}finally{plaintext.fill(0);}
      if(!Number.isFinite(parsed.expires)||parsed.expires<=now()){
        fs.rmSync(target,{force:true});
        return undefined;
      }
      return {message:parsed.message,identity:parsed.identity,expires:parsed.expires};
    }catch{
      return undefined;
    }
  }
  function get(keyData){const row=entry(keyData);return row&&retryIdentityMatches(row.identity,keyData)?row.message:undefined;}
  return {save,get,entry,prune,diagnostics(){
    try{const inventory=prune();return {state:lastFailure==='storage_error'?'unavailable':lastFailure?'full':'ok',
      error_code:lastFailure,entries:inventory.files.length,bytes:inventory.bytes,max_entries:max,
      max_total_bytes:maxTotalBytes??null,retention_ms:ttlMs,minimum_free_bytes:minimumFreeBytes,denied_saves:deniedSaves};}
    catch{return {state:'unavailable',error_code:'storage_error',denied_saves:deniedSaves};}
  },clear(){
    for(const name of fs.readdirSync(directory)){
      if(/^[a-f0-9]{64}\.enc$/.test(name))fs.rmSync(path.join(directory,name),{force:true});
    }
  }};
}
