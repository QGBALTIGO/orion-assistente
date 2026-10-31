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
 now=Date.now,ttlMs=48*60*60*1000,max=2000
}){
  if(!directory?.startsWith("/data/") && !process.env.ORION_RETRY_TEST_MODE)
    throw Error("Encrypted cache directory must be private /data");
  if(typeof secret!=="string" || secret.length<32)throw Error("Retry store encryption secret too short");
  const key=Buffer.from(hkdfSync("sha256",Buffer.from(secret),
    Buffer.from("orion/baileys/message-retry/v1"),Buffer.from("aes-256-gcm"),32));
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  function file(id){
    const hash=createHmac("sha256",key).update(String(id)).digest("hex");
    return path.join(directory,hash+".enc");
  }
  function prune(){
    const list=fs.readdirSync(directory).filter(x=>/^[a-f0-9]{64}\.enc$/.test(x));
    const sorted=list.map(name=>({name,mtime:fs.statSync(path.join(directory,name)).mtimeMs})).sort((a,b)=>a.mtime-b.mtime);
    for(const entry of [...sorted]){
      if(entry.mtime<now()-ttlMs){
        fs.rmSync(path.join(directory,entry.name),{force:true});
        sorted.splice(sorted.indexOf(entry),1);
      }
    }
    while(sorted.length>max){const oldest=sorted.shift();fs.rmSync(path.join(directory,oldest.name),{force:true})}
  }
  function save(keyData,message){
    if(!keyData?.id || !message || typeof message!=="object")return false;
    try{
      const id=String(keyData.id);
      const payload=Buffer.from(serialize({expires:now()+ttlMs,message}),"utf8");
      if(payload.length>96*1024)return false;
      const iv=randomBytes(12);
      const cipher=createCipheriv("aes-256-gcm",key,iv);
      cipher.setAAD(Buffer.from("orion-retry-v1"));
      const encrypted=Buffer.concat([cipher.update(payload),cipher.final()]);
      const record={v:1,iv:iv.toString("base64"),tag:cipher.getAuthTag().toString("base64"),
        ciphertext:encrypted.toString("base64")};
      const target=file(id),tmp=target+"."+randomBytes(8).toString("hex")+".tmp";
      try{
        // A process restart or abrupt Railway container termination must not
        // discard the only pre-relay retry copy while WhatsApp requests it.
        const fd=fs.openSync(tmp,"wx",0o600);
        try{
          fs.writeFileSync(fd,JSON.stringify(record));
          fs.fsyncSync(fd);
        }finally{fs.closeSync(fd)}
        fs.renameSync(tmp,target);
        // Persist the filename as well as the ciphertext on Linux volumes.
        const directoryFd=fs.openSync(directory,"r");
        try{fs.fsyncSync(directoryFd)}finally{fs.closeSync(directoryFd)}
      }finally{fs.rmSync(tmp,{force:true})}
      prune();
      return true;
    }catch{
      return false;
    }
  }
  function get(keyData){
    if(!keyData?.id)return undefined;
    const target=file(keyData.id);
    try{
      const record=JSON.parse(fs.readFileSync(target,"utf8"));
      if(record.v!==1)return undefined;
      const decipher=createDecipheriv("aes-256-gcm",key,Buffer.from(record.iv,"base64"));
      decipher.setAAD(Buffer.from("orion-retry-v1"));
      decipher.setAuthTag(Buffer.from(record.tag,"base64"));
      const plaintext=Buffer.concat([
        decipher.update(Buffer.from(record.ciphertext,"base64")),decipher.final()]);
      const parsed=deserialize(plaintext.toString("utf8"));
      if(!Number.isFinite(parsed.expires)||parsed.expires<=now()){
        fs.rmSync(target,{force:true});
        return undefined;
      }
      return parsed.message;
    }catch{
      return undefined;
    }
  }
  return {save,get,prune,clear(){
    for(const name of fs.readdirSync(directory)){
      if(/^[a-f0-9]{64}\.enc$/.test(name))fs.rmSync(path.join(directory,name),{force:true});
    }
  }};
}
