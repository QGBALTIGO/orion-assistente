import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';
import * as sdk from '@whiskeysockets/baileys';
import {buildChatbotContent} from '../src/chatbot-message.mjs';
import {sendStoredProtocol} from '../src/reliable-send.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';
import {saveFallbackSelection,resolveFallbackSelection} from '../src/selection-fallback.mjs';
process.env.ORION_RETRY_TEST_MODE='1';
const phone='5567999999901',jid=phone+'@s.whatsapp.net',other='5567999999902',secret='synthetic-fallback-secret-'.repeat(3);
const action={type:'list',title:'Serviços',description:'Selecione a opção desejada',button_text:'Escolher',
  sections:[{title:'Planos',rows:[{id:'ocb:synthetic-one',title:'Primeiro',description:'Detalhe A'},{id:'ocb:synthetic-two',title:'Segundo',description:'Detalhe B'}]}]};
const reply=(id,text='2',recipient=phone)=>({key:{id:'reply-fixture',remoteJid:recipient+'@s.whatsapp.net',fromMe:false},
  message:{extendedTextMessage:{text,contextInfo:{stanzaId:id}}}});
function fixture(t){const root=fs.mkdtempSync(path.join(os.tmpdir(),'orion-menu-fallback-'));let now=1000;
  t.after(()=>{const target=path.resolve(root);assert.ok(target.startsWith(path.resolve(os.tmpdir())+path.sep+'orion-menu-fallback-'));fs.rmSync(target,{recursive:true,force:true});});
  const open=(name='selection')=>makeEncryptedRetryStore({directory:path.join(root,name),secret,now:()=>now,max:10,maxPayloadBytes:8192});
  return {root,open,time:value=>{now=value;}};
}
test('a validated list becomes clear numbered text with exactly the original opaque choices',async()=>{
  const result=await buildChatbotContent(action,{sdk,interactiveMode:'text_fallback'});
  try{assert.equal(result.content.listMessage,null);assert.match(result.content.extendedTextMessage.text,/1\. Primeiro — Detalhe A/);
    assert.match(result.content.extendedTextMessage.text,/2\. Segundo — Detalhe B/);assert.match(result.content.extendedTextMessage.text,/citando esta mensagem/);
    assert.deepEqual(result.selectionIds,['ocb:synthetic-one','ocb:synthetic-two']);assert.doesNotMatch(result.content.extendedTextMessage.text,/ocb:/);
  }finally{result.dispose();}
});
test('conservative carousel keeps titles, bodies and choices without fetching or uploading unsupported tiles',async()=>{
  const cards=Array.from({length:3},(_,index)=>({selection_id:'ocb:card-'+index,title:'Opção '+index,body:'Detalhe '+index,button_text:'Selecionar',image_url:'https://never-fetch.example/image.jpg'}));
  const result=await buildChatbotContent({type:'carousel',body:'Escolha',cards},{sdk,interactiveMode:'text_fallback',
    fetchImage:()=>assert.fail('No remote image fetch'),prepareCarouselImage:()=>assert.fail('No preparation'),upload:()=>assert.fail('No upload')});
  try{assert.equal(result.content.interactiveMessage,null);assert.match(result.content.extendedTextMessage.text,/3\. Opção 2\nDetalhe 2/);
    assert.equal(result.selectionIds,undefined);assert.doesNotMatch(result.content.extendedTextMessage.text,/Responda|Selecionar/);
    assert.equal(result.wireMode,'text_fallback');}finally{result.dispose();}
});
test('the exact text protocol and encrypted menu bindings exist before relay and survive a valid-session restart',async t=>{
  const f=fixture(t),retry=f.open('proto'),selection=f.open(),built=await buildChatbotContent(action,{sdk,interactiveMode:'text_fallback'});
  let sent,calls=0;
  try{sent=await sendStoredProtocol({jid,store:retry,
    makeMessage:async to=>sdk.generateWAMessageFromContent(to,built.content,{userJid:other+'@s.whatsapp.net',messageId:'menu-protocol-fixture'}),
    onPrepared:message=>saveFallbackSelection(selection,message,phone,built.selectionIds),
    relay:async(to,content,options)=>{calls++;assert.equal(to,jid);assert.equal(options.additionalNodes,undefined);
      assert.deepEqual(sdk.proto.Message.encode(retry.get({id:options.messageId,remoteJid:jid})).finish(),sdk.proto.Message.encode(content).finish());
      assert.deepEqual(resolveFallbackSelection(selection,reply(options.messageId),phone),{origin:'contact',selection_id:'ocb:synthetic-two',quoted_message_id:options.messageId});
    }});
  }finally{built.dispose();}
  assert.equal(calls,1);const reopened=f.open();assert.deepEqual(resolveFallbackSelection(reopened,reply(sent.key.id),phone),
    {origin:'contact',selection_id:'ocb:synthetic-two',quoted_message_id:sent.key.id});
  const files=fs.readdirSync(path.join(f.root,'selection')).map(name=>fs.readFileSync(path.join(f.root,'selection',name),'utf8')).join('');
  assert.doesNotMatch(files,/ocb:|5567999999901|synthetic-two/);
});
test('unquoted, foreign, malformed and expired numeric replies never acquire selection authority',t=>{
  const f=fixture(t),store=f.open();saveFallbackSelection(store,{key:{id:'menu-id',fromMe:true}},phone,['ocb:one','ocb:two']);
  for(const message of [reply('unknown'),reply('menu-id','0'),reply('menu-id','2 extra'),reply('menu-id','3'),
    {key:{fromMe:false},message:{conversation:'2'}},{...reply('menu-id'),key:{fromMe:true}}])assert.equal(resolveFallbackSelection(store,message,phone),null);
  assert.equal(resolveFallbackSelection(store,reply('menu-id','2',other),other),null);
  assert.equal(resolveFallbackSelection(f.open('other-channel'),reply('menu-id'),phone),null);
  f.time(48*60*60*1000+1001);assert.equal(resolveFallbackSelection(f.open(),reply('menu-id'),phone),null);
});
test('failure to persist the menu mapping prevents relay instead of emitting an unusable choice',async()=>{
  let calls=0;const built=await buildChatbotContent(action,{sdk,interactiveMode:'text_fallback'});
  try{await assert.rejects(sendStoredProtocol({jid,store:{save:()=>true},makeMessage:async()=>({key:{id:'not-relayed',fromMe:true,remoteJid:jid},message:built.content}),
    onPrepared:message=>saveFallbackSelection({save:()=>false},message,phone,built.selectionIds),relay:async()=>{calls++;}}),/not_persisted/);
  }finally{built.dispose();}assert.equal(calls,0);
});
