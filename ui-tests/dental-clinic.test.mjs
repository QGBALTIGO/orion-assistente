import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';

const js=readFileSync(new URL('../app/static/orion-dental-clinic.js',import.meta.url),'utf8');
const css=readFileSync(new URL('../app/static/orion-dental-clinic.css',import.meta.url),'utf8');
const tenant='10000000-0000-4000-8000-000000000001';
const patient='30000000-0000-4000-8000-000000000003';
const pro='40000000-0000-4000-8000-000000000004';
const account='50000000-0000-4000-8000-000000000005';
const patientDTO={id:patient,name:'Paciente de teste',version:1,guardian_name:'Responsável',
  birth_date:'2001-05-06',created_at:'2026-11-01T11:00:00Z'};
const response=(path,method='GET',data,config)=>{
  const rows=config.patients||[patientDTO];
  if(path.endsWith('/dental/access'))return {account_id:account,can_manage:config.canManage===true,can_read_records:config.level==='dentist',
    clinical_access:config.level?{account_id:account,level:config.level,professional_id:pro}:null,grants:[]};
  if(path.includes('/dental/patients?'))return {items:rows,total:rows.length};
  if(path.endsWith('/dental/patients'))return {id:randomUUID(),...data};
  if(path.includes('/dental/patients/'+patient+'/records?'))return {items:[]};
  if(path.includes('/dental/patients/'+patient+'/exams')&&method==='GET')return {items:[]};
  if(path.endsWith('/dental/patients/'+patient))return patientDTO;
  if(path.includes('/dental/appointments?'))return {
    timezone:'America/Campo_Grande',items:config.appointments||[]};
  if(path.endsWith('/dental/patients/'+patient+'/records')&&method==='POST')return {id:randomUUID(),recorded:true};
  if(path.endsWith('/dental/intake-templates/clinical'))return {items:config.intakeTemplates||[]};
  if(path.endsWith('/dental/rooms'))return method==='GET'?{items:config.rooms||[]}:{id:randomUUID(),...data};
  if(path.endsWith('/room')&&method==='PUT')return {assigned:true};
  if(path.includes('/dental/supplies?'))return config.catalog?.(new URL(path,'https://orion.test').searchParams)||{items:config.supplies||[],next_cursor:null};
  if(path.includes('/dental/supplies/')&&method==='POST')return config.move?.(data)||{replayed:false};
  if(path.endsWith('/dental/supplies'))return method==='GET'?{items:config.supplies||[]}:{id:randomUUID(),...data};
  if(path.includes('/dental/supplies/')&&path.includes('/movements')&&method==='GET')
    return config.supplyHistory?.(new URL(path,'https://orion.test').searchParams)||{items:[],next_cursor:null};
  if(path.includes('/dental/prostheses'))
    return config.prostheses?.(new URL(path,'https://orion.test'),method,data)||{items:[],next_cursor:null};
  if(path.includes('/dental/laboratories'))
    return config.laboratories?.(path,method,data)||{items:[]};
  return {items:[]};
};
function makeDOM(config={}){
  const dom=new JSDOM('<!doctype html><html lang="pt-BR"><body><section id="clinical"></section></body></html>',
    {url:'https://orion.test/painel',runScripts:'outside-only'});
  const {window}=dom;
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID,configurable:true});
  window.eval(js);
  const calls=[];
  const host=window.document.getElementById('clinical');
  const requester=async (path,method,data)=>{
    calls.push({path,method:method||'GET',data});
    return await response(path,method,data,config);
  };
  const mount=(options={})=>window.OrionDentalClinic.mount({host,request:requester,tenantId:tenant,canAccess:()=>true,...options});
  const click=selector=>{const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);node.click();return node};
  const fill=(selector,value)=>{const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node};
  const submit=selector=>{const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);
    return node.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}))};
  return {dom,window,host,calls,mount,click,fill,submit,close(){window.OrionDentalClinic.unmount();dom.window.close();}};
}
async function flush(){for(let i=0;i<5;i++)await new Promise(resolve=>setImmediate(resolve));}

