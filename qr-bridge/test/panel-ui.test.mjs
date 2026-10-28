import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {randomUUID} from "node:crypto";

const panel=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
const fields=fs.readFileSync(new URL("../../app/static/orion-fields.js",import.meta.url),"utf8");
const sections=[...panel.matchAll(/<script>([\s\S]*?)<\/script>/g)];

test("painel tem script válido em JavaScript",()=>{
  assert.equal(sections.length,2,"bootstrap de tema e controlador do painel devem existir");
  for(const [index,section] of sections.entries())
    assert.doesNotThrow(()=>new vm.Script(section[1],{filename:"panel-inline-"+index+".js"}));
});

test("QR indica atraso de 25 segundos, expiração e falha",()=>{
  for(const state of ["qr_delayed","qr_expired","pair_failed","logged_out"]){
    assert.ok(panel.includes(state),state+" missing from UI");
  }
  assert.match(panel,/Atualizar ou tentar novamente/);
  assert.match(panel,/Refazer vínculo/);
  assert.match(panel,/25 segundos/);
});

test("código não é mantido visível depois que expira",()=>{
  assert.match(panel,/pairExpiresAt/);
  assert.match(panel,/pairExpiresAt>0&&Date\.now\(\)>pairExpiresAt/);
  assert.match(panel,/Código expirado/);
});

test("a página requer cookie autenticado e não expõe tokens",()=>{
  assert.doesNotMatch(panel,/SUPPORT_API_TOKEN/);
  assert.doesNotMatch(panel,/QR_BRIDGE_TOKEN/);
  assert.match(panel,/\/auth\/login/);
  assert.match(panel,/X-Orion-CSRF/);
});

// Execute the real inline controller with deferred HTTP responses. No network,
// WhatsApp messages, browser cookies or production credentials are used.
function panelHarness(){
  let activeElement=null;
  function eventTarget(properties){
    const listeners=new Map();
    return Object.defineProperties({
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
    },Object.getOwnPropertyDescriptors(properties));
  }
  class Element{
    constructor(tag="div"){
      this.children=[];this.listeners={};this.options=[];this.value="";
      this.tagName=tag.toUpperCase();this.focusCount=0;this.files=[];this.checked=false;this.disabled=false;
      this.textContent="";this.dataset={};this.hiddenClasses=new Set();this.open=false;this.isConnected=true;
      this.classList={
        add:name=>this.hiddenClasses.add(name),
        remove:name=>this.hiddenClasses.delete(name),
        contains:name=>this.hiddenClasses.has(name),
        toggle:(name,force)=>{
          const add=force??!this.hiddenClasses.has(name);
          if(add)this.hiddenClasses.add(name);else this.hiddenClasses.delete(name);
          return add;
        }
      };
    }
    get textContent(){return (this._textNode?.textContent||'')+this.children.map(child=>typeof child==='string'?child:child?.textContent||'').join('')}
    set textContent(value){this._textNode=String(value)?{nodeType:3,textContent:String(value)}:null;this.children=[]}
    get firstChild(){return this._textNode||this.children[0]||null}
    addEventListener(type,callback){(this.listeners[type]??=[]).push(callback)}
    append(...children){this.children.push(...children);for(const child of children)if(typeof child==='object')child.parent=this}
    get parentElement(){return this.parent||null}
    before(...children){if(!this.parent)return;const parent=this.parent;parent.children.splice(parent.children.indexOf(this),0,...children);for(const child of children)if(child&&typeof child==='object')child.parent=parent}
    after(...children){if(!this.parent)return;const parent=this.parent;parent.children.splice(parent.children.indexOf(this)+1,0,...children);for(const child of children)if(child&&typeof child==='object')child.parent=parent}
    replaceChildren(...children){this.children=[];this.options=[];this.textContent="";this.append(...children);for(const child of children)if(child&&Object.hasOwn(child,'value'))this.options.push(child);if(this.options.length)this.value=this.options[0].value}
    remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this)}
    setAttribute(){}
    removeAttribute(){}
    focus(){this.focusCount++;activeElement=this}
    showModal(){this.open=true;this.modalCalls=(this.modalCalls||0)+1}
    close(){this.open=false}
    reset(){this.value='';this.checked=false}
    reportValidity(){return true}
    querySelector(){return new Element()}
    querySelectorAll(){return []}
    add(option){this.options.push(option);if(this.options.length===1)this.value=option.value}
  }
  const nodes=new Map(),requests=[];let fakeNow;
  const node=id=>{
    assert.ok(panel.includes('id="'+id+'"'),"unknown panel element "+id);
    if(!nodes.has(id))nodes.set(id,new Element());
    return nodes.get(id);
  };
  const context={
    document:eventTarget({getElementById:node,createElement:tag=>new Element(tag),
      querySelector:()=>new Element(),querySelectorAll:()=>[],documentElement:{dataset:{}},get activeElement(){return activeElement}}),
    window:eventTarget({matchMedia:()=>({matches:false,addEventListener(){}})}),
    // This lightweight DOM has no CSS engine; an unset custom property is empty.
    getComputedStyle:()=>({getPropertyValue:()=>""}),
    Option:function(text,value){this.textContent=text;this.value=value},
    fetch:(url,options={})=>new Promise((resolve,reject)=>requests.push({
      url,options,reject,respond:(body,status=200)=>resolve({
        ok:status>=200&&status<300,status,json:async()=>body
      })
    })),
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    confirm:()=>true,AbortController,URLSearchParams,URL,Intl,console,crypto:{randomUUID},
    location:{search:""},history:{replaceState(){}},Date:class extends Date{static now(){return fakeNow??Date.now()}}
  };
  const source=sections[1][1].replace("renderForm();renderGuide();",`
    renderForm();renderGuide();
    globalThis.panelTest={updateStatus,refreshMessages,selectChat,aiBubble,aiCheckStatus,aiLoadProfiles,aiLoadTenants,showLogin,
      loadBusinessProfile,loadBusinesses,collectBusinessProfile,businessMutation,reviewBusinessVersion,businessChanged,loadBusinessStyleExamples,openBusinessPreview,
      loadCommerceTenant,loadCommerceTenants,commerceSelectProduct,commerceSelectVariant,commerceSave,commerceProductPayload,commerceVariantPayload,commerceStockPayload,commerceAddFields,commerceAddBasket,commerceCalculate,commerceCreateOrder,commerceConfirmOrder,commerceRead,commerceCatalogChanged,commercePlanningChanged,
      commerceReadComplements,commerceSaveComplement,commerceFillComplement,
      loadTenants,loadRecords,submitRecord,openPage,confirmAction,
      setRole:value=>{loginRole=value},
      setBusinessContext:({id='tenant-a',name='Empresa sintética',timezone='America/Sao_Paulo',role='owner'}={})=>{
        // CI: establish a verified, single-business identity. The real API must still
        // deny all tenant and QR routes when no authenticated business exists.
        suiteTenantId=id;businessRows=[{id,name,timezone,role}];businessContextReady=true;businessContextError='';
      },
      authenticate:()=>{csrf="synthetic-csrf"},
      deviceStatus:()=>deviceStatus};
  `);
  vm.runInNewContext(fields,context,{filename:"orion-fields.js"});
  vm.runInNewContext(source,context);
  context.panelTest.setBusinessContext();
  const take=(url,method="GET")=>{
    const request=requests.find(r=>!r.taken&&r.url===url&&(r.options.method||"GET")===method);
    assert.ok(request,"missing "+method+" "+url);
    request.taken=true;return request;
  };
  return {ui:context.panelTest,node,take,requests,activeElement:()=>activeElement,advanceTime:ms=>{fakeNow=(fakeNow??Date.now())+ms}};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function acceptAction(h){h.node('actionConfirmAccept').listeners.click[0]();await tick()}
async function cancelAction(h){h.node('actionConfirmCancel').listeners.click[0]();await tick()}
const phoneA="5511999990001",phoneB="5511999990002";
const messageUrl=phone=>"/api/v1/qr/messages?phone="+phone;
const row=(body,id="out:synthetic-1",delivery="server_accepted")=>({
  id,body,delivery,direction:"out",at:1792250000000
});

test("trocar contato limpa o histórico e ignora uma resposta anterior atrasada",async()=>{
  const h=panelHarness();
  h.ui.selectChat(phoneA);
  const first=h.ui.refreshMessages();
  const old=h.take(messageUrl(phoneA));
  h.node("chatBubbles").append({textContent:"mensagem do contato anterior"});
  h.ui.selectChat(phoneB);
  assert.equal(h.node("chatBubbles").children.length,1);
  assert.equal(h.node("chatBubbles").children[0].textContent,"Carregando mensagens...");
  assert.equal(h.node("chatTitle").textContent,"Conversa com +"+phoneB);
  const second=h.ui.refreshMessages();
  h.take(messageUrl(phoneB)).respond({items:[row("mensagem do contato atual")]});
  await second;
  old.respond({items:[row("mensagem antiga atrasada")]});
  await first;
  assert.equal(h.node("chatBubbles").children[0].children[0].textContent,"mensagem do contato atual");
});

test("polling fora de ordem não regride recibos nem mostra erro de consulta antiga",async()=>{
  const h=panelHarness();h.ui.selectChat(phoneA);
  const earlier=h.ui.refreshMessages(),old=h.take(messageUrl(phoneA));
  const later=h.ui.refreshMessages();
  h.take(messageUrl(phoneA)).respond({items:[row("recibo novo","out:new","delivered")]});
  await later;
  old.respond({items:[row("recibo antigo","out:new","pending")]});
  await earlier;
  assert.match(h.node("chatBubbles").children[0].children[1].textContent,/entregue$/);
  const stale=h.ui.refreshMessages(),staleRequest=h.take(messageUrl(phoneA));
  const fresh=h.ui.refreshMessages();
  h.take(messageUrl(phoneA)).respond({items:[row("recibo lido","out:new","read")]});
  await fresh;
  staleRequest.reject(Error("falha antiga"));await stale;
  assert.equal(h.node("chatNotice").textContent,"");
  assert.match(h.node("chatBubbles").children[0].children[1].textContent,/lida$/);
});

async function connect(h,status={}){
  h.ui.authenticate();const request=h.ui.updateStatus();
  h.take("/api/v1/qr/status").respond({status:"connected",...status});
  await request;
}
async function sendOnce(h){
  await connect(h);
  h.node("chatPhone").value=phoneA;h.node("chatText").value="mensagem sintética";
  const submitted=h.node("sendForm").listeners.submit[0]({preventDefault(){}});
  await acceptAction(h);
  h.take("/api/v1/qr/send","POST").respond({accepted:true,id:"out:synthetic-1",delivery:"server_accepted"});
  await tick();
  h.take(messageUrl(phoneA)).respond({items:[row("mensagem sintética")]});
  await tick();
  h.take("/api/v1/qr/chats").respond({items:[]});await submitted;
}

