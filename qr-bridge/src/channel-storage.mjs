/** Private per-channel storage. No existing /data/auth directory is accessed. */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,createCipheriv,createDecipheriv,hkdfSync,randomBytes} from 'node:crypto';

export const channelId=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
export const digest=value=>createHash('sha256').update(value).digest('hex');
export function privateDirectory(directory,{create=true}={}){
  if(create)fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const stat=fs.lstatSync(directory);
  if(!stat.isDirectory()||stat.isSymbolicLink())throw Error('unsafe_channel_storage');
  if(process.platform!=='win32'&&(stat.mode&0o077)!==0)throw Error('unsafe_channel_storage');
  return directory;
}
export function channelDirectory(root,tenant,id,options){
  if(!channelId(tenant)||!channelId(id))throw Error('invalid_channel_identity');
  const base=path.resolve(root);
  if(base===path.resolve('/data/auth')||base.startsWith(path.resolve('/data/auth')+path.sep))throw Error('legacy_storage_forbidden');
  privateDirectory(base,options);
  const owner=privateDirectory(path.join(base,tenant),options);
  return privateDirectory(path.join(owner,id),options);
}
function readPrivate(file){
  const stat=fs.lstatSync(file);
  if(!stat.isFile()||stat.isSymbolicLink()||stat.size>16*1024*1024||
      (process.platform!=='win32'&&(stat.mode&0o077)!==0))throw Error('unsafe_channel_file');
  const fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
  try{return fs.readFileSync(fd);}finally{fs.closeSync(fd);}
}
function writePrivate(file,bytes,{exclusive=false}={}){
  const target=exclusive?file:file+'.'+randomBytes(8).toString('hex')+'.tmp';
  const fd=fs.openSync(target,'wx',0o600);
  try{fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  if(!exclusive){
    if(fs.existsSync(file))readPrivate(file);
    fs.renameSync(target,file);
  }
  if(process.platform!=='win32'){
    const dir=fs.openSync(path.dirname(file),'r');try{fs.fsyncSync(dir);}finally{fs.closeSync(dir);}
  }
}
export function encryptedChannelStore({directory,secret,serialize=JSON.stringify,deserialize=JSON.parse,readOnly=false}){
  privateDirectory(directory,{create:!readOnly});
  const key=Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.from('orion-channel-v1'),Buffer.from(directory),32));
  const file=id=>path.join(directory,digest(id)+'.enc');
  return {
    get(id){
      let bytes;try{bytes=readPrivate(file(id));}catch(e){if(e.code==='ENOENT')return null;throw e;}
      const decipher=createDecipheriv('aes-256-gcm',key,bytes.subarray(0,12));
      decipher.setAAD(Buffer.from(id));decipher.setAuthTag(bytes.subarray(12,28));
      return deserialize(Buffer.concat([decipher.update(bytes.subarray(28)),decipher.final()]).toString());
    },
    set(id,value,options){
      if(readOnly)throw Error('read_only_channel_store');
      const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from(id));
      const payload=Buffer.concat([cipher.update(serialize(value)),cipher.final()]);
      writePrivate(file(id),Buffer.concat([iv,cipher.getAuthTag(),payload]),options);
    }
  };
}
export function sendLedger(directory,secret,{readOnly=false}={}){
  const store=encryptedChannelStore({directory:path.join(directory,'send-ledger'),secret,readOnly});
  return {
    get(id){if(!channelId(id))throw Error('invalid_operation_id');return store.get(id);},
    reserve(id,fingerprint){
      if(!channelId(id))throw Error('invalid_operation_id');
      const record={fingerprint,state:'uncertain',accepted:false,delivery:'unknown'};
      try{store.set(id,record,{exclusive:true});return {fresh:true,record};}
      catch(error){if(error.code!=='EEXIST')throw error;}
      const old=store.get(id);
      if(!old||old.fingerprint!==fingerprint)throw Error('operation_conflict');
      return {fresh:false,record:old};
    },
    save(id,record){store.set(id,record);}
  };
}