test('unapproved account cannot open records and shows owner permission guidance',async t=>{
  const h=makeDOM();t.after(()=>h.close());
  await h.mount();await flush();
  assert.match(h.host.textContent,/acesso ao prontuário ainda não foi concedido/);
  assert.equal(h.host.querySelector('[data-dental-form=record]'),null);
  assert.equal(h.calls.some(x=>x.path.includes('/records?')),false);
  assert.equal(h.calls.some(x=>x.method==='POST'),false);
});

test('assistant sees administrative patients but no charts or examination requests',async t=>{
  const h=makeDOM({level:'assistant'});t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  assert.match(h.host.textContent,/Este acesso administrativo não inclui/);
  assert.equal(h.host.querySelector('[data-dental-form=record]'),null);
  assert.equal(h.calls.some(x=>x.path.includes('/records?')),false);
  assert.equal(h.calls.some(x=>x.path.includes('/exams')&&x.method==='GET'),false);
});

test('dentist sees 32 permanent teeth and can record one explicit finding only',async t=>{
  const h=makeDOM({level:'dentist'});t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  h.click('[data-dental-tab=odontogram]');await flush();
  assert.equal(h.host.querySelectorAll('[data-dental-tooth]').length,32);
  assert.equal(h.host.querySelectorAll('.os-dental-tooth[aria-pressed=true]').length,1);
  h.click('[data-dental-tooth="18"]');
  assert.equal(h.host.querySelector('.os-dental-tooth[aria-pressed=true]').dataset.dentalTooth,'18');
  h.click('[data-dental-action=add-finding]');await flush();
  assert.match(h.host.textContent,/18/);
  assert.match(h.host.textContent,/watch|Acompanhar|caries|Cárie/);
  h.submit('[data-dental-form=record]');await flush();
  const writes=h.calls.filter(x=>x.method==='POST'&&x.path.endsWith('/records'));
  assert.equal(writes.length,1);
  assert.equal(writes[0].data.kind,'odontogram');
  assert.equal(writes[0].data.content.findings.length,1);
  assert.equal(writes[0].data.content.findings[0].tooth,18);
  assert.equal(writes[0].data.content.findings[0].face,'M');
});

test('periodontal sites keep null for all unmeasured locations instead of inventing 2mm',async t=>{
  const h=makeDOM({level:'dentist'});t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  h.click('[data-dental-tab=periodontal]');await flush();
  const tooth=h.host.querySelector('select[name=period_tooth]');
  tooth.value='11';tooth.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  h.fill('input[name=depth_0]','3');
  h.click('[data-dental-action=add-period]');await flush();
  assert.match(h.host.textContent,/1 dente\(s\) medido/);
  h.submit('[data-dental-form=record]');await flush();
  const write=h.calls.find(x=>x.method==='POST'&&x.path.endsWith('/records'));
  assert.ok(write);
  assert.equal(write.data.kind,'periodontal');
  assert.equal(write.data.content.teeth[0].sites.length,6);
  assert.equal(write.data.content.teeth[0].sites[0].depth_mm,3);
  for(const site of write.data.content.teeth[0].sites.slice(1))
    assert.equal(site.depth_mm,null);
});

