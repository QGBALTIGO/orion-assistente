import fs from 'node:fs';
import path from 'node:path';
import {createCipheriv,createDecipheriv,hkdfSync,randomBytes,createHash} from 'node:crypto';

const keyFor=secret=>Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.from('orion-auth-backup-v1'),Buffer.from('aes-256-gcm'),32));
export function readAuthBackup(file,secret){
  const record=fs.readFileSync(file);
  if(record.length<29)throw Error('Invalid auth backup');
  const decipher=createDecipheriv('aes-256-gcm',keyFor(secret),record.subarray(0,12));
  decipher.setAAD(Buffer.from('orion-auth-backup-v1'));
  decipher.setAuthTag(record.subarray(12,28));
  const payload=JSON.parse(Buffer.concat([decipher.update(record.subarray(28)),decipher.final()]).toString());
  if(payload.v!==1||!Array.isArray(payload.files))throw Error('Invalid auth backup');
  for(const item of payload.files){
    if(path.basename(item.name)!==item.name||!item.name.endsWith('.json'))throw Error('Invalid backup path');
    if(createHash('sha256').update(Buffer.from(item.data,'base64')).digest('hex')!==item.sha256)throw Error('Invalid backup checksum');
  }
  return payload;
}

// Call BEFORE opening any socket, with a stopped previous volume writer.
// Raw auth JSON remains byte-for-byte unchanged. Nothing is deleted or restored.
export function backupAuth({directory,backupDirectory,secret,label}){
  if(typeof secret!=='string'||secret.length<32||!/^[-a-z0-9]{1,80}$/.test(label))throw Error('Invalid backup configuration');
  const credentials=path.join(directory,'creds.json');
  if(!fs.existsSync(credentials))return {state:'no_existing_auth',files:0};
  const parsed=JSON.parse(fs.readFileSync(credentials,'utf8'));
  if(!parsed.signedIdentityKey||!parsed.noiseKey)throw Error('Invalid existing credentials; refusing fresh authentication');
  fs.mkdirSync(backupDirectory,{recursive:true,mode:0o700});
  const scope=createHash('sha256').update(directory).digest('hex').slice(0,12);
  const target=path.join(backupDirectory,label+'-'+scope+'.enc');
  if(fs.existsSync(target)){
    const saved=readAuthBackup(target,secret);
    if(saved.directory!==directory)throw Error('Backup belongs to different auth directory');
    return {state:'verified_existing',files:saved.files.length,label};
  }
  let size=0;
  const files=[];
  for(const name of fs.readdirSync(directory).filter(x=>x.endsWith('.json')).sort()){
    const file=path.join(directory,name),stat=fs.lstatSync(file);
    if(!stat.isFile()||stat.isSymbolicLink())throw Error('Unexpected auth file');
    size+=stat.size;if(size>16*1024*1024)throw Error('Auth backup exceeds safety limit');
    const data=fs.readFileSync(file);
    JSON.parse(data.toString());
    files.push({name,data:data.toString('base64'),sha256:createHash('sha256').update(data).digest('hex')});
  }
  const payload={v:1,directory,createdAt:new Date().toISOString(),files};
  const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',keyFor(secret),iv);
  cipher.setAAD(Buffer.from('orion-auth-backup-v1'));
  const ciphertext=Buffer.concat([cipher.update(JSON.stringify(payload)),cipher.final()]);
  const temp=target+'.'+randomBytes(8).toString('hex')+'.tmp';
  const fd=fs.openSync(temp,'wx',0o600);
  try{fs.writeFileSync(fd,Buffer.concat([iv,cipher.getAuthTag(),ciphertext]));fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  readAuthBackup(temp,secret);
  fs.renameSync(temp,target);
  return {state:'created_verified',files:files.length,label};
}
