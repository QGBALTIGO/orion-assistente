import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryPresentationFromPermit} from '../src/delivery-confirmation.mjs';
const id='a'.repeat(32);
const body='📝 *Seu pedido*\n\nTotal: *R$ 10,00*';
const suffix='\nResponda *CONFIRMO* para registrar o pedido ou *NÃO* para cancelar o resumo.';
function make(){return {authorized:true,type:'send_text',purpose:'delivery_reply',payload:{phone:'5567999999902',text:body+suffix,delivery_presentation:{version:1,id,action:{type:'reply_buttons',text:body,buttons:[{id:'oa1_'+id+'_0',title:'Confirmar pedido'},{id:'oa1_'+id+'_1',title:'Cancelar'}]}}}};}
const snapshot={capabilities:{delivery_reply_buttons:1}};
test('canonical delivery permit exposes exactly two buttons',()=>{const p=make();assert.deepEqual(deliveryPresentationFromPermit(p,snapshot),p.payload.delivery_presentation);});
test('old bridges and other niches keep canonical text',()=>{const p=make();assert.equal(deliveryPresentationFromPermit(p,{capabilities:{}}),null);assert.equal(deliveryPresentationFromPermit({...p,purpose:'appointment_reply'},snapshot),null);});
test('rejects forged labels, IDs and unrelated body',()=>{const p=make();p.payload.delivery_presentation.action.buttons[0].title='Outro';assert.equal(deliveryPresentationFromPermit(p,snapshot),null);const q=make();q.payload.delivery_presentation.action.buttons[0].id='oa1_'+'b'.repeat(32)+'_0';assert.equal(deliveryPresentationFromPermit(q,snapshot),null);const t=make();t.payload.text='outro pedido';assert.equal(deliveryPresentationFromPermit(t,snapshot),null);});
test('rejects unauthenticated and overlong presentations',()=>{const p=make();p.authorized=false;assert.equal(deliveryPresentationFromPermit(p,snapshot),null);const q=make();q.payload.delivery_presentation.action.text='🙂'.repeat(600);q.payload.text=q.payload.delivery_presentation.action.text+suffix;assert.equal(deliveryPresentationFromPermit(q,snapshot),null);});
