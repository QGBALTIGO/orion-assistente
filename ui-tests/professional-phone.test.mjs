import test from 'node:test';
import assert from 'node:assert/strict';
import { makeSuite, flush, defaultResponse } from './harness.mjs';

const firstId='c1000000-0000-4000-8000-000000000001';
const secondId='c1000000-0000-4000-8000-000000000002';
const createdId='c1000000-0000-4000-8000-000000000003';
const formSelector='[data-os-form=professional]';
const phoneSelector=formSelector+' [name=phone_e164]';

async function fixture(t,{role='owner',phone='+5567912345678'}={}){
  const staff=[
    {id:firstId,name:'Profissional A',specialty:'Barbeiro',phone_e164:phone,
      active:true,color:'#31ad52',image_url:null,commission_mode:'percent',
      commission_percent:10,commission_config_version:1,accepts_appointments:true},
    {id:secondId,name:'Profissional B',specialty:'Barbeiro',phone_e164:null,
      active:true,color:'#31ad52',image_url:null,commission_mode:'percent',
      commission_percent:15,commission_config_version:1,accepts_appointments:true},
  ];
  const saves=[];
  const h=await makeSuite({initialRoute:'funcionarios',role,respond:(path,method,payload)=>{
    if(path.endsWith('/professionals')&&method==='GET')return staff;
    if(path.endsWith('/services')||path.endsWith('/professional_services'))return [];
    if(path.endsWith('/management/professionals/commission-rules'))return {items:[]};
    const match=path.match(/\/management\/professionals\/(?:([^/]+)\/)?configure$/);
    if(match&&['POST','PUT'].includes(method)){
      const id=match[1]||createdId;
      saves.push({path,method,id,payload});
      let row=staff.find(item=>item.id===id);
      if(!row){row={id,commission_config_version:0};staff.push(row);}
      Object.assign(row,payload,{commission_config_version:row.commission_config_version+1});
      return {...row};
    }
    return defaultResponse(path);
  }});
  t.after(()=>{h.close();assert.deepEqual(h.errors,[]);});
  await flush();
  async function open(id=firstId){
    h.click('[data-os-professional="'+id+'"]');await flush();
    const form=h.document.querySelector(formSelector);
    assert.ok(form);
    return {form,input:form.elements.phone_e164};
  }
  return {h,staff,saves,open};
}

function edit(h,input,inputType,data=null){
  const before=new h.window.InputEvent('beforeinput',{bubbles:true,cancelable:true,inputType,data});
  if(!input.dispatchEvent(before))return;
  let start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
  if(inputType==='deleteContentBackward'&&start===end)start=Math.max(0,start-1);
  if(inputType==='deleteContentForward'&&start===end)end=Math.min(input.value.length,end+1);
  input.setRangeText(data??'',start,end,'end');
  input.dispatchEvent(new h.window.InputEvent('input',{bubbles:true,inputType,data}));
}

test('telefone fica após especialidade, com teclado de telefone, exemplo e ajuda acessível',async t=>{
  const {open}=await fixture(t);
  const {form,input}=await open();
  assert.equal(form.elements.specialty.closest('.os-field').nextElementSibling,input.closest('.os-field'));
  assert.equal(input.type,'tel');
  assert.equal(input.inputMode,'tel');
  assert.equal(input.autocomplete,'tel-national');
  assert.equal(input.placeholder,'(00) 00000-0000');
  assert.equal(input.required,false,'old professionals can be edited before their number is known');
  assert.match(input.labels[0].textContent,/Telefone\/WhatsApp/);
  const help=form.querySelector('#'+input.getAttribute('aria-describedby'));
  assert.match(help.textContent,/deste profissional, com DDD/);
  assert.equal(input.value,'(67) 91234-5678');
});

test('digitação incremental organiza DDD e número sem levar o cursor para o lugar errado',async t=>{
  const {h,open}=await fixture(t,{phone:null});
  const {input}=await open();
  input.focus();input.setSelectionRange(0,0);
  for(const char of '67912345678'){
    edit(h,input,'insertText',char);
    assert.equal(input.selectionStart,input.value.length);
  }
  assert.equal(input.value,'(67) 91234-5678');
  assert.equal(input.value.replace(/\D/g,''),'67912345678');
});

