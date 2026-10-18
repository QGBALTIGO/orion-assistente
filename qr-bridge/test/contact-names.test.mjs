import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {EventEmitter} from 'node:events';
import vm from 'node:vm';
import {timingSafeEqual} from 'node:crypto';
import {makeContactNames,cleanContactName,cleanContactProfile,contactPhone,requestedContactPhones,
  requestedContactQuery,installContactNameEvents,CONTACT_NAME_TTL_MS,CONTACT_NAME_LIMIT} from '../src/contact-names.mjs';

process.env.ORION_CONTACT_TEST_MODE='1';
const secret='synthetic-contact-test-key-not-used-outside-local-tests';
const own='5511999990000',a='5511999990001',b='5511999990002',c='15551230003';
const lid='123456789012345678@lid',ownLid='123456789012345679@lid';
const pn=phone=>phone+'@s.whatsapp.net';
function setup(t,options={}){
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-contact-names-'));
  t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const params={directory,secret,...options};
  return {params,store:makeContactNames(params)};
}
const incoming=(jid,name,extra={})=>({key:{remoteJid:jid,fromMe:false,...extra},pushName:name});

test('display labels preserve accents and emoji while refusing numeric, placeholder, JID and control labels',()=>{
  for(const [input,expected] of [['  José\t da  Silva  ','José da Silva'],['Ｊｏａｎａ','Joana'],
    ['✂️','✂️'],['👩🏽‍🔧','👩🏽‍🔧'],['👨‍👩‍👧‍👦','👨‍👩‍👧‍👦'],['A 💇','A 💇']])
    assert.equal(cleanContactName(input),expected);
  for(const input of [null,42,{},'',a,'+55 (11) 99999-0001','Cliente WhatsApp','NOME NÃO INFORMADO',
    'Contato','Unknown','cliente do WhatsApp','Contato do WhatsApp','Someone '+lid,'Someone '+pn(a),
    'A\u202eB','A\u200bB','A\u200dB','A\u0000B','😀\u2066','A'.repeat(121)])assert.equal(cleanContactName(input),null);
  assert.deepEqual(cleanContactProfile({name:' Joana ',source:'push_name',extra:'ignored'}),{name:'Joana',source:'push_name'});
  assert.equal(cleanContactProfile({name:'Joana',source:'guessed'}),null);
  assert.equal(contactPhone(a),a);assert.equal(contactPhone(lid),null);assert.equal(contactPhone('0'+a),null);
});

test('registry is encrypted, private and unavailable until the authenticated account is bound',t=>{
  const {store,params}=setup(t);
  assert.equal(store.accountPhone(),null);
  store.bindIdentity({id:own+':4@s.whatsapp.net',lid:ownLid});
  store.observeContacts([{id:pn(a),name:'Joana Exemplo'}]);
  assert.deepEqual(store.requested({phones:[a,b]}),{account_phone:own,items:[{
    phone:a,display_name:'Joana Exemplo',name_source:'contact_name'}]});
  const bytes=fs.readFileSync(store.file);
  assert.doesNotMatch(bytes.toString('utf8'),/Joana Exemplo|551199999000[01]|contact_name/);
  if(process.platform!=='win32')assert.equal(fs.statSync(store.file).mode&0o777,0o600);
  const reloaded=makeContactNames(params);
  assert.equal(reloaded.lookup(a),null);assert.deepEqual(reloaded.entries(),[]);
  reloaded.bindIdentity({id:own+':9@s.whatsapp.net',lid:ownLid});
  assert.equal(reloaded.lookup(a).display_name,'Joana Exemplo');
});

