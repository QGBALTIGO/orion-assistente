import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID,createHash} from 'node:crypto';
import * as sdk from '@whiskeysockets/baileys';
import {buildChatbotContent,validateChatbotAsset} from '../src/chatbot-message.mjs';
import {sendStoredProtocol} from '../src/reliable-send.mjs';
import {makeEncryptedRetryStore} from '../src/encrypted-retry-store.mjs';

process.env.ORION_RETRY_TEST_MODE='1';
const jid='5567999999901@s.whatsapp.net',self='5567999999902@s.whatsapp.net';
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const silent={debug(){},warn(){},info(){},error(){},trace(){},child(){return this;}};
function assets(){
  const entries=new Map(),copies=[],uploads=[];
  function add(kind,{size=40,thumbnailSize=40}={}){
    const buffer=Buffer.alloc(size,kind==='document'?65:83),thumbnail=['image','video','ptv'].includes(kind)?Buffer.alloc(thumbnailSize,74):null;
    const descriptor={id:randomUUID(),kind,mime:{image:'image/jpeg',sticker:'image/webp',video:'video/mp4',ptv:'video/mp4',audio:'audio/mpeg',document:'text/plain; charset=utf-8'}[kind],
      size:buffer.length,sha256:sha(buffer),...(!['document','audio'].includes(kind)?{width:512,height:512}:{}),
      ...(['video','ptv','audio'].includes(kind)?{duration_ms:2000}:{}),...(kind==='document'?{file_name:'resposta.txt'}:{}),
      thumbnail:thumbnail?{mime:'image/jpeg',size:thumbnail.length,sha256:sha(thumbnail)}:null};
    entries.set(descriptor.id,{buffer,thumbnail});return descriptor;
  }
  const options={sdk,jid,logger:silent,
    loadAsset:async(asset,{variant})=>{const row=entries.get(asset.id),buffer=Buffer.from(variant==='thumbnail'?row.thumbnail:row.buffer);copies.push(buffer);return buffer;},
    upload:async(file,metadata)=>{const bytes=fs.readFileSync(file);uploads.push({bytes,metadata});return {mediaUrl:'https://mmg.whatsapp.net/synthetic',directPath:'/synthetic'};}};
  return {add,options,copies,uploads};
}
function wire(content){return sdk.proto.Message.decode(sdk.proto.Message.encode(content).finish());}

test('pinned Baileys builds a real native list; labels never replace opaque selection IDs',async()=>{
  const {content,dispose}=await buildChatbotContent({type:'list',title:'Menu',description:'Escolha um item',footer:'Orion',button_text:'Abrir',
    sections:[{title:'Opções',rows:[{id:'orion:menu:one',title:'Primeira',description:'Detalhe'},{id:'orion:menu:two',title:'Segunda',description:''}]}]},assets().options);
  try{
    const decoded=wire(content);assert.equal(decoded.listMessage.listType,1);assert.equal(decoded.listMessage.sections[0].rows[0].rowId,'orion:menu:one');
    assert.equal(decoded.listMessage.buttonText,'Abrir');assert.equal(decoded.extendedTextMessage,null);
  }finally{dispose();}
});

test('native list limits and duplicate/forged row identities fail before any upload',async()=>{
  const f=assets(),row={id:'one',title:'One',description:''},base={type:'list',title:'Menu',description:'Choose',button_text:'Open',sections:[{title:'',rows:[row]}]};
  for(const action of [{...base,sections:[{rows:[row,row]}]},
    {...base,sections:[{rows:Array.from({length:11},(_,i)=>({...row,id:'row'+i}))}]},
    {...base,sections:[{rows:[{...row,id:'../../forged'}]}]}, {...base,button_text:'x'.repeat(21)}]){
    await assert.rejects(buildChatbotContent(action,f.options),/chatbot_message_invalid/);
  }
  assert.equal(f.uploads.length,0);
});

