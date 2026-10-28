import test from 'node:test';
import assert from 'node:assert/strict';
import {makeOnboarding, fullState, flush, deferred} from './onboarding-harness.mjs';
import {makeChannels, item, listing, runtime} from './channels-harness.mjs';

const writes=h=>h.calls.filter(call=>call.method!=='GET');
const saved=(server,payload)=>({...server.data,version:server.data.version+1,last_operation_key:payload.idempotency_key});
const saoPaulo={cep:'01001-000',logradouro:'Praça da Sé',bairro:'Sé',localidade:'São Paulo',uf:'SP',complemento:'lado ímpar'};
const campoGrande={cep:'79002-000',logradouro:'Rua sintética do centro',bairro:'Centro',localidade:'Campo Grande',uf:'MS'};

async function makeCodeConnection(respond){
  const row=item({enabled:true});
  const h=await makeChannels({data:listing([row]),respond:async(path,method,payload,server)=>{
    const response=await respond?.(path,method,payload,server);if(response!==undefined)return response;
    if(path.endsWith('/status'))return {...runtime(row,'qr_ready'),capabilities:{pair:true}};
    if(path.endsWith('/pair')&&method==='POST')return {...runtime(row,'pairing'),code:'TEST-0001',expires_in_ms:79000};
  }});
  h.click('[data-qc-action=tutorial-next]');
  return h;
}

