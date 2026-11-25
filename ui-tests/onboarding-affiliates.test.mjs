import test from 'node:test';
import assert from 'node:assert/strict';
import {entry,flush,fullState,makeOnboarding,tenantA} from './onboarding-harness.mjs';
import {makePanel} from './panel-harness.mjs';
import {planState} from './harness.mjs';

test('Afiliados cria um espaço com atividade própria e abre o painel correspondente',async t=>{
  const h=await makeOnboarding({
    decision:entry({tenant_id:null,affiliate_available:true}),
    respond(path,method,data,server){
      if(method!=='POST')return;
      assert.equal(path,'/api/v1/onboarding/businesses');
      assert.equal(data.niche,'afiliados');
      assert.equal(data.affiliate_activity,'offers');
      assert.equal(data.activity,'products');
      assert.equal(data.contact_phone,null);
      server.decision=entry({kind:'affiliate',create_allowed:false,
        businesses:[{id:tenantA,name:data.name,role:'owner'}]});
      return fullState({name:data.name,status:'complete',current_step:'review',configuration:data});
    },
  });
  t.after(()=>h.close());
  h.change('[name=niche]','afiliados');
  assert.equal(h.element('[data-ob-title]').textContent,'Configure seu negócio de afiliados');
  assert.equal(h.element('[name=contact_phone]').disabled,true);
  assert.equal(h.element('[name=contact_phone]').required,false);
  assert.deepEqual([...h.element('[name=activity]').options].map(o=>o.value),['','offers','reviews','both']);
  h.change('[name=niche]','barbearia');
  assert.equal(h.element('[name=contact_phone]').required,true);
  assert.equal(h.element('[name=activity]').options[1].value,'services');
  h.fill('[name=name]','Canal de Ofertas');
  h.change('[name=niche]','afiliados');
  h.change('[name=activity]','offers');
  h.submit();await flush();
  assert.equal(h.completed.length,1);
  assert.equal(h.completed[0].tenant_id,tenantA);
  assert.equal(h.document.querySelector('a.ob-button'),null);
  assert.equal(await h.controller.resolveEntry().then(r=>r.kind),'affiliate');
  assert.equal(h.document.querySelector('a.ob-button'),null);
  assert.deepEqual(h.errors,[]);
});

