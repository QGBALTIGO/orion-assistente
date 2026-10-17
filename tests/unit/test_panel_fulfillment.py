"""Real panel controller with deferred DOM/HTTP; no browser session or network."""
from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def test_fulfillment_controls_are_optional_accessible_and_preserve_dialogs():
    class Markup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = {}
            self.labels = set()
            self.dialogs = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            self.dialogs += tag == "dialog"
            if "id" in attrs:
                assert attrs["id"] not in self.ids
                self.ids[attrs["id"]] = (tag, attrs)
            if tag == "label" and "for" in attrs:
                self.labels.add(attrs["for"])

    markup = Markup()
    markup.feed((ROOT / "app/static/panel.html").read_text(encoding="utf-8"))
    assert markup.dialogs == 4  # The shared action dialog serves five existing flows.
    for control in ("comFulfillmentMode", "comPickupAppointment"):
        tag, attrs = markup.ids[control]
        assert tag == "select" and "required" not in attrs
        assert control in markup.labels
        assert attrs["aria-describedby"] in markup.ids
    for control in ("bizFulfillmentPickup", "bizFulfillmentDelivery"):
        assert "checked" not in markup.ids[control][1]
        assert "disabled" in markup.ids[control][1]
    assert markup.ids["comPickupLoad"][1]["type"] == "button"
    assert markup.ids["comPickupStatus"][1]["role"] == "status"
    assert "aiExportEvaluation" in markup.ids


