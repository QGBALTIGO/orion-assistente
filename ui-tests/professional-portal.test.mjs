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
 assert.ok(h.document.querySelector('[data-os-team-member]'));
});

async function portal(t){
 const errors=[],calls=[];
 const html=await readFile(new URL('../app/static/orion-professional.html',import.meta.url),'utf8');
 const js=await readFile(new URL('../app/static/orion-professional.js',import.meta.url),'utf8');
 const c=new VirtualConsole();c.on('jsdomError',e=>errors.push(String(e)));
 const dom=new JSDOM(html,{url:'https://orionagenda.pro/profissional',runScripts:'outside-only',virtualConsole:c});
 t.after(()=>dom.window.close());
 const w=dom.window;Object.defineProperty(w.crypto,'randomUUID',{value:randomUUID,configurable:true});
 w.fetch=async(path,opts={})=>{
   const method=opts.method||'GET';calls.push({path,method,body:opts.body&&JSON.parse(opts.body)});
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
   else if(path.includes('/api/v1/professional/appointments/'))data={id:'d1000000-0000-4000-8000-000000000001'};
   else {status=404;data={detail:'Not found'}}
   return {ok:status===200,status,json:async()=>data,headers:new Map()};
 };
 w.eval(js);
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
