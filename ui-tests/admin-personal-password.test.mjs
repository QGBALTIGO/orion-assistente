import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel} from './panel-harness.mjs';
import {flush} from './harness.mjs';

test('admin changes own password from the client center, never through a customer account',async t=>{
  const calls=[];
  const h=await makePanel({role:'admin',initialRoute:'member',respond(path,method,payload){
    if(path==='/auth/admin/password'&&method==='POST'){
      calls.push(payload);
      return {ok:true,requires_login:true,message:'Senha alterada. Entre novamente.'};
    }
  }});
  t.after(()=>h.close());await flush();
  h.click('#adminClientCenter [data-admin-control=own-password]');
  assert.equal(h.element('#adminCurrentPassword').type,'password');
  assert.equal(h.element('#adminNewPassword').autocomplete,'new-password');
  h.fill('#adminCurrentPassword','synthetic-old-value');
  h.fill('#adminNewPassword','synthetic-new-value');
  h.fill('#adminConfirmPassword','synthetic-new-value');
  h.submit('[data-admin-form=own-password]');await flush();
  assert.deepEqual(calls,[{current_password:'synthetic-old-value',new_password:'synthetic-new-value',confirm_password:'synthetic-new-value'}]);
  assert.equal(h.element('#loginView').classList.contains('hidden'),false);
  assert.match(h.element('#authGlobalMessage').textContent,/Senha alterada/);
  assert.deepEqual(h.errors,[]);
});

test('ordinary member cannot see platform password form',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member'});t.after(()=>h.close());await flush();
 assert.equal(h.document.querySelector('[data-admin-control=own-password]'),null);
});
