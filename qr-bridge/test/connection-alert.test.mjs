import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import {
  CONNECTION_CONFIRMATION_TEXT,ownAccountJid,
  noticePath,noticeState,sendConnectionNoticeOnce
} from "../src/connection-alert.mjs";

function temp(){
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),"orion-alert-test-"));
  return {dir,remove:()=>fs.rmSync(dir,{recursive:true,force:true})};
}
function fakeSocket(id="5521997557364:12@s.whatsapp.net"){
  const calls=[];
  return {
    user:{id},calls,
    async sendMessage(jid,body) {
      calls.push({jid,body});
      return {key:{id:"wa-message-1"}};
    }
  };
}

test("mensagem usa texto de confirmação e não inclui dados secretos",()=>{
  assert.match(CONNECTION_CONFIRMATION_TEXT,/🎉 Dispositivo Conectado com Sucesso/);
  assert.match(CONNECTION_CONFIRMATION_TEXT,/Orion/);
  assert.doesNotMatch(CONNECTION_CONFIRMATION_TEXT,/SUPPORT_API_TOKEN|QR_BRIDGE_TOKEN|senha/i);
});

test("número da conta deriva somente do socket autenticado",()=>{
  assert.equal(ownAccountJid("5521997557364:12@s.whatsapp.net"),
    "5521997557364@s.whatsapp.net");
  assert.equal(ownAccountJid("5521997557364@s.whatsapp.net"),
    "5521997557364@s.whatsapp.net");
  for(const id of ["","1234567890@lid","1234@g.us","5521997557364@c.us","null:5@s.whatsapp.net"]) {
    assert.equal(ownAccountJid(id),null);
  }
});

test("notificação é enviada ao próprio número uma vez por sessão",async()=>{
  const t=temp();
  try{
    const sock=fakeSocket();
    const recorded=[];
    const args={authDir:t.dir,client:sock,isCurrent:()=>true,
      onSent:item=>recorded.push(item)};
    assert.equal(noticeState(t.dir),"pending");
    assert.deepEqual(await sendConnectionNoticeOnce(args),{state:"sent"});
    assert.equal(sock.calls.length,1);
    assert.equal(sock.calls[0].jid,"5521997557364@s.whatsapp.net");
    assert.equal(sock.calls[0].body.text,CONNECTION_CONFIRMATION_TEXT);
    assert.equal(recorded.length,1);
    assert.equal(noticeState(t.dir),"sent");
    assert.equal((await sendConnectionNoticeOnce(args)).state,"already_attempted");
    assert.equal(sock.calls.length,1);
  }finally{t.remove()}
});

test("reinício do servidor recupera recibo de conexão e não duplica mensagem",async()=>{
  const t=temp();
  try{
    const first=fakeSocket();
    await sendConnectionNoticeOnce({authDir:t.dir,client:first,isCurrent:()=>true});
    const afterRestart=fakeSocket();
    const r=await sendConnectionNoticeOnce({
      authDir:t.dir,client:afterRestart,isCurrent:()=>true,
    });
    assert.equal(r.state,"already_attempted");
    assert.equal(afterRestart.calls.length,0);
  }finally{t.remove()}
});

test("reconexões concorrentes não mandam notificações repetidas",async()=>{
  const t=temp();
  try{
    const first=fakeSocket();
    const second=fakeSocket();
    const answers=await Promise.all([first,second].map(client=>
      sendConnectionNoticeOnce({authDir:t.dir,client,isCurrent:()=>true})));
    assert.equal(answers.filter(a=>a.state==="sent").length,1);
    assert.equal(first.calls.length+second.calls.length,1);
  }finally{t.remove()}
});

test("novo vínculo autorizado com novo authDir pode receber confirmação",async()=>{
  const t=temp();
  try{
    const a=path.join(t.dir,"auth");
    const b=path.join(t.dir,"auth-renew");
    fs.mkdirSync(a);fs.mkdirSync(b);
    const first=fakeSocket(),second=fakeSocket();
    assert.equal((await sendConnectionNoticeOnce({
      authDir:a,client:first,isCurrent:()=>true})).state,"sent");
    assert.equal((await sendConnectionNoticeOnce({
      authDir:b,client:second,isCurrent:()=>true})).state,"sent");
    assert.equal(first.calls.length+second.calls.length,2);
  }finally{t.remove()}
});

test("conexão incompleta ou socket obsoleto nunca notifica",async()=>{
  const t=temp();
  try{
    const sock=fakeSocket();
    assert.equal((await sendConnectionNoticeOnce({
      authDir:t.dir,client:sock,isCurrent:()=>false})).state,"not_ready");
    assert.equal(sock.calls.length,0);
    assert.equal(noticeState(t.dir),"pending");
    const invalid=fakeSocket("example@lid");
    assert.equal((await sendConnectionNoticeOnce({
      authDir:t.dir,client:invalid,isCurrent:()=>true})).state,"unknown_own_number");
    assert.equal(noticeState(t.dir),"pending");
  }finally{t.remove()}
});

test("falha ambígua no envio não gera spam a cada reconexão",async()=>{
  const t=temp();
  try{
    let count=0;
    const sock=fakeSocket();
    sock.sendMessage=async()=>{count++;throw Error("connection dropped")};
    assert.equal((await sendConnectionNoticeOnce({
      authDir:t.dir,client:sock,isCurrent:()=>true})).state,"delivery_uncertain");
    assert.equal(noticeState(t.dir),"delivery_uncertain");
    assert.equal((await sendConnectionNoticeOnce({
      authDir:t.dir,client:sock,isCurrent:()=>true})).state,"already_attempted");
    assert.equal(count,1);
  }finally{t.remove()}
});

test("recibo no volume não guarda número, conteúdo, sessão ou credenciais",async()=>{
  const t=temp();
  try{
    await sendConnectionNoticeOnce({
      authDir:t.dir,client:fakeSocket(),isCurrent:()=>true,
    });
    const data=fs.readFileSync(noticePath(t.dir),"utf8");
    assert.deepEqual(JSON.parse(data),{schema:1,state:"sent"});
    assert.doesNotMatch(data,/5521997557364|Dispositivo Conectado|auth|jid|token/);
    assert.equal(fs.statSync(noticePath(t.dir)).mode&0o777,0o600);
  }finally{t.remove()}
});

test("confirmação usa remetente autorizado e callback de transmissão nova",async()=>{
 const tmp=temp();
 try{
   const first=fakeSocket(),sent=[];
   const recorder=async (jid,text)=>{
     sent.push({jid,text});
     return {key:{id:"prepared-id"},message:{conversation:text}};
   };
   const state=await sendConnectionNoticeOnce({authDir:tmp.dir,client:first,
     isCurrent:()=>true,sendText:recorder,
     onSent:({message})=>assert.equal(message.conversation,CONNECTION_CONFIRMATION_TEXT)});
   assert.equal(state.state,"sent");
   assert.equal(sent.length,1);
   assert.equal(first.calls.length,0);
 }finally{tmp.remove()}
});
