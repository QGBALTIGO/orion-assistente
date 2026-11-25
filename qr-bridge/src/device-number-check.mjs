/** Read-only, bounded WhatsApp registration queries on an existing device.
 * No message, presence, contact import or socket lifecycle operation is used. */
import {channelId} from './channel-storage.mjs';

export const NUMBER_CHECK_LIMITS=Object.freeze({numbers:1000,batch:50,request_bytes:24576,
  batch_timeout_ms:8000,timeout_ms:25000});
export const NUMBER_CHECK_REASONS=Object.freeze(['invalid_request','read_busy','context_changed',
  'provider_timeout','provider_failed','provider_result_unverified','deadline_exceeded']);
const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const unavailable=reason=>Object.assign(Error('number_check_unavailable'),{
  numberCheckReason:NUMBER_CHECK_REASONS.includes(reason)?reason:'provider_failed'
});
const phone=value=>typeof value==='string'&&/^\+[1-9]\d{7,14}$/.test(value);
const scope=value=>({device_id:value.device_id,generation:value.generation,settings_revision:value.settings_revision});

export function validNumberCheckRequest(input){
  const fields=['device_id','generation','settings_revision','numbers'];
  return record(input)&&Object.keys(input).length===fields.length&&fields.every(key=>Object.hasOwn(input,key))&&
    channelId(input.device_id)&&channelId(input.generation)&&Number.isSafeInteger(input.settings_revision)&&
    input.settings_revision>=1&&Array.isArray(input.numbers)&&input.numbers.length>=1&&
    input.numbers.length<=NUMBER_CHECK_LIMITS.numbers&&Array.from(input.numbers).every(phone);
}

function registrationResults(numbers,value){
  // Missing rows never prove absence. The production adapter preserves the
  // explicit contact=false response that onWhatsApp otherwise filters away.
  if(!Array.isArray(value)||value.length>numbers.length)return null;
  const requested=new Set(numbers),found=new Map();
  for(const row of value){
    if(!record(row)||typeof row.jid!=='string'||row.exists!==undefined&&typeof row.exists!=='boolean')return null;
    const match=row.jid.match(/^([1-9]\d{7,14})(?::\d{1,5})?@s\.whatsapp\.net$/);
    const number=match?'+'+match[1]:null;
    // Never infer a phone from a LID or associate a returned different phone
    // with a request by position. An unverified batch has no negative verdicts.
    if(!number||!requested.has(number)||found.has(number))return null;
    found.set(number,row.exists);
  }
  return numbers.map(number=>({number,status:found.get(number)===true?'registered':found.get(number)===false?'not_registered':'inconclusive',
    reason:typeof found.get(number)==='boolean'?null:'provider_result_unverified'}));
}

/** A private USync contact query using the installed SDK, preserving explicit
 * negatives and missing rows. This does not change onWhatsApp for any caller. */
export function createNumberCheckLookup({client,USyncQuery,USyncUser}={}){
  return async numbers=>{
    if(typeof client?.executeUSyncQuery!=='function'||typeof USyncQuery!=='function'||typeof USyncUser!=='function')
      throw unavailable('provider_failed');
    const query=new USyncQuery().withContactProtocol();
    const contact=query.protocols.find(protocol=>protocol.name==='contact'),parse=contact?.parser;
    if(typeof parse!=='function')throw unavailable('provider_failed');
    // Baileys normally maps every type other than "in" to false. Only the
    // protocol's explicit "out" may become a negative registration verdict.
    contact.parser=node=>node?.tag==='contact'&&['in','out'].includes(node.attrs?.type)?parse(node):null;
    for(const number of numbers)query.withUser(new USyncUser().withPhone(number));
    const result=await client.executeUSyncQuery(query);
    if(!record(result)||!Array.isArray(result.list)||result.list.length>numbers.length)return undefined;
    return result.list.map(row=>record(row)?{jid:row.id,exists:row.contact}:null);
  };
}

