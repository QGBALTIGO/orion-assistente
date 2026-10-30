/** Temporary one-shot, owner-consented diagnostic using the connected QR.
 * One fixed run ID, no public HTTP route, no plaintext PII in this repository.
 * Destination numbers and source guard come only from explicitly set Railway
 * deployment variables. Only two country patterns are accepted.
 */
import fs from 'node:fs';
import path from 'node:path';

export const SMOKE_ID='orion-owned-pair-20261030-v1';

function targets(env){
  const br=env.ORION_SMOKE_BR_E164,py=env.ORION_SMOKE_PY_E164;
  const source=env.ORION_SMOKE_SOURCE_E164;
  if(typeof source!=='string'||!/^55[1-9][0-9]{9,10}$/.test(source)||
     typeof br!=='string'||!/^55[1-9][0-9]{9,10}$/.test(br)||
     typeof py!=='string'||!/^5959[0-9]{8}$/.test(py)||
     br===source||py===source||br===py)return null;
  return [
    {slot:'br',phone:br,text:'🧪 *Orion — teste real de entrega*\n\nEsta mensagem verifica o WhatsApp da recepção, com autorização do responsável. Não é um agendamento e não altera dados.\n\nIdentificador: *ORION-QA-BR*\n\n_Não é necessário responder._'},
    {slot:'py',phone:py,text:'🧪 *Orion — teste real de entrega*\n\nEsta mensagem verifica o WhatsApp da recepção, com autorização do responsável. Não é um agendamento e não altera dados.\n\nIdentificador: *ORION-QA-PY*\n\n_Não é necessário responder._'}
  ];
}

export async function runConsentedPairSmoke({
  env=process.env,directory,pairedPhone,isCurrent=()=>false,
  send,report=()=>{}
}={}){
  if(env.ORION_ONE_TIME_LIVE_SEND!==SMOKE_ID)return {state:'disabled',attempts:0};
  const cases=targets(env);
  if(!cases||pairedPhone!==env.ORION_SMOKE_SOURCE_E164||
     env.QR_RELIABLE_SEND_V2!=='1'||!isCurrent()||
     typeof directory!=='string'||
     (!directory.startsWith('/data/')&&env.ORION_SMOKE_TEST_MODE!=='1')||
     typeof send!=='function'){
    report({slot:'none',state:'source_unavailable'});
    return {state:'source_unavailable',attempts:0};
  }
  const root=path.join(directory,'consented-live-qa');
  fs.mkdirSync(root,{recursive:true,mode:0o700});
  let attempts=0;
  for(const item of cases){
    if(!isCurrent()){report({slot:item.slot,state:'socket_changed'});break;}
    const marker=path.join(root,SMOKE_ID+'-'+item.slot+'.json');
    let fd;
    try{
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
      fs.writeSync(fd,JSON.stringify({v:1,run:SMOKE_ID,slot:item.slot,
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
      report({slot:item.slot,state:'unknown_after_attempt'});
    }
  }
  return {state:'finished',attempts};
}
