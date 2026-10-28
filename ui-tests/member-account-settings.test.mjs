import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel,jsonResponse} from './panel-harness.mjs';
import {flush,tenantA} from './harness.mjs';

const original={name:'Barbearia Exemplo',contact_phone:'+5567999990000'};
const changes=h=>h.calls.filter(c=>c.method!=='GET');

test('Minha conta loads existing business name and formatted phone on mobile without writes',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member',respond:path=>{
  if(path==='/api/v1/account/business-identity')return original;
 }});t.after(()=>h.close());await flush();
 assert.equal(h.element('#memberBusinessName').value,original.name);
 assert.equal(h.element('#memberBusinessPhone').value,'(67) 99999-0000');
 assert.equal(h.element('#memberCurrentPassword').type,'password');
 assert.equal(h.element('#memberNewPassword').autocomplete,'new-password');
 assert.equal(h.element('#memberConfirmPassword').autocomplete,'new-password');
 assert.equal(changes(h).length,0);
 assert.deepEqual(h.errors,[]);
});

test('owner edits business name and phone once, without changing session or account identity',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member',respond:(path,method,payload)=>{
  if(path==='/api/v1/account/business-identity'&&method==='GET')return original;
  if(path==='/api/v1/account/business-identity'&&method==='PUT')
   return {ok:true,name:payload.name,contact_phone:'+5567998887777'};
 }});t.after(()=>h.close());await flush();
 h.fill('#memberBusinessName','Barbearia Nova');
 h.fill('#memberBusinessPhone','(67) 99888-7777');
 h.submit('#memberBusinessForm');await flush();
 const writes=changes(h);
 assert.equal(writes.length,1);
 assert.equal(writes[0].path,'/api/v1/account/business-identity');
 assert.equal(writes[0].data.expected_name,original.name);
 assert.equal(writes[0].data.expected_phone,original.contact_phone);
 assert.equal(writes[0].data.name,'Barbearia Nova');
 assert.equal(writes[0].options.headers['X-Orion-CSRF'],'synthetic-mobile-csrf');
 assert.match(h.element('#memberBusinessFeedback').textContent,/sucesso/);
 assert.equal(h.element('#memberBusinessPhone').value,'(67) 99888-7777');
 assert.equal(h.element('#memberProfileName').textContent,'Conta sintética');
 assert.deepEqual(h.errors,[]);
});

test('wrong current password preserves new fields and never marks a change successful',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member',respond:(path,method)=>{
  if(path==='/api/v1/account/business-identity')return original;
  if(path==='/auth/account/password'&&method==='POST')return jsonResponse({detail:'Senha atual incorreta'},403);
 }});t.after(()=>h.close());await flush();
 h.fill('#memberCurrentPassword','Incorrect#123');
 h.fill('#memberNewPassword','Different#123');
 h.fill('#memberConfirmPassword','Different#123');
 h.submit('#memberPasswordForm');await flush();
 assert.equal(changes(h).length,1);
 assert.equal(h.element('#memberCurrentPassword').value,'');
 assert.equal(h.element('#memberNewPassword').value,'Different#123');
 assert.match(h.element('#memberPasswordFeedback').textContent,/incorreta/);
 assert.equal(h.element('#loginView').classList.contains('hidden'),true);
 assert.deepEqual(h.errors,[]);
});

test('password mismatch and unchanged password are blocked without server requests',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member',respond:path=>{
  if(path==='/api/v1/account/business-identity')return original;
 }});t.after(()=>h.close());await flush();
 h.fill('#memberCurrentPassword','Same#Password123');
 h.fill('#memberNewPassword','Same#Password123');
 h.fill('#memberConfirmPassword','Same#Password123');
 h.submit('#memberPasswordForm');await flush();
 assert.match(h.element('#memberPasswordFeedback').textContent,/diferente/);
 h.fill('#memberNewPassword','New#Password123');
 h.submit('#memberPasswordForm');await flush();
 assert.match(h.element('#memberPasswordFeedback').textContent,/coincidem/);
 assert.equal(changes(h).length,0);
});

test('successful password change invalidates local view and requires fresh login',async t=>{
 const h=await makePanel({role:'member',initialRoute:'member',respond:(path,method)=>{
  if(path==='/api/v1/account/business-identity')return original;
  if(path==='/auth/account/password'&&method==='POST')
   return {ok:true,requires_login:true,message:'Senha alterada com sucesso. Entre novamente.'};
 }});t.after(()=>h.close());await flush();
 h.fill('#memberCurrentPassword','Old#Password123');
 h.fill('#memberNewPassword','New#Password123');
 h.fill('#memberConfirmPassword','New#Password123');
 h.submit('#memberPasswordForm');await flush();
 assert.equal(changes(h).length,1);
 assert.equal(h.element('#loginView').classList.contains('hidden'),false);
 assert.equal(h.element('#memberNewPassword').value,'');
 assert.equal(h.element('#memberCurrentPassword').value,'');
 assert.match(h.element('#authNotice')?.textContent||h.document.body.textContent,/Senha alterada/);
 assert.deepEqual(h.errors,[]);
});
