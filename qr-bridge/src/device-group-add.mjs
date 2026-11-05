/** One explicitly claimed group participant operation. This namespace does not
 * send messages/invitations, create a socket, or consume the message-effect queue. */
import path from 'node:path';
import {performance} from 'node:perf_hooks';
import {channelId,channelDirectory,privateDirectory,encryptedChannelStore,digest} from './channel-storage.mjs';
import {GROUP_CONTACTS_LIMITS,groupContactsId,groupContactPhone,groupContactPeer,
  groupContactGroup,groupContactGroups,groupContactMembers,boundedGroupContacts} from './device-group-contacts.mjs';

export const GROUP_ADD_LIMITS=Object.freeze({...GROUP_CONTACTS_LIMITS,interval_ms:3000});
export const GROUP_ADD_REASONS=Object.freeze(['provider_refused','not_admin','group_unavailable','context_changed',
  'device_unavailable','ingress_blocked','device_busy','deadline_exceeded','provider_result_unverified',
  'operation_incomplete','storage_unavailable']);
const PURPOSE='subscription-group-add-v1';
const STATES=new Set(['added','already_member','not_added','uncertain','not_attempted']);
const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const scope=value=>({device_id:value.device_id,generation:value.generation,settings_revision:value.settings_revision});
const operation=value=>({...scope(value),operation_id:value.operation_id,group_id:value.group_id,phone:value.phone,claim_token:value.claim_token});
const same=(a,b)=>Object.entries(b).every(([key,value])=>a?.[key]===value);
const error=reason=>Object.assign(Error('group_add_unavailable'),{groupAddReason:GROUP_ADD_REASONS.includes(reason)?reason:'group_unavailable'});
const reasonOf=(failure,fallback='context_changed')=>GROUP_ADD_REASONS.includes(failure?.groupAddReason)?failure.groupAddReason:fallback;
const response=(input,status,reason=null,settled=false)=>({...scope(input),operation_id:input.operation_id,group_id:input.group_id,phone:input.phone,status,reason,settled});

export function validGroupAddRequest(action,input){
  const fields=['device_id','generation','settings_revision',...(['members','add','lookup'].includes(action)?['group_id']:[]),
    ...(['add','lookup'].includes(action)?['operation_id','phone','claim_token']:[])];
  return ['groups','members','add','lookup'].includes(action)&&record(input)&&Object.keys(input).length===fields.length&&
    fields.every(field=>Object.hasOwn(input,field))&&channelId(input.device_id)&&channelId(input.generation)&&
    Number.isSafeInteger(input.settings_revision)&&input.settings_revision>=1&&
    (action==='groups'||groupContactsId(input.group_id))&&(!['add','lookup'].includes(action)||
      channelId(input.operation_id)&&channelId(input.claim_token)&&typeof input.phone==='string'&&/^\+[1-9]\d{7,14}$/.test(input.phone));
}

/** Only the authenticated socket's actual participant grants authority. The
 * creator/owner display fields are deliberately not inspected here. */
