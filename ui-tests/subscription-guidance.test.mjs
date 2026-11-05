import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,planState,tenantA,tenantB,flush,deferred} from './harness.mjs';

const feature='whatsapp_guidance.view';
const article='[data-os-guidance="warmup"]';
const business=(overrides={})=>({id:tenantA,name:'Empresa sintética',role:'owner',status:'active',workspace_profile:'subscription_services',...overrides});
const access=(overrides={})=>planState('elite',{
  workspace_profile:'subscription_services',revision:'guidance-active',
  features:['account.manage','business.setup','devices.manage',feature],
  permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,[feature]:true},
  ...overrides,
});
async function setup(t,options={}){
  const h=await makeSuite({initialRoute:'aquecimento',permissions:access(),bridge:{tenants:[business()]},...options});
  t.after(()=>h.close());await flush();return h;
}
const onlyPermissionReads=h=>{
  assert.ok(h.calls.length>0);
  assert.ok(h.calls.every(call=>call.method==='GET'&&call.path.endsWith('/management/permissions')),JSON.stringify(h.calls));
  assert.deepEqual(h.errors,[]);
};

test('active subscription owner sees the native knowledge article and four manual steps without operational calls',async t=>{
  const h=await setup(t),page=h.document.querySelector(article);assert.ok(page);
  assert.ok(page.classList.contains('os-help-article'));assert.ok(page.querySelector('.os-help-prose'));
  assert.equal(page.querySelector('h1').textContent,'Aquecimento');
  assert.equal(h.document.title,'Aquecimento · Orion');
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'aquecimento');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'aquecimento');
  assert.ok(h.document.querySelector('#osNav [data-os-route="aquecimento"]'));
  assert.deepEqual([...page.querySelectorAll('[data-os-guidance-step]')].map(node=>node.dataset.osGuidanceStep),['1','2','3','4']);
  for(const section of page.querySelectorAll('section[aria-labelledby]'))assert.ok(page.querySelector('#'+section.getAttribute('aria-labelledby')));
  assert.equal(page.querySelector('form,input,select,textarea,progress,[role="progressbar"],iframe,video,audio,img'),null);
  assert.ok([...page.querySelectorAll('button')].every(button=>button.hasAttribute('data-os-route')));
  onlyPermissionReads(h);
});

test('active subscription company without a commercial plan keeps the guide and device shortcut',async t=>{
  const value=access({code:'none',name:'Sem acesso ativo',source:'none',active:false,status:'no_access',revision:'guidance-no-commercial-plan',limits:{max_professionals:0,active_professionals:0,excess_professionals:0}});
  const h=await setup(t,{permissions:value});
  assert.equal(value.active,false);assert.equal(value.status,'no_access');
  assert.equal(h.bridge.tenants[0].status,'active');assert.equal(value.permissions[feature],true);
  assert.ok(h.document.querySelector(article));
  assert.ok(h.document.querySelector('#osNav [data-os-route="aquecimento"]'));
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'aquecimento');
  const shortcut=h.document.querySelector(article+' [data-os-route="dispositivos"]');
  assert.ok(shortcut);assert.equal(shortcut.disabled,false);assert.equal(shortcut.hidden,false);
  assert.equal(shortcut.textContent,'Meus Dispositivos');onlyPermissionReads(h);
});

