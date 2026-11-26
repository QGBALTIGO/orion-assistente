import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {flush} from './harness.mjs';

const source=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
const ID='a'.repeat(32),other='b'.repeat(32);
async function setup(t,supported=true){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/panel',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,host=window.document.getElementById('host'),calls=[],notices=[];
  window.HTMLElement.prototype.scrollIntoView=()=>{};window.scrollTo=()=>{};
  window.CSS={escape:String};Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.eval(source.replace('export function mount(','function mountEditor(')+'\nwindow.mountEditor=mountEditor;');
  const state={company:'Minha marca',image_templates_v1:supported,deal_template:ID,template:[
    {id:'default',name:'Modelo simples',brand:'Padrão',layout:'simple',media:'card',text:'{produto}',style:'plain'},
    {id:ID,name:'Meu modelo automático',brand:'PromoTech',layout:'discount',media:'card',text:'{produto}\n{link}',style:'plain'}
  ],connection:[],destination:[],offer:[],rule:[],decision:[],jobs:[],product_flag:[],niches:[],sorts:{}};
  const root='/api/v1/afiliados/tenant/api/';
  let mounted=window.mountEditor({host,tenantId:'tenant',view:'templates',icon:()=>'',open(){},showNotice:(text,error)=>notices.push({text,error}),request:async(path,method,payload)=>{
    const action=path.slice(root.length);calls.push({action,method,payload});
    if(action==='state')return JSON.parse(JSON.stringify(state));
    if(action==='templates/preview-image'){assert.ok(supported);return {image:'data:image/png;base64,AAAA'};}
    if(action.endsWith('/default')){state.deal_template=action.split('/')[1];return {ok:true,default:state.deal_template};}
    if(action.startsWith('templates/')&&method==='PUT'){
      const id=action.split('/')[1],row={id,...payload};
      state.template=state.template.filter(t=>t.id!==id).concat(row);return row;
    }
    throw Error('Unexpected API '+action);
  }});
  t.after(()=>{mounted.unmount();window.close();});
  await flush();await new Promise(r=>setTimeout(r,20));
  const $=s=>host.querySelector(s);
  return {host,window,$,calls,notices,state,click:s=>$(s).click(),
    fill(s,value){const el=$(s);el.value=value;el.dispatchEvent(new window.Event('input',{bubbles:true}));},
    change(s,value){const el=$(s);el.value=value;el.dispatchEvent(new window.Event('change',{bubbles:true}));},
    submit(){ $('[data-form=template]').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));}};
}
test('opens the actual default model, separates image and caption, previews and saves style',async t=>{
  const h=await setup(t);
  assert.equal(h.$('[name=name]').value,'Meu modelo automático');
  assert.equal(h.$('[name=brand]').value,'PromoTech');
  h.fill('[name=brand]','Minha marca');
  h.fill('[name=visual_handle]','@minhasofertas');
  h.fill('[name=visual_cta]','Aproveite agora');
  h.change('[name=visual_theme]','dark');
  h.click('[name=custom_colors]');
  h.fill('[name=visual_accent]','#aa3311');
  h.click('[data-template-refresh]');
  await new Promise(r=>setTimeout(r,30));
  const p=h.calls.filter(c=>c.action==='templates/preview-image').at(-1).payload;
  assert.equal(p.brand,'Minha marca');
  assert.equal(p.image_style.accent,'#aa3311');
  assert.equal(p.image_style.theme,'dark');
  assert.equal(h.$('[data-template-preview-image]').hidden,false);
  h.click('[data-template-tab=caption]');
  assert.equal(h.$('[data-template-panel=image]').hidden,true);
  h.fill('[name=text]','{produto}\n{preco}\n{link}');
  h.click('[data-template-tab=image]');
  assert.equal(h.$('[name=brand]').value,'Minha marca');
  h.submit();await flush();
  const saved=h.calls.find(c=>c.action==='templates/'+ID&&c.method==='PUT');
  assert.equal(saved.payload.brand,'Minha marca');
  assert.equal(saved.payload.image_style.cta,'Aproveite agora');
  assert.equal(saved.payload.text,'{produto}\n{preco}\n{link}');
});
test('can duplicate a template and select it as the default without replacing existing records',async t=>{
  const h=await setup(t);
  h.click('[data-act=duplicate-template][data-value="'+ID+'"]');
  assert.match(h.$('[name=name]').value,/cópia/);
  h.fill('[name=name]','Minha cópia');
  h.submit();await flush();
  const saved=h.calls.find(c=>c.method==='PUT');
  assert.ok(saved&&saved.action!=='templates/'+ID);
  const id=saved.action.split('/')[1];
  h.click('[data-act=default-template][data-value="'+id+'"]');await flush();
  assert.equal(h.state.deal_template,id);
  assert.ok(h.state.template.some(t=>t.id===ID));
});
test('legacy engine still permits editing the real selected brand, without unsupported writes',async t=>{
  const h=await setup(t,false);
  assert.equal(h.$('[name=name]').value,'Meu modelo automático');
  assert.equal(h.$('[name=visual_theme]'),null);
  h.fill('[name=brand]','Marca corrigida');h.submit();await flush();
  const saved=h.calls.find(c=>c.method==='PUT');
  assert.equal(saved.action,'templates/'+ID);
  assert.equal(saved.payload.brand,'Marca corrigida');
  assert.equal('image_style' in saved.payload,false);
  assert.equal(h.calls.some(c=>c.action==='templates/preview-image'),false);
});
