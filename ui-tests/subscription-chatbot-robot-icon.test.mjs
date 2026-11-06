import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeChatbot,flush} from './subscription-chatbot-harness.mjs';
import {makeChatbotTest} from './subscription-chatbot-test-harness.mjs';
import {makeSuite,planState,tenantA} from './harness.mjs';

const geometry="M12 3v3.5M8 7h8a5 5 0 0 1 5 5v4a5 5 0 0 1-5 5H9l-4 2v-4.1A5 5 0 0 1 3 15V12a5 5 0 0 1 5-5ZM9 13h.01M15 13h.01M9 17c1.7 1.1 4.3 1.1 6 0";
const files=[
  'orion-suite.js','orion-subscription-chatbot.js',
  'orion-subscription-chatbot-test.js'
];

test('os três módulos usam o mesmo SVG de robô arredondado',async()=>{
  for(const file of files){
    const source=await readFile(new URL('../app/static/'+file,import.meta.url),'utf8');
    assert.equal(source.split("bot:'"+geometry+"'").length-1,1,file);
    assert.doesNotMatch(source,/M8 4h8v3H8zM4 7h16v13H4z/);
    assert.doesNotMatch(source,/M12 3v4M3 7h18v14H3z/);
  }
  assert.match(geometry,/a5 5 0 0 1/,'cantos arredondados');
  assert.match(geometry,/M9 17c/,'expressão amigável');
  assert.match(geometry,/l-4 2v/,'silhueta de conversa');
});

test('o botão Testar Chatbot exibe o robô novo e mantém a ação original',async t=>{
  let opened=0;
  const h=await makeChatbot({openTester(){opened++;}});
  t.after(()=>h.close());
  await h.expand();await flush();
  const button=h.element('[data-sc-action="simulate"]');
  const svg=button.querySelector(':scope > svg.os-icon');
  assert.ok(svg);
  assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');
  assert.equal(svg.getAttribute('aria-hidden'),'true');
  assert.equal(svg.querySelector('path')?.getAttribute('d'),geometry);
  button.click();await flush();
  assert.equal(opened,1);
  assert.deepEqual(h.errors,[]);
  assert.equal(h.calls.filter(c=>c.method!=='GET').length,0);
});

test('o simulador usa o robô novo no estado vazio',async t=>{
  const h=await makeChatbotTest();
  t.after(()=>h.close());
  const svg=h.element('.os-chatbot-test-empty svg.os-icon');
  assert.equal(svg.getAttribute('viewBox'),'0 0 24 24');
  assert.equal(svg.querySelector('path')?.getAttribute('d'),geometry);
  assert.deepEqual(h.errors,[]);
});

test('o menu Chatbot é atualizado sem repetir o ícone de Testar Chatbot',async t=>{
  const features=['account.manage','business.setup','devices.manage','chatbot.manage'];
  const entitlements=planState('elite',{
    workspace_profile:'subscription_services',revision:'robot-icon',
    features,permissions:Object.fromEntries(features.map(f=>[f,true]))
  });
  const h=await makeSuite({
    initialRoute:'novidades',permissions:entitlements,
    bridge:{tenants:[{id:tenantA,name:'Empresa sintética',role:'owner',
      status:'active',workspace_profile:'subscription_services'}]}
  });
  t.after(()=>h.close());await flush();
  const nav=h.document.querySelector('#osNav [data-os-route="chatbot"] svg.os-icon');
  const testNav=h.document.querySelector('#osNav [data-os-route="testar-chatbot"] svg.os-icon');
  assert.ok(nav&&testNav);
  assert.equal(nav.querySelector('path').getAttribute('d'),geometry);
  assert.notEqual(testNav.querySelector('path').getAttribute('d'),geometry);
  assert.deepEqual(h.errors,[]);
});
