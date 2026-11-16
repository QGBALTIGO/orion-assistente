import test from 'node:test';
import assert from 'node:assert/strict';
import {isBookingProtocolMessage,fetchBookingProtocolConfirmation,resolveBookingSenderPhone}
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

test('strict LID->PN resolution waits only for provider-authenticated mapping',async()=>{
  const lid='998877665544332211@lid',key={remoteJid:lid};
  let count=0;
  const waits=[];
  const valid=await resolveBookingSenderPhone(key,{},{
    resolver:async()=>{count++;return count<3?null:'5567998888777';},
    sleep:async ms=>{waits.push(ms);}
  });
  assert.equal(valid,'5567998888777');
  assert.deepEqual(waits,[150,350]);
  assert.equal(count,3);
  const unresolved=await resolveBookingSenderPhone(key,{},{
    resolver:async()=>null,sleep:async()=>{}
  });
  assert.equal(unresolved,null);
  let reads=0;
  const group=await resolveBookingSenderPhone({remoteJid:'123456789@g.us'}, {},{
    resolver:async()=>{reads++;return null;},sleep:async()=>{throw Error('must not wait');}
  });
  assert.equal(group,null);
  assert.equal(reads,1);
});

test('temporary lookup outage retries only the idempotent private API request',async()=>{
  let count=0;const states=[];
  const result=await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'same-id',text,
    sleep:async()=>{},onStatus:status=>states.push(status),
    fetchImpl:async(_url,options)=>{
      count++;
      assert.equal(JSON.parse(options.body).message_id,'same-id');
      if(count===1)return {ok:false,status:503};
      return {ok:true,json:async()=>({state:'confirmed',answer:'Confirmado'})};
    }
  });
  assert.equal(result,'Confirmado');
  assert.equal(count,2);
  assert.deepEqual(states,['retrying','confirmed']);
});

test('unavailable database retries once without exposing a booking or sending to a phone',async()=>{
  let count=0;
  const result=await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'same-id',text,
    sleep:async()=>{},
    fetchImpl:async()=>{count++;return {ok:true,json:async()=>({
      state:count===1?'unavailable':'unrecognized',
      ...(count===1?{answer:null}:{answer:'Não foi possível validar o protocolo.'})
    })};}
  });
  assert.equal(result,'Não foi possível validar o protocolo.');
  assert.equal(count,2);
});

test('invalid or unauthorized private response is never a customer confirmation',async()=>{
  for(const response of [
    {ok:false,status:401},
    {ok:true,json:async()=>({state:'confirmed',answer:null})},
    {ok:true,json:async()=>({state:'unexpected',answer:'not trusted'})}
  ]){
    await assert.rejects(fetchBookingProtocolConfirmation({
      apiUrl:base,token,phone:'5567998888777',messageId:'id',text,
      fetchImpl:async()=>response,sleep:async()=>{}
    }));
  }
});


test('protocolo do cliente paraguaio é recebido com PN verificado e sem liberar IA',async()=>{
  let seen;
  const answer=await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'595992000001',messageId:'synthetic-py-1',
    text:'Olá Orion! Protocolo: AG-3SVM6N',
    fetchImpl:async(_url,opts)=>{
      seen=JSON.parse(opts.body);
      return {ok:true,json:async()=>({state:'confirmed',answer:'Atendimento confirmado com segurança'})};
    }
  });
  assert.equal(answer,'Atendimento confirmado com segurança');
  assert.deepEqual(seen,{
    phone:'595992000001',message_id:'synthetic-py-1',
    text:'Olá Orion! Protocolo: AG-3SVM6N'
  });
  await assert.rejects(fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'595899000001',messageId:'synthetic-py-2',
    text:'AG-3SVM6N',fetchImpl:async()=>{throw Error('Não deve consultar API');}
  }));
});

test('encaminha OTP do link curto com alias de colisão sem revelar token do atendimento',async()=>{
  const message='ORION VERIFICAR P AG-4NNSY2-E1721F7D ' + 'K'.repeat(22);
  assert.equal(isBookingProtocolMessage(message),true);
  let sent=0;
  const answer=await fetchBookingProtocolConfirmation({
    apiUrl:base,token,phone:'5567998888777',messageId:'short-qa-1',text:message,
    fetchImpl:async(_url,opts)=>{
      const data=JSON.parse(opts.body);
      assert.equal(data.phone,'5567998888777');
      assert.equal(data.text,message);
      assert.ok(!opts.body.includes('82552b25-d0c8-5338-8c42-49f11de59e45'));
      sent++;
      return {ok:true,json:async()=>({
        state:'confirmed',answer:'Código de acesso Orion: 123456'
      })};
    }
  });
  assert.equal(answer,'Código de acesso Orion: 123456');
  assert.equal(sent,1);
});
