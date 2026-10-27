import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM,VirtualConsole} from 'jsdom';
import {flush,deferred} from './harness.mjs';

const invitation='a'.repeat(43);
async function setup(t,{verified=false,authenticated=true,respond}={}){
  const html=await readFile(new URL('../app/static/orion-invite.html',import.meta.url),'utf8');
  const js=await readFile(new URL('../app/static/orion-invite.js',import.meta.url),'utf8');
  const errors=[],console=new VirtualConsole();console.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(html,{url:'https://orionagenda.pro/convite#token='+invitation,runScripts:'outside-only',virtualConsole:console});
  const {window}=dom,document=window.document,calls=[];
  const state={me:{account:authenticated,email:authenticated?'synthetic@example.invalid':null,name:'Pessoa sintética',email_verified:verified,csrf:'synthetic-csrf'}};
  window.fetch=async(path,options={})=>{
    const body=options.body?JSON.parse(options.body):null;calls.push({path,options,body});
    let result=respond?await respond(path,body,state):undefined;
    if(result===undefined){
      if(path.endsWith('/preview'))result={business_name:'Empresa sintética',role:'editor',expires_at:'2030-01-01T12:00:00Z',status:'pending',email_bound:true};
      else if(path==='/auth/me')result=state.me;
      else if(path==='/auth/verify/resend')result={message:'Solicitação registrada. Aguarde o código no seu e-mail.'};
      else if(path==='/auth/verify'){state.me={...state.me,email_verified:true};result={ok:true};}
      else if(path.endsWith('/accept'))result={status:'claimed'};
      else throw Error('Unexpected synthetic request: '+path);
    }
    const status=typeof result.status==='number'?result.status:200;
    return {ok:status<400,status,json:async()=>result};
  };
  t.after(()=>window.close());window.eval(js);await flush();
  return {window,document,calls,state,errors,click:id=>document.getElementById(id).click(),submit:()=>document.getElementById('inviteVerifyForm').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}))};
}

test('opening an invitation never sends an email or accepts before the account proves its email',async t=>{
  const h=await setup(t);
  assert.deepEqual(h.calls.map(c=>c.path),['/api/v1/team-invitations/preview','/auth/me']);
  assert.equal(h.document.getElementById('inviteVerifyForm').hidden,false);
  assert.equal(h.document.getElementById('inviteAccept').hidden,true);
  assert.equal(h.document.getElementById('inviteVerifyEmail').textContent,'synthetic@example.invalid');
  h.click('inviteAccept');await flush();assert.equal(h.calls.some(c=>c.path.endsWith('/accept')),false);
  assert.equal(h.errors.length,0);
});

test('requesting a code is explicit, single-flight and described as queued rather than delivered',async t=>{
  const pending=deferred();
  const h=await setup(t,{respond:path=>path==='/auth/verify/resend'?pending.promise:undefined});
  h.click('inviteResend');h.click('inviteResend');await flush();
  const requests=h.calls.filter(c=>c.path==='/auth/verify/resend');assert.equal(requests.length,1);
  assert.deepEqual(requests[0].body,{email:'synthetic@example.invalid'});
  assert.equal(requests[0].options.headers['X-Orion-CSRF'],'synthetic-csrf');
  assert.equal(h.document.getElementById('inviteResend').disabled,true);
  pending.resolve({message:'Solicitação registrada. Aguarde o código no seu e-mail.'});await flush();
  assert.match(h.document.getElementById('inviteVerifyStatus').textContent,/Solicitação registrada/);
  assert.doesNotMatch(h.document.getElementById('inviteVerifyStatus').textContent,/entregue|Código enviado/);
  assert.equal(h.document.getElementById('inviteResend').disabled,false);
});

