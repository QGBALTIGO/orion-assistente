import test from 'node:test';
import assert from 'node:assert/strict';
import {makeDirect,base,writes,flush,confirm} from './subscription-direct-messages-harness.mjs';
import {makeChatbot,base as chatbotBase,copy,detail} from './subscription-chatbot-harness.mjs';
import {makeChatbotTest,deviceId,response} from './subscription-chatbot-test-harness.mjs';

const dm=name=>'[data-dm-field="'+name+'"]',sc=name=>'[data-sc-field="'+name+'"]';
const original={text:'*Olá* {name}. <img src=x onerror=alert(1)>',footer:'Site externo',button_text:'Acessar meu sistema',url:'https://orionagenda.pro/'};
function fillDirect(h,changes={}){
  h.click('[data-dm-tab="url_button"]');
  h.change(dm('device_id'),h.server.options.devices[0].id);h.fill(dm('phone'),'5567999999901');
  for(const [name,value]of Object.entries({...original,...changes}))h.fill(dm(name),value);
}

test('direct URL button is an explicit draft with no activation, fetch or hidden template',async t=>{
  const h=await makeDirect();t.after(()=>h.close());
  assert.equal(h.controller.getState().kind,'text');fillDirect(h);
  assert.equal(h.element(dm('text')).maxLength,1024);assert.equal(h.element(dm('footer')).maxLength,60);assert.equal(h.element(dm('button_text')).maxLength,20);
  assert.equal(h.document.querySelector('[data-dm-save]'),null);assert.equal(writes(h).length,0);
  h.click('[data-dm-tab="text"]');assert.equal(h.element(dm('text')).value,'');h.click('[data-dm-tab="url_button"]');
  assert.equal(h.element(dm('text')).value,original.text);
  h.click('[data-dm-action="review"]');await flush();
  const payload=writes(h)[0];assert.equal(payload.path,base+'/review');
  assert.deepEqual(payload.data.message,{kind:'url_button',device_id:h.server.options.devices[0].id,recipients:{mode:'phone',phone:'5567999999901',contact_keys:[]},...original});
  const dialog=h.element('[data-dm-dialog]');assert.equal(dialog.dataset.dmView,'review');
  const preview=dialog.querySelector('[data-dm-cta-preview]');assert.ok(preview);assert.match(preview.textContent,/Acessar meu sistema.*https:\/\/orionagenda.pro\//s);
  assert.equal(dialog.querySelector('a,iframe,img,script'),null);assert.match(dialog.textContent,/<img src=x/);
  assert.deepEqual(h.server.savedTemplates,[]);assert.equal(h.server.confirmations.length,0);assert.deepEqual(h.external,[]);
  await confirm(h);assert.equal(h.server.confirmations.length,1);assert.deepEqual(h.server.savedTemplates,[]);
  assert.equal(writes(h)[1].path,base+'/confirm');assert.equal(writes(h)[1].data.idempotency_key,payload.data.idempotency_key);
  assert.equal(writes(h)[1].data.confirmation,'ENVIAR');assert.deepEqual(h.errors,[]);
});

for(const [name,value]of [['url','http://example.com/'],['url','https://127.1/'],['url','https://2130706433/'],['url','https://example.com/{name}'],['url','https://example.com/%7bname%7d'],['url','https://user:pass@example.com/'],['url','https://example.com/#x'],['url','https://[::1]/'],['url','https://192.0.0.9/'],['text','😀'.repeat(513)],['footer','😀'.repeat(31)],['button_text','😀'.repeat(11)],['button_text','linha\tquebrada']])test('invalid direct CTA '+name+' '+JSON.stringify(value).slice(0,65)+' never creates review',async t=>{
  const h=await makeDirect();t.after(()=>h.close());fillDirect(h,{[name]:value});h.click('[data-dm-action="review"]');await flush();
  assert.equal(writes(h).length,0);assert.equal(h.server.confirmations.length,0);assert.deepEqual(h.external,[]);assert.ok(h.document.querySelector('[role="alert"]'));
});

test('Chatbot editor saves a reusable disabled URL button with only the typed fields',async t=>{
  const h=await makeChatbot({respond(path,method,payload,server){if(path===chatbotBase+'/rules'&&method==='POST'){const next=detail();next.rule={...next.rule,...copy(payload.rule)};return next;}}});t.after(()=>h.close());
  h.click('[data-sc-action="create"]');h.fill(sc('keyword'),'botão sintético');h.check(sc('enabled'),false);
  h.change(sc('response.actions.0.type'),'url_button');
  for(const [name,value]of Object.entries({...original,text:'Olá {name}, acesse {my_name}.'}))h.fill(sc('response.actions.0.'+name),value);
  assert.equal(h.document.querySelector('[data-sc-action="read-url-tags"]'),null);assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
  h.submit('[data-sc-form="editor"]');await flush();const saved=h.calls.find(c=>c.method==='POST'&&c.path===chatbotBase+'/rules');
  assert.ok(saved);assert.equal(saved.data.rule.enabled,false);const action=saved.data.rule.response.actions[0];
  assert.equal(action.type,'url_button');assert.equal(action.url,original.url);assert.equal(action.text,'Olá {name}, acesse {my_name}.');
  assert.deepEqual(Object.keys(action).sort(),['id','type','delay_seconds','text','footer','button_text','url'].sort());
  assert.deepEqual(h.downloadCalls,[]);assert.deepEqual(h.errors,[]);
});

test('simulator displays a declared URL button as escaped content without opening the site or selecting a business action',async t=>{
  const h=await makeChatbotTest({respond(path,method,payload){if(method==='POST')return response(payload,{messages:[{id:'00000000-0000-4000-8000-000000000401',delay_seconds:1,type:'url_button',...original,notice:'O link não é acessado na simulação.'}]});}});t.after(()=>h.close());
  h.select(deviceId);await h.send('testar');
  assert.match(h.document.body.textContent,/Acessar meu sistema/);assert.match(h.document.body.textContent,/https:\/\/orionagenda.pro\//);
  assert.equal(h.document.querySelector('[data-sct-message] a, [data-sct-message] iframe, [data-sct-message] img'),null);
  assert.deepEqual(h.binaryCalls,[]);assert.equal(h.calls.filter(c=>c.method==='POST').length,1);assert.deepEqual(h.errors,[]);
});
