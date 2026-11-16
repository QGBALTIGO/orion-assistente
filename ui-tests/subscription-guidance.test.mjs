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

const avoidArticle='[data-os-guidance="avoid-ban"]';
function avoidAccess(overrides={}){
  const value=access();
  return {...value,code:null,name:'Sem plano ativo',source:'none',active:false,status:'no_access',revision:'avoid-ban-no-commercial-plan',
    features:[...value.features,'diagnostics.manage'],permissions:{...value.permissions,'diagnostics.manage':true},...overrides};
}

test('Evitar Banimento is a native ten-topic guide for an active company without a commercial plan',async t=>{
  const value=avoidAccess(),h=await setup(t,{initialRoute:'evitar-banimento',permissions:value});
  assert.equal(value.active,false);assert.equal(h.bridge.tenants[0].status,'active');
  const page=h.document.querySelector(avoidArticle);assert.ok(page);
  assert.ok(page.classList.contains('os-help-article'));assert.ok(page.querySelector('.os-help-prose'));
  assert.equal(page.querySelector('h1').textContent,'Evitar Banimento');
  assert.equal(h.document.title,'Evitar Banimento · Orion');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'evitar-banimento');
  assert.ok(h.document.querySelector('#osNav [data-os-route="evitar-banimento"]'));
  const sections=[...page.querySelectorAll('[data-os-guidance-tip]')];
  assert.deepEqual(sections.map(node=>node.dataset.osGuidanceTip),Array.from({length:10},(_,index)=>String(index+1)));
  for(const section of sections)assert.equal(section.querySelector('h2').id,section.getAttribute('aria-labelledby'));
  assert.deepEqual([...page.querySelectorAll('[data-os-guidance-tip] [data-os-route]')].map(node=>node.dataset.osRoute),['dispositivos','aquecimento','diagnosticos']);
  assert.equal(page.querySelectorAll('[data-os-guidance-tip="8"] ol li').length,3);
  assert.equal(page.querySelector('form,input,select,textarea,progress,[role="progressbar"],iframe,video,audio,img'),null);
  assert.ok([...page.querySelectorAll('button')].every(button=>button.hasAttribute('data-os-route')));
  const sources=[...page.querySelectorAll('a[href]')];
  assert.deepEqual(sources.map(link=>link.href),['https://whatsappbusiness.com/policy/','https://www.whatsapp.com/legal/messaging-guidelines']);
  for(const link of sources){assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');}
  onlyPermissionReads(h);
});

test('the three Evitar Banimento shortcuts navigate to their existing routes without starting operations',async t=>{
  const mounts=[];
  const module=route=>({mount:bridge=>{mounts.push({route,tenantId:bridge.tenantId});bridge.host.textContent='Consulta sintética';},unmount(){},clearContext(){}});
  const h=await setup(t,{initialRoute:'evitar-banimento',permissions:avoidAccess(),configureWindow:window=>{
    window.OrionSubscriptionDevices=module('dispositivos');window.OrionSubscriptionDiagnostics=module('diagnosticos');
  }});
  for(const route of ['dispositivos','aquecimento','diagnosticos']){
    h.click(avoidArticle+' [data-os-route="'+route+'"]');await flush();
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,route);
    assert.equal(h.document.querySelector(avoidArticle),null);
    if(route==='aquecimento')assert.ok(h.document.querySelector(article));
    h.click('#osNav [data-os-route="evitar-banimento"]');await flush();
    assert.ok(h.document.querySelector(avoidArticle));
  }
  assert.deepEqual(mounts,[{route:'dispositivos',tenantId:tenantA},{route:'diagnosticos',tenantId:tenantA}]);
  onlyPermissionReads(h);
});

test('Evitar Banimento omits device and diagnostics shortcuts when their own permissions are missing',async t=>{
  const value=avoidAccess();
  for(const feature of ['devices.manage','diagnostics.manage']){delete value.permissions[feature];value.features=value.features.filter(item=>item!==feature);}
  const h=await setup(t,{initialRoute:'evitar-banimento',permissions:value});assert.ok(h.document.querySelector(avoidArticle));
  assert.deepEqual([...h.document.querySelectorAll(avoidArticle+' [data-os-guidance-tip] [data-os-route]')].map(node=>node.dataset.osRoute),['aquecimento']);
  onlyPermissionReads(h);
});

const resolveArticle='[data-os-guidance="resolve-ban"]';
const resolveAccess=(overrides={})=>access({code:null,name:'Sem plano ativo',source:'none',active:false,status:'no_access',revision:'resolve-ban-no-commercial-plan',...overrides});

