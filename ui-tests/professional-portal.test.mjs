import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM,VirtualConsole} from 'jsdom';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const pro='c1000000-0000-4000-8000-000000000001';
const other='c1000000-0000-4000-8000-000000000002';
const person=(id,name)=>({id,name,specialty:'Barbeiro',active:true,commission_percent:10,
 commission_mode:'percent',commission_config_version:1,color:'#31ad52',accepts_appointments:true});

test('owner sets email in the existing professional drawer and sends a bound invitation',async t=>{
 const calls=[];
 const h=await makeSuite({initialRoute:'funcionarios',respond:(path,method,data)=>{
   calls.push({path,method,data});
   if(path.endsWith('/professionals')&&method==='GET')return [person(pro,'Guilherme'),person(other,'Peixoto')];
   if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
   if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[],professional_access:[]};
   if(path.includes('/management/professionals/')&&method==='PUT')return {id:pro};
   if(path.endsWith('/team/professionals/'+pro+'/invite')&&method==='POST')
     return {role:'professional',professional_id:pro,link:'https://orionagenda.pro/convite#token='+'Z'.repeat(43),
       email_delivery:{status:'manual'},expires_at:'2030-01-01T12:00:00Z'};
   return defaultResponse(path);
 }});
 t.after(()=>h.close());
 await flush();
 h.click('[data-os-professional="'+pro+'"]');await flush();
 const form=h.document.querySelector('[data-os-form=professional]');
 assert.ok(form);
 assert.equal(form.elements.access_email.type,'email');
 form.elements.access_email.value='guilherme@example.invalid';
 form.elements.send_invitation.checked=true;
 h.submit('[data-os-form=professional]');await flush();await flush();
 const sent=calls.find(c=>c.path.endsWith('/team/professionals/'+pro+'/invite')&&c.method==='POST');
 assert.ok(sent,'invitation goes to the selected professional, not generic team endpoint');
 assert.equal(sent.data.email,'guilherme@example.invalid');
 assert.equal(calls.filter(c=>c.path.endsWith('/management/team/invitations')).length,0);
 assert.match(h.document.getElementById('osDialog').textContent,/Copie o link agora/);
 assert.equal(h.errors.length,0);
});

test('legacy company-editor can edit operational professional, but cannot send invites',async t=>{
 const h=await makeSuite({initialRoute:'funcionarios',role:'editor',respond:(path,method)=>{
   if(path.endsWith('/professionals')&&method==='GET')return [person(pro,'Guilherme')];
   if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
   if(path.endsWith('/management/team'))return {can_manage:false,members:[],invitations:[]};
   return defaultResponse(path);
 }});
 t.after(()=>h.close());await flush();
 h.click('[data-os-professional="'+pro+'"]');await flush();
 const form=h.document.querySelector('[data-os-form=professional]');
 assert.ok(form);
 assert.equal(form.elements.access_email,undefined);
 assert.equal(form.elements.send_invitation,undefined);
 assert.equal(h.errors.length,0);
});

test('team view only offers invites inside registered professionals and retains legacy member controls',async t=>{
 const h=await makeSuite({initialRoute:'funcionarios',respond:path=>{
   if(path.endsWith('/professionals'))return [person(pro,'Guilherme')];
   if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
   if(path.endsWith('/management/team'))return {can_manage:true,professional_access:[
      {professional_id:pro,email:'guilherme@example.invalid',active:true,account_id:'a1000000-0000-4000-8000-000000000001',
       updated_at:'2026-11-02T12:00:00Z'}],
     invitations:[],members:[{role:'viewer',account_id:'a1000000-0000-4000-8000-000000000003',
       email:'leitor@example.invalid',name:'Acesso antigo',active:true}]};
   return defaultResponse(path);
 }});
 t.after(()=>h.close());await flush();
 h.click('[data-os-staff-tab=permissions]');await flush();
 assert.match(h.document.getElementById('osPageBody').textContent,/Acesso ativo/);
 assert.match(h.document.getElementById('osPageBody').textContent,/Acessos anteriores/);
 assert.equal(h.document.querySelector('[data-os-action=new-invitation]'),null);
 assert.ok(h.document.querySelector('[data-os-action=pro-access-suspend]'));
 assert.ok(h.document.querySelector('[data-os-action=pro-block-permission]'));
 assert.ok(h.document.querySelector('[data-os-team-member]'));
});