test("aviso acompanha somente o ID enviado sem repetir o POST ou afirmar legibilidade",async()=>{
  const h=panelHarness();await sendOnce(h);
  assert.match(h.node("chatNotice").textContent,/Aguardando confirmação de entrega/);
  let polling=h.ui.refreshMessages();
  h.take(messageUrl(phoneA)).respond({items:[row("outra mensagem","out:other","read")]});
  await polling;
  assert.match(h.node("chatNotice").textContent,/Aguardando confirmação de entrega/);
  for(const [delivery,expected] of [["delivered",/confirmou a entrega/],["read",/confirmou a leitura/]]){
    polling=h.ui.refreshMessages();
    h.take(messageUrl(phoneA)).respond({items:[row("mensagem sintética","out:synthetic-1",delivery)]});
    await polling;
    assert.match(h.node("chatNotice").textContent,expected);
    assert.match(h.node("chatNotice").textContent,/Confirme no celular se o texto está legível/);
    assert.doesNotMatch(h.node("chatNotice").textContent,/Aguardando confirmação/);
  }
  h.ui.selectChat(phoneB);assert.equal(h.node("chatNotice").textContent,"");
  assert.equal(h.requests.filter(r=>r.options.method==="POST").length,1);
});

test("falha de envio permanece observável e não dispara outro POST",async()=>{
  const h=panelHarness();await sendOnce(h);
  const polling=h.ui.refreshMessages();
  h.take(messageUrl(phoneA)).respond({items:[row("mensagem sintética","out:synthetic-1","failed")]});
  await polling;
  assert.match(h.node("chatNotice").textContent,/informou falha.*Verifique no celular/);
  assert.equal(h.requests.filter(r=>r.options.method==="POST").length,1);
});

test("falha de consulta invalida conectado e bloqueia envio até nova confirmação",async()=>{
  const h=panelHarness();await connect(h,{automaticRepliesEnabled:true,transportConfig:{pilotConfigured:true}});
  assert.equal(h.node("chatConnection").textContent,"Conectado");
  const failed=h.ui.updateStatus();h.take("/api/v1/qr/status").reject(Error("rede indisponível"));await failed;
  assert.equal(h.ui.deviceStatus(),"error");
  assert.equal(h.node("chatConnection").textContent,"Conexão não confirmada");
  assert.equal(h.node("chatDot").className,"dot red");
  assert.equal(h.node("connectedBanner").classList.contains("hidden"),true);
  assert.match(h.node("chatMode").textContent,/indisponível/);
  h.node("chatPhone").value=phoneA;h.node("chatText").value="mensagem sintética";
  await h.node("sendForm").listeners.submit[0]({preventDefault(){}});
  assert.equal(h.requests.filter(r=>r.options.method==="POST").length,0);
  await connect(h);assert.equal(h.ui.deviceStatus(),"connected");
});

test("falha antiga de status não invalida confirmação mais recente",async()=>{
  const h=panelHarness();h.ui.authenticate();
  const old=h.ui.updateStatus(),oldRequest=h.take("/api/v1/qr/status");
  const current=h.ui.updateStatus();h.take("/api/v1/qr/status").respond({status:"connected"});await current;
  oldRequest.reject(Error("falha atrasada"));await old;
  assert.equal(h.ui.deviceStatus(),"connected");
  assert.equal(h.node("chatConnection").textContent,"Conectado");
});

test("cabeçalho distingue piloto autorizado, automação do canal e modo manual",async()=>{
  const h=panelHarness();
  await connect(h,{automaticRepliesEnabled:true,transportConfig:{pilotConfigured:true}});
  assert.match(h.node("chatMode").textContent,/restritas ao contato autorizado do piloto/);
  await connect(h,{automaticRepliesEnabled:true,transportConfig:{pilotConfigured:false}});
  assert.match(h.node("chatMode").textContent,/conforme as configurações do canal/);
  await connect(h,{automaticRepliesEnabled:false});
  assert.match(h.node("chatMode").textContent,/Respostas automáticas desativadas/);
});

const previewUrl=tenant=>"/api/v1/tenants/"+tenant+"/ai/conversation-preview";
const aiStatusUrl=tenant=>"/api/v1/tenants/"+tenant+"/ai/status";
async function readyAi(h,tenant=h.node('aiTenant').value,overrides={}){
  h.node('aiTenant').value=tenant;const checking=h.ui.aiCheckStatus();h.take(aiStatusUrl(tenant)).respond({configured:true,preview_enabled:true,audio_preview_enabled:true,...overrides});await checking;
}
const contentOf=node=>typeof node==='string'?node:node.children?.length?
  node.children.map(contentOf).join(''):node.textContent||'';
function submitPreview(h,text){
  h.node('aiInput').value=text;
  return h.node('aiForm').listeners.submit[0]({preventDefault(){}});
}

test('simulador ignora resposta de outro tenant e finally antigo não libera envio novo',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const first=submitPreview(h,'pergunta da empresa A');
  const old=h.take(previewUrl('tenant-a'),'POST');
  h.node('aiTenant').value='tenant-b';
  const changed=h.node('aiTenant').listeners.change[0]();
  h.take(businessUrl('tenant-b')).respond(snapshot('Empresa B'));
  h.take(aiStatusUrl('tenant-b')).respond({configured:true,preview_enabled:true});await changed;
  await tick();
  const second=submitPreview(h,'pergunta da empresa B');
  const current=h.take(previewUrl('tenant-b'),'POST');
  assert.notEqual(JSON.parse(old.options.body).session,JSON.parse(current.options.body).session);
  old.respond({answer:'DADO PRIVADO DA EMPRESA A',step:'menu'});await first;
  assert.equal(h.node('aiSend').disabled,true);
  assert.equal(h.node('aiInput').focusCount,0);
  assert.doesNotMatch(contentOf(h.node('aiMessages')),/EMPRESA A/);
  current.respond({answer:'resposta da empresa B',step:'catalog'});await second;
  assert.match(contentOf(h.node('aiMessages')),/resposta da empresa B/);
  assert.equal(h.node('aiSend').disabled,false);
  assert.equal(h.node('aiInput').focusCount,1);
});

test('Nova conversa descarta erro antigo sem trocar diagnóstico ou botão da nova sessão',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const oldSubmission=submitPreview(h,'mensagem anterior'),old=h.take(previewUrl('tenant-a'),'POST');
  h.node('aiReset').listeners.click[0]();
  h.take(businessUrl('tenant-a')).respond(snapshot());await tick();
  const freshSubmission=submitPreview(h,'nova mensagem'),fresh=h.take(previewUrl('tenant-a'),'POST');
  old.reject(Error('erro da sessão anterior'));await oldSubmission;
  assert.equal(h.node('aiSend').disabled,true);
  assert.equal(h.node('aiDiagnostics').textContent,'Nova conversa simulada.');
  assert.equal(contentOf(h.node('aiMessages')),'nova mensagem');
  fresh.respond({answer:'nova resposta',step:'date'});await freshSubmission;
  assert.match(h.node('aiDiagnostics').textContent,/date/);
  assert.equal(h.node('aiSend').disabled,false);
});

test('logout invalida prévia pendente e submit repetido não duplica POST',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const sending=submitPreview(h,'mensagem uma vez'),request=h.take(previewUrl('tenant-a'),'POST');
  await submitPreview(h,'segunda submissão bloqueada');
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,1);
  h.ui.showLogin();
  request.respond({answer:'resposta da conta anterior',step:'confirm'});await sending;
  assert.equal(h.node('aiMessages').children.length,0);
  assert.equal(h.node('aiDiagnostics').textContent,'');
});

test('status atrasado do simulador não substitui estado do tenant atual',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  const first=h.ui.aiCheckStatus(),old=h.take(aiStatusUrl('tenant-a'));
  h.node('aiTenant').value='tenant-b';
  const second=h.node('aiTenant').listeners.change[0]();
  h.take(aiStatusUrl('tenant-b')).respond({configured:true,preview_enabled:true});await second;
  old.reject(Error('empresa A indisponível'));await first;
  assert.equal(h.node('aiStatus').textContent,'Prévia disponível');
});

test('readiness desconhecida bloqueia controles e envio direto de texto ou arquivo antes de qualquer POST',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioSend').disabled,true);assert.equal(h.node('aiAudioFile').disabled,true);
  await submitPreview(h,'não deve sair');await submitAudio(h,{name:'x.wav',size:100,type:'audio/wav'});
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);assert.match(h.node('aiDiagnostics').textContent,/Atualize a disponibilidade/);
});

test('flags de configuração e prévia desativadas bloqueiam texto e áudio; áudio off ainda permite texto',async()=>{
  for(const disabled of [{configured:false},{preview_enabled:false}]){
    const h=panelHarness();await readyAi(h,'tenant-a',disabled);assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioFile').disabled,true);
    await submitPreview(h,'bloqueado');await submitAudio(h,{name:'x.ogg',size:100,type:'audio/ogg'});assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  }
  const h=panelHarness();await readyAi(h,'tenant-a',{audio_preview_enabled:false});assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiAudioSend').disabled,true);assert.equal(h.node('aiAudioFile').disabled,true);
  await submitAudio(h,{name:'x.ogg',size:100,type:'audio/ogg'});assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);assert.match(h.node('aiAudioAvailability').textContent,/desativado.*texto/);
  const sending=submitPreview(h,'texto permitido'),post=h.take(previewUrl('tenant-a'),'POST');post.respond({answer:'Resposta permitida',step:'menu'});await sending;
});

test('falha de status invalida readiness anterior e botão de atualização recupera sem reenviar mensagem',async()=>{
  const h=panelHarness();await readyAi(h,'tenant-a');const checking=h.ui.aiCheckStatus(),request=h.take(aiStatusUrl('tenant-a'));
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiStatusRefresh').disabled,true);request.reject(Error('Status indisponível'));await checking;
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioSend').disabled,true);assert.equal(h.node('aiStatusRefresh').disabled,false);
  await submitPreview(h,'sem status não sai');assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  const refreshed=h.node('aiStatusRefresh').listeners.click[0]();h.take(aiStatusUrl('tenant-a')).respond({configured:true,preview_enabled:true,audio_preview_enabled:true});await refreshed;
  assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiAudioFile').disabled,false);assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
});

test('readiness e finally antigos não liberam tenant novo nem conta desconectada',async()=>{
  const h=panelHarness();await readyAi(h,'tenant-a');const pendingA=h.ui.aiCheckStatus(),oldA=h.take(aiStatusUrl('tenant-a'));
  h.node('aiTenant').value='tenant-b';const changed=h.node('aiTenant').listeners.change[0]();h.take(businessUrl('tenant-b')).respond(snapshot('Empresa B'));
  const newB=h.take(aiStatusUrl('tenant-b'));oldA.respond({configured:true,preview_enabled:true,audio_preview_enabled:true});await pendingA;
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiStatusRefresh').disabled,true);newB.respond({configured:true,preview_enabled:false,audio_preview_enabled:false});await changed;await tick();
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioFile').disabled,true);
  const pendingB=h.ui.aiCheckStatus(),late=h.take(aiStatusUrl('tenant-b'));h.ui.showLogin();late.respond({configured:true,preview_enabled:true,audio_preview_enabled:true});await pendingB;
  assert.equal(h.node('aiTenant').value,'');assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioFile').disabled,true);
});

test('rascunho e estilo legítimos preservam readiness da mesma empresa sem habilitar áudio com override',async()=>{
  const h=panelHarness();await readyAi(h,'tenant-a');const loading=h.ui.aiLoadProfiles();h.take(businessUrl('tenant-a')).respond(snapshot());await loading;
  h.node('aiProfile').value=draftId;h.node('aiProfile').listeners.change[0]();assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiAudioSend').disabled,false);
  h.node('aiStyle').value='professional';h.node('aiStyle').listeners.change[0]();assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiAudioSend').disabled,true);
  h.node('aiStyle').value='';h.node('aiStyle').listeners.change[0]();h.node('aiReset').listeners.click[0]();assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiAudioSend').disabled,false);
});

