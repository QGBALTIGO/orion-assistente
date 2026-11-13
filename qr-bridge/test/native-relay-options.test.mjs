import test from 'node:test';import assert from 'node:assert/strict';
import {nativeRelayOptions} from '../src/native-relay-options.mjs';
import {sendStoredProtocol} from '../src/reliable-send.mjs';
import {makeMessageRetryStore} from '../src/message-retry-store.mjs';
import {proto} from '@whiskeysockets/baileys';
test('native list has business routing metadata without changing its choices or encrypted proto',async()=>{
 const jid='5567999999901@s.whatsapp.net',content=proto.Message.fromObject({listMessage:{title:'Synthetic',description:'Choose',buttonText:'Open',listType:1,sections:[{title:'Items',rows:[{title:'One',rowId:'row-1'}]}]}});
 const expected=proto.Message.encode(content).finish(),store=makeMessageRetryStore();let calls=0;
 await sendStoredProtocol({jid,store,makeMessage:async()=>({key:{id:'list-1',remoteJid:jid,fromMe:true},message:content}),relay:async(to,body,options)=>{
  calls++;assert.equal(to,jid);assert.deepEqual(options.additionalNodes,[{tag:'biz',attrs:{},content:[{tag:'list',attrs:{type:'product_list',v:'2'}}]}]);
  assert.deepEqual(proto.Message.encode(body).finish(),expected);assert.deepEqual(proto.Message.encode(store.get({id:'list-1'})).finish(),expected);
 }});assert.equal(calls,1);
});
test('carousel metadata is fixed and ordinary text, media and URLs cannot inject nodes',()=>{
 assert.equal(nativeRelayOptions({interactiveMessage:{carouselMessage:{cards:[]}}}).additionalNodes[0].content[0].tag,'interactive');
 for(const message of [{conversation:'https://example.com'},{audioMessage:{}},{imageMessage:{}},{extendedTextMessage:{text:'biz'}},{additionalNodes:[{tag:'evil'}]}])assert.deepEqual(nativeRelayOptions(message),{});
});
