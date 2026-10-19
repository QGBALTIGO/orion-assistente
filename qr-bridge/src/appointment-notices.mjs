/** Durable idempotency for transactional notices. No automatic replay after an ambiguous relay.
 * The journal contains identifiers and a fingerprint, never the customer's message body.
 * Keep it outside the rotating auth directory. A full or unreadable journal fails closed.
 */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,createCipheriv,createDecipheriv,randomBytes} from 'node:crypto';
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const pn=/^[1-9][0-9]{7,14}$/;
const states=new Set(['sending','sent','delivered','read','uncertain','not_sent']);
export function makeAppointmentNotices({directory,secret,state,send,now=Date.now,max=50000}){
  if(typeof secret!=='string'||secret.length<32||typeof state!=='function'||typeof send!=='function')throw Error('notice_configuration_invalid');
  const key=createHash('sha256').update('orion/appointment-notices/v1\0'+secret).digest();
  const inFlight=new Map(),byMessage=new Map();let broken=false;
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const file=id=>path.join(directory,id+'.enc');
  function read(id){
    try{
      const data=fs.readFileSync(file(id));if(data.length>8192)throw Error();
      const decipher=createDecipheriv('aes-256-gcm',key,data.subarray(0,12));
      decipher.setAAD(Buffer.from(id));decipher.setAuthTag(data.subarray(12,28));
      const row=JSON.parse(Buffer.concat([decipher.update(data.subarray(28)),decipher.final()]).toString('utf8'));
      if(row.id!==id||!states.has(row.status)||!uuid.test(row.tenant_id)||!pn.test(row.account_phone))throw Error();
      return row;
    }catch(e){if(e.code==='ENOENT')return null;broken=true;throw Error('storage_unavailable');}
  }
  function write(row){
    const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);cipher.setAAD(Buffer.from(row.id));
    const encrypted=Buffer.concat([cipher.update(JSON.stringify(row),'utf8'),cipher.final()]);
    const tmp=file(row.id)+'.'+randomBytes(8).toString('hex')+'.tmp';
    let fd;
    try{fd=fs.openSync(tmp,'wx',0o600);fs.writeFileSync(fd,Buffer.concat([iv,cipher.getAuthTag(),encrypted]));fs.fsyncSync(fd);fs.closeSync(fd);fd=null;fs.renameSync(tmp,file(row.id));const dir=fs.openSync(directory,'r');try{fs.fsyncSync(dir)}finally{fs.closeSync(dir)}}
    catch{broken=true;throw Error('storage_unavailable')}
    finally{if(fd!==undefined&&fd!==null)fs.closeSync(fd);try{fs.unlinkSync(tmp)}catch{}}
    if(row.message_id)byMessage.set(row.message_id,row.id);
  }
  const files=fs.readdirSync(directory).filter(x=>uuid.test(x.slice(0,-4))&&x.endsWith('.enc'));
  if(files.length>max)broken=true;
  if(!broken)for(const name of files){try{const r=read(name.slice(0,-4));if(r?.message_id)byMessage.set(r.message_id,r.id)}catch{break}}
  function publicRow(row){return {id:row.id,tenant_id:row.tenant_id,account_phone:row.account_phone,status:row.status,message_id:row.message_id||null}}
  function readiness(){const current=state();return {ready:!broken&&current.enabled&&current.connected&&uuid.test(current.tenant_id||'')&&pn.test(current.account_phone||''),tenant_id:current.tenant_id||null,account_phone:current.account_phone||null,error:broken?'storage_unavailable':!current.enabled?'channel_not_configured':!current.connected?'not_connected':null}}
  function scope(payload){const current=state();if(!payload||!uuid.test(payload.id||'')||payload.tenant_id!==current.tenant_id||!pn.test(payload.account_phone||''))throw Error('notice_scope_invalid');return current}
  function receipt(payload){scope(payload);const row=read(payload.id);if(!row)return {id:payload.id,status:'unknown'};if(row.tenant_id!==payload.tenant_id||row.account_phone!==payload.account_phone)throw Error('notice_scope_invalid');return publicRow(row)}
  async function dispatch(payload){
    const current=scope(payload);
    if(Object.keys(payload).sort().join(',')!=='account_phone,expires_at,id,phone,tenant_id,text'||!pn.test(payload.phone||'')||typeof payload.text!=='string'||!payload.text.trim()||payload.text.length>1200||!Number.isSafeInteger(payload.expires_at))throw Error('notice_payload_invalid');
    const fingerprint=createHash('sha256').update(JSON.stringify([payload.tenant_id,payload.account_phone,payload.phone,payload.text,payload.expires_at])).digest('hex');
    const existing=read(payload.id);
    if(existing&&existing.fingerprint!==fingerprint)throw Error('notice_key_conflict');
    if(inFlight.has(payload.id))return inFlight.get(payload.id);
    if(existing&&existing.status!=='not_sent')return publicRow(existing);
    if(broken)throw Error('storage_unavailable');
    if(!current.enabled||!current.connected)throw Error('not_connected');
    if(current.account_phone!==payload.account_phone)throw Error('account_changed');
    if(payload.expires_at<=now()||payload.expires_at>now()+48*3600000)throw Error('notice_expired');
    if(!existing&&fs.readdirSync(directory).filter(x=>x.endsWith('.enc')).length>=max)throw Error('storage_unavailable');
    const row={id:payload.id,tenant_id:payload.tenant_id,account_phone:payload.account_phone,fingerprint,status:'sending',created_at:now(),message_id:null};
    write(row); // Durable claim BEFORE any transport call.
    const valid=()=>{const c=state();return c.enabled&&c.connected&&c.account_phone===row.account_phone&&c.tenant_id===row.tenant_id&&now()<payload.expires_at};
    let prepared=false;
    const task=(async()=>{
      try{
        const result=await send(payload.phone,payload.text,{forceReliable:true,isCurrent:valid,onNoticePrepared:message=>{
          prepared=true;row.message_id=message.key.id;write(row);
        }});
        // Receipts can precede the HTTP result. Never overwrite delivered/read with sent.
        const latest=read(row.id)||row;
        if(!['delivered','read'].includes(latest.status))latest.status=result?.accepted===true?'sent':'uncertain';
        if(result?.id)latest.message_id=String(result.id).replace(/^out:/,'');
        write(latest);return publicRow(latest);
      }catch{
        const latest=read(row.id)||row;
        if(!['delivered','read'].includes(latest.status))latest.status=prepared?'uncertain':'not_sent';
        write(latest);return publicRow(latest);
      }
    })();
    inFlight.set(payload.id,task);
    try{return await task}finally{inFlight.delete(payload.id)}
  }
  function observe(messageId,delivery){
    const id=byMessage.get(messageId);if(!id)return;
    const status={server_accepted:'sent',delivered:'delivered',read:'read',played:'read'}[delivery];if(!status)return;
    try{const row=read(id);const rank={not_sent:0,sending:0,uncertain:0,sent:1,delivered:2,read:3};if(row&&rank[status]>rank[row.status])write({...row,status})}catch{broken=true}
  }
  return {readiness,dispatch,receipt,observe};
}
