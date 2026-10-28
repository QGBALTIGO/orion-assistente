import test from 'node:test';
import assert from 'node:assert/strict';
import {makePanel as createPanel,jsonResponse} from './panel-harness.mjs';
import {flush,tenantA,deferred} from './harness.mjs';

// The legacy operator pages are now exclusive to platform-admin accounts.
const makePanel=(options={})=>createPanel({role:'admin',...options});

const productId='40000000-0000-4000-8000-000000000004',variantId='50000000-0000-4000-8000-000000000005';
const profileId='60000000-0000-4000-8000-000000000006',appointmentId='70000000-0000-4000-8000-000000000007';
const writes=h=>h.calls.filter(call=>call.method!=='GET');
function change(h,selector,value){const input=h.element(selector);if(input.type==='checkbox')input.checked=value;else input.value=value;input.dispatchEvent(new h.window.Event('change',{bubbles:true}));return input;}
function product(){return {id:productId,name:'Produto sintético',revision:3,active:true,description:'Descrição preservada',synonyms:[],official_benefits:[],faqs:[],variants:[{id:variantId,name:'Unidade',sku:'SKU-001',attributes:{volume:'100ml'},revision:4,inventory_revision:2,active:true,price_kind:'fixed',unit_price_cents:123456,stock_mode:'tracked',quantity_on_hand:10,reserved_quantity:2,stock_source:'Contagem sintética',observed_at:'2026-10-28T12:00:00Z',valid_until:'2026-10-28T20:00:00Z'}]};}
function status(){return {enabled:true,assistant_connected:false,access:{role:'owner',can_edit_catalog:true,can_create_orders:true},fulfillment:{enabled:true,enabled_modes:['pickup'],supported_modes:['pickup'],profile_version_id:profileId,profile_revision:1,timezone:'America/Sao_Paulo'}};}
async function commerce(respond){return makePanel({initialRoute:'catalogo',respond(path,method,payload,options){const result=respond?.(path,method,payload,options);if(result!==undefined)return result;if(path.endsWith('/commerce/status'))return status();if(path.endsWith('/commerce/products'))return [product()];if(path.endsWith('/business/profile'))return {generation:1,published:{id:profileId,revision:1,profile:{business_name:'Empresa sintética',timezone:'America/Sao_Paulo',style_rules:{}}},draft:null,access:{role:'owner',can_edit_draft:true,can_publish:true},supported_capabilities:[],runtime_connected:false};}});}
function selectVariant(h){change(h,'#comProduct',productId);change(h,'#comVariant',variantId);}

test('legacy authorized staff uses a national phone mask and sends the existing E.164 contract',async t=>{
  const h=await makePanel({initialRoute:'cadastros',respond:(path,method)=>method==='POST'&&path.endsWith('/authorized_staff')?jsonResponse({detail:'Rejeição sintética'},422):undefined});t.after(()=>h.close());
  const input=h.fill('#record-phone_e164','+55 (55) 99123-4567');h.fill('#record-role','barber');assert.equal(input.value,'(55) 99123-4567');assert.equal(input.type,'tel');assert.equal(input.autocomplete,'tel-national');assert.equal(input.maxLength,-1);
  h.submit('#recordForm');await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data,{phone_e164:'+5555991234567',role:'barber',active:true});assert.equal(input.value,'(55) 99123-4567');assert.deepEqual(h.errors,[]);
});

test('legacy staff invalid or international phones stay visible and do not reach the Brazilian endpoint',async t=>{
  const h=await makePanel({initialRoute:'cadastros'});t.after(()=>h.close());h.fill('#record-role','owner');
  for(const value of ['(55) 99123-45678','+12025550123','telefone']){const input=h.fill('#record-phone_e164',value);h.submit('#recordForm');await flush();assert.equal(writes(h).length,0);assert.equal(input.value,value);assert.equal(input.getAttribute('aria-invalid'),'true');}
});