test('onboarding phone formats DDD 55, keeps leading country code unambiguous and sends canonical identity',async t=>{
  for(const raw of ['55991234567','+55 (55) 99123-4567','5555991234567']){
    const h=await makeOnboarding({respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
    const input=h.fill('[name=contact_phone]',raw);assert.equal(input.value,'(55) 99123-4567');assert.equal(input.type,'tel');assert.equal(input.maxLength,-1);
    h.submit();await flush();assert.equal(writes(h)[0].data.data.contact_phone,'+5555991234567');assert.deepEqual(h.errors,[]);
  }
});

test('WhatsApp is required and invalid or foreign numbers do not save a business',async t=>{
  for(const raw of ['', '+1 (202) 555-0123']){
    const data=fullState();data.configuration.contact_phone='';
    const h=await makeOnboarding({data,respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
    const phone=h.fill('[name=contact_phone]',raw);
    assert.equal(phone.required,true);
    h.fill('[name=name]','Barbearia Áurea');
    h.submit();await flush();assert.equal(writes(h).length,0);
  }
});

test('business name formats repeated whitespace on blur and submission without rewriting a brand',async t=>{
  const h=await makeOnboarding({respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
  const input=h.fill('[name=name]',"   Studio    D'Ávila-McX   ");
  input.dispatchEvent(new h.window.FocusEvent('focusout',{bubbles:true}));
  assert.equal(input.value,"Studio D'Ávila-McX");
  assert.equal(input.getAttribute('autocapitalize'),'words');
  h.submit();await flush();
  assert.equal(writes(h)[0].data.data.name,"Studio D'Ávila-McX");
  assert.equal(writes(h)[0].data.data.timezone,'America/Cuiaba');
  assert.deepEqual(h.errors,[]);
});

test('onboarding invalid phone stays visible, blocks writes and keeps a correction draft',async t=>{
  const h=await makeOnboarding();t.after(()=>h.close());
  h.fill('[name=name]','Rascunho preservado');const input=h.fill('[name=contact_phone]','(55) 99123-45678');h.submit();await flush();
  assert.equal(writes(h).length,0);assert.equal(input.value,'(55) 99123-45678');assert.equal(input.getAttribute('aria-invalid'),'true');
  assert.match(h.document.getElementById(input.getAttribute('aria-describedby').split(' ').at(-1)).textContent,/10 ou 11/);
  h.click('[data-ob-step=location]');h.click('[data-ob-step=business]');assert.equal(h.element('[name=name]').value,'Rascunho preservado');assert.equal(h.element('[name=contact_phone]').value,'(55) 99123-45678');
});

test('onboarding prices send integer cents and durations reject fractions without erasing the text',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'catalog'}),respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
  h.click('[data-ob-action=edit][data-ob-kind=service]');const input=h.fill('[name=price]','1.234,56');assert.equal(input.value,'1.234,56');assert.equal(input.type,'text');assert.equal(input.inputMode,'decimal');assert.equal(input.previousElementSibling.textContent,'R$');
  const duration=h.fill('[name=duration_min]','40,5');assert.equal(duration.inputMode,'numeric');h.submit();await flush();assert.equal(writes(h).length,0);assert.equal(duration.value,'40,5');assert.equal(duration.getAttribute('aria-invalid'),'true');
  h.fill('[name=duration_min]','40');h.submit();await flush();const data=writes(h)[0].data.data.item;assert.equal(data.price_cents,123456);assert.equal(data.duration_min,40);assert.equal(data.prep_min,5);assert.equal(data.cleanup_min,5);
});

test('onboarding product price modes preserve blank, zero, minimum and upper limits',async t=>{
  const data=fullState({current_step:'catalog'});data.configuration.activity='products';
  const h=await makeOnboarding({data,respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
  h.click('[data-ob-action=add-product]');h.fill('[name=name]','Produto sintético');h.fill('[name=variant_name]','Unidade');h.change('[name=price_kind]','from');
  for(const value of ['', '-1', '1.000.000,01','1,234']){h.fill('[name=price]',value);h.submit();await flush();assert.equal(writes(h).length,0);}
  h.fill('[name=price]','0');h.submit();await flush();assert.equal(writes(h)[0].data.data.variant.unit_price_cents,0);
  h.click('[data-ob-action=add-product]');h.fill('[name=name]','Sob consulta');h.fill('[name=variant_name]','Unidade');h.change('[name=price_kind]','fixed');h.fill('[name=price]','texto inválido');h.submit();await flush();assert.equal(writes(h).length,1);
  h.change('[name=price_kind]','on_request');h.submit();await flush();assert.equal(writes(h)[1].data.data.variant.unit_price_cents,null);
});

test('onboarding CEP lookup fills address, keeps leading zero and never overwrites number or complement',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:()=>saoPaulo,respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
  h.fill('[name=number]','123A');h.fill('[name=complement]','Sala 02');const cep=h.fill('[name=postal_code]','01001000');assert.equal(cep.value,'01001-000');assert.equal(cep.maxLength,-1);
  await h.tick(300);await flush();assert.equal(h.postalCalls.length,1);assert.equal(h.postalCalls[0].url,'https://viacep.com.br/ws/01001000/json/');assert.equal(h.postalCalls[0].options.credentials,'omit');assert.equal(h.postalCalls[0].options.referrerPolicy,'no-referrer');
  assert.equal(h.element('[name=street]').value,'Praça da Sé');assert.equal(h.element('[name=state]').value,'SP');assert.equal(h.element('[name=state]').tagName,'SELECT');assert.equal(h.element('[name=number]').value,'123A');assert.equal(h.element('[name=complement]').value,'Sala 02');
  h.submit();await flush();assert.equal(writes(h)[0].data.data.address.postal_code,'01001000');assert.equal(writes(h)[0].data.data.address.number,'123A');assert.equal(writes(h)[0].data.data.address.complement,'Sala 02');
});

test('an older CEP response cannot replace a newer address or edits made while lookup is pending',async t=>{
  const first=deferred(),second=deferred();
  const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:url=>url.includes('01001000')?first.promise:second.promise});t.after(()=>h.close());
  h.fill('[name=postal_code]','01001000');await h.tick(300);h.fill('[name=postal_code]','79002000');await h.tick(300);
  assert.equal(h.postalCalls[0].options.signal.aborted,true);h.fill('[name=street]','Rua escolhida manualmente');h.change('[name=state]','MT');h.fill('[name=number]','10B');assert.equal(h.document.querySelector('[name=no_number]'),null);h.fill('[name=complement]','Fundos');
  second.resolve(campoGrande);await flush();assert.equal(h.element('[name=street]').value,'Rua escolhida manualmente');assert.equal(h.element('[name=state]').value,'MT');assert.equal(h.element('[name=city]').value,'Campo Grande');
  first.resolve(saoPaulo);await flush();assert.equal(h.element('[name=postal_code]').value,'79002-000');assert.equal(h.element('[name=city]').value,'Campo Grande');assert.equal(h.element('[name=number]').value,'10B');assert.equal(h.document.querySelector('[name=no_number]'),null);assert.equal(h.element('[name=complement]').value,'Fundos');assert.equal(writes(h).length,0);
});

test('CEP lookup failure, unknown codes and generic addresses keep manual editing available',async t=>{
  for(const response of [()=>{throw Error('offline');},()=>({erro:true}),()=>({...saoPaulo,logradouro:'',bairro:''}),()=>({...saoPaulo,uf:'ZZ'})]){
    const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:response});t.after(()=>h.close());h.fill('[name=postal_code]','01001000');await h.tick(300);await flush();
    for(const name of ['postal_code','street','number','complement','neighborhood','city','state'])assert.equal(h.element('[name='+name+']').disabled,false);
    assert.equal(h.element('[name=number]').value,'1');const note=h.element('#ob-cep-status');
    if(note.dataset.status==='found'){
      assert.equal(note.hidden,true);
      assert.equal(note.textContent,'');
    }else{
      assert.equal(note.hidden,false);
      assert.ok(note.textContent.length>0);
    }assert.equal(writes(h).length,0);
  }
});

