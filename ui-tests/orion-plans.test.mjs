import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {JSDOM} from 'jsdom';
import {planState} from './harness.mjs';

const code=fs.readFileSync(new URL('../app/static/orion-plans.js',import.meta.url),'utf8');
const css=fs.readFileSync(new URL('../app/static/orion-plans.css',import.meta.url),'utf8');
const base={
 plans:[
 {code:'essencial',name:'Orion Essencial',monthly_cents:2990,max_professionals:2},
 {code:'profissional',name:'Orion Profissional',monthly_cents:8990,max_professionals:6},
 {code:'elite',name:'Orion Elite',monthly_cents:13990,max_professionals:10}],
 subscription:null,trial:null,active_professionals:0,can_manage_plan:true,checkout_enabled:false,
 trial_scope:['Agenda','Clientes'],trial_requires_team_activation:true
};
function fixture(overrides={},options={}){
 const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><div id="host"></div></body></html>',
    {url:'https://orionagenda.pro/panel',runScripts:'dangerously'});
 const {window}=dom;
 Object.defineProperty(window.HTMLDialogElement.prototype,'showModal',{configurable:true,value:function(){this.open=true;}});
 Object.defineProperty(window.HTMLDialogElement.prototype,'close',{configurable:true,value:function(){this.open=false;}});
 window.OrionFields={
  phoneDisplay:value=>value,
  phoneValue:value=>{if(value==='+5567999991111'||value==='(67) 99999-1111')return '+5567999991111';throw Error('WhatsApp inválido');}
 };
 window.eval(code);
 const subscription=overrides.subscription;
 const effective=subscription?planState(subscription.code?.replace('orion_','')||'essencial',{name:subscription.name,commercial_subscription:subscription,active:subscription.status==='active',limits:{max_professionals:subscription.max_professionals,active_professionals:0,excess_professionals:0}}):planState('none');
 const calls=[],overview={...base,effective_plan:effective,...overrides};let current=options.pixCurrent||{status:'none'},chargeAttempts=0;
 const ctx={user:{name:'Pessoa Sintética'},tenant:{name:'Empresa Sintética'},canManage:options.canManage!==false,isAdmin:!!options.isAdmin,
  request:async(path,method='GET',data)=>{calls.push({path,method,data});
   if(path==='/plan-trial'){overview.trial={status:'pending',started_at:null,ends_at:null};return{status:'pending',message:'Solicitação recebida!'};}
   if(path==='/plan-overview')return overview;
   if(path==='/plan-pix-current'){if(options.currentUnavailable||options.currentUnavailableAfterCharge&&chargeAttempts)throw Error('synthetic current lookup unavailable');return current;}
   if(path==='/plan-pix-config')return options.pixConfig||{available:false,methods:[],mode:'manual_30_days'};
   if(path==='/plan-pix-charge' && options.pixConfig?.available){
    const attempt=chargeAttempts++;
    if(attempt<(options.chargeRejections?.length||0)){
     current=options.rejectionCurrent||{status:'none'};
     throw Object.assign(Error('synthetic payment form rejection'),{status:options.chargeRejections[attempt]});
    }
    current={status:'pending_confirmation',intent_id:'synthetic-pix-intent',
            plan_code:data.plan_code,
            monthly_cents:overview.plans.find(plan=>plan.code===data.plan_code).monthly_cents,
            pix_code:'00020126580014br.gov.bcb.pix.synthetic-example',
            pix_qr_data_uri:'data:image/png;base64,c3ludGhldGlj'};
    if(options.lostChargeResponse)throw Error('synthetic response lost after creation');return current;
   }
   if(path.startsWith('/plan-pix-status?') && options.pixConfig?.available)
    return options.pixStatus||{status:'pending'};
   throw Error('Caminho não permitido: '+path);
  }};
 const root=window.document.getElementById('host');
 window.OrionPlans.mount(root,overview,ctx);
 return {window,root,calls,ctx,overview,get current(){return current;},close:()=>dom.window.close()};
}
function tick(){return new Promise(resolve=>setImmediate(resolve));}
function fillPix(f,dialog){
 const form=dialog.querySelector('[data-pix-form]');
 for(const [key,value] of Object.entries({name:'Pessoa Sintética',email:'synthetic@example.test',phone:'(67) 99999-1111',doc_number:'52998224725'}))form.elements.namedItem(key).value=value;
 form.elements.namedItem('one_time').checked=true;form.elements.namedItem('understood').checked=true;
 form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
}

