import test from "node:test";
import assert from "node:assert/strict";
import {sendStoredText} from "../src/reliable-send.mjs";
import {makeMessageRetryStore} from "../src/message-retry-store.mjs";

const jid="5521997557364@s.whatsapp.net";
test("getMessage responde ANTES de relayMessage alcançar rede",async()=>{
  const store=makeMessageRetryStore(),calls=[];
  const full={key:{id:"3EB0NEW001",remoteJid:jid},message:{conversation:"Olá teste"}};
  await sendStoredText({
    jid,text:"Olá teste",store,
    makeMessage:async()=>{calls.push("generate");return full},
    relay:async(dest,msg,options)=>{
      calls.push("relay");
      assert.deepEqual(store.get({id:full.key.id}),full.message);
      assert.equal(options.messageId,full.key.id);
    },
    onPrepared:()=>calls.push("prepared"),
    onAccepted:()=>calls.push("accepted")
  });
  assert.deepEqual(calls,["generate","prepared","relay","accepted"]);
});
test("envio pode solicitar device-list nova sem perder ID nem texto",async()=>{
  const store=makeMessageRetryStore();
  const full={key:{id:"ABC",remoteJid:jid},message:{conversation:"test"}};
  await sendStoredText({jid,text:"test",store,makeMessage:async()=>full,
    relay:async(dest,msg,opts)=>assert.equal(opts.useUserDevicesCache,false),
    freshDevices:true});
});
test("falha ambígua no transporte não reenvia e conserva conteúdo para retry",async()=>{
  let relayCount=0;
  const store=makeMessageRetryStore();
  const full={key:{id:"A-1",remoteJid:jid},message:{conversation:"hello"}};
  await assert.rejects(sendStoredText({jid,text:"hello",store,
    makeMessage:async()=>full,
    relay:async()=>{relayCount++;throw Error("socket closed")}}),/socket closed/);
  assert.equal(relayCount,1);
  assert.equal(store.get({id:"A-1"}),full.message);
});
test("sem registro de recuperação não envia para não queimar tentativa de retry",async()=>{
  let relays=0;
  await assert.rejects(sendStoredText({jid,text:"hello",
    makeMessage:async()=>({key:{id:"A-1"},message:{conversation:"hello"}}),
    store:{save:()=>false},relay:async()=>{relays++}}),/cache/);
  assert.equal(relays,0);
});
test("destinatários e texto inválidos são rejeitados",async()=>{
  for(const addr of ["", "123@lid","123@g.us","1@s.whatsapp.net","5521997557364@s.whatsapp.net/evil"]){
    await assert.rejects(sendStoredText({jid:addr,text:"hello",
      store:{save:()=>true},makeMessage:async()=>{},relay:async()=>{}}),/recipient/);
  }
  await assert.rejects(sendStoredText({jid,text:"",store:{save:()=>true},
    makeMessage:async()=>{},relay:async()=>{}}),/text/);
});