test('volume and timing examples are labeled as nonofficial guidance rather than earned account capacity',async t=>{
  const h=await setup(t),page=h.document.querySelector(article),table=page.querySelector('[data-os-warmup-example]');
  assert.match(table.caption.textContent,/Exemplo ilustrativo/);
  assert.deepEqual([...table.querySelectorAll('tbody tr')].map(row=>[...row.cells].map(cell=>cell.textContent)),[['1','10'],['2','20'],['3','50'],['4','80'],['5','100']]);
  assert.deepEqual([...table.querySelectorAll('thead th')].map(node=>node.textContent),['Dia','Contatos']);
  assert.ok([...table.querySelectorAll('thead th')].every(node=>node.scope==='col'));
  assert.ok([...table.querySelectorAll('tbody th')].every(node=>node.scope==='row'));
  assert.match(page.querySelector('#'+table.getAttribute('aria-describedby')).textContent,/não são limites oficiais nem metas obrigatórias/);
  assert.match(page.textContent,/roteiro de uso manual/);
  assert.match(page.textContent,/não garantem entrega/);
  assert.match(page.textContent,/7 ou 14 dias são exemplos/);
  assert.equal(page.querySelectorAll('.os-notice').length,1);
  assert.match(page.querySelector('[data-os-guidance-step="3"]').textContent,/texto, imagem, áudio, documento ou vídeo/);
  assert.match(page.querySelector('[data-os-guidance-step="3"]').textContent,/intervalos para ler as respostas/);
  const sources=[...page.querySelectorAll('a[href]')];
  assert.deepEqual(sources.map(link=>link.href),['https://whatsappbusiness.com/policy/','https://www.whatsapp.com/legal/messaging-guidelines']);
  for(const link of sources){assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');assert.match(link.getAttribute('aria-label'),/nova aba/);}
  assert.match(page.textContent,/pedidos para parar de receber/);
  assert.match(page.textContent,/listas compradas/);
  assert.match(page.textContent,/não comprovam, por si só, uma restrição/);
  onlyPermissionReads(h);
});

test('Meus Dispositivos opens the existing device module without initiating connection or sends',async t=>{
  const mounts=[];
  const h=await setup(t,{configureWindow:window=>{
    window.OrionSubscriptionDevices={mount:bridge=>{mounts.push(bridge);bridge.host.textContent='Lista sintética de dispositivos';},unmount(){},clearContext(){}};
  }});
  const button=h.document.querySelector(article+' [data-os-route="dispositivos"]');
  assert.equal(button.textContent,'Meus Dispositivos');button.click();await flush();
  assert.equal(h.document.querySelector(article),null);assert.equal(mounts.length,1);
  assert.equal(mounts[0].tenantId,tenantA);assert.equal(mounts[0].host.id,'osSubscriptionDevices');
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dispositivos');
  onlyPermissionReads(h);
});

test('the device shortcut follows device permission while the guide remains available',async t=>{
  const value=access();delete value.permissions['devices.manage'];value.features=value.features.filter(item=>item!=='devices.manage');
  const h=await setup(t,{permissions:value});assert.ok(h.document.querySelector(article));
  assert.equal(h.document.querySelector(article+' [data-os-route="dispositivos"]'),null);
  onlyPermissionReads(h);
});

test('the article returns to the existing knowledge base without creating a second help surface',async t=>{
  const h=await setup(t);h.click(article+' [data-os-route="ajuda"]');await flush();
  assert.equal(h.document.querySelector(article),null);assert.ok(h.document.querySelector('#osHelpSearch'));
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'ajuda');onlyPermissionReads(h);
});

for(const scenario of [
  {name:'standard business even with a forged guidance flag',plan:{workspace_profile:'standard'},tenant:{workspace_profile:'standard'},fallback:'plano'},
  {name:'subscription without the feature',plan:{permissions:{'account.manage':true,'business.setup':true,'devices.manage':true}},fallback:'novidades'},
  {name:'paused company even with an active commercial plan and stale true flag',tenant:{status:'paused'},fallback:'novidades'},
  {name:'company without confirmed active status',tenant:{status:undefined},fallback:'novidades'},
  {name:'viewer membership even with a stale owner feature',tenant:{role:'viewer'},fallback:'novidades'},
  {name:'editor membership even with a stale owner feature',tenant:{role:'editor'},fallback:'novidades'},
])test('Aquecimento denies '+scenario.name,async t=>{
  const h=await setup(t,{permissions:access(scenario.plan),bridge:{tenants:[business(scenario.tenant)]}});
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="aquecimento"]'),null);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,scenario.fallback);
  assert.equal(h.calls.some(call=>call.method!=='GET'),false);
  assert.equal(h.calls.some(call=>/subscription-whatsapp|warmup|guidance/.test(call.path)),false);
  assert.deepEqual(h.errors,[]);
});

test('unresolved or failed permissions never flash the guide or its navigation entry',async t=>{
  const pending=deferred();const h=await setup(t,{permissions:()=>pending.promise});
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="aquecimento"]'),null);
  pending.reject(Error('Permissões indisponíveis'));await flush();
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="aquecimento"]'),null);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');onlyPermissionReads(h);
});

test('feature revocation removes an open article and its device shortcut',async t=>{
  let value=access();const h=await setup(t,{permissions:()=>({effective_plan:value})});assert.ok(h.document.querySelector(article));
  value=access({revision:'guidance-revoked',permissions:{'account.manage':true,'business.setup':true}});
  h.window.dispatchEvent(new h.window.Event('orion:plan-changed'));await flush();
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="aquecimento"]'),null);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');onlyPermissionReads(h);
});

test('account change clears the guide and keeps the next paused company closed despite a stale grant',async t=>{
  const pending=deferred();let next=false;
  const h=await setup(t,{permissions:()=>next?pending.promise:{effective_plan:access()}});assert.ok(h.document.querySelector(article));
  next=true;h.window.OrionSuite.sync({user:{role:'member',email:'another@example.invalid'},tenantId:tenantB,tenants:[business({id:tenantB,status:'paused'})]});await flush();
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="aquecimento"]'),null);
  pending.resolve({effective_plan:access({revision:'next-paused-company'})});await flush();
  assert.equal(h.document.querySelector(article),null);
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'novidades');onlyPermissionReads(h);
});
