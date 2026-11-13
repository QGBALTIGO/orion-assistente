import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeChatbot,base,ruleId,detail,listing,catalog,flush,failure} from './subscription-chatbot-harness.mjs';

const action=kind=>'[data-sc-card] [data-sc-action="'+kind+'"]';
const writes=h=>h.calls.filter(call=>call.method!=='GET');

test('card actions use accessible icons only, with the switch isolated in the header',async t=>{
  const h=await makeChatbot();t.after(()=>h.close());
  const card=h.element('[data-sc-card]');
  const toggle=h.element(action('toggle'));
  assert.equal(toggle.getAttribute('role'),'switch');
  assert.equal(toggle.getAttribute('aria-checked'),'true');
  assert.match(toggle.getAttribute('aria-label'),/^Desativar resposta: /);
  assert.ok(toggle.closest('.os-chatbot-card-header'));
  assert.ok(toggle.querySelector('.os-chatbot-switch-track[aria-hidden="true"]'));
  assert.equal(card.querySelector('.os-badge').textContent,'Ativa');

  const group=card.querySelector('.os-chatbot-card-actions');
  assert.equal(group.getAttribute('role'),'group');
  assert.ok(group.closest('.os-chatbot-card-title'),'ações devem ficar junto do conteúdo');
  assert.ok(group.closest('.os-chatbot-card-header'),'ações não precisam de linha vazia separada');
  const buttons=[...group.querySelectorAll('button')];
  assert.deepEqual(buttons.map(x=>x.dataset.scAction),['edit','copy','delete']);
  assert.deepEqual(buttons.map(x=>x.getAttribute('title')),['Editar','Copiar','Excluir']);
  for(const button of buttons){
    assert.equal(button.textContent.trim(),'','não mostrar rótulos visíveis');
    assert.match(button.getAttribute('aria-label'),/^(Editar|Copiar|Excluir) resposta: /);
    const icon=button.querySelector(':scope > svg.os-icon');
    assert.equal(icon?.getAttribute('aria-hidden'),'true');
    assert.equal(icon?.getAttribute('viewBox'),'0 0 24 24');
  }
  assert.ok(group.querySelector('.os-chatbot-action-divider[aria-hidden="true"]'));
  assert.equal(group.querySelectorAll('.os-chatbot-icon-danger').length,1);
  assert.equal(group.querySelector('[data-sc-action="delete"].os-chatbot-icon-danger')!=null,true);
  assert.equal(group.contains(toggle),false);
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});


test('switch changes directly only after versioned PATCH returns success; no confirmation popup',async t=>{
  const h=await makeChatbot({respond(path,method,payload,server){
    if(path===base+'/rules/'+ruleId+'/enabled'&&method==='PATCH'){
      const version=server.data.chatbot_revision+1;
      server.data.chatbot_revision=version;
      server.data.items[0]={...server.data.items[0],enabled:payload.enabled,revision:2};
      server.data.counts={...server.data.counts,active:0,inactive:1};
      const dto=detail();
      dto.rule.enabled=payload.enabled;dto.rule.revision=2;dto.chatbot_revision=version;
      return dto;
    }
  }});
  t.after(()=>h.close());
  const toggle=h.element(action('toggle'));
  toggle.click();
  assert.equal(h.document.querySelector('[data-sc-form="toggle"]'),null);
  assert.equal(h.document.querySelector('dialog'),null);
  assert.equal(toggle.getAttribute('aria-checked'),'true','não antecipar a confirmação do servidor');
  await flush();
  const patch=writes(h)[0];
  assert.equal(writes(h).length,1);
  assert.equal(patch.path,base+'/rules/'+ruleId+'/enabled');
  assert.equal(patch.method,'PATCH');
  assert.equal(patch.data.enabled,false);
  assert.equal(patch.data.expected_revision,1);
  assert.equal(patch.data.expected_chatbot_revision,1);
  assert.match(patch.data.idempotency_key,/^[0-9a-f-]{36}$/i);
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'false');
  assert.match(h.element(action('toggle')).getAttribute('aria-label'),/^Ativar resposta: /);
  assert.equal(h.element('[data-sc-card] .os-badge').textContent,'Inativa');
  assert.deepEqual(h.errors,[]);
});