test('simulador usa classe de alinhamento correta e negrito WhatsApp com DOM seguro',()=>{
  const h=panelHarness();
  h.ui.aiBubble('Corte: *R$ 35*\nTotal: *R$ 55*','out');
  const outgoing=h.node('aiMessages').children[0];
  assert.equal(outgoing.className,'bubble out');
  assert.equal(contentOf(outgoing),'Corte: R$ 35\nTotal: R$ 55');
  assert.deepEqual(outgoing.children.filter(n=>n.tagName==='STRONG').map(contentOf),['R$ 35','R$ 55']);
  h.ui.aiBubble('*<img src=x onerror=alert(1)>* <script>alert(2)</script>','in');
  const incoming=h.node('aiMessages').children[1];
  assert.equal(incoming.className,'bubble in');
  assert.match(contentOf(incoming),/<img src=x onerror=alert\(1\)>/);
  assert.ok(incoming.children.every(n=>typeof n==='string'||n.tagName==='STRONG'));
  assert.ok(incoming.children.filter(n=>typeof n!=='string').every(n=>!Object.hasOwn(n,'innerHTML')));
});

test('formatação incompleta, escapada ou dupla não cria elementos HTML inesperados',()=>{
  const h=panelHarness();
  const value='literal **duplo** e \\*escapado\\* e *aberto\nsem fechar';
  h.ui.aiBubble(value,'in');
  const bubble=h.node('aiMessages').children[0];
  assert.equal(contentOf(bubble),value.replaceAll('\\*','*'));
  assert.equal(bubble.children.filter(n=>n.tagName==='STRONG').length,0);
});

test('itálico WhatsApp aparece na prévia e na conversa usando apenas DOM de texto seguro',async()=>{
  const h=panelHarness();h.ui.aiBubble('*Total:* R$ 55,00\n_Valor consultado; sem reserva._\n_<img src=x onerror=alert(1)>_','in');
  const bubble=h.node('aiMessages').children[0];assert.deepEqual(bubble.children.filter(x=>typeof x==='object').map(x=>x.tagName),['STRONG','EM','EM']);
  assert.equal(contentOf(bubble),'Total: R$ 55,00\nValor consultado; sem reserva.\n<img src=x onerror=alert(1)>');
  assert.ok(bubble.children.filter(x=>typeof x==='object').every(x=>!Object.hasOwn(x,'innerHTML')));
  h.ui.selectChat(phoneA);const reading=h.ui.refreshMessages();h.take(messageUrl(phoneA)).respond({items:[row('*Resumo*\n_Sem pagamento confirmado._')]});await reading;
  const text=h.node('chatBubbles').children[0].children[0];assert.deepEqual(text.children.filter(x=>typeof x==='object').map(x=>x.tagName),['STRONG','EM']);
});

test('underscore de identificador, URL ou escape não vira itálico e condição longa não perde texto',()=>{
  const h=panelHarness();const literal='foo_bar_baz https://example.invalid/a_b_c \\_literal\\_ __duplo__ _aberto';h.ui.aiBubble(literal,'in');
  assert.equal(contentOf(h.node('aiMessages').children[0]),literal.replaceAll('\\_','_'));assert.equal(h.node('aiMessages').children[0].children.filter(x=>typeof x==='object').length,0);
  const condition='Regra oficial; ' .repeat(40);h.ui.aiBubble('_'+condition.trim()+'_','in');const result=h.node('aiMessages').children[1];assert.equal(contentOf(result),condition.trim());assert.equal(result.children.filter(x=>x.tagName==='EM').length,1);
});

test('labels escapados mantêm asteriscos e underscores literais dentro de formatação externa',()=>{
  const h=panelHarness();h.ui.aiBubble('*Consulta \\*Especial\\** — R$ 55,00\n_Linha \\_Premium\\_ SKU_250_ML_','in');
  const bubble=h.node('aiMessages').children[0],formats=bubble.children.filter(x=>typeof x==='object');
  assert.deepEqual(formats.map(x=>x.tagName),['STRONG','EM']);assert.equal(formats[0].textContent,'Consulta *Especial*');assert.equal(formats[1].textContent,'Linha _Premium_ SKU_250_ML');
  assert.equal(contentOf(bubble),'Consulta *Especial* — R$ 55,00\nLinha _Premium_ SKU_250_ML');
});

const businessUrl=(tenant,suffix='')=>'/api/v1/tenants/'+tenant+'/business/profile'+suffix;
const draftId='11111111-1111-4111-8111-111111111111';
const publishedId='22222222-2222-4222-8222-222222222222';
const olderId='33333333-3333-4333-8333-333333333333';
const profile=(name='Empresa A')=>({
  business_name:name,unit_name:'Centro',categories:['Barbearia'],description:'Descrição aprovada',
  address:'Rua cadastrada, 10',timezone:'America/Cuiaba',contact_phone:'+5567999999999',assistant_name:'Lia',
  hours:[{weekday:0,opens:'09:00:00',closes:'18:00:00'}],
  faqs:[{question:'Tem estacionamento?',answer:'Não há estacionamento próprio.'}],
  policies:[{topic:'Atraso',description:'Consulte a equipe sobre atrasos.'}],differentiators:['Acesso por elevador'],
  objectives:['answer_questions','request_human_help'],capabilities:['catalog_answers','appointments','human_handoff_request'],
  style_rules:{style:'warm',emoji:'none',length:'balanced',formality:'neutral',commercial_intensity:'none',avoid_phrases:['imperdível']},
  autonomy_rules:{discounts:'human_only',price_changes:'human_only',refunds:'human_only',cancellation_exceptions:'human_only',payment_confirmation:'verified_provider_only'}
});
const snapshot=(name='Empresa A',overrides={})=>({
  generation:7,draft:{id:draftId,revision:3,profile:profile(name)},published:{id:publishedId,revision:2,profile:profile(name)},
  draft_template:profile(name),supported_capabilities:['catalog_answers','appointments','rescheduling','human_handoff_request'],
  scheduling_timezone:'America/Cuiaba',runtime_connected:true,warnings:[],
  access:{role:'owner',can_edit_draft:true,can_publish:true,can_manage_memberships:false},...overrides
});
const history=()=>[{id:draftId,revision:3,published_before:false},{id:publishedId,revision:2,published_before:true},{id:olderId,revision:1,published_before:true}];
async function loadBusiness(h,tenant='tenant-a',data=snapshot(),versions=history()){
  h.node('businessTenant').value=tenant;
  const loading=h.ui.loadBusinessProfile();
  h.take(businessUrl(tenant)).respond(data);h.take(businessUrl(tenant,'/history')).respond(versions);
  await loading;
}

test('perfil preserva todos os fatos e autonomia ao editar apenas a linguagem',async()=>{
  const h=panelHarness();await loadBusiness(h);
  h.node('bizStyle').value='direct';h.ui.businessChanged();
  const body=JSON.parse(JSON.stringify(h.ui.collectBusinessProfile()));
  assert.deepEqual(body,{...profile(),style_rules:{...profile().style_rules,style:'direct'}});
  assert.equal(h.node('businessPublish').disabled,true,'edição local não pode publicar um rascunho antigo');
  assert.equal(h.node('businessTestDraft').disabled,true,'rascunho salvo não representa edição local');
  assert.equal(h.node('businessSave').disabled,false);
});

test('permissões de proprietário, editor e leitor respeitam o contrato retornado',async()=>{
  const h=panelHarness();await loadBusiness(h);
  assert.equal(h.node('businessFields').disabled,false);assert.equal(h.node('businessPublish').disabled,false);
  await loadBusiness(h,'tenant-a',snapshot('Empresa A',{access:{role:'editor',can_edit_draft:true,can_publish:false}}));
  assert.equal(h.node('businessFields').disabled,false);assert.equal(h.node('businessPublish').disabled,true);
  const before=h.requests.length;await h.ui.businessMutation('publish');assert.equal(h.requests.length,before);
  await loadBusiness(h,'tenant-a',snapshot('Empresa A',{access:{role:'viewer',can_edit_draft:false,can_publish:false}}));
  assert.equal(h.node('businessFields').disabled,true);assert.equal(h.node('businessSave').disabled,true);
  const count=h.requests.length;await h.ui.businessMutation('draft');await h.ui.businessMutation('rollback');
  assert.equal(h.requests.length,count);assert.equal(h.node('businessTest').disabled,false,'leitura permite teste isolado autorizado');
});

test('troca de empresa ignora perfil atrasado e falha antiga sem expor seus dados',async()=>{
  const h=panelHarness();h.node('businessTenant').value='tenant-a';
  const first=h.ui.loadBusinessProfile(),oldProfile=h.take(businessUrl('tenant-a')),oldHistory=h.take(businessUrl('tenant-a','/history'));
  await loadBusiness(h,'tenant-b',snapshot('Empresa B'));
  oldProfile.respond(snapshot('DADO PRIVADO DA EMPRESA A'));oldHistory.reject(Error('falha antiga A'));await first;
  assert.equal(h.node('bizName').value,'Empresa B');assert.doesNotMatch(h.node('businessFeedback').textContent,/antiga A/);
  assert.equal(h.node('businessSave').disabled,false);
});

test('conflito 409 mantém edição local, exige atualizar e nunca repete a gravação',async()=>{
  const h=panelHarness();await loadBusiness(h);h.node('bizDescription').value='Edição local a preservar';h.ui.businessChanged();
  const saving=h.ui.businessMutation('draft'),request=h.take(businessUrl('tenant-a','/draft'),'PUT');
  assert.equal(JSON.parse(request.options.body).expected_generation,7);
  request.respond({detail:'A configuração foi atualizada por outra pessoa'},409);await saving;
  assert.equal(h.node('bizDescription').value,'Edição local a preservar');assert.match(h.node('businessFeedback').textContent,/Atualize/);
  assert.equal(h.node('businessSave').disabled,true);await h.ui.businessMutation('draft');
  assert.equal(h.requests.filter(x=>x.options.method==='PUT').length,1);
});

test('publicar envia somente a versão salva e relê o estado confirmado',async()=>{
  const h=panelHarness();await loadBusiness(h);
  const publishing=h.ui.businessMutation('publish');await acceptAction(h);const request=h.take(businessUrl('tenant-a','/publish'),'POST');
  assert.deepEqual(JSON.parse(request.options.body),{expected_generation:7,version_id:draftId});
  request.respond({version_id:draftId,revision:3,generation:8,status:'published'});await tick();
  h.take(businessUrl('tenant-a')).respond(snapshot('Empresa A',{generation:8,published:{id:draftId,revision:3,profile:profile()}}));
  h.take(businessUrl('tenant-a','/history')).respond(history());await publishing;
  assert.match(h.node('businessFeedback').textContent,/Perfil publicado · versão 3/);
  assert.equal(h.requests.filter(x=>x.url.endsWith('/publish')).length,1);
});