async function portal(t,{respond,enableDental=false}={}){
 const errors=[],calls=[];
 const html=await readFile(new URL('../app/static/orion-professional.html',import.meta.url),'utf8');
 const js=await readFile(new URL('../app/static/orion-professional.js',import.meta.url),'utf8');
 const shared=await readFile(new URL('../app/static/orion-fields.js',import.meta.url),'utf8');
 const dentalJs=enableDental?await readFile(new URL('../app/static/orion-dental-clinic.js',import.meta.url),'utf8'):null;
 const c=new VirtualConsole();c.on('jsdomError',e=>errors.push(String(e)));
 const dom=new JSDOM(html,{url:'https://orionagenda.pro/profissional',runScripts:'outside-only',virtualConsole:c});
 t.after(()=>dom.window.close());
 const w=dom.window;Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID,configurable:true});
 w.fetch=async(path,opts={})=>{
   const method=opts.method||'GET',request={path,method,body:opts.body&&JSON.parse(opts.body)};calls.push(request);
   const override=await respond?.(request);if(override!==undefined)return override;
   let data,status=200;
   if(path==='/auth/me')data={authenticated:true,role:'member',csrf:'signed-csrf'};
   else if(path==='/api/v1/professional/me')data={id:pro,name:'Guilherme',business_name:'Barbearia Peixoto',role:'professional',
       timezone:'America/Campo_Grande'};
   else if(path==='/api/v1/professional/dashboard')data={profile:{name:'Guilherme'},date:'2026-11-02',
        today:{confirmed:1,completed:0,cancelled:0,revenue_cents:0,commission_cents:0},goals:[]};
   else if(path.includes('/api/v1/professional/agenda'))data={date:'2026-11-02',blocks:[],
        appointments:[{id:'d1000000-0000-4000-8000-000000000001',customer_name:'Meu cliente',
        start_at:'2026-11-02T18:00:00Z',end_at:'2026-11-02T18:30:00Z',updated_at:'2026-11-02T10:00:00Z',
        status:'confirmed',services:[{name:'Corte'}],total_cents:4000}]};
   else if(path.includes('/api/v1/professional/finance'))data={professional:'Guilherme',period:{},summary:{revenue_cents:20000,
       earned_cents:5000,balance_cents:3000,advances_cents:0,payments_cents:2000,payable_cents:3000}};
   else if(path==='/api/v1/professional/goals')data={items:[]};
   else if(path==='/api/v1/professional/dental/context')data=enableDental?
     {enabled:true,tenant_id:'10000000-0000-4000-8000-000000000001'}:{enabled:false};
   else if(enableDental&&path.endsWith('/dental/access'))data={
     can_manage:false,can_read_records:true,grants:[],
     clinical_access:{professional_id:pro,level:'dentist'}};
   else if(enableDental&&path.includes('/dental/patients?'))data={items:[],total:0};
   else if(path==='/api/v1/professional/services')data={items:[
     {id:other,name:'Corte',duration_min:40,price_cents:4000,price_mode:'fixed'}]};
   else if(path.startsWith('/api/v1/professional/availability?'))data={
      slots:['2026-11-02T17:00:00-04:00'],duration_min:40,price_cents:4000,
      timezone:'America/Campo_Grande'};
   else if(path==='/api/v1/professional/appointments'&&method==='POST')
       data={id:'d1000000-0000-4000-8000-000000000004',status:'confirmed'};
   else if(path.includes('/api/v1/professional/appointments/'))data={id:'d1000000-0000-4000-8000-000000000001'};
   else {status=404;data={detail:'Not found'}}
   return {ok:status===200,status,json:async()=>data,headers:new Map()};
 };
 w.eval(shared);if(dentalJs)w.eval(dentalJs);w.eval(js);
 for(let i=0;i<10;i++)await new Promise(r=>setTimeout(r,12));
 return {w,doc:w.document,calls,errors};
}

test('individual portal uses dedicated scoped routes and does not expose global company data',async t=>{
 const h=await portal(t);
 assert.equal(h.doc.getElementById('portal').hidden,false);
 assert.match(h.doc.getElementById('content').textContent,/Guilherme/);
 h.doc.querySelector('[data-page=financeiro]').click();
 for(let i=0;i<8;i++)await new Promise(r=>setTimeout(r,10));
 assert.match(h.doc.getElementById('content').textContent,/R\$\s*50,00/);
 assert.ok(h.calls.every(c=>c.path==='/auth/me'||c.path.startsWith('/api/v1/professional/')));
 assert.equal(h.errors.length,0);
});

