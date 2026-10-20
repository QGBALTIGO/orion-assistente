/** A Baileys instance per context; shared helpers, no copy of the legacy server.
 * Private allowlisted text and opt-in bounded audio; no legacy AI routing.
 * Calls/history importing remain disabled. ASR never grants booking authority.
 */
import path from 'node:path';
import {encryptedChannelStore,privateDirectory} from './channel-storage.mjs';
import {extractText,resolveMessagePhone,normalizeFromJid,freshQR} from './core.mjs';
import {shouldIngestUpsert,timestampMs} from './linked-session-health.mjs';
import {deliveryFromAck} from './message-retry-store.mjs';
import {makeEncryptedRetryStore} from './encrypted-retry-store.mjs';
import {sendStoredText} from './reliable-send.mjs';
import {makePilotDeviceCache,resolvePilotRecipient} from './pilot-recipient.mjs';
import {makeAssistantTyping} from './assistant-typing.mjs';
import {withAssistantAudio} from './ai-audio.mjs';
import {makeConversationIntake} from './conversation-intake.mjs';
import {sendConnectionNoticeOnce} from './connection-alert.mjs';

const silent={level:'silent',trace(){},debug(){},info(){},warn(){},error(){},fatal(){},child(){return this;}};
export async function createBaileysChannelSession(context,{loadBaileys=()=>import('@whiskeysockets/baileys'),now=Date.now,typingFactory=makeAssistantTyping,intakeOptions={},schedule=setTimeout,cancel=clearTimeout}={}){
  await context.assertCurrent();
  const mod=await loadBaileys();await context.assertCurrent();
  const makeSocket=mod.default,openedAt=now();
  const store=encryptedChannelStore({directory:privateDirectory(path.join(context.directory,'auth')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver)});
  const creds=store.get('creds')||mod.initAuthCreds();
  if(context.resumeOnly===true&&creds.registered!==true)throw Error('channel_auth_unavailable');
  let state='connecting',qrValue=null,qrAt=0,closed=false,queue=Promise.resolve(),identityQueue=Promise.resolve(),pending=0,noticeTimer=null,opened=false;
  const seen=new Set(),metadata=new Map(),typingByPhone=new Map(),typingLeases=new Set(),abort=new AbortController();
  const retryStore=makeEncryptedRetryStore({directory:privateDirectory(path.join(context.directory,'retry-proto')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver),now});
  const deviceCache=makePilotDeviceCache();
  const current=()=>!closed&&context.isCurrent();
  const guarded=async()=>{if(!current())throw Error('channel_socket_replaced');await context.assertCurrent();if(!current())throw Error('channel_socket_replaced');};
  const write=job=>{const next=queue.then(async()=>{await guarded();job();});queue=next.catch(()=>{});return next;};
  const auth={creds,keys:{
    async get(type,ids){await guarded();return Object.fromEntries(ids.map(id=>{
      let value=store.get('key:'+type+':'+id);
      if(type==='app-state-sync-key'&&value)value=mod.proto.Message.AppStateSyncKeyData.fromObject(value);
      return [id,value];}));},
    set:data=>write(()=>{for(const [type,entries] of Object.entries(data))for(const [id,value] of Object.entries(entries))store.set('key:'+type+':'+id,value);})}};
  const client=makeSocket({auth,logger:silent,browser:mod.Browsers.windows('Chrome'),
    printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,userDevicesCache:deviceCache,maxMsgRetryCount:5,
    shouldSyncHistoryMessage:()=>false,getMessage:async key=>{try{await guarded();const message=retryStore.get(key);return current()?message:undefined;}catch{return undefined;}}});
  const close=()=>{if(closed)return;closed=true;state='stopped';qrValue=null;cancel(noticeTimer);abort.abort();for(const lease of typingLeases)lease.stop();typingLeases.clear();client.ev.removeAllListeners();try{client.end(undefined);}catch{}};
  const groupSetting=(name,fallback,max)=>{const raw=process.env[name],value=Number(raw);
    return raw!==undefined&&Number.isSafeInteger(value)&&value>=0&&value<=max?value:fallback;};
  const settleMs=groupSetting('QR_AI_GROUP_WAIT_MS',350,2000);
  const intake=makeConversationIntake({settleMs,maxGroupMs:Math.max(settleMs,groupSetting('QR_AI_GROUP_MAX_WAIT_MS',1200,5000)),
    ...intakeOptions,
    onRejected:async batch=>{const first=metadata.get(batch.messageIds[0]);if(first)context.onRejected?.({phone:first.phone});},
    consume:async batch=>{
      const entries=batch.messages.map(item=>metadata.get(item.id)),first=entries[0];
      const batchCurrent=()=>current()&&batch.isCurrent()&&!!first&&entries.every(item=>item?.phone===first.phone&&context.allowedSender(item.phone));
      if(!batchCurrent())return {state:'superseded',not_committed:true};
      await guarded();
      return context.onMessage({phone:first.phone,message_id:batch.messages[0].id,message_ids:batch.messages.map(item=>item.id),
        text:batch.text,intake_revision:batch.intakeRevision},{noticeProofs:entries.map(item=>item.proof),isCurrent:batchCurrent,
        beforeReply:value=>entries.at(-1)?.lease?.beforeReply(value)});
    }});
  client.ev.on('creds.update',()=>{write(()=>store.set('creds',creds)).catch(close);});
  client.ev.on('connection.update',event=>{
    if(!current())return close();
    if(event.qr){qrValue=event.qr;qrAt=now();state='qr_ready';}
    if(event.connection==='open'){
      state='connected';qrValue=null;
      if(!opened){opened=true;
        void Promise.resolve().then(()=>context.onConnected()).then(()=>{
          if(context.notifySelf!==true||!current())return;
          noticeTimer=schedule(async()=>{
            try{
              await queue;await guarded();
              await sendConnectionNoticeOnce({authDir:path.join(context.directory,'auth'),client,isCurrent:current,
                sendText:async(jid,text)=>{
                  await guarded();
                  return sendStoredText({jid,text,store:retryStore,freshDevices:true,isCurrent:current,
                    makeMessage:(to,body)=>mod.generateWAMessage(to,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
                    relay:async(to,body,options)=>{await guarded();return client.relayMessage(to,body,options);}});
                }});
            }catch{/* Preserve the receipt; never retry an ambiguous notice. */}
          },2500);noticeTimer?.unref?.();
        }).catch(close);
      }
    }
    if(event.connection==='close'){
      const code=event.lastDisconnect?.error?.output?.statusCode;
      close();context.onDisconnected(code===mod.DisconnectReason.loggedOut?'logged_out':'disconnected');
    }
  });
  client.ev.on('messages.update',updates=>{
    for(const item of updates){const delivery=deliveryFromAck(item.update?.status);
      if(current()&&['delivered','read'].includes(delivery))context.onReceipt(item.key?.id,delivery).catch(()=>{});}
  });
  client.ev.on('messages.upsert',({messages=[],type})=>{
    for(const message of messages.slice(0,100)){
      const id=message.key?.id,at=timestampMs(message.messageTimestamp);
      if(!current()||message.key?.fromMe||typeof id!=='string'||!/^[a-zA-Z0-9._:-]{1,160}$/.test(id)||seen.has(id)||
          !shouldIngestUpsert(type,message.messageTimestamp,now())||!at||at<openedAt-1000)continue;
      const text=extractText(message.message),media=message.message?.audioMessage||message.message?.ephemeralMessage?.message?.audioMessage;
      if((typeof text!=='string'||!text.trim())&&!media||typeof text==='string'&&text.length>1500)continue;
      if(pending>=32)continue;
      seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);
      const peer=String(message.key.remoteJid||'');
      if(!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(peer))continue;
      // Only identity resolution is ordered here. Never wait for ASR or an AI
      // reply before registering a newer authenticated message's revision.
      pending++;
      const identityTask=identityQueue.catch(()=>{}).then(async()=>{
        await guarded();const phone=await resolveMessagePhone(message.key,client);await guarded();
        const self=normalizeFromJid(client.user?.id);
        if(!phone||!self||!context.allowedSender(phone)||phone===self)return null;
        const entry={phone};metadata.set(id,entry);
        const entryCurrent=()=>current()&&context.allowedSender(phone);
        const task=intake.submit({conversationKey:context.channelId+':'+phone,messageId:id,isCurrent:entryCurrent,
          notice:async()=>{const proof=await context.onInbound({phone,message_id:id});await guarded();entry.proof=proof;return proof;},
          prepare:async({signal})=>{
            await guarded();
            if(context.assistantEnabled?.()===false||(!text?.trim()&&context.audioEnabled?.()!==true))return {skip:true};
            let typing=typingByPhone.get(phone);
            if(!typing){typing=typingFactory({socket:client,pilotPhone:()=>phone,isConnected:()=>state==='connected'&&current(),
              isAllowed:value=>context.allowedSender(value)&&context.assistantEnabled?.()!==false});typingByPhone.set(phone,typing);}
            entry.lease=typing.begin({phone,chatJid:peer,isCurrent:entryCurrent});typingLeases.add(entry.lease);
            let body=text;
            if(!body?.trim())body=await withAssistantAudio(message,{client,downloadMediaMessage:mod.downloadMediaMessage,logger:silent,
              signal:AbortSignal.any([abort.signal,signal])},async({buffer,mediaType,signal})=>{
                await guarded();return context.transcribe({phone,buffer,mediaType,signal});});
            await guarded();if(signal.aborted)return {skip:true};return {text:body};
          }});
        // The identity queue returns immediately; only this message's cleanup
        // waits for ordered consumption (including an explicit zero-write carry).
        void task.catch(()=>{}).finally(()=>{entry.lease?.stop();typingLeases.delete(entry.lease);metadata.delete(id);pending--;});
        return true;
      });
      identityQueue=identityTask;identityTask.then(accepted=>{if(!accepted)pending--;},()=>{pending--;});
    }
  });
  return {close,status:()=>state,
    qr:()=>state==='qr_ready'&&freshQR(qrAt,now())?{value:qrValue,expires_in_ms:Math.max(0,45000-(now()-qrAt))}:null,
    async send(phone,text,{isCurrent:operationCurrent=()=>true}={}){await guarded();const self=normalizeFromJid(client.user?.id);
      const sendCurrent=()=>{try{return current()&&operationCurrent()===true;}catch{return false;}};
      if(!sendCurrent())throw Error('channel_operation_superseded');
      if(state!=='connected'||!self||!context.allowedSender(phone)||phone===self)throw Error('channel_send_denied');
      const exists=await client.onWhatsApp(phone);await guarded();
      const recipient=exists?.find(row=>row.exists&&normalizeFromJid(row.jid)===phone);
      if(!recipient)throw Error('channel_recipient_unverified');
      const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:context.verifiedLid?.()===true,
        socket:client,deviceCache,isCurrent:current});await guarded();
      const sent=await sendStoredText({jid:proof.jid,recipientProof:proof,text,store:retryStore,freshDevices:true,isCurrent:sendCurrent,
        makeMessage:(jid,body)=>mod.generateWAMessage(jid,{text:body},{userJid:client.user.id,logger:silent,messageId:mod.generateMessageIDV2(client.user.id)}),
        relay:async(jid,body,options)=>{await guarded();if(!sendCurrent())throw Error('channel_operation_superseded');return client.relayMessage(jid,body,options);}});
      return {id:sent?.key?.id};}
  };
}
