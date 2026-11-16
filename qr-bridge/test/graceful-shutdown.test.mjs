import test from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import vm from 'node:vm';
const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8').replace(/\r\n/g,'\n');
const start=source.indexOf('let shutdownPromise;'),end=source.indexOf("for(const signal of ['SIGTERM','SIGINT'])",start);
assert.ok(start>0&&end>start);const actual=source.slice(start,end);
function harness({pending=false}={}){
  const events=[],timers=new Map();let closeHttp,serial=0;
  const context=vm.createContext({stopping:false,resumeTimer:'resume',
    clearInterval:id=>events.push('stop-resume:'+id),stopPrimaryTransport:reason=>events.push('fence-primary:'+reason),
    channelRoutes:{close:()=>events.push('close-channels')},
    server:{close:callback=>{events.push('close-http');closeHttp=callback;},
      closeIdleConnections:()=>events.push('close-idle'),closeAllConnections:()=>events.push('force-http')},
    saveQueue:Promise.resolve(),assistantQueues:new Map(pending?[['inflight',new Promise(()=>{})]]:[]),
    setTimeout:(fn,ms)=>{const id=++serial;timers.set(id,{fn,ms});return id;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(actual,context);return {context,events,timers,close:()=>closeHttp()};
}
test('the real shutdown fences transports before draining requests and is idempotent without logout',async()=>{
  const h=harness(),a=h.context.shutdown(),b=h.context.shutdown();assert.equal(a,b);assert.equal(h.context.stopping,true);
  assert.deepEqual(h.events.slice(0,4),['stop-resume:resume','fence-primary:shutdown','close-channels','close-http']);
  h.close();await a;assert.equal(h.timers.size,0);assert.equal(h.events.filter(event=>event==='force-http').length,1);
  assert.equal(h.events.some(event=>/logout|delete|relink/.test(event)),false);
});
test('an interrupted in-flight operation cannot keep shutdown beyond its explicit eight-second drain',async()=>{
  const h=harness({pending:true}),closing=h.context.shutdown();h.close();const deadline=[...h.timers.values()][0];
  assert.equal(deadline.ms,8000);deadline.fn();await closing;assert.equal(h.events.at(-1),'force-http');assert.equal(h.timers.size,0);
});
