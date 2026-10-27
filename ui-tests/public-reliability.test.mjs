import test from 'node:test';
import assert from 'node:assert/strict';
import {flush,deferred} from './harness.mjs';
import {makePublic,catalogue,available,confirmed,response,quote,firstStart,publicBase} from './public-harness.mjs';

const key='orion-public-recovery:'+publicBase;
test('SSR habilita agendamento somente depois que o catálogo foi inicializado no navegador',async t=>{
  const pending=deferred();
  const h=await makePublic({respond:url=>url===publicBase?pending.promise:undefined});
  t.after(()=>h.close());
  const nodes=['hero-book','share-site','service-search'].map(id=>h.document.getElementById(id));
  for(const node of nodes){node.disabled=true;node.setAttribute('data-server-loading','');}
  h.document.body.style.setProperty('--primary','#fdc700');h.document.body.style.setProperty('--primary-ink','#000000');
  pending.resolve(response(catalogue()));await flush();
  for(const node of nodes){assert.equal(node.disabled,false);assert.equal(node.hasAttribute('data-server-loading'),false);}
  assert.equal(h.document.body.style.getPropertyValue('--primary'),'');
  assert.equal(h.document.body.style.getPropertyValue('--primary-ink'),'');
  assert.ok(h.calls.every(call=>call.options.credentials==='same-origin'));
});
test('falha da hidratação mantém SSR legível, informa erro e permite repetir sem ativar controles antes da resposta',async t=>{
  let attempt=0;const pending=deferred();
  const h=await makePublic({respond:url=>url===publicBase?(++attempt===1?pending.promise:response(catalogue())):undefined});
  t.after(()=>h.close());
  const content=h.document.getElementById('site-content');content.hidden=false;
  const grid=h.document.getElementById('services-grid');grid.textContent='Serviço publicado no servidor';
  const hero=h.document.getElementById('hero-book');hero.disabled=true;hero.setAttribute('data-server-loading','');
  const message=h.document.getElementById('page-message');message.hidden=true;
  pending.resolve(response({detail:'Falha temporária.'},503));await flush();
  assert.equal(content.hidden,false);assert.equal(message.hidden,false);
  assert.equal(grid.textContent,'Serviço publicado no servidor');assert.equal(hero.disabled,true);
  h.click('#page-message button');await flush();
  assert.equal(message.hidden,true);assert.equal(hero.disabled,false);
  assert.ok(grid.querySelector('.service-card'));
});
async function review(h){
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  h.click('#time-slots .slot');h.click('#booking-next');
  h.fill('[name=customer_name]','Cliente sintético');h.fill('[name=customer_phone]','(11) 99999-9999');
  h.click('[name=confirmed]');
}
test('busca encontra equivalentes sem acento e mantém conteúdo como texto',async t=>{
  const data=catalogue();data.services[0].name='Hidratação e Pé';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.fill('#service-search','HIDRATACAO');assert.equal(h.document.querySelectorAll('#services-grid .service-card').length,1);
  h.fill('#service-search','pe');assert.equal(h.document.querySelectorAll('#services-grid .service-card').length,1);
});
test('share.google preserva local oficial sem resolver link ou mascarar iframe como precisão confirmada',async t=>{
  const data=catalogue();data.site.maps_url='https://share.google/Abc123xyZ';
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('maps-link').href,data.site.maps_url);
  assert.equal(h.document.getElementById('hero-map').href,data.site.maps_url);
  assert.match(h.document.getElementById('maps-link').textContent,/localização oficial/);
  assert.match(h.document.getElementById('location-map').title,/Busca pelo endereço/);
  assert.equal(h.calls.length,1,'não resolve endereço compartilhado pelo servidor nem faz chamadas extras');
});
test('calendário desabilita somente dias fechados autoritativos do profissional e não infere do resumo da empresa',async t=>{
  const data=catalogue();data.professionals[0].bookable_weekdays=[0,1,2,3,4,5];
  const h=await makePublic({data});t.after(()=>h.close());await flush();
  h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  const sunday=h.document.querySelector('[data-day="2030-01-13"]');
  assert.equal(sunday.disabled,true);assert.match(sunday.textContent,/Fechado/);
  assert.match(sunday.getAttribute('aria-label'),/Fechado/);
  const count=h.calls.length;sunday.click();assert.equal(h.calls.length,count);
  assert.match(h.document.querySelector('.timezone-note').textContent,/Horário local da barbearia/);
  assert.doesNotMatch(h.document.querySelector('.timezone-note').textContent,/America\//);
  const legacy=await makePublic();t.after(()=>legacy.close());await flush();
  legacy.click('#services-grid .service-card');legacy.click('#booking-next');await flush();
  assert.equal(legacy.document.querySelector('[data-day="2030-01-13"]').disabled,false,'sem metadado preciso, backend decide');
});
test('mudança de etapa e erros levam foco a contexto acessível, seleção preserva foco e fechamento retorna ao abridor',async t=>{
  const h=await makePublic();t.after(()=>h.close());await flush();
  const opener=h.document.querySelector('#services-grid .service-card');opener.focus();opener.click();
  assert.match(h.document.activeElement.textContent,/Escolha seus serviços/);
  const card=h.document.querySelector('#booking-service-list .service-card');card.focus();card.click();
  assert.equal(h.document.activeElement,card);
  card.click();h.click('#booking-next');await flush();
  assert.match(h.document.activeElement.textContent,/Escolha o profissional/);
  h.click('#close-booking');assert.equal(h.document.activeElement,opener);
});
test('conflito temporário mantém tentativa, dados, consentimento e identidade em retry sem persistir dados pessoais',async t=>{
  let posts=0;
  const h=await makePublic({respond:url=>{
    if(url.endsWith('/appointments'))return ++posts===1?
      response({detail:{code:'agenda_busy',message:'Agenda ocupada temporariamente.',retryable:true}},409):response(confirmed(),201);
  }});t.after(()=>h.close());await flush();await review(h);
  h.click('#booking-next');await flush();
  assert.ok(h.document.querySelector('#customer-form'));
  assert.equal(h.document.querySelector('[name=confirmed]').checked,true);
  assert.equal(h.document.querySelector('[name=customer_phone]').value,'(11) 99999-9999');
  assert.equal(h.document.querySelector('[name=customer_phone]').readOnly,true);
  assert.equal(h.document.getElementById('booking-back').disabled,true);
  assert.equal(h.document.activeElement.id,'booking-error');
  const draft=JSON.parse(h.window.sessionStorage.getItem(key));
  assert.deepEqual(draft,{quote,start_at:firstStart});assert.equal(h.window.localStorage.length,0);
  h.click('#booking-next');await flush();
  const requests=h.calls.filter(x=>x.url.endsWith('/appointments'));
  assert.equal(requests.length,2);assert.deepEqual(requests[0].body,requests[1].body);
  assert.equal(h.window.sessionStorage.getItem(key),null);
  assert.match(h.document.getElementById('booking-body').textContent,/cancelar ou remarcar/);
  assert.equal(h.document.querySelector('.booking-follow-up a').href,'https://wa.me/5511999999999');
});
test('perda de resposta preserva tentativa e explica falha da conexão em português',async t=>{
  const h=await makePublic({respond:url=>{if(url.endsWith('/appointments'))throw new TypeError('Failed to fetch');}});
  t.after(()=>h.close());await flush();await review(h);h.click('#booking-next');await flush();
  assert.match(h.document.getElementById('booking-error').textContent,/Não foi possível conectar ao servidor/);
  assert.ok(h.window.sessionStorage.getItem(key));assert.equal(h.document.querySelector('[name=confirmed]').checked,true);
  assert.equal(h.document.querySelector('[name=customer_phone]').value,'(11) 99999-9999');
});
test('reserva alterada por outra via mantém identidade e orienta recuperação ou contato em vez de criar outra',async t=>{
  const h=await makePublic({respond:url=>url.endsWith('/appointments')?response({detail:{code:'appointment_changed',message:'Este agendamento já foi alterado. Entre em contato com a barbearia.',retryable:false}},409):undefined});
  t.after(()=>h.close());await flush();await review(h);h.click('#booking-next');await flush();
  assert.ok(h.window.sessionStorage.getItem(key));assert.match(h.document.getElementById('booking-body').textContent,/Recuperar confirmação/);
  assert.equal(h.document.querySelector('#booking-service-list'),null);assert.equal(h.document.querySelectorAll('.booking-body a[href^="https://wa.me/"]').length,1);
  assert.equal(h.calls.filter(call=>call.url.endsWith('/appointments')).length,1);
});
test('409 antigo e ambíguo preserva tentativa, conflito definitivo renova disponibilidade sem apagar nome e telefone',async t=>{
  let n=0;
  const h=await makePublic({respond:url=>{
    if(url.endsWith('/appointments'))return response({detail:++n===1?'Agenda ocupada.':{code:'slot_unavailable',message:'Horário ocupado.',retryable:false}},409);
  }});t.after(()=>h.close());await flush();await review(h);
  h.click('#booking-next');await flush();assert.ok(h.document.querySelector('#customer-form'));
  h.click('#booking-next');await flush();
  assert.equal(h.document.querySelector('#customer-form'),null);
  assert.equal(h.window.sessionStorage.getItem(key),null);
  assert.equal(h.calls.filter(x=>x.url.includes('/availability?')).length,2);
  h.click('#time-slots .slot');h.click('#booking-next');
  assert.equal(h.document.querySelector('[name=customer_phone]').value,'(11) 99999-9999');
});
test('reload oferece recuperação somente consultiva e mantém token em processamento ou telefone incorreto',async t=>{
  let n=0;
  const h=await makePublic({initialSession:{[key]:JSON.stringify({quote,start_at:firstStart})},respond:url=>{
    if(url.endsWith('/appointments/recover')){
      n++;if(n===1)return response({status:'pending'},202);
      if(n===2)return response({detail:{code:'confirmation_not_found',message:'Não encontrada para este telefone.'}},404);
      return response(confirmed());
    }
  }});t.after(()=>h.close());await flush();
  assert.ok(h.document.getElementById('booking-recovery-banner'));
  h.click('#hero-book');assert.match(h.document.getElementById('booking-body').textContent,/Recuperar confirmação/);
  assert.equal(h.document.querySelector('[name=customer_name]'),null);
  h.fill('[name=customer_phone]','(11) 99999-9999');h.click('#booking-next');await flush();
  assert.match(h.document.getElementById('booking-error').textContent,/em processamento/);
  assert.ok(h.window.sessionStorage.getItem(key));h.click('#booking-next');await flush();
  assert.ok(h.window.sessionStorage.getItem(key),'telefone incorreto não descarta identificação');
  assert.match(h.document.querySelector('#recovery-restart').textContent,/Se o telefone estiver incorreto, pode existir uma reserva/);
  h.click('#booking-next');await flush();assert.match(h.document.getElementById('booking-body').textContent,/SYNTHETIC-1/);
  assert.equal(h.window.sessionStorage.getItem(key),null);
  assert.equal(h.calls.some(x=>x.url.endsWith('/appointments')),false,'recuperação nunca confirma reserva nova');
  for(const call of h.calls.filter(x=>x.options.method==='POST'))assert.deepEqual(call.body,{quote,start_at:firstStart,customer_phone:'(11) 99999-9999'});
});
test('não encontrado mantém tentativa até escolha explícita de novo agendamento após aviso',async t=>{
  const h=await makePublic({initialSession:{[key]:JSON.stringify({quote,start_at:firstStart})},respond:url=>
    url.endsWith('/appointments/recover')?response({detail:{code:'confirmation_not_found',message:'Confira o telefone.',retryable:true}},404):undefined});
  t.after(()=>h.close());await flush();h.click('#hero-book');
  h.fill('[name=customer_phone]','(11) 99999-9999');h.click('#booking-next');await flush();
  assert.ok(h.window.sessionStorage.getItem(key));assert.equal(h.document.querySelector('#booking-service-list'),null);
  h.click('#recovery-restart button');assert.equal(h.window.sessionStorage.getItem(key),null);
  assert.ok(h.document.querySelector('#booking-service-list'));
  assert.equal(h.calls.some(x=>x.url.endsWith('/appointments')),false);
});
test('sem vagas oferece próximo dia por ação explícita e evita consultas em dias fechados',async t=>{
  const data=catalogue();data.today='2030-01-12';data.professionals[0].bookable_weekdays=[0,1,2,3,4,5];
  const h=await makePublic({data,respond:url=>url.includes('/availability?')?response({...available(),slots:[]}):undefined});
  t.after(()=>h.close());await flush();h.click('#services-grid .service-card');h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.url.includes('/availability?')).length,1);
  h.click('.availability-alternatives button');await flush();
  const calls=h.calls.filter(x=>x.url.includes('/availability?'));assert.equal(calls.length,2);
  assert.match(calls[1].url,/day=2030-01-14/);
});
