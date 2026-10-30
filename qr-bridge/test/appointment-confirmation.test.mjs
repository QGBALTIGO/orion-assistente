import test from 'node:test';
import assert from 'node:assert/strict';
import {isBookingProtocolMessage,fetchBookingProtocolConfirmation}
  from '../src/appointment-confirmation.mjs';

const text='*Olá, Orion!* 👋\n\nAcabei de realizar um agendamento pelo site.\n\n🧾 Protocolo: *AG-3SVM6N*\n\n_Aguardo os detalhes da minha confirmação._';
const token='synthetic-private-bridge-key-'+'x'.repeat(38);
const base='http://orion-assistente.railway.internal/internal/qr/ai/reply';

test('reconhece protocolo de qualquer cliente, sem IA ou allowlist',()=>{
  assert.equal(isBookingProtocolMessage(text),true);
  assert.equal(isBookingProtocolMessage('Protocolo AG-ARYGUH'),true);
  for(const value of ['', 'oi', 'AG-0ZZZZZ', 'AG-123', 'Meu AG-3SVM6N foi cancelado',
    'Cancelar AG-3SVM6N','AG-3SVM6N e AG-ARYGUH','AG-3SVM6Nmore']){
    assert.equal(isBookingProtocolMessage(value),false,value);
  }
});

test('encaminha protocolo pelo canal privado, sem tenant vindo da mensagem',async()=>{
  const calls=[];
  const result=await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'WAMSG-1',text,
    fetchImpl:async(url,options)=>{
      calls.push({url,options});
      return {ok:true,json:async()=>({state:'confirmed',answer:'Olá! Confirmação AG-3SVM6N'})};
    }
  });
  assert.equal(result,'Olá! Confirmação AG-3SVM6N');
  assert.equal(calls.length,1);
  assert.equal(calls[0].url,'http://orion-assistente.railway.internal/internal/qr/appointment-confirmation');
  assert.equal(calls[0].options.method,'POST');
  assert.equal(calls[0].options.headers['X-Orion-Bridge-Key'],token);
  assert.deepEqual(JSON.parse(calls[0].options.body),{
    phone:'5567998888777',message_id:'WAMSG-1',text});
});

test('não libera qualquer resposta de IA nem destinos falsos',async()=>{
  for(const url of ['https://example.com/internal/qr/ai/reply',
    'http://attacker.railway.internal.evil.example/internal/qr/ai/reply',
    'http://orion-assistente.railway.internal/private',
    'http://test:password@orion-assistente.railway.internal/']){
    await assert.rejects(fetchBookingProtocolConfirmation({
      apiUrl:url,token,phone:'5567998888777',messageId:'id',text,
      fetchImpl:async()=>{throw Error('unexpected request');}
    }));
  }
  assert.equal(await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'id',text,
    fetchImpl:async()=>({ok:true,json:async()=>({state:'disabled',answer:null})})
  }),null);
  assert.equal(await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'id',text,
    isCurrent:()=>false,fetchImpl:async()=>{throw Error('must not call');}
  }),null);
});

test('número novo é aceito, mas resposta precisa ser do backend',async()=>{
  const answer=await fetchBookingProtocolConfirmation({
    apiUrl:'http://orion-assistente.railway.internal/',token,phone:'556781030648',
    messageId:'new-customer-id',text,
    fetchImpl:async()=>({ok:true,json:async()=>({
      state:'unrecognized',answer:'Não foi possível validar o protocolo para este número.'
    })})
  });
  assert.match(answer,/Não foi possível validar/);
  for(const phone of ['123','55555555555','556799888877799','whatsapp@g.us']){
    await assert.rejects(fetchBookingProtocolConfirmation({
      apiUrl:base,token,phone,messageId:'id',text,
      fetchImpl:async()=>{throw Error('should not send');}
    }));
  }
});
