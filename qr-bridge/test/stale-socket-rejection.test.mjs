import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';

const moduleUrl=new URL('../src/stale-socket-rejection.mjs',import.meta.url).href;
function run(source){return spawnSync(process.execPath,['--input-type=module','-e',source],{encoding:'utf8',timeout:5000});}

test('a retired socket retry cannot terminate the bridge',()=>{
  const child=run(`import {installStaleSocketRejectionGuard,StaleSocketRejection} from ${JSON.stringify(moduleUrl)};
    installStaleSocketRejectionGuard();Promise.reject(new StaleSocketRejection());
    setTimeout(()=>process.stdout.write('alive'),20);`);
  assert.equal(child.status,0,child.stderr);assert.equal(child.stdout,'alive');
});

test('unrelated unhandled failures remain fatal',()=>{
  const child=run(`import {installStaleSocketRejectionGuard} from ${JSON.stringify(moduleUrl)};
    installStaleSocketRejectionGuard();Promise.reject(Error('synthetic_storage_failure'));
    setTimeout(()=>process.stdout.write('alive'),20);`);
  assert.notEqual(child.status,0);assert.match(child.stderr,/synthetic_storage_failure/);
  assert.equal(child.stdout,'');
});