test('changing setup step or modality and unmounting abort CEP reads and ignore delayed responses',async t=>{
  for(const action of ['step','online','unmount']){
    const pending=deferred();const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:()=>pending.promise});t.after(()=>h.close());h.fill('[name=postal_code]','01001000');await h.tick(300);
    if(action==='step')h.click('[data-ob-step=business]');else if(action==='online')h.change('[name=modality]','online');else h.controller.unmount();
    assert.equal(h.postalCalls[0].options.signal.aborted,true);assert.equal(h.timers.size,0);pending.resolve(saoPaulo);await flush();
    if(action==='step'){h.click('[data-ob-step=location]');assert.equal(h.element('[name=street]').value,'Rua sintética');}else if(action==='online')assert.equal(h.element('[name=street]').value,'Rua sintética');else assert.equal(h.element('#setup').childElementCount,0);
  }
});

test('incomplete or excess CEP stays visible and issues no lookup, while optional empty remains valid',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:()=>saoPaulo,respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});t.after(()=>h.close());
  for(const raw of ['01001','010010000','01001-00x']){const input=h.fill('[name=postal_code]',raw);h.submit();await h.tick(300);await flush();assert.equal(writes(h).length,0);assert.equal(h.postalCalls.length,0);assert.equal(input.getAttribute('aria-invalid'),'true');if(raw.length>8)assert.equal(input.value,raw);}
  h.fill('[name=postal_code]','');h.submit();await flush();assert.equal(writes(h)[0].data.data.address.postal_code,null);
});

test('a CEP timeout offers manual entry and ignores a response that arrives after cancellation',async t=>{
  const late=deferred();const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:()=>late.promise});t.after(()=>h.close());
  h.fill('[name=postal_code]','01001000');await h.tick(300);await h.tick(8000);assert.equal(h.postalCalls[0].options.signal.aborted,true);assert.equal(h.element('#ob-cep-status').dataset.status,'unknown');
  late.resolve(saoPaulo);await flush();assert.equal(h.element('[name=street]').value,'Rua sintética');assert.equal(h.element('[data-ob-action=cep-retry]').hidden,false);assert.equal(writes(h).length,0);
});

test('single connection phone formats DDD 55 and submits only the canonical pairing identity',async t=>{
  for(const raw of ['55991234567','+55 (55) 99123-4567','5555991234567']){
    const h=await makeCodeConnection();t.after(()=>h.close());
    const input=h.fill('[data-qc-pair-phone]',raw);
    assert.equal(input.value,'(55) 99123-4567');assert.equal(input.type,'tel');assert.equal(input.inputMode,'tel');assert.equal(input.maxLength,-1);
    assert.equal(h.document.querySelector('[name=label],[name=allowed_senders],[data-qc-action=new]'),null);assert.deepEqual(writes(h),[]);
    h.submit();await flush();assert.equal(writes(h).length,1);assert.match(writes(h)[0].path,/\/pair$/);assert.deepEqual(writes(h)[0].data,{expected_revision:1,phone:'5555991234567'});
    assert.equal(writes(h).some(call=>/send|start|assistant|provision/.test(call.path)),false);assert.deepEqual(h.errors,[]);
  }
});

test('single connection phone keeps invalid input visible with an associated error and rejects recipient lists',async t=>{
  const h=await makeCodeConnection();t.after(()=>h.close());
  for(const raw of ['user@lid','(55) 99123-45678','(67) 99999-9901; (55) 99123-4567']){
    const input=h.fill('[data-qc-pair-phone]',raw);h.submit();await flush();assert.equal(writes(h).length,0);assert.equal(input.value,raw);assert.equal(input.getAttribute('aria-invalid'),'true');
    const described=input.getAttribute('aria-describedby').split(' ');assert.ok(described.includes('qc-pair-help'));const error=h.document.getElementById(described.at(-1));assert.ok(error);assert.ok(error.textContent.length>0);
  }
  const corrected=h.fill('[data-qc-pair-phone]','(55) 99123-4567');assert.equal(corrected.getAttribute('aria-invalid'),null);assert.equal(corrected.getAttribute('aria-describedby'),'qc-pair-help');assert.deepEqual(writes(h),[]);
});

test('single connection phone middle editing and punctuation deletion preserve the local caret',async t=>{
  const h=await makeCodeConnection();t.after(()=>h.close());const input=h.fill('[data-qc-pair-phone]','(55) 99123-4567');
  input.setSelectionRange(1,3);input.setRangeText('54',1,3,'end');input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  assert.equal(input.value,'(54) 99123-4567');assert.equal(input.selectionStart,3);
  const hyphen=input.value.indexOf('-');input.setSelectionRange(hyphen+1,hyphen+1);const event=new h.window.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentBackward'});input.dispatchEvent(event);
  assert.equal(event.defaultPrevented,true);assert.equal(input.value,'(54) 9912-4567');assert.ok(input.selectionStart>3&&input.selectionStart<input.value.length);
  input.setSelectionRange(0,input.value.length);input.setRangeText('',0,input.value.length,'start');input.dispatchEvent(new h.window.Event('input',{bubbles:true}));assert.equal(input.value,'');assert.deepEqual(writes(h),[]);
});

