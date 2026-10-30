import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {test} from "node:test";
import {JSDOM} from "jsdom";

const client = readFileSync(new URL("../app/static/orion-pwa.js", import.meta.url), "utf8");
const worker = readFileSync(new URL("../app/static/orion-pwa-sw.js", import.meta.url), "utf8");

const tick = () => new Promise(resolve => setTimeout(resolve, 8));

function setup({installed=true}={}) {
  const dom = new JSDOM('<!doctype html><html><body><div id="host"></div></body></html>', {
    url: "https://orionagenda.pro/panel", runScripts: "outside-only",
  });
  const {window:w} = dom;
  Object.defineProperty(w, "isSecureContext", {value:true,configurable:true});
  Object.defineProperty(w.navigator, "standalone", {value:installed,configurable:true});
  Object.defineProperty(w.navigator, "userAgent", {value:"Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X)",configurable:true});
  w.matchMedia=()=>({matches:installed});
  let permissionCalls=0,subCalls=0,existing=null;
  w.Notification={
    permission:"default",
    async requestPermission(){permissionCalls++;this.permission="granted";return "granted";}
  };
  const keys={p256dh:"test",auth:"test"};
  const sub={endpoint:"https://web.push.apple.com/synthetic-allowed-endpoint",
    toJSON:()=>({endpoint:sub.endpoint,keys}),unsubscribe:async()=>true};
  const registration={pushManager:{
    getSubscription:async()=>existing,
    subscribe:async options=>{assert.equal(options.userVisibleOnly,true);assert.equal(options.applicationServerKey.length,65);subCalls++;existing=sub;return sub;}
  }};
  w.navigator.serviceWorker={register:async(url,opts)=>{
    assert.equal(url,"/pwa-sw.js");assert.equal(opts.scope,"/panel");return registration;
  }};
  w.eval(client);
  const requests=[];
  const request=async (path,method="GET",data)=>{
    requests.push({path,method,data});
    if(path.endsWith("config"))return {enabled:true,devices:0,
      vapid_public_key:w.btoa(String.fromCharCode(4,...Array(64).fill(3))).replaceAll("+","-").replaceAll("/","_").replace(/=+$/,"")};
    if(path.endsWith("test"))return {accepted_by_push_service:true};
    return {subscribed:true};
  };
  return {w,host:w.document.getElementById("host"),request,requests,
    get permissionCalls(){return permissionCalls;},get subCalls(){return subCalls;}};
}

test("iOS Safari tab offers Add to Home Screen without requesting permission",async()=>{
  const t=setup({installed:false});
  await t.w.OrionPWA.mount(t.host,t.request);
  assert.match(t.host.textContent,/Adicionar à Tela de Início/);
  assert.equal(t.permissionCalls,0);
  assert.equal(t.host.querySelector("#orionPushActivate").hidden,true);
  t.w.close();
});

test("PWA requests permission only after tap and sends test only to opted-in admin device",async()=>{
  const t=setup({installed:true});
  await t.w.OrionPWA.mount(t.host,t.request);
  assert.equal(t.permissionCalls,0);
  assert.equal(t.subCalls,0);
  assert.deepEqual(t.requests.map(x=>x.path),["/api/pwa/admin/config"]);
  t.host.querySelector("#orionPushActivate").click();
  await tick();await tick();
  assert.equal(t.permissionCalls,1);
  assert.equal(t.subCalls,1);
  assert.deepEqual(t.requests.map(x=>x.path),
    ["/api/pwa/admin/config","/api/pwa/admin/subscribe","/api/pwa/admin/test"]);
  assert.equal(t.requests[1].data.endpoint,"https://web.push.apple.com/synthetic-allowed-endpoint");
  assert.match(t.host.textContent,/aceito pelo serviço push/);
  t.w.close();
});

test("service worker always displays visible push and opens only Orion panel", async()=>{
  const events={};const shown=[];const opened=[];
  const self={
    addEventListener:(name,fn)=>events[name]=fn,
    skipWaiting:async()=>{},
    clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async path=>{opened.push(path)}},
    registration:{showNotification:async(...args)=>shown.push(args),setAppBadge:async()=>{}}
  };
  vm.runInNewContext(worker,{self,Promise,URL});
  let until;events.push({waitUntil:promise=>{until=promise}});
  await until;
  assert.equal(shown[0][0],"Orion Agenda");
  assert.match(shown[0][1].body,/teste do painel Orion/);
  events.notificationclick({notification:{close(){}},waitUntil:promise=>until=promise});
  await until;
  assert.deepEqual(opened,["/panel"]);
  assert.ok(!worker.includes('addEventListener("fetch"'));
});
