import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)][0]?.[1];
assert.ok(script,"O painel deve incluir seu controlador JavaScript");
const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
const required=["disconnectedWizard","wizardChoose","wizardTutorial","wizardStepCount",
  "wizardNext","wizardPrevious","wizardSuccess","wizardOpenCenter",
  "qrPanel","qrCountdown","qrImage","codePanel","pairForm","pairCode","copyCode",
  "retryCode","wizardRecover","connectedCenter"];
test("todos os elementos do wizard existem e cada ID é único",()=>{
  const found=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(found.filter((x,i)=>found.indexOf(x)!==i),[]);
  for(const id of required)assert.ok(ids.has(id),id+" ausente");
  assert.match(html,/prefers-reduced-motion/);
  assert.match(html,/wizard-scan/);
});

function harness(){
  const elements=new Map(),calls=[],copied=[];
  let connectionStatus="starting";
  class Element {
    constructor(id=""){this.id=id;this.style={};this.value=id==="resource"?"authorized_staff":"";
      this.dataset={};this.disabled=false;this.checked=false;this.textContent="";
      this.src="";this.listeners=new Map();this.attributes={};this.options=[];
      const states=new Set(["wizardTutorial","qrPanel","codePanel","wizardSuccess"]);
      this.classes=new Set(states.has(id)?["hidden"]:[]);
      this.classList={
        add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),
        contains:x=>this.classes.has(x),
        toggle:(x,force)=>{const add=force??!this.classes.has(x);
          add?this.classes.add(x):this.classes.delete(x);return add}
      };
    }
    addEventListener(event,fn){this.listeners.set(event,fn)}
    async fire(event="click"){
      const fn=this.listeners.get(event);
      if(!fn)throw Error("No listener for "+this.id+" "+event);
      return fn({preventDefault(){},target:this});
    }
    click(){return this.fire("click")}
    append(...args){this.children??=[];this.children.push(...args)}
    replaceChildren(...args){this.children=[...args]}
    setAttribute(name,v){this.attributes[name]=String(v)}
    getAttribute(name){return this.attributes[name]??null}
    removeAttribute(name){delete this.attributes[name]}
    focus(){}
    reset(){}
    add(option){this.options.push(option)}
    querySelector(sel){return new Element(sel)}
    querySelectorAll(){return []}
    get selectedOptions(){return this.options.filter(x=>x.selected)}
  }
  function byId(id){
    if(!elements.has(id))elements.set(id,new Element(id));
    return elements.get(id);
  }
  const tabs=["qr","code"].map(m=>{const e=new Element("method-"+m);e.dataset.method=m;return e});
  byId("settingsWeekdays").options=[0,1,2,3,4,5,6].map(value=>({value:String(value),selected:value>=1&&value<=5}));
  const document={
    getElementById:byId,
    querySelectorAll:query=>query==="[data-method]"?tabs:[],
    querySelector:()=>new Element("query"),
    createElement:tag=>new Element(tag)
  };
  async function fetch(url,request={}){
    calls.push({url,method:request.method||"GET",body:request.body?JSON.parse(request.body):null});
    let payload={},status=200;
    if(url==="/auth/me"){status=401;payload={detail:"not authenticated"}}
    else if(url==="/auth/login"){payload={csrf:"synthetic-csrf"}}
    else if(url==="/api/v1/qr/status"){
      payload={status:connectionStatus,phone:"5511999999999",ingestion:"receiving",
        messageStats:{available:false},featureSettings:{},featureCapabilities:{}};
    }else if(url==="/api/v1/qr/image"){
      payload={status:"qr_ready",image:"data:image/png;base64,QUJDRA==",expiresIn:44};
    }else if(url==="/api/v1/qr/pair"){
      payload={status:"pairing",code:"ABCD-EFGH",expiresIn:79};
    }else if(url==="/api/v1/tenants"){payload=[]}
    return {ok:status<400,status,json:async()=>payload};
  }
  const context={
    document,fetch,history:{replaceState(){}},
    location:{search:"",hostname:"localhost",protocol:"https:"},
    navigator:{clipboard:{writeText:async x=>copied.push(x)}},
    Option:class{constructor(label,value){this.label=label;this.value=value}},
    URLSearchParams,Date,Number,Intl,Math,Object,Array,String,RegExp,Set,Map,JSON,
    clearInterval(){},setInterval(){return 1},setTimeout(){return 1},
    confirm:()=>true,prompt:()=>null,
    alert:()=>{},console:{log(){},warn(){},error(){}}
  };
  vm.runInNewContext(script,context,{filename:"orion-connected-panel.js"});
  return {byId,tabs,calls,copied,connect:()=>{connectionStatus="connected"},
    flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

test("QR: modo escolhido, 5 etapas, preparo real somente após clique, contagem verdadeira",async()=>{
  const h=harness();
  assert.equal(h.byId("wizardChoose").classList.contains("hidden"),false);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
  await h.tabs[0].click();
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal(h.byId("guideTitle").textContent,"Abra o WhatsApp");
  for(let i=0;i<5;i++)await h.byId("wizardNext").click();
  await h.flush();await h.flush();
  assert.equal(h.byId("qrPanel").classList.contains("hidden"),false);
  assert.ok(h.calls.some(x=>x.url==="/api/v1/qr/image"));
  assert.match(h.byId("qrImage").src,/^data:image\/png;base64,/);
  assert.match(h.byId("qrCountdown").textContent,/s$/);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"),false);
});

test("Código: tutorial -> número -> geração real -> código -> copiar",async()=>{
  const h=harness();
  await h.tabs[1].click();
  for(let i=0;i<5;i++)await h.byId("wizardNext").click();
  assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),false);
  h.byId("pairPhone").value="11 99999-9999";
  await h.byId("pairForm").fire("submit");
  assert.equal(h.calls.filter(x=>x.url==="/api/v1/qr/pair").length,1);
  const pair=h.calls.find(x=>x.url==="/api/v1/qr/pair");
  assert.equal(pair.body.phone,"5511999999999");
  assert.equal(h.byId("pairCode").textContent,"ABCD-EFGH");
  assert.equal(h.byId("codeResult").classList.contains("hidden"),false);
  await h.byId("copyCode").click();
  assert.deepEqual(h.copied,["ABCDEFGH"]);
});

test("número inválido não envia solicitação de pareamento",async()=>{
  const h=harness();
  await h.tabs[1].click();
  for(let i=0;i<5;i++)await h.byId("wizardNext").click();
  h.byId("pairPhone").value="abc";
  await h.byId("pairForm").fire("submit");
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"),false);
  assert.match(h.byId("codeStatus").textContent,/número válido/);
});

test("conexão confirmada pelo backend ativa etapa de sucesso, sem envio",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await h.tabs[1].click();
  for(let i=0;i<5;i++)await h.byId("wizardNext").click();
  h.byId("pairPhone").value="5511999999999";
  await h.byId("pairForm").fire("submit");
  h.connect();
  await h.byId("statusRefresh").click();
  assert.equal(h.byId("wizardSuccess").classList.contains("hidden"),false);
  assert.equal(h.byId("connectedCenter").classList.contains("hidden"),true);
  await h.byId("wizardOpenCenter").click();
  assert.equal(h.byId("connectedCenter").classList.contains("hidden"),false);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/send"),false);
});
