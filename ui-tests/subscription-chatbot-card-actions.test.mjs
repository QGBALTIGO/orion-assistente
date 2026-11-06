import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeChatbot,base,ruleId,detail,listing,catalog,copy,flush} from './subscription-chatbot-harness.mjs';

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

test('switch opens existing confirmation; cancel leaves state unchanged, confirm sends versioned PATCH',async t=>{
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
  h.click(action('toggle'));
  assert.ok(h.element('[data-sc-form="toggle"]'));
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'true');
  assert.equal(writes(h).length,0);
  h.click('[data-sc-form="toggle"] [data-sc-action="close-dialog"]');
  assert.equal(h.document.querySelector('[data-sc-form="toggle"]'),null);
  assert.equal(h.element(action('toggle')).getAttribute('aria-checked'),'true');
  h.click(action('toggle'));
  h.submit('[data-sc-form="toggle"]');
  await flush();
  const patch=writes(h)[0];
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
  assert.match(css,/\.os-chatbot-card-actions\{[^}]*justify-content:flex-end/);
  assert.match(css,/\.os-chatbot-card-actions>\.os-chatbot-icon-action\{[^}]*width:44px;[^}]*height:44px/);
  assert.match(css,/\.os-chatbot-switch\{[^}]*height:44px/);
  assert.match(css,/\.os-chatbot-switch\[aria-checked=true\]/);
  assert.match(css,/@media\(max-width:640px\)/);
  assert.doesNotMatch(css,/\.os-chatbot-card-actions>\.os-button\{flex:1 1 auto\}/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
});
