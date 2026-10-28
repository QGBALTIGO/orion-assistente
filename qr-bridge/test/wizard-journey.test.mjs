import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const html=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
const fields=fs.readFileSync(new URL("../../app/static/orion-fields.js",import.meta.url),"utf8");
const connectView=fs.readFileSync(new URL("../../app/static/orion-connect.js",import.meta.url),"utf8");
const connectCSS=fs.readFileSync(new URL("../../app/static/orion-suite.css",import.meta.url),"utf8");
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
  assert.match(html,/orion-suite\.css/);
  assert.doesNotMatch(html,/orion-connect\.css/);
  assert.match(html,/orion-connect\.js/);
});

test("a apresentação comum separa sete passos de Código e QR sem instrução contraditória",()=>{
  const context={window:{}};
  vm.runInNewContext(connectView,context);
  const view=context.window.OrionConnectView;
  const code=view.instructions("code"),qr=view.instructions("qr");
  assert.equal((code.match(/role="listitem"/g)||[]).length,7);
  assert.equal((qr.match(/role="listitem"/g)||[]).length,7);
  assert.equal((code.match(/wizard-timeline-dot edge/g)||[]).length,2);
  assert.equal((code.match(/<strong>/g)||[]).length,7);
  assert.equal((code.match(/<p>/g)||[]).length,7);
  for(let step=1;step<=7;step++)assert.match(code,new RegExp('PASSO '+step));
  assert.match(code,/Conectar com número de telefone/);
  assert.doesNotMatch(code,/câmera|escane|QR Code/i);
  assert.match(qr,/câmera.*QR Code/);
  assert.doesNotMatch(qr,/número de telefone/);
  assert.match(view.methods("code"),/data-method="code" aria-pressed="true"/);
  assert.match(view.methods("qr","data-qc-method"),/data-qc-method="qr" aria-pressed="true"/);
  assert.ok(view.methods("code").indexOf('data-method="qr"')<view.methods("code").indexOf('data-method="code"'));
  assert.match(view.methods("code"),/Escaneie com seu telefone/);
  assert.match(view.methods("code"),/Digite no seu telefone/);
  assert.equal(view.normalizeCode("abcdefgh"),"ABCD-EFGH");
  assert.throws(()=>view.normalizeCode("not-a-code"));
  assert.throws(()=>view.instructions("unexpected"));
  assert.throws(()=>view.methods("code","onclick"));
});

function harness(initialStatus="qr_ready",{deferPair=false}={}){
  const elements=new Map(),calls=[],copied=[];
  let connectionStatus=initialStatus,currentTime=Date.now(),timerCallback=null,activeElement=null,finishPair=null;
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
    get textContent(){return (this._textNode?.textContent||'')+(this.children||[]).map(child=>typeof child==='string'?child:child?.textContent||'').join('')}
    set textContent(value){this._textNode=String(value)?{nodeType:3,textContent:String(value)}:null;this.children=[]}
    get firstChild(){return this._textNode||this.children[0]||null}
    async fire(event="click"){
      const fn=this.listeners.get(event);
      if(!fn)throw Error("No listener for "+this.id+" "+event);
      return fn({preventDefault(){},target:this});
    }
    click(){return this.fire("click")}
    append(...args){this.children??=[];this.children.push(...args);for(const child of args)if(child&&typeof child==="object")child.parent=this}
    get parentElement(){return this.parent||null}
    before(...children){if(!this.parent)return;const parent=this.parent;parent.children.splice(parent.children.indexOf(this),0,...children);for(const child of children)if(child&&typeof child==='object')child.parent=parent}
    after(...children){if(!this.parent)return;const parent=this.parent;parent.children.splice(parent.children.indexOf(this)+1,0,...children);for(const child of children)if(child&&typeof child==='object')child.parent=parent}
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
    else if(url==="/auth/login"){payload={authenticated:true,role:"admin",csrf:"synthetic-csrf"}}
    else if(url==="/api/v1/account/businesses"||url==="/api/v1/tenants"){
      payload=[{id:"10000000-0000-4000-8000-000000000001",name:"Empresa sintética",role:"owner"}];
    }
    else if(url.endsWith("/business/profile")){
      payload={generation:0,draft:null,published:null,draft_template:{business_name:"Empresa sintética",timezone:"America/Cuiaba"},
        supported_capabilities:[],scheduling_timezone:"America/Cuiaba",warnings:[],access:{can_edit_draft:true,can_publish:true}};
    }
    else if(url.endsWith("/business/profile/history")){payload=[]}
    else if(url==="/api/v1/qr/status"){
      payload={status:connectionStatus,phone:"5511999999999",ingestion:"receiving",
        messageStats:{available:false},featureSettings:{},featureCapabilities:{}};
    }else if(url==="/api/v1/qr/image"){
      payload={status:"qr_ready",image:"data:image/png;base64,QUJDRA==",expiresIn:44};
    }else if(url==="/api/v1/qr/pair"){
      if(deferPair)await new Promise(resolve=>{finishPair=resolve});
      payload={status:"pairing",code:"ABCD-EFGH",expiresIn:79};
    }
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
    location:{href:"https://localhost/panel?aba=dispositivo",pathname:"/panel",search:"?aba=dispositivo",hostname:"localhost",protocol:"https:"},
    sessionStorage:{getItem(){return null},removeItem(){}},
    navigator:{clipboard:{writeText:async x=>copied.push(x)}},
    Option:class{constructor(label,value){this.label=label;this.value=value}},
    AbortController,URL,URLSearchParams,Date:FakeDate,Number,Intl,Math,Object,Array,String,RegExp,Set,Map,JSON,
    clearInterval(){},clearTimeout(){},
    setInterval(fn,ms){if(ms===1000)timerCallback=fn;return 1},
    setTimeout(){return 1},
    confirm:()=>true,prompt:()=>null,
    alert:()=>{},console:{log(){},warn(){},error(){}}
  };
  vm.runInNewContext(fields,context,{filename:"orion-fields.js"});
  vm.runInNewContext(connectView,context,{filename:"orion-connect.js"});
  vm.runInNewContext(script,context,{filename:"orion-connected-panel.js"});
  return {byId,tabs,calls,copied,themeButtons,authThemeButtons,brandImages,root:htmlRoot,storage,
    changeSystem:async matches=>{preferred.matches=matches;systemListener?.({matches})},
    connect:()=>{connectionStatus="connected"},
    finishPair:()=>finishPair?.(),
    advance:ms=>{currentTime+=ms;timerCallback?.()},
    flush:()=>new Promise(resolve=>setImmediate(resolve))};
}

