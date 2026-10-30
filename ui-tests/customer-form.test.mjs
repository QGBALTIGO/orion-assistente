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
  assert.equal(extra.querySelector('.os-client-more-label-collapsed')?.textContent,'Mais dados');
  assert.equal(extra.querySelector('.os-client-more-label-expanded')?.textContent,'Menos dados');
  assert.equal(extra.querySelector('summary small')?.textContent,'(e-mail, aniversário, endereço)');
  assert.equal(extra.querySelectorAll('.os-client-more-icon svg.os-icon').length,2,'plus e minus usam SVGs reais');
  assert.equal(extra.querySelector('summary .os-client-more-icon').getAttribute('aria-hidden'),'true');
  assert.equal(extra.querySelector('[name=email]').type,'email');
  assert.equal(extra.querySelector('[name=birth_date]').type,'date','usa seletor de data nativo');
  assert.equal(extra.querySelector('label[for=osField-birth_date]').textContent,'Data de nascimento (opcional)');
  assert.equal(extra.querySelector('[name=birth_day]'),null);
  assert.equal(extra.querySelector('[name=birth_month]'),null);
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
  assert.equal(Object.hasOwn(writes[0].payload,'birth_day'),false);
  assert.equal(Object.hasOwn(writes[0].payload,'birth_month'),false);
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

test('aniversário antigo sem ano é preservado, completado ou removido sem inventar uma data',async t=>{
  const legacy={...activeRecord,birth_date:null,birth_month:5,birth_day:15};
  for(const mode of ['preservar','completar','remover']){
    await t.test(mode,async t=>{
      const writes=[],h=await fixture(t,{respond:fakeCustomers([legacy],v=>writes.push(v))});
      h.click('[data-os-customer="'+customerId+'"]');
      const input=h.document.querySelector('[name=birth_date]');
      assert.equal(input.value,'');
      assert.match(h.document.getElementById('osCustomerLegacyBirthday').textContent,/15\/05/);
      h.fill('[name=name]','Nome atualizado');
      if(mode==='completar')h.fill('[name=birth_date]','1995-05-15');
      if(mode==='remover')h.click('[data-os-action=customer-birth-clear]');
      h.submit('[data-os-form=customer]');await flush();
      assert.equal(writes.length,1);
      const payload=writes[0].payload;
      if(mode==='preservar')assert.equal(Object.hasOwn(payload,'birth_date'),false,'salvar outro campo não apaga o aniversário parcial');
      else assert.equal(payload.birth_date,mode==='completar'?'1995-05-15':null);
      assert.equal(Object.hasOwn(payload,'birth_day'),false);
      assert.equal(Object.hasOwn(payload,'birth_month'),false);
      assert.equal(h.errors.length,0);
    });
  }
});

test('apagar a data completa envia null sem reenviar dia e mês antigos',async t=>{
  const writes=[],h=await fixture(t,{respond:fakeCustomers([{...activeRecord,birth_month:5,birth_day:15}],v=>writes.push(v))});
  h.click('[data-os-customer="'+customerId+'"]');
  h.fill('[name=birth_date]','');h.submit('[data-os-form=customer]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].payload.birth_date,null);
  assert.equal(Object.hasOwn(writes[0].payload,'birth_day'),false);
  assert.equal(Object.hasOwn(writes[0].payload,'birth_month'),false);
  assert.equal(h.errors.length,0);
});

test('cadastro reconhecido pelo telefone informa que o cliente existente foi mantido',async t=>{
  const h=await fixture(t,{respond:(path,method='GET')=>{
    if(path.includes('/management/customers')&&method==='POST')return {...activeRecord,reused_existing:true};
    return fakeCustomers([activeRecord])(path,method);
  }});
  h.click('[data-os-action=new-customer]');
  h.fill('[name=name]','Mesmo cliente');h.fill('[name=phone]','(11) 91234-5678');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(h.document.getElementById('osToastRegion').textContent,'Cliente já cadastrado. Mantivemos as informações existentes.');
  assert.equal(h.document.querySelectorAll('.os-mobile-customers .os-swipe-row').length,1);
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
  assert.match(js,/const hasExtra=!!\(record&&\(c\.email\|\|c\.birth_date\|\|c\.birth_day\|\|c\.neighborhood\|\|c\.city\)\)/);
  assert.match(css,/\.os-client-more-toggle::-webkit-details-marker/);
  assert.doesNotMatch(js,/id="client-name"|class="os-client-more-toggle__icon"/,'sem IDs ou padrões paralelos genéricos');
});

test('mais e menos dados usam dois rótulos e SVGs alinhados conforme open',async t=>{
  const h=await fixture(t,{respond:fakeCustomers([])});
  h.click('[data-os-action=new-customer]');
  const details=h.document.querySelector('details.os-client-more');
  const summary=details.querySelector('summary.os-client-more-toggle');
  assert.equal(details.open,false);
  const plus=summary.querySelector('.os-client-more-icon>.os-icon:first-child');
  const minus=summary.querySelector('.os-client-more-icon>.os-client-more-minus');
  assert.ok(plus&&minus);
  assert.equal(plus.getAttribute('viewBox'),'0 0 24 24');
  assert.equal(minus.getAttribute('viewBox'),'0 0 24 24');
  assert.equal(plus.querySelector('path')?.getAttribute('d'),'M12 5v14M5 12h14');
  assert.equal(minus.querySelector('path')?.getAttribute('d'),'M5 12h14');
  assert.equal(summary.querySelector('.os-client-more-label-collapsed').textContent,'Mais dados');
  assert.equal(summary.querySelector('.os-client-more-label-expanded').textContent,'Menos dados');
  summary.click();assert.equal(details.open,true);
  summary.click();assert.equal(details.open,false);
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const piece of [
    '.os-client-more-icon{display:grid;place-items:center;flex:0 0 20px;width:20px;height:20px}',
    '.os-client-more-icon>.os-icon{display:block;grid-area:1/1;width:18px;height:18px',
    '.os-client-more[open] .os-client-more-icon>.os-icon:first-child{display:none}',
    '.os-client-more[open] .os-client-more-icon>.os-client-more-minus{display:block}',
    '.os-client-more[open] .os-client-more-label-collapsed{display:none}',
    '.os-client-more[open] .os-client-more-label-expanded{display:inline}'
  ])assert.ok(css.includes(piece),'estilo acessível do controle: '+piece);
  assert.equal(h.errors.length,0);
});

