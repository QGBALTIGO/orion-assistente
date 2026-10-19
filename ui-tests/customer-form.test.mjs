import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse,customer,customerId} from './harness.mjs';

const activeRecord={
  ...customer('Cliente de teste',customerId),
  version:5,active:true,phone:'+5511912345678',instagram:'@cliente_teste',
  email:'cliente@example.invalid',birth_date:'1995-05-15',
  neighborhood:'Centro',city:'Santos',notes:'Prefere atendimento pela manhã',
  total_appointments:3,total_spent_cents:10000,loyalty_points:8,
};

function fakeCustomers(items=[activeRecord],write){
  return (path,method='GET',payload)=>{
    if(path.includes('/management/customers')&&method==='GET')
      return {items,total:items.length};
    if(path.includes('/management/customers')&&['POST','PATCH'].includes(method)){
      write?.({path,method,payload});
      return {...(items[0]||customer('Novo cliente')), ...payload,version:6};
    }
    return defaultResponse(path);
  };
}
async function fixture(t,options={}){
  const h=await makeSuite({initialRoute:'clientes',...options});
  t.after(()=>h.close());
  await flush();return h;
}

test('novo cliente apresenta campos com ícones, notas visíveis e Mais dados nativo recolhido',async t=>{
  const h=await fixture(t,{respond:fakeCustomers([])});
  h.click('[data-os-action=new-customer]');
  const dialog=h.document.getElementById('osDialog');
  assert.ok(dialog.open);
  const form=dialog.querySelector('[data-os-form=customer]');
  assert.ok(form);
  assert.ok(form.querySelector('.os-client-form'));
  const icons={name:'userPlus',phone:'phone',instagram:'instagram'};
  for(const name of Object.keys(icons)){
    const input=form.querySelector('[name='+name+']');
    assert.ok(input,'campo '+name+' presente');
    const wrapper=input.closest('.os-client-input');
    assert.ok(wrapper,'campo '+name+' dentro de controle visual');
    assert.ok(wrapper.querySelector('.os-client-input-icon svg.os-icon'),'ícone do campo '+name);
    assert.equal(wrapper.querySelector('svg.os-icon').getAttribute('aria-hidden'),'true');
  }
  const extra=form.querySelector('details.os-client-more');
  assert.ok(extra);
  assert.equal(extra.open,false,'novos cadastros começam compactos');
  assert.match(extra.querySelector('summary').textContent,/Mais dados\s*\(e-mail, aniversário, endereço\)/);
  assert.equal(extra.querySelector('summary .os-client-more-icon').getAttribute('aria-hidden'),'true');
  assert.equal(extra.querySelector('[name=email]').type,'email');
  assert.equal(extra.querySelector('[name=birth_date]').type,'date','usa seletor de data nativo');
  assert.ok(extra.querySelector('.os-client-grid [name=neighborhood]'));
  assert.ok(extra.querySelector('.os-client-grid [name=city]'));
  assert.equal(form.querySelector('[name=notes]').closest('details'),null,'observações ficam visíveis fora de Mais dados');
  assert.ok(form.querySelector('.os-client-textarea textarea[name=notes]'));
  assert.equal(form.elements.name.required,true);
  assert.equal(form.elements.email.required,false);
  assert.equal(form.elements.phone.required,false);
  extra.querySelector('summary').click();
  assert.equal(extra.open,true,'sumário nativo expande sem JS adicional');
  extra.querySelector('summary').click();
  assert.equal(extra.open,false,'sumário nativo recolhe');
  assert.equal(h.errors.length,0);
});

test('cadastro envia os mesmos campos mesmo quando Mais dados permanece fechado',async t=>{
  const writes=[],h=await fixture(t,{respond:fakeCustomers([],(v)=>writes.push(v))});
  h.click('[data-os-action=new-customer]');
  const values={name:'Cliente Sintética',phone:'(11) 91234-5678',instagram:'@cliente',
    email:'pessoa@example.invalid',birth_date:'1990-03-04',neighborhood:'Centro',
    city:'Rio de Janeiro',notes:'Prefere horários cedo'};
  for(const [name,value] of Object.entries(values))h.fill('[name='+name+']',value);
  const more=h.document.querySelector('details.os-client-more');
  assert.equal(more.open,false,'o resumo fechado não impede a gravação dos campos');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].method,'POST');
  assert.equal(writes[0].payload.name,values.name);
  assert.equal(writes[0].payload.phone,'+5511912345678');
  for(const key of ['instagram','email','birth_date','neighborhood','city','notes'])
    assert.equal(writes[0].payload[key],values[key]);
  assert.equal(writes[0].payload.active,true);
  assert.match(writes[0].payload.idempotency_key,/^[a-f0-9-]{36}$/i);
  assert.equal(h.errors.length,0);
});

