import test from 'node:test';
import assert from 'node:assert/strict';
import {makeOnboarding, fullState, flush, deferred} from './onboarding-harness.mjs';
import {makeChannels, item} from './channels-harness.mjs';

const writes=h=>h.calls.filter(call=>call.method!=='GET');
const saved=(server,payload)=>({...server.data,version:server.data.version+1,last_operation_key:payload.idempotency_key});
const saoPaulo={cep:'01001-000',logradouro:'Praça da Sé',bairro:'Sé',localidade:'São Paulo',uf:'SP',complemento:'lado ímpar'};
const campoGrande={cep:'79002-000',logradouro:'Rua sintética do centro',bairro:'Centro',localidade:'Campo Grande',uf:'MS'};

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

test('channel sender list formats each number, preserves separators and sends canonical Brazilian recipients',async t=>{
  const h=await makeChannels({respond:(path,method,payload)=>method==='POST'?item({label:payload.label,allowed_senders:payload.allowed_senders}):undefined});t.after(()=>h.close());
  h.click('[data-qc-action=new]');h.fill('[name=label]','Dispositivo sintético');const input=h.fill('[name=allowed_senders]','+55 (55) 99123-4567, 11987654321;\n+55 (21) 3456-7890');
  assert.equal(input.value,'(55) 99123-4567, (11) 98765-4321;\n(21) 3456-7890');assert.equal(input.inputMode,'tel');assert.equal(input.maxLength,-1);
  h.submit();await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data.allowed_senders,['5555991234567','5511987654321','552134567890']);assert.match(h.element('.qc-authorized').textContent,/\(55\) 99123-4567/);assert.equal(writes(h).some(call=>/send|start|assistant/.test(call.path)),false);
});

test('sender list leaves an invalid item visible, reports its position and rejects canonical duplicates',async t=>{
  const h=await makeChannels();t.after(()=>h.close());h.click('[data-qc-action=new]');h.fill('[name=label]','Dispositivo');
  for(const raw of ['(67) 99999-9901; user@lid','(67) 99999-9901; +12025550123','(67) 99999-9901; (55) 99123-45678']){
    const input=h.fill('[name=allowed_senders]',raw);h.submit();await flush();assert.equal(writes(h).length,0);assert.equal(input.value,raw);assert.equal(input.getAttribute('aria-invalid'),'true');assert.match(h.element('#qc-senders-error').textContent,/número 2/);assert.match(input.getAttribute('aria-describedby'),/qc-senders-help qc-senders-error/);
  }
  h.fill('[name=allowed_senders]','(55) 99123-4567\n+55 (55) 99123-4567');h.submit();await flush();assert.match(h.element('#qc-senders-error').textContent,/repetidos/);assert.equal(writes(h).length,0);
  h.fill('[name=allowed_senders]','(55) 99123-4567');assert.equal(h.document.getElementById('qc-senders-error'),null);assert.equal(h.element('[name=allowed_senders]').getAttribute('aria-invalid'),null);
});

test('sender list middle editing and punctuation deletion preserve the other recipients and local caret',async t=>{
  const h=await makeChannels();t.after(()=>h.close());h.click('[data-qc-action=new]');const input=h.fill('[name=allowed_senders]','(67) 99999-9901; (55) 99123-4567');
  const start=input.value.indexOf('(55)')+1;input.setSelectionRange(start,start+2);input.setRangeText('54',start,start+2,'end');input.dispatchEvent(new h.window.Event('input',{bubbles:true}));
  assert.equal(input.value,'(67) 99999-9901; (54) 99123-4567');assert.equal(input.selectionStart,start+2);
  const hyphen=input.value.lastIndexOf('-');input.setSelectionRange(hyphen+1,hyphen+1);const event=new h.window.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType:'deleteContentBackward'});input.dispatchEvent(event);
  assert.equal(event.defaultPrevented,true);assert.ok(input.value.startsWith('(67) 99999-9901; '));assert.equal(input.value.split('; ')[1],'(54) 9912-4567');assert.ok(input.selectionStart>=start&&input.selectionStart<input.value.length);
  input.setSelectionRange(0,input.value.length);input.setRangeText('',0,input.value.length,'start');input.dispatchEvent(new h.window.Event('input',{bubbles:true}));assert.equal(input.value,'');
});

test('channel validation rejection keeps the formatted draft across rerenders without duplicate handlers',async t=>{
  const h=await makeChannels({respond:(path,method)=>{if(method==='POST')throw Object.assign(Error('Rejeição sintética'),{status:422});}});t.after(()=>h.close());
  h.click('[data-qc-action=new]');h.fill('[name=label]','Rascunho do canal');h.fill('[name=allowed_senders]','5555991234567; 2134567890');h.submit();await flush();
  assert.equal(h.element('[name=label]').value,'Rascunho do canal');assert.equal(h.element('[name=allowed_senders]').value,'(55) 99123-4567; (21) 3456-7890');assert.deepEqual(writes(h)[0].data.allowed_senders,['5555991234567','552134567890']);
  h.click('[data-qc-action=cancel-create]');h.click('[data-qc-action=new]');h.submit();await flush();assert.equal(writes(h).length,2);assert.deepEqual(writes(h)[1].data.allowed_senders,writes(h)[0].data.allowed_senders);assert.deepEqual(h.errors,[]);
});

test('channel list composition is preserved until completion and a remount has one submit handler',async t=>{
  const h=await makeChannels({respond:(path,method,payload)=>method==='POST'?item({allowed_senders:payload.allowed_senders}):undefined});t.after(()=>h.close());
  h.controller.unmount();h.window.OrionChannels.mount(h.bridge);await flush();h.click('[data-qc-action=new]');h.fill('[name=label]','Dispositivo');
  const input=h.element('[name=allowed_senders]');input.value='55991234567';input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true,isComposing:true}));assert.equal(input.value,'55991234567');input.dispatchEvent(new h.window.CompositionEvent('compositionend',{bubbles:true}));assert.equal(input.value,'(55) 99123-4567');
  h.submit();await flush();assert.equal(writes(h).length,1);assert.deepEqual(writes(h)[0].data.allowed_senders,['5555991234567']);
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
