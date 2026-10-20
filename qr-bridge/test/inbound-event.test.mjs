import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeInbound,updateEvent,callEvent,safetyControl,queueControl} from '../src/inbound-event.mjs';
const key={id:'source-id',fromMe:false,remoteJid:'5567999999901@s.whatsapp.net'};
test('oversized input keeps no useful prefix and retains a finite opt-out control',()=>{
 const result=normalizeInbound({message:{conversation:'A'.repeat(1500)+' não quero promoções'}});
 assert.equal(result.text,'');assert.equal(result.event.oversized,true);assert.equal(result.event.control,'marketing');
 assert.equal(safetyControl('não quero parar as mensagens'),null);
});
test('view-once media never exposes its caption or download URL',()=>{
 const result=normalizeInbound({message:{viewOnceMessageV2:{message:{imageMessage:{caption:'pode confirmar',url:'never-download'}}}}});
 assert.equal(result.event.kind,'view_once');assert.equal(result.text,'');assert.equal(JSON.stringify(result).includes('never-download'),false);
});
test('actual v6 edit and revoke shapes retain target and have distinct bounded event identities',()=>{
 const edit=updateEvent(key,{message:{editedMessage:{message:{conversation:'sim'}}}},()=>100000);
 assert.equal(normalizeInbound(edit).event.kind,'edited');assert.equal(normalizeInbound(edit).event.target,key.id);
 const deleted=updateEvent(key,{message:null,messageStubType:1});assert.equal(normalizeInbound(deleted).event.kind,'deleted');
 assert.notEqual(deleted.key.id,updateEvent({...key,id:'another-id'},{message:null,messageStubType:1}).key.id);
 assert.equal(updateEvent({...key,fromMe:true},{message:null,messageStubType:1}),null);
 assert.equal(updateEvent(key,{status:3}),null);
});
test('calls are notifications only; groups and completed calls cannot become ingress',()=>{
 assert.equal(callEvent({id:'c',from:key.remoteJid,status:'offer'}).message.orionAdapterCall,true);
 assert.equal(callEvent({id:'c',from:key.remoteJid,status:'terminate'}),null);
 assert.equal(callEvent({id:'c',from:key.remoteJid,status:'offer',isGroup:true}),null);
});
test('reference metadata is kept outside the text and invalid Unicode is rejected whole',()=>{
 const r=normalizeInbound({message:{extendedTextMessage:{text:'sim',contextInfo:{stanzaId:'old-proposal'}}}});
 assert.equal(r.text,'sim');assert.equal(r.event.reply_to,'old-proposal');assert.equal(r.special,true);
 assert.equal(normalizeInbound({message:{conversation:'pode confirmar\ud800'}}).text,'');
});

test('bounded control queue preserves the strongest opt-out and human request under repeated weaker controls',()=>{
 const queue=[],item=(id,text)=>[{key:{id}},{text,event:{version:1,kind:'text'}}];
 queueControl(queue,item('all','parar tudo'));queueControl(queue,item('human','quero falar com atendente'));
 for(let i=0;i<100;i++)queueControl(queue,item('birthday-'+i,'não quero aniversário'));
 assert.equal(queue.length,2);assert.deepEqual(queue.map(x=>x[0].key.id),['all','human']);
 assert.equal(safetyControl('não quero parar mensagens. Na verdade pare promoções'),'marketing');
 assert.equal(safetyControl('quero falar com atendente, não, espera'),null);
});
