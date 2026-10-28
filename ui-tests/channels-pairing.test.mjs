import test from 'node:test';
import assert from 'node:assert/strict';
import {makeChannels,item,listing,runtime,tenantA,tenantB,channelA,channelB,png,flush,deferred} from './channels-harness.mjs';

const base='/api/v1/tenants/'+tenantA+'/qr-channels';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
const pairCalls=h=>h.calls.filter(call=>call.path.endsWith('/pair'));
const status=(row,state='qr_ready',pair=true)=>({...runtime(row,state),capabilities:{pair}});
const result=(row,extra={})=>({...runtime(row,'pairing'),code:'abcd1234',expires_in_ms:79000,...extra});
const submitPair=(h,phone='(67) 99999-9901')=>{h.fill('[data-qc-pair-phone]',phone);h.element('[data-qc-form=pair]').dispatchEvent(new h.window.Event('submit',{bubbles:true,cancelable:true}));};

test('opening or changing connection methods never starts a session or requests QR/code',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());
  assert.equal(h.element('[data-qc-method=code]').getAttribute('aria-pressed'),'true');
  h.click('[data-qc-method=qr]');h.click('[data-qc-method=code]');await h.tick(15000);
  assert.deepEqual(writes(h),[]);assert.equal(h.calls.some(call=>/\/(qr|pair)$/.test(call.path)),false);
  assert.equal(h.element('[data-qc-disclosure=instructions]').querySelectorAll('li').length,7);assert.equal(h.element('[data-qc-disclosure=instructions]').open,true);assert.match(h.element('[data-qc-disclosure=instructions] summary').textContent,/Tutorial: 7 passos/);
});

test('operational capability is discovered by status; the list alone cannot offer pairing',async t=>{
  const row=item({enabled:true});
  for(const capability of [undefined,false]){
    const h=await makeChannels({data:listing([row],{capabilities:{pair:true}}),respond:path=>path.endsWith('/status')?{...runtime(row,'qr_ready'),...(capability===undefined?{}:{capabilities:{pair:capability}})}:undefined});
    assert.equal(h.document.querySelector('[data-qc-method=code]'),null);assert.equal(h.document.querySelector('[data-qc-form=pair]'),null);assert.deepEqual(writes(h),[]);h.close();
  }
  const h=await makeChannels({data:listing([row],{capabilities:{pair:false}}),respond:path=>path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());
  assert.ok(h.element('[data-qc-form=pair]'));assert.equal(h.element('[data-qc-form=pair] button[type=submit]').disabled,false);
});

test('code generation waits for fresh connection readiness and cannot enable or start a channel',async t=>{
  for(const state of ['connecting','stopped','pairing','pair_failed','pair_expired','logged_out']){
    const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row,state):undefined});
    assert.equal(h.element('[data-qc-form=pair] button[type=submit]').disabled,true);submitPair(h);await flush();assert.deepEqual(writes(h),[]);h.close();
  }
  const h=await makeChannels({data:listing([item()])});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-qc-form=pair]'),null);
});

test('an explicitly started capable session defaults to code without fetching QR imagery',async t=>{
  const row=item({enabled:true});let started=false;
  const h=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/start')&&method==='POST'?(started=true,status(row,'connecting')):path.endsWith('/status')?status(row,started?'qr_ready':'stopped',started):undefined});t.after(()=>h.close());
  h.click('[data-qc-action=start]');h.submit();await flush();await h.tick(5000);
  assert.equal(h.element('[data-qc-method=code]').getAttribute('aria-pressed'),'true');assert.equal(h.calls.some(call=>/\/(qr|pair)$/.test(call.path)),false);
  assert.deepEqual(writes(h),[{path:base+'/'+channelA+'/start',method:'POST',data:{expected_revision:1}}]);
});

