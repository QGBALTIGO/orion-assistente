/** Names already delivered by Baileys 6.7.24, never a directory/number lookup.
 * Contact.id/lid/jid, notify/name/verifiedName and chats.phoneNumberShare are
 * the pinned version's metadata contract. Message bodies are never inspected.
 */
import fs from 'node:fs';
import path from 'node:path';
import {createCipheriv,createDecipheriv,createHmac,hkdfSync,randomBytes} from 'node:crypto';

export const CONTACT_NAME_LIMIT=2000;
export const CONTACT_NAME_TTL_MS=30*86400000;
export const CONTACT_NAME_SOURCES=Object.freeze(['contact_name','verified_name','push_name']);
const rank={contact_name:3,verified_name:2,push_name:1};
const placeholders=new Set(['cliente whatsapp','cliente do whatsapp','contato whatsapp','contato do whatsapp','whatsapp',
  'cliente','contato','sem nome','nome nao informado','desconhecido','unknown',
  'whatsapp customer','whatsapp contact']);
export const contactPhone=value=>typeof value==='string'&&/^[1-9]\d{7,14}$/.test(value)?value:null;
export function cleanContactName(value){
  if(typeof value!=='string'||value.length>1024)return null;
  const name=value.normalize('NFKC').trim().replace(/\s+/gu,' ');
  const pictogram=/[\p{So}\p{Sk}]/u.test(name);
  const controls=pictogram?name.replace(/\u200d/g,''):name;
  if(!name||[...name].length>120||! /[\p{L}\p{So}\p{Sk}]/u.test(name)||/\p{C}/u.test(controls)||
      /@(?:lid|s\.whatsapp\.net|g\.us)\b/i.test(name))return null;
  const key=name.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();
  return placeholders.has(key)?null:name;
}
export function cleanContactProfile(value){
  const name=cleanContactName(value?.name);
  return name&&CONTACT_NAME_SOURCES.includes(value?.source)?{name,source:value.source}:null;
}
function peerJid(value){
  if(typeof value!=='string')return null;
  const pn=value.match(/^([1-9]\d{7,14})(?::\d+)?@s\.whatsapp\.net$/);
  if(pn)return pn[1]+'@s.whatsapp.net';
  const lid=value.match(/^(\d{8,20})(?::\d+)?@lid$/);
  return lid?lid[1]+'@lid':null;
}
const pnOf=jid=>jid?.endsWith('@s.whatsapp.net')?jid.split('@')[0]:null;
const lidOf=jid=>jid?.endsWith('@lid')?jid:null;
function identityHints(id,values){
  const jid=peerJid(id);
  if(!jid)return null;
  const ids=[jid,...values.filter(x=>x!==undefined&&x!==null&&x!=='').map(peerJid)];
  if(ids.some(x=>!x))return null;
  const pns=new Set(ids.filter(x=>pnOf(x))),lids=new Set(ids.filter(x=>lidOf(x)));
  if(pns.size>1||lids.size>1)return null;
  return {jid,pn:[...pns][0]||null,lid:[...lids][0]||null};
}
function contactMetadata(contact,historical){
  const identity=identityHints(contact?.id,[contact?.jid,contact?.lid]);
  if(!identity)return null;
  const names=[['contact_name',contact.name],['verified_name',contact.verifiedName],['push_name',contact.notify]];
  const candidate=names.map(([source,value])=>({name:cleanContactName(value),source})).find(x=>x.name);
  return {...identity,...candidate,historical};
}
function messageMetadata(message,historical){
  // A sender's pushName on an outgoing/peer-routed message belongs to us.
  // Require explicit incoming direction and a private remote JID.
  const key=message?.key;
  if(key?.fromMe!==false)return null;
  const identity=identityHints(key.remoteJid,[key.remoteJidAlt,key.senderPn,key.senderLid,
    key.participant,key.participantPn,key.participantLid]);
  if(!identity)return null;
  const verified=cleanContactName(message.verifiedBizName),push=cleanContactName(message.pushName);
  return {...identity,name:verified||push,source:verified?'verified_name':'push_name',historical};
}

