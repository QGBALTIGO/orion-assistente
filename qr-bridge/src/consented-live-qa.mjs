/** One-shot, owner-authorized, exactly-two-recipient linked WhatsApp smoke.
 *
 * No public endpoint, no model input, no customer or booking lookup. Nothing
 * runs unless the explicit exact-match deployment flag is enabled AND the
 * currently paired verified Admin phone matches the expected source.
 *
 * An fsynced exclusive marker is written BEFORE each attempt. A timeout,
 * process crash or Railway redeploy cannot create a duplicate send.
 * Never logs phone, JID, body, provider ID, secret, or session files.
 */
import fs from 'node:fs';
import path from 'node:path';

export const SMOKE_ID = 'orion-owned-pair-20261030-v1';
const EXPECTED_ADMIN = '556781030648';
const CASES = Object.freeze([
  Object.freeze({
    slot:'br',
    phone:'5521997557364',
    text:'🧪 *Orion — teste real do WhatsApp*\n\nVerificação de entrega autorizada pelo responsável do Orion. Esta mensagem não cria nem altera agendamentos.\n\nIdentificador: *ORION-QA-BR-30OUT*\n\n_Não é necessário responder._'
  }),
  Object.freeze({
    slot:'py',
    phone:'595992820210',
    text:'🧪 *Orion — teste real do WhatsApp*\n\nVerificação de entrega autorizada pelo responsável do Orion. Esta mensagem não cria nem altera agendamentos.\n\nIdentificador: *ORION-QA-PY-30OUT*\n\n_Não é necessário responder._'
  })
]);

export async function runConsentedPairSmoke({
  env=process.env, directory, pairedPhone, isCurrent=()=>false,
  send, report=()=>{}
}={}){
  if(env.ORION_ONE_TIME_LIVE_SEND!==SMOKE_ID)return {state:'disabled',attempts:0};
  // Fail closed on a changed sender account, disabled authenticated cache,
  // missing durable volume, or unavailable connected socket.
  if(pairedPhone!==EXPECTED_ADMIN||env.QR_RELIABLE_SEND_V2!=='1'||
     !isCurrent()||typeof directory!=='string'||
     (!directory.startsWith('/data/')&&env.ORION_SMOKE_TEST_MODE!=='1')||
     typeof send!=='function'){
    report({slot:'none',state:'source_unavailable'});
    return {state:'source_unavailable',attempts:0};
  }
  const root=path.join(directory,'consented-live-qa');
  fs.mkdirSync(root,{recursive:true,mode:0o700});
  let attempts=0;
  for(const item of CASES){
    if(!isCurrent()){report({slot:item.slot,state:'socket_changed'});break;}
    const marker=path.join(root,SMOKE_ID+'-'+item.slot+'.json');
    let fd;
    try{
      // A durable, exclusive claim is the idempotency fence; retries and
      // later restarts MUST skip this phone even when the send result was
      // ambiguous. Mark before any WhatsApp packet is transmitted.
      fd=fs.openSync(marker,'wx',0o600);
    }catch(error){
      if(error?.code==='EEXIST'){
        report({slot:item.slot,state:'already_attempted'});
        continue;
      }
      report({slot:item.slot,state:'storage_unavailable'});
      continue;
    }
    let marked=false;
    try{
      fs.writeSync(fd,JSON.stringify({version:1,run:SMOKE_ID,slot:item.slot,
        claimed_at:new Date().toISOString()}));
      fs.fsyncSync(fd);marked=true;
    }catch{
      report({slot:item.slot,state:'storage_unavailable'});
    }finally{
      fs.closeSync(fd);
      if(!marked)try{fs.rmSync(marker,{force:true})}catch{}
    }
    if(!marked)continue;
    attempts++;
    if(!isCurrent()){
      report({slot:item.slot,state:'socket_changed_after_claim'});
      break;
    }
    report({slot:item.slot,state:'attempting'});
    try{
      const response=await send(item.phone,item.text,{
        waitForCapacity:true,transactionalTyping:true,isCurrent
      });
      report({slot:item.slot,state:response?.accepted===true?'accepted':'unconfirmed',
        delivery:typeof response?.delivery==='string'?response.delivery:'unknown',
        ...(typeof response?.trace==='string'?{trace:response.trace}:{})});
    }catch{
      // No resend. An exception may occur after the WhatsApp server accepted.
      report({slot:item.slot,state:'unknown_after_attempt'});
    }
  }
  return {state:'finished',attempts};
}
