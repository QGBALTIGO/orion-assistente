import test from "node:test";
import assert from "node:assert/strict";
import {makeMessageRetryStore,deliveryFromAck,advanceDelivery}
  from "../src/message-retry-store.mjs";

test("getMessage devolve mensagem original para retentativa criptografada",()=>{
  const store=makeMessageRetryStore();
  const msg={conversation:"Mensagem de teste"};
  assert.equal(store.save({id:"ID-1",remoteJid:"5511999999999@s.whatsapp.net"},msg),true);
  assert.equal(store.get({id:"ID-1",remoteJid:"outro@lid"}),msg);
  assert.equal(store.get({id:"ID-inexistente"}),undefined);
});
test("cache rejeita mensagens sem ID ou corpo",()=>{
  const store=makeMessageRetryStore();
  assert.equal(store.save({id:""},{}),false);
  assert.equal(store.save({id:"ok"},null),false);
  assert.equal(store.size(),0);
});
test("cache expira após TTL e evita recuperação de chave antiga",()=>{
  let now=1000;const store=makeMessageRetryStore({ttlMs:500,clock:()=>now});
  store.save({id:"a"},{conversation:"a"});now=1499;
  assert.deepEqual(store.get({id:"a"}),{conversation:"a"});
  now=1500;assert.equal(store.get({id:"a"}),undefined);
});
test("cache mantém limite de mensagens e descarta as mais antigas",()=>{
  const store=makeMessageRetryStore({max:2});
  for(const id of ["a","b","c"])store.save({id},{conversation:id});
  assert.equal(store.size(),2);assert.equal(store.get({id:"a"}),undefined);
  assert.deepEqual(store.get({id:"b"}),{conversation:"b"});
});
test("clear elimina conteúdo da sessão encerrada",()=>{
  const store=makeMessageRetryStore();store.save({id:"a"},{conversation:"x"});
  store.clear();assert.equal(store.size(),0);
});
test("sendMessage não equivale a confirmação de leitura ou entrega",()=>{
  assert.equal(deliveryFromAck(undefined),"pending");
  assert.equal(deliveryFromAck(1),"pending");
  assert.equal(deliveryFromAck(2),"server_accepted");
  assert.equal(deliveryFromAck(3),"delivered");
  assert.equal(deliveryFromAck(4),"read");
  assert.equal(deliveryFromAck(0),"failed");
});
test("status não regride quando chega recibo fora de ordem",()=>{
  assert.equal(advanceDelivery("delivered","pending"),"delivered");
  assert.equal(advanceDelivery("server_accepted","delivered"),"delivered");
  assert.equal(advanceDelivery("read","delivered"),"read");
  assert.equal(advanceDelivery("pending","failed"),"failed");
});