export function requestedContactPhones(input){
  if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(k=>k!=='phones')||
      !Array.isArray(input.phones)||input.phones.length<1||input.phones.length>100||
      input.phones.some(phone=>!contactPhone(phone))||new Set(input.phones).size!==input.phones.length)
    throw Error('invalid_contact_phones');
  return [...input.phones];
}
export function requestedContactQuery(input){
  if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(k=>k!=='q')||
      typeof input.q!=='string'||input.q.length>1024)throw Error('invalid_contact_query');
  const query=input.q.normalize('NFKC').trim().replace(/\s+/gu,' ');
  const controls=/[\p{So}\p{Sk}]/u.test(query)?query.replace(/\u200d/g,''):query;
  if(!query||[...query].length>120||/\p{C}/u.test(controls))throw Error('invalid_contact_query');
  return query;
}
const searchKey=value=>value.normalize('NFKD').replace(/\p{M}/gu,'').toLowerCase();

/** One encrypted registry per auth directory, additionally bound to our PN.
 * Only names and explicit PN/LID associations are retained: 2,000 records and
 * aliases, 30 days since observation; pre-identity metadata is capped at 500.
 * Corrupt/unreadable data is not replaced. Logout explicitly purges this file.
 */
export function makeContactNames({directory,secret,now=Date.now,limit=CONTACT_NAME_LIMIT,
  ttlMs=CONTACT_NAME_TTL_MS,pendingLimit=500}){
  if(!directory?.startsWith('/data/')&&process.env.ORION_CONTACT_TEST_MODE!=='1')
    throw Error('Private volume required');
  if(typeof secret!=='string'||Buffer.byteLength(secret)<32)throw Error('Missing encryption secret');
  if(!Number.isSafeInteger(limit)||limit<1||limit>CONTACT_NAME_LIMIT||!Number.isFinite(ttlMs)||
      ttlMs<=0||ttlMs>CONTACT_NAME_TTL_MS||!Number.isSafeInteger(pendingLimit)||pendingLimit<1||pendingLimit>500)
    throw Error('Invalid contact bounds');
  fs.mkdirSync(directory,{recursive:true,mode:0o700});
  const key=Buffer.from(hkdfSync('sha256',Buffer.from(secret),Buffer.from('orion/contact-names/v1'),
    Buffer.from(path.basename(directory)),32));
  const file=path.join(directory,'contact-names.v1.enc'),aad=Buffer.from('orion/contact-names/v1');
  let health='ready',identity=null,activePhone=null,ownLids=new Set(),records=new Map(),aliases=new Map(),pending=[];
  const validAt=at=>Number.isFinite(at)&&at>=now()-ttlMs&&at<=now()+60000;
  function prune(){
    let changed=false;
    for(const [jid,row] of records)if(!validAt(row.at)){records.delete(jid);changed=true;}
    for(const [lid,row] of aliases)if(!validAt(row.at)){aliases.delete(lid);changed=true;}
    for(const map of [records,aliases]){
      if(map.size>limit){
        const oldest=[...map].sort((a,b)=>a[1].at-b[1].at).slice(0,map.size-limit);
        for(const [id] of oldest)map.delete(id);
        changed=true;
      }
    }
    return changed;
  }
  function save(){
    if(health!=='ready')return false;
    let temporary;
    try{
      prune();
      const iv=randomBytes(12),cipher=createCipheriv('aes-256-gcm',key,iv);
      cipher.setAAD(aad);
      const payload=JSON.stringify({v:1,identity,ownLids:[...ownLids],records:[...records],aliases:[...aliases]});
      const data=Buffer.concat([cipher.update(payload,'utf8'),cipher.final()]);
      temporary=file+'.'+randomBytes(8).toString('hex')+'.tmp';
      fs.writeFileSync(temporary,Buffer.concat([iv,cipher.getAuthTag(),data]),{mode:0o600,flag:'wx'});
      fs.renameSync(temporary,file);return true;
    }catch{health='write_failed';return false;}
    finally{if(temporary)try{fs.rmSync(temporary,{force:true});}catch{}}
  }
  if(fs.existsSync(file)){
    try{
      if(fs.statSync(file).size>4*1024*1024)throw Error('Contact file too large');
      const buf=fs.readFileSync(file),decipher=createDecipheriv('aes-256-gcm',key,buf.subarray(0,12));
      decipher.setAAD(aad);decipher.setAuthTag(buf.subarray(12,28));
      const state=JSON.parse(Buffer.concat([decipher.update(buf.subarray(28)),decipher.final()]).toString('utf8'));
      if(state?.v!==1||! /^(?:[a-f0-9]{64})$/.test(state.identity)||!Array.isArray(state.records)||
          !Array.isArray(state.aliases)||state.records.length>CONTACT_NAME_LIMIT||state.aliases.length>CONTACT_NAME_LIMIT||
          !Array.isArray(state.ownLids)||state.ownLids.length>4)throw Error('Invalid contact registry');
      identity=state.identity;ownLids=new Set(state.ownLids.filter(id=>peerJid(id)===id&&lidOf(id)));
      for(const [jid,row] of state.records){
        const profile=cleanContactProfile(row);
        if(peerJid(jid)===jid&&profile&&validAt(row.at))records.set(jid,{...profile,at:row.at});
      }
      for(const [lid,row] of state.aliases){
        if(peerJid(lid)===lid&&lidOf(lid)&&validAt(row?.at)&&
            (row.jid===null||peerJid(row.jid)===row.jid&&pnOf(row.jid)))aliases.set(lid,{jid:row.jid,at:row.at});
      }
      prune();
    }catch{health='unreadable';identity=null;ownLids.clear();records.clear();aliases.clear();}
  }
  const isOwn=jid=>pnOf(jid)===activePhone||ownLids.has(jid);
  const available=()=>health==='ready'&&!!activePhone;
  const refreshDue=at=>now()-at>=Math.min(3600000,ttlMs/2);
  function apply(rows){
    if(health!=='ready')return false;
    if(!activePhone){
      pending.push(...rows.filter(Boolean));
      if(pending.length>pendingLimit)pending.splice(0,pending.length-pendingLimit);
      return false;
    }
    let changed=prune(),namesChanged=false;
    for(const row of rows){
      if(!row||isOwn(row.jid)||row.pn&&isOwn(row.pn)||row.lid&&isOwn(row.lid))continue;
      if(row.pn&&row.lid){
        const previous=aliases.get(row.lid);
        // Conflicting mappings are quarantined; never move a cached LID name
        // from one person's phone to another. Direct PN names stay independent.
        const jid=previous&&previous.jid!==row.pn?null:row.pn;
        if(!previous||previous.jid!==jid){aliases.set(row.lid,{jid,at:now()});changed=namesChanged=true;}
        else if(!row.historical&&refreshDue(previous.at)){previous.at=now();changed=true;}
      }
      const profile=cleanContactProfile(row);
      if(!profile)continue; // Partial/photo updates must not erase a known name.
      const previous=records.get(row.jid);
      if(!previous||rank[profile.source]>rank[previous.source]||
          rank[profile.source]===rank[previous.source]&&!row.historical){
        if(!previous||previous.name!==profile.name||previous.source!==profile.source){
          namesChanged=true;records.set(row.jid,{...profile,at:now()});changed=true;
        }else if(refreshDue(previous.at)){previous.at=now();changed=true;}
      }
    }
    if(changed&&!save())return false;
    return namesChanged;
  }
  function lookup(phone){
    if(!available()||!contactPhone(phone)||phone===activePhone)return null;
    const jid=phone+'@s.whatsapp.net',candidates=[];
    const direct=records.get(jid);
    if(direct&&validAt(direct.at))candidates.push(direct);
    for(const [lid,alias] of aliases){
      if(alias.jid!==jid||!validAt(alias.at)||ownLids.has(lid))continue;
      const row=records.get(lid);
      if(row&&validAt(row.at))candidates.push(row);
    }
    candidates.sort((a,b)=>rank[b.source]-rank[a.source]||b.at-a.at);
    const found=candidates[0];
    return found?{phone,display_name:found.name,name_source:found.source}:null;
  }
  return {
    file,health:()=>health,accountPhone:()=>available()?activePhone:null,
    bindIdentity(me){
      const own=peerJid(typeof me==='string'?me:me?.id),phone=pnOf(own);
      if(!phone||health!=='ready')return false;
      const hash=createHmac('sha256',key).update(phone).digest('hex');
      const switched=activePhone&&activePhone!==phone;
      if(identity!==hash){records.clear();aliases.clear();ownLids.clear();identity=hash;}
      if(switched)pending=[];
      activePhone=phone;
      const ownLid=peerJid(typeof me==='object'?me?.lid:null);
      if(lidOf(ownLid)){ownLids.add(ownLid);if(ownLids.size>4)ownLids.delete(ownLids.values().next().value);}
      records.delete(own);
      for(const lid of ownLids){records.delete(lid);aliases.delete(lid);}
      for(const [lid,row] of aliases)if(row.jid===own)aliases.delete(lid);
      if(!save())return false;
      const waiting=pending;pending=[];apply(waiting);
      return available();
    },
    suspend(){activePhone=null;pending=[];},
    purge(){
      activePhone=null;identity=null;pending=[];ownLids.clear();records.clear();aliases.clear();
      try{fs.rmSync(file,{force:true});health='ready';return true;}catch{health='write_failed';return false;}
    },
    observeContacts(items,{historical=false}={}){
      return apply((Array.isArray(items)?items:[]).slice(-CONTACT_NAME_LIMIT).map(item=>contactMetadata(item,historical)));
    },
    observeMessages(items,{historical=false}={}){
      const source=Array.isArray(items)?items:[];
      // Baileys history is newest first. First historical observation wins
      // within a source; old push names cannot replace a newer live label.
      return apply((historical?source.slice(0,CONTACT_NAME_LIMIT):source.slice(-CONTACT_NAME_LIMIT))
        .map(item=>messageMetadata(item,historical)));
    },
    observeResolvedMessage(message,phone,{historical=false}={}){
      // Called only after the existing local PN resolver and current-socket
      // guard have succeeded; the LID's digits are never treated as a phone.
      const row=messageMetadata(message,historical),pn=contactPhone(phone);
      if(!row||!pn||row.pn&&pnOf(row.pn)!==pn)return false;
      return apply([{...row,pn:pn+'@s.whatsapp.net'}]);
    },
    observePhoneShare(item){
      const lid=peerJid(item?.lid),jid=peerJid(item?.jid);
      return lidOf(lid)&&pnOf(jid)?apply([{jid:lid,lid,pn:jid,historical:false}]):false;
    },
    lookup,
    profile(phone){const row=lookup(phone);return row?{name:row.display_name,source:row.name_source}:undefined;},
    requested(input){
      const phones=requestedContactPhones(input);
      return {account_phone:available()?activePhone:null,items:phones.map(lookup).filter(Boolean)};
    },
    matches(input){
      const query=searchKey(requestedContactQuery(input));
      return {account_phone:available()?activePhone:null,
        phones:this.entries().filter(row=>searchKey(row.display_name).includes(query)).map(row=>row.phone)};
    },
    // Private replay into the configured Orion API only. Not an HTTP directory.
    entries(){
      if(!available())return [];
      const best=new Map();
      const consider=(phone,row)=>{
        if(!phone||phone===activePhone||!row||!validAt(row.at))return;
        const previous=best.get(phone);
        if(!previous||rank[row.source]>rank[previous.source]||
            rank[row.source]===rank[previous.source]&&row.at>previous.at)best.set(phone,row);
      };
      for(const [jid,row] of records)consider(pnOf(jid),row);
      for(const [lid,alias] of aliases)if(validAt(alias.at)&&!ownLids.has(lid))consider(pnOf(alias.jid),records.get(lid));
      return [...best].slice(0,limit).map(([phone,row])=>({phone,display_name:row.name,name_source:row.source}));
    }
  };
}