export function groupAddAdmin(metadata,user){
  if(!record(user)||!Array.isArray(metadata?.participants))return false;
  const self=new Set([groupContactPeer(user.id),groupContactPeer(user.lid)].filter(Boolean));
  const selfPhones=new Set([groupContactPhone(user.id),groupContactPhone(user.lid)].filter(Boolean));
  if(!self.size||selfPhones.size>1)return false;
  const matches=[];
  for(const row of metadata.participants){
    if(!record(row))continue;
    const identities=[groupContactPeer(row.id),groupContactPeer(row.jid),groupContactPeer(row.lid)].filter(Boolean);
    if(!identities.some(id=>self.has(id)))continue;
    const phones=new Set([groupContactPhone(row.id),groupContactPhone(row.jid)].filter(Boolean));
    if(phones.size>1||selfPhones.size&&phones.size&&![...phones].every(phone=>selfPhones.has(phone)))return false;
    matches.push(row);
  }
  return matches.length===1&&['admin','superadmin'].includes(matches[0].admin);
}
function completeMetadata(value,id){
  let group;
  try{group=groupContactGroup(value,id);}catch{throw error('group_unavailable');}
  if(!Array.isArray(value.participants)||value.participants.length>GROUP_ADD_LIMITS.participants||
      value.participants.length!==group.member_count)throw error('group_unavailable');
  return group;
}
function matchingParticipant(metadata,phone){
  return metadata.participants.find(row=>{
    if(!record(row))return false;
    const phones=new Set([groupContactPhone(row.id),groupContactPhone(row.jid)].filter(Boolean));
    return phones.size===1&&phones.has(phone);
  });
}
function providerOutcome(value,phone,metadata){
  if(!Array.isArray(value)||value.length!==1||!record(value[0])||typeof value[0].status!=='string'||
      !/^\d{3}$/.test(value[0].status))return null;
  try{boundedGroupContacts(value);}catch{return null;}
  const row=value[0],peer=groupContactPeer(row.jid),participant=matchingParticipant(metadata,phone);
  const matches=groupContactPhone(row.jid)===phone||participant&&peer&&
    [participant.id,participant.jid,participant.lid].some(jid=>groupContactPeer(jid)===peer);
  if(!matches)return null;
  if(row.status==='200')return participant?'added':null;
  // A per-participant refusal is evidence only when a subsequent complete
  // metadata read still shows absence. Contradictory replies stay uncertain.
  return /^4\d{2}$/.test(row.status)&&row.status!=='408'&&!participant?'not_added':null;
}

export function createGroupAddSocket({client,isCurrent,isConnected,deviceSnapshot,assertMutationReady,mutationCurrent,
  schedule=setTimeout,cancel=clearTimeout,timeoutMs=GROUP_ADD_LIMITS.timeout_ms}={}){
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>GROUP_ADD_LIMITS.timeout_ms)throw error('deadline_exceeded');
  let busy=false;
  return async function run(action,input,{authorize,beforeAttempt,onAttempt,isExpired=()=>false}={}){
    if(!validGroupAddRequest(action,input)||action==='lookup'||typeof authorize!=='function')throw error('context_changed');
    if(busy)throw error('device_busy');
    let expired=false,timer;
    const current=()=>{
      const snapshot=deviceSnapshot?.();
      return !expired&&!isExpired()&&isCurrent?.()===true&&isConnected?.()===true&&
        snapshot?.id===input.device_id&&snapshot.generation===input.generation&&snapshot.settings_revision===input.settings_revision;
    };
    const guard=async()=>{
      if(!current())throw error(expired||isExpired()?'deadline_exceeded':'context_changed');
      await authorize();
      if(!current())throw error(expired||isExpired()?'deadline_exceeded':'context_changed');
      if(action==='add'){
        if(typeof assertMutationReady!=='function')throw error('device_unavailable');
        await assertMutationReady();
        if(!current())throw error('context_changed');
      }
    };
    busy=true;
    const work=(async()=>{
      try{
        await guard();
        if(typeof client?.groupFetchAllParticipating!=='function')throw error('group_unavailable');
        const raw=await client.groupFetchAllParticipating();await guard();
        let list;try{list=groupContactGroups(raw);}catch{throw error('group_unavailable');}
        if(action==='groups')return boundedGroupContacts({...scope(input),groups:list.groups.map(group=>
          ({...group,can_add:groupAddAdmin(raw[group.id],client.user)}))});
        if(!list.ids.has(input.group_id)||typeof client.groupMetadata!=='function')throw error('group_unavailable');
        const metadata=await client.groupMetadata(input.group_id);await guard();
        const group=completeMetadata(metadata,input.group_id),is_admin=groupAddAdmin(metadata,client.user);
        if(action==='members'){
          const output=boundedGroupContacts({...scope(input),group,...groupContactMembers(metadata),is_admin});
          const final=await client.groupFetchAllParticipating();await guard();
          if(!groupContactGroups(final).ids.has(input.group_id))throw error('group_unavailable');
          return output;
        }
        if(!is_admin)throw error('not_admin');
        // An unambiguous own participant was proved above. Its authenticated
        // socket PN is usable even when that participant is represented by LID;
        // this never guesses a telephone number for any other LID.
        if(matchingParticipant(metadata,input.phone)||groupContactPhone(client.user?.id)===input.phone)
          return {status:'already_member',reason:null};
        if(typeof beforeAttempt!=='function'||typeof onAttempt!=='function'||typeof mutationCurrent!=='function'||
            typeof client.groupParticipantsUpdate!=='function')throw error('device_unavailable');
        await guard();await beforeAttempt();
        // The only participant mutation in this adapter. No invite/send fallback
        // is reachable, and a timed-out reservation is never dispatched again.
        if(!current()||!groupAddAdmin(metadata,client.user))throw error('context_changed');
        if(mutationCurrent()!==true)throw error('device_unavailable');
        // Synchronous persistence is the last boundary before invoking the SDK;
        // no broker await may consume part of the promised three-second gap.
        onAttempt();
        const result=await client.groupParticipantsUpdate(input.group_id,[input.phone.slice(1)+'@s.whatsapp.net'],'add');
        await guard();
        const after=await client.groupMetadata(input.group_id);await guard();
        completeMetadata(after,input.group_id);
        if(!groupAddAdmin(after,client.user))throw error('not_admin');
        const outcome=providerOutcome(result,input.phone,after);
        return outcome?{status:outcome,reason:outcome==='not_added'?'provider_refused':null}:
          {status:'uncertain',reason:'provider_result_unverified'};
      }finally{busy=false;}
    })();
    // Add has a controller-owned deadline so its durable receipt can distinguish
    // a preflight timeout from an already attempted provider mutation.
    if(action==='add')return work;
    const deadline=new Promise((_,reject)=>{timer=schedule(()=>{expired=true;reject(error('deadline_exceeded'));},timeoutMs);});
    try{return await Promise.race([work,deadline]);}finally{cancel(timer);}
  };
}

