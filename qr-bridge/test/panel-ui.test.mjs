import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const panel=fs.readFileSync(new URL("../../app/static/panel.html",import.meta.url),"utf8");
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
  class Element{
    constructor(){
      this.children=[];this.listeners={};this.options=[];this.value="";
      this.textContent="";this.dataset={};this.hiddenClasses=new Set();
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
    addEventListener(type,callback){(this.listeners[type]??=[]).push(callback)}
    append(...children){this.children.push(...children)}
    replaceChildren(...children){this.children=children;this.textContent=""}
    setAttribute(){}
    removeAttribute(){}
    querySelector(){return new Element()}
    add(option){this.options.push(option);if(this.options.length===1)this.value=option.value}
  }
  const nodes=new Map(),requests=[];
  const node=id=>{
    assert.ok(panel.includes('id="'+id+'"'),"unknown panel element "+id);
    if(!nodes.has(id))nodes.set(id,new Element());
    return nodes.get(id);
  };
  const context={
    document:{getElementById:node,createElement:()=>new Element(),
      querySelectorAll:()=>[],documentElement:{dataset:{}}},
    window:{matchMedia:()=>({matches:false,addEventListener(){}})},
    Option:function(text,value){this.textContent=text;this.value=value},
    fetch:(url,options={})=>new Promise((resolve,reject)=>requests.push({
      url,options,reject,respond:(body,status=200)=>resolve({
        ok:status>=200&&status<300,status,json:async()=>body
      })
    })),
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,
    confirm:()=>true,URLSearchParams,Intl,console
  };
  const source=sections[1][1].replace("renderForm();renderGuide();",`
    renderForm();renderGuide();
    globalThis.panelTest={updateStatus,refreshMessages,selectChat,
      authenticate:()=>{csrf="synthetic-csrf"},
      deviceStatus:()=>deviceStatus};
  `);
  vm.runInNewContext(source,context);
  const take=(url,method="GET")=>{
    const request=requests.find(r=>!r.taken&&r.url===url&&(r.options.method||"GET")===method);
    assert.ok(request,"missing "+method+" "+url);
    request.taken=true;return request;
  };
  return {ui:context.panelTest,node,take,requests};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
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