test('contact, verified business and push names have explicit precedence; history does not overwrite a current same-source label',t=>{
  let now=1_800_000_000_000;
  const {store}=setup(t,{now:()=>now});store.bindIdentity(pn(own));
  store.observeMessages([incoming(pn(a),'Primeiro Nome')]);
  now++;
  store.observeContacts([{id:pn(a),verifiedName:'Empresa Exemplo'}]);
  assert.equal(store.lookup(a).name_source,'verified_name');
  store.observeContacts([{id:pn(a),name:'Contato Salvo'}]);
  store.observeMessages([incoming(pn(a),'Outro Perfil')]);
  assert.equal(store.lookup(a).display_name,'Contato Salvo');
  store.observeContacts([{id:pn(a),name:'Nome Histórico'}],{historical:true});
  assert.equal(store.lookup(a).display_name,'Contato Salvo');
  store.observeContacts([{id:pn(a),name:'Nome Atualizado'}]);
  assert.equal(store.lookup(a).display_name,'Nome Atualizado');
  store.observeMessages([incoming(pn(b),'Nome Recente'),incoming(pn(b),'Nome Antigo')],{historical:true});
  assert.equal(store.lookup(b).display_name,'Nome Recente');
});

test('partial contact updates and duplicate observations neither erase a label nor rewrite encrypted data',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  assert.equal(store.observeContacts([{id:pn(a),name:'Joana'}]),true);
  const before=fs.readFileSync(store.file);
  for(const update of [{id:pn(a),imgUrl:'changed'},{id:pn(a),name:''},{id:pn(a),notify:'123'},
    {id:pn(a),name:'Joana'}])assert.equal(store.observeContacts([update]),false);
  assert.deepEqual(fs.readFileSync(store.file),before);assert.equal(store.lookup(a).display_name,'Joana');
});

test('outgoing messages, own PN/LID, groups, broadcasts and unknown direction cannot name another customer',t=>{
  const {store}=setup(t);store.bindIdentity({id:pn(own),lid:ownLid});
  store.observeContacts([{id:pn(own),name:'Nosso Perfil'},{id:ownLid,notify:'Nosso Perfil'},
    {id:'123456789@g.us',name:'Grupo Interno'},{id:ownLid,jid:pn(a),name:'Não usar'}]);
  store.observeMessages([incoming(pn(a),'Nosso Nome',{fromMe:true}),incoming(lid,'Nosso Nome',{fromMe:true,senderPn:pn(a)}),
    incoming(pn(own),'Nosso Nome'),incoming(ownLid,'Nosso Nome',{senderPn:pn(a)}),
    incoming('123456789@g.us','Participante',{participantPn:pn(a)}),incoming('status@broadcast','Contato'),
    {key:{remoteJid:pn(a)},pushName:'Direção Ausente'}]);
  assert.deepEqual(store.entries(),[]);
});

test('LID stays opaque until an explicit phone-share event and conflicting mappings quarantine its name',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:lid,notify:'Nome do Perfil'}]);
  assert.deepEqual(store.entries(),[]);assert.equal(store.lookup(lid.split('@')[0]),null);
  store.observePhoneShare({lid,jid:pn(a)});
  assert.equal(store.lookup(a).display_name,'Nome do Perfil');
  store.observePhoneShare({lid,jid:pn(b)});
  assert.equal(store.lookup(a),null);assert.equal(store.lookup(b),null);
  store.observePhoneShare({lid,jid:pn(a)});
  assert.equal(store.lookup(a),null);
  store.observeContacts([{id:pn(a),name:'Nome PN Independente'}]);
  assert.equal(store.lookup(a).display_name,'Nome PN Independente');
});

test('pinned Contact.id/lid/jid and incoming senderPn are used only when every identity hint agrees',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:lid,jid:pn(a),name:'Contato com LID'}]);
  assert.equal(store.lookup(a).display_name,'Contato com LID');
  store.observeContacts([{id:pn(b),jid:pn(a),name:'Conflito'}]);
  store.observeMessages([incoming(pn(b),'Conflito',{senderPn:pn(a)})]);
  assert.equal(store.lookup(b),null);
  const another='123456789012345677@lid';
  store.observeMessages([incoming(another,'Contato Recebido',{senderPn:pn(b)})]);
  assert.equal(store.lookup(b).display_name,'Contato Recebido');
  store.observeContacts([{id:pn(c),lid:'not-a-lid',name:'Inválido'}]);
  assert.equal(store.lookup(c),null);
});

