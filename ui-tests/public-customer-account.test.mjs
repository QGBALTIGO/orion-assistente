import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {flush} from "./harness.mjs";
import {makePublic, response} from "./public-harness.mjs";

const ACCOUNT="/api/v1/public/sites/empresa-sintetica/account";
const profile={name:"Cliente sintético",whatsapp_e164:"+5511987654321",
  email:"cliente@example.test",instagram:"@cliente",birth_date:"1999-10-30",
  city:"São Paulo",neighborhood:"Centro",version:4};
const future={id:"30000000-0000-4000-8000-000000000003",protocol:"AG-TEST1A",
  status:"confirmed",start_at:"2030-01-07T09:00:00-03:00",
  end_at:"2030-01-07T09:40:00-03:00",updated_at:"2029-12-30T15:00:00Z",
  professional:"Guilherme",services:["Corte","Barba"],duration_min:40,
  price_cents:6500,price_mode:"fixed",can_change:true};
const history={...future,id:"30000000-0000-4000-8000-000000000004",
  status:"completed",protocol:"AG-TEST2B",start_at:"2029-10-01T09:00:00-03:00",
  can_change:false};

async function portal({authenticated=false,available=true,path,custom}={}){
  const calls=[];
  const h=await makePublic({path,respond:(url,opt={})=>{
    if(!url.startsWith(ACCOUNT))return undefined;
    const method=opt.method||"GET",payload=opt.body?JSON.parse(opt.body):null;
    calls.push({url,method,payload});
    if(custom){const responseValue=custom(url,method,payload);if(responseValue)return responseValue;}
    if(url===ACCOUNT)return response({available,authenticated,profile:authenticated?profile:undefined});
    if(url.includes("/request-code"))return response({message:"Se houver cadastro, enviaremos um código."},202);
    if(url.includes("/verify-code"))return response({authenticated:true,profile});
    if(url.includes("/appointments?kind=upcoming"))return response({items:[future],has_more:false,next_offset:1,timezone:"America/Sao_Paulo"});
    if(url.includes("/appointments?kind=history"))return response({items:[history],has_more:false,next_offset:1,timezone:"America/Sao_Paulo"});
    if(url.includes("/slots?"))return response({slots:["2030-01-09T10:00:00-03:00"],timezone:"America/Sao_Paulo"});
    if(url.includes("/reschedule")||url.includes("/cancel"))return response({item:future});
    if(url.endsWith("/profile"))return response({profile:{...profile,...payload,version:profile.version+1}});
    if(url.includes("/phone/request-code"))return response({message:"SMS enviado"},202);
    if(url.includes("/phone/confirm"))return response({profile:{...profile,whatsapp_e164:payload.phone,version:5}});
    if(url.includes("/logout"))return response({authenticated:false});
    return response({});
  }});
  h.window.eval(await readFile(new URL("../app/static/orion-customer-account.js",import.meta.url),"utf8"));
  await flush();
  return {...h,accountCalls:calls};
}

test("account icon shares the existing theme toolbar and is not enabled in admin preview",async t=>{
  const h=await portal();t.after(()=>h.close());
  const button=h.document.querySelector("#customer-account-trigger");
  const theme=h.document.querySelector("#public-theme-toggle");
  assert.equal(theme.nextElementSibling,button);
  assert.match(button.getAttribute("aria-label"),/minha conta/i);
  assert.equal(button.querySelectorAll("svg").length,1);
  assert.equal(button.hidden,false);
  assert.equal(h.document.querySelectorAll("#customer-account-tabs button").length,3);
  assert.equal(h.accountCalls.length,0,"no private calls on page load");
  const v=await portal({path:"/app/site-preview/30000000-0000-4000-8000-000000000003"});
  t.after(()=>v.close());
  assert.equal(v.document.querySelector("#customer-account-trigger").hidden,true);
});

