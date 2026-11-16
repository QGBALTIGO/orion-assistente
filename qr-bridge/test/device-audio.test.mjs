import test from 'node:test';
import assert from 'node:assert/strict';
import {deviceAudioMetadata,withDeviceAudio,DEVICE_AUDIO_MEDIA_TYPES} from '../src/device-audio.mjs';

const voice=(patch={})=>({message:{audioMessage:{fileLength:20,seconds:1,mimetype:'audio/ogg; codecs=opus',...patch}}});
const base={client:{},logger:{},maxBytes:25_000_000,maxSeconds:1800};
test('forwarded audio MIME types are admitted only by the dedicated device decoder contract',async()=>{
  for(const mediaType of DEVICE_AUDIO_MEDIA_TYPES){
    const bytes=Buffer.from('synthetic media decoded only by the Python service');
    assert.equal(await withDeviceAudio(voice({mimetype:mediaType}),{...base,downloadMediaMessage:async()=>bytes},
      async payload=>{assert.equal(payload.mediaType,mediaType);return 'accepted for decode';}),'accepted for decode');
    assert.ok(bytes.every(n=>n===0));
  }
});
test('device audio admits up to the granted 30-minute limit and always wipes bytes without invoking assistant routes',async()=>{
  const bytes=Buffer.from('OggS synthetic bounded voice');let called=0;
  assert.equal(deviceAudioMetadata(voice({seconds:1800})).seconds,1800);
  const result=await withDeviceAudio(voice({seconds:1800}),{...base,downloadOptions:{httpsAgent:'device-only-proxy'},
    downloadMediaMessage:async(_message,kind,options)=>{assert.equal(kind,'stream');assert.equal(options.options.httpsAgent,'device-only-proxy');assert.equal(options.options.maxRedirects,0);return bytes;}},
    async({buffer,mediaType,seconds})=>{called++;assert.equal(buffer,bytes);assert.equal(mediaType,'audio/ogg');assert.equal(seconds,1800);return {text:'transcrição completa'};});
  assert.deepEqual(result,{text:'transcrição completa'});assert.equal(called,1);assert.ok(bytes.every(b=>b===0));
});
test('invalid metadata, missing capability limits and over-limit duration never download',async()=>{
  for(const patch of [{seconds:1801},{seconds:0},{seconds:NaN},{fileLength:25_000_001},{mimetype:'video/mp4'}]){
    assert.equal(await withDeviceAudio(voice(patch),{...base,downloadMediaMessage:()=>assert.fail('invalid audio')},()=>assert.fail('provider')),null);
  }
  for(const limits of [{maxSeconds:120},{maxBytes:0},{maxSeconds:1801},{maxSeconds:undefined}]){
    assert.equal(await withDeviceAudio(voice({seconds:121}),{...base,...limits,downloadMediaMessage:()=>assert.fail('ungranted duration')},()=>assert.fail('provider')),null);
  }
});
test('streaming byte limit and cancellation wipe every chunk and never expose a partial transcript',async()=>{
  const a=Buffer.alloc(10,1),b=Buffer.alloc(11,1);let destroyed=false;
  const stream={async *[Symbol.asyncIterator](){yield a;yield b;},destroy(){destroyed=true;}};
  assert.equal(await withDeviceAudio(voice(),{...base,maxBytes:20,downloadMediaMessage:async()=>stream},()=>assert.fail('oversize provider')),null);
  assert.ok(a.every(n=>n===0)&&b.every(n=>n===0));assert.equal(destroyed,true);
  const controller=new AbortController(),bytes=Buffer.from('bounded synthetic audio');
  assert.equal(await withDeviceAudio(voice(),{...base,signal:controller.signal,downloadMediaMessage:async()=>bytes},async()=>{controller.abort();return {text:'late'};}),null);
  assert.ok(bytes.every(n=>n===0));
});
test('revocation after download prevents provider use and provider failures erase memory',async()=>{
  const bytes=Buffer.from('bounded synthetic audio');let checks=0;
  await assert.rejects(withDeviceAudio(voice(),{...base,downloadMediaMessage:async()=>bytes,
    assertCurrent:async()=>{if(++checks>1)throw Error('revoked');}},()=>assert.fail('provider after revocation')),/revoked/);
  assert.ok(bytes.every(n=>n===0));
  const data=Buffer.from('bounded synthetic audio');
  await assert.rejects(withDeviceAudio(voice(),{...base,downloadMediaMessage:async()=>data},async()=>{throw Error('synthetic failure');}),/synthetic failure/);
  assert.ok(data.every(n=>n===0));
});