test('pairing validation rejection keeps the formatted phone across rerenders without duplicate handlers',async t=>{
  const h=await makeCodeConnection((path,method)=>{if(path.endsWith('/pair')&&method==='POST')throw Object.assign(Error('Rejeição sintética'),{status:422});});t.after(()=>h.close());
  h.fill('[data-qc-pair-phone]','5555991234567');h.submit();await flush();
  assert.equal(h.element('[data-qc-pair-phone]').value,'(55) 99123-4567');assert.deepEqual(writes(h)[0].data,{expected_revision:1,phone:'5555991234567'});assert.equal(h.controller.getState().uncertain,false);
  h.click('[data-qc-action=tutorial-back]');h.click('[data-qc-action=tutorial-next]');assert.equal(h.element('[data-qc-pair-phone]').value,'(55) 99123-4567');h.submit();await flush();
  assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[1].data,writes(h)[0].data);assert.deepEqual(h.errors,[]);
});

test('pairing phone composition finishes before formatting and remount retains one submit handler',async t=>{
  const h=await makeCodeConnection();t.after(()=>h.close());
  h.controller.unmount();h.window.OrionChannels.mount(h.bridge);await flush();h.click('[data-qc-action=tutorial-next]');
  const input=h.element('[data-qc-pair-phone]');input.value='55991234567';input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true,isComposing:true}));assert.equal(input.value,'55991234567');input.dispatchEvent(new h.window.CompositionEvent('compositionend',{bubbles:true}));assert.equal(input.value,'(55) 99123-4567');
  h.submit();await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data,{expected_revision:1,phone:'5555991234567'});assert.deepEqual(h.errors,[]);
});


test('address has no redundant notices and masks text, CEP and establishment number',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'location'}),postalRespond:()=>saoPaulo,
    respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});
  t.after(()=>h.close());
  const form=h.element('[data-ob-form=location]');
  for(const notice of [
    'Endereço operacional privado. Preencher esta etapa não publica seu endereço.',
    'Digite o CEP para preencher o endereço. Você também pode preencher manualmente.',
    'Confira os dados e informe o número. O endereço só muda quando você salva.',
    'Dados salvos.'
  ])assert.equal(h.element('#setup').textContent.includes(notice),false);
  assert.equal(h.document.querySelector('[name=no_number]'),null);
  assert.equal(form.elements.number.required,true);
  assert.equal(form.elements.number.getAttribute('pattern'),'.*[0-9].*');
  assert.equal(form.elements.state.querySelector('option[value=""]').disabled,true);
  assert.equal(form.elements.modality.querySelector('option[value=""]').disabled,true);
  assert.equal(h.element('#ob-cep-status').hidden,true);
  h.fill('[name=street]','   Rua    São   José  ');
  h.element('[name=street]').dispatchEvent(new h.window.FocusEvent('focusout',{bubbles:true}));
  assert.equal(h.element('[name=street]').value,'Rua São José');
  h.fill('[name=number]',' 78b ');
  h.element('[name=number]').dispatchEvent(new h.window.FocusEvent('focusout',{bubbles:true}));
  assert.equal(h.element('[name=number]').value,'78B');
  h.fill('[name=postal_code]','01001000');
  await h.tick(300);await flush();
  assert.equal(h.element('[name=postal_code]').value,'01001-000');
  assert.equal(h.element('#ob-cep-status').hidden,true);
  assert.equal(form.elements.city.value,'São Paulo');
  h.submit();await flush();
  assert.equal(writes(h).length,1);
  assert.equal(writes(h)[0].data.data.address.number,'78B');
  assert.deepEqual(h.errors,[]);
});

test('location rejects blank or S/N number, then saves real building number',async t=>{
  const h=await makeOnboarding({data:fullState({current_step:'location'}),
    respond:(path,method,payload,server)=>method==='PUT'?saved(server,payload):undefined});
  t.after(()=>h.close());
  h.fill('[name=number]','S/N');
  h.submit();await flush();assert.equal(writes(h).length,0);
  h.fill('[name=number]','');
  h.submit();await flush();assert.equal(writes(h).length,0);
  h.fill('[name=number]','500-A');
  h.submit();await flush();assert.equal(writes(h).length,1);
  assert.equal(writes(h)[0].data.data.address.no_number,false);
  assert.equal(writes(h)[0].data.data.address.number,'500-A');
  assert.deepEqual(h.errors,[]);
});
