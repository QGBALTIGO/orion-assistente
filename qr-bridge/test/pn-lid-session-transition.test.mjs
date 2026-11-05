/** Actual pinned Signal, synthetic peers, no network or production auth.
 * This models an author-address transition; it does not establish which
 * address WhatsApp used on a physical customer's phone in an incident. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as sdk from '@whiskeysockets/baileys';
import {protectSignalSessionLogs} from '../src/signal-log-safety.mjs';
import {makePilotDeviceCache,resolvePilotRecipient} from '../src/pilot-recipient.mjs';
import {sendStoredProtocol} from '../src/reliable-send.mjs';

protectSignalSessionLogs();
const alicePn='5567999999901@s.whatsapp.net',aliceLid='100000000000001@lid';
const bobPn='5567999999902@s.whatsapp.net',bobLid='100000000000002@lid';
const clone=value=>JSON.parse(JSON.stringify(value,sdk.BufferJSON.replacer),sdk.BufferJSON.reviver);
const message=text=>Buffer.from(sdk.proto.Message.encode(sdk.proto.Message.fromObject({extendedTextMessage:{text}})).finish());

function peer(){
  const rows=new Map(),state={creds:sdk.initAuthCreds(),keys:{
    async get(type,ids){return Object.fromEntries(ids.map(id=>[id,clone(rows.get(type+':'+id)??null)]));},
    async set(data){for(const[type,entries]of Object.entries(data))for(const[id,value]of Object.entries(entries))rows.set(type+':'+id,clone(value));}
  }};
  return {state,rows,repository:sdk.DEFAULT_CONNECTION_CONFIG.makeSignalRepository(state),preKeyId:0};
}

async function establish(from,to,jid){
  const preKey=sdk.Curve.generateKeyPair(),id=++to.preKeyId,c=to.state.creds;
  await to.state.keys.set({'pre-key':{[id]:preKey}});
  await from.repository.injectE2ESession({jid,session:{registrationId:c.registrationId,
    identityKey:sdk.generateSignalPubKey(c.signedIdentityKey.public),
    signedPreKey:{keyId:c.signedPreKey.keyId,publicKey:sdk.generateSignalPubKey(c.signedPreKey.keyPair.public),signature:c.signedPreKey.signature},
    preKey:{keyId:id,publicKey:sdk.generateSignalPubKey(preKey.public)}}});
}

async function transmit(from,to,target,author,text){
  const packet=await from.repository.encryptMessage({jid:target,data:message(text)});
  const plain=await to.repository.decryptMessage({jid:author,...packet});
  assert.deepEqual(Buffer.from(plain),message(text));return packet;
}

function sessionHash(peer,jid){
  const row=peer.rows.get('session:'+peer.repository.jidToSignalProtocolAddress(jid));
  return row?createHash('sha256').update(JSON.stringify(row)).digest('hex'):null;
}

test('an incoming LID alias cannot locate the existing PN ratchet without an authenticated association',async()=>{
  const alice=peer(),bob=peer();await establish(alice,bob,bobPn);
  await transmit(alice,bob,bobPn,alicePn,'synthetic readable direct test');
  const reply=await bob.repository.encryptMessage({jid:alicePn,data:message('lista')});
  assert.equal(reply.type,'msg');
  assert.notEqual(alice.repository.jidToSignalProtocolAddress(bobPn),alice.repository.jidToSignalProtocolAddress(bobLid));
  await assert.rejects(alice.repository.decryptMessage({jid:bobLid,...reply}),/No session record/);
  const recovered=await alice.repository.decryptMessage({jid:bobPn,...reply});
  assert.deepEqual(Buffer.from(recovered),message('lista'),'The ciphertext is valid; its stored peer address determines whether the state is found');
});

test('negative control: separate PN and LID states alone do not break a retained receiver author branch',async()=>{
  const alice=peer(),bob=peer();await establish(alice,bob,bobPn);
  await transmit(alice,bob,bobPn,aliceLid,'synthetic readable direct test');
  const original=sessionHash(alice,bobPn);
  await establish(bob,alice,aliceLid);
  await transmit(bob,alice,aliceLid,bobLid,'lista');
  assert.equal(sessionHash(alice,bobPn),original,'The LID handshake does not mutate the PN record');
  await transmit(alice,bob,bobPn,aliceLid,'synthetic numbered menu');
});

test('a historical PN send fails against the new LID author branch while the established LID route decrypts',async()=>{
  const alice=peer(),bob=peer();await establish(alice,bob,bobPn);
  await transmit(alice,bob,bobPn,alicePn,'synthetic historical PN conversation');
  await transmit(bob,alice,alicePn,bobPn,'synthetic historical acknowledgement');
  const direct=await transmit(alice,bob,bobPn,alicePn,'synthetic readable direct test');
  assert.equal(direct.type,'msg');
  const original=sessionHash(alice,bobPn);
  await establish(bob,alice,aliceLid);
  const incoming=await transmit(bob,alice,aliceLid,bobLid,'lista');
  assert.equal(incoming.type,'pkmsg');
  assert.equal(sessionHash(alice,bobPn),original,'Receiving a new LID branch leaves the historical PN ratchet untouched');
  const auto=await alice.repository.encryptMessage({jid:bobPn,data:message('synthetic numbered menu')});
  assert.equal(auto.type,'msg');
  await assert.rejects(bob.repository.decryptMessage({jid:aliceLid,...auto}),/No matching sessions found/);
  const priorAuthor=await bob.repository.decryptMessage({jid:alicePn,...auto});
  assert.deepEqual(Buffer.from(priorAuthor),message('synthetic numbered menu'));
  const phone=bobPn.split('@')[0];
  const proof=await resolvePilotRecipient({phone,pilotPhone:phone,enabled:true,deviceCache:makePilotDeviceCache(),
    socket:{user:{id:alicePn.replace('@',':4@'),lid:aliceLid.replace('@',':4@')},
      onWhatsApp:async()=>[{jid:bobPn,lid:bobLid,exists:true}],getUSyncDevices:async()=>[]}});
  const canonical=await transmit(alice,bob,proof.jid,aliceLid,'synthetic numbered menu');
  assert.equal(canonical.type,'msg','The current LID branch works without refreshing or erasing any session');
});

test('the existing verified LID proof cannot be reused to encrypt a menu to another peer',async()=>{
  const phone=bobPn.split('@')[0],otherLid='100000000000003@lid';let calls=0;
  const socket={user:{id:alicePn.replace('@',':4@'),lid:aliceLid.replace('@',':4@')},
    onWhatsApp:async()=>[{jid:bobPn,lid:bobLid,exists:true}],getUSyncDevices:async()=>[]};
  const options={phone,pilotPhone:phone,enabled:true,socket,deviceCache:makePilotDeviceCache()};
  const proof=await resolvePilotRecipient(options);
  await assert.rejects(sendStoredProtocol({jid:otherLid,recipientProof:proof,
    makeMessage:async()=>{calls++;},relay:async()=>{calls++;},store:{save(){calls++;return true;}}}),/invalid_native_transport/);
  assert.equal(calls,0);
  socket.onWhatsApp=async()=>[{jid:alicePn,lid:bobLid,exists:true}];
  await assert.rejects(resolvePilotRecipient(options),/Nenhuma mensagem foi enviada/);
});
