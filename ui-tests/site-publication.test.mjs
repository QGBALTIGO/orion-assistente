import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, deferred, defaultResponse } from './harness.mjs';

const prefix='/api/v1/tenants/10000000-0000-4000-8000-000000000001/management/site';
const preview='/app/site-preview/10000000-0000-4000-8000-000000000001';
const site=extra=>({title:'Empresa sintética',description:'Apresentação do estabelecimento',slug:'empresa-sintetica',theme_color:'#2e7bf6',show_prices:true,version:4,published:false,preview_url:preview,public_url:null,...extra});
const publication=extra=>({state:'ready',can_edit:true,can_publish:true,ready:true,issues:[],draft_version:4,public_version:null,published_at:null,public_url:null,history:[],...extra});
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function setup(t,{data=site(),status=publication(),role='owner',userRole='member',bridge={},respond}={}){
  const server={data,status};
  const h=await makeSuite({initialRoute:'site',role,bridge:{user:{role:userRole,name:'Conta sintética'},...bridge},respond:async(path,method='GET',payload)=>{
    if(respond){const result=await respond(path,method,payload,server);if(result!==undefined)return result;}
    if(path===prefix+'/publication'&&method==='GET')return server.status;
    if(path===prefix&&method==='GET')return server.data;
    if(path===prefix+'/metrics?days=30')return {daily:[],visits:0};
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();return {h,server};
}
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const toast=h=>h.document.getElementById('osToastRegion').textContent;

test('a legitimate admin owner publishes directly, waits for backend and suppresses double submission',async t=>{
  const pending=deferred();
  const {h,server}=await setup(t,{userRole:'admin',respond:(path,method,payload)=>path===prefix+'/publication'&&method==='POST'?pending.promise:undefined});
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Pronto para publicar');
  h.click('[data-os-action=publish-site]');
  assert.equal(writes(h).length,0,'only the owner confirmation starts publication');
  assert.match(h.document.getElementById('osDialogTitle').textContent,/Publicar site/);
  h.submit('[data-os-form=site-publication]');h.submit('[data-os-form=site-publication]');await flush();
  assert.equal(writes(h).length,1);
  assert.deepEqual({...writes(h)[0].data},{expected_version:4,enabled:true});
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Publicando');
  assert.equal(h.document.querySelector('[data-os-form=site-publication] button[type=submit]').disabled,true);
  assert.equal(toast(h),'','success is never announced while the backend is pending');
  server.data=site({published:true,version:5,public_url:'/s/empresa-sintetica'});
  server.status=publication({state:'published',draft_version:5,public_version:5,published_at:'2030-01-07T15:00:00Z',public_url:'/s/empresa-sintetica'});
  pending.resolve({published:true,version:5});await flush();
  assert.match(toast(h),/Site publicado/);
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Publicado');
  assert.equal(h.document.querySelector('.os-site-address a').href,'https://orion.test/s/empresa-sintetica');
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,true);
  assert.ok(h.calls.every(call=>!call.path.includes('/admin/')),'no Orion administrator approval endpoint is involved');
  assert.equal(h.errors.length,0);
});

test('publication uses the existing address card and keeps technical details collapsed',async t=>{
  const {h}=await setup(t,{status:publication({public_version:2,published_at:'2030-01-07T15:00:00Z',history:[{version:2,action:'publish',published_at:'2030-01-07T15:00:00Z'}]})});
  const address=h.document.getElementById('osSitePublication');
  assert.equal(address.className,'os-card os-site-address');
  assert.ok(address.querySelector('.os-card-header'));
  assert.ok(address.querySelector('.os-actions>[data-os-action=publish-site]'));
  assert.equal(h.document.querySelector('.os-site-publication'),null,'there is no separate publication card');
  const options=address.querySelector('#osSitePublicationOptions');
  assert.equal(options.className,'os-more-fields');assert.equal(options.open,false);
  assert.equal(options.querySelector('summary').textContent,'Mais opções');
  assert.ok(options.querySelector('#osSitePublicationMeta'));
  assert.ok(options.querySelector('#osSitePublicationHistory'));
  assert.equal(address.querySelector('h3'),null,'the address is not followed by a second oversized title');
  h.click('[data-os-site-tab=settings]');
  assert.equal(h.document.querySelectorAll('#osSitePublication').length,1);
  assert.equal(h.document.querySelectorAll('#osSitePublicationState').length,1);
  assert.equal(h.document.querySelector('#osSiteForm .os-site-address'),null,'settings reuse their existing address section without nesting another card');
  assert.ok(h.document.getElementById('osSitePublication').closest('.os-site-config-card'));
  assert.equal(h.document.querySelector('#osSitePublicationOptions').open,false);
  assert.equal(writes(h).length,0);
});

test('legacy admin can confirm identity on this device without logout or a publication write',async t=>{
  let confirmations=0;
  const {h}=await setup(t,{userRole:'admin',status:publication({can_publish:false,reauthentication_required:true,access_reason:'administrator_reauthentication_required'}),bridge:{reauthenticate:async()=>{confirmations++;}}});
  assert.equal(h.document.querySelector('[data-os-action=publish-site]'),null);
  const control=h.document.querySelector('[data-os-action=site-reauthenticate]');
  assert.equal(control.textContent,'Atualizar acesso de admin');
  assert.match(h.document.getElementById('osSitePublication').textContent,/Confirme seu login de administrador para publicar neste dispositivo/);
  assert.doesNotMatch(h.document.getElementById('osSitePublication').textContent,/publicação é feita pelo proprietário/);
  h.click('[data-os-action=site-reauthenticate]');await flush();
  assert.equal(confirmations,1);assert.equal(writes(h).length,0);
  assert.equal(h.calls.some(call=>call.path.includes('logout')),false);
});

test('updating admin access preserves unsaved settings and never invokes the login bridge',async t=>{
  let confirmations=0;
  const {h}=await setup(t,{userRole:'admin',status:publication({can_publish:false,reauthentication_required:true,access_reason:'administrator_reauthentication_required'}),bridge:{reauthenticate:async()=>{confirmations++;}}});
  h.click('[data-os-site-tab=settings]');
  const form=h.document.getElementById('osSiteForm');
  h.fill('#osSiteForm [name=title]','Rascunho ainda não salvo');
  h.click('[data-os-action=site-reauthenticate]');await flush();
  assert.equal(confirmations,0);
  assert.equal(h.document.getElementById('osSiteForm'),form);
  assert.equal(form.querySelector('[name=title]').value,'Rascunho ainda não salvo');
  assert.equal(form.dataset.dirty,'true');
  assert.match(toast(h),/Salve as configurações antes de atualizar seu acesso/);
  assert.equal(writes(h).length,0);
});

test('identified admin owners and ordinary editors are never prompted to reauthenticate',async t=>{
  for(const [role,userRole,canPublish] of [['owner','admin',true],['editor','member',false],['viewer','member',false]]){
    const {h}=await setup(t,{role,userRole,status:publication({can_publish:canPublish,reauthentication_required:false})});
    assert.equal(h.document.querySelector('[data-os-action=site-reauthenticate]'),null);
    assert.equal(!!h.document.querySelector('[data-os-action=publish-site]'),canPublish);
    assert.equal(writes(h).length,0);
  }
});

test('an unavailable administrator identity offers a read refresh instead of a login loop',async t=>{
  let confirmations=0;
  const {h}=await setup(t,{userRole:'admin',status:publication({can_publish:false,reauthentication_required:true,access_reason:'administrator_identity_unavailable',access_message:'A identidade deste acesso administrativo não está disponível.'}),bridge:{reauthenticate:async()=>{confirmations++;}}});
  assert.equal(h.document.querySelector('[data-os-action=site-reauthenticate]'),null);
  assert.match(h.document.getElementById('osSitePublication').textContent,/Não foi possível confirmar seu acesso de administrador/);
  assert.ok(h.document.querySelector('[data-os-action=site-refresh]'));
  h.click('[data-os-action=site-refresh]');await flush();
  assert.equal(confirmations,0);assert.equal(writes(h).length,0);
});

test('server capabilities govern editing and publishing for owners, editors, readers and support',async t=>{
  for(const [role,userRole,canEdit,canPublish] of [['owner','member',true,true],['owner','admin',true,true],['editor','member',true,false],['editor','member',true,true],['viewer','member',false,false],['owner','support',false,false]]){
    await t.test(`${role}/${userRole}/${canEdit}/${canPublish}`,async t=>{
      const {h}=await setup(t,{role,userRole,status:publication({can_edit:canEdit,can_publish:canPublish})});
      assert.equal(!!h.document.querySelector('[data-os-action=publish-site]'),canPublish);
      h.click('[data-os-site-tab=settings]');
      assert.equal(h.document.querySelector('#osSiteForm [name=title]').matches(':disabled'),!canEdit);
      if(!canPublish){const forged=h.document.createElement('button');forged.dataset.osAction='publish-site';h.document.body.append(forged);forged.click();assert.equal(h.document.querySelector('[data-os-form=site-publication]'),null);}
      if(!canEdit){h.submit('#osSiteForm');await flush();}
      assert.equal(writes(h).length,0);
    });
  }
});

test('metrics outage does not block editing or publication and never fabricates zero indicators',async t=>{
  const {h}=await setup(t,{respond:path=>{if(path.includes('/metrics?'))throw Error('Métricas indisponíveis');}});
  assert.ok(h.document.querySelector('[data-os-action=publish-site]'));
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,false);
  assert.match(h.document.querySelector('.os-site-metrics-error').textContent,/indicadores.*indisponíveis/);
  assert.equal(h.document.querySelector('.os-site-metrics'),null);
  h.click('[data-os-site-tab=settings]');assert.ok(h.document.getElementById('osSiteForm'));
  assert.equal(h.document.querySelector('#osSiteForm [name=title]').disabled,false);
  assert.equal(writes(h).length,0);
});

test('missing or unavailable publication status disables publication without hiding the saved draft',async t=>{
  for(const unavailable of [true,false])await t.test(String(unavailable),async t=>{
    const {h}=await setup(t,{respond:(path,method)=>{if(path===prefix+'/publication'&&method==='GET'){if(unavailable)throw Error('Publicação temporariamente indisponível');return [];}}});
    assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Publicação indisponível');
    assert.equal(h.document.querySelector('[data-os-action=publish-site]'),null);
    assert.ok(h.document.querySelector('[data-os-action=site-refresh]'));
    h.click('[data-os-site-tab=settings]');assert.equal(h.document.querySelector('#osSiteForm [name=title]').value,'Empresa sintética');
    assert.equal(writes(h).length,0);
  });
});

test('blocking requirements render as text with routes to the company settings',async t=>{
  const {h}=await setup(t,{status:publication({state:'blocked',ready:false,issues:[{code:'service_missing',message:'Cadastre um serviço',section:'services'},{code:'professional_missing',message:'Cadastre um profissional',section:'professionals'},{code:'hours_missing',message:'Defina horários',section:'hours'},{code:'x',message:'<img src=x onerror=alert(1)>',section:'site'}]})});
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,true);
  assert.equal(h.document.querySelectorAll('#osSitePublicationIssues [role=listitem]').length,4);
  assert.equal(h.document.querySelector('#osSitePublicationIssues img'),null);
  assert.deepEqual([...h.document.querySelectorAll('[data-os-site-fix]')].map(x=>x.dataset.osSiteFix),['services','professionals','hours','site']);
  h.click('[data-os-site-fix=site]');assert.ok(h.document.getElementById('osSiteForm'));
  h.click('[data-os-site-fix=services]');await flush();
  assert.ok(h.calls.some(call=>call.path.endsWith('/services')));
  assert.equal(writes(h).length,0);
});

