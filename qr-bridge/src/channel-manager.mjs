import {safetyControl} from './inbound-event.mjs';
/** Per-channel lifecycle; SQL broker owns the distributed fence, not this Map. */
import {randomUUID,timingSafeEqual} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {channelId,channelDirectory,digest,sendLedger} from './channel-storage.mjs';
import {validPhone} from './core.mjs';
import {makeSendCapacity} from './send-capacity.mjs';

const sameKey=(a,b)=>typeof a==='string'&&typeof b==='string'&&a.length===b.length&&
  timingSafeEqual(Buffer.from(a),Buffer.from(b));
export function reconnectDelay(attempt,random=Math.random){
  return Math.min(30000,Math.round(Math.min(30000,1000*2**Math.min(attempt,5))*(.8+.4*random())));
}
export function createChannelManager({broker,createSession,root='/data/orion-channels',
    owner=randomUUID(),clock=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,random=Math.random,maxChannels=10,
    audioEnabled=()=>process.env.QR_AI_AUDIO_ENABLED==='1',verifiedLid=()=>process.env.QR_PILOT_USE_VERIFIED_LID==='1',resumeStore=null}){
  if(!channelId(owner))throw Error('invalid_channel_owner');
  if(!Number.isInteger(maxChannels)||maxChannels<1||maxChannels>50)throw Error('invalid_channel_capacity');
  const contexts=new Map(),stopping=new Set();let resuming=false;
  const timer=(fn,ms)=>{const t=schedule(fn,ms);t?.unref?.();return t;};
  const current=c=>!c.stopped&&contexts.get(c.id)===c&&clock()<c.deadline;
  const requireCurrent=c=>{if(!current(c)){retire(c);throw Error('channel_lease_lost');}};
  function acceptLease(c,result,started,{renew=false}={}){
    if(result?.channel_id!==c.id||result?.owner!==owner||!channelId(result.tenant_id)||
        result.provider!=='baileys'||typeof result.assistant_enabled!=='boolean'||!Number.isSafeInteger(result.fence)||result.fence<1||
        !Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=500||result.expires_in_ms>30000||
        !Array.isArray(result.allowed_senders)||result.allowed_senders.length>10||
        (!result.allowed_senders.length&&result.assistant_enabled!==false)||
        result.allowed_senders.some(p=>typeof p!=='string'||(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(p)&&
          !(result.legacy_transport===true&&p===result.verified_self_phone&&/^55[1-9]\d[2-9]\d{7}$/.test(p))))||
        (c.fence&&(result.fence!==c.fence||result.tenant_id!==c.tenant)))throw Error('invalid_channel_lease');
    c.tenant=result.tenant_id;c.fence=result.fence;c.allowed=new Set(result.allowed_senders);c.assistant=result.assistant_enabled;
    if(c.legacyTransport!==undefined&&c.legacyTransport!==(result.legacy_transport===true))throw Error('channel_transport_changed');
    c.legacyTransport=result.legacy_transport===true;
    c.inboxControl=c.inboxControl===true||result.inbox_control===true||process.env.INBOX_CONTROL_ENABLED==='1';
    const deadline=started+result.expires_in_ms-250;
    c.deadline=renew||!c.deadline?deadline:Math.min(c.deadline,deadline);
    requireCurrent(c);
    cancel(c.expiry);c.expiry=timer(()=>retire(c),Math.max(0,c.deadline-clock()));
  }
  function retire(c){
    if(c.stopped)return;
    c.stopped=true;c.generation++;
    cancel(c.expiry);cancel(c.renewal);cancel(c.reconnect);
    try{c.session?.close();}catch{}
    c.session=null;
    if(contexts.get(c.id)===c)contexts.delete(c.id);
  }
  async function validate(c,key=c.key){
    requireCurrent(c);
    if(!sameKey(c.key,key))throw Error('channel_unauthorized');
    const started=clock();
    try{
      const result=await broker.lease(c.id,key,'check',owner,c.fence);
      requireCurrent(c);acceptLease(c,result,started);return c;
    }catch(error){retire(c);throw error;}
  }
  function renewLater(c){
    c.renewal=timer(async()=>{
      if(!current(c))return retire(c);
      const started=clock();
      try{
        const result=await broker.lease(c.id,c.key,'renew',owner,c.fence);
        requireCurrent(c);acceptLease(c,result,started,{renew:true});renewLater(c);
      }catch{retire(c);}
    },Math.max(100,Math.min(8000,(c.deadline-clock())/3)));
  }
  function disconnected(c,generation,reason){
    if(!current(c)||generation!==c.generation)return;
    try{c.session?.close();}catch{}c.session=null;c.generation++;
    if(reason==='logged_out'){try{resumeStore?.forget(c.id,c.key);}catch{}retire(c);return;}
    if(c.attempts>=8){retire(c);return;}
    cancel(c.reconnect);
    c.reconnect=timer(()=>connect(c).catch(()=>{}),reconnectDelay(c.attempts++,random));
  }
  async function connect(c){
    await validate(c);
    const generation=++c.generation;
    const assertCurrent=async()=>{await validate(c);if(generation!==c.generation)throw Error('channel_socket_replaced');};
    const isCurrent=()=>current(c)&&generation===c.generation;
    const notices=new WeakMap();
    async function notice(payload){
      await assertCurrent();
      if(!c.allowed.has(payload.phone)||typeof payload.message_id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(payload.message_id))throw Error('channel_sender_denied');
      if(c.inboxControl&&('text' in payload||'kind' in payload)){
        if(typeof broker.journal!=='function')throw Error('inbox_journal_unavailable');
        const saved=await broker.journal(c.id,c.key,{owner,fence:c.fence},payload);
        if(saved?.recorded!==true)throw Error('inbox_journal_unconfirmed');
      }
      const control=payload.envelope?.control||safetyControl(payload.text);
      const recorded=await broker.interaction(c.id,c.key,{owner,fence:c.fence},{phone:payload.phone,message_id:payload.message_id,
        ...(!c.inboxControl&&control?{control}:{})});
      if(recorded?.recorded!==true||!Number.isSafeInteger(recorded.revision)||recorded.revision<1)throw Error('channel_interaction_unconfirmed');
      await assertCurrent();const proof=Object.freeze({revision:recorded.revision});
      notices.set(proof,{phone:payload.phone,id:payload.message_id,revision:recorded.revision,state:'new'});return proof;
    }
    try{
      const session=await createSession({channelId:c.id,tenantId:c.tenant,owner,fence:c.fence,
        directory:c.directory,secret:c.key,assertCurrent,isCurrent,resumeOnly:c.resumeOnly,
        notifySelf:!c.resumeOnly&&!c.legacyTransport&&process.env.ORION_NOTIFY_SELF_ON_LINK!=='0',
        newLinkNotice:c.newLinkNotice,
        legacyTransport:c.legacyTransport,
        allowedSender:phone=>c.allowed.has(phone),
        assistantEnabled:()=>c.assistant===true,
        audioEnabled:()=>audioEnabled()===true&&c.assistant===true,
        verifiedLid:()=>verifiedLid()===true,
        onInbound:notice,
        onRejected:({phone})=>{if(isCurrent()&&c.allowed.has(phone))c.summaryReview.set(phone,true);},
        transcribe:async payload=>{
          await assertCurrent();
          if(!c.assistant||audioEnabled()!==true||!c.allowed.has(payload.phone)||typeof broker.transcribe!=='function')throw Error('channel_audio_denied');
          const result=await broker.transcribe(c.id,c.key,{owner,fence:c.fence},payload);await assertCurrent();
          if(!c.assistant||audioEnabled()!==true||result?.truncated!==false||typeof result?.text!=='string'||!result.text.trim()||result.text.trim().length>1500)throw Error('channel_audio_unusable');
          return result.text.trim();
        },
        onConnected:()=>{if(!isCurrent())return;
          resumeStore?.remember({id:c.id,tenant:c.tenant,key:c.key});c.attempts=0;},
        onDisconnected:reason=>disconnected(c,generation,reason),
        onReceipt:async(id,delivery)=>{
          if(!['delivered','read'].includes(delivery))return;
          await assertCurrent();const item=c.sent.get(id);if(!item)return;
          const saved=c.ledger.reserve(item.operation,item.fingerprint).record;
          if(saved.delivery!=='read')c.ledger.save(item.operation,{...saved,delivery});
          if(c.inboxControl&&typeof broker.receipt==='function')
            await broker.receipt(c.id,c.key,{owner,fence:c.fence},{phone:item.phone,operation_id:item.operation,provider_id:id,delivery});
        },
        onMessage:async(payload,{noticeProof,noticeProofs,beforeReply,isCurrent:batchCurrent=()=>true}={})=>{
          await assertCurrent();
          if(!c.allowed.has(payload.phone))throw Error('channel_sender_denied');
          const ids=payload.message_ids??[payload.message_id];
          if(!Array.isArray(ids)||ids.length<1||ids.length>6||ids[0]!==payload.message_id||new Set(ids).size!==ids.length||
              ids.some(id=>typeof id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(id)))throw Error('invalid_channel_batch');
          let proofs=noticeProofs??(noticeProof?[noticeProof]:[]);
          if(!noticeProofs&&ids.length===1&&!(notices.get(proofs[0])?.phone===payload.phone&&notices.get(proofs[0])?.id===ids[0]))
            proofs=[await notice(payload)];
          if(proofs.length!==ids.length)throw Error('invalid_channel_notice_proofs');
          const observed=proofs.map(proof=>notices.get(proof));
          if(observed.some((item,index)=>item?.phone!==payload.phone||item.id!==ids[index]||item.state!=='new'))throw Error('invalid_channel_notice_proofs');
          const revision=Math.max(...observed.map(item=>item.revision));
          if(payload.intake_revision!==undefined&&payload.intake_revision!==revision)throw Error('invalid_channel_revision');
          const stillCurrent=()=>{try{return isCurrent()&&batchCurrent()===true;}catch{return false;}};
          if(!stillCurrent()){c.summaryReview.set(payload.phone,true);return {state:'superseded',not_committed:true};}
          await assertCurrent();
          if(!c.assistant)return;
          if(typeof payload.text!=='string'||!payload.text.trim()||payload.text.length>1500)throw Error('invalid_channel_inbound');
          // Never accept tenant, owner, channel or model routing from content.
          observed.forEach(item=>{item.state='inflight';});
          try{
            const result=await broker.forward(c.id,c.key,{owner,fence:c.fence},
              {phone:payload.phone,message_id:payload.message_id,message_ids:ids,text:payload.text,intake_revision:revision,
                require_summary_review:c.summaryReview.get(payload.phone)!==false,
                ...(payload.envelope?{envelope:payload.envelope}:{})});
            if(result?.step==='superseded'&&result.answer===null&&result.not_committed===true){
              observed.forEach(item=>{item.state='new';});c.summaryReview.set(payload.phone,true);
              return {state:'superseded',not_committed:true};
            }
            observed.forEach(item=>{item.state='spent';});
            await assertCurrent();
            if(result?.step==='superseded_after_write'){
              c.summaryReview.set(payload.phone,true);return {state:'superseded_after_write',not_committed:false};
            }
            if(!stillCurrent()){c.summaryReview.set(payload.phone,true);return;}
            if(result?.answer==null)return;
            if(typeof result.answer!=='string'||!result.answer.trim()||result.answer.length>1200)throw Error('invalid_channel_reply');
            if(typeof beforeReply==='function')await beforeReply(result.answer);
            await assertCurrent();
            if(!stillCurrent()){c.summaryReview.set(payload.phone,true);return;}
            const operation=deterministicOperation(c.id,ids.join('\0'));
            const sent=await send(c.id,c.key,{to:payload.phone,text:result.answer,operation_id:operation,isCurrent:stillCurrent,
              source:'assistant',control_revision:result.control_revision});
            c.summaryReview.set(payload.phone,!sent?.accepted||!stillCurrent());return sent;
          }catch(error){observed.forEach(item=>{item.state='spent';});c.summaryReview.set(payload.phone,true);throw error;}
        }});
      if(!isCurrent()){session?.close();throw Error('channel_lease_lost');}
      c.session=session;
    }catch(error){
      if(error?.message==='channel_auth_unavailable'){resumeStore?.forget(c.id,c.key);retire(c);}
      else disconnected(c,generation,'error');throw error;}
  }
  async function start(id,key,{resumeOnly=false,expectedTenant=null}={}){
    if(!channelId(id)||typeof key!=='string'||key.length<32||key.length>256)throw Error('invalid_channel');
    if(stopping.has(id))throw Error('channel_stopping');
    if(resumeOnly&&!resumeStore?.list().some(row=>row.id===id&&row.key===key&&row.tenant===expectedTenant))
      throw Error('channel_resume_cancelled');
    const existing=contexts.get(id);
    if(existing){if(!sameKey(existing.key,key))throw Error('channel_unauthorized');await existing.starting;await validate(existing,key);return status(id,key);}
    if(contexts.size>=maxChannels)throw Error('channel_capacity');
    const c={id,key,resumeOnly,deadline:0,fence:0,stopped:false,generation:0,attempts:0,sent:new Map(),summaryReview:new Map(),newLinkNotice:{pending:false},
      capacity:makeSendCapacity({now:clock})};contexts.set(id,c);
    c.starting=(async()=>{
      const started=clock(),lease=await broker.lease(id,key,'claim',owner);
      if(c.stopped||contexts.get(id)!==c)throw Error('channel_lease_lost');
      acceptLease(c,lease,started,{renew:true});
      if(expectedTenant&&c.tenant!==expectedTenant)throw Error('resume_tenant_changed');
      c.directory=channelDirectory(root,c.tenant,id);c.ledger=sendLedger(c.directory,key);
      renewLater(c);await connect(c).catch(error=>{if(!current(c)||!c.reconnect)throw error;});
    })();
    try{await c.starting;return status(id,key);}catch(error){retire(c);throw error;}
  }
  async function status(id,key){
    const c=contexts.get(id);
    if(!c){await broker.lease(id,key,'inspect',owner);return {channel_id:id,status:'stopped',capabilities:{pair:false}};}
    await validate(c,key);
    const phase=c.session?.status?.()||'connecting',phone=phase==='connected'?c.session?.phone?.():null;
    const verified=typeof phone==='string'&&/^[1-9][0-9]{7,14}$/.test(phone);
    return {channel_id:id,status:phase,fence:c.fence,phone:verified?phone:null,phone_verified:verified,
      capabilities:{text:true,audio:audioEnabled()===true&&c.assistant===true,reliable_send:true,typing:true,
        pair:!c.legacyTransport&&typeof c.session?.pair==='function'&&c.session?.supportsPairing?.()===true}};
  }
  async function pair(id,key,{phone}){
    if(typeof phone!=='string'||phone!==phone.trim()||!/^[1-9]\d{7,14}$/.test(phone))throw Error('invalid_pairing_phone');
    const c=contexts.get(id);if(!c)throw Error('channel_not_started');
    if(!sameKey(c.key,key))throw Error('channel_unauthorized');
    await c.starting;await validate(c,key);
    const session=c.session,generation=c.generation;
    if(c.legacyTransport||!session||session.supportsPairing?.()!==true||typeof session.pair!=='function')throw Error('channel_pairing_unavailable');
    const result=await session.pair(phone),receivedAt=clock();await validate(c,key);
    if(c.session!==session||c.generation!==generation)throw Error('channel_socket_replaced');
    if(result?.status!=='pairing'||typeof result.code!=='string'||result.code.length!==9||!(/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(result.code))||
        !Number.isSafeInteger(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>90000)throw Error('invalid_pairing_result');
    const expires=Math.floor(result.expires_in_ms-Math.max(0,clock()-receivedAt));
    if(expires<=0)throw Error('channel_pairing_expired');
    return {channel_id:id,status:'pairing',code:result.code,expires_in_ms:expires};
  }
  async function send(id,key,{to,text,operation_id,isCurrent:operationCurrent=()=>true,source='automation',control_revision}){
    const c=contexts.get(id);if(!c)throw Error('channel_not_started');
    await validate(c,key);const phone=validPhone(to);
    if(typeof operationCurrent!=='function'||operationCurrent()!==true)throw Error('channel_operation_superseded');
    if(!c.allowed.has(phone))throw Error('channel_sender_denied');
    if(typeof text!=='string'||!text.trim()||text.length>1200)throw Error('invalid_channel_message');
    const fingerprint=digest(JSON.stringify([phone,text])),reserved=c.ledger.reserve(operation_id,fingerprint);
    if(!reserved.fresh)return publicSend(operation_id,reserved.record);
    try{await c.capacity.acquire({isCurrent:()=>current(c)&&operationCurrent()===true});}
    catch{
      const refused={fingerprint,state:'refused',accepted:false,delivery:'not_sent'};
      c.ledger.save(operation_id,refused);return publicSend(operation_id,refused);
    }
    // The durable uncertain record exists before any network send. A process
    // crash or timeout can never cause this operation to be sent a second time.
    let attempted=false;
    try{
      await validate(c,key);
      if(!c.session||operationCurrent()!==true)throw Error('channel_not_connected_or_superseded');
      const slot={phone,operation_id,fingerprint,source,text,...(['assistant','manual'].includes(source)?{control_revision}:{} )};
      if(c.inboxControl===true||control_revision!==undefined){
        if(typeof broker.authorizeSend!=='function')throw Error('inbox_control_unavailable');
        const permit=await broker.authorizeSend(c.id,c.key,{owner,fence:c.fence},slot);
        if(permit?.authorized!==true||permit.operation_id!==operation_id)throw Error('inbox_send_refused');
      }
      attempted=true;
      const result=await c.session.send(phone,text,{isCurrent:operationCurrent});requireCurrent(c);
      if(typeof result?.id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(result.id))throw Error('channel_send_unknown');
      const record={fingerprint,state:'accepted',accepted:true,delivery:'unknown',provider_id:result.id};
      c.ledger.save(operation_id,record);c.sent.set(result.id,{operation:operation_id,fingerprint,phone});
      if(c.inboxControl===true||control_revision!==undefined){
        // A lost finish response leaves a conservative slot. It is released
        // only after the API verifies this already persisted receipt.
        try{await broker.finishSend(c.id,c.key,{owner,fence:c.fence},{...slot,accepted:true,provider_id:result.id});}catch{}
      }
      if(c.sent.size>1000)c.sent.delete(c.sent.keys().next().value);
      return publicSend(operation_id,record);
    }catch{
      if(!attempted){const refused={fingerprint,state:'refused',accepted:false,delivery:'not_sent'};c.ledger.save(operation_id,refused);return publicSend(operation_id,refused);}
      return publicSend(operation_id,reserved.record);
    }
  }
  async function operation(id,key,operationId){
    if(!channelId(id)||!channelId(operationId))throw Error('invalid_operation_id');
    // A paused channel/company can reconcile history under its current owner's
    // credential. This distinct broker grant never authorizes a live socket.
    const identity=await broker.lease(id,key,'receipt',owner);let record;
    if(identity?.channel_id!==id||!channelId(identity.tenant_id)||identity.provider!=='baileys'||identity.read_only!==true)throw Error('invalid_receipt_identity');
    try{
      const directory=channelDirectory(root,identity.tenant_id,id,{create:false});
      record=sendLedger(directory,key,{readOnly:true}).get(operationId);
    }catch(error){if(error.code!=='ENOENT')throw error;}
    return publicSend(operationId,record||{state:'unrecorded',accepted:false,delivery:'unknown'});
  }
  return {start,status,pair,send,operation,
    async resume(){
      if(!resumeStore||resuming)return {resumed:0};
      let resumed=0;resuming=true;
      try{
        for(const row of resumeStore.list()){
          if(contexts.has(row.id))continue;
          if(contexts.size>=maxChannels)break;
          try{await start(row.id,row.key,{resumeOnly:true,expectedTenant:row.tenant});resumed++;}
          catch(error){if([401,403,404,423].includes(error?.status)||
            ['channel_auth_unavailable','resume_tenant_changed'].includes(error?.message))resumeStore.forget(row.id,row.key);}
        }
        return {resumed};
      }finally{resuming=false;}
    },
    async qr(id,key){const c=contexts.get(id);if(!c)throw Error('channel_not_started');await validate(c,key);return c.session?.qr?.()||null;},
    async stop(id,key){const c=contexts.get(id);if(c&&!sameKey(c.key,key))throw Error('channel_unauthorized');
      if(stopping.has(id))throw Error('channel_stopping');stopping.add(id);
      try{
        if(!c){await broker.lease(id,key,'inspect',owner);resumeStore?.forget(id,key);return;}
        await c.starting;await validate(c,key);resumeStore?.forget(id,key);retire(c);
        try{await broker.lease(id,key,'release',owner,c.fence);}catch{}
      }finally{stopping.delete(id);}},
    close(){for(const c of contexts.values())retire(c);},
    get size(){return contexts.size;}};
}
function publicSend(id,record){return {operation_id:id,state:record.state,accepted:record.accepted===true,delivery:record.delivery};}
function deterministicOperation(channel,message){
  const hex=digest(channel+'\0'+message).slice(0,32);
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
