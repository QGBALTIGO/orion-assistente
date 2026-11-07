import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const panel=fs.readFileSync(new URL('../../app/static/panel.html',import.meta.url),'utf8');
const controller=[...panel.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

// Build the relevant DOM from the real markup, including ancestry and initial
// hidden classes. Execute the production renderers and event handlers below;
// no network, browser login, pairing or WhatsApp operation is performed.
class Element{
  constructor(tag,attributes={}){
    this.tagName=tag;this.attributes=attributes;this.children=[];this.parentElement=null;
    this.dataset={};this.listeners={};this.disabled=false;this.open=false;this._text='';
    this.classes=new Set((attributes.class||'').split(/\s+/).filter(Boolean));
    this.classList={contains:name=>this.classes.has(name),add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),
      toggle:(name,force)=>{const add=force??!this.classes.has(name);if(add)this.classes.add(name);else this.classes.delete(name);return add;}};
    for(const [key,value] of Object.entries(attributes))if(key.startsWith('data-'))
      this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
  }
  set className(value){this.classes=new Set(value.split(/\s+/).filter(Boolean));}
  get className(){return [...this.classes].join(' ');}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join('');}
  set innerHTML(value){this._text=value;this.children=[];}
  setAttribute(key,value){this.attributes[key]=value;}
  removeAttribute(key){delete this.attributes[key];}
  addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
  async click(){assert.ok(visible(this),'A hidden control cannot be clicked');if(!this.disabled)for(const fn of this.listeners.click||[])await fn();}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  querySelectorAll(selector){return descendants(this).filter(node=>matchesPath(node,selector.split(/\s+/)));}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
function descendants(node){return node.children.flatMap(child=>[child,...descendants(child)]);}
function matches(node,selector){return selector.startsWith('.')?node.classes.has(selector.slice(1)):
  selector.startsWith('[')?Object.hasOwn(node.attributes,selector.slice(1,-1)):node.tagName===selector;}
function matchesPath(node,parts){
  if(!matches(node,parts.at(-1)))return false;
  let ancestor=node.parentElement;
  for(let i=parts.length-2;i>=0;i--){while(ancestor&&!matches(ancestor,parts[i]))ancestor=ancestor.parentElement;if(!ancestor)return false;ancestor=ancestor.parentElement;}
  return true;
}
function visible(node){
  if(!node)return false;
  for(let current=node;current;current=current.parentElement)if(current.classes.has('hidden')||Object.hasOwn(current.attributes,'hidden')||
    current.tagName==='dialog'&&!current.open)return false;
  return true;
}
function dom(){
  const root=new Element('document'),stack=[root],ids=new Map();
  const html=panel.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'').replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi,'').replace(/<!--[\s\S]*?-->/g,'');
  for(const token of html.matchAll(/<\/?([a-z][\w-]*)\b([^>]*)>|([^<]+)/gi)){
    if(!token[1]){stack.at(-1)._text+=token[3]||'';continue;}
    const tag=token[1].toLowerCase();
    if(token[0].startsWith('</')){const at=stack.findLastIndex(node=>node.tagName===tag);if(at>0)stack.length=at;continue;}
    const attributes=Object.fromEntries([...token[2].matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)].map(m=>[m[1],m[2]??m[3]??m[4]??'']));
    const node=new Element(tag,attributes);node.parentElement=stack.at(-1);stack.at(-1).children.push(node);
    if(attributes.id)ids.set(attributes.id,node);
    if(!/\/\s*$/.test(token[2])&&!['area','base','br','col','embed','hr','img','input','link','meta','source','track','wbr'].includes(tag))stack.push(node);
  }
  return {root,ids};
}
function definition(name,optional=false){
  const found=controller.match(new RegExp('^(?:async )?function '+name+'\\(','m'));
  if(!found&&optional)return '';
  assert.ok(found,`Real production function ${name} must exist`);
  const lines=controller.slice(found.index).split(/\r?\n/);let source='';
  for(const line of lines){source+=line+'\n';try{new vm.Script(source);return source;}catch{}}
  throw Error(`Cannot parse production function ${name}`);
}
function harness(role='admin',{reply=async()=>({status:'needs_attention'})}={}){
  const {root,ids}=dom(),requests=[],mounts=[],qrReads=[],timers=[];let now=1794000000000;
  // The application itself is authenticated in this fixture; preserve each
  // protected control's real data-admin-only visibility rule.
  ids.get('appView').classList.remove('hidden');ids.get('page-dispositivo').classList.remove('hidden');
  const document={querySelectorAll:selector=>root.querySelectorAll(selector)};
  const context={document,console,Date:class extends Date{static now(){return now;}},Intl,
    setTimeout:callback=>{timers.push(callback);return timers.length;},
    api:async(path,method='GET')=>{requests.push({path,method});if(path==='/api/v1/qr/image')qrReads.push(true);return reply(path,method);},
    window:{OrionIngressReview:{mount:value=>mounts.push(value)}},$:id=>{assert.ok(ids.has(id),`Unknown production node ${id}`);return ids.get(id);},
    connectView:{instructions:()=>'',seconds:()=>''}};
  const bindings=controller.match(/^for\(const id of \["deviceDiagnostics"[^\n]*\]\)\r?\n[^\n]+showDeviceDiagnostics\);/m);
  assert.ok(bindings,'Use the actual diagnostic event registrations');
  const roleRule=controller.match(/const isAdmin=role==="support"\|\|role==="admin";[\s\S]*?document\.querySelectorAll\("\[data-admin-only\]"\)\.forEach\([^\n]+/)[0];
  const source=`let csrf='synthetic',authEpoch=1,statusSequence=0,deviceStatus='starting',lastDeviceStatus='starting',loginRole=${JSON.stringify(role)},page='dispositivo';
    let method='code',wizardBusy=false,wizardUncertain=false,wizardStep='tutorial',wizardAttempted=false,wizardSuccessDone=false,wizardSuccessUntil=0,
      wizardCodeExpired=false,wizardQrRead=0,wizardQrDeadline=0,pairExpiresAt=0,showQr=false,wizardOperation=0;
    ${definition('sessionNeedsAttention',true)}
    ${definition('clearWizardCodes')}
    ${definition('countdownLabel')}
    ${definition('updateWizardClocks')}
    ${definition('renderGuide')}
    ${definition('statusWord')}
    let connectedSnapshot=null;
    ${definition('renderConnectedCenter')}
    ${definition('renderConversationMode')}
    ${definition('closeOptions')}
    ${definition('showDeviceDiagnostics')}
    ${definition('updateStatus')}
    ${definition('updateQr')}
    ${definition('attemptQrRefresh')}
    ${bindings[0]}
    function setRole(role){loginRole=role;${roleRule}}
    setRole(loginRole);
    globalThis.view={load:snapshot=>updateStatus({snapshot}),poll:()=>updateStatus(),setRole,showOldQr:()=>{showQr=true;method='qr'},statusWord,
      pairingStarted:()=>{deviceStatus='pairing';wizardAttempted=true;},refreshQr:attemptQrRefresh,
      invalidateSession:()=>{authEpoch++;},invalidateOperation:()=>{wizardOperation++;}};`;
  vm.runInNewContext(source,context,{filename:'production-attention-renderers.js'});
  return {node:id=>ids.get(id),view:context.view,requests,mounts,qrReads,
    finishSuccess:()=>{now+=2300;for(const callback of timers.splice(0))callback();}};
}
const linked=status=>({status,messagesEnabled:true,phone_verified:true,phone:'5567999999901',automaticRepliesEnabled:false});