test('edição mantém dados, detalhes preenchidos abertos, estatísticas e versão',async t=>{
  const writes=[],h=await fixture(t,{respond:fakeCustomers([activeRecord],v=>writes.push(v))});
  h.click('[data-os-customer="'+customerId+'"]');
  const dialog=h.document.getElementById('osDialog');
  const form=dialog.querySelector('[data-os-form=customer]');
  assert.ok(form);
  const details=form.querySelector('details.os-client-more');
  assert.equal(details.open,true,'mostrar dados extras existentes na edição');
  assert.equal(form.elements.email.value,activeRecord.email);
  assert.equal(form.elements.birth_date.value,activeRecord.birth_date);
  assert.equal(form.elements.instagram.value,activeRecord.instagram);
  assert.equal(form.elements.neighborhood.value,activeRecord.neighborhood);
  assert.equal(form.elements.city.value,activeRecord.city);
  assert.equal(form.elements.notes.value,activeRecord.notes);
  assert.equal(form.elements.active.checked,true);
  assert.equal(dialog.querySelectorAll('.os-stat').length,3,'resumo de atendimentos é mantido');
  details.open=false;
  h.fill('[name=name]','Cliente atualizado');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].method,'PATCH');
  assert.match(writes[0].path,new RegExp('/management/customers/'+customerId+'$'));
  assert.equal(writes[0].payload.expected_version,5);
  assert.equal(writes[0].payload.email,activeRecord.email);
  assert.equal(writes[0].payload.birth_date,activeRecord.birth_date);
  assert.equal(writes[0].payload.notes,activeRecord.notes);
  assert.equal(writes[0].payload.name,'Cliente atualizado');
  assert.equal(h.errors.length,0);
});

test('usuário leitor continua sem permissão de gravação e conteúdo é escapado',async t=>{
  const record={...activeRecord,name:'<img src=x onerror=alert(1)>'};
  const h=await fixture(t,{role:'viewer',respond:fakeCustomers([record])});
  h.click('[data-os-customer="'+customerId+'"]');
  const dialog=h.document.getElementById('osDialog'),form=dialog.querySelector('[data-os-form=customer]');
  assert.equal(form.querySelector('fieldset').disabled,true);
  assert.equal(form.querySelector('button[type=submit]').disabled,true);
  assert.equal(dialog.querySelector('[name=name]').value,record.name);
  assert.equal(dialog.querySelector('img[src=x]'),null);
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(h.calls.filter(x=>x.method!=='GET').length,0);
});

test('estilos de clientes respeitam o tema e têm foco e layout responsivo sem mexer em outros formulários',async ()=>{
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  const js=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
  for(const cls of ['os-client-form','os-client-input','os-client-input-icon','os-client-textarea',
    'os-client-more','os-client-more-toggle','os-client-more-icon','os-client-extra-fields','os-client-grid'])
    assert.match(js,new RegExp(cls),'estrutura do formulário: '+cls);
  assert.match(css,/#osDialog\.os-customer-dialog \.os-client-input:focus-within/);
  assert.match(css,/#osDialog\.os-customer-dialog \.os-client-more-toggle:focus-visible/);
  assert.match(css,/#osDialog\.os-customer-dialog \.os-client-input>input:not/);
  assert.match(css,/background:color-mix\(in srgb,var\(--os-tint\)/,'campos usam cores semânticas dos temas claro e escuro');
  assert.match(css,/@media\(max-width:359px\)\{#osDialog\.os-customer-dialog \.os-client-grid\{grid-template-columns:1fr\}\}/);
  assert.match(js,/const hasExtra=!!\(record&&\(c\.email\|\|c\.birth_date\|\|c\.neighborhood\|\|c\.city\)\)/);
  assert.match(css,/\.os-client-more-toggle::-webkit-details-marker/);
  assert.doesNotMatch(js,/id="client-name"|class="os-client-more-toggle__icon"/,'sem IDs ou padrões paralelos genéricos');
});
