/** The actual pinned Signal implementation, synthetic peers, no network. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {BufferJSON,initAuthCreds,proto,Curve,generateSignalPubKey,DEFAULT_CONNECTION_CONFIG} from '@whiskeysockets/baileys';
import {useDurableAuthState} from '../src/durable-auth.mjs';
import {protectSignalSessionLogs} from '../src/signal-log-safety.mjs';
protectSignalSessionLogs();
const sdk={BufferJSON,initAuthCreds,proto},secret='synthetic-signal-roundtrip-'.repeat(3);
const aliceJid='5567999999901@s.whatsapp.net',bobJid='5567999999902@s.whatsapp.net';

test('real Signal keeps list, text and media protocols decryptable across both peer restarts',async t=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-signal-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  const open=name=>useDurableAuthState(path.join(root,name),{secret,sdk});
  let alice=open('alice'),bob=open('bob');
  const preKey=Curve.generateKeyPair();
  await bob.state.keys.set({'pre-key':{'1':preKey}});
  let sender=DEFAULT_CONNECTION_CONFIG.makeSignalRepository(alice.state);
  let receiver=DEFAULT_CONNECTION_CONFIG.makeSignalRepository(bob.state);
  const c=bob.state.creds;
  await sender.injectE2ESession({jid:bobJid,session:{registrationId:c.registrationId,
    identityKey:generateSignalPubKey(c.signedIdentityKey.public),
    signedPreKey:{keyId:c.signedPreKey.keyId,publicKey:generateSignalPubKey(c.signedPreKey.keyPair.public),signature:c.signedPreKey.signature},
    preKey:{keyId:1,publicKey:generateSignalPubKey(preKey.public)}}});
  const messages=[
    {conversation:'Confirmação sintética de agendamento https://example.com/AGTEST'},
    {listMessage:{title:'Lista sintética',description:'Escolha',buttonText:'Abrir',listType:1,sections:[{title:'Opções',rows:[{title:'Uma',rowId:'fixture:1'}]}]}},
    {audioMessage:{url:'https://example.com/fixture.enc',mimetype:'audio/ogg; codecs=opus',ptt:true,seconds:4,mediaKey:Buffer.alloc(32,7),fileSha256:Buffer.alloc(32,8)}},
    {documentMessage:{url:'https://example.com/doc.enc',mimetype:'application/pdf',fileName:'fixture.pdf',mediaKey:Buffer.alloc(32,9)}},
  ];
  const identity=Buffer.from(alice.state.creds.signedIdentityKey.public);
  for(const [i,content] of messages.entries()){
    const bytes=Buffer.from(proto.Message.encode(proto.Message.fromObject(content)).finish());
    const packet=await sender.encryptMessage({jid:bobJid,data:bytes});
    assert.notDeepEqual(packet.ciphertext,bytes);
    const decrypted=await receiver.decryptMessage({jid:aliceJid,...packet});
    assert.deepEqual(Buffer.from(decrypted),bytes);
    const ack=await receiver.encryptMessage({jid:aliceJid,data:Buffer.from('ack-'+i)});
    assert.equal(Buffer.from(await sender.decryptMessage({jid:bobJid,...ack})).toString(),'ack-'+i);
    // Re-open the exact persisted ratchets and identities, not fresh keys.
    alice=open('alice');bob=open('bob');
    assert.deepEqual(alice.state.creds.signedIdentityKey.public,identity);
    sender=DEFAULT_CONNECTION_CONFIG.makeSignalRepository(alice.state);
    receiver=DEFAULT_CONNECTION_CONFIG.makeSignalRepository(bob.state);
  }
  const damaged=await sender.encryptMessage({jid:bobJid,data:Buffer.from('tamper fixture')});
  damaged.ciphertext[damaged.ciphertext.length-1]^=1;
  await assert.rejects(receiver.decryptMessage({jid:aliceJid,...damaged}));
});
