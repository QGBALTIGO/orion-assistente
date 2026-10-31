import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM} from 'jsdom';

const source = readFileSync(
  new URL('../app/static/orion-appointment-communications.js', import.meta.url),'utf8'
);
const tenant='26694b4b-be15-4850-bcb9-69b184897e79';
const staff='fb09446d-5173-47f4-aaf5-0fc0205d25ee';
const prefix='/api/v1/tenants/'+tenant+'/appointment-communications';
const past=new Date(Date.now()-5*60*1000).toISOString();

function config(canCheck=true){
 return {
  available:true,central_sender:true,channel_connected:true,timezone:'America/Sao_Paulo',
  can_check_professional_recipient:canCheck,
  settings:{enabled:true,version:1,signature:'Orion Agenda',review_delay_min:15,
    professional_notice:true,confirmation:true,cancellation:true,
    rescheduling:true,reminder:true,review:true},
  return_services:[],reviews:[],transport_limit:'WhatsApp central.',
  notifications:[
   {id:'one',event:'professional_new',recipient_kind:'professional',
    recipient_id:staff,recipient_label:'Profissional teste',protocol:'AG-ABCDEFG',
    status:'accepted',attempted_at:past,due_at:past},
   {id:'two',event:'professional_new',recipient_kind:'professional',
    recipient_id:staff,recipient_label:'Profissional teste',protocol:'AG-BCDEFGH',
    status:'accepted',attempted_at:past,due_at:past},
   {id:'three',event:'reminder',recipient_kind:'customer',
    recipient_id:'other',recipient_label:'Cliente',protocol:'AG-BCDEFGH',
    status:'delivered',attempted_at:past,due_at:past}
  ]
 };
}
async function fixture(canCheck=true){
 const dom=new JSDOM('<!doctype html><html><body><main id="host"></main></body></html>',
  {url:'https://orionagenda.pro/painel',runScripts:'outside-only'});
 dom.window.eval(source);
 const calls=[],host=dom.window.document.querySelector('#host');
 const req=async(path,method='GET')=>{
  calls.push({path,method});
  if(path===prefix&&method==='GET')return config(canCheck);
  if(path===prefix+'/professional-recipient/'+staff&&method==='GET')
    return {status:'not_recognized'};
  throw Error('No network is allowed: '+path);
 };
 await dom.window.OrionAppointmentCommunications.mount({
  host,request:req,tenantId:tenant,canWrite:true,canFeature:()=>false
 });
 return {dom,host,calls};
}
const flush=()=>new Promise(r=>setImmediate(r));

test('accepted is never labelled delivered; old accepted staff message shows warning',async()=>{
 const x=await fixture();
 try {
  assert.match(x.host.textContent,/Aceito; entrega não confirmada/);
  assert.match(x.host.textContent,/Sem recibo de entrega/);
  assert.match(x.host.textContent,/Entregue/);
  assert.equal(x.calls.length,1,'opening UI performs only the configuration GET');
 }finally{x.dom.window.close();}
});

test('one verified lookup action for a professional with repeated notifications',async()=>{
 const x=await fixture();
 try{
  const buttons=[...x.host.querySelectorAll('button')]
    .filter(b=>b.textContent==='Verificar WhatsApp');
  assert.equal(buttons.length,1);
  buttons[0].click();await flush();
  assert.equal(x.calls.filter(c=>c.path.includes('/professional-recipient/')).length,1);
  assert.ok(x.calls.every(c=>c.method==='GET'),'read-only: no resends or changes');
  assert.match(x.host.textContent,/WhatsApp não localizou o número cadastrado/);
  assert.doesNotMatch(x.host.textContent,/\+5511\d{9}/);
 }finally{x.dom.window.close();}
});

test('read-only viewer has no provider lookup button',async()=>{
 const x=await fixture(false);
 try{
  assert.equal([...x.host.querySelectorAll('button')]
   .filter(b=>b.textContent==='Verificar WhatsApp').length,0);
  assert.equal(x.calls.length,1);
 }finally{x.dom.window.close();}
});
