/** Durable auth for the preserved central socket. Signal is unchanged.
 * Legacy JSON is migrated in-place only after a verified encrypted backup.
 * Corruption is an error, never permission to silently create a new identity.
 */
import fs from 'node:fs';
import path from 'node:path';
import {createCipheriv,createDecipheriv,hkdfSync,randomBytes} from 'node:crypto';

const MAX=16*1024*1024;
const authName=/^(?:creds|(?:pre-key|session|sender-key|sender-key-memory|app-state-sync-key|app-state-sync-version|lid-mapping)-[^/\\]+)\.json$/;
const journalName='orion-auth-transaction.json';
const failure=()=>Error('auth_storage_unavailable');
const keyFor=(directory,secret)=>{
  if(typeof secret!=='string'||Buffer.byteLength(secret)<32)throw failure();
  return Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.from('orion/baileys/auth/v2'),Buffer.from(path.resolve(directory)),32));
};
function readBytes(file){
  let fd;
  try{
    fd=fs.openSync(file,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
    const st=fs.fstatSync(fd);
    if(!st.isFile()||st.size>MAX)throw failure();
    return fs.readFileSync(fd);
  }finally{if(fd!==undefined)fs.closeSync(fd);}
}
export function decodeAuthRecord(bytes,{directory,secret,name,deserialize=JSON.parse}){
  const envelope=JSON.parse(bytes.toString('utf8'));
  if(!envelope||typeof envelope!=='object')throw failure();
  if(!Object.hasOwn(envelope,'orion_auth_v'))return {value:deserialize(bytes.toString('utf8')),legacy:true};
  if(envelope.orion_auth_v!==2||typeof envelope.iv!=='string'||typeof envelope.tag!=='string'||typeof envelope.data!=='string')throw failure();
  const iv=Buffer.from(envelope.iv,'base64'),tag=Buffer.from(envelope.tag,'base64'),key=keyFor(directory,secret);
  let plain;
  try{
    if(iv.length!==12||tag.length!==16)throw failure();
    const cipher=createDecipheriv('aes-256-gcm',key,iv);
    cipher.setAAD(Buffer.from('orion-auth-v2\0'+name));cipher.setAuthTag(tag);
    plain=Buffer.concat([cipher.update(Buffer.from(envelope.data,'base64')),cipher.final()]);
    return {value:deserialize(plain.toString('utf8')),legacy:false};
  }finally{plain?.fill(0);key.fill(0);}
}
export function useDurableAuthState(directory,{secret,sdk,isCurrent=()=>true,backupVerified=false,onFailure=()=>{}}){
  const dir=path.resolve(directory),key=keyFor(dir,secret);
  fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const st=fs.lstatSync(dir);if(!st.isDirectory()||st.isSymbolicLink())throw failure();
  fs.chmodSync(dir,0o700);
  const serialize=value=>JSON.stringify(value,sdk.BufferJSON.replacer);
  const deserialize=value=>JSON.parse(value,sdk.BufferJSON.reviver);
  let broken=false;
  const guard=()=>{if(broken||!isCurrent())throw failure();};
  const fail=()=>{broken=true;try{onFailure();}catch{}return failure();};
  const recordPath=name=>{
    if(name!==journalName&&!authName.test(name))throw failure();
    return path.join(dir,name);
  };
  function read(name){
    let bytes;
    try{bytes=readBytes(recordPath(name));}catch(error){if(error.code==='ENOENT')return null;throw error;}
    try{return decodeAuthRecord(bytes,{directory:dir,secret,name,deserialize});}finally{bytes.fill(0);}
  }
  function write(name,value){
    guard();const target=recordPath(name),tmp=target+'.'+randomBytes(8).toString('hex')+'.tmp';
    const plain=Buffer.from(serialize(value)),iv=randomBytes(12);let fd;
    try{
      if(plain.length>MAX/2)throw failure();
      if(fs.existsSync(target)&&!fs.lstatSync(target).isFile())throw failure();
      const cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from('orion-auth-v2\0'+name));
      const data=Buffer.concat([cipher.update(plain),cipher.final()]);
      const bytes=JSON.stringify({orion_auth_v:2,iv:iv.toString('base64'),tag:cipher.getAuthTag().toString('base64'),data:data.toString('base64')});
      fd=fs.openSync(tmp,'wx',0o600);fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      guard();fs.renameSync(tmp,target);
      const parent=fs.openSync(dir,'r');try{fs.fsyncSync(parent);}finally{fs.closeSync(parent);}
    }finally{plain.fill(0);if(fd!==undefined)fs.closeSync(fd);fs.rmSync(tmp,{force:true});}
  }
  const fileFor=(type,id)=>{
    if(typeof type!=='string'||typeof id!=='string'||!id||id.length>1024)throw failure();
    return (type+'-'+id+'.json').replace(/\//g,'__').replace(/:/g,'-');
  };
  try{
    guard();
    const pending=read(journalName);
    if(pending?.value){
      if(pending.legacy||!Array.isArray(pending.value)||pending.value.length>10000)throw failure();
      for(const item of pending.value){if(!item||!authName.test(item.name)||item.name==='creds.json')throw failure();write(item.name,item.value);}
      write(journalName,null);
    }
    const names=fs.readdirSync(dir).filter(name=>authName.test(name));
    const stored=read('creds.json');
    if(!stored&&names.length)throw failure();
    if(stored&&(!stored.value?.noiseKey||!stored.value?.signedIdentityKey))throw failure();
    // Validate everything first. No partial migration on a malformed input.
    const migration=[];
    for(const name of names){const row=read(name);if(row.legacy)migration.push({name,value:row.value});}
    if(migration.length&&!backupVerified)throw failure();
    for(const row of migration)write(row.name,row.value);
    const creds=stored?.value||sdk.initAuthCreds();
    const saveCreds=()=>{try{guard();write('creds.json',creds);}catch{throw fail();}};
    if(!stored)saveCreds();
    return {state:{creds,keys:{
      async get(type,ids){
        try{guard();return Object.fromEntries(ids.map(id=>{
          let value=read(fileFor(type,id))?.value??null;
          if(type==='app-state-sync-key'&&value)value=sdk.proto.Message.AppStateSyncKeyData.fromObject(value);
          return [id,value];
        }));}catch{throw fail();}
      },
      async set(data){
        // No await between lease validation, journal and atomic file commits.
        try{
          guard();const batch=[];
          for(const [type,values] of Object.entries(data))for(const [id,value] of Object.entries(values))batch.push({name:fileFor(type,id),value:value??null});
          if(batch.length>10000)throw failure();
          for(const row of batch)recordPath(row.name);
          if(!batch.length)return;
          write(journalName,batch);for(const row of batch)write(row.name,row.value);write(journalName,null);
        }catch{throw fail();}
      }
    }},saveCreds};
  }catch{key.fill(0);throw fail();}
}
