/** Baileys 6 QR pairing leaves `registered` false. Classify the authenticated
 * companion bundle instead of treating a phone-code lifecycle flag as proof.
 * This never creates credentials, modifies keys, or authorizes a transport lease.
 */
import * as libsignal from 'libsignal';
const bytes=(value,length)=>value instanceof Uint8Array&&value.byteLength===length;
const pair=value=>value&&bytes(value.public,32)&&bytes(value.private,32);
const primaryPn=/^[1-9]\d{7,14}@s\.whatsapp\.net$/;
const companionPn=/^([1-9]\d{7,14}):([1-9]\d{0,4})@s\.whatsapp\.net$/;

// Protobuf account.toJSON runs before BufferJSON.replacer, so an existing
// encrypted account legitimately reloads its byte fields as canonical base64.
function accountBytes(value,length){
  if(value instanceof Uint8Array)return (!length||value.byteLength===length)?Buffer.from(value):null;
  if(typeof value!=='string'||!value||value.length>(length?Math.ceil(length/3)*4:5464)||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(value))return null;
  const decoded=Buffer.from(value,'base64');
  return decoded.toString('base64')===value&&(!length||decoded.length===length)?decoded:null;
}
// The pinned SDK's Curve.verify catches exceptions but discards a false return.
// Use the same existing Signal primitive and check its boolean result directly.
const verify=(key,message,signature)=>libsignal.curve.verifySignature(Buffer.concat([Buffer.from([5]),key]),message,signature)===true;

export function classifyPairedAuth(creds,{proto}={}){
  try{
    if(!creds||typeof creds!=='object'||Array.isArray(creds)||
        (creds.registered!==undefined&&typeof creds.registered!=='boolean')||
        typeof creds.advSecretKey!=='string'||!accountBytes(creds.advSecretKey,32)||
        !pair(creds.noiseKey)||!pair(creds.signedIdentityKey)||!pair(creds.signedPreKey?.keyPair)||
        !bytes(creds.signedPreKey.signature,64)||!Number.isSafeInteger(creds.signedPreKey.keyId)||creds.signedPreKey.keyId<0||
        !Number.isSafeInteger(creds.registrationId)||creds.registrationId<0||creds.registrationId>16383)return 'invalid';
    if(!creds.me&&!creds.account)return creds.registered===true?'invalid':'unlinked';
    const id=creds.me?.id;
    if(!creds.account){
      // requestPairingCode sets this provisional primary PN before the signed
      // pair-success response. Even registered=true at companion_finish is
      // still not a complete, resumable companion identity.
      return typeof id==='string'&&primaryPn.test(id)&&typeof creds.pairingCode==='string'&&
        /^[a-zA-Z0-9]{8}$/.test(creds.pairingCode)?'pending':'invalid';
    }
    const companion=typeof id==='string'&&id.match(companionPn);
    if(!companion||Number(companion[2])>65535)return 'invalid';
    const account=creds.account,details=accountBytes(account.details),accountKey=accountBytes(account.accountSignatureKey,32),
      accountSignature=accountBytes(account.accountSignature,64),deviceSignature=accountBytes(account.deviceSignature,64);
    if(!details?.length||details.length>4096||!accountKey||!accountSignature||!deviceSignature||
        typeof proto?.ADVDeviceIdentity?.decode!=='function')return 'invalid';
    const identity=proto.ADVDeviceIdentity.decode(details);
    if(!Number.isSafeInteger(identity.keyIndex)||identity.keyIndex<0)return 'invalid';
    const ownKey=creds.signedIdentityKey.public;
    const accountMessage=Buffer.concat([Buffer.from([6,0]),Buffer.from(details),Buffer.from(ownKey)]);
    if(!verify(accountKey,accountMessage,accountSignature))return 'invalid';
    // configureSuccessfulPairing uses 6,1 for ordinary accounts and 6,6 for
    // hosted accounts. Both bind the server bundle to this companion's key.
    const verifiesDevice=prefix=>verify(ownKey,Buffer.concat([Buffer.from(prefix),Buffer.from(details),
      Buffer.from(ownKey),accountKey]),deviceSignature);
    return verifiesDevice([6,1])||verifiesDevice([6,6])?'paired':'invalid';
  }catch{return 'invalid';}
}
