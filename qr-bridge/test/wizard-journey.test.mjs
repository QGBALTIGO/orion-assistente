import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)?.[1];
assert.ok(script,"O painel deve incluir seu controlador JavaScript");
const ids=new Set([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]));
const required=["disconnectedWizard","wizardTutorial","guideSteps",
  "wizardNext","wizardSuccess","wizardOpenCenter",
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
  let connectionStatus="starting",currentTime=Date.now(),timerCallback=null,activeElement=null;
  function eventTarget(properties){
    const listeners=new Map();
    return Object.assign({
      addEventListener(type,callback){
        const callbacks=listeners.get(type)||[];
        if(!callbacks.includes(callback))callbacks.push(callback);
        listeners.set(type,callbacks);
      },
      removeEventListener(type,callback){
        listeners.set(type,(listeners.get(type)||[]).filter(value=>value!==callback));
      },
      dispatchEvent(event){
        for(const callback of [...(listeners.get(event.type)||[])])callback.call(this,event);
        return !event.defaultPrevented;
      }
    },properties);
  }
  class FakeDate extends Date {
    static now(){return currentTime}
  }
  class Element {
    constructor(id=""){this.id=id;this.style={};this.value=id==="resource"?"authorized_staff":"";
      this.dataset={};this.disabled=false;this.checked=false;this.textContent="";
      this.src="";this.listeners=new Map();this.attributes={};this.options=[];
      const states=new Set(["wizardTutorial","qrPanel","codePanel","wizardSuccess","authThemeMenu"]);
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
    append(...args){this.children??=[];this.children.push(...args);for(const child of args)if(child&&typeof child==="object")child.parent=this}
    replaceChildren(...args){this.children=[...args]}
    setAttribute(name,v){this.attributes[name]=String(v)}
    getAttribute(name){return this.attributes[name]??null}
    removeAttribute(name){delete this.attributes[name]}
    focus(){activeElement=this}
    closest(selector){
      if(!selector.startsWith("#"))return null;
      for(let current=this;current;current=current.parent)if(current.id===selector.slice(1))return current;
      return null;
    }
    contains(element){for(let current=element;current;current=current.parent)if(current===this)return true;return false}
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
  const themeButtons=["auto","light","dark"].map(choice=>{
    const e=new Element("theme-"+choice);e.dataset.themeChoice=choice;return e;
  });
  const authThemeButtons=["light","dark","auto"].map(choice=>{
    const e=new Element("auth-theme-"+choice);e.dataset.themeChoice=choice;return e;
  });
  byId("authThemeMenu").append(...authThemeButtons);
  byId("authTheme").append(byId("authThemeToggle"),byId("authThemeMenu"));
  const brandImages=[new Element("logo-sidebar"),new Element("logo-auth")];
  const htmlRoot={dataset:{theme:"dark",themePreference:"auto"}};
  let systemListener=null;
  const preferred={matches:true,addEventListener:(event,fn)=>{if(event==="change")systemListener=fn}};
  const storage=new Map();
  byId("settingsWeekdays").options=[0,1,2,3,4,5,6].map(value=>({value:String(value),selected:value>=1&&value<=5}));
  const document=eventTarget({
    getElementById:byId,
    documentElement:htmlRoot,
    querySelectorAll:query=>query==="[data-method]"?tabs:
      query==="[data-theme-choice]"?[...authThemeButtons,...themeButtons]:
      query==="[data-orion-logo]"?brandImages:[],
    querySelector:()=>new Element("query"),
    createElement:tag=>new Element(tag)
  });
  Object.defineProperty(document,"activeElement",{get:()=>activeElement});
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
    window:eventTarget({matchMedia:()=>preferred}),
    getComputedStyle:()=>({getPropertyValue:()=>""}),
    localStorage:{
      getItem:key=>storage.get(key)??null,
      setItem:(key,value)=>storage.set(key,value)
    },
    document,fetch,history:{replaceState(){}},
    location:{search:"",hostname:"localhost",protocol:"https:"},
    navigator:{clipboard:{writeText:async x=>copied.push(x)}},
    Option:class{constructor(label,value){this.label=label;this.value=value}},
    AbortController,URLSearchParams,Date:FakeDate,Number,Intl,Math,Object,Array,String,RegExp,Set,Map,JSON,
    clearInterval(){},clearTimeout(){},
    setInterval(fn,ms){if(ms===1000)timerCallback=fn;return 1},
    setTimeout(){return 1},
    confirm:()=>true,prompt:()=>null,
    alert:()=>{},console:{log(){},warn(){},error(){}}
  };
  vm.runInNewContext(script,context,{filename:"orion-connected-panel.js"});
  return {byId,tabs,calls,copied,themeButtons,authThemeButtons,brandImages,root:htmlRoot,storage,
    changeSystem:async matches=>{preferred.matches=matches;systemListener?.({matches})},
    connect:()=>{connectionStatus="connected"},
    advance:ms=>{currentTime+=ms;timerCallback?.()},
    flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

test("QR: modo escolhido, 5 etapas, preparo real somente após clique, contagem verdadeira",async()=>{
  const h=harness();
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal(h.byId("guideSteps").children.length,7);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
  await h.tabs[0].click();
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal(h.byId("guideSteps").children.length,7);
  await h.byId("wizardNext").click();
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
  await h.byId("wizardNext").click();
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
  await h.byId("wizardNext").click();
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
  await h.byId("wizardNext").click();
  h.byId("pairPhone").value="5511999999999";
  await h.byId("pairForm").fire("submit");
  h.connect();
  await h.byId("statusRefresh").click();
  assert.equal(h.byId("wizardSuccess").classList.contains("hidden"),false,
    JSON.stringify({calls:h.calls.map(x=>x.url),stepCount:h.byId("wizardStepCount").textContent,
      pairingCode:h.byId("pairCode").textContent,card:h.byId("wizardCard").textContent}));
  assert.equal(h.byId("connectedCenter").classList.contains("hidden"),true);
  await h.byId("wizardOpenCenter").click();
  assert.equal(h.byId("connectedCenter").classList.contains("hidden"),false);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/send"),false);
});


test("tutorial integral mostra sete passos de uma vez e avança com um clique",async()=>{
 const h=harness();
 assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
 assert.equal(h.byId("guideSteps").children.length,7);
 assert.equal(h.byId("guideSteps").children[0].children[1].children[0].textContent,"PASSO 1");
 assert.equal(h.byId("guideSteps").children[6].children[1].children[0].textContent,"PASSO 7");
 assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"||x.url==="/api/v1/qr/pair"),false);
 await h.tabs[0].click();
 assert.equal(h.byId("guideSteps").children.length,7);
 assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
 assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
 await h.byId("wizardNext").click();
 await h.flush();
 assert.equal(h.byId("qrPanel").classList.contains("hidden"),false);
 assert.ok(h.calls.some(x=>x.url==="/api/v1/qr/image"));
 await h.byId("wizardQrBack").click();
 assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
 assert.equal(h.byId("guideSteps").children.length,7);
 await h.tabs[1].click();
 await h.byId("wizardNext").click();
 assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),false);
});

test("QR expira no tempo anunciado e só atualiza ao clicar",async()=>{
 const h=harness();
 await h.byId("loginForm").fire("submit");
 await h.flush();
 await h.tabs[0].click();
 await h.byId("wizardNext").click();
 await h.flush();
 const before=h.calls.filter(x=>x.url==="/api/v1/qr/image").length;
 assert.ok(before>=1);
 h.advance(45000);
 assert.equal(h.byId("qrImage").classList.contains("hidden"),true);
 assert.match(h.byId("qrStatus").textContent,/expirado/);
 assert.equal(h.calls.filter(x=>x.url==="/api/v1/qr/reconnect").length,0);
 await h.byId("refreshQr").click();
 assert.ok(h.calls.filter(x=>x.url==="/api/v1/qr/image").length>before);
});

test("código expira e volta ao formulário sem reenviar pedidos automaticamente",async()=>{
 const h=harness();
 await h.byId("loginForm").fire("submit");
 await h.flush();
 await h.tabs[1].click();
 await h.byId("wizardNext").click();
 h.byId("pairPhone").value="5511999999999";
 await h.byId("pairForm").fire("submit");
 assert.equal(h.byId("codeResult").classList.contains("hidden"),false);
 const before=h.calls.filter(x=>x.url==="/api/v1/qr/pair").length;
 h.advance(80000);
 assert.equal(h.byId("codeResult").classList.contains("hidden"),true);
 assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),false);
 assert.match(h.byId("codeStatus").textContent,/expirou/);
 assert.equal(h.calls.filter(x=>x.url==="/api/v1/qr/pair").length,before);
});