test('Resolver Banimento is a read-only native two-step guide with seven official topics without a commercial plan',async t=>{
  const value=resolveAccess(),h=await setup(t,{initialRoute:'resolver-banimento',permissions:value});
  assert.equal(value.active,false);assert.equal(h.bridge.tenants[0].status,'active');
  const page=h.document.querySelector(resolveArticle);assert.ok(page);
  assert.ok(page.classList.contains('os-help-article'));assert.ok(page.querySelector('.os-help-prose'));
  assert.equal(page.querySelector('h1').textContent,'Resolver Banimento');
  assert.equal(h.document.title,'Resolver Banimento · Orion');
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'resolver-banimento');
  assert.ok(h.document.querySelector('#osNav [data-os-route="resolver-banimento"]'));
  const steps=[...page.querySelectorAll('[data-os-guidance-step]')];
  assert.deepEqual(steps.map(node=>node.dataset.osGuidanceStep),['1','2']);
  for(const section of steps)assert.equal(section.querySelector('h2').id,section.getAttribute('aria-labelledby'));
  assert.equal(page.querySelector('[aria-labelledby="osResolveBanReasons"]').querySelectorAll('li').length,4);
  assert.equal(steps[0].querySelector('[data-os-route]'),null);
  assert.equal(steps[1].querySelector('[data-os-route="dispositivos"]').textContent,'Meus Dispositivos');
  assert.equal(page.querySelector('form,input,select,textarea,progress,[role="progressbar"],iframe,video,audio,img'),null);
  assert.ok([...page.querySelectorAll('button')].every(button=>button.hasAttribute('data-os-route')));
  const sources=[...page.querySelectorAll('[aria-labelledby="osResolveBanOfficial"] a[href]')];
  assert.deepEqual(sources.map(link=>link.href),[
    'https://faq.whatsapp.com/465883178708358',
    'https://faq.whatsapp.com/723378546580115/',
    'https://faq.whatsapp.com/1848531392146538/',
    'https://faq.whatsapp.com/1297640017664693/',
    'https://faq.whatsapp.com/465883178708358',
    'https://faq.whatsapp.com/1217634902127718/',
    'https://whatsappbusiness.com/policy/',
  ]);
  for(const link of sources){assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');}
  onlyPermissionReads(h);
});

test('Resolver Banimento opens the existing devices route and returns without starting a connection or appeal',async t=>{
  const mounts=[];
  const h=await setup(t,{initialRoute:'resolver-banimento',permissions:resolveAccess(),configureWindow:window=>{
    window.OrionSubscriptionDevices={mount:bridge=>{mounts.push({tenantId:bridge.tenantId});bridge.host.textContent='Consulta sintética';},unmount(){},clearContext(){}};
  }});
  h.click(resolveArticle+' [data-os-route="dispositivos"]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'dispositivos');
  assert.equal(h.document.querySelector(resolveArticle),null);
  assert.deepEqual(mounts,[{tenantId:tenantA}]);
  h.click('#osNav [data-os-route="resolver-banimento"]');await flush();
  assert.ok(h.document.querySelector(resolveArticle));onlyPermissionReads(h);
});

test('Resolver Banimento remains readable while omitting the device shortcut without its permission',async t=>{
  const value=resolveAccess();delete value.permissions['devices.manage'];value.features=value.features.filter(item=>item!=='devices.manage');
  const h=await setup(t,{initialRoute:'resolver-banimento',permissions:value});
  assert.ok(h.document.querySelector(resolveArticle));
  assert.equal(h.document.querySelector(resolveArticle+' [data-os-route="dispositivos"]'),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route="dispositivos"]'),null);
  onlyPermissionReads(h);
});

test('the added guide routes share the subscription, active-company and feature boundaries',async t=>{
  for(const [route,selector] of [['evitar-banimento',avoidArticle],['resolver-banimento',resolveArticle]])for(const scenario of [
    {plan:{workspace_profile:'standard'},tenant:{workspace_profile:'standard'},fallback:'plano'},
    {plan:{active:true,status:'active'},tenant:{status:'paused'},fallback:'novidades'},
    {plan:{permissions:{'account.manage':true,'business.setup':true,'devices.manage':true,'diagnostics.manage':true}},fallback:'novidades'},
  ]){
    const h=await setup(t,{initialRoute:route,permissions:avoidAccess(scenario.plan),bridge:{tenants:[business(scenario.tenant)]}});
    assert.equal(h.document.querySelector(selector),null);
    assert.equal(h.document.querySelector('#osNav [data-os-route="'+route+'"]'),null);
    assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,scenario.fallback);
    assert.equal(h.calls.some(call=>call.method!=='GET'||call.path.includes('/subscription-whatsapp/')),false);
    assert.deepEqual(h.errors,[]);
  }
});