test('aniversário mantém ícone e conteúdo nativo da data centralizados no Safari',async t=>{
  const h=await fixture(t,{respond:fakeCustomers([activeRecord])});
  h.click('[data-os-customer="'+customerId+'"]');
  const input=h.document.querySelector('input[name=birth_date]');
  const wrapper=input?.closest('.os-client-date'),icon=wrapper?.querySelector('.os-client-input-icon svg');
  assert.ok(wrapper&&icon,'campo de aniversário presente e iconizado');
  assert.equal(input.type,'date');
  assert.equal(input.value,'1995-05-15','a data permanece nativa e não é convertida em texto');
  assert.equal(icon.getAttribute('aria-hidden'),'true');
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const piece of [
    '.os-client-date{display:grid;grid-template-columns:19px minmax(0,1fr);align-items:center;',
    '.os-client-date .os-client-input-icon{display:grid;place-items:center;align-self:center;width:19px;height:48px}',
    '.os-client-date>input[type=date]{display:block;box-sizing:border-box;width:100%;height:48px;',
    'input[type=date]::-webkit-date-and-time-value{display:flex;align-items:center;min-height:48px;',
    'input[type=date]::-webkit-datetime-edit{display:inline-flex;align-items:center;min-height:24px;'
  ])assert.ok(css.includes(piece),'centralização do aniversário: '+piece);
  assert.equal(h.errors.length,0);
});

test('totais altos ocupam uma linha inteira e não truncam valores monetários',async t=>{
  const veryLarge=12345678901234;
  const rec={...activeRecord,total_spent_cents:veryLarge,total_appointments:10000000,loyalty_points:99999999};
  const h=await fixture(t,{respond:fakeCustomers([rec])});
  h.click('[data-os-customer="'+customerId+'"]');
  const stats=h.document.querySelector('.os-client-stats'),cards=[...stats.querySelectorAll('.os-stat')];
  assert.equal(cards.length,3);
  assert.equal(cards[0].classList.contains('os-client-stat-total'),true);
  assert.equal(cards[0].querySelector('small').textContent,'Total atendido');
  const expected=new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(veryLarge/100);
  assert.equal(cards[0].querySelector('strong').textContent,expected,'o valor completo deve ser preservado');
  assert.ok(cards[1].textContent.includes('10000000'),'atendimentos continuam visíveis');
  assert.ok(cards[2].textContent.includes('99999999'),'pontos continuam visíveis');
  const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  for(const piece of [
    '.os-client-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));',
    '.os-client-stats>.os-client-stat-total{grid-column:1/-1;',
    '.os-client-stats>.os-stat>strong{display:block;min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere;',
    'font:700 clamp(18px,5.3vw,25px)/1.25 var(--os-display)'
  ])assert.ok(css.includes(piece),'responsividade dos indicadores: '+piece);
  assert.equal(h.errors.length,0);
});


test('customer creation in admin preserves a literal +595 instead of silently saving Brazilian +55',async t=>{
  const writes=[],h=await fixture(t,{respond:fakeCustomers([],value=>writes.push(value))});
  h.click('[data-os-action=new-customer]');
  const field=h.document.querySelector('[data-os-form=customer] [name=phone]');
  assert.equal(field.dataset.orionInternational,'true');
  h.fill('[name=name]','Cliente internacional de teste');
  h.fill('[name=phone]','+595 992 820210');
  assert.equal(field.value,'+595 992 820210');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].payload.phone,'+595992820210');
  assert.equal(writes[0].payload.name,'Cliente internacional de teste');
  assert.deepEqual(h.errors,[]);
});

test('editing an international client retains +595 and never converts it to +55',async t=>{
  const record={...activeRecord,phone:'+595992820210'};
  const writes=[],h=await fixture(t,{respond:fakeCustomers([record],value=>writes.push(value))});
  h.click('[data-os-customer="'+customerId+'"]');
  const field=h.document.querySelector('[data-os-form=customer] [name=phone]');
  assert.equal(field.value,'+595992820210');
  h.submit('[data-os-form=customer]');await flush();
  assert.equal(writes.length,1);
  assert.equal(writes[0].payload.phone,'+595992820210');
  assert.equal(writes[0].method,'PATCH');
  assert.deepEqual(h.errors,[]);
});
