import test from 'node:test';import assert from 'node:assert/strict';
import {makeBoundedCache} from '../src/bounded-cache.mjs';
import {makePilotDeviceCache} from '../src/pilot-recipient.mjs';
test('retry/device counters expire and stay bounded without retaining timers',()=>{
 let now=1;const c=makeBoundedCache({max:2,ttlMs:100,now:()=>now});
 c.set('a',1);c.set('b',2);c.set('c',3);assert.equal(c.size,2);assert.equal(c.get('a'),undefined);
 now=102;assert.equal(c.get('b'),undefined);assert.equal(c.size,0);
 c.set('short',5,.01);now+=11;assert.equal(c.get('short'),undefined);
 c.set('long',6,9999);now+=101;assert.equal(c.get('long'),undefined);
 c.set('a',1);c.flushAll();assert.equal(c.size,0);assert.equal(c.set('x'.repeat(3000),1),false);
});
test('verified device scopes do not contaminate another concurrent send',async()=>{
 const base=makeBoundedCache(),c=makePilotDeviceCache(base);base.set('peer',['outside']);
 await Promise.all([['a'],['b']].map(snapshot=>c.run(new Map([['peer',snapshot]]),async()=>{
  await Promise.resolve();assert.deepEqual(c.get('peer'),snapshot);
 })));
 assert.deepEqual(c.get('peer'),['outside']);
});