test('saved draft and published version dates/history remain distinct and escaped',async t=>{
  const {h}=await setup(t,{data:site({version:9,published:true,has_unpublished_changes:true,public_url:'/s/antigo'}),status:publication({state:'pending_changes',draft_version:9,public_version:5,published_at:'2030-01-07T15:00:00Z',public_url:'/s/antigo',history:[{version:5,published_at:'2030-01-07T15:00:00Z',action:'published'},{version:6,published_at:'bad-date',action:'<script>bad</script>'}]})});
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Alterações pendentes');
  assert.match(h.document.querySelector('#osSitePublicationOptions').textContent,/RascunhoVersão 9/);
  assert.match(h.document.querySelector('#osSitePublicationOptions').textContent,/Versão 5/);
  assert.equal(h.document.querySelector('#osSitePublicationMeta time').dateTime,'2030-01-07T15:00:00Z');
  assert.equal(h.document.querySelectorAll('#osSitePublicationHistory [role=listitem]').length,2);
  assert.equal(h.document.querySelector('#osSitePublicationHistory script'),null);
  assert.match(h.document.querySelector('#osSitePublicationHistory').textContent,/Data não informada/);
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,false);
  let copied='';Object.defineProperty(h.window.navigator,'clipboard',{configurable:true,value:{writeText:async value=>{copied=value;}}});
  h.click('[data-os-action=copy-site]');await flush();assert.equal(copied,'https://orion.test/s/antigo');
});