for(const [pasted,formatted,canonical]of[
  ['67912345678','(67) 91234-5678','+5567912345678'],
  ['+55 (67) 91234-5678','(67) 91234-5678','+5567912345678'],
  ['5567912345678','(67) 91234-5678','+5567912345678'],
  ['556732345678','(67) 3234-5678','+556732345678'],
  ['5555991234567','(55) 99123-4567','+5555991234567'],
  ['55991234567','(55) 99123-4567','+5555991234567'],
  ['(67) 3234-5678','(67) 3234-5678','+556732345678'],
]){
  test('colar '+pasted+' mantém o número correto ao salvar',async t=>{
    const {h,open,saves}=await fixture(t,{phone:null});
    const {input}=await open();
    edit(h,input,'insertFromPaste',pasted);
    assert.equal(input.value,formatted);
    assert.equal(input.selectionStart,formatted.length);
    h.submit(formSelector);await flush();
    assert.equal(saves.length,1);
    assert.equal(saves[0].payload.phone_e164,canonical);
    assert.equal(saves[0].payload.commission_percent,10);
  });
}

test('um dígito excedente no DDD 55 mascarado não muda o destinatário e permite correção',async t=>{
  const {h,open,saves}=await fixture(t,{phone:'+5555991234567'});
  const {input,form}=await open();
  assert.equal(input.value,'(55) 99123-4567');
  input.setSelectionRange(input.value.length,input.value.length);
  edit(h,input,'insertText','8');
  assert.equal(input.value,'(55) 99123-45678');
  assert.equal(input.selectionStart,input.value.length);
  h.submit(formSelector);await flush();
  assert.equal(saves.length,0);
  assert.equal(input.getAttribute('aria-invalid'),'true');
  assert.match(form.querySelector('.os-form-message').textContent,/telefone do profissional com DDD/);
  input.setSelectionRange(input.value.length,input.value.length);
  edit(h,input,'deleteContentBackward');
  assert.equal(input.value,'(55) 99123-4567');
  assert.equal(input.hasAttribute('aria-invalid'),false);
  h.submit(formSelector);await flush();
  assert.equal(saves.length,1);
  assert.equal(saves[0].payload.phone_e164,'+5555991234567');
});

for(const invalid of ['(55) 99123-45678','(55) 99123-456789']){
  test('colar telefone nacional excedente '+invalid+' preserva todos os dígitos e bloqueia o envio',async t=>{
    const {h,open,saves}=await fixture(t,{phone:null});
    const {input,form}=await open();
    edit(h,input,'insertFromPaste',invalid);
    assert.equal(input.value,invalid);
    h.submit(formSelector);await flush();
    assert.equal(saves.length,0);
    assert.equal(input.getAttribute('aria-invalid'),'true');
    assert.match(form.querySelector('.os-form-message').textContent,/telefone do profissional com DDD/);
  });
}

test('backspace atravessa o hífen e os parênteses sem prender o cursor na máscara',async t=>{
  const {h,open}=await fixture(t);
  const {input}=await open();
  input.setSelectionRange(11,11);
  edit(h,input,'deleteContentBackward');
  assert.equal(input.value,'(67) 9123-5678');
  assert.equal(input.selectionStart,9);
  input.setSelectionRange(5,5);
  edit(h,input,'deleteContentBackward');
  assert.equal(input.value.replace(/\D/g,''),'691235678');
  assert.equal(input.selectionStart,2);
  input.setSelectionRange(input.value.length,input.value.length);
  while(input.value)edit(h,input,'deleteContentBackward');
  assert.equal(input.value,'');
  assert.equal(input.selectionStart,0);
});

test('editar uma seleção e apagar à frente preserva os outros dígitos e a posição lógica',async t=>{
  const {h,open}=await fixture(t);
  const {input}=await open();
  input.setSelectionRange(1,3);
  edit(h,input,'insertText','11');
  assert.equal(input.value,'(11) 91234-5678');
  assert.equal(input.selectionStart,3);
  input.setSelectionRange(10,10);
  edit(h,input,'deleteContentForward');
  assert.equal(input.value,'(11) 9123-4678');
  assert.equal(input.selectionStart,11);
});