export function groupAddLedger(root,tenant,id,key,{readOnly=false}={}){
  const base=channelDirectory(root,tenant,id,{create:!readOnly});
  const directory=privateDirectory(path.join(base,'device-group-add'),{create:!readOnly});
  const store=encryptedChannelStore({directory,secret:key,readOnly});
  const operationKey=id=>{if(!channelId(id))throw error('context_changed');return 'operation:'+id;};
  const lane=()=>{
    const value=store.get('cadence');if(value===null)return {purpose:PURPOSE,in_flight:null,next_at:0};
    if(value?.purpose!==PURPOSE||!Number.isSafeInteger(value.next_at)||value.next_at<0||
        value.in_flight!==null&&!channelId(value.in_flight))throw error('storage_unavailable');
    return value;
  };
  return {
    get(id){return store.get(operationKey(id));},
    reserve(id,initial){
      try{store.set(operationKey(id),initial,{exclusive:true});return {fresh:true,record:initial};}
      catch(failure){if(failure.code!=='EEXIST')throw failure;}
      const old=store.get(operationKey(id));
      if(!old||old.purpose!==PURPOSE||old.fingerprint!==initial.fingerprint)throw error('context_changed');
      return {fresh:false,record:old};
    },
    save(id,value){store.set(operationKey(id),value);},
    cadence(){const value=lane();if(value.in_flight!==null)throw error('device_busy');return value.next_at;},
    claimLane(id){if(!channelId(id))throw error('context_changed');const value=lane();
      if(value.in_flight!==null)throw error('device_busy');store.set('cadence',{...value,in_flight:id});},
    releaseLane(id,next_at){const value=lane();
      if(value.in_flight!==id||!Number.isSafeInteger(next_at)||next_at<0)throw error('storage_unavailable');
      store.set('cadence',{purpose:PURPOSE,in_flight:null,next_at});}
  };
}
const fingerprint=(id,tenant,input)=>digest(JSON.stringify([PURPOSE,id,tenant,operation(input)]));
function project(id,tenant,input,value){
  if(!value)return response(input,'unrecorded');
  if(value.purpose!==PURPOSE||value.fingerprint!==fingerprint(id,tenant,input)||!same(value.request,operation(input))||
      !STATES.has(value.status)||value.reason!==null&&!GROUP_ADD_REASONS.includes(value.reason)||typeof value.settled!=='boolean')throw error('context_changed');
  return response(input,value.status,value.reason,value.settled);
}