test('confirming six digits refreshes the same account and still requires explicit invitation acceptance',async t=>{
  const h=await setup(t);h.document.getElementById('inviteCode').value='123456';h.submit();await flush();
  assert.deepEqual(h.calls.find(c=>c.path==='/auth/verify').body,{email:'synthetic@example.invalid',code:'123456'});
  assert.equal(h.calls.filter(c=>c.path==='/auth/me').length,2);
  assert.equal(h.document.getElementById('inviteVerifyForm').hidden,true);
  assert.equal(h.document.getElementById('inviteAccept').hidden,false);
  assert.equal(h.calls.some(c=>c.path.endsWith('/accept')),false,'verification never auto-accepts');
  assert.equal(h.window.location.hash,'#token='+invitation);
  assert.equal(h.document.getElementById('inviteCode').value,'');
  h.click('inviteAccept');await flush();
  assert.equal(h.calls.filter(c=>c.path.endsWith('/accept')).length,1);
  assert.equal(h.window.location.hash,'');
  assert.match(h.document.getElementById('inviteMessage').textContent,/Aguarde a aprovação do proprietário/);
  assert.ok(h.calls.every(c=>c.options.credentials==='same-origin'));
});

test('invalid or expired OTP retains the invitation and offers correction without an automatic resend',async t=>{
  const h=await setup(t,{respond:path=>path==='/auth/verify'?{status:400,detail:'Código inválido ou expirado'}:undefined});
  h.document.getElementById('inviteCode').value='12x';h.submit();await flush();
  assert.equal(h.calls.some(c=>c.path==='/auth/verify'),false);
  h.document.getElementById('inviteCode').value='123456';h.submit();await flush();
  assert.match(h.document.getElementById('inviteVerifyStatus').textContent,/inválido ou expirado/);
  assert.equal(h.document.getElementById('inviteVerifyForm').hidden,false);
  assert.equal(h.document.getElementById('inviteAccept').hidden,true);
  assert.equal(h.window.location.hash,'#token='+invitation);
  assert.equal(h.calls.some(c=>c.path==='/auth/verify/resend'||c.path.endsWith('/accept')),false);
});

test('verified accounts skip OTP while technical or unauthenticated sessions cannot accept',async t=>{
  const verified=await setup(t,{verified:true});
  assert.equal(verified.document.getElementById('inviteVerifyForm').hidden,true);
  assert.equal(verified.document.getElementById('inviteAccept').hidden,false);
  assert.equal(verified.calls.some(c=>c.path==='/auth/verify/resend'),false);
  const anonymous=await setup(t,{authenticated:false});
  assert.equal(anonymous.document.getElementById('inviteVerifyForm').hidden,true);
  assert.equal(anonymous.document.getElementById('inviteAccept').hidden,true);
  assert.equal(anonymous.document.getElementById('inviteLogin').hidden,false);
  anonymous.click('inviteLogin');
  assert.equal(anonymous.window.sessionStorage.getItem('orion_pending_invitation'),invitation);
});

test('a verification response alone cannot unlock acceptance without an authoritative verified account',async t=>{
  const h=await setup(t,{respond:path=>path==='/auth/verify'?{ok:true}:undefined});
  h.document.getElementById('inviteCode').value='123456';h.submit();await flush();
  assert.equal(h.document.getElementById('inviteAccept').hidden,true);
  assert.equal(h.document.getElementById('inviteVerifyForm').hidden,false);
  assert.match(h.document.getElementById('inviteMessage').textContent,/Não foi possível confirmar a conta atual/);
  assert.equal(h.calls.some(c=>c.path.endsWith('/accept')),false);
});

test('an unavailable code queue keeps the user on the invitation without automatic retries',async t=>{
  const h=await setup(t,{respond:path=>path==='/auth/verify/resend'?{status:503,detail:'Confirmação temporariamente indisponível'}:undefined});
  h.document.getElementById('inviteCode').value='654321';h.click('inviteResend');await flush();
  assert.match(h.document.getElementById('inviteVerifyStatus').textContent,/temporariamente indisponível/);
  assert.equal(h.document.getElementById('inviteCode').value,'654321');
  assert.equal(h.document.getElementById('inviteResend').disabled,false);
  assert.equal(h.calls.filter(c=>c.path==='/auth/verify/resend').length,1);
  assert.equal(h.window.location.hash,'#token='+invitation);
});
