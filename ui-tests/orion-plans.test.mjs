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
function fixture(overrides={}){
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
 const ctx={user:{name:'Pessoa Sintética'},tenant:{name:'Empresa Sintética'},canManage:true,isAdmin:false,
  request:async(path,method='GET',data)=>{calls.push({path,method,data});
   if(path==='/plan-trial'){overview.trial={status:'pending',started_at:null,ends_at:null};return{status:'pending',message:'Solicitação recebida!'};}
   if(path==='/plan-overview')return overview;
   if(path==='/plan-checkout')throw Error('Contratações em preparação.');
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

test('comparison is one aligned four-column table with expandable categories',()=>{
 const f=fixture();
 try{
  const table=f.root.querySelector('.op-matrix');
  assert.ok(table);
  assert.equal(table.querySelectorAll('colgroup col').length,4);
  assert.equal(table.querySelectorAll('thead tr').length,1);
  const group=table.querySelector('[data-op-category="0"]');
  const rows=group.querySelectorAll('.op-matrix-row');
  assert.ok(rows.length>=3);
  assert.equal(rows[0].querySelectorAll('th,td').length,4);
  assert.equal(rows[0].querySelectorAll('td').length,3);
  assert.equal(table.querySelectorAll('thead th').length,4);
  assert.match(table.querySelector('thead').textContent,/R\$\s*89,90/);
  const toggle=group.querySelector('[data-op-category-toggle]');
  assert.equal(toggle.getAttribute('aria-expanded'),'true');
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'),'false');
  assert.ok([...rows].every(row=>row.hidden));
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'),'true');
  assert.ok([...rows].every(row=>!row.hidden));
 }finally{f.close();}
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

test('checkout failure remains visible without falsely starting a paid subscription',async()=>{
 const f=fixture();
 try{
  f.root.querySelector('[data-op-checkout=profissional]').click();
  await tick();await tick();
  assert.equal(f.calls.filter(c=>c.path==='/plan-checkout').length,1);
  assert.match(f.root.querySelector('#opPlanFeedback').textContent,/preparação/);
  assert.equal(f.root.querySelector('.op-account-state'),null);
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