test("visitor sees polished phone access but no private bookings before code approval",async t=>{
  const h=await portal();t.after(()=>h.close());
  h.click("#customer-account-trigger");await flush();
  assert.equal(h.document.querySelector("#customer-account-dialog").open,true);
  assert.equal(h.document.querySelector("#customer-account-tabs").hidden,true);
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Seus horários, em um só lugar/);
  assert.equal(h.accountCalls.length,1);
  assert.equal(h.accountCalls[0].url,ACCOUNT);
  assert.equal(h.accountCalls.some(c=>c.url.includes("/appointments")),false);
  h.fill("#customer-account-body [name=phone]","(11) 98765-4321");
  h.document.querySelector(".customer-account-auth-form").dispatchEvent(
    new h.window.Event("submit",{bubbles:true,cancelable:true}));
  await flush();
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Confirme seu telefone/);
  assert.ok(h.accountCalls.some(c=>c.url.endsWith("/request-code")&&c.payload.phone==="+5511987654321"));
  assert.equal(h.accountCalls.some(c=>c.url.includes("/appointments")),false);
  h.fill("#customer-account-body [name=otp]","123456");
  h.document.querySelector(".customer-account-auth-form").dispatchEvent(
    new h.window.Event("submit",{bubbles:true,cancelable:true}));
  await flush();
  assert.ok(h.accountCalls.some(c=>c.url.endsWith("/verify-code")&&c.payload.code==="123456"));
  assert.equal(h.document.querySelector("#customer-account-tabs").hidden,false);
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Corte \+ Barba/);
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Guilherme/);
  assert.equal(h.errors.length,0);
});

test("3 tabs show history and profile, never write staff-only records or innerHTML from names",async t=>{
  const malicious={...profile,name:"<img src=x onerror=alert(1)>"};
  const h=await portal({authenticated:true,custom:(url)=>{
    if(url===ACCOUNT)return response({available:true,authenticated:true,profile:malicious});
  }});t.after(()=>h.close());
  h.click("#customer-account-trigger");await flush();
  assert.equal(h.document.querySelector("#customer-account-tabs").hidden,false);
  assert.match(h.document.querySelector("#customer-account-body").textContent,/img src=x/);
  assert.equal(h.document.querySelector("#customer-account-body img"),null);
  h.click('[data-customer-tab="history"]');await flush();
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Histórico de atendimentos/);
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Concluído/);
  h.click('[data-customer-tab="profile"]');await flush();
  assert.ok(h.document.querySelector('[name=instagram]'));
  assert.ok(h.document.querySelector('[name=birth_date]'));
  assert.equal(h.document.querySelector('[name=notes]'),null);
  assert.equal(h.document.querySelector('[name=customer_id]'),null);
  assert.ok(h.document.querySelector('.customer-account-phone-row strong'));
  assert.equal(h.errors.length,0);
});

test("profile edit writes only changed allowed fields and includes optimistic version",async t=>{
  const h=await portal({authenticated:true});t.after(()=>h.close());
  h.click("#customer-account-trigger");await flush();
  h.click('[data-customer-tab="profile"]');await flush();
  h.fill('[name=instagram]',"@novousuario");
  h.click(".customer-account-profile-footer .primary");await flush();
  const patch=h.accountCalls.find(c=>c.url.endsWith("/profile")&&c.method==="PATCH");
  assert.ok(patch);
  assert.equal(patch.payload.expected_version,4);
  assert.equal(patch.payload.instagram,"@novousuario");
  for(const key of ["whatsapp_e164","customer_id","notes","tenant_id","active"])
    assert.equal(key in patch.payload,false,key);
  assert.match(h.document.querySelector("#customer-account-feedback").textContent,/atualizados/);
  assert.equal(h.errors.length,0);
});

test("managing an active booking stays inside account and confirms cancellation",async t=>{
  const h=await portal({authenticated:true});t.after(()=>h.close());
  h.click("#customer-account-trigger");await flush();
  h.click("#customer-account-body .customer-account-booking-footer button");await flush();
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Gerenciar atendimento/);
  h.click("#customer-account-body .customer-account-actions .outline");await flush();
  assert.match(h.document.querySelector("#customer-account-body").textContent,/Cancelar este agendamento/);
  assert.equal(h.accountCalls.some(c=>c.method==="POST"&&c.url.includes("/cancel")),false);
  h.click("#customer-account-body .customer-account-danger .primary");await flush();
  const action=h.accountCalls.find(c=>c.url.endsWith("/cancel"));
  assert.ok(action);
  assert.equal(action.payload.expected_updated_at,future.updated_at);
  assert.match(action.payload.idempotency_key,/^[0-9a-f-]{36}$/i);
  assert.equal(h.errors.length,0);
});

test("unconfigured SMS shows an honest unavailable state, never leaks history",async t=>{
  const h=await portal({available:false});t.after(()=>h.close());
  h.click("#customer-account-trigger");await flush();
  assert.match(h.document.querySelector("#customer-account-body").textContent,/em preparação/);
  assert.equal(h.document.querySelector("#customer-account-body [name=phone]"),null);
  assert.equal(h.accountCalls.filter(c=>c.url.includes("appointments")).length,0);
});
