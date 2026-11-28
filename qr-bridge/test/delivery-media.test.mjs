import test from 'node:test';
import assert from 'node:assert/strict';
import {deliveryMedia,deliveryLiveUpdate,withDeliveryImage} from '../src/delivery-media.mjs';
const m=message=>({key:{id:'qa1',fromMe:false,remoteJid:'5567999999901@s.whatsapp.net'},message});

test('media metadata rejects invalid coordinates, oversize images and view-once pixels',()=>{
  assert.deepEqual(deliveryMedia(m({locationMessage:{degreesLatitude:90.00001,degreesLongitude:0}})),{kind:'invalid_location'});
  assert.deepEqual(deliveryMedia(m({locationMessage:{degreesLatitude:'20',degreesLongitude:0}})),{kind:'invalid_location'});
  assert.deepEqual(deliveryMedia(m({imageMessage:{fileLength:8_000_001,mimetype:'image/jpeg'}})),{kind:'image_unavailable'});
  assert.deepEqual(deliveryMedia(m({viewOnceMessage:{message:{imageMessage:{fileLength:5,mimetype:'image/jpeg'}}}})),{kind:'view_once'});
  assert.equal(deliveryMedia({...m({locationMessage:{degreesLatitude:0,degreesLongitude:0}}),key:{fromMe:true}}),null);
});
test('photo download requires valid metadata, bounds stream and wipes buffer on failure',async()=>{
  const photo=m({imageMessage:{fileLength:5,mimetype:'image/png'}});
  let source=Buffer.from('abcde'),calls=0;
  const opts={client:{},maxBytes:5,downloadMediaMessage:async()=>source,assertCurrent:async()=>{}};
  await assert.rejects(withDeliveryImage(photo,opts,async({buffer})=>{calls++;assert.equal(buffer.toString(),'abcde');throw Error('synthetic provider failure');}));
  assert.equal(calls,1);assert.ok(source.every(x=>x===0));
  source=Buffer.from('abcdef');
  assert.equal(await withDeliveryImage(photo,opts,async()=>assert.fail('overlimit upload')),null);
  assert.ok(source.every(x=>x===0));
});
test('live location update requires actual incoming provider update with valid sequence',()=>{
  const key=m({}).key;
  assert.equal(deliveryLiveUpdate({key,update:{message:{liveLocationMessage:{degreesLatitude:0,degreesLongitude:0}}}},10000),null);
  const actual=deliveryLiveUpdate({key,update:{message:{liveLocationMessage:{degreesLatitude:0,degreesLongitude:0,sequenceNumber:8}}}},10000);
  assert.deepEqual(deliveryMedia(actual),{kind:'live_location',latitude:0,longitude:0,source_id:'qa1',sequence:8,update:true});
});