DOM_RUNNER = r"""
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {randomUUID}=require('node:crypto');
const panel=fs.readFileSync('app/static/panel.html','utf8');
const sections=[...panel.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.equal(sections.length,2);for(const section of sections)new vm.Script(section[1]);
const fixture=fs.readFileSync('qr-bridge/test/panel-ui.test.mjs','utf8');
const start=fixture.indexOf('function panelHarness(){'),end=fixture.indexOf('\nconst tick=',start);
assert.ok(start>=0&&end>start);
let forbidHtml=false;
const harness=fixture.slice(start,end).replace('setAttribute(){}',
 'setAttribute(name,value){(this.attributes??={})[name]=String(value)} set innerHTML(value){if(htmlForbidden())throw Error("Unsafe HTML sink")}');
const sandbox={assert,vm,panel,sections,randomUUID,Intl,URLSearchParams,console,htmlForbidden:()=>forbidHtml};
vm.runInNewContext(harness+'\nglobalThis.makeHarness=panelHarness;',sandbox);
const h=sandbox.makeHarness(),tick=()=>new Promise(resolve=>setImmediate(resolve));
const clone=x=>JSON.parse(JSON.stringify(x));
const variant='55555555-5555-4555-8555-555555555555',quoteId='66666666-6666-4666-8666-666666666666',orderId='77777777-7777-4777-8777-777777777777';
const appointment='88888888-8888-4888-8888-888888888888',otherAppointment='99999999-9999-4999-8999-999999999999';
const phone='+5567999999999',url=(suffix,tenant='tenant-a')=>'/api/v1/tenants/'+tenant+'/commerce'+suffix;
const fulfillment={enabled:true,enabled_modes:['pickup','delivery'],supported_modes:['pickup','delivery'],profile_version_id:'published-1',profile_revision:1,timezone:'America/Cuiaba',delivery_requires_team_review:true};
const status=(f=fulfillment)=>({enabled:true,access:{role:'owner',can_edit_catalog:true,can_create_orders:true},fulfillment:f});
const products=[{id:'product-a',revision:1,name:'Produto',active:true,variants:[{id:variant,name:'Variante',active:true,price_kind:'fixed',unit_price_cents:1000}]}];
const candidate={id:appointment,protocol:'AG-TESTE',start_at:'2099-10-17T18:00:00Z',timezone:'America/Cuiaba',professional_name:'Profissional',services:['Serviço']};
const candidates=(items=[candidate],overrides={})=>({items,profile_version_id:'published-1',profile_revision:1,...overrides});
const quote=(mode='pickup',overrides={})=>({kind:'quote',id:quoteId,confirmation_token:'synthetic-private-token',contact_e164:phone,customer_name:'Teste',expires_at:new Date(Date.now()+900000).toISOString(),items:[],total_cents:1000,confirmation_requirements_met:true,fulfillment_mode:mode,fulfillment_snapshot:mode?{mode,profile_version_id:'published-1',profile_revision:1,pickup_location:'Endereço revisado'}:null,pickup_appointment_id:null,...overrides});
const click=id=>h.node(id).listeners.click[0]();
function set(id,value,event='change'){h.node(id).value=value;h.node(id).listeners[event]?.[0]()}
async function load(tenant='tenant-a',s=status()){
 h.ui.authenticate();h.node('comTenant').value=tenant;const pending=h.ui.loadCommerceTenant();
 h.take(url('/status',tenant)).respond(s);await tick();h.take(url('/products',tenant)).respond(products);await pending;
}
function basket(){h.node('comBasketVariant').value=variant;h.node('comBasketQuantity').value='1';h.ui.commerceAddBasket();set('comContact',phone,'input')}
async function pickup(items=[candidate],overrides={}){
 const pending=click('comPickupLoad');const request=h.take(url('/pickup-appointments?contact_e164='+encodeURIComponent(phone)));
 request.respond(candidates(items,overrides));await pending;return request;
}
async function calculate(save=false,result=quote(),fresh=status()){
 const pending=h.ui.commerceCalculate(save);
 if(h.node('comFulfillmentMode').value){h.take(url('/status')).respond(fresh);await tick()}
 const request=h.take(url(save?'/quotes':'/estimate'),'POST');request.respond(result);await pending;return request;
}
async function createOrder(){const pending=h.ui.commerceCreateOrder();h.take(url('/status')).respond(status());await tick();h.take(url('/orders'),'POST').respond({id:orderId,quote_id:quoteId,status:'draft',revision:1});await pending}
const writes=()=>h.requests.filter(r=>['POST','PUT','PATCH','DELETE'].includes(r.options.method));
async function business(data){
 h.node('businessTenant').value='tenant-a';const pending=h.ui.loadBusinessProfile();
 h.take('/api/v1/tenants/tenant-a/business/profile').respond(data);h.take('/api/v1/tenants/tenant-a/business/profile/history').respond([]);await pending;
}
const profile={business_name:'Empresa',timezone:'America/Cuiaba',capabilities:['product_orders'],objectives:[],style_rules:{style:'warm'},fulfillment:{enabled_modes:[]}};
const snapshot=(overrides={})=>({supported_capabilities:['product_orders'],supported_fulfillment_modes:['pickup','delivery'],access:{role:'owner',can_edit_draft:true,can_publish:true},published:{id:'published-1',revision:1,profile},draft:null,draft_template:profile,...overrides});

let finished=false;process.on('beforeExit',()=>assert.ok(finished,'Controller scenario left an unresolved request'));
(async()=>{
 const scenario=process.argv[1];if(scenario==='compile')return;
 if(scenario==='profile'){
  await business(snapshot());assert.equal(h.node('bizFulfillmentPickup').checked,false);assert.equal(h.node('bizFulfillmentDelivery').checked,false);
  assert.deepEqual(clone(h.ui.collectBusinessProfile().fulfillment),{enabled_modes:[]});
  h.node('bizFulfillmentPickup').checked=true;h.ui.businessChanged();const edited=clone(h.ui.collectBusinessProfile());
  assert.deepEqual(edited.fulfillment,{enabled_modes:['pickup']});assert.deepEqual(edited.capabilities,['product_orders']);
  assert.equal(writes().length,0,'Choosing a mode must not publish a profile');
  await business(snapshot({supported_fulfillment_modes:undefined,published:{id:'published-1',revision:1,profile:{...profile,fulfillment:undefined}}}));
  assert.equal(h.node('bizFulfillmentPickup').disabled,true);h.node('bizFulfillmentPickup').checked=true;
  assert.equal(Object.hasOwn(h.ui.collectBusinessProfile(),'fulfillment'),false,'Old servers keep their original contract');return;
 }
 await load();forbidHtml=true;
 if(scenario==='disabled-legacy'){
  assert.equal(h.node('comFulfillmentMode').value,'');assert.equal(h.node('comPickupAppointment').value,'');
  assert.deepEqual(Array.from(h.node('comFulfillmentMode').options,o=>o.value),['','pickup','delivery']);
  await load('tenant-a',status({...fulfillment,enabled:false,enabled_modes:[]}));basket();
  assert.equal(h.node('comFulfillmentMode').disabled,true);await click('comPickupLoad');assert.equal(writes().length,0);
  await business(snapshot());assert.equal(h.node('comPlanFields').disabled,false,'A disabled feature must not block legacy catalog after reading the profile');
  const request=await calculate(false,quote(null));assert.deepEqual(Object.keys(JSON.parse(request.options.body)),['items']);
  assert.match(h.node('comQuoteSummary').textContent,/Recebimento não informado/);return;
 }
 basket();set('comFulfillmentMode','pickup');
 if(scenario==='candidate-contract'){
  const hostile={...candidate,protocol:'<img src=x onerror=alert(1)>',professional_name:'<script>evil</script>',services:['<b>Serviço</b>']};
  const before=writes().length;const request=await pickup([hostile]);assert.equal(request.options.method,'GET');
  assert.equal(writes().length,before);assert.equal(h.node('comPickupAppointment').value,'','No candidate auto-selected');
  const option=h.node('comPickupAppointment').options[1];assert.equal(option.value,appointment);assert.match(option.textContent,/<img/);assert.match(option.textContent,/14:00.*America\/Cuiaba/);assert.match(option.textContent,/<script>/);assert.match(option.textContent,/<b>/);
  set('comPickupAppointment',appointment);const calculated=await calculate(false,quote('pickup',{pickup_appointment_id:appointment,fulfillment_snapshot:{pickup_appointment:hostile}}));
  assert.deepEqual(JSON.parse(calculated.options.body).fulfillment,{mode:'pickup',pickup_appointment_id:appointment});assert.equal(JSON.parse(calculated.options.body).contact_e164,phone);
  assert.match(h.node('comQuoteSummary').textContent,/<img/);return;
 }
 if(scenario==='optional-no-appointment'){
  const request=await calculate(false,quote('pickup'));assert.deepEqual(JSON.parse(request.options.body).fulfillment,{mode:'pickup',pickup_appointment_id:null});
  assert.equal(h.requests.some(r=>r.url.includes('/pickup-appointments')),false);assert.match(h.node('comQuoteSummary').textContent,/não exige horário nem profissional/);return;
 }
 if(scenario==='empty-and-network'){
  await pickup([]);assert.match(h.node('comPickupStatus').textContent,/continua possível sem agendamento/);
  const pending=click('comPickupLoad');h.take(url('/pickup-appointments?contact_e164='+encodeURIComponent(phone))).reject(Error('synthetic offline'));await pending;
  assert.equal(h.node('comPickupAppointment').options.length,1);assert.equal(h.node('comPickupLoad').disabled,false);assert.equal(writes().length,0);assert.match(h.node('comPickupStatus').textContent,/Nenhum vínculo/);return;
 }
 if(['late-phone','late-mode','late-tenant','late-logout','late-profile'].includes(scenario)){
  const kind=scenario.slice(5),pending=click('comPickupLoad'),old=h.take(url('/pickup-appointments?contact_e164='+encodeURIComponent(phone)));
  if(kind==='phone')set('comContact','+5567999990000','input');
  if(kind==='mode')set('comFulfillmentMode','delivery');
  if(kind==='tenant')await load('tenant-b');
  if(kind==='logout')h.ui.showLogin();
  if(kind==='profile')await business(snapshot({published:{id:'published-2',revision:2,profile}}));
  old.respond(candidates([{...candidate,protocol:'STALE PRIVATE VISIT'}]));await pending;
  assert.equal(h.node('comPickupAppointment').options.length,1);assert.doesNotMatch(h.node('comPickupStatus').textContent,/STALE PRIVATE/);
  assert.equal(h.node('comOrderCreate').disabled,true);assert.equal(writes().length,0);return;
 }
 if(scenario==='candidate-version-drift'){
  await pickup([candidate],{profile_version_id:'published-2',profile_revision:2});assert.equal(h.node('comPickupAppointment').options.length,1);
  assert.equal(h.node('comQuoteCreate').disabled,true);assert.match(h.node('comQuoteSummary').textContent,/perfil publicado.*mudaram/);return;
 }
 if(scenario==='unknown-or-stale-candidate'){
  await pickup();set('comPickupAppointment',otherAppointment);await h.ui.commerceCalculate(true);assert.equal(writes().length,0);
  set('comPickupAppointment',appointment);h.node('comContact').value='+5567999990000';await h.ui.commerceCalculate(true);assert.equal(writes().length,0);
  assert.match(h.node('comFeedback').textContent,/Consulte novamente/);return;
 }
 if(scenario==='selection-invalidates'){
  await pickup();set('comPickupAppointment',appointment);await calculate(true,quote('pickup',{pickup_appointment_id:appointment}));assert.equal(h.node('comOrderCreate').disabled,false);
  set('comPickupAppointment','');assert.equal(h.node('comOrderCreate').disabled,true);const before=writes().length;await h.ui.commerceCreateOrder();assert.equal(writes().length,before);
  await calculate(true);h.node('comContact').value='+5567999990000';await h.ui.commerceCreateOrder();assert.equal(writes().length,before+1,'A field changed before its input event must invalidate the token too');return;
 }
 if(scenario==='quote-version-drift'){
  const pending=h.ui.commerceCalculate(true);h.take(url('/status')).respond(status({...fulfillment,profile_version_id:'published-2',profile_revision:2}));await pending;
  assert.equal(writes().length,0);assert.equal(h.node('comQuoteCreate').disabled,true);assert.equal(h.node('comFulfillmentMode').value,'');return;
 }
 if(scenario==='quote-preflight-offline'){
  const pending=h.ui.commerceCalculate(true);h.take(url('/status')).reject(Error('synthetic offline'));await pending;
  assert.equal(writes().length,0);assert.equal(h.node('comQuoteCreate').disabled,false);assert.doesNotMatch(h.node('comFeedback').textContent,/resultado incerto/);return;
 }
 if(scenario==='preflight-late-error'){
  const pending=h.ui.commerceCalculate(true),request=h.take(url('/status'));set('comContact','+5567999990000','input');
  request.reject(Error('STALE PRIVATE ERROR'));await pending;assert.equal(writes().length,0);assert.doesNotMatch(h.node('comFeedback').textContent,/STALE PRIVATE ERROR/);return;
 }
 if(scenario==='late-quote'){
  const pending=h.ui.commerceCalculate(true);h.take(url('/status')).respond(status());await tick();const request=h.take(url('/quotes'),'POST');
  set('comContact','+5567999990000','input');request.respond(quote('pickup',{customer_name:'STALE CUSTOMER'}));await pending;
  assert.equal(h.node('comOrderCreate').disabled,true);assert.doesNotMatch(h.node('comQuoteSummary').textContent,/STALE CUSTOMER/);return;
 }
 if(scenario==='confirm-version-drift'){
  await calculate(true);await createOrder();h.node('comAccept').checked=true;
  const pending=h.ui.commerceConfirmOrder();h.take(url('/status')).respond(status({...fulfillment,enabled_modes:['delivery']}));await pending;
  assert.equal(writes().filter(r=>r.url.endsWith('/confirm')).length,0);assert.equal(h.node('comAccept').checked,false);assert.equal(h.node('comOrderConfirm').disabled,true);return;
 }
 if(scenario==='pickup-confirm'){
  await calculate(true);await createOrder();h.node('comAccept').checked=true;
  const pending=h.ui.commerceConfirmOrder();h.take(url('/status')).respond(status());await tick();const request=h.take(url('/orders/'+orderId+'/confirm'),'POST');
  request.respond({id:orderId,quote_id:quoteId,status:'confirmed',revision:2});await pending;
  assert.equal(JSON.parse(request.options.body).confirmation_token,'synthetic-private-token');assert.match(h.node('comOrderSummary').textContent,/confirmado pelo servidor/);assert.equal(h.node('comOrderConfirm').disabled,true);return;
 }
 if(scenario==='delivery'){
  set('comFulfillmentMode','delivery');assert.match(h.node('comFulfillmentStatus').textContent,/análise da equipe.*Frete e prazo/);
  const request=await calculate(true,quote('delivery'));assert.deepEqual(JSON.parse(request.options.body).fulfillment,{mode:'delivery',pickup_appointment_id:null});
  assert.equal(h.node('comOrderCreate').disabled,true);const before=writes().length;await h.ui.commerceCreateOrder();h.node('comAccept').checked=true;await h.ui.commerceConfirmOrder();assert.equal(writes().length,before);
  assert.match(h.node('comQuoteSummary').textContent,/não confirma entrega, não reserva estoque/);return;
 }
 if(scenario==='history-current-separate'||scenario==='history-unavailable'){
  h.node('comLookupOrder').value=orderId;const pending=h.ui.commerceRead('order');const current=scenario==='history-unavailable'?{available:false}:{available:true,status:'cancelled',protocol:'AG-TESTE',start_at:'2099-10-18T18:00:00Z',future:true};
  h.take(url('/orders/'+orderId)).respond({id:orderId,quote_id:quoteId,status:'confirmed',revision:2,quote:quote('pickup',{confirmation_token:undefined,pickup_appointment_id:appointment,fulfillment_snapshot:{mode:'pickup',pickup_location:'LOCAL HISTÓRICO <img>',pickup_appointment:candidate},pickup_appointment_current:current})});await pending;
  for(const box of ['comQuoteSummary','comOrderSummary']){const text=h.node(box).textContent;assert.match(text,/Recebimento revisado.*LOCAL HISTÓRICO <img>/);assert.match(text,/17\/10\/2099/);assert.match(text,/Situação atual do agendamento/);assert.match(text,/Cancelamentos.*independentes/);
   if(current.available){assert.match(text,/cancelado/);assert.match(text,/18\/10\/2099/)}else assert.match(text,/vínculo não está disponível/);
  }
  assert.equal(h.node('comOrderConfirm').disabled,true);assert.equal(writes().length,0);return;
 }
 if(scenario==='history-late-response'){
  h.node('comLookupQuote').value=quoteId;const pending=h.ui.commerceRead('quote'),request=h.take(url('/quotes/'+quoteId));
  set('comContact','+5567999990000','input');request.respond(quote('pickup',{customer_name:'STALE CUSTOMER'}));await pending;
  assert.doesNotMatch(h.node('comQuoteValidity').textContent,/STALE CUSTOMER/);assert.equal(h.node('comOrderCreate').disabled,true);return;
 }
 throw Error('Unknown scenario '+scenario);
})().then(()=>{finished=true},error=>{finished=true;console.error(error);process.exitCode=1});
"""


@pytest.mark.skipif(NODE is None, reason="Node required for real panel DOM/controller; main CI runs these cases")
@pytest.mark.parametrize("scenario", [
    "compile", "profile", "disabled-legacy", "candidate-contract", "optional-no-appointment",
    "empty-and-network", "late-phone", "late-mode", "late-tenant", "late-logout", "late-profile",
    "candidate-version-drift", "unknown-or-stale-candidate", "selection-invalidates",
    "quote-version-drift", "quote-preflight-offline", "preflight-late-error", "late-quote", "confirm-version-drift",
    "pickup-confirm", "delivery", "history-current-separate", "history-unavailable", "history-late-response",
])
def test_panel_fulfillment_controller(scenario):
    result = subprocess.run(
        [NODE, "-e", DOM_RUNNER, scenario], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
