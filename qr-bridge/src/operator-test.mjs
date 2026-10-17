/** Explicit operator-only probe. No listener, public route, or default startup work.
 * Enable ONLY with --import ./src/operator-test.mjs AND QR_OPERATOR_TEST_ENABLED=1.
 * The separate /data ledger is permanent: never delete it or change a marker to
 * retry an ambiguous send. Receipts do not establish handset readability.
 */
import fs from 'node:fs';
import path from 'node:path';
import {createHash,createHmac} from 'node:crypto';
import {pilotNumber} from './ai-pilot.mjs';

const states=new Set(['pending','server_accepted','delivered','read','played','failed']);
const terminal=new Set(['delivered','read','played','failed']);
const hash=value=>createHash('sha256').update(value).digest('hex');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const validId=id=>typeof id==='string'&&/^out:[A-Za-z0-9._:-]{1,160}$/.test(id);

// An existing file, including a corrupt/partial reservation, can NEVER send.
export function createOperatorLedger(directory,marker){
  if(process.platform==='win32')throw Error('posix_storage_required');
  if(!/^ORION-[A-Za-z0-9][A-Za-z0-9._-]{0,89}$/.test(marker)||!path.isAbsolute(directory))throw Error('invalid_ledger');
  try{
    fs.mkdirSync(directory,{mode:0o700});
    const parent=fs.openSync(path.dirname(directory),'r');
    try{fs.fsyncSync(parent)}finally{fs.closeSync(parent)}
  }catch(error){if(error.code!=='EEXIST')throw error}
  const dir=fs.lstatSync(directory);
  if(fs.realpathSync(directory)!==directory||!dir.isDirectory()||(dir.mode&0o777)!==0o700||dir.uid!==process.geteuid())throw Error('unsafe_directory');
  const target=path.join(directory,marker+'.json');
  let created=true,fd;
  try{fd=fs.openSync(target,fs.constants.O_RDWR|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600)}
  catch(error){
    if(error.code!=='EEXIST')throw error;
    created=false;fd=fs.openSync(target,fs.constants.O_RDWR|fs.constants.O_NOFOLLOW);
  }
  try{
    const info=fs.fstatSync(fd);
    if(!info.isFile()||(info.mode&0o777)!==0o600||info.uid!==process.geteuid()||info.nlink!==1)throw Error('unsafe_file');
    const dirFd=fs.openSync(directory,'r');
    try{fs.fsyncSync(dirFd)}finally{fs.closeSync(dirFd)}
    const data=created?{}:JSON.parse(fs.readFileSync(fd,'utf8'));
    if(!data||typeof data!=='object'||Array.isArray(data))throw Error('invalid_ledger');
    return {created,data,save(){
      const bytes=Buffer.from(JSON.stringify(this.data)+'\n');
      let offset=0;
      while(offset<bytes.length)offset+=fs.writeSync(fd,bytes,offset,bytes.length-offset,offset);
      fs.ftruncateSync(fd,bytes.length);fs.fsyncSync(fd);
    },close(){fs.closeSync(fd)}};
  }catch(error){fs.closeSync(fd);throw error}
}