test('contact metadata buffered before identity is bounded, excludes own labels, and contains no message bodies',t=>{
  const {store}=setup(t,{pendingLimit:2});
  store.observeContacts([{id:pn(c),name:'Descartado'},{id:pn(a),name:'Cliente Exemplo'},
    {id:pn(own),name:'Nome Próprio'}]);
  assert.deepEqual(store.requested({phones:[a,c]}),{account_phone:null,items:[]});
  store.bindIdentity(pn(own));
  assert.equal(store.lookup(c),null);assert.equal(store.lookup(a).display_name,'Cliente Exemplo');
  assert.equal(store.lookup(own),null);
  const message=incoming(pn(b),'Outro Cliente');
  Object.defineProperty(message,'message',{get(){throw Error('Message bodies must not be read');}});
  assert.equal(store.observeMessages([message],{historical:true}),true);
  assert.equal(store.lookup(b).display_name,'Outro Cliente');
});

test('switching account discards previous names and aliases, even when switching back to the original account',t=>{
  const {store,params}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:lid,jid:pn(a),name:'Conta Antiga'}]);
  store.bindIdentity(pn(c));
  assert.equal(store.lookup(a),null);assert.equal(store.accountPhone(),c);
  store.observeContacts([{id:lid,notify:'Sem Mapeamento na Nova Conta'}]);
  assert.equal(store.lookup(a),null);
  const reload=makeContactNames(params);reload.bindIdentity(pn(own));
  assert.equal(reload.lookup(a),null);assert.deepEqual(reload.entries(),[]);
});

test('suspend drops pre-identity metadata; purge removes only the contact registry and not credentials/inbox',t=>{
  const {store,params}=setup(t);
  fs.writeFileSync(path.join(params.directory,'creds.json'),'synthetic credentials');
  fs.writeFileSync(path.join(params.directory,'orion_inbox.v1.enc'),'synthetic inbox');
  store.observeContacts([{id:pn(a),name:'Evento Antigo'}]);store.suspend();store.bindIdentity(pn(own));
  assert.equal(store.lookup(a),null);
  store.observeContacts([{id:pn(b),name:'Cliente Exemplo'}]);
  store.suspend();assert.equal(store.accountPhone(),null);assert.equal(store.lookup(b),null);
  store.bindIdentity(pn(own));assert.equal(store.lookup(b).display_name,'Cliente Exemplo');
  assert.equal(store.purge(),true);assert.equal(fs.existsSync(store.file),false);
  assert.equal(fs.readFileSync(path.join(params.directory,'creds.json'),'utf8'),'synthetic credentials');
  assert.equal(fs.readFileSync(path.join(params.directory,'orion_inbox.v1.enc'),'utf8'),'synthetic inbox');
});

test('bounded metadata expires after 30 days and cannot be replayed/read after expiry',t=>{
  let now=1_800_000_000_000;
  const {store,params}=setup(t,{now:()=>now,limit:2});store.bindIdentity(pn(own));
  store.observeContacts([{id:pn(a),name:'Primeiro'}]);now++;
  store.observeContacts([{id:pn(b),name:'Segundo'}]);now++;
  store.observeContacts([{id:pn(c),name:'Terceiro'}]);
  assert.equal(store.lookup(a),null);assert.equal(store.entries().length,2);
  now+=CONTACT_NAME_TTL_MS+1;
  assert.deepEqual(store.entries(),[]);assert.equal(store.lookup(b),null);
  const reload=makeContactNames(params);reload.bindIdentity(pn(own));assert.deepEqual(reload.entries(),[]);
});

test('tampering/wrong keys fail closed and do not overwrite the unreadable registry',t=>{
  const {store,params}=setup(t);store.bindIdentity(pn(own));store.observeContacts([{id:pn(a),name:'Cliente'}]);
  const bytes=fs.readFileSync(store.file);bytes[20]^=0xff;fs.writeFileSync(store.file,bytes);
  const broken=makeContactNames(params);
  assert.equal(broken.health(),'unreadable');assert.equal(broken.bindIdentity(pn(own)),false);
  assert.equal(broken.observeContacts([{id:pn(b),name:'Outro Nome'}]),false);
  assert.deepEqual(broken.entries(),[]);assert.deepEqual(fs.readFileSync(store.file),bytes);
  const wrong=makeContactNames({...params,secret:'another-synthetic-secret-with-at-least-32-bytes'});
  assert.equal(wrong.health(),'unreadable');assert.equal(wrong.accountPhone(),null);
});