async function openCodeForm(h){
  await h.tabs[1].click();
  if(!h.byId("wizardTutorial").classList.contains("hidden"))await h.byId("wizardNext").click();
  assert.equal(h.byId("codePanel").classList.contains("hidden"),false);
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),true);
}

test("Código inicia no tutorial original sem gerar vínculo nem pedir QR na montagem",async()=>{
  const h=harness();
  assert.equal(h.byId("codePanel").classList.contains("hidden"),true);
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal(h.tabs[1].getAttribute("aria-pressed"),"true");
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"||x.url==="/api/v1/qr/pair"),false);
});

test("o tutorial original mostra sete passos completos e abre o formulário Código com um clique sem POST",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  assert.equal((h.byId("guideSteps").innerHTML.match(/role="listitem"/g)||[]).length,7);
  assert.match(h.byId("guideSteps").innerHTML,/Conectar com número de telefone/);
  assert.doesNotMatch(h.byId("guideSteps").innerHTML,/câmera|escane|QR Code/i);
  assert.match(html,/Concluí os passos e estou pronto para prosseguir/);
  await openCodeForm(h);
  assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),false);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"||x.url==="/api/v1/qr/pair"),false);
});

test("Voltar do formulário Código retorna ao tutorial sem apagar o telefone nem solicitar vínculo",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
  h.byId("pairPhone").value="11 99999-9999";
  await h.byId("wizardCodeBack").click();
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal(h.byId("codePanel").classList.contains("hidden"),true);
  await h.byId("wizardNext").click();
  assert.equal(h.byId("pairPhone").value,"11 99999-9999");
  assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),false);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"||x.url==="/api/v1/qr/image"),false);
});

test("QR: sete passos próprios, geração somente após ação explícita e prazo real",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
  await h.tabs[0].click();
  assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
  assert.equal((h.byId("guideSteps").innerHTML.match(/role="listitem"/g)||[]).length,7);
  assert.match(h.byId("guideSteps").innerHTML,/câmera/);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
  await h.byId("wizardNext").click();
  await h.flush();await h.flush();
  assert.equal(h.byId("qrPanel").classList.contains("hidden"),false);
  assert.ok(h.calls.some(x=>x.url==="/api/v1/qr/image"));
  assert.match(h.byId("qrImage").src,/^data:image\/png;base64,/);
  assert.match(h.byId("qrCountdown").textContent,/s$/);
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"),false);
});

test("Código: número, solicitação única, código e cópia sem QR",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
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
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
});

test("número inválido não envia solicitação de pareamento",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
  h.byId("pairPhone").value="abc";
  await h.byId("pairForm").fire("submit");
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"),false);
  assert.match(h.byId("codeStatus").textContent,/número válido/);
});

test("sessão revogada mostra recuperação e bloqueia geração sem reinício automático",async()=>{
  const h=harness("logged_out");
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  assert.equal(h.byId("wizardRecoveryArea").classList.contains("hidden"),false);
  assert.equal(h.byId("wizardRecover").classList.contains("hidden"),false);
  assert.equal(h.byId("wizardGenerate").disabled,true);
  assert.equal(h.byId("codePanel").classList.contains("hidden"),true);
  h.byId("pairPhone").value="5511999999999";
  await h.byId("pairForm").fire("submit");
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"||x.url==="/api/v1/qr/recover"||x.url==="/api/v1/qr/reconnect"),false);
});