/** All listeners observe only the current socket; no client methods are called. */
export function installContactNameEvents({client,store,isCurrent,onChange=()=>{}}){
  let active=true;
  const listeners=[];
  const listen=(event,work)=>{
    const handler=input=>{if(active&&isCurrent()&&work(input))onChange();};
    client.ev.on(event,handler);listeners.push([event,handler]);
  };
  listen('contacts.upsert',items=>store.observeContacts(items));
  listen('contacts.update',items=>{
    const saved=[],profiles=[];
    for(const item of (Array.isArray(items)?items:[]).slice(-CONTACT_NAME_LIMIT)){
      // In 6.7.24 chats.js echoes message.pushName into contacts.update even
      // for an offline append. Live messages already update the registry;
      // these undated profile echoes only fill gaps, while saved-name edits
      // remain authoritative. This also avoids old history winning a race.
      (cleanContactName(item?.name)?saved:profiles).push(item);
    }
    const profileChanged=store.observeContacts(profiles,{historical:true});
    const savedChanged=store.observeContacts(saved);
    return profileChanged||savedChanged;
  });
  listen('messages.upsert',event=>store.observeMessages(event?.messages,{historical:event?.type!=='notify'}));
  listen('messaging-history.set',event=>{
    const contacts=store.observeContacts(event?.contacts,{historical:true});
    const messages=store.observeMessages(event?.messages,{historical:true});
    return contacts||messages;
  });
  listen('chats.phoneNumberShare',item=>store.observePhoneShare(item));
  listen('creds.update',update=>update?.me?.id?store.bindIdentity(update.me):false);
  return {stop(){active=false;for(const [event,handler] of listeners)client.ev.off?.(event,handler);}};
}