test('batch lookup cannot enumerate the registry or coerce LIDs, duplicates or unvalidated phones',t=>{
  for(const input of [null,{},[],{phones:[]},{phones:[a,a]},{phones:[lid]},{phones:['+'+a]},
    {phones:[42]},{phones:[a],all:true},{phones:Array.from({length:101},(_,i)=>String(5511900000000+i))}])
    assert.throws(()=>requestedContactPhones(input),/invalid_contact_phones/);
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:pn(a),name:'Nome Um'},{id:pn(b),name:'Nome Dois'}]);
  assert.deepEqual(store.requested({phones:[b,c]}).items,[{phone:b,display_name:'Nome Dois',name_source:'contact_name'}]);
});

test('real event contracts are metadata-only, bounded by current socket, and stop unregisters listeners',t=>{
  const {store}=setup(t);const ev=new EventEmitter();let current=true,changes=0;
  const client={ev,onWhatsApp(){throw Error('No network lookup');},sendMessage(){throw Error('No sends');}};
  const watcher=installContactNameEvents({client,store,isCurrent:()=>current,onChange:()=>changes++});
  ev.emit('contacts.upsert',[{id:pn(a),name:'Antes da Identidade'}]);
  ev.emit('creds.update',{me:{id:pn(own),lid:ownLid}});
  assert.equal(store.lookup(a).display_name,'Antes da Identidade');
  const message=incoming(lid,'Nome Histórico');
  Object.defineProperty(message,'message',{get(){throw Error('No history body import');}});
  ev.emit('messaging-history.set',{contacts:[{id:lid,notify:'Nome Histórico'}],messages:[message]});
  ev.emit('chats.phoneNumberShare',{lid,jid:pn(b)});
  assert.equal(store.lookup(b).display_name,'Nome Histórico');
  const count=changes;ev.emit('contacts.update',[{id:pn(a),name:'Antes da Identidade'}]);assert.equal(changes,count);
  current=false;ev.emit('contacts.upsert',[{id:pn(c),name:'Evento Atrasado'}]);assert.equal(store.lookup(c),null);
  watcher.stop();current=true;ev.emit('contacts.update',[{id:pn(c),name:'Evento Removido'}]);
  assert.equal(store.lookup(c),null);assert.equal(ev.listenerCount('contacts.upsert'),0);
});

test('offline append and its Baileys contacts.update echo cannot overwrite a newer notify label',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  const ev=new EventEmitter(),watcher=installContactNameEvents({client:{ev},store,isCurrent:()=>true});
  t.after(()=>watcher.stop());
  ev.emit('messages.upsert',{type:'notify',messages:[incoming(pn(a),'Nome Atual')]});
  ev.emit('messages.upsert',{type:'append',messages:[incoming(pn(a),'Nome Antigo')]});
  ev.emit('contacts.update',[{id:pn(a),notify:'Nome Antigo'}]);
  assert.equal(store.lookup(a).display_name,'Nome Atual');
  ev.emit('messages.upsert',{type:'notify',messages:[incoming(pn(a),'Nome Novo')]});
  ev.emit('contacts.update',[{id:pn(a),notify:'Nome Novo'}]);
  assert.equal(store.lookup(a).display_name,'Nome Novo');
  ev.emit('contacts.update',[{id:pn(b),notify:'Só Perfil Disponível'}]);
  assert.equal(store.lookup(b).display_name,'Só Perfil Disponível');
});

