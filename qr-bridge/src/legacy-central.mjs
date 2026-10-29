/** Adapts the existing socket to the managed broker. Never constructs a socket,
 * reads/writes credentials, emits a connection notice or copies an auth folder.
 */
import {randomUUID,createHmac} from 'node:crypto';
import {channelId} from './channel-storage.mjs';

export function createLegacyCentral({env=process.env,fetchImpl=fetch,start,send,status,sessionDigest,rawSend,now=Date.now}){
  let context=null,identity=null,starting=null;
  const configured=()=>env.QR_LEGACY_CENTRAL_ENABLED==='1';
  const matching=()=>identity&&typeof sessionDigest==='function'&&sessionDigest()===identity.session_digest;
  const active=()=>configured()&&context?.isCurrent()===true&&matching();
  async function resume(){
    if(!configured())return {ready:false,configured:false};
    if(starting)return starting;
    if(active())return health();
    starting=(async()=>{
      const base=new URL(env.QR_CHANNEL_BROKER_URL);
      if(base.protocol!=='http:'||!base.hostname.endsWith('.railway.internal')||base.pathname!=='/'||
          base.username||base.password||base.search||base.hash)throw Error('unsafe_legacy_broker');
      const response=await fetchImpl(new URL('/internal/qr/legacy/identity',base),{method:'GET',redirect:'error',
        headers:{'X-Orion-Bridge-Key':env.QR_BRIDGE_TOKEN},signal:AbortSignal.timeout(5000)});
      if(!response.ok)throw Error('legacy_adoption_not_ready');
      const row=await response.json();
      const self=row?.verified_self_phone;
      const selfProof=typeof self==='string'&&/^55[1-9]\d[2-9]\d{7}$/.test(self)&&
        createHmac('sha256',env.QR_BRIDGE_TOKEN).update('orion:preserved-primary-session:v1\0'+self).digest('hex')===row.session_digest;
      if(row?.legacy_transport!==true||!channelId(row.channel_id)||!channelId(row.tenant_id)||
          typeof row.session_digest!=='string'||!/^[a-f0-9]{64}$/.test(row.session_digest)||
          !Array.isArray(row.allowed_senders)||!row.allowed_senders.length||row.allowed_senders.length>10||
          row.allowed_senders.some(p=>typeof p!=='string'||(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(p)&&!(selfProof&&p===self)))||
          (env.QR_AI_PILOT_NUMBER&&!row.allowed_senders.includes(env.QR_AI_PILOT_NUMBER))||
          (identity&&identity.channel_id!==row.channel_id))throw Error('invalid_legacy_adoption');
      identity=row;
      await start(row.channel_id,env.QR_BRIDGE_TOKEN);
      return health();
    })();
    try{return await starting;}finally{starting=null;}
  }
  async function attach(value){
    if(!configured()||value.legacyTransport!==true||!identity||value.channelId!==identity.channel_id||
        value.tenantId!==identity.tenant_id||value.secret!==env.QR_BRIDGE_TOKEN)throw Error('legacy_socket_not_authorized');
    await value.assertCurrent();
    if(context&&context!==value&&context.isCurrent())throw Error('legacy_socket_already_adopted');
    context=value;
    // The manager may retire this adapter; it must never close the main socket.
    return {status:()=>status()==='connected'&&matching()?'connected':'connecting',qr:()=>null,
      close(){if(context===value)context=null;},
      async send(phone,text,{isCurrent=()=>true}={}){
        if(context!==value||!value.isCurrent()||!matching()||!value.allowedSender(phone)||!isCurrent())throw Error('legacy_send_revoked');
        await value.assertCurrent();
        const result=await rawSend(phone,text,{waitForCapacity:true,isCurrent:()=>
          context===value&&value.isCurrent()&&matching()&&value.allowedSender(phone)&&isCurrent()});
        const id=result?.accepted===true&&typeof result.id==='string'?result.id.replace(/^out:/,''):null;
        if(!id)throw Error('legacy_send_unknown');
        return {id};
      }};
  }
  async function inbound(payload){
    await resume();
    if(!active()||!context.allowedSender(payload.phone))throw Error('legacy_ingress_denied');
    return context.onInbound(payload);
  }
  async function forward(payload,options={}){
    if(!active()||!context.allowedSender(payload.phone))throw Error('legacy_assistant_not_ready');
    return context.onMessage(payload,options);
  }
  async function automatic(phone,text,options={}){
    await resume();
    if(!active())throw Error('legacy_send_not_ready');
    return send(identity.channel_id,env.QR_BRIDGE_TOKEN,{to:phone,text,operation_id:randomUUID(),
      source:'automation',isCurrent:options.isCurrent||(()=>true)});
  }
  async function receipt(id,delivery){if(active())await context.onReceipt(id,delivery);}
  function allowed(phone){return active()&&context.allowedSender(phone);}
  function health(){return {configured:configured(),ready:active(),connected:status()==='connected',
    observed_at:now(),session_preserved:Boolean(matching())};}
  return {configured,resume,attach,inbound,forward,automatic,receipt,allowed,health};
}