export async function runOperatorTest({env=process.env,fetchImpl=globalThis.fetch,sleep=delay,
  directory='/data/orion-operator-tests',ledgerFactory=createOperatorLedger,now=Date.now,
  emit=event=>console.info('ORION_OPERATOR_TEST '+JSON.stringify(event))}={}){
  if(env.QR_OPERATOR_TEST_ENABLED!=='1')return {stage:'disabled'};
  const marker=env.QR_OPERATOR_TEST_MARKER||'',phone=env.QR_OPERATOR_TEST_PHONE||'',key=env.QR_BRIDGE_TOKEN||'';
  const port=Number(env.PORT||3000),started=now();
  const output=(stage,extra={})=>{const event={marker,stage,elapsedMs:Math.max(0,now()-started),...extra};emit(event);return event};
  if(!/^ORION-[A-Za-z0-9][A-Za-z0-9._-]{0,89}$/.test(marker)||!phone||phone!==pilotNumber(env)||
    Buffer.byteLength(key)<32||!Number.isInteger(port)||port<1||port>65535){
    emit({stage:'configuration_refused'});return {stage:'configuration_refused'};
  }
  const trace=id=>validId(id)?createHmac('sha256',key).update('orion-trace-v1:'+id.slice(4)).digest('hex').slice(0,20):null;
  async function request(route,method='GET',body,timeoutMs=15000){
    const response=await fetchImpl(`http://127.0.0.1:${port}${route}`,{method,redirect:'error',
      headers:{'X-Orion-Bridge-Key':key,'Content-Type':'application/json'},
      signal:AbortSignal.timeout(Math.max(1,Math.ceil(timeoutMs))),body:body?JSON.stringify(body):undefined});
    if(!response.ok)throw Error('request_failed');
    return response.json();
  }
  const getRows=async(timeoutMs=15000)=>{
    const snapshot=await request('/internal/messages?phone='+encodeURIComponent(phone),'GET',undefined,timeoutMs);
    if(!Array.isArray(snapshot?.items))throw Error('invalid_history');
    return snapshot.items.filter(row=>row?.phone===phone&&row.direction==='out'&&row.body===marker);
  };
  let ledger;
  try{
    ledger=ledgerFactory(directory,marker);
    const data=ledger.data;
    if(ledger.created){
      Object.assign(data,{version:1,marker,phoneHash:hash(phone),stage:'reserved',createdAt:now(),handsetReadability:'unverified'});
      ledger.save();
    }else if(data.version!==1||data.marker!==marker||data.phoneHash!==hash(phone))throw Error('ledger_identity_mismatch');
    if(ledger.created){
      let status;
      const deadline=now()+45000;
      while(now()<deadline){
        try{status=await request('/internal/status','GET',undefined,Math.min(15000,deadline-now()))}catch{}
        if(status?.status==='connected')break;
        if(now()<deadline)await sleep(Math.min(3000,deadline-now()));
      }
      if(status?.status!=='connected')throw Error('not_connected');
      if(typeof status.phone!=='string'||!/^[1-9][0-9]{7,14}$/.test(status.phone)||status.phone===phone)throw Error('self_or_unknown_account');
      const recipient=await request('/internal/recipient?phone='+encodeURIComponent(phone));
      if(recipient?.registered!==true||recipient.identity!=='pn_confirmed'||recipient.self!==false)throw Error('recipient_unverified');
      output('identity_verified',{connected:true,self:false,reliableSendEnabled:status.reliableSendEnabled===true});
      const prior=await getRows();
      if(prior.length>1)throw Error('duplicate_matches');
      if(prior.length===1){
        if(!validId(prior[0].id))throw Error('invalid_message_id');
        Object.assign(data,{id:prior[0].id,stage:'existing_observation_only'});ledger.save();
      }else{
        Object.assign(data,{stage:'attempting',attemptedAt:now()});ledger.save();
        output('attempting');
        try{
          const result=await request('/internal/send','POST',{phone,text:marker});
          if(result?.accepted===true&&validId(result.id)){
            Object.assign(data,{stage:'accepted_unverified',id:result.id,
              trace:/^[a-f0-9]{20}$/.test(result.trace||'')?result.trace:trace(result.id)});
          }else data.stage='ambiguous_never_resend';
        }catch{data.stage='ambiguous_never_resend'}
        ledger.save();output(data.stage,{trace:data.trace||null});
      }
    }else output('existing_ledger_observation_only',{trace:trace(data.id)});
    const deadline=now()+60000;
    while(now()<=deadline){
      let rows;
      try{rows=await getRows(Math.min(15000,Math.max(1,deadline-now())))}catch{output('observation_unavailable')}
      if(rows){
        if(rows.length>1)throw Error('duplicate_matches');
        const row=rows[0];
        if(row){
          if(!validId(row.id)||(data.id&&data.id!==row.id))throw Error('message_identity_mismatch');
          data.id=row.id;data.trace=trace(row.id);
          if(states.has(row.delivery)){
            data.receipt=row.delivery;data.observedAt=now();ledger.save();
            output('receipt',{trace:data.trace,receipt:row.delivery,
              deliveryElapsedMs:data.attemptedAt?Math.max(0,now()-data.attemptedAt):null});
            if(terminal.has(row.delivery))return output('observation_complete',{trace:data.trace,receipt:row.delivery,handsetReadability:'unverified'});
          }
        }
      }
      if(now()>=deadline)break;
      await sleep(Math.min(3000,deadline-now()));
    }
    return output('receipt_unconfirmed_never_resend',{trace:trace(data.id),receipt:data.receipt||'unobserved'});
  }catch{
    return output('stopped_never_resend');
  }finally{ledger?.close()}
}

// Preload schedules work after the main server starts; imports in tests are inert.
const args=process.execArgv;
const preloaded=args.some((arg,index)=>arg==='--import'&&args[index+1]?.replaceAll('\\','/').endsWith('/operator-test.mjs')||
  arg.startsWith('--import=')&&arg.replaceAll('\\','/').endsWith('/operator-test.mjs'));
if(preloaded&&process.env.QR_OPERATOR_TEST_ENABLED==='1'){
  setImmediate(()=>{runOperatorTest().catch(()=>console.info('ORION_OPERATOR_TEST {"stage":"stopped_never_resend"}'))});
}