test('only the owner sees an enabled prepaid renewal, including when the current tier matches',()=>{
 const active=planState('essencial',{active:true,source:'pix_prepaid'});
 for(const options of [{canManage:false},{}]){
  const f=fixture({effective_plan:active,can_manage_plan:false,checkout_enabled:true},options);
  try{assert.ok([...f.root.querySelectorAll('[data-op-checkout]')].every(button=>button.disabled));
   assert.equal(f.calls.length,0);assert.match(f.root.querySelector('.op-essencial [data-op-checkout]').textContent,/Somente proprietário/);
  }finally{f.close();}
 }
});

test('catalogue is the one source for displayed price, consent, charged-price precondition and professional limits',async()=>{
 const changed=base.plans.map(plan=>plan.code==='essencial'?{...plan,monthly_cents:1234,max_professionals:3}:plan);
 const f=fixture({plans:changed},{pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
 try{
  assert.match(f.root.querySelector('.op-essencial').textContent,/12,34/);assert.match(f.root.querySelector('.op-essencial').textContent,/até 3 profissionais/);
  const limits=[...f.root.querySelectorAll('.op-matrix-row')].find(row=>row.textContent.includes('Profissionais permitidos'));
  assert.match(limits.textContent,/Até 3/);
  f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');assert.match(dialog.textContent,/12,34/);fillPix(f,dialog);
  await tick();await tick();assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(f.calls.find(call=>call.path==='/plan-pix-charge').data.expected_cents,1234);
  dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('invalid or duplicated catalogue prices cannot open a financial form or generate a request',()=>{
 for(const plans of [base.plans.map(plan=>({...plan,monthly_cents:0})),base.plans.map(plan=>({...plan,monthly_cents:'2990'})),
   [...base.plans,{...base.plans[0]}]]){
  const f=fixture({plans});try{assert.equal(f.root.querySelector('.op-essencial [data-op-checkout]').disabled,true);assert.equal(f.calls.length,0);}finally{f.close();}
 }
});

test('reopening or reloading restores the authenticated existing Pix with its frozen amount and no new charge',async()=>{
 const pending={status:'pending_confirmation',intent_id:'synthetic-existing',plan_code:'elite',monthly_cents:1234,
  pix_code:'synthetic-existing-qr',pix_qr_data_uri:'data:image/png;base64,c3ludGhldGlj'};
 for(let reload=0;reload<2;reload++){
  const f=fixture({}, {pixCurrent:pending});try{
   for(let reopen=0;reopen<2;reopen++){
    f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
    const dialog=f.root.querySelector('.op-checkout-dialog');assert.ok(dialog.open);
    assert.equal(dialog.querySelector('[data-pix-code]').value,pending.pix_code);assert.match(dialog.querySelector('.op-payment-summary').textContent,/Elite.*12,34/);
    assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);assert.match(dialog.textContent,/Nenhuma nova cobrança/);
    dialog.querySelector('[data-pix-close]').click();
   }
   assert.equal(f.calls.filter(call=>call.path==='/plan-pix-current').length,2);
   assert.equal(f.calls.some(call=>call.method==='POST'||call.path==='/plan-pix-config'),false);
  }finally{f.close();}
 }
});

test('an ambiguous creation response recovers only the original order through GET',async()=>{
 const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'},lostChargeResponse:true});
 try{
  f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');fillPix(f,dialog);await tick();await tick();await tick();
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);assert.equal(f.calls.filter(call=>call.path==='/plan-pix-current').length,2);
  assert.equal(dialog.querySelector('[data-pix-code]').value,f.current.pix_code);assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);
  dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

for(const httpStatus of [400,422])test(`definitive${httpStatus} plus confirmed empty recovery permits corrected data and a new explicit click`,async()=>{
 const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'},chargeRejections:[httpStatus]});
 try{f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');fillPix(f,dialog);await tick();await tick();await tick();
  const form=dialog.querySelector('[data-pix-form]'),submit=dialog.querySelector('[data-pix-submit]');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1,'A rejection never sends another charge automatically');
  assert.equal(submit.disabled,false);assert.equal(form.hidden,false);
  form.elements.namedItem('email').value='corrected@example.test';
  form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
  const charges=f.calls.filter(call=>call.method==='POST');assert.equal(charges.length,2);assert.equal(charges[1].data.payer.email,'corrected@example.test');
  assert.ok(dialog.querySelector('[data-pix-code]'));dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('ambiguous response or unavailable recovery cannot enable a second financial POST',async()=>{
 for(const options of [{chargeRejections:[409]},{chargeRejections:[500]},{chargeRejections:[503]},
  {chargeRejections:[undefined]},{chargeRejections:[400],currentUnavailableAfterCharge:true},
  {chargeRejections:[400],rejectionCurrent:{status:'pending_confirmation',intent_id:'synthetic-racing-tab',plan_code:'essencial',monthly_cents:2990}}]){
  const f=fixture({}, {...options,pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
  try{f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
   const dialog=f.root.querySelector('.op-checkout-dialog');fillPix(f,dialog);await tick();await tick();await tick();
   assert.equal(dialog.querySelector('[data-pix-submit]').disabled,true);
   dialog.querySelector('form').dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));await tick();
   assert.equal(f.calls.filter(call=>call.method==='POST').length,1);dialog.querySelector('[data-pix-close]').click();
  }finally{f.close();}
 }
});

test('pending bank confirmation without a reusable QR keeps polling the original intent without another charge',async()=>{
 const f=fixture({}, {pixCurrent:{status:'pending_confirmation',intent_id:'synthetic-confirming',plan_code:'essencial',monthly_cents:2990},
  pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
 try{f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');assert.equal(dialog.querySelector('[data-pix-code]'),null);
  assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);assert.match(dialog.textContent,/Aguardando confirmação/);
  dialog.querySelector('[data-pix-check]').click();await tick();await tick();
  assert.equal(f.calls.filter(call=>call.path==='/plan-pix-status?intent_id=synthetic-confirming').length,1);
  assert.equal(f.calls.some(call=>call.method==='POST'||call.path==='/plan-pix-config'),false);dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('an expired paid period stops the old payment wait and offers no automatic renewal',async()=>{
 const f=fixture({}, {pixCurrent:{status:'pending_confirmation',intent_id:'synthetic-expired',plan_code:'essencial',monthly_cents:2990},
  pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'},pixStatus:{status:'expired'}});
 try{f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');dialog.querySelector('[data-pix-check]').click();await tick();await tick();
  assert.match(dialog.querySelector('[data-pix-status]').textContent,/período encerrou/);
  assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);assert.equal(f.calls.some(call=>call.method==='POST'),false);
  dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('unknown, unavailable or review-required current payment cannot expose another charge form',async()=>{
 for(const options of [{currentUnavailable:true},{pixCurrent:{status:'review_required'}},{pixCurrent:{status:'unexpected'}}]){
  const f=fixture({}, {...options,pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
  try{f.root.querySelector('[data-op-checkout=essencial]').click();await tick();await tick();const dialog=f.root.querySelector('.op-checkout-dialog');
   assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);assert.equal(f.calls.some(call=>call.method==='POST'||call.path==='/plan-pix-config'),false);
   dialog.querySelector('[data-pix-close]').click();
  }finally{f.close();}
 }
});

test('manual Elite access is shown separately from the commercial Essencial subscription without another checkout',()=>{
 const subscription={code:'orion_essencial',name:'Orion Essencial',monthly_cents:2990,max_professionals:2,status:'active'};
 const f=fixture({subscription,effective_plan:planState('elite',{source:'manual',manual_assignment:{plan_code:'elite',active:true},commercial_subscription:subscription})});
 try{assert.match(f.root.querySelector('[data-op-effective-plan]').textContent,/Elite|Liberação manual/);assert.match(f.root.querySelector('.op-account-state:not([data-op-effective-plan])').textContent,/Essencial|29,90/);assert.equal(f.root.querySelector('.op-elite [data-op-checkout]').textContent,'Plano atual');assert.equal(f.root.querySelector('.op-elite [data-op-checkout]').disabled,true);assert.equal(f.calls.some(call=>call.method==='POST'),false);}finally{f.close();}
});

test('canceling renewal keeps the server-verified paid period visible without promising another automatic charge',()=>{
 const subscription={code:'orion_essencial',name:'Orion Essencial',monthly_cents:2990,max_professionals:2,status:'canceled',next_renewal_at:'2027-01-01T00:00:00Z'};
 const effective=planState('essencial',{active:true,source:'cakto',valid_until:'2027-01-01T00:00:00Z',commercial_subscription:subscription});
 const f=fixture({subscription,effective_plan:effective});try{
  assert.match(f.root.querySelector('[data-op-effective-plan]').textContent,/Renovação cancelada.*Acesso pago até/);
  const banner=f.root.querySelector('.op-account-state:not([data-op-effective-plan])');
  assert.match(banner.textContent,/Renovação cancelada/);assert.match(banner.textContent,/Acesso pago até/);
  assert.doesNotMatch(banner.textContent,/Próxima cobrança|Mensalidade/);
  assert.equal(f.root.querySelector('.op-essencial [data-op-checkout]').disabled,true);assert.equal(f.calls.length,0);
 }finally{f.close();}
});

test('billing unavailable is explained before checkout without starting a charge',()=>{
 const f=fixture();
 try{
  const notice=f.root.querySelector('.op-billing-notice');
  assert.ok(notice);
  assert.match(notice.textContent,/temporariamente indisponíveis/);
  assert.match(f.root.querySelector('.op-essencial .op-plan-caption').textContent,/nenhuma cobrança/);
  assert.equal(f.calls.some(call=>call.method==='POST'),false);
 }finally{f.close();}
});

test('three actual card prices, limits, recommendation and explicit AI separation',()=>{
 const f=fixture();
 try{
  assert.equal(f.root.querySelectorAll('.op-plan').length,3);
  assert.match(f.root.textContent,/R\$\s*29,90/);
  assert.match(f.root.textContent,/R\$\s*89,90/);
  assert.match(f.root.textContent,/R\$\s*139,90/);
  assert.match(f.root.querySelector('.op-profissional .op-recommended').textContent,/Recomendado/);
  assert.match(f.root.querySelector('.op-essencial').textContent,/até 2 profissionais/i);
  assert.match(f.root.querySelector('.op-profissional').textContent,/até 6 profissionais/i);
  assert.match(f.root.querySelector('.op-elite').textContent,/até 10 profissionais/i);
  assert.doesNotMatch(f.root.querySelector('.op-essencial').textContent,/Recepcionista IA/);
  assert.doesNotMatch(f.root.querySelector('.op-profissional').textContent,/Recepcionista IA/);
  assert.match(f.root.querySelector('.op-elite').textContent,/Recepcionista IA/);
  assert.equal(f.root.querySelectorAll('.op-matrix-category').length,10);
  assert.equal(f.root.querySelectorAll('.op-matrix thead th').length,4);
 }finally{f.close();}
});

test('desktop matrix and mobile comparison are always expanded with no category buttons',()=>{
 const f=fixture();
 try{
  const table=f.root.querySelector('.op-matrix');
  assert.ok(table);
  assert.equal(table.querySelectorAll('colgroup col').length,4);
  assert.equal(table.querySelectorAll('thead th').length,4);
  assert.equal(table.querySelectorAll('tbody[data-op-category]').length,10);
  assert.equal(f.root.querySelectorAll('.op-mobile-group').length,10);
  assert.equal(f.root.querySelectorAll('[data-op-category-toggle]').length,0);
  assert.equal(f.root.querySelectorAll('.op-matrix-category button').length,0);
  assert.match(table.querySelector('thead').textContent,/R\$\s*89,90/);
  assert.equal(table.querySelector('[data-op-category="0"] .op-matrix-category-label').textContent,'Gestão e agenda');
  assert.equal(table.querySelector('[data-op-category="0"] .op-matrix-row').querySelectorAll('th,td').length,4);
  assert.ok(f.root.querySelector('.op-mobile-feature .op-mobile-values[role=list]'));
  assert.equal(f.root.querySelector('.op-mobile-feature .op-mobile-values').children.length,3);
  assert.equal(f.root.querySelectorAll('.op-mobile-tier').length,f.root.querySelectorAll('.op-mobile-feature').length*3);
 }finally{f.close();}
});

test('show only differences is default, reversible and synchronized across mobile and desktop',()=>{
 const f=fixture();
 try{
  const toggle=f.root.querySelector('[data-op-only-differences]');
  assert.ok(toggle.checked);
  assert.equal(f.root.querySelectorAll('[data-op-only-differences]').length,1);
  const table=f.root.querySelector('.op-matrix');
  const mobile=f.root.querySelector('.op-mobile-comparison');
  assert.equal(table.querySelectorAll('.op-matrix-row:not([hidden])').length,15);
  assert.equal(mobile.querySelectorAll('.op-mobile-feature:not([hidden])').length,15);
  assert.equal(table.querySelectorAll('tbody:not([hidden])').length,8);
  assert.equal(mobile.querySelectorAll('.op-mobile-group:not([hidden])').length,8);
  assert.ok(table.querySelector('[data-op-category="2"]').hidden,'identical section hidden');
  toggle.click();
  assert.equal(toggle.checked,false);
  assert.equal(table.querySelectorAll('.op-matrix-row:not([hidden])').length,25);
  assert.equal(mobile.querySelectorAll('.op-mobile-feature:not([hidden])').length,25);
  assert.equal(table.querySelectorAll('tbody:not([hidden])').length,10);
  assert.equal(mobile.querySelectorAll('.op-mobile-group:not([hidden])').length,10);
  toggle.click();
  assert.equal(table.querySelectorAll('.op-matrix-row:not([hidden])').length,15);
  assert.equal(mobile.querySelectorAll('.op-mobile-feature:not([hidden])').length,15);
  assert.match(f.root.querySelector('[data-op-compare-state]').textContent,/somente diferenças/);
 }finally{f.close();}
});

test('Essencial commercial comparison removes manual WhatsApp while keeping performance; higher tiers unchanged',()=>{
  const f=fixture();
  try{
    const essential=f.root.querySelector('.op-essencial').textContent;
    assert.match(essential,/Meu desempenho: atendimentos e faturamento/);
    assert.doesNotMatch(essential,/Central de conversas e respostas manuais/);
    const rows=[...f.root.querySelectorAll('.op-matrix-row')];
    const find=label=>rows.find(row=>row.textContent.includes(label));
    const manual=find('Conversas e respostas manuais');
    const connection=find('Conectar WhatsApp da empresa');
    const performance=find('Relatórios por profissional');
    for(const row of [manual,connection,performance])assert.ok(row);
    for(const row of [manual,connection]){
      const cells=row.querySelectorAll('td');
      assert.ok(cells[0].querySelector('.op-no'),row.textContent);
      assert.ok(cells[1].querySelector('.op-yes'),row.textContent);
      assert.ok(cells[2].querySelector('.op-yes'),row.textContent);
    }
    assert.ok(performance.querySelectorAll('.op-yes').length===3);
  }finally{f.close();}
});

test('plan comparison ends the page without instructional legend, admin requests card or billing disclaimer',()=>{
 for(const isAdmin of [false,true]){
  const f=fixture({}, {isAdmin});
  try{
   const page=f.root.querySelector('.op-page');
   assert.ok(page);
   assert.ok(page.lastElementChild.classList.contains('op-compare'));
   for(const selector of ['.op-matrix-footnote','.op-admin-trials','.op-legal-foot'])
    assert.equal(f.root.querySelector(selector),null,selector);
   assert.doesNotMatch(f.root.textContent,/Toque em uma categoria para recolher ou expandir|Solicitações de teste grátis|Preços mensais por empresa/);
   assert.ok(f.root.querySelector('.op-free-trial'), 'trial application remains available');
   assert.ok(f.root.querySelector('.op-compare-switch'), 'comparison filter remains before the end');
  }finally{f.close();}
 }
});

test('trial modal reuses account/company, has consent, phone validation, no instant trial',async()=>{
 const f=fixture();
 try{
  f.root.querySelector('[data-op-trial]').click();
  const dlg=f.root.querySelector('.op-dialog');
  assert.ok(dlg&&dlg.open);
  assert.equal(dlg.querySelector('[name=full_name]').value,'Pessoa Sintética');
  assert.equal(dlg.querySelector('[name=company_name]').value,'Empresa Sintética');
  assert.equal(dlg.querySelector('[name=whatsapp]').placeholder,'(00) 00000-0000');
  assert.ok(dlg.querySelector('[data-op-privacy]'));
  assert.ok(dlg.querySelector('[name=privacy_ack]').required);
  dlg.querySelector('[name=whatsapp]').value='(67) 99999-1111';
  dlg.querySelector('[name=privacy_ack]').checked=true;
  dlg.querySelector('form').dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
  await tick();await tick();
  assert.equal(f.calls.filter(call=>call.path==='/plan-trial').length,1);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].data)),{
   full_name:'Pessoa Sintética',company_name:'Empresa Sintética',
   whatsapp:'+5567999991111',privacy_ack:true});
  assert.match(f.root.textContent,/Solicitação em análise/);
  assert.ok(f.root.querySelector('[data-op-trial]').disabled);
 }finally{f.close();}
});

test('current paid subscription does not offer a second purchase',()=>{
 const f=fixture({subscription:{
  id:'sub-test',code:'orion_profissional',name:'Orion Profissional',
  status:'active',monthly_cents:8990,max_professionals:6
 },active_professionals:2});
 try{
  const cards=f.root.querySelectorAll('[data-op-checkout]');
  assert.equal(cards.length,3);
  assert.ok([...cards].every(button=>button.disabled));
  assert.match(f.root.textContent,/Plano atual/);
  assert.match(f.root.textContent,/2 de 6/);
  assert.equal(f.root.querySelector('[data-op-trial]').disabled,true);
 }finally{f.close();}
});

test('clicking subscribe never redirects and checks one-time Pix server availability',async()=>{
 const f=fixture();
 try{
  f.root.querySelector('[data-op-checkout=profissional]').click();
  await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');
  assert.ok(dialog && dialog.open);
  assert.match(dialog.textContent,/Pagar 30 dias de acesso/);
  assert.match(dialog.textContent,/R\$\s*89,90/);
  assert.match(dialog.textContent,/temporariamente indisponível/);
  assert.equal(f.calls.filter(c=>c.path==='/plan-pix-config').length,1);
  assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  assert.equal(dialog.querySelector('[data-pix-form]').hidden,true);
  assert.doesNotMatch(code,/window\.location\.assign\s*\(/);
  dialog.querySelector('[data-pix-close]').click();
  assert.equal(f.root.querySelector('.op-checkout-dialog'),null);
 }finally{f.close();}
});

test('Pix QR is generated inside Orion only after owner explicitly consents',async()=>{
 const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
 try{
  f.root.querySelector('[data-op-checkout=essencial]').click();
  await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');
  assert.ok(dialog && dialog.open);
  const form=dialog.querySelector('[data-pix-form]');
  assert.equal(form.hidden,false);
  form.elements.namedItem('name').value='Pessoa Sintética';
  form.elements.namedItem('email').value='synthetic@example.test';
  form.elements.namedItem('phone').value='(67) 99999-1111';
  form.elements.namedItem('doc_number').value='52998224725';
  form.elements.namedItem('one_time').checked=true;
  form.elements.namedItem('understood').checked=true;
  form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
  await tick();await tick();await tick();
  const charges=f.calls.filter(c=>c.path==='/plan-pix-charge');
  assert.equal(charges.length,1);
  assert.equal(charges[0].data.plan_code,'essencial');
  assert.equal(charges[0].data.payer.doc_type,'cpf');
  assert.equal(charges[0].data.payer.doc_number,'52998224725');
  assert.equal(charges[0].data.consent_one_time,true);
  assert.ok(!('card_token' in charges[0].data));
  assert.equal(dialog.querySelector('[data-pix-result]').hidden,false);
  assert.equal(dialog.querySelector('[data-pix-code]').value,'00020126580014br.gov.bcb.pix.synthetic-example');
  assert.ok(dialog.querySelector('img.op-payment-qr').src.startsWith('data:image/png;base64,'));
  assert.equal(f.calls.filter(c=>c.path==='/plan-overview').length,0,
               'The client never grants access from an unpaid QR code.');
  dialog.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('one-time Pix checkout does not render cards, recurring methods or banking forms',async()=>{
 const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
 try{
  f.root.querySelector('[data-op-checkout=elite]').click();
  await tick();await tick();
  const dlg=f.root.querySelector('.op-checkout-dialog');
  assert.match(dlg.textContent,/Sem débito automático e sem cartão/);
  for(const name of ['card_number','cvv','expiry','holder','doc_type','zip','street','city'])
    assert.equal(dlg.querySelector('[name='+name+']'),null,name);
  assert.equal(dlg.querySelector('[data-op-payment-method=card]'),null);
  assert.doesNotMatch(code,/CaktoSDK|createToken\(|authenticate3DS\(/);
  assert.equal(f.calls.some(c=>c.path==='/plan-direct-charge'),false);
  dlg.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('CPF and CNPJ masks correctly identify payer without selecting document type',async()=>{
 for(const [raw,shown,kind] of [
  ['52998224725','529.982.247-25','cpf'],
  ['11444777000161','11.444.777/0001-61','cnpj']
 ]){
  const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
  try{
   f.root.querySelector('[data-op-checkout=essencial]').click();
   await tick();await tick();
   const dlg=f.root.querySelector('.op-checkout-dialog'),form=dlg.querySelector('form');
   const doc=form.elements.namedItem('doc_number');
   doc.value=raw;doc.dispatchEvent(new f.window.Event('input',{bubbles:true}));
   assert.equal(doc.value,shown);
   form.elements.namedItem('name').value='Pessoa Sintética';
   form.elements.namedItem('email').value='synthetic@example.test';
   form.elements.namedItem('phone').value='(67) 99999-1111';
   form.elements.namedItem('one_time').checked=true;
   form.elements.namedItem('understood').checked=true;
   form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
   for(let i=0;i<4;i++)await tick();
   const charge=f.calls.find(c=>c.path==='/plan-pix-charge');
   assert.ok(charge);
   assert.equal(charge.data.payer.doc_number,raw);
   assert.equal(charge.data.payer.doc_type,kind);
   dlg.querySelector('[data-pix-close]').click();
  }finally{f.close();}
 }
});

test('invalid CPF/CNPJ never creates Pix charge',async()=>{
 const f=fixture({}, {pixConfig:{available:true,methods:['pix'],mode:'manual_30_days'}});
 try{
  f.root.querySelector('[data-op-checkout=elite]').click();
  await tick();await tick();
  const dlg=f.root.querySelector('.op-checkout-dialog'),form=dlg.querySelector('form');
  form.elements.namedItem('name').value='Pessoa Sintética';
  form.elements.namedItem('email').value='synthetic@example.test';
  form.elements.namedItem('phone').value='(67) 99999-1111';
  form.elements.namedItem('doc_number').value='123456789012';
  form.elements.namedItem('one_time').checked=true;
  form.elements.namedItem('understood').checked=true;
  form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
  await tick();await tick();
  assert.equal(f.calls.filter(c=>c.path==='/plan-pix-charge').length,0);
  assert.equal(form.elements.namedItem('doc_number').validity.customError,true);
  dlg.querySelector('[data-pix-close]').click();
 }finally{f.close();}
});

test('current prepaid subscription can renew same plan manually but not switch tiers',()=>{
 const valid=planState('essencial',{active:true,source:'pix_prepaid',valid_until:'2027-01-01T00:00:00Z'});
 const f=fixture({effective_plan:valid,checkout_enabled:true});
 try{
  assert.match(f.root.querySelector('[data-op-effective-plan]').textContent,/Pix pré-pago/);
  assert.match(f.root.querySelector('.op-essencial [data-op-checkout]').textContent,/Renovar com Pix/);
  assert.equal(f.root.querySelector('.op-essencial [data-op-checkout]').disabled,false);
  assert.equal(f.root.querySelector('.op-profissional [data-op-checkout]').disabled,true);
  assert.equal(f.root.querySelector('.op-elite [data-op-checkout]').disabled,true);
  assert.equal(f.root.querySelector('[data-op-trial]').disabled,true);
 }finally{f.close();}
});

test('checkout layout respects Orion tokens and has no redundant address or selector',()=>{
 assert.match(css,/\.op-payment-top\{/);
 assert.match(css,/\.op-payment-method\.active\{/);
 assert.match(css,/\.op-payment-actions\{/);
 assert.match(css,/@media\(max-width:600px\)/);
 assert.match(css,/--os-primary/);
 assert.doesNotMatch(code,/Tipo de documento|Endereço do titular para autenticação com o banco/);
 assert.doesNotMatch(code,/checkoutField\('(zip|state|city|street|number|complement)'/);
 assert.doesNotMatch(css,/\.op-payment-document/);
});

test('no horizontal-scroll-prone three-column layout on mobile screens',()=>{
 const widths=[320,375,390,430,768,1024,1440];
 for(const width of widths){
  assert.ok(width>=320);
  assert.match(css,/max-width:767px/);
  assert.match(css,/grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css,/max-width:1199px/);
  assert.match(css,/max-width:350px/);
 }
});