for(const kind of ['image','sticker','video','ptv','audio','document'])test(`real encrypted ${kind} protocol uses normalized private bytes and retains the exact type`,async()=>{
  const f=assets(),asset=f.add(kind);
  const built=await buildChatbotContent({type:kind,asset,...(['image','video'].includes(kind)?{caption:'Legenda'}:{})},f.options);
  try{
    const decoded=wire(built.content),value=decoded[kind==='ptv'?'ptvMessage':kind+'Message'];
    assert.ok(value);assert.equal(value.mimetype,asset.mime);assert.equal(Number(value.fileLength),asset.size);
    assert.equal(f.uploads.length,1);assert.equal(f.uploads[0].metadata.mediaType,kind==='ptv'?'video':kind);
    assert.equal(value.mediaKey.length,32);assert.equal(Buffer.compare(value.fileSha256,Buffer.from(asset.sha256,'hex')),0);
    assert.ok(!f.uploads[0].bytes.includes(Buffer.alloc(40,kind==='document'?65:83)),'Upload is encrypted by the actual pinned SDK');
    if(kind==='ptv')assert.equal(decoded.videoMessage,null);
    if(kind==='document')assert.equal(value.fileName,'resposta.txt');
    if(kind==='audio'){assert.equal(value.ptt,false);assert.equal(value.seconds,2);assert.equal(decoded.documentMessage,null);}
    if(asset.thumbnail)assert.equal(value.jpegThumbnail.length,asset.thumbnail.size);
  }finally{built.dispose();}
  assert.ok(f.copies.every(buffer=>buffer.every(byte=>byte===0)));
});

for(const [mime,name]of [['application/pdf','arquivo.pdf'],
  ['application/vnd.openxmlformats-officedocument.wordprocessingml.document','arquivo.docx'],
  ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','arquivo.xlsx'],
  ['application/vnd.android.package-archive','arquivo.apk'],['text/plain; charset=utf-8','tabela.csv']]){
 test(`document protocol preserves ${name} and its canonical MIME without opening the file`,async()=>{
  const f=assets(),asset=f.add('document');asset.mime=mime;asset.file_name=name;
  const result=await buildChatbotContent({type:'document',asset,caption:'Legenda',file_name:'outro-'+name},f.options);
  try{const value=wire(result.content).documentMessage;assert.equal(value.mimetype,mime);assert.equal(value.fileName,'outro-'+name);assert.equal(value.caption,'Legenda');assert.equal(f.uploads.length,1);}finally{result.dispose();}
 });
}

test('native location serializes geographic coordinates without fetching a map or uploading media',async()=>{
 const f=assets();const result=await buildChatbotContent({type:'location',latitude:-22.9,longitude:-43.2},f.options);
 try{const value=wire(result.content);assert.equal(value.locationMessage.degreesLatitude,-22.9);assert.equal(value.locationMessage.degreesLongitude,-43.2);assert.equal(value.extendedTextMessage,null);assert.equal(f.uploads.length,0);}finally{result.dispose();}
 for(const action of [{type:'location',latitude:91,longitude:0},{type:'location',latitude:0,longitude:-181},
   {type:'location',latitude:NaN,longitude:0},{type:'location',latitude:'0',longitude:0},{type:'location',latitude:false,longitude:0}]){
  await assert.rejects(buildChatbotContent(action,f.options),/invalid/);
 }
});

test('audio captions, active document MIME and mismatched filename cannot be silently delivered',async()=>{
 const f=assets(),audio=f.add('audio'),document=f.add('document');
 await assert.rejects(buildChatbotContent({type:'audio',asset:audio,caption:'must be a separate text action'},f.options),/invalid/);
 await assert.rejects(buildChatbotContent({type:'document',asset:{...document,mime:'text/html'},file_name:'arquivo.html'},f.options),/invalid/);
 await assert.rejects(buildChatbotContent({type:'document',asset:{...document,mime:'application/pdf',file_name:'arquivo.pdf'},file_name:'arquivo.exe'},f.options),/invalid/);
 assert.equal(f.uploads.length,0);
});