test('explicit pairing normalizes phone and code, copies the visible code and never exposes it in state/storage',async t=>{
  const row=item({enabled:true});let paired=false;
  const h=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?(paired=true,result(row)):path.endsWith('/status')?status(row,paired?'pairing':'qr_ready'):undefined});t.after(()=>h.close());
  submitPair(h,'(11) 98765-4321');await flush();assert.deepEqual(pairCalls(h),[{path:base+'/'+channelA+'/pair',method:'POST',data:{expected_revision:1,phone:'5511987654321'}}]);
  assert.equal(h.element('[data-qc-pair-code]').textContent,'ABCD-1234');h.click('[data-qc-action=copy-code]');await flush();assert.deepEqual(h.copied,['ABCD-1234']);
  const observed=JSON.stringify({state:h.controller.getState(),logs:h.logs,local:[...Object.keys(h.window.localStorage)],session:[...Object.keys(h.window.sessionStorage)],url:h.window.location.href});
  assert.equal(observed.includes('ABCD-1234'),false);assert.equal(observed.includes('5511987654321'),false);assert.equal(observed.includes('(11) 98765-4321'),false);
  assert.equal(h.window.localStorage.length,0);assert.equal(h.window.sessionStorage.length,0);
  h.click('[data-qc-method=qr]');assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);assert.equal(h.element('[data-qc-action=qr]').disabled,true);
  h.click('[data-qc-method=code]');assert.equal(h.element('[data-qc-pair-code]').textContent,'ABCD-1234');assert.equal(pairCalls(h).length,1);
});

test('international phone requires explicit country prefix and sends digits only',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?result(row):path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());
  submitPair(h,'+1 202 555 0101');await flush();assert.equal(pairCalls(h)[0].data.phone,'12025550101');
});

test('expired code disappears and never reissues automatically, even while readiness returns',async t=>{
  const row=item({enabled:true});let paired=false,expired=false;
  const h=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?(paired=true,result(row,{expires_in_ms:1500})):path.endsWith('/status')?status(row,paired&&!expired?'pairing':'qr_ready'):undefined});t.after(()=>h.close());
  submitPair(h);await flush();expired=true;await h.tick(1500);assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);assert.equal(h.controller.getState().hasPair,false);
  await h.tick(15000);assert.equal(pairCalls(h).length,1);assert.match(h.element('#channels').textContent,/código expirou/i);
});

test('unchanged polling preserves the phone node, value, selection and expanded instructions',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());
  const input=h.fill('[data-qc-pair-phone]','(67) 99999-9901');input.focus();input.setSelectionRange(6,9);const details=h.element('[data-qc-disclosure=instructions]');details.open=true;
  await h.tick(15000);assert.equal(h.element('[data-qc-pair-phone]'),input);assert.equal(h.document.activeElement,input);assert.equal(input.value,'(67) 99999-9901');assert.equal(input.selectionStart,6);assert.equal(input.selectionEnd,9);assert.equal(h.element('[data-qc-disclosure=instructions]'),details);assert.equal(details.open,true);
});

test('the seven-step tutorial is initially open for both methods and stays closed after polling or a status transition',async t=>{
  const row=item({enabled:true});let phase='connecting';
  const qr=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row,'qr_ready',false):undefined});
  assert.equal(qr.controller.getState().method,'qr');assert.equal(qr.element('[data-qc-disclosure=instructions]').open,true);assert.equal(qr.element('[data-qc-disclosure=instructions]').querySelectorAll('li').length,7);qr.close();
  const h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row,phase):undefined});t.after(()=>h.close());
  const details=h.element('[data-qc-disclosure=instructions]');assert.equal(details.open,true);assert.equal(details.querySelectorAll('li').length,7);
  assert.ok(h.element('[data-qc-form=pair]')); // The tutorial does not gate the phone form.
  details.open=false;h.element('[data-qc-disclosure=preferences]').open=true;await h.tick(5000);assert.equal(h.element('[data-qc-disclosure=instructions]'),details);assert.equal(details.open,false);
  phase='qr_ready';await h.tick(5000);assert.equal(h.element('[data-qc-disclosure=instructions]').open,false);assert.equal(h.element('[data-qc-disclosure=preferences]').open,true);
  h.click('[data-qc-method=qr]');assert.equal(h.element('[data-qc-disclosure=instructions]').open,false);assert.equal(h.element('[data-qc-disclosure=instructions]').querySelectorAll('li').length,7);
});

