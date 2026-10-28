import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel,jsonResponse} from './panel-harness.mjs';
import {flush} from './harness.mjs';

async function access(t,respond){
  const h=await makePanel({respond:(path,method,data)=>{
    if(path==='/auth/me')return jsonResponse({detail:'Entre na conta'},401);
    if(path==='/auth/features')return {registration:true,passwordRecovery:true};
    return respond?.(path,method,data);
  }});
  t.after(()=>{h.close();assert.deepEqual(h.errors,[]);});await flush();return h;
}

test('login continues to accept a username and preserves password manager attributes and exact credentials',async t=>{
  let payload;
  const h=await access(t,(path,method,data)=>{if(path==='/auth/login'){payload=data;return jsonResponse({detail:'Teste isolado'},401);}});
  const username=h.element('#username');assert.equal(username.type,'text');assert.equal(username.autocomplete,'username');
  assert.equal(username.placeholder,'Usuário ou nome@email.com');assert.equal(h.element('#password').autocomplete,'current-password');
  h.fill('#username','Usuario-Sintético');h.fill('#password',' Senha fictícia 9! ');h.submit('#loginForm');await flush();
  assert.equal(payload.username,'Usuario-Sintético');assert.equal(payload.password,' Senha fictícia 9! ');
});

test('register validates email and sends full pasted OTP with a leading zero without touching the person name',async t=>{
  let registered,verified;
  const h=await access(t,(path,method,data)=>{
    if(path==='/auth/register'){registered=data;return {codeRequested:true};}
    if(path==='/auth/verify'){verified=data;return {message:'Confirmação sintética'};}
  });
  h.click('#authTabRegister');h.fill('#registerName',"João D'Ávila-Souza");h.fill('#registerEmail','inválido');
  h.fill('#registerPassword','Senha fictícia 9!');h.fill('#registerConfirm','Senha fictícia 9!');h.submit('#registerForm');await flush();
  assert.equal(registered,undefined);assert.equal(h.element('#registerEmail').getAttribute('aria-invalid'),'true');
  h.fill('#registerEmail','  teste@example.invalid  ');h.submit('#registerForm');await flush();
  assert.equal(registered.name,"João D'Ávila-Souza");assert.equal(registered.email,'teste@example.invalid');
  const otp=h.element('#verifyCode');assert.equal(otp.type,'text');assert.equal(otp.inputMode,'numeric');assert.equal(otp.autocomplete,'one-time-code');assert.equal(otp.hasAttribute('maxlength'),false);
  h.fill('#verifyCode','012 345');assert.equal(otp.value,'012345');h.submit('#verifyForm');await flush();
  assert.equal(verified.code,'012345');assert.equal(verified.email,'teste@example.invalid');
});

test('recovery keeps the reset code textual and rejects a malformed complete paste before the synthetic endpoint',async t=>{
  let reset;const h=await access(t,(path,method,data)=>{
    if(path==='/auth/password/forgot')return {message:'Código sintético'};
    if(path==='/auth/password/reset'){reset=data;return {message:'Redefinição sintética'};}
  });
  h.click('#authForgotLink');h.fill('#forgotEmail','teste@example.invalid');h.submit('#forgotForm');await flush();
  h.fill('#resetPassword','Outra senha fictícia 9!');h.fill('#resetConfirm','Outra senha fictícia 9!');
  h.fill('#resetCode','01x2345');h.submit('#resetForm');await flush();assert.equal(reset,undefined);assert.equal(h.element('#resetCode').value,'01x2345');
  h.fill('#resetCode','001 234');h.submit('#resetForm');await flush();assert.equal(reset.code,'001234');assert.equal(reset.password,'Outra senha fictícia 9!');
  for(const id of ['registerEmail','forgotEmail','resetEmail'])assert.equal(h.element('#'+id).placeholder,'nome@email.com');
});

test('pairing phone has one country code for DDD55 and explicit international numbers, without real calls',async t=>{
  const writes=[];const h=await makePanel({respond:(path,method,data)=>{if(path==='/api/v1/qr/pair'){writes.push(data);return {code:'ABCD-1234',expiresIn:90,status:'pair_code'};}}});
  t.after(()=>{h.close();assert.deepEqual(h.errors,[]);});
  h.fill('#pairPhone','+55 (55) 99123-4567');h.submit('#pairForm');await flush();assert.equal(writes[0].phone,'5555991234567');
  h.fill('#pairPhone','+44 20 7946 0958');h.submit('#pairForm');await flush();assert.equal(writes[1].phone,'442079460958');
});
