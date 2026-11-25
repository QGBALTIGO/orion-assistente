/** Explicit offer publications on one authenticated tenant socket. */
import {createHash} from 'node:crypto';
import {channelId} from './channel-storage.mjs';
import {groupAddAdmin} from './device-group-add.mjs';
import {groupContactLabel} from './device-group-contacts.mjs';

const scope=value=>({device_id:value.device_id,generation:value.generation,settings_revision:value.settings_revision});
const record=value=>value&&typeof value==='object'&&!Array.isArray(value);
const group=value=>typeof value==='string'&&/^[1-9]\d{4,20}(?:-\d{1,20})?@g\.us$/.test(value);
const refused=()=>Error('affiliate_destination_unavailable');

export function validAffiliateRequest(action,input){
  if(!['groups','verify','send'].includes(action)||!record(input)||!channelId(input.device_id)||
      !channelId(input.generation)||!Number.isSafeInteger(input.settings_revision)||input.settings_revision<1)return false;
  const keys=['device_id','generation','settings_revision'];
  if(action!=='groups'){
    if(input.mode==='whatsapp_group'){
      if(!group(input.address))return false;keys.push('mode','address');
    }else if(input.mode==='whatsapp_channel'){
      if(typeof input.invite!=='string'||!/^[A-Za-z0-9]{20,32}$/.test(input.invite))return false;
      keys.push('mode','invite');
    }else return false;
  }
  if(action==='send'){
    keys.push('operation_id','text','link','media');
    if(!channelId(input.operation_id)||typeof input.text!=='string'||!input.text.trim()||
        input.text.length>(input.media?1024:4096)||typeof input.link!=='string'||input.link.length>2048||
        !input.text.includes(input.link))return false;
    try{
      const url=new URL(input.link);
      if(url.protocol!=='https:'||url.username||url.password||url.port||/[\s<>\\]/.test(input.link))return false;
    }catch{return false;}
    if(input.media!==null){
      if(typeof input.media!=='string'||input.media.length>1400000||!input.media.length||
          !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(input.media))return false;
      const bytes=Buffer.from(input.media,'base64');
      if(bytes.length<32||bytes.length>1000000||!(bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))||
          bytes.subarray(0,3).equals(Buffer.from('ffd8ff','hex'))))return false;
    }
  }
  return Object.keys(input).length===keys.length&&keys.every(key=>Object.hasOwn(input,key));
}

export function createAffiliateOfferSocket({client,sdk,logger,isCurrent,isConnected,deviceSnapshot,
    schedule=setTimeout,cancel=clearTimeout,timeoutMs=36000}={}){
  if(!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>36000)throw refused();
  return async function affiliate(action,input,{authorize,beforeAttempt=async()=>{}}={}){
    if(!validAffiliateRequest(action,input)||typeof authorize!=='function')throw refused();
    let expired=false,timer;
    const current=()=>{
      const device=deviceSnapshot?.();
      return !expired&&isCurrent?.()===true&&isConnected?.()===true&&device?.id===input.device_id&&
        device.generation===input.generation&&device.settings_revision===input.settings_revision;
    };
    const guard=async()=>{if(!current())throw refused();await authorize();if(!current())throw refused();};
    const verify=async()=>{
      await guard();
      if(input.mode==='whatsapp_group'){
        const metadata=await client.groupMetadata(input.address);
        await guard();
        if(metadata?.id!==input.address||!groupAddAdmin(metadata,client.user))throw refused();
        return {address:input.address,title:groupContactLabel(metadata.subject)||'Grupo do WhatsApp',role:'ADMIN'};
      }
      let metadata=await client.newsletterMetadata('invite',input.invite);
      await guard();
      const id=metadata?.id;
      if(!/^[1-9][0-9]{7,23}@newsletter$/.test(id||''))throw refused();
      if(!['OWNER','ADMIN'].includes(metadata?.viewer_metadata?.role)){
        metadata=await client.newsletterMetadata('jid',id);await guard();
      }
      const role=metadata?.viewer_metadata?.role;
      if(metadata?.id!==id||!['OWNER','ADMIN'].includes(role)||metadata?.state?.type&&metadata.state.type!=='ACTIVE')throw refused();
      return {address:metadata.id,title:groupContactLabel(metadata.thread_metadata?.name?.text)||'Canal do WhatsApp',role};
    };
    const work=(async()=>{
      await guard();
      if(action==='groups'){
        const all=await client.groupFetchAllParticipating();await guard();
        if(!record(all)||Object.keys(all).length>1000)throw refused();
        const groups=Object.entries(all).filter(([address,meta])=>group(address)&&meta?.id===address&&groupAddAdmin(meta,client.user))
          .map(([address,meta])=>({address,title:groupContactLabel(meta.subject)||'Grupo do WhatsApp',role:'ADMIN'}));
        return {...scope(input),groups};
      }
      let destination=await verify();
      if(action==='verify')return {...scope(input),...destination};
      let message={extendedTextMessage:{text:input.text}};
      if(input.media){
        const bytes=Buffer.from(input.media,'base64');
        message=await sdk.prepareWAMessageMedia({image:bytes,caption:input.text,
          mimetype:bytes[0]===0xff?'image/jpeg':'image/png'},
          {jid:destination.address,upload:client.waUploadToServer,logger,mediaUploadTimeoutMs:12000});
        const image=message?.imageMessage;
        if(!image?.directPath||!createHash('sha256').update(bytes).digest().equals(Buffer.from(image.fileSha256||[])))throw refused();
        // Image preparation may take several seconds; recheck the actual
        // destination's current administration after upload, before relay.
        const fresh=await verify();if(fresh.address!==destination.address)throw refused();destination=fresh;
      }
      await guard();
      const id=sdk.generateMessageIDV2(client.user.id);
      if(typeof id!=='string'||!id||id.length>160||typeof client.waitForMessage!=='function')throw refused();
      const waiting=client.waitForMessage(id,7000).catch(()=>null);
      await beforeAttempt();
      if(!current())throw refused();
      // The manager persists the uncertain operation before this boundary.
      try{await client.relayMessage(destination.address,message,{messageId:id});}catch{}
      const ack=await waiting;
      if(ack?.tag!=='ack'||ack.attrs?.id!==id||ack.attrs?.from!==destination.address||ack.attrs?.error||
          input.mode==='whatsapp_channel'&&!/^\d{1,16}$/.test(ack.attrs?.server_id||'')){
        return {...scope(input),state:'unknown',accepted:false,provider_id:id};
      }
      return {...scope(input),state:'sent',accepted:true,provider_id:id};
    })();
    const timeout=new Promise((_,reject)=>{timer=schedule(()=>{expired=true;reject(refused());},timeoutMs);timer?.unref?.();});
    try{return await Promise.race([work,timeout]);}finally{expired=true;cancel(timer);}
  };
}
