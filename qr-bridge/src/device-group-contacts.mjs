/** Bounded reads from one already-connected device socket. No contact store,
 * participant mutation, outbound message or connection lifecycle is used. */
import {channelId} from './channel-storage.mjs';

export const GROUP_CONTACTS_LIMITS=Object.freeze({groups:1000,participants:5000,bytes:240000,timeout_ms:12000,name_utf16:100});
const unavailable=()=>Error('group_contacts_unavailable');
const record=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
export const groupContactsId=value=>typeof value==='string'&&/^[0-9]{5,20}(?:-[0-9]{1,20})?@g\.us$/.test(value);
export function validGroupContactsRequest(action,input){
  const fields=['device_id','generation','settings_revision',...(action==='members'?['group_id']:[])];
  return ['groups','members'].includes(action)&&record(input)&&Object.keys(input).length===fields.length&&
    fields.every(field=>Object.hasOwn(input,field))&&channelId(input.device_id)&&channelId(input.generation)&&
    Number.isSafeInteger(input.settings_revision)&&input.settings_revision>=1&&
    (action!=='members'||groupContactsId(input.group_id));
}
function label(value){
  if(typeof value!=='string')return '';
  const clean=value.slice(0,4096).normalize('NFC').replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g,' ').replace(/\s+/g,' ').trim();
  let result='';
  for(const char of clean){
    if(char.length===1&&char.charCodeAt(0)>=0xd800&&char.charCodeAt(0)<=0xdfff)continue;
    if(result.length+char.length>GROUP_CONTACTS_LIMITS.name_utf16)break;
    result+=char;
  }
  return result.trim();
}
function pn(jid){
  if(typeof jid!=='string')return null;
  const match=jid.match(/^([1-9]\d{7,14})(?::\d{1,5})?@s\.whatsapp\.net$/);
  return match?'+'+match[1]:null;
}
function peer(jid){
  if(typeof jid!=='string')return null;
  const match=jid.match(/^([1-9]\d{0,19})(?::\d{1,5})?@(s\.whatsapp\.net|lid)$/);
  return match?match[1]+'@'+match[2]:null;
}
function groupItem(meta,id){
  if(!record(meta)||!groupContactsId(id)||meta.id!==id)throw unavailable();
  const size=meta.size===undefined&&Array.isArray(meta.participants)?meta.participants.length:meta.size;
  if(!Number.isSafeInteger(size)||size<0||size>GROUP_CONTACTS_LIMITS.participants)throw unavailable();
  return {id,name:label(meta.subject)||'Grupo sem nome',member_count:size};
}
function participating(value){
  if(!record(value))throw unavailable();
  const entries=Object.entries(value);
  if(entries.length>GROUP_CONTACTS_LIMITS.groups)throw unavailable();
  const groups=entries.map(([id,meta])=>groupItem(meta,id));
  return {groups,ids:new Set(groups.map(group=>group.id))};
}
function memberItems(meta){
  if(!Array.isArray(meta.participants)||meta.participants.length>GROUP_CONTACTS_LIMITS.participants)throw unavailable();
  const owners=new Set([peer(meta.owner),peer(meta.ownerJid)].filter(Boolean)),members=new Map();
  let unavailable_count=0,duplicate_count=0;
  const rank={member:0,admin:1,owner:2};
  for(const row of meta.participants){
    if(!record(row)){unavailable_count++;continue;}
    // Baileys 6.7.24 supplies a verified PN in `jid` when `id` is a LID.
    // A numeric LID or a contact-store guess is never a telephone number.
    const candidates=new Set([pn(row.id),pn(row.jid)].filter(Boolean));
    if(candidates.size!==1){unavailable_count++;continue;}
    const phone=[...candidates][0],name=label(row.name)||label(row.notify)||label(row.verifiedName);
    const role=row.admin==='superadmin'||[peer(row.id),peer(row.jid)].some(id=>id&&owners.has(id))?'owner':row.admin==='admin'?'admin':'member';
    const previous=members.get(phone);
    if(previous){
      duplicate_count++;
      if(!previous.name&&name)previous.name=name;
      if(rank[role]>rank[previous.role])previous.role=role;
    }else members.set(phone,{phone,name,role});
  }
  return {members:[...members.values()],total:meta.participants.length,unavailable_count,duplicate_count};
}
function bounded(value){
  if(Buffer.byteLength(JSON.stringify(value),'utf8')>GROUP_CONTACTS_LIMITS.bytes)throw unavailable();
  return value;
}

// Read-only projections shared by the separate group-add adapter. Display
// roles from memberItems are never an authorization proof for a mutation.
export {label as groupContactLabel,pn as groupContactPhone,peer as groupContactPeer,
  groupItem as groupContactGroup,participating as groupContactGroups,
  memberItems as groupContactMembers,bounded as boundedGroupContacts};

export function createDeviceGroupContacts({client,isCurrent,isConnected,deviceSnapshot,
  schedule=setTimeout,cancel=clearTimeout,timeoutMs=GROUP_CONTACTS_LIMITS.timeout_ms}={}){
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>GROUP_CONTACTS_LIMITS.timeout_ms)throw unavailable();
  let busy=false;
  return async function read(action,input,{authorize}={}){
    if(!validGroupContactsRequest(action,input)||typeof authorize!=='function'||busy)throw unavailable();
    const expected={device_id:input.device_id,generation:input.generation,settings_revision:input.settings_revision};
    let expired=false,timer;
    const current=()=>{
      const device=deviceSnapshot?.();
      return !expired&&isCurrent?.()===true&&isConnected?.()===true&&device?.id===expected.device_id&&
        device.generation===expected.generation&&device.settings_revision===expected.settings_revision;
    };
    const guard=async()=>{
      if(!current())throw unavailable();
      await authorize();
      if(!current())throw unavailable();
    };
    busy=true;
    // A provider query cannot be cancelled independently of its live socket.
    // Keep the read slot until it settles, even after returning a timeout.
    const work=(async()=>{
      try{
        await guard();
        if(typeof client?.groupFetchAllParticipating!=='function'||action==='members'&&typeof client.groupMetadata!=='function')throw unavailable();
        const initial=await client.groupFetchAllParticipating();
        await guard();
        const first=participating(initial);
        if(action==='groups')return bounded({...expected,groups:first.groups});
        if(!first.ids.has(input.group_id))throw unavailable();
        const metadata=await client.groupMetadata(input.group_id);
        await guard();
        const group=groupItem(metadata,input.group_id),result=memberItems(metadata);
        // A partial provider list cannot be presented as a complete extraction.
        if(group.member_count!==result.total)throw unavailable();
        const output=bounded({...expected,group,...result});
        const final=await client.groupFetchAllParticipating();
        await guard();
        const latest=participating(final);
        if(!latest.ids.has(input.group_id))throw unavailable();
        return output;
      }catch{throw unavailable();}
      finally{busy=false;}
    })();
    const deadline=new Promise((_,reject)=>{timer=schedule(()=>{expired=true;reject(unavailable());},timeoutMs);});
    try{return await Promise.race([work,deadline]);}
    finally{cancel(timer);}
  };
}
