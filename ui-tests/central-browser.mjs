/* Browser-only qualification. Every API is synthetic; all network is aborted. */
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const {chromium}=await import(pathToFileURL(process.env.ORION_PLAYWRIGHT_MODULE).href);
const output=process.env.ORION_INBOX_BROWSER_OUTPUT;await mkdir(output,{recursive:true});
const channel='20000000-0000-4000-8000-000000000001',account='30000000-0000-4000-8000-000000000001',phone='+5567999999901';
const css=(await readFile(path.join(root,'app/static/orion-suite.css'),'utf8'))+'\n'+await readFile(path.join(root,'app/static/orion-inbox.css'),'utf8');
const browser=await chromium.launch({headless:true,executablePath:process.env.ORION_CHROMIUM_EXECUTABLE}),results=[];
try{for(const width of [360,768,1440])for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width,height:900}}),calls=[],errors=[];let lost=true,status='received',body='Mensagem de cliente sintético com nome comprido. '+('texto '.repeat(45));
  page.on('pageerror',e=>errors.push(e.message));await page.route('**/*',r=>r.request().url()==='https://orion.test/'?r.fulfill({status:200,contentType:'text/html',body:'<html></html>'}):r.abort());await page.goto('https://orion.test/');
  await page.exposeFunction('request',async(url,method='GET',data)=>{calls.push({url,method,data});
    if(url.endsWith('/workflows'))return {items:[{id:account,name:'Fluxo sintético de retorno para revisão humana',version:1,steps:[{kind:'handoff',label:'Atendimento da equipe'},{kind:'reminder_draft',label:'Preparar revisão de agendamento'}]}]};
    if(url.endsWith('/simulate'))return {trace:[{description:'Solicitaria atendimento humano: Atendimento da equipe'},{description:'Prepararia um lembrete para revisão: Preparar revisão de agendamento'}]};
    if(url.endsWith('/channels'))return {items:[{id:channel,provider:'qr',label:'QR de atendimento sintético',enabled:true},{id:account,provider:'meta',label:'Meta sintética',enabled:true}],account_id:account,send_enabled:true};
    if(url.includes('/conversations?'))return {items:[{channel_id:channel,provider:'qr',contact_e164:phone,name:'Cliente sintético com nome longo para testar a tela pequena',mode:'human',preview:body}],has_more:false};
    if(method==='POST'){if(lost){lost=false;throw Error('Resposta sintética perdida; solicitação preservada');}status='queued';body=data.text;return {status:'queued'};}
    if(url.includes('/messages?'))return {control:{mode:'human',assigned_account_id:account,revision:2,pending_operation:null},items:[{id:account,direction:status==='received'?'in':'out',source:status==='received'?'customer':'manual',kind:'text',body,status,created_at:'2026-10-20T12:00:00Z'}],has_more:false};
    throw Error('Unexpected synthetic request '+url);
  });
  await page.setContent('<html data-theme="'+theme+'"><body><main id="page-suite" class="orion-suite-enabled" style="padding:20px"><h1>Central de atendimento</h1><div id="host" class="os-inbox"></div></main></body></html>');
  await page.addStyleTag({content:css+'\nbody{margin:0;background:var(--os-bg)}#page-suite{min-height:100vh;color:var(--os-text);background:var(--os-bg);font-family:var(--os-font)}'});
  await page.addScriptTag({path:path.join(root,'app/static/orion-conversations.js')});
  await page.evaluate(()=>window.OrionConversations.mount({host:document.querySelector('#host'),request:window.request,tenantId:'synthetic-tenant',canWrite:true}));
  await page.locator('[data-central-phone]').click();await page.locator('textarea').waitFor();
  await page.locator('textarea').fill('Texto sintético revisado');await page.locator('[data-central-compose] button').click();
  await page.getByText('Resposta sintética perdida; solicitação preservada').waitFor();assert.equal(await page.locator('textarea').inputValue(),'Texto sintético revisado');
  await page.locator('[data-central-compose] button').click();await page.getByText('Na fila',{exact:true}).waitFor();
  const sends=calls.filter(x=>x.method==='POST');assert.equal(sends.length,2);assert.equal(sends[0].data.operation_id,sends[1].data.operation_id);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`overflow ${width} ${theme}`);
  await page.screenshot({path:path.join(output,`central-${width}-${theme}.png`),fullPage:true});assert.deepEqual(errors,[]);
  await page.addScriptTag({path:path.join(root,'app/static/orion-flows.js')});
  await page.evaluate(()=>{window.OrionConversations.unmount();return window.OrionFlows.mount({host:document.querySelector('#host'),request:window.request,tenantId:'synthetic-tenant',canWrite:true});});
  await page.locator('[data-flow-action=select]').click();await page.locator('[data-flow-action=simulate]').click();await page.getByText('Nenhuma ação foi executada.').waitFor();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:path.join(output,`flows-${width}-${theme}.png`),fullPage:true});assert.deepEqual(errors,[]);
  results.push({width,theme,overflow:false,uncertain_request_preserved:true,same_operation_retry:true,real_sends:0});await page.close();
}await writeFile(path.join(output,'results.json'),JSON.stringify({results,external_requests:0,real_sends:0},null,2));process.stdout.write(JSON.stringify({cases:results.length,real_sends:0,output})+'\n');}finally{await browser.close();}
