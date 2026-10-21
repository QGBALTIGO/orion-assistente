import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
const require=createRequire('C:/Users/kayky/Documents/Playground/orion-marcflow-20261020/ui-tests/package.json');
const {JSDOM,VirtualConsole}=require('jsdom');
const base=new URL('./',import.meta.url),errors=[],networkAttempts=[];
const tick=async()=>{for(let i=0;i<3;i++)await new Promise(resolve=>setTimeout(resolve,0));};
function dom(html){const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(String(error)));
 const value=new JSDOM(html,{url:'file:///C:/Users/kayky/Documents/Playground/orion-inventario-revisao-20261020/relatorio.html',
  runScripts:'dangerously',virtualConsole:vc,beforeParse(window){
   window.HTMLElement.prototype.scrollIntoView=()=>{};
   window.fetch=(...args)=>{networkAttempts.push(args);throw Error('Network forbidden in offline report verification');};
   window.XMLHttpRequest.prototype.open=function(...args){networkAttempts.push(args);throw Error('XHR forbidden');};
   window.WebSocket=class{constructor(...args){networkAttempts.push(args);throw Error('WebSocket forbidden');}};
 }});return value;}
function change(window,id,value){const el=window.document.getElementById(id);el.value=value;el.dispatchEvent(new window.Event(id==='search'?'input':'change',{bubbles:true}));}
function noMarkup(document){assert.equal(document.querySelectorAll('script').length,2);assert.equal(document.querySelectorAll('img,iframe,object,embed,svg,[data-injected]').length,0);}

const html=await readFile(new URL('relatorio.html',base),'utf8'),compactText=await readFile(new URL('manual-inventory-compact.json',base),'utf8'),compact=JSON.parse(compactText);
const consolidated=JSON.parse(await readFile(new URL('manual-inventory.json',base),'utf8'));
const expectedFindings=consolidated.findings.length,expectedObservations=consolidated.findings.reduce((n,item)=>n+item.observations.length,0);
assert.equal(consolidated.summary.findings_unique,expectedFindings);
assert.equal(consolidated.summary.findings_raw_observations,expectedObservations);
assert.equal(new Set(consolidated.findings.map(item=>item.id)).size,expectedFindings);
assert.equal(compact.findings.length,expectedFindings);
const expectedSourceFiles=new Set([...Object.keys(compact.source_files),...Object.keys(compact.dependency_files||{})]).size;
const actual=dom(html);const {window}=actual,document=window.document;await tick();
assert.equal(window.__ORION_REVIEW_STATE__.total,1247);assert.equal(window.__ORION_REVIEW_STATE__.findings,expectedFindings);
assert.equal(document.querySelectorAll('#findings-list .finding').length,expectedFindings);
assert.equal(document.querySelectorAll('.facts .fact strong')[1].textContent,String(expectedFindings));
assert.equal(document.querySelectorAll('.facts .fact strong')[2].textContent,String(expectedSourceFiles));
assert.equal(document.querySelectorAll('[data-inventory-id]').length,25);assert.match(document.getElementById('results').textContent,/1\.247 de 1\.247/);
const embedded=JSON.parse(document.getElementById('review-data').textContent);
if(!embedded.receipt?.data.ci)assert.match(document.getElementById('ci-receipt').textContent,/Pendente/);
else assert.match(document.getElementById('ci-receipt').textContent,/Informado pelo recibo.*não consultou o provedor/);
if(!embedded.receipt?.data.deployments?.length)assert.match(document.getElementById('deployments-receipt').textContent,/Pendente/);
else assert.match(document.getElementById('deployments-receipt').textContent,/Informado pelo recibo.*não consultou o provedor/);
if(embedded.receipt?.data.validation){assert.equal(document.getElementById('validation-updates').hidden,false);assert.match(document.getElementById('validation-updates').textContent,/gerador não executa testes/);}
document.getElementById('next').click();assert.equal(document.querySelector('[data-inventory-id]').id,'INV0026');
change(window,'pageSize','1247');assert.equal(document.querySelectorAll('[data-inventory-id]').length,1247);
assert.deepEqual([...document.querySelectorAll('[data-inventory-id]')].map(el=>el.id),compact.records.map(r=>r.id));
for(const el of document.querySelectorAll('[data-inventory-id]'))el.open=true;await tick();
assert.equal(document.querySelectorAll('.inventory-item .item-body').length,1247);
for(const r of compact.records){const entry=document.getElementById(r.id);assert.ok(entry.textContent.includes(r.rationale),r.id+' rationale missing');assert.ok(entry.textContent.includes(r.actual_sink),r.id+' sink missing');}
noMarkup(document);
change(window,'search','INV1247');assert.equal(window.__ORION_REVIEW_STATE__.filtered,1);assert.equal(document.querySelector('[data-inventory-id]').id,'INV1247');
document.getElementById('clear').click();assert.equal(window.__ORION_REVIEW_STATE__.filtered,1247);
change(window,'file','app/static/qr_connect.html');assert.equal(window.__ORION_REVIEW_STATE__.filtered,9);
document.getElementById('clear').click();change(window,'kind','javascript_candidate');assert.equal(window.__ORION_REVIEW_STATE__.filtered,396);
document.getElementById('clear').click();change(window,'search','nao-existe-probe-809377');assert.equal(window.__ORION_REVIEW_STATE__.filtered,0);assert.ok(document.querySelector('.empty'));
document.getElementById('clear').click();
const buttons=[...document.querySelectorAll('#findings-list .inline-id')];assert.ok(buttons.length>0);buttons[0].click();await tick();assert.equal(window.__ORION_REVIEW_STATE__.filtered,1);assert.equal(document.querySelector('[data-inventory-id]').open,true);
actual.window.close();