test('patient names and clinical content are escaped and never persisted in localStorage',async t=>{
  const unsafe='<img src=x onerror=alert(1)>';
  const h=makeDOM({level:'dentist',patients:[{...patientDTO,name:unsafe}]});t.after(()=>h.close());
  await h.mount();await flush();
  assert.equal(h.host.querySelector('img[src=x]'),null);
  assert.match(h.host.textContent,/onerror=alert/);
  assert.doesNotMatch(js,/localStorage|sessionStorage|indexedDB/);
  assert.doesNotMatch(js,/fetch\([^,]+,\s*\{[^}]*method:\s*['"]POST['"]/);
});

test('unmount clears DOM, forgets patient and invalidates old user context',async t=>{
  const h=makeDOM({level:'dentist'});t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  h.window.OrionDentalClinic.unmount();
  assert.equal(h.host.childNodes.length,0);
  assert.equal(h.calls.filter(x=>x.method==='POST').length,0);
});

test('CSS reuses Orion tokens and preserves mobile/low-motion design',()=>{
  assert.match(css,/var\(--os-text\)/);
  assert.match(css,/var\(--os-primary\)/);
  assert.match(css,/@media\(max-width:767px\)/);
  assert.match(css,/@media\(max-width:359px\)/);
  assert.match(css,/prefers-reduced-motion:reduce/);
});


test('consultório selects actual confirmed appointment without exposing internal UUID input',async t=>{
  const appointment='60000000-0000-4000-8000-000000000006';
  const room='70000000-0000-4000-8000-000000000007';
  const h=makeDOM({level:'dentist',rooms:[{id:room,name:'Consultório 1',active:true}],
    appointments:[{id:appointment,customer_name:'Paciente Teste',professional_name:'Dentista A',
      start_at:'2026-11-10T13:00:00Z',room_name:null}]});
  t.after(()=>h.close());await h.mount();await flush();
  h.click('[data-dental-action=section-rooms]');await flush();
  assert.ok(h.calls.some(x=>x.path.includes('/dental/appointments?day=')));
  assert.equal(h.host.querySelector('input[name=appointment_id]'),null);
  const selector=h.host.querySelector('select[name=appointment_id]');
  assert.ok(selector);
  assert.equal(selector.options.length,1);
  assert.equal(selector.value,appointment);
  assert.match(selector.options[0].textContent,/Paciente Teste/);
  h.submit('[data-dental-form=room-reservation]');await flush();
  const writes=h.calls.filter(x=>x.method==='PUT'&&x.path.includes('/appointments/'));
  assert.equal(writes.length,1);
  assert.equal(writes[0].path,'/api/v1/tenants/'+tenant+'/dental/appointments/'+appointment+'/room');
  assert.equal(writes[0].data.room_id,room);
});

test('dentista seleciona um modelo e envia respostas, com a versão, ao prontuário existente',async t=>{
  const template={id:'64444444-4444-4444-8444-444444444444',name:'Modelo sintético',version:3,questions:['Pergunta A','Pergunta B']};
  const h=makeDOM({level:'dentist',intakeTemplates:[template]});t.after(()=>h.close());
  await h.mount();h.click('[data-dental-action=select-patient]');await flush();
  const select=h.host.querySelector('[name=intake_template]');
  assert.ok(select,'o formulário oferece modelos configurados');
  select.value=template.id;
  select.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  h.fill('[name=complaint]','Queixa de teste');
  h.fill('[name=intake_answer_0]','Resposta sintética');
  h.submit('[data-dental-form=record][data-kind=anamnesis]');await flush();
  const call=h.calls.find(c=>c.method==='POST'&&c.path.endsWith('/records'));
  assert.ok(call);
  assert.deepEqual(JSON.parse(JSON.stringify(call.data.content.intake_template)),
    {template_id:template.id,version:3,answers:['Resposta sintética','']});
  assert.equal(h.window.localStorage.length,0);
});

test('stock history paginates and filters entries without affecting the balance',async t=>{
  const supply={id:randomUUID(),name:'Gaze de teste',quantity:13,unit:'box'};
  const newest={id:randomUUID(),movement:'in',quantity:10,occurred_at:'2026-11-01T18:00:00Z'};
  const older={id:randomUUID(),movement:'out',quantity:2,occurred_at:'2026-11-01T17:00:00Z'};
  const h=makeDOM({level:'assistant',supplies:[supply],supplyHistory:params=>{
    if(params.get('movement')==='out'||params.has('cursor'))return {items:[older],next_cursor:null};
    return {items:[newest],next_cursor:newest.id};
  }});
  t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=section-supplies]');await flush();
  assert.match(h.host.textContent,/13 cx\./);
  h.click('[data-dental-action=supply-history]');await flush();
  assert.match(h.host.textContent,/Histórico de movimentações/);
  assert.match(h.host.textContent,/10 cx\./);
  h.click('[data-dental-action=supply-history-more]');await flush();
  assert.equal(h.host.querySelectorAll('tbody tr').length,2);
  assert.ok(h.calls.some(call=>call.path.endsWith('cursor='+newest.id)));
  const filter=h.host.querySelector('select[name=supply_history_movement]');
  filter.value='out';
  filter.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  assert.equal(h.host.querySelectorAll('tbody tr').length,1);
  assert.match(h.host.querySelector('tbody').textContent,/Saída.*2 cx\./);
  h.click('[data-dental-action=supply-history-close]');await flush();
  assert.match(h.host.textContent,/13 cx\./);
  assert.equal(h.window.document.activeElement.dataset.dentalAction,'supply-history');
  assert.ok(h.calls.every(call=>call.method==='GET'));
});

test('worklist filters phases, updates the selected work and opens its clinical record',async t=>{
  const id=randomUUID();
  const nextId=randomUUID();
  let saved=false;
  const initial={id,patient_id:patient,patient_name:'Paciente de demonstração',
    content:{laboratory:'Laboratório de teste',description:'Prótese sintética',status:'requested',due_date:'2027-01-09'},
    actions:['ready_to_send','cancelled']};
  const h=makeDOM({level:'dentist',prostheses:(url,method)=>{
    if(method==='PATCH'){saved=true;return {id:nextId,status:'ready_to_send'};}
    const filter=url.searchParams.get('status');
    const next={...initial,id:nextId,content:{...initial.content,status:'ready_to_send'},actions:['working','requested','cancelled']};
    return {items:filter==='cancelled'?[]:[saved?next:initial],next_cursor:null};
  }});
  t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=section-prostheses]');await flush();
  assert.match(h.host.textContent,/Paciente de demonstração/);
  assert.match(h.host.textContent,/09\/01\/2027/);
  const selectFilter=value=>{
    const filter=h.host.querySelector('select[name=prosthesis_filter]');
    filter.value=value;
    filter.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  };
  selectFilter('cancelled');await flush();
  assert.match(h.host.textContent,/Nenhuma prótese encontrada/);
  assert.ok(h.calls.some(call=>call.path.endsWith('/prostheses?status=cancelled')));
  selectFilter('');await flush();
  h.fill('select[name="prosthesis_step_'+id+'"]','ready_to_send');
  h.submit('[data-dental-form=prosthesis-step]');await flush();
  const change=h.calls.find(call=>call.method==='PATCH');
  assert.ok(change.path.endsWith('/prostheses/'+id+'/status'));
  assert.equal(change.data.status,'ready_to_send');
  assert.ok(change.data.entry_key);
  assert.match(h.host.textContent,/Pronta para envio/);
  assert.equal(h.host.querySelector('[data-dental-form=prosthesis-step]').dataset.id,nextId);
  h.click('[data-dental-action=open-prosthesis-patient]');await flush();
  assert.ok(h.host.querySelector('[data-dental-tab="prosthesis"][aria-selected=true]'));
  assert.ok(h.calls.some(call=>call.path.endsWith('/patients/'+patient+'/records?limit=100')));
});

test('an assistant cannot open prosthesis records from the worklist',async t=>{
  const h=makeDOM({level:'assistant'});t.after(()=>h.close());
  await h.mount();await flush();
  assert.equal(h.host.querySelector('[data-dental-action=section-prostheses]'),null);
  assert.equal(h.calls.some(call=>call.path.includes('/prostheses')),false);
});

test('failed patient lookup from the worklist does not expose the previously open chart',async t=>{
  const differentPatient=randomUUID();
  const id=randomUUID();
  const h=makeDOM({level:'dentist',prostheses:()=>({items:[{
    id,patient_id:differentPatient,patient_name:'Outro paciente sintético',
    content:{laboratory:'Laboratório',description:'Prótese diferente',status:'completed'},actions:[]
  }],next_cursor:null})});
  t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  assert.ok(h.host.querySelector('[data-dental-form=record]'));
  h.click('[data-dental-action=section-prostheses]');await flush();
  h.click('[data-dental-action=open-prosthesis-patient]');await flush();
  assert.match(h.host.textContent,/Resposta não corresponde ao paciente selecionado/);
  assert.equal(h.host.querySelector('[data-dental-form=record]'),null);
  assert.equal(h.host.querySelector('[data-dental-tab=prosthesis]'),null);
});

test('laboratories are reusable after registration and inactive entries stop being suggested',async t=>{
  const id=randomUUID(),otherId=randomUUID();
  const initial=[{id:otherId,name:'Laboratório ativo de exemplo',phone:null,active:true,version:1}];
  const h=makeDOM({level:'dentist',laboratories:(_path,method,data)=>{
    if(method==='POST')return {id,...data,active:true,version:1};
    if(method==='PATCH')return {id,name:data.name,phone:data.phone,active:data.active,version:2};
    return {items:initial};
  }});
  t.after(()=>h.close());
  await h.mount();await flush();
  h.click('[data-dental-action=section-prostheses]');await flush();
  h.click('[data-dental-action=section-laboratories]');await flush();
  h.fill('[name=laboratory_name]','Laboratório recém-cadastrado');
  h.fill('[name=laboratory_phone]','(67) 99999-0000');
  h.submit('[data-dental-form=laboratory]');await flush();
  assert.match(h.host.textContent,/Laboratório recém-cadastrado/);
  assert.equal(h.calls.find(call=>call.method==='POST').data.name,'Laboratório recém-cadastrado');
  h.click('[data-dental-action=edit-laboratory][data-id="'+id+'"]');
  h.fill('[name=laboratory_active]','false');
  h.submit('[data-dental-form=laboratory]');await flush();
  const change=h.calls.find(call=>call.method==='PATCH');
  assert.equal(change.data.expected_version,1);
  assert.equal(change.data.active,false);
  assert.match(h.host.textContent,/Inativo/);

  h.click('[data-dental-action=section-patients]');await flush();
  h.click('[data-dental-action=select-patient]');await flush();
  h.click('[data-dental-tab=prosthesis]');await flush();
  const optionValues=Array.from(h.host.querySelectorAll('#dental-laboratory-options option')).map(o=>o.value);
  assert.deepEqual(optionValues,['Laboratório ativo de exemplo']);
  assert.equal(h.host.querySelector('[name=laboratory]').getAttribute('list'),'dental-laboratory-options');
});


test('o estoque dedicado busca, ordena e pagina sem requisitar dados de pacientes ou equipe',async t=>{
  const first={id:randomUUID(),name:'Gaze A',unit:'box',quantity:4};
  const second={id:randomUUID(),name:'Gaze B',unit:'box',quantity:0};
  const h=makeDOM({level:'assistant',canManage:true,catalog:params=>{
    if(params.get('q')==='inexistente')return {items:[],next_cursor:null};
    if(params.has('cursor'))return {items:[second],next_cursor:null};
    return {items:[first],next_cursor:first.id};
  }});
  t.after(()=>h.close());await h.mount({dedicatedStock:true});
  assert.equal(h.host.querySelector('[data-dental-action=section-patients]'),null);
  h.click('[data-dental-action=stock-more]');await flush();
  assert.equal(h.host.querySelectorAll('.os-stock-list li').length,2);
  h.fill('[name=stock_query]','inexistente');h.fill('[name=stock_order]','desc');
  h.submit('[data-dental-form=stock-search]');await flush();
  assert.match(h.host.textContent,/Nenhum material corresponde/);
  assert.ok(h.calls.some(x=>x.path.includes('q=inexistente&order=desc')));
  assert.ok(h.calls.every(x=>x.path.endsWith('/access')||x.path.includes('/dental/supplies?')));
});

test('uma queda após a movimentação conserva a operação no formulário para repetir com a mesma chave',async t=>{
  const item={id:randomUUID(),name:'Gaze',unit:'box',quantity:6};
  let key=null;
  const h=makeDOM({level:'assistant',supplies:[item],move:data=>{
    if(!key){key=data.entry_key;item.quantity-=data.quantity;throw Error('A resposta se perdeu');}
    assert.equal(data.entry_key,key);return {replayed:true};
  }});
  t.after(()=>h.close());await h.mount({dedicatedStock:true});
  h.click('[data-dental-action=supply-out]');h.fill('[name=stock_quantity]','2');
  h.submit('[data-dental-form=stock-move]');await flush();
  assert.match(h.host.textContent,/A resposta se perdeu/);
  assert.equal(h.host.querySelector('[name=stock_quantity]').value,'2');
  h.submit('[data-dental-form=stock-move]');await flush();
  assert.equal(item.quantity,4);assert.match(h.host.textContent,/4 cx/);
  assert.equal(h.calls.filter(x=>x.method==='POST').length,2);
});

test('o diretório exige concessão e não carrega cadastros de um proprietário sem ela',async t=>{
  const h=makeDOM();t.after(()=>h.close());
  const paths=[];let opened=0;
  await h.window.OrionDentalDirectory.mount({host:h.host,tenantId:tenant,canAccess:()=>true,canWrite:()=>true,
    startNew:true,openAccess:()=>opened++,request:async path=>{
      paths.push(path);return {can_manage:true,clinical_access:null,can_read_records:false};
    }});
  assert.equal(h.host.querySelector('[data-patient-editor]'),null);
  assert.ok(paths.every(path=>path.endsWith('/dental/access')));
  h.click('[data-patient-action=access]');assert.equal(opened,1);
});

test('a página de pacientes pesquisa no corpo da requisição e descarta resultados antigos',async t=>{
  const h=makeDOM();t.after(()=>h.close());
  const requests=[];let finishOld;
  await h.window.OrionDentalDirectory.mount({host:h.host,tenantId:tenant,canAccess:()=>true,canWrite:()=>true,
    request:async(path,method,options)=>{
      requests.push({path,method,options});
      if(path.endsWith('/access'))return {clinical_access:{level:'assistant'}};
      if(options.query==='Anterior')return new Promise(resolve=>finishOld=()=>resolve({items:[patientDTO],has_more:false}));
      if(options.query==='Atual')return {items:[],has_more:false};
      return {items:[patientDTO],has_more:options.offset===0};
    }});
  h.click('[data-patient-action=next]');await flush();assert.equal(requests.at(-1).options.offset,25);
  h.fill('[name=query]','Anterior');h.submit('[data-patient-search]');await flush();
  h.fill('[name=query]','Atual');h.submit('[data-patient-search]');await flush();
  finishOld();await flush();
  assert.match(h.host.textContent,/Nenhum paciente corresponde à busca/);
  assert.doesNotMatch(h.host.textContent,/Paciente de teste/);
  assert.ok(requests.slice(1).every(x=>x.method==='POST'&&x.path.endsWith('/patients/search')));
});

test('um dentista abre os dados cadastrais antes de decidir entrar no prontuário',async t=>{
  const h=makeDOM();t.after(()=>h.close());
  const paths=[];let selected;
  await h.window.OrionDentalDirectory.mount({host:h.host,tenantId:tenant,canAccess:()=>true,canWrite:()=>true,
    openRecords:id=>selected=id,request:async path=>{
      paths.push(path);if(path.endsWith('/access'))return {clinical_access:{level:'dentist'},can_read_records:true};
      if(path.endsWith('/search'))return {items:[patientDTO],has_more:false};
      return patientDTO;
    }});
  h.click('[data-patient-id]');await flush();
  h.click('[data-patient-action=records]');
  assert.equal(selected,patient);
  assert.ok(paths.every(path=>!path.includes('/records')&&!path.includes('/exams')));
});
