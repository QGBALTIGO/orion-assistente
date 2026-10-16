/**
 * Small, encrypted, local inbox for an already authenticated linked session.
 * The primary phone and the browser are NOT consulted for this storage.
 *
 * Exactly one bridge process may own the mounted /data volume.
 */
import fs from "node:fs";
import path from "node:path";
import {randomBytes,createCipheriv,createDecipheriv,hkdfSync} from "node:crypto";

export function makeDurableInbox({
  directory,secret,now=Date.now,limit=500,ttlMs=7*86400000
}){
  if(!directory?.startsWith("/data/") && process.env.ORION_INBOX_TEST_MODE!=="1")
    throw Error("Private volume required");
  if(typeof secret!=="string"||Buffer.byteLength(secret)<32)throw Error("Missing encryption secret");
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const key=Buffer.from(hkdfSync("sha256",Buffer.from(secret),
    Buffer.from("orion/linked-inbox/v1"),
    Buffer.from("inbox:"+path.basename(directory)),32));
  const file=path.join(directory,"orion_inbox.v1.enc");
  let state="ready";
  function bounded(items){
    if(!Array.isArray(items))throw Error("Invalid inbox");
    const ids=new Set(),out=[];
    for(const row of items){
      if(!row||typeof row.id!=="string"||row.id.length>160||
        !/^[1-9]\d{7,14}$/.test(row.phone||"")||
        !["in","out"].includes(row.direction)||
        typeof row.body!=="string"||row.body.length>2000||
        !Number.isFinite(row.at)||row.at<now()-ttlMs||
        row.at>now()+60000 || ids.has(row.id))continue;
      ids.add(row.id);
      out.push({id:row.id,phone:row.phone,
        direction:row.direction,body:row.body,at:row.at,
        ...(typeof row.delivery==="string"?{delivery:row.delivery}:{})});
    }
    return out.slice(-limit);
  }
  function load(){
    if(!fs.existsSync(file))return [];
    try{
      const record=JSON.parse(fs.readFileSync(file,"utf8"));
      if(record.v!==1)throw Error("Unsupported inbox");
      const decipher=createDecipheriv("aes-256-gcm",key,Buffer.from(record.iv,"base64"));
      decipher.setAAD(Buffer.from("orion/linked-inbox/v1"));
      decipher.setAuthTag(Buffer.from(record.tag,"base64"));
      const plain=Buffer.concat([
        decipher.update(Buffer.from(record.data,"base64")),decipher.final()]);
      const rows=bounded(JSON.parse(plain.toString("utf8")));
      state="ready";
      return rows;
    }catch{
      // Do not overwrite a corrupt or un-decryptable inbox with empty data.
      state="unreadable";
      return [];
    }
  }
  function save(items){
    if(state!=="ready")return false;
    let temporary;
    try{
      const data=Buffer.from(JSON.stringify(bounded(items)),"utf8");
      const iv=randomBytes(12),cipher=createCipheriv("aes-256-gcm",key,iv);
      cipher.setAAD(Buffer.from("orion/linked-inbox/v1"));
      const encrypted=Buffer.concat([cipher.update(data),cipher.final()]);
      const record=JSON.stringify({v:1,iv:iv.toString("base64"),
        tag:cipher.getAuthTag().toString("base64"),data:encrypted.toString("base64")});
      temporary=file+"."+randomBytes(8).toString("hex")+".tmp";
      fs.writeFileSync(temporary,record,{flag:"wx",mode:0o600});
      fs.renameSync(temporary,file);
      return true;
    }catch{
      state="write_failed";
      return false;
    }finally{
      if(temporary)fs.rmSync(temporary,{force:true});
    }
  }
  function purge(){
    fs.rmSync(file,{force:true});
    state="ready";
  }
  return {load,save,purge,health:()=>state,file};
}
