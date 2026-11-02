import test from 'node:test';
import assert from 'node:assert/strict';
import {makeSuite,flush,defaultResponse} from './harness.mjs';

const domain='https://orionagenda.pro',legacy='https://orion-assistente-production.up.railway.app';
const writes=h=>h.calls.filter(call=>call.method!=='GET');

test('the official domain starts Google only on its server-configured callback origin',async t=>{
  for(const origin of [legacy,domain]){
    const h=await makeSuite({url:domain+'/panel',initialRoute:'agenda',respond:path=>path.endsWith('/google/status')?{configured:true,connected:false,authorization_origin:origin,capabilities:{can_connect:true}}:defaultResponse(path)});
    t.after(()=>h.close());await flush();h.click('[data-os-action=agenda-google]');await flush();h.click('[data-os-action=google-connect]');await flush();
    const dialog=h.document.getElementById('osDialog');
    if(origin===legacy){assert.equal(new URL(dialog.querySelector('a').href).origin,legacy);assert.equal(dialog.querySelector('form'),null);}
    else assert.ok(dialog.querySelector('[data-os-form=google-action]'));
    assert.equal(writes(h).length,0);
  }
});

test('an invalid OAuth origin never becomes a clickable redirect or starts authorization',async t=>{
  for(const origin of ['http://foreign.invalid','https://user:password@foreign.invalid','javascript:alert(1)','https://foreign.invalid/path']){
    const h=await makeSuite({url:domain+'/panel',initialRoute:'agenda',respond:path=>path.endsWith('/google/status')?{configured:true,authorization_origin:origin,capabilities:{can_connect:true}}:defaultResponse(path)});
    t.after(()=>h.close());await flush();h.click('[data-os-action=agenda-google]');await flush();h.click('[data-os-action=google-connect]');await flush();
    assert.equal(h.document.querySelector('#osDialog a'),null);assert.equal(h.document.querySelector('[data-os-form=google-action]'),null);assert.equal(writes(h).length,0);
  }
});

const syntheticProfessional='d1000000-0000-4000-8000-000000000007';
async function createProfessionalInvite(t,response){
  const staff={id:syntheticProfessional,name:'Profissional sintético',specialty:'Barbeiro',
    active:true,color:'#31ad52',commission_mode:'percent',commission_percent:0,
    commission_config_version:1,accepts_appointments:true};
  const h=await makeSuite({url:domain+'/panel',initialRoute:'funcionarios',respond:(path,method)=>{
    if(path.endsWith('/professionals')&&method==='GET')return [staff];
    if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
    if(path.endsWith('/management/team'))return {can_manage:true,members:[],invitations:[],professional_access:[]};
    if(path.includes('/management/professionals/')&&method==='PUT')return staff;
    if(path.endsWith('/team/professionals/'+syntheticProfessional+'/invite')&&method==='POST')return response;
    return defaultResponse(path);
  }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-professional="'+syntheticProfessional+'"]');await flush();
  const form=h.document.querySelector('[data-os-form=professional]');
  assert.ok(form);
  form.elements.access_email.value='profissional@example.invalid';
  form.elements.send_invitation.checked=true;
  h.submit('[data-os-form=professional]');await flush();await flush();
  return h;
}

test('professional invitation delivery distinguishes queue and provider acceptance from delivery',async t=>{
  for(const [status,text] of [['queued',/na fila de envio/],['accepted',/provedor aceitou/],['failed',/Não foi possível concluir/],['disabled',/indisponível/],['manual',/Compartilhe o link/],['suppressed',/não foi realizado/],['expired',/expirou/],['uncertain',/não foi confirmado/]]){
    const h=await createProfessionalInvite(t,{link:domain+'/convite#token='+'a'.repeat(43),
      expires_at:'2030-01-01T12:00:00Z',email_delivery:{status,message:'<img src=x onerror=alert(1)>'}});
    const dialog=h.document.getElementById('osDialog');
    assert.match(dialog.querySelector('[role=status]').textContent,text);
    assert.equal(dialog.querySelector('img'),null);
    assert.equal(dialog.querySelector('[aria-label="Link do convite"]').value,domain+'/convite#token='+'a'.repeat(43));
    assert.doesNotMatch(dialog.textContent,/e-mail entregue|convite entregue/i);
    assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path.includes('/team/professionals/')).length,1);
    assert.equal(h.calls.some(c=>c.path.endsWith('/team/invitations')),false);
  }
});

test('professional invitation replay with no link never creates another token or implies delivery',async t=>{
  const h=await createProfessionalInvite(t,{link:null,expires_at:'2030-01-01T12:00:00Z',
    email_delivery:{status:'queued'}});
  assert.match(h.document.getElementById('osDialog').textContent,/na fila de envio/);
  assert.equal(h.document.querySelector('[aria-label="Link do convite"]'),null);
  assert.equal(h.calls.filter(c=>c.method==='POST'&&c.path.includes('/team/professionals/')).length,1);
});