test('unpublishing preserves the draft and reactivation explicitly restores the previous snapshot',async t=>{
  const {h,server}=await setup(t,{data:site({published:true,public_url:'/s/empresa-sintetica'}),status:publication({state:'published',public_version:3,public_url:'/s/empresa-sintetica'}),respond:(path,method,payload,server)=>{
    if(path!==prefix+'/publication'||method!=='POST')return;
    const enabled=payload.enabled;server.data=site({version:enabled?6:5});server.status=publication({state:enabled?'published':'unpublished',ready:false,draft_version:enabled?6:5,public_version:3,public_url:enabled?'/s/empresa-sintetica':null,issues:[{code:'draft_invalid',message:'Revise o rascunho',section:'site'}]});return {published:enabled,version:enabled?6:5};
  }});
  h.click('[data-os-action=unpublish-site]');h.submit('[data-os-form=site-publication]');await flush();
  assert.deepEqual({...writes(h)[0].data},{expected_version:4,enabled:false});
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Retirado do ar');
  assert.equal(h.document.querySelector('.os-site-address a[href*="/s/"]'),null);
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,true,'an invalid draft cannot be published');
  assert.equal(h.document.querySelector('[data-os-action=restore-site]').disabled,false,'restoring the previous snapshot does not publish the invalid draft');
  h.click('[data-os-action=restore-site]');h.submit('[data-os-form=site-publication]');await flush();
  assert.deepEqual({...writes(h)[1].data},{expected_version:5,enabled:true,restore_previous:true});
  assert.match(toast(h),/Última publicação reativada/);
  assert.equal(server.data.title,'Empresa sintética');
  assert.ok(writes(h).every(call=>call.path===prefix+'/publication'),'no appointments or other resources are changed');
});

