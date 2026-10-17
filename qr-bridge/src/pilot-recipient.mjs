// Only a contemporary, exact PN lookup may authorize a LID for the pilot.
// Keep the phone as application metadata; never derive it from an opaque LID.
import {AsyncLocalStorage} from 'node:async_hooks';
const proofs=new WeakMap();
const caches=new WeakSet();
const pn=/^[1-9]\d{7,14}$/;
const primaryLid=/^\d{1,30}@lid$/;
const ownLidWithDevice=/^\d{1,30}:\d{1,5}@lid$/;
const identityError=()=>Error('Não foi possível confirmar a identidade do destinatário. Nenhuma mensagem foi enviada.');

// Baileys does not cache empty USync results. Scope a checked snapshot to this
// relay, including empty lists, without changing cache behavior for other work.
export function makePilotDeviceCache(base=new Map()){
  const scope=new AsyncLocalStorage();
  const cache={
    get(key){const snapshot=scope.getStore();return snapshot?.has(key)?snapshot.get(key):base.get(key);},
    set(key,value){return base.set(key,value);},
    run(snapshot,work){return scope.run(snapshot,work);}
  };
  caches.add(cache);
  return cache;
}

export async function resolvePilotRecipient({phone,pilotPhone,enabled=false,socket,deviceCache,isCurrent=()=>true}){
  if(typeof phone!=='string'||!pn.test(phone))throw identityError();
  const normal={jid:phone+'@s.whatsapp.net',phone,kind:'pn'};
  if(!enabled||phone!==pilotPhone)return normal;
  const ownAddress=typeof socket?.user?.id==='string'&&socket.user.id.match(/^([1-9]\d{7,14}):(\d{1,5})@s\.whatsapp\.net$/);
  const ownPn=ownAddress?.[1],ownDevice=Number(ownAddress?.[2]);
  const ownLid=socket?.user?.lid;
  // Pinned Baileys 6.7.24 uses me.lid.split(':')[0] for its own LID fanout.
  // A primary-only own LID would produce a malformed address in that version.
  if(!ownPn||ownPn===phone||typeof ownLid!=='string'||!ownLidWithDevice.test(ownLid)||
      typeof socket?.onWhatsApp!=='function'||typeof socket?.getUSyncDevices!=='function'||
      !caches.has(deviceCache)||!isCurrent())throw identityError();
  let results;
  try{results=await socket.onWhatsApp(phone);}catch{throw identityError();}
  if(!isCurrent()||!Array.isArray(results))throw identityError();
  const matches=results.filter(row=>row?.jid===normal.jid);
  if(matches.length!==1||matches[0].exists!==true||typeof matches[0].lid!=='string'||
      !primaryLid.test(matches[0].lid))throw identityError();
  const jid=matches[0].lid,ownLidPrimary=ownLid.replace(/:\d+(?=@)/,'');
  if(jid===ownLidPrimary||jid.split('@')[0]===ownPn)throw identityError();
  // Refresh and validate the exact cache the pinned relay will consume. Its
  // extractor loses domains: an own-LID companion or recipient-PN companion
  // cannot safely be reinterpreted by that relay. Stop before creating a message.
  let devices;
  try{devices=await socket.getUSyncDevices([socket.user.id,jid],false,true);}catch{throw identityError();}
  if(!isCurrent()||!Array.isArray(devices)||devices.length>200)throw identityError();
  const recipientUser=jid.split('@')[0];
  for(const device of devices){
    if(!device||![ownPn,recipientUser].includes(device.user)||
        !Number.isSafeInteger(device.device)||device.device<=0||
        (device.user===ownPn&&device.device===ownDevice))throw identityError();
  }
  const snapshot=new Map([ownPn,recipientUser].map(user=>[user,Object.freeze(devices
    .filter(device=>device.user===user).map(device=>Object.freeze({user:device.user,device:device.device})))]));
  const proof=Object.freeze({jid,phone,kind:'lid'});
  proofs.set(proof,{deviceCache,snapshot});
  return proof;
}

export function isVerifiedPilotLid(proof,jid){
  return !!proof&&proofs.has(proof)&&proof.jid===jid&&primaryLid.test(jid);
}

export function withVerifiedPilotDevices(proof,work){
  const verified=proofs.get(proof);
  return verified?verified.deviceCache.run(verified.snapshot,work):work();
}