test('restaurar exige revisar a versão da mesma empresa e preserva o formulário atual',async()=>{
  const h=panelHarness();await loadBusiness(h);h.node('businessVersion').value=olderId;
  const initial=h.requests.length;await h.ui.businessMutation('rollback');assert.equal(h.requests.length,initial);
  const review=h.ui.reviewBusinessVersion();h.take(businessUrl('tenant-a','/versions/'+olderId)).respond({id:olderId,revision:1,profile:profile('<img src=x onerror=alert(1)>')});await review;
  assert.equal(h.node('bizName').value,'Empresa A');assert.match(h.node('businessVersionReview').textContent,/<img/);
  assert.equal(Object.hasOwn(h.node('businessVersionReview'),'innerHTML'),false);assert.equal(h.node('businessRollback').disabled,false);
  h.node('businessVersion').value=draftId;h.node('businessVersion').listeners.change[0]();
  const count=h.requests.length;await h.ui.businessMutation('rollback');assert.equal(h.requests.length,count);
});

test('perfil desativado e capacidades sem implementação têm apresentação verdadeira',async()=>{
  const h=panelHarness();await loadBusiness(h,'tenant-a',snapshot('Empresa A',{runtime_connected:false,supported_capabilities:['catalog_answers']}));
  assert.equal(h.node('businessTestDraft').disabled,true);assert.equal(h.node('businessExamples').disabled,true);
  assert.equal(h.node('bizCapAppointments').disabled,true);assert.equal(h.node('businessTest').disabled,false);
  assert.ok(h.ui.collectBusinessProfile().capabilities.includes('appointments'),'indisponibilidade não apaga capacidade previamente salva');
  assert.match(h.node('businessRuntime').textContent,/ainda não usa este perfil/);
  assert.match(panel,/Produtos, estoque e pedidos são configurados separadamente/);assert.match(panel,/Responder com voz — indisponível/);
});

test('membro carrega somente empresas atribuídas para configuração e simulador',async()=>{
  const h=panelHarness();h.ui.setRole('member');
  const loading=h.ui.loadBusinesses();h.take('/api/v1/account/businesses').respond([{id:'tenant-m',name:'Empresa atribuída',role:'editor'}]);await tick();
  h.take(businessUrl('tenant-m')).respond(snapshot());h.take(businessUrl('tenant-m','/history')).respond(history());await loading;
  const aiLoading=h.ui.aiLoadTenants();h.take('/api/v1/account/businesses').respond([{id:'tenant-m',name:'Empresa atribuída'}]);await tick();
  h.take(businessUrl('tenant-m')).respond(snapshot());h.take(aiStatusUrl('tenant-m')).respond({configured:true,preview_enabled:true});await aiLoading;
  assert.equal(h.requests.some(x=>x.url==='/api/v1/tenants'),false);assert.equal(h.node('aiTenant').value,'tenant-m');
});

test('comparação usa catálogo real e DOM seguro; edição invalida exemplo pendente',async()=>{
  const h=panelHarness();await loadBusiness(h);
  const comparison=h.ui.loadBusinessStyleExamples(),request=h.take('/api/v1/tenants/tenant-a/ai/style-examples','POST');
  assert.deepEqual(JSON.parse(request.options.body),{style_rules:profile().style_rules});
  request.respond({examples:['warm','professional','relaxed','direct'].map(style=>({style,label:style,answer:'Corte: *R$ 35,00* <img src=x>'}))});await comparison;
  assert.equal(h.node('businessStyleExamples').children.length,4);assert.match(contentOf(h.node('businessStyleExamples')),/R\$ 35,00/);
  assert.equal(h.node('businessStyleExamples').children[0].children[1].children[1].children.filter(x=>x.tagName==='STRONG').length,1);
  const second=h.ui.loadBusinessStyleExamples(),old=h.take('/api/v1/tenants/tenant-a/ai/style-examples','POST');
  h.node('bizStyle').value='direct';h.ui.businessChanged();old.respond({examples:[{label:'VELHO',answer:'DADO ANTIGO'}]});await second;
  assert.doesNotMatch(contentOf(h.node('businessStyleExamples')),/DADO ANTIGO/);assert.equal(h.node('businessExamples').disabled,false);
});

test('texto envia UUID único e usa rascunho somente após escolha explícita',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const loading=h.ui.aiLoadProfiles();h.take(businessUrl('tenant-a')).respond(snapshot());await loading;
  const first=submitPreview(h,'qual o preço?'),one=h.take(previewUrl('tenant-a'),'POST');const firstBody=JSON.parse(one.options.body);
  assert.equal(firstBody.profile_version,null);assert.equal(firstBody.style_rules,null);assert.match(firstBody.request_id,/^[0-9a-f-]{36}$/);
  h.node('aiProfile').value=draftId;h.node('aiProfile').listeners.change[0]();h.node('aiStyle').value='direct';h.node('aiStyle').listeners.change[0]();
  const next=submitPreview(h,'e agora?'),two=h.take(previewUrl('tenant-a'),'POST');const body=JSON.parse(two.options.body);
  assert.equal(body.profile_version,draftId);assert.deepEqual(body.style_rules,{...profile().style_rules,style:'direct'});
  assert.notEqual(body.request_id,firstBody.request_id);assert.notEqual(body.session,firstBody.session);
  one.respond({answer:'ANTIGA'});await first;assert.equal(h.node('aiSend').disabled,true);
  two.respond({answer:'Nova',step:'catalog'});await next;assert.doesNotMatch(contentOf(h.node('aiMessages')),/ANTIGA/);
});

test('runtime desligado não oferece rascunho nem estilos e logout limpa nomes e versões',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';const loading=h.ui.aiLoadProfiles();
  h.take(businessUrl('tenant-a')).respond(snapshot('Empresa privada',{runtime_connected:false}));await loading;
  assert.equal(h.node('aiProfile').options.length,1);assert.equal(h.node('aiProfile').disabled,true);assert.equal(h.node('aiStyle').disabled,true);
  await loadBusiness(h);h.ui.showLogin();assert.equal(h.node('bizName').value,'');assert.equal(h.node('businessTenant').value,'');assert.equal(h.node('aiTenant').value,'');
  assert.equal(h.node('aiProfile').options.length,1);assert.doesNotMatch(h.node('aiMode').textContent,/Empresa privada/);
});

test('carregamento de rascunho bloqueia texto e áudio até versão explícita estar pronta',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const loading=h.ui.aiLoadProfiles(draftId),profileRequest=h.take(businessUrl('tenant-a'));
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiAudioSend').disabled,true);
  await submitPreview(h,'texto durante a consulta');await submitAudio(h,{name:'teste.wav',size:100,type:'audio/wav'});
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);assert.equal(h.node('aiMessages').children.length,0);
  profileRequest.respond(snapshot());await loading;
  assert.equal(h.node('aiProfile').value,draftId);assert.match(h.node('aiMode').textContent,/rascunho salvo/);
  const sending=submitPreview(h,'teste do rascunho'),request=h.take(previewUrl('tenant-a'),'POST');
  assert.equal(JSON.parse(request.options.body).profile_version,draftId);request.respond({answer:'Resposta do rascunho',step:'menu'});await sending;
});

test('Testar rascunho bloqueia o tenant antigo desde a navegação, antes mesmo da lista carregar',async()=>{
  const h=panelHarness();await loadBusiness(h,'tenant-b',snapshot('Empresa B'));
  h.node('aiTenant').value='tenant-a';h.ui.aiBubble('Conversa anterior da empresa A','in');
  h.ui.openBusinessPreview(true);
  assert.equal(h.node('aiSend').disabled,true);assert.equal(h.node('aiMessages').children.length,0);
  await submitPreview(h,'durante lista de empresas');assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  h.take('/api/v1/tenants').respond([{id:'tenant-a',name:'Empresa A'},{id:'tenant-b',name:'Empresa B'}]);await tick();
  assert.equal(h.node('aiTenant').value,'tenant-b');assert.equal(h.node('aiSend').disabled,true);
  await submitPreview(h,'durante carregamento do rascunho');assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  h.take(businessUrl('tenant-b')).respond(snapshot('Empresa B'));h.take(aiStatusUrl('tenant-b')).respond({configured:true,preview_enabled:true});await tick();
  assert.equal(h.node('aiSend').disabled,false);assert.equal(h.node('aiProfile').value,draftId);
  const sending=submitPreview(h,'rascunho B'),request=h.take(previewUrl('tenant-b'),'POST');
  assert.equal(JSON.parse(request.options.body).profile_version,draftId);request.respond({answer:'B',step:'menu'});await sending;
});

test('erro antigo de perfil não libera tenant novo e rascunho ausente nunca vira atual silenciosamente',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  const oldLoad=h.ui.aiLoadProfiles(draftId),old=h.take(businessUrl('tenant-a'));
  h.node('aiTenant').value='tenant-b';const newLoad=h.ui.aiLoadProfiles(draftId),current=h.take(businessUrl('tenant-b'));
  old.reject(Error('erro antigo A'));await oldLoad;assert.equal(h.node('aiSend').disabled,true);
  current.respond(snapshot('Empresa B',{draft:null}));await newLoad;
  await readyAi(h,'tenant-b');
  assert.equal(h.node('aiSend').disabled,true);assert.match(h.node('aiMode').textContent,/Escolha explicitamente/);
  await submitPreview(h,'não pode usar atual automaticamente');assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  h.node('aiProfile').value='';h.node('aiProfile').listeners.change[0]();
  assert.equal(h.node('aiSend').disabled,false);
  const sending=submitPreview(h,'agora escolhi atual'),request=h.take(previewUrl('tenant-b'),'POST');
  assert.equal(JSON.parse(request.options.body).profile_version,null);request.respond({answer:'Atual B',step:'menu'});await sending;
});

function submitAudio(h,file){
  h.node('aiAudioFile').files=[file];return h.node('aiAudioForm').listeners.submit[0]({preventDefault(){}});
}
function takeAudio(h,tenant='tenant-a'){
  const request=h.requests.find(r=>!r.taken&&r.url.startsWith(previewUrl(tenant)+'/audio?'));
  assert.ok(request,'missing audio upload');return h.take(request.url,'POST');
}
test('áudio envia bytes uma única vez com sessão, UUID, versão e CSRF; mostra transcrição segura',async()=>{
  const h=panelHarness();h.ui.authenticate();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const loading=h.ui.aiLoadProfiles();h.take(businessUrl('tenant-a')).respond(snapshot());await loading;
  h.node('aiProfile').value=draftId;h.node('aiProfile').listeners.change[0]();
  const file={name:'consulta.ogg',size:2048,type:'audio/ogg'},sending=submitAudio(h,file),request=takeAudio(h);
  assert.equal(request.options.body,file);assert.equal(request.options.headers['Content-Type'],'audio/ogg');assert.equal(request.options.headers['X-Orion-CSRF'],'synthetic-csrf');
  assert.equal(request.options.credentials,'same-origin');assert.equal(request.options.cache,'no-store');
  const params=new URLSearchParams(request.url.split('?')[1]);assert.equal(params.get('profile_version'),draftId);assert.match(params.get('request_id'),/^[0-9a-f-]{36}$/);
  assert.equal(params.get('reset'),'false');assert.match(params.get('session'),/^[a-z0-9-]{8,64}$/);
  await submitPreview(h,'não pode concorrer com áudio');await submitAudio(h,file);assert.equal(h.requests.filter(x=>x.options.method==='POST').length,1);
  request.respond({transcript:'Quanto custa? <img src=x>',answer:'Total: *R$ 55,00*',step:'catalog',input_kind:'audio'});await sending;
  assert.match(contentOf(h.node('aiMessages')),/Transcrição: Quanto custa\? <img src=x>/);assert.match(contentOf(h.node('aiMessages')),/Total: R\$ 55,00/);
  assert.equal(h.node('aiAudioFile').value,'');assert.equal(h.node('aiAudioSend').disabled,false);
});

