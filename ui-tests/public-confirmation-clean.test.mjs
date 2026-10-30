import test from 'node:test';
import assert from 'node:assert/strict';
import { flush } from './harness.mjs';
import { makePublic, catalogue, publicBase, quote, firstStart, response, confirmed } from './public-harness.mjs';

async function confirmStage(h){
  await flush();
  h.click('#services-grid .service-card');
  h.click('#booking-next');await flush();
  h.click('#time-slots .slot');
  h.click('#booking-next');
}

test('confirmation is compact, without duplicate price, long checkbox or privacy paragraph',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  const body=h.document.getElementById('booking-body');
  assert.ok(body.classList.contains('booking-confirmation-body'));
  assert.equal(body.querySelectorAll('.review-card').length,1);
  assert.equal(body.querySelector('h3.booking-contact-heading').textContent,'Seus dados');
  assert.equal(body.querySelector('h3:not(.booking-contact-heading)'),body.querySelector('.review-card h3'));
  assert.equal(body.querySelector('.consent-label'),null);
  assert.equal(body.querySelector('.privacy-note'),null);
  assert.equal(body.querySelector('[name=confirmed]'),null);
  assert.ok(body.querySelector('.review-row.total'));
  assert.match(body.querySelector('.review-row.total').textContent,/52,00/);
  assert.equal(h.document.getElementById('booking-total').hidden,true);
  assert.ok(h.document.querySelector('.booking-footer').classList.contains('booking-footer-confirm'));
  assert.equal(h.document.getElementById('booking-next').textContent,'Confirmar agendamento');
  assert.equal(h.document.getElementById('booking-next').disabled,false);
  assert.deepEqual([...body.querySelectorAll('#customer-form .field')].map(label=>label.textContent.replace(/\s+/g,' ').trim()),
    ['Nome completo','WhatsApp com DDD','Data de nascimento']);
  const birth=body.querySelector('[name=customer_birth_date]');
  assert.equal(birth.type,'date');assert.equal(birth.required,true);
  assert.equal(birth.min,'1900-01-01');
  assert.equal(birth.max,catalogue().today);
  h.click('#booking-back');
  assert.equal(h.document.getElementById('booking-total').hidden,false);
  assert.equal(h.document.querySelector('.booking-footer').classList.contains('booking-footer-confirm'),false);
  assert.equal(h.errors.length,0);
});

test('Brazilian WhatsApp is masked while typing, pasting +55 and using 10-digit landlines',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  const input=h.document.querySelector('[name=customer_phone]');
  assert.equal(input.type,'tel');
  assert.equal(input.placeholder,'(00) 00000-0000');
  for(const [typed,shown] of [
    ['86','(86'],['86972','(86) 972'],
    ['86972676282','(86) 97267-6282'],
    ['+55 (86) 97267-6282','(86) 97267-6282'],
    ['8632676282','(86) 3267-6282']
  ]){
    h.fill('[name=customer_phone]',typed);
    assert.equal(input.value,shown);
  }
  assert.equal(h.errors.length,0);
});

test('submits exactly once with canonical WhatsApp, real birth date and affirmative button',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  h.fill('[name=customer_name]',' Cliente sintética ');
  h.fill('[name=customer_phone]','86972676282');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0,'missing birth date must not submit');
  h.fill('[name=customer_birth_date]','2030-01-08');
  h.click('#booking-next');await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,0,'future birthday must not submit');
  h.fill('[name=customer_birth_date]','1994-04-09');
  h.click('#booking-next');await flush();
  const posts=h.calls.filter(x=>x.options.method==='POST');
  assert.equal(posts.length,1);
  assert.equal(posts[0].url,publicBase+'/appointments');
  assert.deepEqual(posts[0].body,{
    quote,start_at:firstStart,customer_name:'Cliente sintética',
    customer_phone:'+5586972676282',customer_birth_date:'1994-04-09',confirmed:true
  });
  assert.equal(h.document.querySelector('[name=customer_birth_date]'),null);
  assert.match(h.document.getElementById('booking-body').textContent,/Agendamento confirmado!/);
  assert.equal(h.errors.length,0);
});

test('form values survive back/forward and return to an unmodified confirmation view',async t=>{
  const h=await makePublic();t.after(()=>h.close());await confirmStage(h);
  h.fill('[name=customer_name]','Cliente visitante');
  h.fill('[name=customer_phone]','86972676282');
  h.fill('[name=customer_birth_date]','1989-07-05');
  h.click('#booking-back');h.click('#booking-next');
  assert.equal(h.document.querySelector('[name=customer_phone]').value,'(86) 97267-6282');
  assert.equal(h.document.querySelector('[name=customer_birth_date]').value,'1989-07-05');
  assert.equal(h.document.querySelector('[name=customer_name]').value,'Cliente visitante');
  assert.equal(h.document.getElementById('booking-total').hidden,true);
  assert.equal(h.errors.length,0);
});


