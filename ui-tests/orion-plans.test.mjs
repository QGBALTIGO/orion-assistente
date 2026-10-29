import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {JSDOM} from 'jsdom';

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
 const calls=[],overview={...base,...overrides};
 const ctx={user:{name:'Pessoa Sintética'},tenant:{name:'Empresa Sintética'},canManage:true,isAdmin:!!options.isAdmin,
  request:async(path,method='GET',data)=>{calls.push({path,method,data});
   if(path==='/plan-trial'){overview.trial={status:'pending',started_at:null,ends_at:null};return{status:'pending',message:'Solicitação recebida!'};}
   if(path==='/plan-overview')return overview;
   if(path==='/plan-direct-config')return options.directConfig||{available:false,methods:[],sdk_client_id:null};
   if(path==='/plan-direct-prepare' && options.directConfig?.available)
    return {status:'ready',intent_id:'synthetic-intent-id',plan_code:data.plan_code,
            monthly_cents:overview.plans.find(plan=>plan.code===data.plan_code).monthly_cents};
   if(path==='/plan-direct-charge' && options.directConfig?.available)
    return {status:'pending_confirmation',intent_id:'synthetic-intent-id',
            pix_code:'00020126580014br.gov.bcb.pix.synthetic-example',
            pix_qr_data_uri:'data:image/png;base64,c3ludGhldGlj'};
   if(path.startsWith('/plan-direct-status?') && options.directConfig?.available)
    return {status:'pending'};
   throw Error('Caminho não permitido: '+path);
  }};
 const root=window.document.getElementById('host');
 window.OrionPlans.mount(root,overview,ctx);
 return {window,root,calls,ctx,close:()=>dom.window.close()};
}
function tick(){return new Promise(resolve=>setImmediate(resolve));}

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
  assert.equal(table.querySelectorAll('tbody:not([hidden])').length,9);
  assert.equal(mobile.querySelectorAll('.op-mobile-group:not([hidden])').length,9);
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

