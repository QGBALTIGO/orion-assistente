// Private, explicitly enabled storage of opaque encrypted database backups.
// This module has no database credentials and never touches WhatsApp auth files.
import fs from 'node:fs';
import path from 'node:path';
import {createHash, timingSafeEqual, hkdfSync, createDecipheriv} from 'node:crypto';

const MAGIC=Buffer.from('ORIONDB1');
const MAX=32*1024*1024;
const QUOTA=64*1024*1024;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DIGEST=/^[0-9a-f]{64}$/;
const queues=new Map();
class BackupError extends Error {constructor(status){super('database_backup_refused');this.status=status}}
const hash=body=>createHash('sha256').update(body).digest('hex');
function envelope(body,id,secret){
  if(body.length<MAGIC.length+12+16||body.length>MAX||!body.subarray(0,MAGIC.length).equals(MAGIC))
    throw new BackupError(422);
  if(typeof secret!=='string'||Buffer.byteLength(secret)<32)throw new BackupError(503);
  const key=Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.alloc(0),Buffer.from('orion:database-backup:v1'),32));
  let plain;const decrypted=[];
  try{
    const cipher=body.subarray(MAGIC.length+12,-16);
    const decipher=createDecipheriv('aes-256-gcm',key,body.subarray(MAGIC.length,MAGIC.length+12));
    decipher.setAAD(Buffer.concat([MAGIC,Buffer.from(id,'ascii')]));
    decipher.setAuthTag(body.subarray(-16));
    decrypted.push(decipher.update(cipher));decrypted.push(decipher.final());
    plain=Buffer.concat(decrypted);
    if(!plain.subarray(0,5).equals(Buffer.from('PGDMP')))throw new BackupError(422);
  }catch(error){
    throw error instanceof BackupError?error:new BackupError(422);
  }finally{plain?.fill(0);for(const part of decrypted)part.fill(0);key.fill(0)}
}
function directory(root){
  const resolved=path.resolve(root);
  fs.mkdirSync(resolved,{recursive:true,mode:0o700});
  if(!fs.lstatSync(resolved).isDirectory()||fs.lstatSync(resolved).isSymbolicLink())throw new BackupError(409);
  fs.chmodSync(resolved,0o700);
  return resolved;
}
function json(res,status,value){
  res.writeHead(status,{'content-type':'application/json','cache-control':'no-store'});
  res.end(JSON.stringify(value));
}

export async function databaseBackup(req,res,{root,id,enabled=false,secret}={}){
  if(!enabled)return json(res,423,{error:'database_backup_disabled'});
  if(!UUID.test(String(id||'')))return json(res,400,{error:'invalid_backup_id'});
  if(!['GET','POST'].includes(req.method))return json(res,405,{error:'method_not_allowed'});
  let body;
  try{
    const dir=directory(root),file=path.join(dir,id+'.bin');
    if(req.method==='GET'){
      let fd;
      try{
        fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
        const stat=fs.fstatSync(fd);
        if(!stat.isFile()||stat.size>MAX)throw new BackupError(409);
        body=fs.readFileSync(fd);
      }finally{if(fd!==undefined)fs.closeSync(fd)}
      envelope(body,id,secret);
      res.writeHead(200,{'content-type':'application/octet-stream','cache-control':'no-store',
        'content-length':body.length,'x-orion-sha256':hash(body)});
      res.end(body);
      return;
    }
    if(req.headers['content-type']!=='application/octet-stream')throw new BackupError(415);
    const expected=req.headers['x-orion-sha256'];
    if(!DIGEST.test(String(expected||'')))throw new BackupError(400);
    const declared=req.headers['content-length'];
    if(declared!==undefined&&(!/^\d+$/.test(declared)||Number(declared)>MAX))throw new BackupError(413);
    const chunks=[];let length=0;
    for await(const chunk of req){
      length+=chunk.length;
      if(length>MAX)throw new BackupError(413);
      chunks.push(chunk);
    }
    body=Buffer.concat(chunks);envelope(body,id,secret);
    if(!timingSafeEqual(Buffer.from(hash(body),'hex'),Buffer.from(expected,'hex')))throw new BackupError(422);
    // Serialize quota/admission and exclusive creation within this process.
    const previous=queues.get(dir)||Promise.resolve();
    let release;const next=new Promise(resolve=>{release=resolve});queues.set(dir,next);
    await previous;
    try{
      let used=0;
      for(const name of fs.readdirSync(dir)){
        const entry=fs.lstatSync(path.join(dir,name));
        if(!entry.isFile()||entry.isSymbolicLink())throw new BackupError(409);
        used+=entry.size;
      }
      const space=fs.statfsSync(dir);
      if(used+body.length>QUOTA||space.bavail*space.bsize<body.length+16*1024*1024)throw new BackupError(507);
      let fd,created;
      try{
        fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|(fs.constants.O_NOFOLLOW||0),0o600);
        created=fs.fstatSync(fd);
        fs.writeFileSync(fd,body);fs.fsyncSync(fd);
        fs.closeSync(fd);fd=undefined;
        if(process.platform!=='win32'){
          const parent=fs.openSync(dir,fs.constants.O_RDONLY);
          try{fs.fsyncSync(parent)}finally{fs.closeSync(parent)}
        }
      }catch(error){
        if(fd!==undefined){try{fs.closeSync(fd)}catch{}fd=undefined}
        // Only an unsuccessful file created by this exact exclusive open may
        // be removed. EEXIST and another inode are never cleanup targets.
        if(created){
          try{
            const now=fs.lstatSync(file);
            if(now.isFile()&&!now.isSymbolicLink()&&now.ino===created.ino&&now.dev===created.dev)fs.unlinkSync(file);
          }catch{}
        }
        throw error;
      }finally{if(fd!==undefined)fs.closeSync(fd)}
    }finally{release();if(queues.get(dir)===next)queues.delete(dir)}
    return json(res,201,{id,sha256:hash(body),size:body.length});
  }catch(error){
    const status=error instanceof BackupError?error.status:error?.code==='EEXIST'?409:error?.code==='ENOENT'?404:503;
    return json(res,status,{error:'database_backup_refused'});
  }
}