export function createDeviceNumberCheck({client,lookupNumbers,isCurrent,isConnected,deviceSnapshot,schedule=setTimeout,cancel=clearTimeout,
  timeoutMs=NUMBER_CHECK_LIMITS.timeout_ms,batchTimeoutMs=NUMBER_CHECK_LIMITS.batch_timeout_ms,
  batchSize=NUMBER_CHECK_LIMITS.batch}={}){
  if(lookupNumbers!==undefined&&typeof lookupNumbers!=='function'||
      !Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>NUMBER_CHECK_LIMITS.timeout_ms||
      !Number.isSafeInteger(batchTimeoutMs)||batchTimeoutMs<1||batchTimeoutMs>NUMBER_CHECK_LIMITS.batch_timeout_ms||
      !Number.isSafeInteger(batchSize)||batchSize<1||batchSize>NUMBER_CHECK_LIMITS.batch)throw unavailable('invalid_request');
  let busy=false;
  return async function check(input,{authorize}={}){
    if(!validNumberCheckRequest(input)||typeof authorize!=='function')throw unavailable('invalid_request');
    if(busy)throw unavailable('read_busy');
    const expected=scope(input),numbers=[...input.numbers],unique=[...new Set(numbers)],results=new Map();
    let expired=false,workDone=false,pending=0,deadlineTimer;
    const contextCurrent=()=>{
      const device=deviceSnapshot?.();
      return isCurrent?.()===true&&isConnected?.()===true&&device?.id===expected.device_id&&
        device.generation===expected.generation&&device.settings_revision===expected.settings_revision;
    };
    const guard=async()=>{
      if(!contextCurrent())throw unavailable('context_changed');
      if(expired)throw unavailable('deadline_exceeded');
      try{await authorize();}catch{throw unavailable('context_changed');}
      if(!contextCurrent())throw unavailable('context_changed');
      if(expired)throw unavailable('deadline_exceeded');
    };
    const project=(reason='deadline_exceeded')=>({...expected,total:numbers.length,
      results:numbers.map(number=>results.get(number)||{number,status:'inconclusive',reason})});
    const release=()=>{if(workDone&&pending===0)busy=false;};
    const lookup=async batch=>{
      let timer;
      pending++;
      // Keep the read slot while a provider query is unsettled. Returning a
      // timeout cannot make a second request accumulate more live queries.
      const provider=Promise.resolve().then(()=>{
        if(expired||!contextCurrent())throw unavailable('context_changed');
        return lookupNumbers?lookupNumbers(batch):client.onWhatsApp(...batch.map(number=>number.slice(1)));
      });
      provider.then(()=>{pending--;release();},()=>{pending--;release();});
      const deadline=new Promise(resolve=>{timer=schedule(()=>resolve({kind:'timeout'}),batchTimeoutMs);});
      try{return await Promise.race([provider.then(value=>({kind:'result',value}),()=>({kind:'failure'})),deadline]);}
      finally{cancel(timer);}
    };
    busy=true;
    const work=(async()=>{
      try{
        await guard();
        if(typeof lookupNumbers!=='function'&&typeof client?.onWhatsApp!=='function')return project('provider_failed');
        for(let offset=0;offset<unique.length;offset+=batchSize){
          const batch=unique.slice(offset,offset+batchSize);
          await guard();
          const answer=await lookup(batch);
          await guard();
          if(answer.kind==='timeout')return project('provider_timeout');
          if(answer.kind==='failure'){
            for(const number of batch)results.set(number,{number,status:'error',reason:'provider_failed'});
            return project('provider_failed');
          }
          const rows=registrationResults(batch,answer.value);
          if(!rows)return project('provider_result_unverified');
          for(const row of rows)results.set(row.number,row);
        }
        return project();
      }catch(failure){
        if(failure?.numberCheckReason==='deadline_exceeded')return project();
        throw unavailable(failure?.numberCheckReason||'provider_failed');
      }finally{workDone=true;release();}
    })();
    const deadline=new Promise(resolve=>{deadlineTimer=schedule(()=>{expired=true;resolve(project());},timeoutMs);});
    try{
      const result=await Promise.race([work,deadline]);
      if(!contextCurrent())throw unavailable('context_changed');
      return result;
    }finally{expired=true;cancel(deadlineTimer);}
  };
}
