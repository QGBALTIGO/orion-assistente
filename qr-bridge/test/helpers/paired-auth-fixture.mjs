/** Real pinned-SDK QR pair-success, signed only with synthetic test keys. */
import {initAuthCreds,Curve,proto,hmacSign,configureSuccessfulPairing} from '@whiskeysockets/baileys';

export function pairSuccessFixture({pn='5567999999902',device=7,registered=false,platform='ios',hosted=false}={}){
  const initialCreds=initAuthCreds(),account=Curve.generateKeyPair();
  const details=proto.ADVDeviceIdentity.encode({rawId:7,timestamp:1,keyIndex:1}).finish();
  const accountSignature=Curve.sign(account.private,Buffer.concat([Buffer.from([6,0]),details,initialCreds.signedIdentityKey.public]));
  const signed=proto.ADVSignedDeviceIdentity.encode({details,accountSignatureKey:account.public,accountSignature}).finish();
  const accountType=hosted?proto.ADVEncryptionType.HOSTED:undefined;
  const hmac=hmacSign(Buffer.concat([hosted?Buffer.from([6,5]):Buffer.alloc(0),signed]),Buffer.from(initialCreds.advSecretKey,'base64'));
  const envelope=proto.ADVSignedDeviceIdentityHMAC.encode({details:signed,hmac,...(hosted?{accountType}:{})}).finish();
  const stanza={tag:'iq',attrs:{id:'SYNTHETIC-QR-CONTRACT'},content:[{tag:'pair-success',attrs:{},content:[
    {tag:'device-identity',attrs:{},content:envelope},{tag:'device',attrs:{jid:`${pn}:${device}@s.whatsapp.net`}},
    {tag:'platform',attrs:{name:platform}}]}]};
  const {creds:update,reply}=configureSuccessfulPairing(stanza,initialCreds);
  return {creds:{...initialCreds,...update,registered},stanza,update,initialCreds,reply};
}

export function pairedAuthFixture(options){return pairSuccessFixture(options).creds;}

export function makePairedAuthFixture({phone,pn,device,registered,platform,hosted}={}){
  return pairSuccessFixture({pn:phone??pn,device,registered,platform,hosted});
}