for(const messagesOptIn of [false,true])test(`professional books with optional appointment messages ${messagesOptIn}`,async t=>{
  let attempts=0;
  const h=await portal(t,{respond:request=>{
    if(messagesOptIn&&request.path==='/api/v1/professional/appointments'&&request.method==='POST'&&++attempts===1)
      throw new Error('Resposta sintética interrompida.');
  }});
  h.doc.querySelector('[data-page=agenda]').click();
  await new Promise(resolve=>setTimeout(resolve,45));
  h.doc.querySelector('[data-action=new-appointment]').click();
  await new Promise(resolve=>setTimeout(resolve,45));
  const form=h.doc.getElementById('modalForm');
  assert.ok(form);
  assert.ok(form.querySelector('[name=customer_name]'),form.innerHTML);
  assert.ok(form.querySelector('[name=customer_phone]'));
  const messages=form.querySelector('[name=appointment_messages_opt_in]');
  assert.equal(messages.checked,false);
  assert.equal(messages.required,false);
  assert.equal(form.querySelectorAll('[name=service_ids]').length,1);
  form.querySelector('[name=customer_name]').value='Cliente sintético novo';
  form.querySelector('[name=customer_phone]').value='67999999999';
  form.querySelector('[name=service_ids]').checked=true;
  messages.checked=messagesOptIn;
  form.dispatchEvent(new h.w.Event('submit',{cancelable:true,bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,50));
  assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/v1/professional/appointments').length,0);
  const slot=form.querySelector('[name=selected_slot]');
  assert.ok(slot);
  slot.checked=true;
  form.dispatchEvent(new h.w.Event('submit',{cancelable:true,bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,40));
  assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path==='/api/v1/professional/appointments').length,0);
  assert.match(h.doc.getElementById('modalReview').textContent,/Revise antes de confirmar/);
  form.dispatchEvent(new h.w.Event('submit',{cancelable:true,bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,75));
  if(messagesOptIn){
    assert.match(h.doc.getElementById('modalError').textContent,/interrompida/);
    assert.equal(messages.disabled,true);
    messages.checked=false;
    form.querySelector('[name=customer_name]').value='Outra pessoa';
    form.dispatchEvent(new h.w.Event('submit',{cancelable:true,bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,75));
  }
  const posted=h.calls.filter(c=>c.method==='POST'&&c.path==='/api/v1/professional/appointments');
  assert.equal(posted.length,messagesOptIn?2:1);
  assert.deepEqual(posted[0].body.service_ids,[other]);
  assert.equal(posted[0].body.customer_phone,'+5567999999999');
  assert.equal(posted[0].body.appointment_messages_opt_in,messagesOptIn);
  assert.equal(posted[0].body.marketing_opt_in,undefined);
  assert.equal(posted[0].body.professional_id,undefined);
  if(messagesOptIn)assert.deepEqual(posted[0].body,posted[1].body);
  assert.equal(h.errors.length,0);
  h.doc.querySelector('[data-action=new-appointment]').click();
  await new Promise(resolve=>setTimeout(resolve,45));
  assert.equal(h.doc.querySelector('[name=appointment_messages_opt_in]').checked,false);
});


test('owner explicitly grants removal of company-created blocks to a linked professional',async t=>{
 const pro='c1000000-0000-4000-8000-000000000001';
 const h=await makeSuite({initialRoute:'funcionarios',respond:(path,method)=>{
   if(path.endsWith('/professionals')&&method==='GET')return [{id:pro,name:'Guilherme',specialty:'Barbeiro',active:true}];
   if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
   if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[],
     professional_access:[{professional_id:pro,account_id:'a1000000-0000-4000-8000-000000000001',
       email:'guilherme@example.invalid',active:true,can_manage_owner_blocks:false,
       updated_at:'2026-11-02T12:00:00Z'}]};
   if(method==='PATCH'&&path.endsWith('/block-permissions'))return {can_manage_owner_blocks:true};
   return defaultResponse(path);
 }});
 t.after(()=>h.close());await flush();
 h.click('[data-os-staff-tab=permissions]');await flush();
 h.click('[data-os-action=pro-block-permission]');await flush();
 assert.equal(h.calls.some(x=>x.method==='PATCH'),false,'no permission is granted without confirmation');
 h.submit('[data-os-form=confirm]');await flush();
 const posted=h.calls.find(x=>x.method==='PATCH'&&x.path.endsWith('/team/professionals/'+pro+'/block-permissions'));
 assert.ok(posted);
 assert.equal(posted.data.can_manage_owner_blocks,true);
 assert.equal(posted.data.expected_updated_at,'2026-11-02T12:00:00Z');
});


test('authorized dentist sees only their clinical tab with verified tenant context',async t=>{
 const h=await portal(t,{enableDental:true});
 const nav=h.doc.querySelector('.navigation [data-page=clinica]');
 assert.equal(nav.hidden,false);
 assert.equal(h.doc.querySelector('.mobile-nav [data-page=clinica]').hidden,false);
 assert.equal(h.doc.querySelector('.mobile-nav').dataset.dentalVisible,'true');
 nav.click();await new Promise(resolve=>setTimeout(resolve,100));
 assert.match(h.doc.getElementById('content').textContent,/Clínica odontológica/);
 assert.match(h.doc.getElementById('content').textContent,/Pacientes/);
 assert.ok(h.calls.some(x=>x.path.endsWith('/dental/access')));
 assert.ok(h.calls.some(x=>x.path.includes('/dental/patients?')));
 assert.ok(!h.calls.some(x=>x.method!=='GET'&&x.path.includes('/dental/')));
 h.doc.querySelector('[data-page=agenda]').click();
 await new Promise(resolve=>setTimeout(resolve,55));
 assert.equal(h.doc.querySelector('#professionalDentalWorkspace'),null);
 assert.equal(h.errors.length,0);
});

test('normal professional cannot see a dental tab or fetch patient data',async t=>{
 const h=await portal(t);
 assert.equal(h.doc.querySelector('.navigation [data-page=clinica]').hidden,true);
 assert.equal(h.doc.querySelector('.mobile-nav [data-page=clinica]').hidden,true);
 assert.equal(h.calls.some(x=>x.path.includes('/dental/patients')),false);
 h.doc.querySelector('.navigation [data-page=clinica]').click();
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(h.doc.querySelector('#professionalDentalWorkspace'),null);
 assert.equal(h.errors.length,0);
});
