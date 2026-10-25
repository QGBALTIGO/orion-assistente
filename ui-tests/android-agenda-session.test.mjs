import test from 'node:test';
import assert from 'node:assert/strict';
import {deferred,flush} from './harness.mjs';
import {jsonResponse,makePanel} from './panel-harness.mjs';

// These requests run against the complete shipped Orion panel in jsdom.
// No login credentials, API traffic, real tenants or Google tokens are used.
const loginVisible=h=>!h.element('#loginView').classList.contains('hidden');
const agendaVisible=h=>!h.element('#appView').classList.contains('hidden')&&
  h.element('#page-suite').dataset.osCurrentRoute==='agenda';
const unauthorized=jsonResponse({detail:'Credenciais de operador inválidas'},401);

for(const width of [360,390,412]){
  test('Android-sized Agenda remains signed in when Google returns 401 but /auth/me confirms the session ('+width+'px)',async t=>{
    const h=await makePanel({initialRoute:'dashboard',width,respond:path=>{
      if(path.endsWith('/google/status'))return unauthorized;
    }});
    t.after(()=>h.close());
    assert.equal(loginVisible(h),false);
    const before=h.calls.filter(x=>x.path==='/auth/me').length;
    h.click('#osNav [data-os-route=agenda]');
    await flush();
    assert.equal(agendaVisible(h),true,'an optional 401 must not close an otherwise signed-in panel');
    assert.equal(loginVisible(h),false);
    assert.match(h.element('#osAgendaGoogleLabel').textContent,/Não foi possível/);
    assert.equal(h.calls.filter(x=>x.path==='/auth/me').length,before+1);
    assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
    assert.deepEqual(h.errors,[]);
  });
}
test('a truly expired panel session still closes safely after /auth/me independently returns 401',async t=>{
  let checks=0;
  const h=await makePanel({initialRoute:'dashboard',width:390,respond:path=>{
    if(path==='/auth/me'&&++checks>1)return jsonResponse({detail:'Sessão expirada'},401);
    if(path.endsWith('/google/status'))return unauthorized;
  }});
  t.after(()=>h.close());
  assert.equal(loginVisible(h),false);
  h.click('#osNav [data-os-route=agenda]');
  await flush();
  assert.equal(loginVisible(h),true);
  assert.equal(h.element('#appView').classList.contains('hidden'),true);
  assert.equal(checks,2);
});
test('network failure during /auth/me verification never guesses that the operator logged out',async t=>{
  let checks=0;
  const h=await makePanel({initialRoute:'dashboard',width:390,respond:path=>{
    if(path==='/auth/me'&&++checks>1)return jsonResponse({detail:'Serviço indisponível'},503);
    if(path.endsWith('/google/status'))return unauthorized;
  }});
  t.after(()=>h.close());
  h.click('#osNav [data-os-route=agenda]');
  await flush();
  assert.equal(loginVisible(h),false);
  assert.equal(agendaVisible(h),true);
  assert.match(h.element('#osAgendaGoogleLabel').textContent,/Não foi possível/);
  assert.equal(checks,2);
});
test('two simultaneous agenda 401 errors perform one bounded auth recheck',async t=>{
  const pending=deferred();let checks=0;
  const h=await makePanel({initialRoute:'dashboard',width:390,respond:path=>{
    if(path==='/auth/me')return ++checks===1?undefined:pending.promise;
    if(path.endsWith('/google/status')||path.includes('/appointments?'))return unauthorized;
  }});
  t.after(()=>h.close());
  h.click('#osNav [data-os-route=agenda]');
  await flush();
  assert.equal(checks,2,'only one confirmation request is allowed in flight');
  pending.resolve(jsonResponse({authenticated:true,csrf:'synthetic-mobile-csrf',role:'support'},200));
  await flush();
  assert.equal(loginVisible(h),false);
  assert.equal(agendaVisible(h),true);
  assert.equal(checks,2);
});
