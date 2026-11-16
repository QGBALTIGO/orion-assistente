import test from 'node:test';
import assert from 'node:assert/strict';
import {makeChatbot,detail,contract,copy,flush} from './subscription-chatbot-harness.mjs';

test('configured URL button footer has one blank line in formatted and raw previews',async t=>{
  const rule=detail();
  const original=rule.rule.response.actions[0];
  rule.rule.response.actions=[{
    id:original.id,type:'url_button',delay_seconds:1,
    text:'Mensagem principal',footer:'Rodapé configurado',
    button_text:'Acessar',url:'https://example.com/suporte'
  }];
  const h=await makeChatbot({rule});
  t.after(()=>h.close());
  await h.expand();
  assert.ok(h.element('.os-chatbot-preview').innerHTML.includes('Mensagem principal\n\nRodapé configurado'));
  h.click('[data-sc-action="preview-raw"]');
  assert.ok(h.element('.os-chatbot-raw').textContent.includes('Mensagem principal\n\nRodapé configurado'));
  assert.deepEqual(h.errors,[]);
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
});

test('list preview continues to place a blank line before the configured footer',async t=>{
  const rule=detail();
  rule.rule.response=copy(contract.list_definition);
  rule.rule.response.description='Texto principal da lista';
  rule.rule.response.footer='Assinatura do negócio';
  const h=await makeChatbot({rule});
  t.after(()=>h.close());
  await h.expand();
  const preview=h.element('.os-chatbot-preview');
  assert.ok(preview.innerHTML.includes('\n\nAssinatura do negócio'));
  h.click('[data-sc-action="preview-raw"]');
  assert.ok(h.element('.os-chatbot-raw').textContent.includes('\n\nAssinatura do negócio'));
  assert.equal(h.calls.some(x=>x.method!=='GET'),false);
  assert.deepEqual(h.errors,[]);
});