test('three-card carousel encodes actual interactive protocol with images and bound quick-reply IDs',async()=>{
  const f=assets(),descriptors=Array.from({length:3},()=>f.add('image')),raw=[];
  const action={type:'carousel',body:'Veja as opções',cards:descriptors.map((_,i)=>({title:'Card '+i,body:i?'':'Primeiro card',
    image_url:`https://images.example.com/card-${i}.jpg`,button_text:'Selecionar',selection_id:'orion:card:'+i}))};
  const built=await buildChatbotContent(action,{...f.options,
    fetchImage:async({url})=>{assert.match(url,/^https:\/\/images\.example\.com\/card-[012]\.jpg$/);const buffer=Buffer.from('raw image');raw.push(buffer);return {buffer};},
    prepareCarouselImage:async(index,buffer)=>{assert.equal(buffer.toString(),'raw image');return descriptors[index];}});
  try{
    const decoded=wire(built.content),interactive=decoded.viewOnceMessage.message.interactiveMessage;
    assert.equal(interactive.body.text,action.body);assert.equal(interactive.carouselMessage.cards.length,3);
    for(const [i,card]of interactive.carouselMessage.cards.entries()){
      assert.ok(card.header.imageMessage.mediaKey);assert.equal(card.header.title,'Card '+i);
      const button=card.nativeFlowMessage.buttons[0];assert.equal(button.name,'quick_reply');
      assert.deepEqual(JSON.parse(button.buttonParamsJson),{display_text:'Selecionar',id:'orion:card:'+i});
    }
    assert.ok(raw.every(buffer=>buffer.every(byte=>byte===0)));assert.equal(decoded.extendedTextMessage,null);
  }finally{built.dispose();}
  assert.ok(f.copies.every(buffer=>buffer.every(byte=>byte===0)));
});

test('asset substitution, wrong MIME or bytes and revocation during upload prevent a prepared message',async()=>{
  const f=assets(),asset=f.add('image');
  assert.throws(()=>validateChatbotAsset({...asset,mime:'image/png'},'image'),/invalid/);
  await assert.rejects(buildChatbotContent({type:'sticker',asset:f.add('sticker'),caption:'Do not silently discard this caption'},f.options),/invalid/);
  await assert.rejects(buildChatbotContent({type:'image',asset:{...asset,sha256:'0'.repeat(64)}},f.options),/invalid/);
  assert.equal(f.uploads.length,0);assert.ok(f.copies.every(buffer=>buffer.every(byte=>byte===0)));
  let active=true;
  await assert.rejects(buildChatbotContent({type:'image',asset},{...f.options,isCurrent:()=>active,
    upload:async(...args)=>{const result=await f.options.upload(...args);active=false;return result;}}),/revoked/);
  assert.ok(f.copies.every(buffer=>buffer.every(byte=>byte===0)));
});

test('exact native proto is encrypted before relay and an ambiguous native relay is never repeated',async t=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-chatbot-proto-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
  const store=makeEncryptedRetryStore({directory,secret:'synthetic-chatbot-cache-secret-'.repeat(3),maxPayloadBytes:512*1024,maxTotalBytes:96*1024*2000,
    serialize:value=>JSON.stringify(value,sdk.BufferJSON.replacer),deserialize:value=>JSON.parse(value,sdk.BufferJSON.reviver)});
  const f=assets(),action={type:'image',asset:f.add('image',{thumbnailSize:100000})},built=await buildChatbotContent(action,f.options);
  let attempts=0,prepared;
  try{
    await assert.rejects(sendStoredProtocol({jid,store,makeMessage:async()=>sdk.generateWAMessageFromContent(jid,built.content,{userJid:self,messageId:'NATIVE-SYNTHETIC'}),
      onPrepared:message=>{prepared=message;},relay:async(to,proto,options)=>{
        attempts++;assert.equal(to,jid);assert.equal(options.messageId,'NATIVE-SYNTHETIC');
        assert.deepEqual(wire(store.get({id:options.messageId})),wire(proto));
        const bytes=fs.readFileSync(path.join(directory,fs.readdirSync(directory)[0]));assert.ok(!bytes.includes(Buffer.from('NATIVE-SYNTHETIC')));
        throw Error('synthetic ambiguous relay');
      }}),/ambiguous/);
    assert.equal(attempts,1);assert.ok(prepared.message.imageMessage);assert.ok(store.get({id:'NATIVE-SYNTHETIC'}));
  }finally{built.dispose();}
});

test('native protocol authorization is checked after preparation/cache and immediately before relay',async()=>{
  let checks=0,relays=0,saves=0;
  await assert.rejects(sendStoredProtocol({jid,store:{save(){saves++;return true;}},assertAuthorized:async()=>{if(++checks===3)throw Error('hours changed');},
    makeMessage:async()=>sdk.generateWAMessageFromContent(jid,{extendedTextMessage:{text:'Test'}},{userJid:self,messageId:'AUTH-CHECK'}),
    relay:async()=>{relays++;}}),/hours changed/);
  assert.equal(saves,1);assert.equal(relays,0);
});
