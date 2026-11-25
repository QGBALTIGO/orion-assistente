import test from 'node:test';
import assert from 'node:assert/strict';
import {entry,flush,fullState,makeOnboarding,tenantA} from './onboarding-harness.mjs';

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
  const expected='/api/v1/afiliados/'+tenantA;
  assert.equal(h.element('a.ob-button').getAttribute('href'),expected);
  assert.equal(h.element('[data-ob-title]').textContent,'Seu espaço de Afiliados');
  assert.equal(await h.controller.resolveEntry().then(r=>r.kind),'affiliate');
  assert.equal(h.element('a.ob-button').getAttribute('href'),expected);
  assert.deepEqual(h.errors,[]);
});