test('failed switch does not flip visually or open a modal',async t=>{
  const h=await makeChatbot({respond(path,method){
    if(method==='PATCH'&&path.endsWith('/enabled'))throw failure(409,'Conflito de versão.');
  }});
  t.after(()=>h.close());
  h.click(action('toggle'));await flush();
  assert.equal(writes(h).length,1);
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'true');
  assert.equal(h.document.querySelector('[data-sc-form="toggle"]'),null);
  assert.equal(h.document.querySelector('dialog'),null);
  assert.match(h.element('[data-sc-feedback]').textContent,/Conflito de versão|dados mudaram/i);
  assert.deepEqual(h.errors,[]);
});

test('uncertain activation result retains the same idempotency key for an explicit retry',async t=>{
  let attempts=0;
  const h=await makeChatbot({respond(path,method){
    if(method==='PATCH'&&path.endsWith('/enabled')){attempts++;throw failure(503);}
  }});
  t.after(()=>h.close());
  h.click(action('toggle'));await flush();
  assert.equal(writes(h).length,1);
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'true');
  assert.equal(h.document.querySelector('[data-sc-form="toggle"]'),null);
  assert.ok(h.element('[data-sc-pending]'));
  assert.equal(h.element(action('toggle')).disabled,true);
  h.click('[data-sc-action="retry-pending"]');await flush();
  assert.equal(writes(h).length,2);
  assert.deepEqual(writes(h)[0].data,writes(h)[1].data);
  assert.equal(attempts,2);
  assert.deepEqual(h.errors,[]);
});

test('inactive card starts with the switch off; no hidden state changes',async t=>{
  const data=listing();data.items[0].enabled=false;data.counts.active=0;data.counts.inactive=1;
  const h=await makeChatbot({data});t.after(()=>h.close());
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'false');
  assert.equal(h.element('[data-sc-card] .os-badge').textContent,'Inativa');
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('read-only users see switch and icon buttons disabled; no mutations',async t=>{
  const configuration=catalog();configuration.capabilities.manage=false;
  const h=await makeChatbot({configuration});t.after(()=>h.close());
  for(const kind of ['edit','copy','toggle','delete']){
    const element=h.element(action(kind));
    assert.equal(element.disabled,true,kind+' must be disabled');
    element.click();
  }
  assert.equal(h.document.querySelector('[data-sc-form="toggle"]'),null);
  assert.equal(writes(h).length,0);
  assert.deepEqual(h.errors,[]);
});

test('long and escaped keywords remain safe in aria labels and title layout',async t=>{
  const data=listing(),name='<img src=x onerror=alert(1)> '+('Nome longo '.repeat(16));
  data.items[0].keyword=name;
  const h=await makeChatbot({data});t.after(()=>h.close());
  assert.equal(h.element('[data-sc-card] h2').textContent,name);
  assert.equal(h.document.querySelector('[data-sc-card] img'),null);
  assert.equal(h.element(action('edit')).getAttribute('aria-label'),'Editar resposta: '+name);
  assert.equal(h.element(action('toggle')).getAttribute('aria-label'),'Desativar resposta: '+name);
  assert.deepEqual(h.errors,[]);
});

test('scoped mobile styles keep actions compact and preserve touch targets',async()=>{
  const css=await readFile(new URL('../app/static/orion-subscription-chatbot.css',import.meta.url),'utf8');
  assert.match(css,/\.os-chatbot-card-actions\{[^}]*justify-content:flex-start/);
  assert.match(css,/\.os-chatbot-card-actions>\.os-chatbot-icon-action\{[^}]*width:44px;[^}]*height:44px/);
  assert.match(css,/\.os-chatbot-switch\{[^}]*height:44px/);
  assert.match(css,/\.os-chatbot-card-actions>\.os-chatbot-icon-action::before\{[^}]*inset:5px/);
  assert.match(css,/\.os-chatbot-switch\[aria-checked=true\]/);
  assert.match(css,/@media\(max-width:640px\)/);
  assert.doesNotMatch(css,/\.os-chatbot-card-actions>\.os-button\{flex:1 1 auto\}/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
});