for(const [state,label] of [['needs_attention','Atendimento automático pausado'],['degraded','Conexão instável']])
test(`a verified linked ${state} session shows honest attention rather than pairing`,async()=>{
  const h=harness();h.view.showOldQr();await h.view.load(linked(state));
  assert.equal(visible(h.node('deviceAttention')),true);
  assert.match(h.node('deviceAttentionTitle').textContent,new RegExp(label));
  assert.equal(visible(h.node('disconnectedWizard')),false);assert.equal(visible(h.node('wizardTutorial')),false);
  assert.equal(visible(h.node('connectedCenter')),false,'An attention state must not look automation-ready');
  assert.equal(h.node('globalStatus').textContent,label);assert.equal(h.node('refreshQr').disabled,true);
  assert.equal(h.qrReads.length,0,'Attention must not request another QR');assert.equal(h.requests.length,0);
});

for(const state of ['needs_attention','degraded'])
for(const facts of [{},{messagesEnabled:true},{phone_verified:true},{messagesEnabled:false,phone_verified:true},
  {messagesEnabled:true,phone_verified:false},{messagesEnabled:'true',phone_verified:true},{messagesEnabled:true,phone_verified:1}])
test(`${state} without strict verified session facts cannot claim a linked session: ${JSON.stringify(facts)}`,async()=>{
  const h=harness();await h.view.load({status:state,...facts});
  assert.equal(visible(h.node('deviceAttention')),false);assert.equal(visible(h.node('connectedCenter')),false);
  assert.equal(visible(h.node('disconnectedWizard')),true);assert.equal(visible(h.node('wizardTutorial')),true);
});

test('healthy, attention, healthy and logged-out transitions preserve their distinct views',async()=>{
  const h=harness();h.view.pairingStarted();await h.view.load(linked('connected'));
  assert.equal(visible(h.node('wizardSuccess')),true,'Use the real successful-pairing branch');
  h.finishSuccess();
  assert.equal(visible(h.node('connectedCenter')),true);assert.equal(visible(h.node('disconnectedWizard')),false);
  await h.view.load(linked('needs_attention'));assert.equal(visible(h.node('deviceAttention')),true);
  await h.view.load(linked('connected'));assert.equal(visible(h.node('deviceAttention')),false);assert.equal(visible(h.node('connectedCenter')),true);
  await h.view.load({status:'logged_out',messagesEnabled:false,phone_verified:false});
  assert.equal(visible(h.node('connectedCenter')),false);assert.equal(visible(h.node('deviceAttention')),false);
  assert.equal(visible(h.node('disconnectedWizard')),true);assert.equal(visible(h.node('wizardRecover')),true);
});