test('áudio rejeita tamanho ou formato inválido antes do envio e não repete falha incerta',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  for(const file of [{name:'x',size:0,type:'audio/ogg'},{name:'x',size:2_000_001,type:'audio/ogg'},{name:'x.exe',size:99,type:'application/octet-stream'}])await submitAudio(h,file);
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0);
  const sending=submitAudio(h,{name:'x.mp3',size:100,type:'audio/mp3'}),request=takeAudio(h);assert.equal(request.options.headers['Content-Type'],'audio/mpeg');
  request.reject(Error('Conexão interrompida'));await sending;await tick();
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,1);assert.match(h.node('aiDiagnostics').textContent,/Conexão interrompida/);
});

test('trocar versão ou reiniciar durante áudio descarta transcrição, erro e finally antigos',async()=>{
  const h=panelHarness();h.node('aiTenant').value='tenant-a';
  await readyAi(h);
  const first=submitAudio(h,{name:'old.wav',size:100,type:'audio/wav'}),old=takeAudio(h);
  h.node('aiReset').listeners.click[0]();h.take(businessUrl('tenant-a')).respond(snapshot());await tick();
  const next=submitPreview(h,'nova conversa'),current=h.take(previewUrl('tenant-a'),'POST');
  old.respond({transcript:'DADO PRIVADO ANTIGO',answer:'VELHA RESPOSTA'});await first;
  assert.doesNotMatch(contentOf(h.node('aiMessages')),/PRIVADO|VELHA/);assert.equal(h.node('aiAudioSend').disabled,true);
  current.respond({answer:'Resposta atual',step:'menu'});await next;assert.equal(h.node('aiAudioSend').disabled,false);
  h.node('aiStyle').value='direct';h.node('aiStyle').listeners.change[0]();
  const count=h.requests.length;await submitAudio(h,{name:'x.ogg',size:100,type:'audio/ogg'});assert.equal(h.requests.length,count);
  assert.match(h.node('aiDiagnostics').textContent,/Usar o estilo da versão/);
});

const productId='44444444-4444-4444-8444-444444444444',variantId='55555555-5555-4555-8555-555555555555';
const quoteId='66666666-6666-4666-8666-666666666666',orderId='77777777-7777-4777-8777-777777777777';
const proposalToken='synthetic_quote_confirmation_token_12345678901234567890';
const commerceUrl=(tenant,suffix)=>'/api/v1/tenants/'+tenant+'/commerce'+suffix;
const commerceAccess=(overrides={})=>({enabled:true,payments_enabled:false,assistant_connected:false,
  access:{role:'owner',can_edit_catalog:true,can_create_orders:true},...overrides});
const commercialProduct=(overrides={})=>({id:productId,revision:4,name:'Shampoo oficial',description:'Descrição aprovada',
  synonyms:['limpeza suave'],faqs:[{question:'Como usar?',answer:'Siga o rótulo.'}],official_benefits:['Benefício cadastrado'],active:true,
  variants:[{id:variantId,product_id:productId,revision:5,name:'250 mL',sku:'SH-250',attributes:{Volume:'250 mL'},price_kind:'fixed',unit_price_cents:1000,active:true,
    stock_mode:'tracked',quantity_on_hand:10,reserved_quantity:2,observed_at:new Date(Date.now()-60000).toISOString(),valid_until:new Date(Date.now()+3600000).toISOString(),stock_source:'Contagem física',inventory_revision:6}],...overrides});
const commercialQuote=(overrides={})=>({id:quoteId,kind:'quote',expires_at:new Date(Date.now()+15*60000).toISOString(),
  confirmation_token:proposalToken,customer_name:'Cliente sintético',contact_e164:'+5567999999999',currency:'BRL',total_cents:2500,minimum_total_cents:2500,
  confirmation_requirements_met:true,limitations:[],items:[{product_id:productId,variant_id:variantId,product_name:'Shampoo oficial',variant_name:'250 mL',attributes:{Volume:'250 mL'},quantity:2,price_kind:'fixed',unit_price_cents:1250,total_cents:2500,minimum_total_cents:2500,
    stock:{mode:'tracked',available_quantity:8,source:'Contagem física',observed_at:new Date(Date.now()-60000).toISOString(),valid_until:new Date(Date.now()+3600000).toISOString()}}],...overrides});
async function loadCommerce(h,tenant='tenant-a',status=commerceAccess(),products=[commercialProduct()]){
  h.node('comTenant').value=tenant;const loading=h.ui.loadCommerceTenant();
  h.take(commerceUrl(tenant,'/status')).respond(status);await tick();
  if(status.enabled===true)h.take(commerceUrl(tenant,'/products')).respond(products);
  await loading;
}
function addCommerceItem(h,quantity='2'){
  h.node('comBasketVariant').value=variantId;h.node('comBasketQuantity').value=quantity;h.ui.commerceAddBasket();
  h.node('comCustomer').value='Cliente sintético';h.node('comContact').value='+5567999999999';
}
async function saveCommerceQuote(h,result=commercialQuote()){
  const saving=h.ui.commerceCalculate(true);const request=h.take(commerceUrl('tenant-a','/quotes'),'POST');request.respond(result);await saving;return request;
}
async function createCommerceOrder(h){
  const creating=h.ui.commerceCreateOrder(),request=h.take(commerceUrl('tenant-a','/orders'),'POST');
  request.respond({id:orderId,quote_id:quoteId,status:'draft',payment_status:'unverified',revision:1});await creating;return request;
}

test('catálogo comercial exige status habilitado antes de ler produtos e mantém aviso com flag desligada',async()=>{
  const h=panelHarness();h.node('comTenant').value='tenant-a';const loading=h.ui.loadCommerceTenant();
  assert.equal(h.node('comProductFields').disabled,true);assert.equal(h.node('comOrderConfirm').disabled,true);
  h.take(commerceUrl('tenant-a','/status')).respond({detail:'Catálogo não habilitado'},423);await loading;
  assert.equal(h.requests.some(x=>x.url.endsWith('/commerce/products')),false);
  assert.equal(h.node('comBody').classList.contains('hidden'),true);assert.match(h.node('comUnavailable').textContent,/não está habilitado/);
  await h.ui.commerceSave('product');assert.equal(h.requests.some(x=>x.options.method==='POST'),false);
});

test('status distingue catálogo manual de atendimento automático; leitura não ganha permissão de edição',async()=>{
  const h=panelHarness();await loadCommerce(h,'tenant-a',commerceAccess({access:{role:'viewer',can_edit_catalog:false,can_create_orders:false}}));
  assert.match(h.node('comAssistant').textContent,/automático ainda está desativado/);assert.equal(h.node('comProductFields').disabled,true);
  addCommerceItem(h);const count=h.requests.length;
  await h.ui.commerceSave('product');await h.ui.commerceCalculate(true);await h.ui.commerceCreateOrder();await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,count);
  const estimate=h.ui.commerceCalculate(),request=h.take(commerceUrl('tenant-a','/estimate'),'POST');
  request.respond({...commercialQuote(),kind:'estimate'});await estimate;assert.match(contentOf(h.node('comQuoteSummary')),/Consulta sem gravação/);
  assert.equal(h.node('comQuoteCreate').disabled,true);
});

test('membro só busca empresas atribuídas no catálogo comercial',async()=>{
  const h=panelHarness();h.ui.setRole('member');const loading=h.ui.loadCommerceTenants();
  h.take('/api/v1/account/businesses').respond([{id:'tenant-m',name:'Empresa atribuída'}]);await tick();
  h.take(commerceUrl('tenant-m','/status')).respond(commerceAccess());await tick();h.take(commerceUrl('tenant-m','/products')).respond([]);await loading;
  assert.equal(h.requests.some(x=>x.url==='/api/v1/tenants'),false);assert.equal(h.node('comTenant').value,'tenant-m');
});

test('produto novo é inativo; gravação preserva fatos oficiais e nunca duplica POST',async()=>{
  const h=panelHarness();await loadCommerce(h);h.node('comProductName').value='Produto novo';h.node('comDescription').value='Descrição verdadeira';h.node('comSynonyms').value='nome A\nnome B';h.node('comBenefits').value='Benefício A';
  const saving=h.ui.commerceSave('product'),request=h.take(commerceUrl('tenant-a','/products'),'POST');
  assert.deepEqual(JSON.parse(request.options.body),{name:'Produto novo',description:'Descrição verdadeira',synonyms:['nome A','nome B'],official_benefits:['Benefício A'],faqs:[],active:false});
  await h.ui.commerceSave('product');assert.equal(h.requests.filter(x=>x.url.endsWith('/commerce/products')&&x.options.method==='POST').length,1);
  request.respond({id:productId,revision:1});await tick();h.take(commerceUrl('tenant-a','/products')).respond([commercialProduct({name:'Produto novo',active:false,variants:[]})]);await saving;
  assert.equal(h.node('comProductName').value,'Produto novo');assert.equal(h.node('comProductActive').checked,false);
  h.ui.commerceSelectProduct(productId);assert.deepEqual(JSON.parse(JSON.stringify(h.ui.commerceProductPayload())).faqs,[{question:'Como usar?',answer:'Siga o rótulo.'}]);
});

test('variante não transforma preço ausente em zero e serializa os quatro tipos corretamente',async()=>{
  const h=panelHarness();await loadCommerce(h);h.ui.commerceSelectProduct(productId);h.node('comVariantName').value='Nova opção';
  for(const kind of ['on_request','custom']){h.node('comPriceKind').value=kind;h.node('comPrice').value='999,99';assert.equal(h.ui.commerceVariantPayload().unit_price_cents,null);}
  h.node('comPriceKind').value='fixed';h.node('comPrice').value='';const count=h.requests.length;await h.ui.commerceSave('variant');assert.equal(h.requests.length,count);assert.match(h.node('comFeedback').textContent,/Preço vazio não é zero/);
  h.node('comPrice').value='0';assert.equal(h.ui.commerceVariantPayload().unit_price_cents,0,'zero só é aceito se informado explicitamente');
  h.node('comPriceKind').value='from';h.node('comPrice').value='12,90';assert.equal(h.ui.commerceVariantPayload().unit_price_cents,1290);
  h.node('comPrice').value='1,234';assert.throws(()=>h.ui.commerceVariantPayload(),/valor em reais/);
});

test('editar variante envia revisão, SKU, atributos e preço sem campos de outro tenant',async()=>{
  const h=panelHarness();await loadCommerce(h);h.ui.commerceSelectProduct(productId);h.ui.commerceSelectVariant(variantId);h.node('comPrice').value='19,95';
  const saving=h.ui.commerceSave('variant'),request=h.take(commerceUrl('tenant-a','/products/'+productId+'/variants/'+variantId),'PUT');
  assert.deepEqual(JSON.parse(request.options.body),{expected_revision:5,variant:{name:'250 mL',sku:'SH-250',attributes:{Volume:'250 mL'},price_kind:'fixed',unit_price_cents:1995,active:true}});
  request.respond({id:variantId,revision:6});await tick();h.take(commerceUrl('tenant-a','/products')).respond([commercialProduct()]);await saving;
});

