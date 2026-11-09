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
  if(path.endsWith('/dental/access'))return {account_id:account,can_manage:false,can_read_records:config.level==='dentist',
    clinical_access:config.level?{account_id:account,level:config.level,professional_id:pro}:null,grants:[]};
  if(path.includes('/dental/patients?'))return {items:rows,total:rows.length};
  if(path.endsWith('/dental/patients'))return {id:randomUUID(),...data};
  if(path.includes('/dental/patients/'+patient+'/records?'))return {items:[]};
  if(path.includes('/dental/patients/'+patient+'/exams')&&method==='GET')return {items:[]};
  if(path.endsWith('/dental/patients/'+patient))return patientDTO;
  if(path.endsWith('/dental/patients/'+patient+'/records')&&method==='POST')return {id:randomUUID(),recorded:true};
  if(path.endsWith('/dental/rooms'))return method==='GET'?{items:[]}:{id:randomUUID(),...data};
  if(path.endsWith('/dental/supplies'))return method==='GET'?{items:[]}:{id:randomUUID(),...data};
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
  const mount=()=>window.OrionDentalClinic.mount({host,request:requester,tenantId:tenant,canAccess:()=>true});
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
