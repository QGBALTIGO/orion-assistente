import {GROUP_CONTACTS_LIMITS,groupContactsId} from './device-group-contacts.mjs';

function providerStatus(error) {
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
export function createGroupCatalogReader({client,extractMetadata,onFailure=()=>{}}) {
  const report=(stage,kind,error)=>{
    const status=providerStatus(error);
    onFailure({stage,kind,...(status===undefined?{}:{status})});
  };
  return async function read({beforeRetry}={}) {
    try{return await client.groupFetchAllParticipating();}
    catch(error){
      const parserError=error instanceof TypeError&&providerStatus(error)===undefined;
      report('sdk',parserError?'parser':'provider',error);
      if(!parserError||typeof client.query!=='function')throw error;
    }
    await beforeRetry?.();
    let packet;
    try{
      packet=await client.query({tag:'iq',attrs:{to:'@g.us',xmlns:'w:g2',type:'get'},content:[
        {tag:'participating',attrs:{},content:[{tag:'participants',attrs:{}},{tag:'description',attrs:{}}]}
      ]});
    }catch(error){report('fallback','provider',error);throw error;}
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
  };
}