test('estoque desconhecido remove metadados; contagem exige origem, data e validade e usa revisão do estoque',async()=>{
  const h=panelHarness();await loadCommerce(h);h.ui.commerceSelectProduct(productId);h.ui.commerceSelectVariant(variantId);
  h.node('comStockMode').value='unknown';assert.deepEqual(JSON.parse(JSON.stringify(h.ui.commerceStockPayload())),{mode:'unknown',quantity_on_hand:null,observed_at:null,source:null,valid_until:null});
  h.node('comStockMode').value='tracked';h.node('comStockQuantity').value='4';h.node('comStockSource').value='Contagem física';h.node('comStockObserved').value='2026-10-17T10:00';h.node('comStockUntil').value='2026-10-19T10:00';
  assert.throws(()=>h.ui.commerceStockPayload(),/até 24 horas/);h.node('comStockUntil').value='2026-10-17T12:00';
  const saving=h.ui.commerceSave('stock'),request=h.take(commerceUrl('tenant-a','/variants/'+variantId+'/stock'),'PUT');const payload=JSON.parse(request.options.body);
  assert.equal(payload.expected_revision,6);assert.equal(payload.stock.quantity_on_hand,4);assert.equal(payload.stock.source,'Contagem física');assert.match(payload.stock.observed_at,/Z$/);
  request.respond({detail:'Estoque alterado'},409);await saving;assert.equal(h.node('comStockQuantity').value,'4');assert.equal(h.node('comStockFields').disabled,true);assert.match(h.node('comFeedback').textContent,/Nenhuma nova tentativa/);
  const count=h.requests.length;await h.ui.commerceSave('stock');assert.equal(h.requests.length,count);
});

test('consulta envia somente itens e quantidades; preços e totais exibidos vêm da resposta do servidor',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);
  const estimating=h.ui.commerceCalculate(),request=h.take(commerceUrl('tenant-a','/estimate'),'POST');
  assert.deepEqual(JSON.parse(request.options.body),{items:[{variant_id:variantId,quantity:2}]});
  request.respond({...commercialQuote(),kind:'estimate'});await estimating;
  assert.match(contentOf(h.node('comQuoteSummary')),/25,00/);assert.match(contentOf(h.node('comQuoteSummary')),/12,50/);
  assert.doesNotMatch(contentOf(h.node('comQuoteSummary')),/20,00/,'metadata anterior de10reais não deve ser somada no cliente');
  assert.equal(h.requests.some(x=>x.url.endsWith('/orders')||x.url.endsWith('/quotes')),false);assert.equal(h.node('comOrderCreate').disabled,true);
});

test('preço a partir de e preço desconhecido nunca são apresentados como total fixo',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);
  const estimate=h.ui.commerceCalculate(),request=h.take(commerceUrl('tenant-a','/estimate'),'POST');
  const value=commercialQuote({kind:'estimate',total_cents:null,minimum_total_cents:2000,confirmation_requirements_met:false,limitations:['non_fixed_price','unknown_stock']});value.items[0]={...value.items[0],price_kind:'from',unit_price_cents:1000,total_cents:null};
  request.respond(value);await estimate;assert.match(contentOf(h.node('comQuoteSummary')),/Valor mínimo dos itens/);assert.match(contentOf(h.node('comQuoteSummary')),/total final exige análise/);assert.equal(h.node('comOrderConfirm').disabled,true);
  const next=h.ui.commerceCalculate(),unknown=h.take(commerceUrl('tenant-a','/estimate'),'POST');unknown.respond({...value,total_cents:null,minimum_total_cents:null});await next;
  assert.match(contentOf(h.node('comQuoteSummary')),/Total sob consulta/);assert.doesNotMatch(contentOf(h.node('comQuoteSummary')),/Valor dos itens: R\$\s*0/);
});

test('pedido exige proposta salva e confirmação explícita correlacionada; pagamento continua não verificado',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h);
  assert.doesNotMatch(contentOf(h.node('comQuoteSummary')),new RegExp(proposalToken));assert.equal(h.node('comOrderConfirm').disabled,true);
  const created=await createCommerceOrder(h);const payload=JSON.parse(created.options.body);assert.equal(payload.quote_id,quoteId);assert.match(payload.idempotency_key,/^[0-9a-f-]{36}$/);
  const before=h.requests.length;await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,before,'checkbox precisa ser marcado antes de confirmar');
  h.node('comAccept').checked=true;h.node('comAccept').listeners.change[0]();assert.equal(h.node('comOrderConfirm').disabled,false);
  const confirming=h.ui.commerceConfirmOrder(),request=h.take(commerceUrl('tenant-a','/orders/'+orderId+'/confirm'),'POST');
  assert.deepEqual(JSON.parse(request.options.body),{expected_revision:1,confirmation_token:proposalToken});
  await h.ui.commerceConfirmOrder();assert.equal(h.requests.filter(x=>x.url.endsWith('/confirm')).length,1);
  request.respond({id:orderId,quote_id:quoteId,status:'confirmed',payment_status:'unverified',revision:2});await confirming;
  assert.match(h.node('comOrderSummary').textContent,/confirmado pelo servidor/);assert.match(h.node('comOrderSummary').textContent,/Pagamento não verificado/);assert.equal(h.node('comOrderConfirm').disabled,true);
  assert.doesNotMatch(contentOf(h.node('comQuoteSummary'))+h.node('comOrderSummary').textContent,new RegExp(proposalToken));
});

test('proposta expirada, estoque incerto e telefone ausente impedem confirmação mesmo com checkbox marcado',async()=>{
  for(const overrides of [{confirmation_requirements_met:false,limitations:['unknown_stock']},{contact_e164:null}]){
    const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h,commercialQuote(overrides));await createCommerceOrder(h);h.node('comAccept').checked=true;
    const count=h.requests.length;await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,count);assert.equal(h.node('comOrderConfirm').disabled,true);
  }
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h);await createCommerceOrder(h);h.node('comAccept').checked=true;h.advanceTime(16*60000);
  const count=h.requests.length;await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,count,'servidor continua revalidando, e cliente também bloqueia prazo vencido');
});

test('alterar cliente ou cesta invalida o resumo confirmável e resultado de consulta atrasado',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h);await createCommerceOrder(h);h.node('comAccept').checked=true;
  h.node('comContact').value='+5511999999999';h.node('comContact').listeners.input[0]();assert.equal(h.node('comOrderConfirm').disabled,true);
  const count=h.requests.length;await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,count);
  const estimating=h.ui.commerceCalculate(),old=h.take(commerceUrl('tenant-a','/estimate'),'POST');h.ui.commercePlanningChanged();old.respond({...commercialQuote(),kind:'estimate'});await estimating;
  assert.doesNotMatch(contentOf(h.node('comQuoteSummary')),/25,00/);assert.equal(h.node('comEstimate').disabled,false);
});

test('consulta de pedido existente não inventa token e falha de confirmação nunca repete POST',async()=>{
  const h=panelHarness();await loadCommerce(h);h.node('comLookupOrder').value=orderId;
  const reading=h.ui.commerceRead('order'),request=h.take(commerceUrl('tenant-a','/orders/'+orderId));const {confirmation_token,...saved}=commercialQuote();
  request.respond({id:orderId,quote_id:quoteId,status:'draft',payment_status:'unverified',revision:1,quote:saved});await reading;
  assert.equal(h.node('comOrderConfirm').disabled,true);h.node('comAccept').checked=true;const count=h.requests.length;await h.ui.commerceConfirmOrder();assert.equal(h.requests.length,count);
  addCommerceItem(h);await saveCommerceQuote(h);await createCommerceOrder(h);h.node('comAccept').checked=true;
  const confirming=h.ui.commerceConfirmOrder(),post=h.take(commerceUrl('tenant-a','/orders/'+orderId+'/confirm'),'POST');post.reject(Error('Conexão interrompida após envio'));await confirming;await tick();
  assert.equal(h.requests.filter(x=>x.url.endsWith('/confirm')).length,1);assert.equal(h.node('comOrderConfirm').disabled,true);assert.match(h.node('comFeedback').textContent,/Consulte o registro/);
});

test('pedido de criação incerta é recuperado por orçamento apenas com GET e sem nova confirmação',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h);
  const creating=h.ui.commerceCreateOrder();h.take(commerceUrl('tenant-a','/orders'),'POST').reject(Error('Conexão interrompida após envio'));await creating;
  assert.equal(h.node('comLookupQuote').value,quoteId);assert.equal(h.node('comOrderCreate').disabled,true);
  const reading=h.ui.commerceRead('by-quote'),request=h.take(commerceUrl('tenant-a','/orders/by-quote/'+quoteId));
  const {confirmation_token,...quote}=commercialQuote();request.respond({id:orderId,quote_id:quoteId,status:'draft',revision:1,payment_status:'unverified',quote});await reading;
  assert.equal(h.node('comLookupOrder').value,orderId);assert.match(h.node('comOrderSummary').textContent,/rascunho/);
  assert.equal(h.node('comOrderConfirm').disabled,true);await h.ui.commerceCreateOrder();await h.ui.commerceConfirmOrder();
  assert.equal(h.requests.filter(x=>x.options.method==='POST').length,2,'somente orçamento e tentativa original de criação');
  assert.match(h.node('comFeedback').textContent,/Revise um orçamento/);
});

test('recarregar na mesma empresa preserva IDs mesmo com falha e leitura não recria pedido sem token',async()=>{
  const h=panelHarness();await loadCommerce(h);h.node('comLookupQuote').value=quoteId;h.node('comLookupOrder').value=orderId;
  const refresh=h.ui.loadCommerceTenant({preserveLookups:true});h.take(commerceUrl('tenant-a','/status')).reject(Error('Sem conexão'));await refresh;
  assert.equal(h.node('comLookupQuote').value,quoteId);assert.equal(h.node('comLookupOrder').value,orderId);
  await loadCommerce(h);h.node('comLookupQuote').value=quoteId;const reading=h.ui.commerceRead('quote');
  const {confirmation_token,...quote}=commercialQuote();h.take(commerceUrl('tenant-a','/quotes/'+quoteId)).respond(quote);await reading;
  assert.equal(h.node('comOrderCreate').disabled,true);const count=h.requests.length;await h.ui.commerceCreateOrder();assert.equal(h.requests.length,count);
});

test('recuperação por orçamento rejeita outro registro e ignora resposta de tenant anterior',async()=>{
  const h=panelHarness();await loadCommerce(h);h.node('comLookupQuote').value=quoteId;const reading=h.ui.commerceRead('by-quote');
  const request=h.take(commerceUrl('tenant-a','/orders/by-quote/'+quoteId));request.respond({id:orderId,quote_id:'outro',quote:commercialQuote()});await reading;
  assert.match(h.node('comFeedback').textContent,/não corresponde/);assert.equal(h.node('comLookupOrder').value,'');
  const late=h.ui.commerceRead('by-quote'),old=h.take(commerceUrl('tenant-a','/orders/by-quote/'+quoteId));await loadCommerce(h,'tenant-b');
  old.respond({id:orderId,quote_id:quoteId,status:'confirmed',quote:commercialQuote()});await late;
  assert.equal(h.node('comLookupOrder').value,'');assert.doesNotMatch(h.node('comOrderSummary').textContent,/confirmado/);
});

