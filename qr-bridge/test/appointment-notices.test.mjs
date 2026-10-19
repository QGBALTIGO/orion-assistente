import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {randomUUID} from 'node:crypto';
import {makeAppointmentNotices} from '../src/appointment-notices.mjs';
const tenant='11111111-1111-4111-8111-111111111111',account='5511999999999';
function setup(t,overrides={}){
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'orion-notice-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 const current={enabled:true,connected:true,tenant_id:tenant,account_phone:account};let sends=0;
 const options={directory,secret:'synthetic-private-secret-at-least-32-characters',state:()=>current,
 send:async(p,text,opts)=>{sends++;assert.equal(opts.forceReliable,true);assert.ok(opts.isCurrent());opts.onNoticePrepared({key:{id:'MESSAGE123'}});return {accepted:true,id:'out:MESSAGE123'}},...overrides};
 const journal=makeAppointmentNotices(options);
 const input={id:randomUUID(),tenant_id:tenant,account_phone:account,phone:'5511888888888',text:'Atualização sintética, sem envio real',expires_at:Date.now()+3600000};
 return {journal,input,current,options,count:()=>sends};
}
test('concurrent requests and replay relay exactly once',async t=>{
 const x=setup(t);const results=await Promise.all(Array.from({length:12},()=>x.journal.dispatch(x.input)));
 assert.equal(x.count(),1);assert.ok(results.every(r=>r.status==='sent'));
 assert.equal((await x.journal.dispatch(x.input)).message_id,'MESSAGE123');assert.equal(x.count(),1);
});
test('restart keeps idempotency and receipt mapping',async t=>{
 const x=setup(t);await x.journal.dispatch(x.input);const restored=makeAppointmentNotices(x.options);
 await restored.dispatch(x.input);assert.equal(x.count(),1);restored.observe('MESSAGE123','delivered');
 assert.equal(restored.receipt(x.input).status,'delivered');restored.observe('MESSAGE123','server_accepted');
 assert.equal(restored.receipt(x.input).status,'delivered');restored.observe('MESSAGE123','read');assert.equal(restored.receipt(x.input).status,'read');
});
test('same key cannot change recipient or text',async t=>{
 const x=setup(t);await x.journal.dispatch(x.input);
 for(const change of [{phone:'5511777777777'},{text:'Outra mensagem'},{account_phone:'5511000000000'}])
  await assert.rejects(x.journal.dispatch({...x.input,...change}),/notice_key_conflict/);
 assert.equal(x.count(),1);
});
test('uncertain relay is never automatically repeated, including after restart',async t=>{
 let count=0;const x=setup(t,{send:async(p,text,opts)=>{count++;opts.onNoticePrepared({key:{id:'LOST123'}});throw Error('simulated timeout')}});
 assert.equal((await x.journal.dispatch(x.input)).status,'uncertain');
 await x.journal.dispatch(x.input);await makeAppointmentNotices(x.options).dispatch(x.input);assert.equal(count,1);
 x.journal.observe('LOST123','delivered');assert.equal(x.journal.receipt(x.input).status,'delivered');
});
test('definitely unsent capacity failure can retry the same key',async t=>{
 let calls=0;const x=setup(t,{send:async(p,text,opts)=>{if(calls++===0)throw Error('capacity');opts.onNoticePrepared({key:{id:'RETRY123'}});return {accepted:true,id:'out:RETRY123'}}});
 assert.equal((await x.journal.dispatch(x.input)).status,'not_sent');assert.equal((await x.journal.dispatch(x.input)).status,'sent');assert.equal(calls,2);
});
test('receipt arriving before send result stays delivered',async t=>{
 let journal;const x=setup(t,{send:async(p,text,opts)=>{opts.onNoticePrepared({key:{id:'FAST123'}});journal.observe('FAST123','read');return {accepted:true,id:'out:FAST123'}}});journal=x.journal;
 assert.equal((await journal.dispatch(x.input)).status,'read');
});
test('wrong tenant, account and disabled channel never send',async t=>{
 const x=setup(t);
 await assert.rejects(x.journal.dispatch({...x.input,tenant_id:randomUUID()}),/scope/);
 await assert.rejects(x.journal.dispatch({...x.input,account_phone:'5511444444444'}),/account_changed/);
 x.current.enabled=false;assert.equal(x.journal.readiness().ready,false);await assert.rejects(x.journal.dispatch(x.input),/not_connected/);
 assert.equal(x.count(),0);
});
test('disconnection invalidates the transport guard',async t=>{
 let current;const x=setup(t,{send:async(p,text,opts)=>{current.connected=false;assert.equal(opts.isCurrent(),false);throw Error('disconnected')}});current=x.current;
 assert.equal((await x.journal.dispatch(x.input)).status,'not_sent');
});
test('expired notice or invalid payload never creates a send',async t=>{
 const x=setup(t);
 for(const change of [{expires_at:Date.now()-1},{text:''},{text:'x'.repeat(1201)},{id:'../../escape'},{phone:'5511@s.whatsapp.net'},{extra:true}])
  await assert.rejects(x.journal.dispatch({...x.input,...change}));
 assert.equal(x.count(),0);
});
test('encrypted durable journal contains no plaintext and is not silently pruned',async t=>{
 const x=setup(t,{max:1});await x.journal.dispatch(x.input);
 const bytes=fs.readFileSync(path.join(x.options.directory,x.input.id+'.enc'));
 assert.ok(!bytes.includes(Buffer.from(x.input.text)));assert.ok(!bytes.includes(Buffer.from(account)));
 assert.equal(fs.statSync(path.join(x.options.directory,x.input.id+'.enc')).mode&0o777,0o600);
 await assert.rejects(x.journal.dispatch({...x.input,id:randomUUID()}),/storage/);assert.equal(x.count(),1);
 assert.equal((await x.journal.dispatch(x.input)).status,'sent');
});
test('corruption fails closed rather than generating another copy',async t=>{
 const x=setup(t);await x.journal.dispatch(x.input);fs.writeFileSync(path.join(x.options.directory,x.input.id+'.enc'),'broken');
 const restored=makeAppointmentNotices(x.options);assert.equal(restored.readiness().ready,false);
 await assert.rejects(restored.dispatch(x.input),/storage/);assert.equal(x.count(),1);
});
test('unknown receipt is not a delivery confirmation',t=>{
 const x=setup(t);assert.equal(x.journal.receipt(x.input).status,'unknown');x.journal.observe('not-existing','read');assert.equal(x.journal.receipt(x.input).status,'unknown');
});
