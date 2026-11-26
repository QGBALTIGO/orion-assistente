import {GROUP_CONTACTS_LIMITS,groupContactGroups,groupContactsId} from './device-group-contacts.mjs';

const MINUTE=60_000;
const MAX_COOLDOWN=60*MINUTE;

function providerStatus(error) {
  // Baileys stores the provider IQ error code in Boom.data while output
  // carries its generic HTTP mapping. Never log provider messages or objects.
  const iqCode=error?.isBoom===true?error.data:undefined;
  if(Number.isInteger(iqCode)&&iqCode>=400&&iqCode<=599)return iqCode;
  const status=error?.output?.statusCode??error?.statusCode;
  return Number.isInteger(status)&&status>=400&&status<=599?status:undefined;
}

function parseGroups(packet,extractMetadata) {
  const children=packet?.content;
  if(packet?.tag!=='iq'||packet.attrs?.type!=='result'||!Array.isArray(children))
    throw Error('invalid_group_catalog');
  const envelopes=children.filter(node=>node?.tag==='groups');
  if(envelopes.length!==1||envelopes[0].content!=null&&!Array.isArray(envelopes[0].content))
    throw Error('invalid_group_catalog');
  const rows=(envelopes[0].content??[]).filter(node=>node?.tag==='group');
  if(rows.length>GROUP_CONTACTS_LIMITS.groups)throw Error('invalid_group_catalog');
  const groups=Object.create(null),seen=new Set();
  for(const [index,node] of rows.entries()){
    const invalid='invalid-'+index;
    try{
      const group=extractMetadata({tag:'result',attrs:{},content:[node]});
      if(!groupContactsId(group?.id)){groups[invalid]=null;continue;}
      if(seen.has(group.id)){
        groups[group.id]=null;
        groups[invalid]=null;
      }else{
        seen.add(group.id);
        groups[group.id]=group;
      }
    }catch{groups[invalid]=null;}
  }
  const omitted=Object.values(groups).filter(group=>group===null).length;
  if(omitted>0&&omitted===rows.length)throw Error('invalid_group_catalog');
  return {groups,omitted};
}

/** Recover an SDK parser failure with the same read-only participating-groups IQ.
 * The caller still verifies the socket and lease before and after catalog reads.
 */
export function createGroupCatalogReader({
  client,extractMetadata,onFailure=()=>{},now=Date.now,cooldownMs=5*MINUTE,quietUntil=0,
  membershipScope=()=>null
}) {
  let pending,pendingScope,membership,blockedUntil=quietUntil,delay=cooldownMs;
  const limited=()=>Object.assign(Error('group_catalog_rate_limited'),{
    groupCatalogReason:'provider_rate_limited',
    retryAfterSeconds:Math.max(1,Math.ceil((blockedUntil-now())/1000))
  });
  const defer=()=>{
    blockedUntil=now()+delay;
    membership=null;
    delay=Math.min(delay*2,MAX_COOLDOWN);
    return limited();
  };
  const report=(stage,kind,error)=>{
    const status=providerStatus(error);
    onFailure({stage,kind,...(status===undefined?{}:{status})});
  };
  async function fetchCatalog(beforeRetry) {
    try{return await client.groupFetchAllParticipating();}
    catch(error){
      const parserError=error instanceof TypeError&&providerStatus(error)===undefined;
      report('sdk',parserError?'parser':'provider',error);
      if(providerStatus(error)===429)throw defer();
      if(!parserError||typeof client.query!=='function')throw error;
    }
    await beforeRetry?.();
    let packet;
    try{
      packet=await client.query({tag:'iq',attrs:{to:'@g.us',xmlns:'w:g2',type:'get'},content:[
        {tag:'participating',attrs:{},content:[{tag:'participants',attrs:{}},{tag:'description',attrs:{}}]}
      ]});
    }catch(error){
      report('fallback','provider',error);
      if(providerStatus(error)===429)throw defer();
      throw error;
    }
    let result;
    try{
      let parser=extractMetadata;
      if(!parser){
        await import('@whiskeysockets/baileys');
        parser=(await import('@whiskeysockets/baileys/lib/Socket/groups.js')).extractGroupMetadata;
      }
      result=parseGroups(packet,parser);
    }catch(error){report('fallback','invalid_metadata',error);throw error;}
    onFailure({stage:'recovered',kind:'parser',omitted:result.omitted});
    return result.groups;
  }
  async function read({beforeRetry}={}) {
    if(now()<blockedUntil)throw limited();
    const startedIn=membershipScope();
    if(pending&&pendingScope!==startedIn)throw Error('group_catalog_scope_changed');
    if(!pending){
      pendingScope=startedIn;
      pending=fetchCatalog(beforeRetry).then(groups=>{
        delay=cooldownMs;
        if(startedIn!==null&&startedIn===membershipScope()){
          // Only the successful roster's group IDs are retained. Admin rights
          // and participant membership are always checked in fresh metadata.
          try{membership={scope:startedIn,ids:groupContactGroups(groups).ids,until:now()+2*MINUTE};}
          catch{membership=null;}
        }
        return groups;
      }).finally(()=>{pending=null;pendingScope=null;});
    }
    return pending;
  }
  async function confirmMembership(groupId,options){
    if(now()<blockedUntil)throw limited();
    const currentScope=membershipScope();
    if(membership&&membership.scope===currentScope&&now()<membership.until&&membership.ids.has(groupId))return true;
    const groups=await read(options);
    if(currentScope!==membershipScope())throw Error('group_catalog_scope_changed');
    return groupContactGroups(groups).ids.has(groupId);
  }
  return Object.assign(read,{confirmMembership});
}
