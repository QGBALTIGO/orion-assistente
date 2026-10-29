import test from 'node:test';
import assert from 'node:assert/strict';
import { makeTestFallbackText, makeTestCloudCtaPayload, sendTestCloudCta, selectTestCtaMode } from '../src/test-url-button.mjs';

const text='🧪 *TESTE ORION — AGENDAMENTO FICTÍCIO*\n\nServiço: Corte masculino\n\n🔗 *VER AGENDAMENTO*\nhttps://orionagenda.pro\n\nMensagem automática de teste do Orion.';
const phone='5521997557364';

test('QR fallback includes the clickable URL exactly once', () => {
  const result=makeTestFallbackText(text);
  assert.match(result,/AGENDAMENTO FICTÍCIO/);
  assert.match(result,/https:\/\/orionagenda\.pro/);
  assert.equal((result.match(/https:\/\/orionagenda\.pro/g)||[]).length,1);
  assert.match(result,/VER AGENDAMENTO/);
});

test('Cloud CTA uses Meta schema and no bare URL in the body', () => {
  const p=makeTestCloudCtaPayload(phone,text);
  assert.equal(p.type,'interactive');
  assert.equal(p.interactive.type,'cta_url');
  assert.equal(p.interactive.action.name,'cta_url');
  assert.equal(p.interactive.action.parameters.display_text,'VER AGENDAMENTO');
  assert.equal(p.interactive.action.parameters.url,'https://orionagenda.pro');
  assert.equal(p.to,phone);
  assert.doesNotMatch(p.interactive.body.text,/https:\/\//);
});

test('reject wrong phone, unrelated text and overflow',()=>{
  assert.throws(()=>makeTestCloudCtaPayload('551199999999',text),/invalid_test_recipient/);
  assert.throws(()=>makeTestCloudCtaPayload(phone,'hello'),/invalid_test_reply/);
  assert.throws(()=>makeTestFallbackText('hello'),/invalid_test_reply/);
  assert.throws(()=>makeTestCloudCtaPayload(phone,'x'.repeat(1025)+'🔗 *VER AGENDAMENTO*'),/invalid_test_body/);
});

test('Cloud API acceptance is not presented as delivery',async()=>{
  let count=0;
  const fetchImpl=async (url,options)=>{
    count++;
    assert.equal(url,'https://graph.facebook.com/v25.0/123456789/messages');
    assert.equal(options.method,'POST');
    assert.equal(options.headers.Authorization,'Bearer test-value');
    assert.equal(JSON.parse(options.body).to,phone);
    return {ok:true,status:200,json:async()=>({messages:[{id:'wamid.abc1234567'}]})};
  };
  const result=await sendTestCloudCta({phone,text,accessToken:'test-value',phoneNumberId:'123456789',fetchImpl});
  assert.equal(count,1);
  assert.deepEqual(result,{accepted:true,transport:'meta',delivery:'unconfirmed',id:'wamid.abc1234567'});
});

test('Cloud API failure is not retried or reported as accepted',async()=>{
  let count=0;
  const result=await sendTestCloudCta({phone,text,accessToken:'test-value',phoneNumberId:'123456789',fetchImpl:async()=>{
    count++; return {ok:false,status:403,json:async()=>({error:'sensitive'})};
  }});
  assert.equal(count,1);
  assert.deepEqual(result,{accepted:false,transport:'meta',httpStatus:403});
});

test('No Cloud credentials: block HTTP request',async()=>{
  let count=0;
  await assert.rejects(()=>sendTestCloudCta({phone,text,accessToken:'',phoneNumberId:'123456789',fetchImpl:async()=>{count++;}}),/cloud_cta_not_configured/);
  assert.equal(count,0);
});

test('selector keeps QR fallback unless Meta is explicitly enabled and configured',()=>{
  assert.deepEqual(selectTestCtaMode({}),{useMeta:false,metaNotConfigured:false});
  assert.deepEqual(selectTestCtaMode({QR_TRIGGER_CTA_PROVIDER:'meta'}),{useMeta:false,metaNotConfigured:true});
  assert.deepEqual(selectTestCtaMode({QR_TRIGGER_CTA_PROVIDER:'meta',META_CLOUD_ACCESS_TOKEN:'abc',META_CLOUD_PHONE_NUMBER_ID:'1234567'}),{useMeta:true,metaNotConfigured:false});
  assert.deepEqual(selectTestCtaMode({QR_TRIGGER_CTA_PROVIDER:'qr_link',META_CLOUD_ACCESS_TOKEN:'abc',META_CLOUD_PHONE_NUMBER_ID:'1234567'}),{useMeta:false,metaNotConfigured:false});
});