test('a publication error retains the draft, displays the reason and supports an authoritative refresh',async t=>{
  let version=4;
  const {h,server}=await setup(t,{respond:(path,method,payload,server)=>{
    if(path===prefix+'/publication'&&method==='POST'){version=8;server.data=site({version});server.status=publication({draft_version:version});throw Object.assign(Error('O rascunho mudou. Atualize o estado.'),{status:409});}
  }});
  h.click('[data-os-action=publish-site]');h.submit('[data-os-form=site-publication]');await flush();
  assert.match(h.document.querySelector('[data-os-form=site-publication] [role=alert]').textContent,/rascunho mudou/);
  assert.doesNotMatch(toast(h),/publicado/);
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Pronto para publicar');
  assert.equal(h.document.querySelector('[data-os-form=site-publication] [data-os-action=site-refresh]').hidden,false);
  h.click('[data-os-form=site-publication] [data-os-action=site-refresh]');await flush();
  h.click('[data-os-action=publish-site]');
  assert.equal(server.status.draft_version,8);
  assert.equal(writes(h).length,1,'refresh does not repeat the publication');
});

test('an incomplete publication response never produces a success notice',async t=>{
  const {h}=await setup(t,{respond:(path,method)=>path===prefix+'/publication'&&method==='POST'?{}:undefined});
  h.click('[data-os-action=publish-site]');h.submit('[data-os-form=site-publication]');await flush();
  assert.match(h.document.querySelector('[data-os-form=site-publication] [role=alert]').textContent,/confirmar o resultado/);
  assert.equal(toast(h),'');assert.equal(h.document.querySelector('[data-os-form=site-publication] button[type=submit]').disabled,false);
});

test('dirty settings cannot publish or be silently discarded by switching tabs',async t=>{
  const {h}=await setup(t);
  h.click('[data-os-site-tab=settings]');h.fill('#osSiteForm [name=title]','Rascunho novo');
  h.click('[data-os-action=publish-site]');assert.equal(h.document.querySelector('[data-os-form=site-publication]'),null);
  h.click('[data-os-site-tab=dashboard]');assert.equal(h.document.querySelector('#osSiteForm [name=title]').value,'Rascunho novo');
  assert.match(toast(h),/Salve as configurações/);assert.equal(writes(h).length,0);
});

test('saving settings writes only the draft and does not publish it automatically',async t=>{
  const {h}=await setup(t,{respond:(path,method,payload,server)=>{
    if(path===prefix&&method==='PUT'){server.data={...server.data,...payload,version:5};server.status=publication({draft_version:5,state:'draft_saved'});return server.data;}
  }});
  h.click('[data-os-site-tab=settings]');h.fill('#osSiteForm [name=title]','Novo rascunho');h.submit('#osSiteForm');await flush();
  assert.equal(writes(h).length,1);assert.equal(writes(h)[0].path,prefix);assert.equal(writes(h)[0].method,'PUT');
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Rascunho salvo');
  assert.equal(h.document.querySelector('#osSiteForm [name=title]').value,'Novo rascunho');
});

