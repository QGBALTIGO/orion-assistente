import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {randomUUID,createHash} from 'node:crypto';
import {requestChatbotJSON,fetchChatbotImage,CHATBOT_HTTP_MAX_BYTES} from '../src/chatbot-network.mjs';
import {createChannelBroker} from '../src/channel-broker.mjs';

function network({status=200,headers={},chunks=[Buffer.from('{"reply":"Resposta"}')],end=true}={}){
  const calls=[];const responses=[];
  const request=(url,options,callback)=>{
    const req=new EventEmitter();
    req.destroy=()=>{req.destroyed=true;req.emit('error',Error('destroyed'));};
    req.end=body=>{
      const call={url:new URL(url),options,body:body?Buffer.from(body):null};calls.push(call);
      options.lookup(url.hostname,{all:true},(error,records)=>{if(error)return req.emit('error',error);call.records=records;
        queueMicrotask(()=>{
          const res=new EventEmitter();responses.push(res);res.statusCode=status;res.headers={'content-type':'application/json',...headers};
          res.destroy=()=>{res.destroyed=true;res.emit('aborted');};callback(res);
          if(res.destroyed)return;
          for(const chunk of chunks){if(!res.destroyed)res.emit('data',chunk);}
          if(end&&!res.destroyed)res.emit('end');
        });
      });
    };
    return req;
  };
  return {calls,responses,options:{requestHttp:request,requestHttps:request,lookup:async()=>[{address:'8.8.8.8',family:4}]}};
}

test('authorized POST uses validated pinned IP, fixed headers and a bounded JSON response',async()=>{
  const f=network(),id=randomUUID(),data={appName:'Orion',senderPhone:'5567999999901',senderMessage:'Olá'};
  const result=await requestChatbotJSON({url:'https://hooks.example.com/respond',method:'POST',data,requestId:id,...f.options});
  assert.deepEqual(result.json,{reply:'Resposta'});assert.equal(f.calls.length,1);
  const call=f.calls[0];assert.deepEqual(call.records,[{address:'8.8.8.8',family:4}]);assert.equal(call.url.hostname,'hooks.example.com');
  assert.equal(call.options.agent,false);assert.equal(call.options.headers['X-Orion-Operation-Id'],id);assert.equal(call.options.headers['Accept-Encoding'],'identity');
  assert.deepEqual(JSON.parse(call.body.toString()),data);
});

test('GET maps the same context to encoded query fields and refuses oversized URLs before connection',async()=>{
  const f=network();await requestChatbotJSON({url:'https://hooks.example.com/a?preserved=1',method:'GET',data:{senderMessage:'Olá & oi',messageDateTime:123},...f.options});
  assert.equal(f.calls[0].body,null);assert.equal(f.calls[0].url.searchParams.get('senderMessage'),'Olá & oi');assert.equal(f.calls[0].url.searchParams.get('preserved'),'1');
  await assert.rejects(requestChatbotJSON({url:'https://hooks.example.com/',method:'GET',data:{senderMessage:'x'.repeat(2048)},...f.options}));
  assert.equal(f.calls.length,1);
});

test('private destinations, mixed DNS answers and redirect to internal metadata never open another hop',async()=>{
  const f=network();for(const url of ['http://127.0.0.1/a','http://169.254.169.254/latest/meta-data','http://api.railway.internal/a','http://[::1]/'])
    await assert.rejects(requestChatbotJSON({url,...f.options}));
  await assert.rejects(requestChatbotJSON({url:'https://public.example.com',...f.options,
    lookup:async()=>[{address:'8.8.8.8',family:4},{address:'10.0.0.1',family:4}]}));
  assert.equal(f.calls.length,0);
  const redirect=network({status:302,headers:{location:'http://169.254.169.254/'}});
  await assert.rejects(requestChatbotJSON({url:'https://public.example.com/',...redirect.options}),/REDIRECT/);assert.equal(redirect.calls.length,1);
});

test('JSON response bounds reject wrong types, encoding, truncation, depth and excessive bytes',async()=>{
  const cases=[{headers:{'content-type':'text/html'}}, {headers:{'content-encoding':'gzip'}},
    {headers:{'content-length':String(CHATBOT_HTTP_MAX_BYTES+1)}}, {headers:{'content-length':'100'}},
    {chunks:[Buffer.alloc(CHATBOT_HTTP_MAX_BYTES+1,65)]}, {chunks:[Buffer.from('["not object"]')]},
    {chunks:[Buffer.from([0xff,0xfe])]}, {chunks:[Buffer.from('{"x":'.repeat(18)+'{}'+'}'.repeat(18))]},
    {chunks:[Buffer.from('{"n":1e9999}')]}, {chunks:[Buffer.from('{"id":9007199254740993}')]},
    ...[{reply:'before\u0000after'},{reply:'\ud800'},{reply:'\udfff'},
        {'bad\u0000key':'value'},{'\ud800':'value'},{['x'.repeat(2049)]:'value'}].map(value=>({chunks:[Buffer.from(JSON.stringify(value))]}))];
  for(const spec of cases){const f=network(spec);await assert.rejects(requestChatbotJSON({url:'https://public.example.com/',...f.options}));assert.equal(f.calls.length,1);}
  const valid=network({chunks:[Buffer.from(JSON.stringify({reply:'Ação 🙂 completa',['🙂'.repeat(2048)]:'value'}))]});
  assert.equal((await requestChatbotJSON({url:'https://public.example.com/',...valid.options})).json.reply,'Ação 🙂 completa');
});