test('private name matches are literal, case/accent-insensitive and return only matching current-account PNs',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:pn(a),name:'José da Silva'},{id:pn(b),name:'JOSEFA'},
    {id:'123456789012345678@lid',name:'José sem PN'},{id:pn(own),name:'José Proprietário'},
    {id:pn(c),name:'A 100% Literal'}]);
  assert.deepEqual(store.matches({q:'  josÉ '}),{account_phone:own,phones:[a,b]});
  assert.deepEqual(store.matches({q:'%'}).phones,[c]);
  assert.deepEqual(store.matches({q:'.*'}).phones,[]);
  for(const input of [{q:''},{q:' '},{q:'a'.repeat(121)},{q:'Jo\u202ee'},{q:42},{q:'José',all:true},null])
    assert.throws(()=>requestedContactQuery(input),/invalid_contact_query/);
  store.suspend();assert.deepEqual(store.matches({q:'José'}),{account_phone:null,phones:[]});
  store.bindIdentity('5511999990999@s.whatsapp.net');assert.deepEqual(store.matches({q:'José'}).phones,[]);
});

test('name search returns the complete bounded PN set for correct downstream SQL pagination',t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts(Array.from({length:CONTACT_NAME_LIMIT},(_,i)=>({id:pn(String(5511800000000+i)),name:'Nome Comum '+i})));
  const result=store.matches({q:'nome comum'});
  assert.equal(result.phones.length,CONTACT_NAME_LIMIT);
  assert.equal(new Set(result.phones).size,CONTACT_NAME_LIMIT);
  assert.deepEqual(Object.keys(result),['account_phone','phones']);
});

test('real HTTP handler authenticates metadata routes, exposes only requested/matched names and preserves chat shape',async t=>{
  const {store}=setup(t);store.bindIdentity(pn(own));
  store.observeContacts([{id:pn(a),name:'Joana'},{id:pn(b),name:'Renata'}]);
  const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
  const auth=source.slice(source.indexOf('function auth(req){'),source.indexOf('function reply(res,code,data){'));
  const handler=source.slice(source.indexOf('const server=http.createServer('),source.indexOf('\nserver.headersTimeout='));
  let route;
  const context=vm.createContext({http:{createServer:fn=>{route=fn;return {}; }},URL,Buffer,timingSafeEqual,
    KEY:secret,authDir:'/data/synthetic-test',status:'connected',contactNames:store,
    requestedContactPhones,requestedContactQuery,
    messages:[{phone:a,body:'Texto sintético da conversa',at:123}],
    collectBody:async req=>req.body,reply:(res,status,body)=>Object.assign(res,{status,body}),
    console:{info(){},warn(){}},process:{env:{}}});
  vm.runInContext(auth+'\n'+handler,context);
  const request=async(url,body,{method='POST',authorized=true}={})=>{
    const response={};await route({url,method,headers:authorized?{'x-orion-bridge-key':secret}:{},body},response);
    return JSON.parse(JSON.stringify(response));
  };
  assert.deepEqual(await request('/internal/contact-names',{phones:[a]},{authorized:false}),
    {status:401,body:{error:'unauthorized'}});
  assert.deepEqual(await request('/internal/contact-names',{phones:[a]}),{status:200,body:{account_phone:own,
    items:[{phone:a,display_name:'Joana',name_source:'contact_name'}]}});
  assert.deepEqual(await request('/internal/contact-name-matches',{q:'ren'}),
    {status:200,body:{account_phone:own,phones:[b]}});
  assert.equal((await request('/internal/contact-name-matches',{q:'ren'},{method:'GET'})).status,404);
  assert.equal((await request('/internal/contact-names',{phones:[a],all:true})).status,422);
  assert.deepEqual(await request('/internal/chats',null,{method:'GET'}),{status:200,body:{items:[{
    phone:a,preview:'Texto sintético da conversa',at:123,display_name:'Joana',name_source:'contact_name'}]}});
  context.status='logged_out';
  assert.deepEqual(await request('/internal/contact-names',{phones:[a]}),
    {status:200,body:{account_phone:null,items:[]}});
  assert.deepEqual(await request('/internal/contact-name-matches',{q:'ren'}),
    {status:200,body:{account_phone:null,phones:[]}});
  assert.equal((await request('/internal/chats',null,{method:'GET'})).body.items[0].display_name,null);
});