test('polling preserves a valid QR node and expiry never fetches another image',async t=>{
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());
  h.click('[data-qc-method=qr]');h.click('[data-qc-action=qr]');await flush();const img=h.element('[data-qc-qr] img');assert.ok(img.src.startsWith(png));
  await h.tick(15000);assert.equal(h.element('[data-qc-qr] img'),img);await h.tick(31000);assert.equal(h.document.querySelector('[data-qc-qr]'),null);assert.equal(h.calls.filter(call=>call.path.endsWith('/qr')).length,1);
});

test('malformed, stale or foreign pairing responses are withheld and never retried',async t=>{
  const row=item({enabled:true});
  for(const invalid of [{channel_id:channelB},{revision:2},{code:'<svg onload=1>'},{code:'123'},{expires_in_ms:90001},{expires_in_ms:0},{status:'connected'}]){
    const ready=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?result(row,invalid):path.endsWith('/status')?status(row):undefined});
    submitPair(ready);await flush();assert.equal(ready.document.querySelector('[data-qc-pair-code]'),null);assert.equal(ready.controller.getState().uncertain,true);assert.equal(pairCalls(ready).length,1);await ready.tick(15000);assert.equal(pairCalls(ready).length,1);ready.close();
  }
});

test('an uncertain pairing outcome blocks duplicate mutations and reconciles only by safe status reads',async t=>{
  const row=item({enabled:true});let attempted=false,ready=false;
  const h=await makeChannels({data:listing([row]),respond:(path,method)=>{if(path.endsWith('/pair')&&method==='POST'){attempted=true;throw Error('response lost');}if(path.endsWith('/status'))return status(row,attempted&&!ready?'pair_failed':'qr_ready');}});t.after(()=>h.close());
  submitPair(h);await flush();assert.equal(h.controller.getState().uncertain,true);assert.equal(h.element('[data-qc-form=pair] button[type=submit]').disabled,true);
  h.click('[data-qc-action=refresh]');await flush();await h.tick(10000);assert.equal(h.controller.getState().uncertain,true);assert.equal(pairCalls(h).length,1);
  ready=true;await h.tick(5000);assert.equal(h.controller.getState().uncertain,false);assert.equal(pairCalls(h).length,1);assert.equal(h.element('[data-qc-form=pair] button[type=submit]').disabled,false);
});

test('viewer, other owner and legacy alias cannot expose or submit pairing',async t=>{
  for(const overrides of [{can_manage:false},{legacy_transport:true}]){
    const row=item({enabled:true,...overrides}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row):undefined});
    assert.equal(h.document.querySelector('[data-qc-form=pair]'),null);assert.equal(h.document.querySelector('[data-qc-method=code]'),null);assert.deepEqual(writes(h),[]);h.close();
  }
  const row=item({enabled:true}),h=await makeChannels({data:listing([row],{access:{can_manage:false}}),respond:path=>path.endsWith('/status')?status(row):undefined});t.after(()=>h.close());assert.equal(h.document.querySelector('[data-qc-form=pair]'),null);assert.deepEqual(writes(h),[]);
});

test('late pairing cannot resurrect a code after tenant change, hiding or unmount',async t=>{
  for(const leave of ['tenant','hide','unmount']){
    const row=item({enabled:true}),late=deferred();
    const h=await makeChannels({data:listing([row]),respond:(path,method)=>path.startsWith('/api/v1/tenants/'+tenantB)?listing([item({id:channelB,tenant_id:tenantB,label:'Empresa B'})]):path.endsWith('/pair')&&method==='POST'?late.promise:path.endsWith('/status')?status(row):undefined});
    submitPair(h);await flush();if(leave==='tenant')await h.controller.setTenant(tenantB);if(leave==='hide')await h.visibility(true);if(leave==='unmount')h.controller.unmount();late.resolve(result(row));await flush();
    assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);assert.equal(h.controller.getState().hasPair,false);assert.equal(pairCalls(h).length,1);h.close();
  }
});