test('Afiliados usa o painel principal, as rotas do nicho e a área nativa da conta',async t=>{
  const business={id:tenantA,name:'Canal de Ofertas',role:'owner',status:'active',
    slug:'canal-de-ofertas',timezone:'America/Cuiaba',workspace_profile:'affiliate'};
  const permissions=planState('none',{workspace_profile:'affiliate',layout_modes:['classic','app']});
  permissions.permissions['devices.manage']=true;
  const h=await makePanel({role:'member',initialRoute:'plano',permissions,
    respond(path){
      if(path==='/api/v1/account/businesses')return [business];
      if(path==='/api/v1/onboarding/entry')return entry({kind:'affiliate',create_allowed:false,businesses:[business]});
      if(path==='/api/v1/account/business-identity')return {name:business.name};
      if(path==='/api/v1/afiliados/'+tenantA+'/api/state')return {company:business.name, jobs:[], niches:[], offer:[],
        connection:[],destination:[],template:[],rule:[],decision:[],product_flag:[],sorts:{2:'Mais vendidos'}};
      if(path.endsWith('/subscription-whatsapp/devices'))return {
        items:[],usage:{used:0,limit:1},can_create:true,capabilities:{manage:true}
      };
    },
  });
  t.after(()=>h.close());
  await flush();
  assert.equal(h.element('#orionSetup').hidden,true);
  assert.equal(h.element('#osAffiliateView').tagName,'SECTION');
  assert.ok(h.element('#osAffiliateView .af-overview'));
  assert.equal(h.document.querySelector('iframe'),null);
  assert.equal(h.element('#sidebarAccountName').textContent,'Conta sintética');
  assert.equal(h.element('#sidebarAccountType').textContent,'Minha conta');
  assert.deepEqual([...h.document.querySelectorAll('#osNav [data-os-route]')].map(node=>node.dataset.osRoute),
    ['dashboard','afiliados-ofertas','afiliados-automacoes','afiliados-fila','afiliados-plataformas','afiliados-conexoes','afiliados-templates','novidades','ajuda','member']);
  assert.ok(h.element('#osNav').textContent.includes('Ajuda e conta'));
  h.click('#osNav [data-os-route=afiliados-ofertas]');
  await flush();
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'afiliados-ofertas');
  assert.equal(h.element('#osNav [data-os-route=afiliados-ofertas]').getAttribute('aria-current'),'page');
  await h.tickClocks(30000);
  assert.ok(h.element('#osAffiliateView'));
  assert.equal(h.calls.some(call=>call.path.includes('/management/dashboard')),false);
  h.click('#osNav [data-os-route=ajuda]');
  await flush();
  assert.equal(new URL(h.window.location.href).searchParams.get('aba'),'ajuda');
  assert.equal(h.document.querySelector('#osAffiliateView'),null);
  assert.match(h.element('#osHelpResults').textContent,/Como conectar a Shopee/);
  assert.doesNotMatch(h.element('#osHelpResults').textContent,/agendamento|atendimento/i);
  h.click('[data-os-help-article=af-whatsapp]');
  await flush();
  assert.match(h.element('#osPageBody').textContent,/Aparelhos conectados/);
  h.click('#osPageBody [data-os-route=afiliados-whatsapp]');
  await flush();
  assert.match(h.element('#osSubscriptionDevices').textContent,/QR Code ou código/);
  assert.equal(h.element('#osSubscriptionDevices').classList.contains('os-devices-connection-only'),true);
  h.click('#osNav [data-os-route=novidades]');
  await flush();
  assert.match(h.element('#osPageBody').textContent,/Automações por categoria/);
  assert.doesNotMatch(h.element('#osPageBody').textContent,/agendamento|atendimento/i);
  h.click('#osNav [data-os-app-settings=appearance]');
  await flush();
  assert.equal(h.element('#osAppDock').hidden,true);
  h.click('[data-os-layout=app]');
  await flush();
  const shortcuts=()=>[...h.element('#osAppDock').querySelectorAll('[data-os-app-tab]')].map(el=>el.dataset.osAppTab);
  assert.deepEqual(shortcuts(),['dashboard','afiliados-ofertas','afiliados-automacoes','afiliados-fila','perfil']);
  const offerSlot=h.element('select[data-os-app-shortcut="1"]');
  assert.equal([...offerSlot.options].some(option=>option.value==='agenda'),false);
  offerSlot.value='afiliados-plataformas';
  offerSlot.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  h.click('[data-os-action=app-shortcuts-save]');
  await flush();
  assert.deepEqual(shortcuts(),['dashboard','afiliados-plataformas','afiliados-automacoes','afiliados-fila','perfil']);
  h.click('#osAppDock [data-os-app-tab=afiliados-plataformas]');
  await flush();
  assert.match(h.element('#osAffiliateView').textContent,/Plataformas/);
  h.click('#osAppDock [data-os-app-tab=perfil]');
  await flush();
  assert.match(h.element('#osPageBody').textContent,/Plataformas.*Conexões e destinos.*Templates/);
  assert.match(h.element('#osPageBody').textContent,/Ajuda e conta.*Novidades.*Base de conhecimento.*Minha conta.*Sair com segurança/);
  h.click('#osPageBody [data-os-app-settings=appearance]');
  await flush();
  h.click('[data-os-layout=classic]');
  await flush();
  assert.equal(h.element('#osAppDock').hidden,true);
  h.click('[data-os-layout=app]');
  await flush();
  assert.equal(shortcuts()[1],'afiliados-plataformas');
  assert.deepEqual(h.errors,[]);
});