export function createDeviceGroupAdd({broker,root,owner,resolve,now=Date.now,clock=()=>performance.now(),
  schedule=setTimeout,cancel=clearTimeout,timeoutMs=GROUP_ADD_LIMITS.timeout_ms,ledgerFactory=groupAddLedger}={}){
  if(!channelId(owner)||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>GROUP_ADD_LIMITS.timeout_ms)throw error('context_changed');
  const running=new Map(),cadenceDeadlines=new Map();let closed=false;
  async function identity(id,key,input){
    if(!channelId(id)||!validGroupAddRequest('lookup',input)||typeof key!=='string'||key.length<32||key.length>256||
        typeof broker?.deviceGroupAddReceipt!=='function')throw error('context_changed');
    const grant=await broker.deviceGroupAddReceipt(id,key,input);
    if(grant?.read_only!==true||grant.channel_id!==id||!channelId(grant.tenant_id)||!same(grant,operation(input)))throw error('context_changed');
    return grant;
  }
  function previous(id,key,input,grant){
    let value;
    try{value=ledgerFactory(root,grant.tenant_id,id,key,{readOnly:true}).get(input.operation_id);}
    catch(failure){if(failure.code!=='ENOENT')throw failure;}
    return project(id,grant.tenant_id,input,value);
  }
  async function lookup(id,key,input){const grant=await identity(id,key,input);return previous(id,key,input,grant);}
  async function add(id,key,input){
    if(closed||!validGroupAddRequest('add',input))throw error('context_changed');
    const localDeadline=clock()+timeoutMs;
    const identityGrant=await identity(id,key,input),old=previous(id,key,input,identityGrant);
    if(old.status!=='unrecorded')return old;
    const ledger=ledgerFactory(root,identityGrant.tenant_id,id,key);
    const initial={purpose:PURPOSE,fingerprint:fingerprint(id,identityGrant.tenant_id,input),request:operation(input),
      status:'uncertain',reason:'operation_incomplete',attempted:false,settled:false};
    const reserved=ledger.reserve(input.operation_id,initial);
    if(!reserved.fresh)return project(id,identityGrant.tenant_id,input,reserved.record);
    let record=reserved.record,attempted=false,expired=false,timer,permit,deadline=localDeadline,context,laneHeld=false;
    const persist=(status,reason,settled=false)=>{record={...record,status,reason,attempted,settled};ledger.save(input.operation_id,record);return response(input,status,reason,settled);};
    if(running.has(id))return persist('not_attempted','device_busy',true);
    const task={};running.set(id,task);
    const fresh=()=>!closed&&!expired&&clock()<deadline;
    const authorize=async()=>{
      if(!fresh())throw error('deadline_exceeded');
      await context.authorize();
      if(!fresh())throw error('deadline_exceeded');
      if(typeof broker.deviceGroupAddAuthorize!=='function')throw error('context_changed');
      const started=clock(),grant=await broker.deviceGroupAddAuthorize(id,key,{owner,fence:context.fence},
        {...operation(input),recheck:!!permit});
      if(!fresh()||grant?.authorized!==true||grant.channel_id!==id||grant.tenant_id!==identityGrant.tenant_id||
          grant.owner!==owner||grant.fence!==context.fence||!same(grant,operation(input))||
          !Number.isFinite(grant.expires_in_ms)||grant.expires_in_ms<=250||grant.expires_in_ms>30000)throw error('context_changed');
      deadline=Math.min(deadline,started+grant.expires_in_ms-100);permit=grant;
      // The SQL claim is the final asynchronous authority check. Only local
      // socket/epoch/readiness guards may follow it before the SDK mutation.
      if(!fresh()||context.current?.()!==true)throw error('context_changed');
    };
    const work=(async()=>{
      let outcome;
      try{
        context=resolve(id,key,input);
        if(context.tenantId!==identityGrant.tenant_id||!channelId(context.epoch)||!Number.isSafeInteger(context.fence)||context.fence<1||
            typeof context.current!=='function'||context.current()!==true)
          throw error('context_changed');
        record={...record,owner,fence:context.fence,auth_epoch:context.epoch};
        await authorize();
        const wait=Math.max(ledger.cadence()-now(),(cadenceDeadlines.get(id)||0)-clock());
        if(wait>0){
          if(wait>GROUP_ADD_LIMITS.interval_ms)throw error('device_busy');
          await new Promise(done=>schedule(done,wait));await authorize();
        }
        const result=await context.session.groupAdd('add',input,{authorize,isExpired:()=>!fresh(),beforeAttempt:async()=>{
          if(!fresh())throw error('deadline_exceeded');
          if(ledger.cadence()>now()||(cadenceDeadlines.get(id)||0)>clock())throw error('device_busy');
          // A durable lane survives a process crash. Only actual completion can
          // release it; a timer or a new owner never infers a settled IQ.
          ledger.claimLane(input.operation_id);laneHeld=true;
          record={...record,attempted:true};ledger.save(input.operation_id,record);
          // fsync can stall the event loop beyond a scheduled timeout. The
          // monotonic deadline is authoritative even before its timer runs.
          if(!fresh())throw error('deadline_exceeded');
          if(context.current()!==true)throw error('context_changed');
          // Persist intent before the final SQL check, so a slow fsync cannot
          // let an older source/owner grant outlive a subsequent revocation.
          await authorize();
        },onAttempt:()=>{
          if(!fresh())throw error('deadline_exceeded');
          if(context.current()!==true)throw error('context_changed');
          attempted=true;
        }});
        if(!fresh())throw error('deadline_exceeded');
        if(!STATES.has(result?.status)||result.status==='not_attempted'||result.reason!==null&&!GROUP_ADD_REASONS.includes(result.reason))
          throw error('provider_result_unverified');
        outcome=result;
      }catch(failure){
        const reason=reasonOf(failure,attempted?'provider_result_unverified':'context_changed');
        outcome={status:attempted?'uncertain':'not_attempted',reason};
      }
      try{
        // Completion is at or after the actual provider invocation. Recording
        // the next slot here preserves the gap even if pre-send fsync was slow.
        if(laneHeld){
          ledger.releaseLane(input.operation_id,Math.floor(now())+GROUP_ADD_LIMITS.interval_ms);laneHeld=false;
          cadenceDeadlines.set(id,clock()+GROUP_ADD_LIMITS.interval_ms);
        }
        return persist(outcome.status,outcome.reason,true);
      }catch{
        try{return persist('uncertain','operation_incomplete',!laneHeld);}
        catch{return response(input,'uncertain','operation_incomplete');}
      }finally{running.delete(id);}
    })();
    task.promise=work;
    const timeout=new Promise(resolveTimeout=>{timer=schedule(()=>{
      expired=true;
      try{resolveTimeout(persist(attempted?'uncertain':'not_attempted','deadline_exceeded'));}
      catch{resolveTimeout(response(input,'uncertain','operation_incomplete'));}
    },Math.max(0,localDeadline-clock()));});
    try{return await Promise.race([work,timeout]);}finally{cancel(timer);}
  }
  return {add,lookup,close(){closed=true;},async settled(){await Promise.all([...running.values()].map(task=>task.promise));},
    get pending(){return running.size;}};
}
