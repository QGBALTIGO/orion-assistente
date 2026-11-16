/** Synthetic reference-model property tests, not integration against Orion. */
import assert from 'node:assert/strict';
import test from 'node:test';
import fc from 'fast-check';

const overlap = (a, b) => a[0] < b[1] && b[0] < a[1];
const valid = (s) => {
  const interval = [s.start, s.start + s.duration];
  if (s.start < 0 || s.duration <= 0 || interval[1] > 1440) return false;
  if (!s.manual && (s.start < s.opens || interval[1] > s.closes ||
       overlap(interval, [720, 780]))) return false;
  return !s.busy.some(b => overlap(interval, b));
};
const arb = fc.record({
  start: fc.integer({min:0,max:1400}),
  duration: fc.constantFrom(10,15,20,30,40,45,60,75,90,120,180,240),
  opens: fc.constantFrom(480,510,540,600),
  closes: fc.constantFrom(1020,1080,1140,1200),
  busy: fc.array(fc.tuple(fc.integer({min:0,max:1300}),
    fc.integer({min:0,max:1400})),{maxLength:5}),
  manual: fc.boolean()
}).map(s=>({...s,busy:s.busy.map(([a,b])=>[Math.min(a,b),Math.max(a,b)])}));

test('10,000 generated model bookings never overlap existing intervals',()=>{
  fc.assert(fc.property(arb,s=>{
    if(valid(s)) for(const busy of s.busy)
      assert.equal(overlap([s.start,s.start+s.duration],busy),false);
  }),{numRuns:10000,seed:2601112});
});

test('10,000 generated longer services cannot create new available slots',()=>{
  fc.assert(fc.property(arb,s=>{
    if(valid({...s,duration:s.duration+15})) assert.equal(valid(s),true);
  }),{numRuns:10000,seed:2601113});
});

test('10,000 manual overrides preserve busy-interval protection',()=>{
  fc.assert(fc.property(arb,s=>{
    const candidate={...s,manual:true};
    if(valid(candidate)) for(const busy of s.busy)
      assert.equal(overlap([s.start,s.start+s.duration],busy),false);
  }),{numRuns:10000,seed:2601114});
});

test('10,000 retries yield only one synthetic model booking',()=>{
  fc.assert(fc.property(fc.integer({min:480,max:1100}),
    fc.constantFrom(20,40,60,90),fc.integer({min:1,max:20}),
    (start,duration,n)=>{
      const ledger=[],keys=new Map();
      const submit=(key)=>{
        if(keys.has(key))return keys.get(key);
        if(ledger.some(x=>overlap(x,[start,start+duration])))return null;
        const booking={id:'fixture-01',start,duration};
        ledger.push([start,start+duration]); keys.set(key,booking);
        return booking;
      };
      const original=submit('idempotency-key');
      for(let i=0;i<n;i++)assert.deepEqual(submit('idempotency-key'),original);
      assert.equal(ledger.length,1);
    }),{numRuns:10000,seed:2601115});
});

test('10,000 finance samples keep product outside commission base',()=>{
  fc.assert(fc.property(fc.integer({min:0,max:500000}),
    fc.integer({min:0,max:100000}),fc.integer({min:0,max:20000}),
    fc.integer({min:0,max:10000}),(service,product,discount,bp)=>{
      const base=Math.max(0,service-Math.min(service,discount));
      const commission=Math.floor((base*bp+5000)/10000);
      assert.ok(commission>=0 && commission<=base);
      if(service===0)assert.equal(commission,0);
      assert.ok(Number.isSafeInteger(Math.max(0,service+product-discount)));
    }),{numRuns:10000,seed:2601116});
});

test('10,000 model tenant-scope queries cannot return another tenant',()=>{
  fc.assert(fc.property(fc.array(fc.record({
    tenant:fc.integer({min:1,max:200}),record:fc.integer()
  }),{maxLength:50}),fc.integer({min:1,max:200}),(rows,tenant)=>{
    const result=rows.filter(x=>x.tenant===tenant);
    assert.ok(result.every(x=>x.tenant===tenant));
  }),{numRuns:10000,seed:2601117});
});

test('Peixoto 9:00 +60 prevents 9:40 +40 for same professional',()=>{
  const s={start:540,duration:60,opens:540,closes:1080,
    manual:false,busy:[]};
  assert.equal(valid(s),true);
  assert.equal(valid({...s,start:580,duration:40,busy:[[540,600]]}),false);
  assert.equal(valid({...s,start:600,duration:40,busy:[[540,600]]}),true);
});