test('WhatsApp abre automaticamente somente depois de o backend confirmar, com protocolo correto',async t=>{
  let finish;
  const pending = new Promise(resolve => { finish = resolve; });
  const h=await makePublic({ respond: url => url.endsWith('/appointments') ? pending : undefined });
  t.after(()=>h.close());await confirmStage(h);
  const opened=[],navigated=[];
  const tab={opener:h.window,location:{replace:url=>navigated.push(url)}};
  h.window.open=(url,target)=>{opened.push({url,target});return tab;};
  h.fill('[name=customer_name]','Cliente sintético');
  h.fill('[name=customer_phone]','86972676282');
  h.fill('[name=customer_birth_date]','1990-07-06');
  h.click('#booking-next');
  h.click('#booking-next');
  await flush();
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,1,'não duplicar reserva');
  assert.equal(opened.length,0,'não abrir WhatsApp enquanto a gravação estiver pendente');

  const message='*Olá, Orion!* 👋\\n\\nAcabei de realizar um agendamento pelo site e gostaria de confirmar meu horário. 😊\\n\\n🧾 Protocolo: *AG-G32XU5*\\n\\n_Aguardo os detalhes da minha confirmação._';
  const link='https://wa.me/5567999999999?text='+encodeURIComponent(message);
  finish(response({...confirmed(),protocol:'AG-G32XU5',whatsapp_confirmation_url:link},201));
  await flush();
  assert.deepEqual(opened,[{url:'about:blank',target:'_blank'}]);
  assert.equal(tab.opener,null,'janela do WhatsApp não acessa a página do Orion');
  assert.deepEqual(navigated,[link]);
  assert.match(h.document.getElementById('booking-body').textContent,/Agendamento confirmado!/);
  assert.match(h.document.getElementById('booking-body').textContent,/AG-G32XU5/);
  const fallback=h.document.querySelector('.success a[href^="https://wa.me/"]');
  assert.ok(fallback,'reabrir WhatsApp se o cliente voltar à confirmação');
  assert.equal(fallback.href,link);
  assert.equal(fallback.textContent,'Abrir WhatsApp novamente');
  assert.equal(fallback.target,'_blank');
  assert.equal(h.errors.length,0);
});

test('sem URL autorizada ou com URL adulterada, agendamento continua salvo sem redirecionar',async t=>{
  for(const badUrl of [null,'https://wa.me.evil.example/5567999999999?text=oi',
                       'javascript:alert(1)','https://wa.me/5567999999999?text=oi&next=https://example.com']){
    const h=await makePublic({respond: url => url.endsWith('/appointments')
      ? response({...confirmed(),whatsapp_confirmation_url:badUrl},201) : undefined});
    t.after(()=>h.close());
    const opened=[];h.window.open=()=>{opened.push(true);return null;};
    await confirmStage(h);
    h.fill('[name=customer_name]','Cliente sintético');
    h.fill('[name=customer_phone]','86972676282');
    h.fill('[name=customer_birth_date]','1990-07-06');
    h.click('#booking-next');await flush();
    assert.equal(opened.length,0,'não abrir URL insegura ou ausente');
    assert.match(h.document.querySelector('.success').textContent,/Seu agendamento foi salvo/);
    assert.equal(h.document.querySelector('.success a[href^="https://wa.me/"]'),null);
    assert.equal(h.errors.length,0);
  }
});

test('erro ao reservar não abre WhatsApp nem exibe comprovante de sucesso',async t=>{
  const h=await makePublic({respond: url => url.endsWith('/appointments')
    ? response({detail:'Horário indisponível'},409) : undefined});
  t.after(()=>h.close());
  let opened=0;h.window.open=()=>{opened++;return null;};
  await confirmStage(h);
  h.fill('[name=customer_name]','Cliente sintético');
  h.fill('[name=customer_phone]','86972676282');
  h.fill('[name=customer_birth_date]','1990-07-06');
  h.click('#booking-next');await flush();
  assert.equal(opened,0);
  assert.equal(h.document.querySelector('.success'),null);
  assert.equal(h.calls.filter(x=>x.options.method==='POST').length,1);
  assert.equal(h.errors.length,0);
});
