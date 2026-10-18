/** A Baileys instance per context; shared helpers, no copy of the legacy server.
 * New channels initially support private pilot text, QR and receipt observation.
 * Media/calls/history importing remain disabled, never routed to the legacy AI.
 */
import path from 'node:path';
import {encryptedChannelStore,privateDirectory} from './channel-storage.mjs';
import {extractText,resolveMessagePhone,normalizeFromJid,freshQR} from './core.mjs';
import {shouldIngestUpsert,timestampMs} from './linked-session-health.mjs';
import {deliveryFromAck} from './message-retry-store.mjs';

const silent={level:'silent',trace(){},debug(){},info(){},warn(){},error(){},fatal(){},child(){return this;}};
export async function createBaileysChannelSession(context,{loadBaileys=()=>import('@whiskeysockets/baileys'),now=Date.now}={}){
  await context.assertCurrent();
  const mod=await loadBaileys();await context.assertCurrent();
  const makeSocket=mod.default,openedAt=now();
  const store=encryptedChannelStore({directory:privateDirectory(path.join(context.directory,'auth')),secret:context.secret,
    serialize:value=>JSON.stringify(value,mod.BufferJSON.replacer),deserialize:value=>JSON.parse(value,mod.BufferJSON.reviver)});
  const creds=store.get('creds')||mod.initAuthCreds();
  let state='connecting',qrValue=null,qrAt=0,closed=false,queue=Promise.resolve(),pending=0;
  const seen=new Set(),inbound=new Map();
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
    printQRInTerminal:false,markOnlineOnConnect:false,syncFullHistory:false,
    shouldSyncHistoryMessage:()=>false,getMessage:async()=>undefined});
  const close=()=>{if(closed)return;closed=true;state='stopped';qrValue=null;client.ev.removeAllListeners();try{client.end(undefined);}catch{}};
  client.ev.on('creds.update',()=>{write(()=>store.set('creds',creds)).catch(close);});
  client.ev.on('connection.update',event=>{
    if(!current())return close();
    if(event.qr){qrValue=event.qr;qrAt=now();state='qr_ready';}
    if(event.connection==='open'){state='connected';qrValue=null;context.onConnected();}
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
      const text=extractText(message.message);if(typeof text!=='string'||!text.trim()||text.length>1500)continue;
      if(pending>=32)continue;
      seen.add(id);if(seen.size>2000)seen.delete(seen.values().next().value);
      const peer=String(message.key.remoteJid||'');
      if(!/^\d{8,20}(?::\d+)?@(s\.whatsapp\.net|lid)$/.test(peer))continue;
      // Queue each peer independently; no global queue mixes another channel.
      pending++;
      const task=(inbound.get(peer)||Promise.resolve()).catch(()=>{}).then(async()=>{
        await guarded();const phone=await resolveMessagePhone(message.key,client);await guarded();
        const self=normalizeFromJid(client.user?.id);
        if(!phone||!self||!context.allowedSender(phone)||phone===self)return;
        await context.onMessage({phone,message_id:id,text});
      });
      inbound.set(peer,task);task.catch(()=>{}).finally(()=>{pending--;if(inbound.get(peer)===task)inbound.delete(peer);});
    }
  });
  return {close,status:()=>state,
    qr:()=>state==='qr_ready'&&freshQR(qrAt,now())?{value:qrValue,expires_in_ms:Math.max(0,45000-(now()-qrAt))}:null,
    async send(phone,text){await guarded();const self=normalizeFromJid(client.user?.id);
      if(state!=='connected'||!self||!context.allowedSender(phone)||phone===self)throw Error('channel_send_denied');
      const exists=await client.onWhatsApp(phone);await guarded();
      const recipient=exists?.find(row=>row.exists&&normalizeFromJid(row.jid)===phone);
      if(!recipient)throw Error('channel_recipient_unverified');
      const sent=await client.sendMessage(recipient.jid,{text});return {id:sent?.key?.id};}
  };
}