test("conexão ainda em preparo não libera Gerar nem envia tentativa de pareamento",async()=>{
  const h=harness("starting");
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
  assert.equal(h.byId("wizardGenerate").disabled,true);
  h.byId("pairPhone").value="5511999999999";
  await h.byId("pairForm").fire("submit");
  assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/pair"),false);
  assert.match(h.byId("codeStatus").textContent,/prepar|pronta/i);
});

test("resposta atrasada de geração não repõe código após sair da conta",async()=>{
  const h=harness("qr_ready",{deferPair:true});
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
  h.byId("pairPhone").value="5511999999999";
  const pending=h.byId("pairForm").fire("submit");
  await h.flush();
  assert.equal(h.calls.filter(x=>x.url==="/api/v1/qr/pair").length,1);
  await h.byId("logout").click();
  h.finishPair();await pending;
  assert.equal(h.byId("pairCode").textContent,"");
  assert.equal(h.byId("codeResult").classList.contains("hidden"),true);
  assert.equal(h.byId("appView").classList.contains("hidden"),true);
});

test("conexão confirmada pelo backend ativa etapa de sucesso, sem envio",async()=>{
  const h=harness();
  await h.byId("loginForm").fire("submit");
  await h.flush();await h.flush();
  await openCodeForm(h);
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


test("trocar o método oculta o código e permite retomar o mesmo código válido sem novo pedido",async()=>{
 const h=harness();
 await h.byId("loginForm").fire("submit");
 await h.flush();await h.flush();
 await openCodeForm(h);
 h.byId("pairPhone").value="5511999999999";
 await h.byId("pairForm").fire("submit");
 assert.equal(h.byId("codeResult").classList.contains("hidden"),false);
 const calls=h.calls.filter(x=>x.url==="/api/v1/qr/pair").length;
 await h.tabs[0].click();
 assert.equal(h.byId("wizardTutorial").classList.contains("hidden"),false);
 assert.equal(h.byId("codePanel").classList.contains("hidden"),true);
 assert.equal(h.calls.some(x=>x.url==="/api/v1/qr/image"),false);
 await openCodeForm(h);
 assert.equal(h.byId("wizardCodeInput").classList.contains("hidden"),true);
 assert.equal(h.byId("codeResult").classList.contains("hidden"),false);
 assert.equal(h.byId("pairCode").textContent,"ABCD-EFGH");
 assert.equal(h.calls.filter(x=>x.url==="/api/v1/qr/pair").length,calls);
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
 await openCodeForm(h);
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
 assert.match(connectCSS,/@media\(max-width:/);
 assert.match(connectCSS,/prefers-reduced-motion:reduce/);
 assert.match(connectCSS,/min-height:44px/);
 assert.match(connectCSS,/\.os-button\.os-large\{/);
 assert.match(connectCSS,/\.os-card\{/);
 assert.match(connectCSS,/\.os-field\{/);
 assert.match(connectCSS,/\.os-service-option\[aria-pressed=true\]/);
 assert.match(connectView,/wizard-method/);
 assert.match(html.match(/<form\b[^>]*id="pairForm"[^>]*>/)?.[0]||"",/class="[^"]*\bos-form\b/);
 assert.match(html.match(/<div\b[^>]*id="wizardCard"[^>]*>/)?.[0]||"",/class="[^"]*\bos-card\b/);
 assert.match(connectView,/Conectar com número de telefone/);
 assert.match(connectView,/wizard-timeline-step|wizard-timeline-dot/);
 assert.match(connectCSS,/\.wizard-timeline-step\{/);
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
 const groups=[...html.matchAll(/<div\b[^>]*(?:id="(?:authThemeMenu|setupThemeMenu)"|class="theme-switch")[^>]*>([\s\S]*?)<\/div>/g)].map(match=>match[1]);
 assert.equal(groups.length,3,"login, setup and sidebar expose independent accessible theme groups");
 for(const section of groups){
   const choices=[...section.matchAll(/data-theme-choice="([^"]+)"/g)].map(match=>match[1]).sort();
   assert.deepEqual(choices,["auto","dark","light"],"each accessible group has one button per preference");
 }
 assert.match(html,/--sidebar-bg:/);
 assert.match(html,/html\[data-theme="light"\]/);
 assert.match(html,/\.device-hero\{background:linear-gradient/);
 assert.match(html,/\.bubbles\{background:var\(--panel-low\)/);
 assert.match(html,/\[data-orion-logo\]/);
 assert.doesNotMatch(html,/<div class="logo">O<\/div>/);
 assert.doesNotMatch(html,/<span class="nav-icon"[^>]*>[⌂▦✉⚙]/);
});