for(const attention of ['needs_attention','degraded'])
for(const next of ['starting','reconnecting','error'])
test(`successful pairing then ${attention} then ${next} cannot revive the green success wizard`,async()=>{
  const h=harness();h.view.pairingStarted();await h.view.load(linked('connected'));
  assert.equal(visible(h.node('wizardSuccess')),true,'The real success branch must first be visible');
  await h.view.load(linked(attention));
  assert.equal(visible(h.node('deviceAttention')),true);assert.equal(visible(h.node('disconnectedWizard')),false);
  await h.view.load({status:next,messagesEnabled:false,phone_verified:false});
  assert.equal(visible(h.node('deviceAttention')),false);assert.equal(visible(h.node('disconnectedWizard')),true);
  assert.equal(visible(h.node('wizardSuccess')),false,'Never claim WhatsApp is ready after the linked state ends');
  assert.equal(visible(h.node('connectedCenter')),false);
});

for(const state of ['needs_attention','degraded'])
test(`an in-flight real QR refresh stops after a verified ${state} status preflight`,async()=>{
  const h=harness('admin',{reply:async path=>path==='/api/v1/qr/status'?linked(state):
    {status:'qr_ready',image:'data:image/png;base64,YQ==',expiresIn:30}});
  await h.view.refreshQr();
  assert.deepEqual(h.requests,[{path:'/api/v1/qr/status',method:'GET'}]);
  assert.equal(h.qrReads.length,0,'Execute actual attemptQrRefresh and updateQr, without a QR stub');
  assert.equal(visible(h.node('deviceAttention')),true);assert.equal(visible(h.node('disconnectedWizard')),false);
});

for(const invalidation of ['invalidateSession','invalidateOperation'])
test(`QR preflight still respects ${invalidation} while the status response is pending`,async()=>{
  let respond;
  const h=harness('admin',{reply:()=>new Promise(resolve=>{respond=resolve;})});
  const refresh=h.view.refreshQr();h.view[invalidation]();respond(linked('needs_attention'));await refresh;
  assert.deepEqual(h.requests,[{path:'/api/v1/qr/status',method:'GET'}]);
  assert.equal(visible(h.node('deviceAttention')),false,'An obsolete response cannot render into the current context');
  assert.equal(h.qrReads.length,0);
});

for(const attention of ['needs_attention','degraded'])
test(`a newer accepted ${attention} poll invalidates the older QR preflight without reconnect or QR fetch`,async()=>{
  let statusReads=0,release;
  const h=harness('admin',{reply:async path=>{
    if(path==='/api/v1/qr/status'){
      if(++statusReads===1)return new Promise(resolve=>{release=resolve;});
      return linked(attention);
    }
    return {status:'qr_ready',image:'data:image/png;base64,YQ==',expiresIn:30};
  }});
  const refresh=h.view.refreshQr();
  await h.view.poll();
  const busyAfterAttention=h.node('wizardCard').attributes['aria-busy'];
  assert.equal(visible(h.node('deviceAttention')),true,'The newer poll is accepted by actual updateStatus');
  release({status:'qr_expired'});await refresh;
  assert.deepEqual(h.requests,[{path:'/api/v1/qr/status',method:'GET'},{path:'/api/v1/qr/status',method:'GET'}]);
  assert.equal(busyAfterAttention,'false','Accepting attention releases the obsolete local wizard attempt');
  assert.equal(h.qrReads.length,0);assert.equal(visible(h.node('deviceAttention')),true);
  assert.equal(visible(h.node('disconnectedWizard')),false);assert.equal(visible(h.node('wizardSuccess')),false);
  assert.equal(h.node('globalStatus').textContent,h.view.statusWord(attention));
});

for(const role of ['admin','support'])
test(`${role} can open the existing diagnostic modal from the unlinked or attention device page`,async()=>{
  const h=harness(role),button=h.node('deviceStatusDiagnostics');
  for(const result of [{status:'starting'},{status:'error'},linked('needs_attention')]){
    await h.view.load(result);assert.equal(visible(button),true,'Entry must not descend from a hidden connected center');
    await button.click();assert.equal(h.node('deviceDiagnosticsDialog').open,true);h.node('deviceDiagnosticsDialog').close();
  }
  assert.deepEqual(h.requests,[{path:'/api/v1/qr/status',method:'GET'},{path:'/api/v1/qr/status',method:'GET'},{path:'/api/v1/qr/status',method:'GET'}]);
  assert.ok(h.mounts.every(value=>value.role===role));
  h.view.setRole('member');assert.equal(visible(button),false,'Existing member visibility restriction remains enforced');
});