test('números inválidos não são truncados nem enviados como outro destinatário',async t=>{
  const {h,open,saves}=await fixture(t,{phone:null});
  const {input,form}=await open();
  for(const invalid of ['+1 (67) 91234-5678','+556791234567899','679123456789999','67 telefone 912345678','679123']){
    h.fill(phoneSelector,invalid);
    const digits=invalid.replace(/\D/g,'');
    assert.equal(input.value.replace(/\D/g,''),digits,'the mask must retain every digit');
    h.submit(formSelector);await flush();
    assert.equal(saves.length,0);
    assert.equal(input.getAttribute('aria-invalid'),'true');
    assert.match(form.querySelector('.os-form-message').textContent,/telefone do profissional com DDD/);
    assert.equal(h.document.activeElement,input);
  }
  h.fill(phoneSelector,'67912345678');
  assert.equal(input.hasAttribute('aria-invalid'),false);
  h.submit(formSelector);await flush();
  assert.equal(saves.length,1);
  assert.equal(saves[0].payload.phone_e164,'+5567912345678');
});

test('cada profissional mantém o próprio WhatsApp ao salvar e reabrir o cadastro',async t=>{
  const {h,open,saves,staff}=await fixture(t,{phone:null});
  await open(firstId);
  h.fill(phoneSelector,'67912345678');
  h.submit(formSelector);await flush();
  await open(secondId);
  h.fill(phoneSelector,'11987654321');
  h.submit(formSelector);await flush();
  assert.deepEqual(saves.map(save=>[save.id,save.payload.phone_e164]),[
    [firstId,'+5567912345678'],[secondId,'+5511987654321'],
  ]);
  assert.deepEqual(staff.map(row=>row.phone_e164),['+5567912345678','+5511987654321']);
  const first=await open(firstId);
  assert.equal(first.input.value,'(67) 91234-5678');
  h.click('[data-os-action=close-dialog]');
  const second=await open(secondId);
  assert.equal(second.input.value,'(11) 98765-4321');
  assert.equal(h.calls.filter(call=>call.method!=='GET').every(call=>call.path.endsWith('/configure')),true,
    'saving contact does not send a message or change access permissions');
});

test('novo profissional salva telefone normalizado e chave de operação',async t=>{
  const {h,saves,open}=await fixture(t);
  h.click('[data-os-action=new-professional]');await flush();
  h.fill(formSelector+' [name=name]','Profissional C');
  h.fill(phoneSelector,'+55 11 95555-1234');
  h.submit(formSelector);await flush();
  assert.equal(saves.length,1);
  assert.equal(saves[0].method,'POST');
  assert.equal(saves[0].payload.phone_e164,'+5511955551234');
  assert.ok(saves[0].payload.idempotency_key);
  assert.equal((await open(createdId)).input.value,'(11) 95555-1234');
});

test('apagar o telefone limpa o contato e cadastros antigos continuam editáveis',async t=>{
  const {h,open,saves}=await fixture(t);
  const {input}=await open();
  input.setSelectionRange(0,input.value.length);
  edit(h,input,'deleteContentBackward');
  h.submit(formSelector);await flush();
  assert.equal(saves[0].payload.phone_e164,null);
  const reopened=await open();
  assert.equal(reopened.input.value,'');
  h.fill(formSelector+' [name=specialty]','Barbeiro e cabeleireiro');
  h.submit(formSelector);await flush();
  assert.equal(saves[1].payload.phone_e164,null);
  assert.equal(saves[1].payload.specialty,'Barbeiro e cabeleireiro');
});

test('acesso de leitura mostra o contato, mas não permite alterar o profissional',async t=>{
  const {h,open,saves}=await fixture(t,{role:'viewer'});
  const {input,form}=await open();
  assert.equal(input.value,'(67) 91234-5678');
  assert.equal(input.matches(':disabled'),true);
  assert.equal(form.querySelector('button[type=submit]').disabled,true);
  h.submit(formSelector);await flush();
  assert.equal(saves.length,0);
});