test('legacy time off uses the company Sao Paulo timezone and sends explicit instants without reverse intervals',async t=>{
  const h=await makePanel({initialRoute:'cadastros',respond:(path,method)=>method==='POST'&&path.endsWith('/time_off')?jsonResponse({detail:'Rejeição sintética'},422):undefined});t.after(()=>h.close());change(h,'#resource','time_off');await flush();
  h.fill('#record-professional_id',profileId);const start=h.fill('#record-starts_at','2026-11-10T09:00');h.fill('#record-ends_at','2026-11-10T08:00');assert.equal(start.type,'datetime-local');h.submit('#recordForm');await flush();assert.equal(writes(h).length,0);
  h.fill('#record-ends_at','2026-11-10T10:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.starts_at,'2026-11-10T12:00:00.000Z');assert.equal(writes(h)[0].data.ends_at,'2026-11-10T13:00:00.000Z');assert.match(h.element('#recordTimezone').textContent,/America\/Sao_Paulo/);assert.match(h.element('label[for=record-starts_at]').textContent,/horário da empresa/);
});

test('legacy time off respects another company timezone, including a fractional UTC offset',async t=>{
  for(const [timezone,expected] of [['America/Manaus','2026-11-10T13:00:00.000Z'],['Asia/Kathmandu','2026-11-10T03:15:00.000Z']]){
    const h=await makePanel({initialRoute:'cadastros',respond(path,method){if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Empresa sintética',role:'owner',timezone}];if(method==='POST'&&path.endsWith('/time_off'))return jsonResponse({detail:'Rejeição sintética'},422);}});t.after(()=>h.close());change(h,'#resource','time_off');await flush();
    h.fill('#record-professional_id',profileId);h.fill('#record-starts_at','2026-11-10T09:00');h.fill('#record-ends_at','2026-11-10T10:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.starts_at,expected);assert.ok(h.element('#recordTimezone').textContent.includes(timezone));assert.deepEqual(h.errors,[]);
  }
});

test('legacy time off rejects missing or invalid company timezones instead of using the device zone',async t=>{
  for(const timezone of [undefined,'','Invalid/Zone']){
    const h=await makePanel({initialRoute:'cadastros',respond(path){if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Empresa sintética',role:'owner',...(timezone===undefined?{}:{timezone})}];}});t.after(()=>h.close());change(h,'#resource','time_off');await flush();
    h.fill('#record-professional_id',profileId);h.fill('#record-starts_at','2026-11-10T09:00');h.fill('#record-ends_at','2026-11-10T10:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,0);assert.match(h.element('#notice').textContent,/fuso horário da empresa/);assert.match(h.element('#recordTimezone').textContent,/fuso horário da empresa/);
  }
});

test('legacy time off rejects daylight-saving gaps and ambiguous repeated times in the company zone',async t=>{
  const h=await makePanel({initialRoute:'cadastros',respond(path,method){if(path==='/api/v1/account/businesses')return [{id:tenantA,name:'Empresa sintética',role:'owner',timezone:'America/New_York'}];if(method==='POST'&&path.endsWith('/time_off'))return jsonResponse({detail:'Rejeição sintética'},422);}});t.after(()=>h.close());change(h,'#resource','time_off');await flush();h.fill('#record-professional_id',profileId);
  h.fill('#record-starts_at','2026-03-08T02:30');h.fill('#record-ends_at','2026-03-08T04:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,0);assert.match(h.element('#notice').textContent,/não existe.*America\/New_York/);
  h.fill('#record-starts_at','2026-11-01T01:30');h.fill('#record-ends_at','2026-11-01T03:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,0);assert.match(h.element('#notice').textContent,/duas vezes.*America\/New_York/);
  h.fill('#record-starts_at','2026-03-08T03:00');h.fill('#record-ends_at','2026-03-08T04:00');h.submit('#recordForm');await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.starts_at,'2026-03-08T07:00:00.000Z');assert.equal(writes(h)[0].data.ends_at,'2026-03-08T08:00:00.000Z');
});

test('business profile contact formats without changing its international contract or other profile facts',async t=>{
  const h=await makePanel({initialRoute:'empresa',respond:(path,method)=>method==='PUT'&&path.endsWith('/business/profile/draft')?jsonResponse({detail:'Rejeição sintética'},422):undefined});t.after(()=>h.close());
  h.fill('#bizContact','5555991234567');h.click('#businessSave');await flush();assert.equal(writes(h)[0].data.profile.contact_phone,'+5555991234567');assert.equal(writes(h)[0].data.profile.timezone,'America/Sao_Paulo');assert.equal(h.element('#bizContact').value,'(55) 99123-4567');
  h.fill('#bizContact','+1 (202) 555-0123');h.click('#businessSave');await flush();assert.equal(writes(h)[1].data.profile.contact_phone,'+12025550123');
  h.fill('#bizContact','erro');h.click('#businessSave');await flush();assert.equal(writes(h).length,2);assert.equal(h.element('#bizContact').getAttribute('aria-invalid'),'true');
  h.fill('#bizContact','');h.click('#businessSave');await flush();assert.equal(writes(h)[2].data.profile.contact_phone,null);
});

test('commercial price edit uses cents, preserves variant metadata and rejects excess decimals',async t=>{
  const h=await commerce((path,method)=>method==='PUT'&&path.includes('/variants/')?jsonResponse({detail:'Rejeição sintética'},422):undefined);t.after(()=>h.close());selectVariant(h);
  const price=h.element('#comPrice');assert.equal(price.value,'1.234,56');assert.equal(price.type,'text');assert.equal(price.maxLength,-1);assert.equal(price.previousElementSibling.textContent,'R$');
  for(const value of ['', '1.000.000,01','1,234','-1']){h.fill('#comPrice',value);h.submit('#comVariantForm');await flush();assert.equal(writes(h).length,0);}
  h.fill('#comPrice','2.345,67');h.submit('#comVariantForm');await flush();assert.equal(writes(h).length,1);const body=writes(h)[0].data;assert.equal(body.expected_revision,4);assert.equal(body.variant.unit_price_cents,234567);assert.deepEqual(body.variant.attributes,{volume:'100ml'});assert.equal(body.variant.sku,'SKU-001');assert.equal(h.element('#comPrice').value,'2.345,67');
});

test('commercial variant modes keep zero and optional price behavior from the backend',async t=>{
  const h=await commerce((path,method)=>method==='PUT'&&path.includes('/variants/')?jsonResponse({detail:'Rejeição sintética'},422):undefined);t.after(()=>h.close());selectVariant(h);
  change(h,'#comPriceKind','from');h.fill('#comPrice','0');h.submit('#comVariantForm');await flush();assert.equal(writes(h)[0].data.variant.unit_price_cents,0);
  h.fill('#comPrice','inválido');h.submit('#comVariantForm');await flush();assert.equal(writes(h).length,1);change(h,'#comPriceKind','on_request');h.submit('#comVariantForm');await flush();assert.equal(writes(h)[1].data.variant.unit_price_cents,null);assert.equal(h.element('#comPrice').disabled,true);assert.equal(h.element('#comPrice').value,'inválido');
});

test('commercial stock remains integer, preserves dates as instants and does not conflate unknown with zero',async t=>{
  const h=await commerce((path,method)=>method==='PUT'&&path.endsWith('/stock')?jsonResponse({detail:'Rejeição sintética'},422):undefined);t.after(()=>h.close());selectVariant(h);
  const input=h.fill('#comStockQuantity','2,5');assert.equal(input.inputMode,'numeric');h.submit('#comStockForm');await flush();assert.equal(writes(h).length,0);assert.equal(input.value,'2,5');
  h.fill('#comStockQuantity','0');h.fill('#comStockObserved','2026-10-28T09:00');h.fill('#comStockUntil','2026-10-28T17:00');h.submit('#comStockForm');await flush();const first=writes(h)[0].data;assert.equal(first.expected_revision,2);assert.equal(first.stock.quantity_on_hand,0);assert.equal(first.stock.observed_at,new h.window.Date('2026-10-28T09:00').toISOString());
  change(h,'#comStockMode','unknown');h.submit('#comStockForm');await flush();assert.deepEqual(writes(h)[1].data.stock,{mode:'unknown',quantity_on_hand:null,observed_at:null,source:null,valid_until:null});
});

test('commercial quote fields send canonical client and integer quantities with the existing validity limits',async t=>{
  const h=await commerce((path,method)=>method==='POST'&&path.endsWith('/quotes')?jsonResponse({detail:'Rejeição sintética'},422):undefined);t.after(()=>h.close());h.click('#comOrdersTab');change(h,'#comBasketVariant',variantId);h.fill('#comBasketQuantity','2');h.click('#comBasketAdd');h.fill('#comContact','55991234567');
  const quantity=h.element('#comBasket input');assert.equal(quantity.type,'text');assert.equal(quantity.inputMode,'numeric');h.fill('#comBasket input','1,5');h.click('#comQuoteCreate');await flush();assert.equal(writes(h).length,0);
  h.fill('#comBasket input','3');h.fill('#comValidity','61');h.click('#comQuoteCreate');await flush();assert.equal(writes(h).length,0);h.fill('#comValidity','15');h.click('#comQuoteCreate');await flush();assert.equal(writes(h).length,1);assert.equal(writes(h)[0].data.contact_e164,'+5555991234567');assert.deepEqual(writes(h)[0].data.items,[{variant_id:variantId,quantity:3}]);assert.equal(writes(h)[0].data.validity_minutes,15);
});

test('commercial pickup read uses canonical phone and drops a response after client identity changes',async t=>{
  const pending=deferred();const h=await commerce(path=>path.includes('/pickup-appointments?')?pending.promise:undefined);t.after(()=>h.close());h.click('#comOrdersTab');change(h,'#comFulfillmentMode','pickup');h.fill('#comContact','55991234567');assert.equal(h.element('#comPickupLoad').disabled,false);h.click('#comPickupLoad');await flush();
  const call=h.calls.find(call=>call.path.includes('/pickup-appointments?'));assert.equal(new URL('https://orion.test'+call.path).searchParams.get('contact_e164'),'+5555991234567');h.fill('#comContact','11987654321');pending.resolve({profile_version_id:profileId,profile_revision:1,items:[{id:appointmentId,protocol:'Cliente anterior'}]});await flush();assert.equal(h.element('#comPickupAppointment').options.length,1);assert.doesNotMatch(h.element('#comPickupFields').textContent,/Cliente anterior/);assert.equal(writes(h).length,0);
});

test('device allowed callers keeps masks intact per item, preserves international recipients and blocks duplicates',async t=>{
  const configuration={chatbot:false,transcription:false,language:'auto',callBlocking:false,callReply:false,callReplyText:'Texto preservado',allowedCallers:['5555991234567','12025550123'],callSchedule:false,callHours:{start:'09:00',end:'18:00',weekdays:[1,2,3,4,5]}};
  const h=await makePanel({deviceStatus:'connected',respond(path,method,payload){if(path==='/api/v1/qr/status')return {status:'connected',featureSettings:configuration,featureCapabilities:{chatbot:false,transcription:false,callBlocking:true}};if(path==='/api/v1/qr/settings'&&method==='POST')return {settings:payload};}});t.after(()=>h.close());h.click('#deviceSettings');
  const input=h.element('#settingsAllowedCallers');assert.equal(input.value,'(55) 99123-4567\n+12025550123');h.fill('#settingsAllowedCallers','(55) 99123-4567; +1 (202) 555-0123, (21) 3456-7890');h.submit('#deviceSettingsForm');await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data.allowedCallers,['5555991234567','12025550123','552134567890']);assert.equal(writes(h)[0].data.callReply,false);assert.equal(writes(h)[0].data.chatbot,false);assert.equal(writes(h)[0].data.callReplyText,'Texto preservado');
  h.click('#deviceSettings');h.fill('#settingsAllowedCallers','(55) 99123-4567\n+55 (55) 99123-4567');h.submit('#deviceSettingsForm');await flush();assert.equal(writes(h).length,1);assert.match(h.element('#settingsAllowedCallersError').textContent,/repetidos/);assert.equal(h.element('#settingsAllowedCallers').getAttribute('aria-invalid'),'true');
  h.fill('#settingsAllowedCallers','');h.submit('#deviceSettingsForm');await flush();assert.deepEqual(writes(h)[1].data.allowedCallers,[]);assert.deepEqual(h.errors,[]);
});

test('legacy caller list does not format readonly, disabled or fieldset-disabled controls on synthetic events',async t=>{
  const h=await makePanel();t.after(()=>h.close());const input=h.element('#settingsAllowedCallers'),fieldset=input.closest('fieldset');assert.ok(fieldset);
  for(const state of ['readonly','disabled','fieldset']){
    input.readOnly=state==='readonly';input.disabled=state==='disabled';fieldset.disabled=state==='fieldset';input.value='+55 (55) 99123-4567';input.setCustomValidity('Estado preservado');input.setAttribute('aria-invalid','true');input.setSelectionRange(input.value.indexOf('-')+1,input.value.indexOf('-')+1);
    for(const type of ['input','change','compositionend','focusout'])input.dispatchEvent(new h.window.Event(type,{bubbles:true}));input.dispatchEvent(new h.window.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentBackward'}));
    assert.equal(input.value,'+55 (55) 99123-4567');assert.equal(input.validationMessage,'');assert.equal(input.getAttribute('aria-invalid'),'true');
  }
  input.readOnly=false;input.disabled=false;fieldset.disabled=false;assert.equal(input.validationMessage,'Estado preservado');assert.equal(writes(h).length,0);
});