test('a version mismatch between draft and status blocks publication until both are refreshed',async t=>{
  const {h,server}=await setup(t,{status:publication({draft_version:8})});
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,true);
  assert.match(h.document.querySelector('#osSitePublication [role=alert]').textContent,/rascunho mudou/);
  const forged=h.document.createElement('button');forged.dataset.osAction='publish-site';h.document.body.append(forged);forged.click();
  assert.equal(h.document.querySelector('[data-os-form=site-publication]'),null);assert.equal(writes(h).length,0);
  server.data=site({version:8});h.click('[data-os-action=site-refresh]');await flush();
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,false);
  h.click('[data-os-action=publish-site]');
  assert.equal(writes(h).length,0,'a refreshed state still requires explicit confirmation');
});

test('fresh preferences at version zero never display a false concurrent-change alert',async t=>{
  const {h}=await setup(t,{data:site({version:0,title:'',slug:null}),status:publication({state:'not_published',draft_version:0,ready:false,issues:[{code:'title_required',message:'Informe o nome público da empresa',section:'config'}]})});
  assert.equal(h.document.getElementById('osSitePublicationState').textContent,'Não publicado');
  assert.equal(h.document.querySelector('#osSitePublication [role=alert]'),null);
  assert.doesNotMatch(h.document.getElementById('osSitePublication').textContent,/rascunho mudou/);
  assert.equal(h.document.querySelector('[data-os-action=publish-site]').disabled,true);
  h.click('[data-os-site-tab=settings]');assert.ok(h.document.getElementById('osSiteForm'));
  assert.equal(writes(h).length,0);
});

test('all advertised publication states are rendered from the backend response',async t=>{
  for(const [state,label] of [['not_published','Não publicado'],['draft_saved','Rascunho salvo'],['ready','Pronto para publicar'],['blocked','Publicação impedida por pendências']]){
    const {h}=await setup(t,{status:publication({state})});
    assert.equal(h.document.getElementById('osSitePublicationState').textContent,label);assert.equal(writes(h).length,0);
  }
});

test('choosing a slug checks availability after a debounce and ignores old responses',async t=>{
  const old=deferred();
  const {h}=await setup(t,{respond:path=>{
    if(path===prefix+'/slug-availability?slug=primeiro-endereco')return old.promise;
    if(path===prefix+'/slug-availability?slug=segundo-endereco')return {slug:'segundo-endereco',available:false,reason:'Endereço reservado'};
  }});
  h.click('[data-os-site-tab=settings]');
  h.fill('#osSiteForm [name=slug]','primeiro-endereco');assert.equal(h.calls.filter(c=>c.path.includes('slug-availability')).length,0);
  await sleep(340);await flush();assert.equal(h.calls.filter(c=>c.path.includes('slug-availability')).length,1);
  h.fill('#osSiteForm [name=slug]','segundo-endereco');await sleep(340);await flush();
  assert.match(h.document.getElementById('osSiteSlugStatus').textContent,/Endereço reservado/);
  assert.equal(h.document.querySelector('#osSiteForm [name=slug]').checkValidity(),false);
  old.resolve({slug:'primeiro-endereco',available:true});await flush();
  assert.match(h.document.getElementById('osSiteSlugStatus').textContent,/Endereço reservado/);
  h.fill('#osSiteForm [name=slug]','ENDEREÇO INVALIDO');await sleep(340);await flush();
  assert.equal(h.calls.filter(c=>c.path.includes('slug-availability')).length,2,'invalid identifiers are rejected before querying');
  assert.equal(writes(h).length,0);
});

test('slug check failure explains fallback validation without blocking draft saving',async t=>{
  const {h}=await setup(t,{respond:path=>{if(path.includes('slug-availability'))throw Error('Rede indisponível');}});
  h.click('[data-os-site-tab=settings]');h.fill('#osSiteForm [name=slug]','novo-endereco');await sleep(340);await flush();
  assert.match(h.document.getElementById('osSiteSlugStatus').textContent,/validada ao salvar/);
  assert.equal(h.document.querySelector('#osSiteForm [name=slug]').checkValidity(),true);
  assert.equal(writes(h).length,0);
});

test('late publication reads cannot replace the newly opened area',async t=>{
  const pending=deferred();
  const h=await makeSuite({initialRoute:'site',respond:path=>path===prefix+'/publication'?pending.promise:path===prefix?site():defaultResponse(path)});
  t.after(()=>h.close());await flush();await h.window.OrionSuite.open('clientes');
  pending.resolve(publication());await flush();
  assert.equal(h.document.getElementById('osSitePublicationState'),null);
  assert.equal(h.document.querySelector('[data-os-action=new-customer]')!==null,true);
  assert.equal(writes(h).length,0);
});