test('resposta de outra empresa e finally antigos não contaminam catálogo ou liberam mutação nova',async()=>{
  const h=panelHarness();await loadCommerce(h);h.ui.commerceSelectProduct(productId);h.node('comProductName').value='Edição A';
  const oldSave=h.ui.commerceSave('product'),old=h.take(commerceUrl('tenant-a','/products/'+productId),'PUT');
  await loadCommerce(h,'tenant-b',commerceAccess(),[commercialProduct({name:'Empresa B'})]);h.ui.commerceSelectProduct(productId);h.node('comProductName').value='Edição B';
  const newSave=h.ui.commerceSave('product'),current=h.take(commerceUrl('tenant-b','/products/'+productId),'PUT');
  old.respond({id:productId,revision:5});await oldSave;assert.equal(h.node('comProductFields').disabled,true);assert.equal(h.node('comProductName').value,'Edição B');
  assert.equal(h.requests.filter(x=>x.url===commerceUrl('tenant-a','/products')).length,1,'resultado velho não pode reler tenant antigo');
  current.respond({id:productId,revision:5});await tick();h.take(commerceUrl('tenant-b','/products')).respond([commercialProduct({name:'Edição B'})]);await newSave;
  assert.equal(h.node('comProductName').value,'Edição B');assert.equal(h.node('comProductFields').disabled,false);
});

test('logout apaga catálogo, proposta e confirmação pendente; respostas antigas permanecem ignoradas',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);await saveCommerceQuote(h);await createCommerceOrder(h);h.node('comAccept').checked=true;
  const confirming=h.ui.commerceConfirmOrder(),old=h.take(commerceUrl('tenant-a','/orders/'+orderId+'/confirm'),'POST');h.ui.showLogin();
  old.respond({id:orderId,quote_id:quoteId,status:'confirmed',payment_status:'unverified',revision:2});await confirming;
  assert.equal(h.node('comProductName').value,'');assert.equal(h.node('comLookupOrder').value,'');assert.equal(h.node('comTenant').value,'');assert.equal(h.node('comOrderConfirm').disabled,true);
  assert.doesNotMatch(h.node('comOrderSummary').textContent,/confirmado pelo servidor/);
});

test('nomes e respostas comerciais usam texto seguro e não criam HTML a partir do catálogo',async()=>{
  const h=panelHarness();await loadCommerce(h);addCommerceItem(h);const estimating=h.ui.commerceCalculate(),request=h.take(commerceUrl('tenant-a','/estimate'),'POST');
  const result=commercialQuote({kind:'estimate'});result.items[0].product_name='<img src=x onerror=alert(1)>';result.items[0].attributes={'<script>':'alert(2)'};request.respond(result);await estimating;
  assert.match(contentOf(h.node('comQuoteSummary')),/<img src=x/);assert.match(contentOf(h.node('comQuoteSummary')),/<script>/);
  assert.ok(h.node('comQuoteSummary').children.every(node=>['STRONG','P'].includes(node.tagName)&&!Object.hasOwn(node,'innerHTML')));
});

const complementId='88888888-8888-4888-8888-888888888888';
const relatedProduct=()=>commercialProduct({id:complementId,name:'Complemento oficial',variants:[]});

test('relação complementar exige outro produto da empresa e aprovação expressa; revogação não vira oferta',async()=>{
  const h=panelHarness();await loadCommerce(h,'tenant-a',commerceAccess(),[commercialProduct(),relatedProduct()]);h.ui.commerceSelectProduct(productId);
  assert.equal(h.node('comComplementApproved').checked,false);
  h.node('comComplementTarget').value=productId;h.node('comComplementReason').value='Justificativa oficial';const count=h.requests.length;
  await h.ui.commerceSaveComplement();assert.equal(h.requests.length,count);
  h.node('comComplementTarget').value='produto-outro-tenant';await h.ui.commerceSaveComplement();assert.equal(h.requests.length,count);
  h.node('comComplementTarget').value=complementId;
  const saving=h.ui.commerceSaveComplement(),put=h.take(commerceUrl('tenant-a','/products/'+productId+'/complements'),'PUT');
  assert.deepEqual(JSON.parse(put.options.body),{target_product_id:complementId,reason:'Justificativa oficial',approved:false});
  assert.equal(h.node('comComplementFields').disabled,true);await h.ui.commerceSaveComplement();
  put.respond({source_product_id:productId,target_product_id:complementId,reason:'Justificativa oficial',approved:false});await tick();
  h.take(commerceUrl('tenant-a','/products/'+productId+'/complements')).respond([]);await saving;
  assert.match(h.node('comFeedback').textContent,/Revogação confirmada/);assert.equal(h.node('comComplementApproved').checked,false);
  assert.equal(h.requests.filter(x=>x.options.method==='PUT').length,1);
  h.node('comComplementApproved').checked=true;const approving=h.ui.commerceSaveComplement(),approval=h.take(commerceUrl('tenant-a','/products/'+productId+'/complements'),'PUT');
  assert.equal(JSON.parse(approval.options.body).approved,true);
  approval.respond({source_product_id:productId,target_product_id:complementId,reason:'Justificativa oficial',approved:true});await tick();
  h.take(commerceUrl('tenant-a','/products/'+productId+'/complements')).respond([{target_product_id:complementId,name:'Complemento oficial',reason:'Justificativa oficial'}]);await approving;
  assert.match(h.node('comFeedback').textContent,/modo comercial contextual/);assert.doesNotMatch(h.node('comFeedback').textContent,/desconto/);
});

test('leitor consulta relações aprovadas sem poder gravar ou inferir desconto e estoque',async()=>{
  const h=panelHarness();await loadCommerce(h,'tenant-a',commerceAccess({access:{role:'viewer',can_edit_catalog:false,can_create_orders:false}}),[commercialProduct(),relatedProduct()]);h.ui.commerceSelectProduct(productId);
  assert.equal(h.node('comComplementFields').disabled,true);const reading=h.ui.commerceReadComplements();
  h.take(commerceUrl('tenant-a','/products/'+productId+'/complements')).respond([{target_product_id:complementId,name:'<b>Complemento</b>',reason:'<script>oficial</script>'}]);await reading;
  assert.match(h.node('comComplementList').textContent,/<b>Complemento<\/b>/);assert.ok(!Object.hasOwn(h.node('comComplementList').children[0],'innerHTML'));
  h.node('comComplementTarget').value=complementId;h.node('comComplementReason').value='Teste';h.node('comComplementApproved').checked=true;
  const count=h.requests.length;await h.ui.commerceSaveComplement();assert.equal(h.requests.length,count);
});

test('resultado complementar atrasado não atravessa seleção de produto ou tenant e erro não repete gravação',async()=>{
  const h=panelHarness();await loadCommerce(h,'tenant-a',commerceAccess(),[commercialProduct(),relatedProduct()]);h.ui.commerceSelectProduct(productId);
  const reading=h.ui.commerceReadComplements(),old=h.take(commerceUrl('tenant-a','/products/'+productId+'/complements'));
  h.ui.commerceSelectProduct(complementId);old.respond([{target_product_id:complementId,name:'Relação antiga',reason:'Antigo'}]);await reading;
  assert.doesNotMatch(h.node('comComplementList').textContent,/Relação antiga/);
  h.ui.commerceSelectProduct(productId);h.node('comComplementTarget').value=complementId;h.node('comComplementReason').value='Oficial';h.node('comComplementApproved').checked=true;
  const saving=h.ui.commerceSaveComplement(),put=h.take(commerceUrl('tenant-a','/products/'+productId+'/complements'),'PUT');put.reject(Error('Resultado de rede incerto'));await saving;
  assert.equal(h.node('comComplementReason').value,'Oficial');assert.equal(h.node('comComplementFields').disabled,true);const count=h.requests.length;await h.ui.commerceSaveComplement();assert.equal(h.requests.length,count);
  await loadCommerce(h,'tenant-b',commerceAccess(),[commercialProduct(),relatedProduct()]);assert.equal(h.node('comComplementReason').value,'');assert.equal(h.node('comComplementApproved').checked,false);
});

async function loadLegacyCatalog(h,rows=[{id:'tenant-a',name:'Empresa A',role:'owner'}],resource='services'){
  h.ui.setRole('member');h.node('resource').value=resource;const loading=h.ui.loadTenants();h.take('/api/v1/account/businesses').respond(rows);await tick();
  if(rows.length)h.take('/api/v1/tenants/'+rows[0].id+'/'+resource).respond([]);await loading;
}
const recordEvent=(h,inputs=[])=>{const form=h.node('recordForm');form.querySelectorAll=()=>inputs;return {preventDefault(){},target:form}};

test('cadastros de membro usam somente empresas atribuídas e papel confirmado em cada empresa',async()=>{
  const h=panelHarness();await loadLegacyCatalog(h,[{id:'tenant-a',name:'Empresa A',role:'owner'},{id:'tenant-b',name:'Empresa B',role:'viewer'}]);
  assert.equal(h.requests.some(x=>x.url==='/api/v1/tenants'),false);assert.equal(h.node('recordFields').disabled,false);
  h.node('tenant').value='tenant-b';const reading=h.ui.loadRecords();assert.equal(h.node('recordFields').disabled,true);
  h.take('/api/v1/tenants/tenant-b/services').respond([{name:'Registro somente da empresa B'}]);await reading;
  assert.match(h.node('catalogAccess').textContent,/somente leitura/);const count=h.requests.length;await h.ui.submitRecord(recordEvent(h));assert.equal(h.requests.length,count);
  h.node('tenant').value='tenant-forged';await h.ui.loadRecords();await h.ui.submitRecord(recordEvent(h));assert.equal(h.requests.length,count);
});

test('editor pode cadastrar serviços mas somente proprietário altera acesso da equipe',async()=>{
  const h=panelHarness();await loadLegacyCatalog(h,[{id:'tenant-a',name:'Empresa A',role:'editor'}]);assert.equal(h.node('recordFields').disabled,false);
  h.node('resource').value='authorized_staff';const reading=h.ui.loadRecords();h.take('/api/v1/tenants/tenant-a/authorized_staff').respond([]);await reading;
  assert.equal(h.node('recordFields').disabled,true);assert.match(h.node('catalogAccess').textContent,/Somente o proprietário/);
  const count=h.requests.length;await h.ui.submitRecord(recordEvent(h));assert.equal(h.requests.length,count);
});

test('cadastro não transforma preço vazio em zero nem duplica mutação incerta',async()=>{
  const h=panelHarness();await loadLegacyCatalog(h);const price={name:'price_cents',type:'number',value:'',required:true};
  let event=recordEvent(h,[price]);const count=h.requests.length;await h.ui.submitRecord(event);assert.equal(h.requests.length,count);assert.match(h.node('notice').textContent,/não é zero/);
  price.value='3500';const saving=h.ui.submitRecord(event),request=h.take('/api/v1/tenants/tenant-a/services','POST');assert.deepEqual(JSON.parse(request.options.body),{price_cents:3500});
  assert.equal(h.node('recordFields').disabled,true);await h.ui.submitRecord(event);assert.equal(h.requests.filter(x=>x.options.method==='POST').length,1);
  request.reject(Error('Resultado incerto'));await saving;await h.ui.submitRecord(event);assert.equal(h.requests.filter(x=>x.options.method==='POST').length,1);assert.equal(h.node('recordFields').disabled,true);
  const refresh=h.ui.loadRecords();h.take('/api/v1/tenants/tenant-a/services').respond([{name:'Serviço salvo pelo servidor'}]);await refresh;assert.equal(h.node('recordFields').disabled,false);
});