const malicious=dom(await readFile(new URL('review-security-fixture.html',base),'utf8'));await tick();
assert.equal(malicious.window.__REPORT_INJECTED__,undefined);noMarkup(malicious.window.document);
change(malicious.window,'search','probe_markup_8473');assert.equal(malicious.window.__ORION_REVIEW_STATE__.filtered,2);
for(const el of malicious.window.document.querySelectorAll('[data-inventory-id]'))el.open=true;await tick();
assert.equal(malicious.window.document.querySelectorAll('.inventory-item .item-body').length,2);
assert.ok(malicious.window.document.querySelector('.inventory-item .item-body').textContent.includes('</script>'));
assert.ok(malicious.window.document.querySelector('.inventory-item .item-body').textContent.includes('__APP__'));
assert.equal(malicious.window.__REPORT_INJECTED__,undefined);noMarkup(malicious.window.document);
assert.ok(malicious.window.document.querySelector('#findings-list').textContent.includes('probe_finding_markup'));
malicious.window.close();
assert.equal(errors.length,0,JSON.stringify(errors));assert.equal(networkAttempts.length,0);
const result={schema_version:1,scope:'offline HTMLParser plus JSDOM, real report and malicious-source fixture',
 report_sha256:createHash('sha256').update(html,'utf8').digest('hex'),
 compact_sha256:createHash('sha256').update(compactText,'utf8').digest('hex'),
 source_records:1247,rendered_records:1247,expanded_records:1247,findings:expectedFindings,observations:expectedObservations,source_and_dependency_files:expectedSourceFiles,search_by_id:true,search_file:true,
 search_kind:true,empty_search:true,pagination:true,finding_links:true,source_conclusions_present:true,
 injected_markup_nodes:0,injected_script_execution:false,network_attempts:0,jsdom_errors:[],
 receipt_status:embedded.receipt?'operator-supplied receipt rendered with explicit provenance; no provider lookup':'pending, no completion receipt supplied',tests_are_artifact_checks_not_application_acceptance:true};
await writeFile(new URL('review-report-validation.json',base),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
