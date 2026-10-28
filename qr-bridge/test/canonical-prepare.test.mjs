/** Exercise the real pre-reconnect guards without constructing a socket. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../src/server.mjs',import.meta.url),'utf8');
const begin=source.indexOf('    if(path==="/internal/reconnect"&&req.method==="POST"){');
const end=source.indexOf('      // Invalidate old listeners first;',begin);
assert.ok(begin>0&&end>begin);
const guard=source.slice(begin,end)+'return "allowed";}';
function check(status){
  const context=vm.createContext({path:'/internal/reconnect',req:{method:'POST',headers:{'x-orion-canonical-prepare':'1'}},
    res:{},status,visibleStatus:()=>status,pairAt:Date.now(),Date,reply:(_res,code)=>code});
  return vm.runInContext('(function(){'+guard+'})()',context);
}
for(const status of ['connected','pairing','connecting','reconnecting','starting','error','logged_out','pair_failed'])
  test(`canonical reconnect cannot reset ${status} or escalate to recovery`,()=>assert.equal(check(status),409));
for(const status of ['qr_ready','qr_expired','qr_delayed','pair_expired','stopped'])
  test(`canonical reconnect permits only the explicit safe ${status} state`,()=>assert.equal(check(status),'allowed'));