test('connected, revoked and provider-failed states clear active code and preserve separate assistant status',async t=>{
  for(const next of ['connected','logged_out','pair_failed']){
    const row=item({enabled:true,assistant_enabled:true});let phase='qr_ready';
    const h=await makeChannels({data:listing([row]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?(phase='pairing',result(row)):path.endsWith('/status')?status(row,phase):undefined});
    submitPair(h);await flush();phase=next;await h.tick(5000);assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);assert.equal(h.controller.getState().hasPair,false);assert.equal(h.controller.getState().items[0].assistant_enabled,true);assert.equal(pairCalls(h).length,1);assert.equal(h.document.querySelector('[data-qc-action=start]'),null);if(next!=='connected'){h.click('[data-qc-method=qr]');assert.equal(h.element('[data-qc-action=qr]').disabled,true);}h.close();
  }
});

test('an active pairing attempt or unknown status cannot start another connection',async t=>{
  for(const phase of ['connecting','qr_ready','pairing','pair_failed','pair_expired','logged_out']){
    const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>path.endsWith('/status')?status(row,phase):undefined});
    assert.equal(h.document.querySelector('[data-qc-action=start]'),null);
    const injected=h.document.createElement('button');injected.dataset.qcAction='start';h.element('#channels').append(injected);injected.click();assert.equal(h.document.querySelector('[data-qc-form=confirm]'),null);assert.deepEqual(writes(h),[]);h.close();
  }
  const row=item({enabled:true}),h=await makeChannels({data:listing([row]),respond:path=>{if(path.endsWith('/status'))throw Object.assign(Error('unavailable'),{status:503});}});t.after(()=>h.close());
  assert.equal(h.element('[data-qc-action=start]').disabled,true);h.click('[data-qc-action=start]');assert.equal(h.document.querySelector('[data-qc-form=confirm]'),null);assert.deepEqual(writes(h),[]);
});

test('switching channels clears pairing credentials and drafts instead of carrying them to another device',async t=>{
  const a=item({enabled:true}),b=item({id:channelB,label:'Segundo canal',enabled:true});let paired=false;
  const h=await makeChannels({data:listing([a,b]),respond:(path,method)=>path.endsWith('/pair')&&method==='POST'?(paired=true,result(a)):path.endsWith('/status')?status(path.includes(channelB)?b:a,path.includes(channelB)||!paired?'qr_ready':'pairing'):undefined});t.after(()=>h.close());
  submitPair(h,'(11) 98765-4321');await flush();assert.equal(h.controller.getState().hasPair,true);h.click('[data-qc-channel="'+channelB+'"]');await flush();
  assert.equal(h.controller.getState().hasPair,false);assert.equal(h.element('[data-qc-pair-phone]').value,'');assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);
  h.click('[data-qc-channel="'+channelA+'"]');await flush();assert.equal(h.document.querySelector('[data-qc-pair-code]'),null);assert.equal(h.element('[data-qc-pair-phone]').value,'');assert.equal(pairCalls(h).length,1);
});

test('a failed QR read after an explicit start does not create a background retry loop',async t=>{
  const row=item({enabled:true});let started=false;
  const h=await makeChannels({data:listing([row]),respond:(path,method)=>{if(path.endsWith('/start')&&method==='POST'){started=true;return runtime(row,'connecting');}if(path.endsWith('/status'))return runtime(row,started?'qr_ready':'stopped');if(path.endsWith('/qr'))throw Object.assign(Error('unavailable'),{status:503});}});t.after(()=>h.close());
  h.click('[data-qc-action=start]');h.submit();await flush();await h.tick(30000);assert.equal(h.calls.filter(call=>call.path.endsWith('/qr')).length,1);assert.equal(writes(h).length,1);assert.equal(h.controller.getState().hasQR,false);
});