test('respostas antigas de cadastros não cruzam tenant, categoria nem logout',async()=>{
  const h=panelHarness();await loadLegacyCatalog(h,[{id:'tenant-a',name:'Empresa A',role:'owner'},{id:'tenant-b',name:'Empresa B',role:'owner'}]);
  const oldRead=h.ui.loadRecords(),old=h.take('/api/v1/tenants/tenant-a/services');h.node('tenant').value='tenant-b';const newRead=h.ui.loadRecords();h.take('/api/v1/tenants/tenant-b/services').respond([{name:'Serviço B'}]);await newRead;
  old.respond([{name:'Serviço A privado'}]);await oldRead;assert.match(h.node('records').textContent,/Serviço B/);assert.doesNotMatch(h.node('records').textContent,/privado/);
  const saving=h.ui.submitRecord(recordEvent(h,[{name:'name',type:'text',value:'Profissional novo',required:true}])),post=h.take('/api/v1/tenants/tenant-b/services','POST');h.ui.showLogin();
  post.respond({id:'new-record'});await saving;assert.equal(h.node('records').textContent,'');assert.equal(h.node('tenant').value,'');assert.equal(h.node('recordFields').disabled,true);
});

test('capacidade de pedidos comerciais só aparece habilitada quando servidor oferece e nunca é pré-selecionada',async()=>{
  const h=panelHarness();const data=snapshot();data.supported_capabilities.push('product_orders');await loadBusiness(h,'tenant-a',data);
  assert.equal(h.node('bizCapProductOrders').checked,false);assert.equal(h.node('bizCapProductOrders').disabled,false);
  const profile=h.ui.collectBusinessProfile();assert.equal(profile.capabilities.includes('product_orders'),false);
  h.node('bizCapProductOrders').checked=true;assert.equal(h.ui.collectBusinessProfile().capabilities.includes('product_orders'),true);
});

test('diálogo HTML exige escolha, usa texto seguro, prende foco e Escape cancela restaurando o foco',async()=>{
  const h=panelHarness(),trigger=h.node('businessPublish');trigger.focus();
  const answer=h.ui.confirmAction('Confirma <img src=x> esta ação?');assert.equal(h.node('actionConfirm').open,true);assert.equal(h.activeElement(),h.node('actionConfirmCancel'));
  assert.equal(h.node('actionConfirmText').textContent,'Confirma <img src=x> esta ação?');assert.ok(!Object.hasOwn(h.node('actionConfirmText'),'innerHTML'));
  assert.equal(await h.ui.confirmAction('Não pode substituir a confirmação aberta'),false);assert.match(h.node('actionConfirmText').textContent,/Confirma <img/);
  let prevented=0;h.node('actionConfirm').listeners.keydown[0]({key:'Tab',shiftKey:true,preventDefault(){prevented++}});assert.equal(h.activeElement(),h.node('actionConfirmAccept'));
  h.node('actionConfirm').listeners.keydown[0]({key:'Tab',shiftKey:false,preventDefault(){prevented++}});assert.equal(h.activeElement(),h.node('actionConfirmCancel'));
  h.node('actionConfirm').listeners.keydown[0]({key:'Escape',preventDefault(){prevented++}});assert.equal(await answer,false);assert.equal(h.node('actionConfirm').open,false);assert.equal(h.activeElement(),trigger);assert.equal(prevented,3);
  assert.match(panel,/<dialog id="actionConfirm"[^>]*aria-modal="true"[^>]*aria-labelledby="actionConfirmTitle"/);assert.match(panel,/\.action-confirm::backdrop/);
});

test('Aceitar resolve somente uma vez e cancelamento nativo não vira aceite',async()=>{
  const h=panelHarness();const accepted=h.ui.confirmAction('Aceitar esta ação específica?');h.node('actionConfirmAccept').listeners.click[0]();h.node('actionConfirmAccept').listeners.click[0]();assert.equal(await accepted,true);assert.equal(h.node('actionConfirm').open,false);
  const cancelled=h.ui.confirmAction('Outra decisão');h.node('actionConfirm').listeners.cancel[0]({preventDefault(){}});assert.equal(await cancelled,false);
});

test('publicação cancelada ou com empresa/sessão alterada durante await não faz POST',async()=>{
  for(const mode of ['cancel','tenant','logout','edit']){
    const h=panelHarness();await loadBusiness(h);const before=h.requests.length,publishing=h.ui.businessMutation('publish');
    assert.equal(h.node('actionConfirm').open,true);assert.match(h.node('actionConfirmText').textContent,/Publicar o rascunho revisado/);
    if(mode==='cancel')await cancelAction(h);else{
      if(mode==='tenant')h.node('businessTenant').value='tenant-b';
      if(mode==='logout')h.ui.showLogin();
      if(mode==='edit'){h.node('bizName').value='Nova edição';h.ui.businessChanged();}
      await acceptAction(h);
    }
    await publishing;assert.equal(h.requests.length,before,mode+' deve preservar zero requisições');
  }
});

test('restauração revisada aguarda aceite e envia uma única versão com geração capturada',async()=>{
  const h=panelHarness();await loadBusiness(h);h.node('businessVersion').value=olderId;const review=h.ui.reviewBusinessVersion();
  h.take(businessUrl('tenant-a','/versions/'+olderId)).respond({id:olderId,revision:1,profile:profile('Versão anterior')});await review;
  const restoring=h.ui.businessMutation('rollback');assert.equal(h.requests.filter(x=>x.url.endsWith('/rollback')).length,0);await h.ui.businessMutation('rollback');
  await acceptAction(h);const post=h.take(businessUrl('tenant-a','/rollback'),'POST');assert.deepEqual(JSON.parse(post.options.body),{expected_generation:7,version_id:olderId});
  post.respond({revision:1,generation:8,status:'published'});await tick();h.take(businessUrl('tenant-a')).respond(snapshot('Versão anterior',{generation:8}));h.take(businessUrl('tenant-a','/history')).respond(history());await restoring;
  assert.equal(h.requests.filter(x=>x.url.endsWith('/rollback')).length,1);
});

test('troca de empresa com edição exige aceite; cancelar preserva formulário e logout não restaura tenant antigo',async()=>{
  const h=panelHarness();await loadBusiness(h);h.node('bizDescription').value='Edição a preservar';h.ui.businessChanged();h.node('businessTenant').value='tenant-b';
  let before=h.requests.length;const cancelled=h.node('businessTenant').listeners.change[0]();await cancelAction(h);await cancelled;
  assert.equal(h.node('businessTenant').value,'tenant-a');assert.equal(h.node('bizDescription').value,'Edição a preservar');assert.equal(h.requests.length,before);
  h.node('businessTenant').value='tenant-b';const changed=h.node('businessTenant').listeners.change[0]();await acceptAction(h);
  h.take(businessUrl('tenant-b')).respond(snapshot('Empresa B'));h.take(businessUrl('tenant-b','/history')).respond(history());await changed;assert.equal(h.node('bizName').value,'Empresa B');
  h.ui.businessChanged();h.node('businessTenant').value='tenant-a';before=h.requests.length;const loggedOut=h.node('businessTenant').listeners.change[0]();h.ui.showLogin();await acceptAction(h);await loggedOut;
  assert.equal(h.node('businessTenant').value,'');assert.equal(h.requests.length,before);
});

test('atualização com edição espera escolha e contexto fresco; cancelar ou mudar contexto não consulta dados',async()=>{
  for(const mode of ['cancel','tenant','logout','edit']){
    const h=panelHarness();await loadBusiness(h);h.node('bizDescription').value='Preservar';h.ui.businessChanged();const before=h.requests.length,reload=h.node('businessReload').listeners.click[0]();
    if(mode==='cancel')await cancelAction(h);else{
      if(mode==='tenant')h.node('businessTenant').value='tenant-b';
      if(mode==='logout')h.ui.showLogin();
      if(mode==='edit'){h.node('bizDescription').value='Edição mais recente';h.ui.businessChanged();}
      await acceptAction(h);
    }
    await reload;assert.equal(h.requests.length,before);
    if(mode==='cancel')assert.equal(h.node('bizDescription').value,'Preservar');
  }
  const h=panelHarness();await loadBusiness(h);h.ui.businessChanged();const reload=h.node('businessReload').listeners.click[0]();await acceptAction(h);
  h.take(businessUrl('tenant-a')).respond(snapshot('Estado do servidor'));h.take(businessUrl('tenant-a','/history')).respond(history());await reload;assert.equal(h.node('bizName').value,'Estado do servidor');
});

test('envio manual cancelado ou com destinatário/texto/contato/sessão alterado durante confirmação não envia',async()=>{
  for(const mode of ['cancel','phone','text','conversation','logout','connection']){
    const h=panelHarness();await connect(h);h.node('chatPhone').value=phoneA;h.node('chatText').value='Mensagem revisada';const sending=h.node('sendForm').listeners.submit[0]({preventDefault(){}});
    assert.equal(h.node('actionConfirm').open,true);assert.match(h.node('actionConfirmText').textContent,new RegExp(phoneA));
    if(mode==='cancel')await cancelAction(h);else{
      if(mode==='phone')h.node('chatPhone').value=phoneB;
      if(mode==='text')h.node('chatText').value='Outra mensagem';
      if(mode==='conversation')h.ui.selectChat(phoneB);
      if(mode==='logout')h.ui.showLogin();
      if(mode==='connection'){const status=h.ui.updateStatus();h.take('/api/v1/qr/status').respond({status:'reconnecting'});await status;}
      await acceptAction(h);
    }
    await sending;assert.equal(h.requests.filter(x=>x.options.method==='POST').length,0,mode);
  }
});

test('duplo submit e duplo Aceitar do envio manual produzem uma única tentativa de transporte',async()=>{
  const h=panelHarness();await connect(h);h.node('chatPhone').value=phoneA;h.node('chatText').value='Uma tentativa';
  const send=()=>h.node('sendForm').listeners.submit[0]({preventDefault(){}}),sending=send();await send();assert.equal(h.node('actionConfirm').modalCalls,1);
  h.node('actionConfirmAccept').listeners.click[0]();h.node('actionConfirmAccept').listeners.click[0]();await tick();const post=h.take('/api/v1/qr/send','POST');assert.deepEqual(JSON.parse(post.options.body),{phone:phoneA,text:'Uma tentativa'});
  await send();post.reject(Error('Resultado incerto'));await sending;assert.equal(h.requests.filter(x=>x.url==='/api/v1/qr/send').length,1);assert.equal(h.node('sendButton').disabled,false);
});

test('cada estilo mostra valores, confirmação e cancelamento com renderer seguro e campos aditivos opcionais',async()=>{
  const h=panelHarness();await loadBusiness(h);const loading=h.ui.loadBusinessStyleExamples();h.take('/api/v1/tenants/tenant-a/ai/style-examples','POST').respond({examples:[{style:'warm',label:'Acolhedor',answer:'*R$ 55,00*',confirmation_example:'Exemplo visual — nenhuma operação realizada. *Resumo* _<img src=x>_',cancellation_example:'Exemplo visual — nenhuma operação realizada. _Cancelamento_ <script>alert(1)</script>'}]});await loading;
  const card=h.node('businessStyleExamples').children[0];assert.equal(card.children.length,4);assert.deepEqual(card.children.slice(1).map(block=>block.children[0].textContent),['Valores','Exemplo de confirmação','Exemplo de cancelamento']);
  assert.match(contentOf(card),/<img src=x>/);assert.match(contentOf(card),/<script>/);for(const block of card.children.slice(1))assert.ok(block.children[1].children.every(node=>typeof node==='string'||['STRONG','EM'].includes(node.tagName)&&!Object.hasOwn(node,'innerHTML')));
});