test('clicking subscribe never navigates away; integrated Orion modal is fail closed',async()=>{
 const f=fixture();
 try{
  f.root.querySelector('[data-op-checkout=profissional]').click();
  await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');
  assert.ok(dialog && dialog.open);
  assert.match(dialog.textContent,/Finalizar assinatura/);
  assert.match(dialog.textContent,/R\$\s*89,90/);
  assert.match(dialog.textContent,/temporariamente indisponível/);
  assert.equal(f.calls.filter(c=>c.path==='/plan-direct-config').length,1);
  assert.equal(f.calls.filter(c=>c.path==='/plan-direct-charge').length,0);
  assert.equal(f.calls.filter(c=>c.path==='/plan-checkout').length,0);
  assert.equal(f.root.querySelector('.op-account-state'),null);
  assert.doesNotMatch(dialog.textContent,/cakto/i);
  assert.doesNotMatch(code,/window\.location\.assign\s*\(/);
  dialog.querySelector('[data-op-checkout-close]').click();
  assert.equal(f.root.querySelector('.op-checkout-dialog'),null);
 }finally{f.close();}
});

test('Pix result stays in Orion with local QR and copy code, never activates without backend',async()=>{
 const f=fixture({}, {directConfig:{available:true,methods:['pix','card'],sdk_client_id:'synthetic-sdk-id'}});
 try{
  f.root.querySelector('[data-op-checkout=essencial]').click();
  await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');
  assert.ok(dialog && dialog.open);
  assert.equal(dialog.querySelector('[data-op-payment-body]').hidden,false);
  const form=dialog.querySelector('[data-op-payment-form]');
  form.elements.namedItem('name').value='Pessoa Sintética';
  form.elements.namedItem('email').value='synthetic@example.test';
  form.elements.namedItem('phone').value='(67) 99999-1111';
  form.elements.namedItem('doc_number').value='52998224725';
  form.elements.namedItem('monthly_ack').checked=true;
  form.elements.namedItem('processor_ack').checked=true;
  form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
  await tick();await tick();await tick();
  const prepared=f.calls.filter(c=>c.path==='/plan-direct-prepare');
  const charged=f.calls.filter(c=>c.path==='/plan-direct-charge');
  assert.equal(prepared.length,1);
  assert.equal(charged.length,1);
  assert.equal(charged[0].data.method,'pix');
  assert.equal(charged[0].data.payer.doc_type,'cpf');
  assert.equal(charged[0].data.payer.doc_number,'52998224725');
  assert.equal(charged[0].data.card_token,null);
  assert.deepEqual(JSON.parse(JSON.stringify(charged[0].data.three_ds)),null);
  assert.equal(dialog.querySelector('[data-op-payment-result]').hidden,false);
  assert.equal(dialog.querySelector('[data-op-pix-code]').value,'00020126580014br.gov.bcb.pix.synthetic-example');
  assert.ok(dialog.querySelector('img.op-payment-qr').getAttribute('src').startsWith('data:image/png;base64,'));
  assert.equal(f.root.querySelector('.op-account-state'),null);
  dialog.querySelector('[data-op-checkout-close]').click();
  assert.equal(f.root.querySelector('.op-checkout-dialog'),null);
 }finally{f.close();}
});

test('card mode never sends raw PAN/CVV to application without tokenization',async()=>{
 const f=fixture({}, {directConfig:{available:true,methods:['pix','card'],sdk_client_id:'synthetic-sdk-id'}});
 try{
  f.root.querySelector('[data-op-checkout=elite]').click();
  await tick();await tick();
  const dialog=f.root.querySelector('.op-checkout-dialog');
  dialog.querySelector('[data-op-payment-method=card]').click();
  assert.equal(dialog.querySelector('[data-op-card-fields]').hidden,false);
  assert.equal(dialog.querySelector('[name=doc_type]'),null);
  for(const field of ['zip','state','city','street','number','complement'])
   assert.equal(dialog.querySelector('[name='+field+']'),null,field);
  assert.ok(dialog.querySelector('[name=card_number]').required);
  assert.ok(dialog.querySelector('[name=cvv]').required);
  assert.ok(dialog.querySelector('[name=card_number]').disabled===false);
  assert.equal(f.calls.filter(c=>c.path==='/plan-direct-charge').length,0);
  dialog.querySelector('[data-op-checkout-close]').click();
 }finally{f.close();}
});

test('credit card SDK tokenizes in browser and Orion API receives only token, not PAN/CVV',async()=>{
 const f=fixture({}, {directConfig:{available:true,methods:['pix','card'],sdk_client_id:'synthetic-sdk-id'}});
 let tokenized=false,challenged=false;
 try{
  f.window.Cakto={CaktoSDK:class{
   constructor(settings){assert.equal(settings.client_id,'synthetic-sdk-id');}
   async initAntifraud(){}
   async createToken(card){tokenized=card.cardNumber==='4111111111111111'&&card.cvv==='123';return{cardToken:'synthetic-card-token-01234567890'};}
   async authenticate3DS(data){challenged=data.customer.amount===8990&&!('address' in data.customer);return{success:true,cavv:'synthetic-cavv',eci:'05',referenceId:'synthetic-ref',version:'2.2.0'};}
   async completeAntifraudProfile(){}
   getAntifraudReference(){return 'synthetic-antifraud-reference';}
  }};
  f.root.querySelector('[data-op-checkout=profissional]').click();
  await tick();await tick();
  const dlg=f.root.querySelector('.op-checkout-dialog');
  dlg.querySelector('[data-op-payment-method=card]').click();
  const form=dlg.querySelector('form');
  const fill=(key,value)=>{form.elements.namedItem(key).value=value;};
  fill('name','Pessoa Sintética');fill('email','synthetic@example.test');
  fill('phone','(67) 99999-1111');fill('doc_number','52998224725');
  fill('holder','Pessoa Sintética');fill('card_number','4111111111111111');
  fill('expiry','12/30');fill('cvv','123');
  
  form.elements.namedItem('monthly_ack').checked=true;
  form.elements.namedItem('processor_ack').checked=true;
  form.dispatchEvent(new f.window.Event('submit',{bubbles:true,cancelable:true}));
  for(let i=0;i<6;i++)await tick();
  assert.equal(tokenized,true);
  assert.equal(challenged,true);
  const charge=f.calls.find(c=>c.path==='/plan-direct-charge');
  assert.ok(charge,'synthetic card token charge created');
  assert.equal(charge.data.method,'card');
  assert.equal(charge.data.card_token,'synthetic-card-token-01234567890');
  assert.equal(charge.data.three_ds.eci,'05');
  assert.equal(Object.prototype.hasOwnProperty.call(charge.data.three_ds,'xid'),false);
  assert.equal(charge.data.antifraud_reference,'synthetic-antifraud-reference');
  assert.equal(form.elements.namedItem('card_number').value,'');
  assert.equal(form.elements.namedItem('cvv').value,'');
  assert.doesNotMatch(JSON.stringify(charge.data),/4111111111111111|(?:\\b|")cvv(?:\\b|")/i);
  dlg.querySelector('[data-op-checkout-close]').click();
 }finally{f.close();}
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