test('revocation after DNS or while awaiting response prevents results; timeout makes one attempt',async()=>{
  const f=network(),abort=new AbortController();
  await assert.rejects(requestChatbotJSON({url:'https://public.example.com/',...f.options,signal:abort.signal,
    lookup:async()=>{abort.abort();return [{address:'8.8.8.8',family:4}];}}));assert.equal(f.calls.length,0);
  const delayed=network({end:false});await assert.rejects(requestChatbotJSON({url:'https://public.example.com/',...delayed.options,timeoutMs:10}),/TIMEOUT/);
  assert.equal(delayed.calls.length,1);assert.equal(delayed.responses[0].destroyed,true);
  const stale=network();await assert.rejects(requestChatbotJSON({url:'https://public.example.com/',...stale.options,assertCurrent:async()=>{throw Error('scope changed');}}),/scope changed/);
  assert.equal(stale.calls.length,0);
});

test('carousel fetch returns bounded raw bytes for the authenticated decoder, never passes its URL to SDK',async()=>{
  const f=network({headers:{'content-type':'application/octet-stream'},chunks:[Buffer.from('untrusted image bytes')]});
  const image=await fetchChatbotImage({url:'https://images.example.com/raw',...f.options});assert.equal(image.buffer.toString(),'untrusted image bytes');image.buffer.fill(0);
  const large=network({headers:{'content-length':'8000001'}});await assert.rejects(fetchChatbotImage({url:'https://images.example.com/raw',...large.options}),/TOO_LARGE/);
});

test('private asset and image preparation bind all scopes, revision, variant and bounded raw bytes',async()=>{
  const calls=[],channel=randomUUID(),owner=randomUUID(),key='synthetic-chatbot-key-'.repeat(3),data=Buffer.from('normalized private image');
  const scope={device_id:randomUUID(),generation:randomUUID(),settings_revision:2,chatbot_revision:3,operation_id:randomUUID()};
  const asset={id:randomUUID(),size:data.length,mime:'image/jpeg',sha256:createHash('sha256').update(data).digest('hex')};
  const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',fetchImpl:async(url,options)=>{
    calls.push({url,options});return options.method==='GET'?new Response(data,{headers:{'content-type':'image/jpeg','content-length':String(data.length)}}):new Response(JSON.stringify(asset),{headers:{'content-type':'application/json'}});
  }});
  const result=await broker.deviceChatbotAsset(channel,key,{owner,fence:4},{...scope,asset,variant:'original'});
  assert.deepEqual(result,data);assert.equal(calls[0].url.pathname,`/internal/qr/channels/${channel}/device/chatbot/assets/${asset.id}/content`);
  assert.equal(calls[0].url.search,'?variant=original');assert.equal(calls[0].options.headers['X-Orion-Chatbot-Revision'],'3');
  assert.equal(calls[0].options.headers['X-Orion-Device-Effect'],scope.operation_id);assert.equal(calls[0].options.redirect,'error');
  await broker.deviceChatbotCarouselImage(channel,key,{owner,fence:4},{...scope,card_index:2,buffer:data});
  assert.equal(calls[1].options.headers['X-Orion-Chatbot-Card'],'2');assert.equal(calls[1].options.headers['Content-Type'],'application/octet-stream');assert.ok(Buffer.isBuffer(calls[1].options.body));
  await assert.rejects(broker.deviceChatbotCarouselImage(channel,key,{owner,fence:4},{...scope,card_index:3,buffer:data}));
  assert.throws(()=>broker.deviceChatbotAsset(channel,key,{owner,fence:4},{...scope,asset:{...asset,id:'../other'},variant:'original'}));assert.equal(calls.length,2);
});

test('private asset download rejects stream overflow and MIME substitution',async()=>{
  const scope={device_id:randomUUID(),generation:randomUUID(),settings_revision:1,chatbot_revision:1,operation_id:randomUUID()},asset={id:randomUUID(),size:2,mime:'image/jpeg'};
  for(const headers of [{'content-type':'text/plain'},{'content-type':'image/jpeg'}]){
    const broker=createChannelBroker({apiUrl:'http://api.railway.internal/',fetchImpl:async()=>new Response(Buffer.from('oversized'),{headers})});
    await assert.rejects(broker.deviceChatbotAsset(randomUUID(),'synthetic-key-'.repeat(4),{owner:randomUUID(),fence:1},{...scope,asset}));
  }
});
