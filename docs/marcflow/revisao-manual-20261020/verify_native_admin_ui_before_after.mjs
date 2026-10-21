import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import {createHash,randomUUID} from 'node:crypto';
import {JSDOM} from 'file:///C:/Users/kayky/Documents/Playground/orion-marcflow-20261020/ui-tests/node_modules/jsdom/lib/api.js';
const root='C:/Users/kayky/Documents/Playground/orion-marcflow-20261020';
const out='C:/Users/kayky/Documents/Playground/orion-inventario-revisao-20261020';
const commit='8369e2e4d53379e7353ac69af3d40b70fc2b926e';
const path='app/static/orion-conversations.js';
const before=execFileSync('git',['show',commit+':'+path],{cwd:root,encoding:'utf8'});
const after=await readFile(root+'/'+path,'utf8');
async function inspect(source){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://synthetic.invalid',runScripts:'outside-only'});
  const {window}=dom;Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  const host=window.document.querySelector('#host'),calls=[];
  window.eval(source);
  async function request(path,method='GET'){
    calls.push(method);assert.equal(method,'GET','This proof never submits an operation');
    if(path.endsWith('/channels'))return {items:[{id:'synthetic-channel',provider:'qr',label:'Synthetic QR',enabled:true}],account_id:null,send_enabled:true};
    if(path.endsWith('/quick-replies'))return {items:[]};
    if(path.includes('/conversations?'))return {items:[{channel_id:'synthetic-channel',contact_e164:'+5567999999901',mode:'human',revision:2,provider:'qr'}],has_more:false};
    if(path.includes('/messages?'))return {control:{mode:'human',revision:2,assigned_account_id:null,pending_operation:null},items:[],has_more:false};
    throw Error('Unexpected synthetic route');
  }
  try{
    await window.OrionConversations.mount({host,request,tenantId:'synthetic-tenant',canWrite:true,toolsEnabled:false});
    host.querySelector('[data-central-phone]').click();
    await new Promise(resolve=>setTimeout(resolve,20));
    return {source_sha256:createHash('sha256').update(source).digest('hex'),claims_with_you:host.textContent.includes('com você'),
      send_button_enabled:!host.querySelector('[data-central-compose] button').disabled,
      claim_button_enabled:!host.querySelector('[data-central-action=mode-human]').disabled,
      writes_requested:calls.filter(method=>method!=='GET').length};
  }finally{window.OrionConversations.unmount();window.close();}
}
const result={scope:'Offline DOM; synthetic account=NULL and assigned=NULL; no API/provider connection or send attempted',before_commit:commit,before:await inspect(before),after:await inspect(after)};
assert.equal(result.before.claims_with_you,true);assert.equal(result.before.send_button_enabled,true);
assert.equal(result.after.claims_with_you,false);assert.equal(result.after.send_button_enabled,false);
assert.equal(result.after.claim_button_enabled,false);
await writeFile(out+'/native-admin-ui-before-after.json',JSON.stringify(result,null,2),'utf8');
process.stdout.write(JSON.stringify(result)+'\n');