test("CSS garante dispositivos móveis, tablets e redução de movimento",()=>{
 assert.match(html,/@media\(max-width:640px\)/);
 assert.match(html,/@media\(min-width:900px\)/);
 assert.match(html,/prefers-reduced-motion:reduce/);
 assert.match(html,/\.wizard-timeline-step/);
 assert.match(html,/wizard-continue/);
 assert.doesNotMatch(html,/Conexão não oficial, limitada ao número que você administra/);
 assert.doesNotMatch(html,/Ambiente de homologação/);
 assert.doesNotMatch(html,/wizardPrevious|wizardTutorialClose|wizardStepCount/);
 assert.equal([...html.matchAll(/id="wizardNext"/g)].length,1);
 assert.match(html,/aria-pressed=/);
});

test("theme switch respects automatic system preference, explicit choice and logo",async()=>{
 const h=harness();
 assert.equal(h.root.dataset.themePreference,"auto");
 assert.equal(h.root.dataset.theme,"dark");
 assert.equal(h.themeButtons[0].attributes["aria-pressed"],"true");
 await h.themeButtons[1].click();
 assert.equal(h.root.dataset.theme,"light");
 assert.equal(h.root.dataset.themePreference,"light");
 assert.equal(h.storage.get("orion-theme"),"light");
 assert.equal(h.themeButtons[1].attributes["aria-pressed"],"true");
 assert.equal(h.themeButtons[0].attributes["aria-pressed"],"false");
 assert.ok(h.brandImages.every(i=>i.getAttribute("src")==="/brand/orion-brand-light.svg"));
 await h.changeSystem(true); // explicit light stays light even when OS toggles dark.
 assert.equal(h.root.dataset.theme,"light");
 await h.themeButtons[0].click();
 await h.changeSystem(false);
 assert.equal(h.root.dataset.theme,"light");
 await h.changeSystem(true);
 assert.equal(h.root.dataset.theme,"dark");
 assert.ok(h.brandImages.every(i=>i.getAttribute("src")==="/brand/orion-brand-dark.svg"));
 await h.themeButtons[2].click();
 await h.changeSystem(false);
 assert.equal(h.root.dataset.theme,"dark");
 assert.equal(h.storage.get("orion-theme"),"dark");
});

test("theme choices are unique in each accessible group and the panel uses official logos",()=>{
 for(const item of ["auto","light","dark"])
   assert.match(html,new RegExp('data-theme-choice="'+item+'"[^>]*aria-label="Tema [^"]+"'));
 const boundary=html.indexOf('<div id="appView"');
 assert.ok(boundary>0);
 for(const section of [html.slice(0,boundary),html.slice(boundary)]){
   const choices=[...section.matchAll(/data-theme-choice="([^"]+)"/g)].map(match=>match[1]).sort();
   assert.deepEqual(choices,["auto","dark","light"],"login and administration each have one button per preference");
 }
 assert.match(html,/--sidebar-bg:/);
 assert.match(html,/html\[data-theme="light"\]/);
 assert.match(html,/\.device-hero\{background:linear-gradient/);
 assert.match(html,/\.bubbles\{background:var\(--panel-low\)/);
 assert.match(html,/\[data-orion-logo\]/);
 assert.doesNotMatch(html,/<div class="logo">O<\/div>/);
 assert.doesNotMatch(html,/<span class="nav-icon"[^>]*>[⌂▦✉⚙]/);
});
