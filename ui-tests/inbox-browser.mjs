/* Synthetic browser qualification; no backend, credentials, sockets or sends.
 * Set ORION_PLAYWRIGHT_MODULE to an installed playwright/index.mjs path.
 */
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const {chromium}=await import(process.env.ORION_PLAYWRIGHT_MODULE?pathToFileURL(process.env.ORION_PLAYWRIGHT_MODULE).href:'playwright');
const output=process.env.ORION_INBOX_BROWSER_OUTPUT||path.join(root,'.inbox-browser');await mkdir(output,{recursive:true});
const customer='10000000-0000-4000-8000-000000000001',reply='30000000-0000-4000-8000-000000000001';
const labels=Array.from({length:18},(_,i)=>({id:`20000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`,name:'Etiqueta de acompanhamento '+(i+1),color:'#18836b',version:1}));
const replies=[{id:reply,title:'Confirmação e preparo para o atendimento',category:'Agendamento',text:'Olá {{cliente.nome}}. Seu protocolo é {{agendamento.protocolo}}.',visibility:'company',version:1}];
const calls=[];
async function request(url,method='GET',body){
  calls.push({url,method});if(method!=='GET'){
    if(url.endsWith('/preview'))return {text:'Olá Cliente sintético. Seu protocolo é OR-SYNTHETIC.',sent:false};
    throw Error('Falha sintética de salvamento');
  }
  if(url.endsWith('/capabilities'))return {enabled:true};
  if(url.endsWith('/labels'))return {items:labels};if(url.endsWith('/quick-replies'))return {items:replies};
  if(url.includes('/customers?'))return {items:Array.from({length:8},(_,i)=>({id:i===0?customer:`10000000-0000-4000-8000-${String(i+1).padStart(12,'0')}`,name:'Cliente com nome comprido para validar a leitura em telas pequenas '+(i+1),phone:'+5567999999901'})),has_more:false};
  if(url.endsWith('/customers/'+customer))return {customer:{id:customer,name:'Cliente sintético com nome extenso para validar quebra de linha',whatsapp_e164:'+5567999999901',notes:'Observações sintéticas autorizadas. <script>Este texto deve permanecer texto.</script>'},timezone:'America/Cuiaba',labels:[],history:[],history_limit:20,upcoming:null};
  throw Error('Unexpected request '+url);
}
const css=await readFile(path.join(root,'app/static/orion-suite.css'),'utf8')+'\n'+await readFile(path.join(root,'app/static/orion-inbox.css'),'utf8');
const fonts=(await Promise.all([['Figtree','figtree-latin.woff2'],['Outfit','outfit-latin.woff2']].map(async([name,file])=>
  '@font-face{font-family:"'+name+'";src:url(data:font/woff2;base64,'+(await readFile(path.join(root,'app/static',file))).toString('base64')+') format("woff2");font-weight:100 900}'))).join('\n');
const browser=await chromium.launch({headless:true,...(process.env.ORION_CHROMIUM_EXECUTABLE?{executablePath:process.env.ORION_CHROMIUM_EXECUTABLE}:{})});const results=[];
try{
  for(const width of [360,768,1440])for(const theme of ['light','dark']){
    const page=await browser.newPage({viewport:{width,height:900},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>route.abort());await page.exposeFunction('inboxRequest',request);
    await page.setContent('<html data-theme="'+theme+'"><body><div id="appView" class="orion-suite-enabled"><main id="page-suite" style="padding:20px"><h1>Ferramentas de atendimento</h1><div id="host"></div></main></div></body></html>');
    await page.addStyleTag({content:fonts+css+'\nbody{margin:0}.orion-suite-enabled{min-height:100vh;background:var(--os-bg);color:var(--os-text);font-family:var(--os-font)}'});
    await page.addScriptTag({path:path.join(root,'app/static/orion-inbox.js')});
    await page.evaluate(()=>window.OrionInbox.mount({host:document.querySelector('#host'),request:window.inboxRequest,tenantId:'00000000-0000-4000-8000-000000000001',canWrite:true}));
    await page.locator('[data-inbox-customer]').first().click();await page.locator('#inboxPreparedReply').waitFor();
    await page.locator('#inboxPreparedReply').selectOption(reply);await page.locator('[data-inbox-action=preview]').click();
    await page.waitForFunction(()=>document.querySelector('#inboxPreparedText')?.value.includes('OR-SYNTHETIC'));
    assert.equal(await page.locator('script').count(),1);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`horizontal overflow ${width} ${theme}`);
    await page.screenshot({path:path.join(output,`crm-${width}-${theme}.png`),fullPage:true});
    await page.locator('[data-inbox-tab=replies]').click();await page.locator('[data-inbox-action=new-reply]').click();
    await page.locator('#inboxReplyTitle').fill('Rascunho sintético preservado');await page.locator('#inboxReplyText').fill('Mensagem sintética');
    await page.locator('[data-inbox-form=reply] button[type=submit], [data-inbox-form=reply] button:not([type])').click();
    await page.getByText('Falha sintética de salvamento').waitFor();
    assert.equal(await page.locator('#inboxReplyTitle').inputValue(),'Rascunho sintético preservado');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.keyboard.press('Tab');assert.notEqual(await page.evaluate(()=>document.activeElement?.tagName),'BODY');
    await page.screenshot({path:path.join(output,`respostas-${width}-${theme}.png`),fullPage:true});
    assert.deepEqual(errors,[]);results.push({width,theme,overflow:false,draft_preserved:true,preview_sent:false,page_errors:0});
    await page.close();
  }
  assert.equal(calls.some(call=>/\/send/.test(call.url)),false);
  await writeFile(path.join(output,'results.json'),JSON.stringify({mode:'synthetic_browser',results,external_requests:0,send_requests:0},null,2));
  process.stdout.write(JSON.stringify({cases:results.length,output,send_requests:0})+'\n');
}finally{await browser.close();}
