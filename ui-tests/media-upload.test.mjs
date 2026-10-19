import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {makeSuite,flush,defaultResponse,tenantA,tenantB} from './harness.mjs';

const asset='/media/'+tenantA+'/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa.webp';
const another='/media/'+tenantA+'/bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb.webp';
const sample={id:'20000000-0000-4000-8000-000000000003',name:'Barba',price_cents:3500,duration_min:30,sort_order:0,color:'#2e7bf6',active:true,image_url:'https://example.invalid/photo.png'};
const site={title:'Studio',slug:'studio',version:2,published:false,logo_url:'https://example.invalid/logo.png',cover_url:null,show_prices:true};
function responses(path){
  if(path.endsWith('/services'))return [sample];
  if(path.endsWith('/professional_services')||path.endsWith('/professionals'))return [];
  if(path.includes('/management/service-packages'))return {items:[]};
  if(path.endsWith('/management/site'))return site;
  if(path.endsWith('/management/site/metrics?days=30'))return {daily:[],visits:0};
  return defaultResponse(path);
}
async function fixture(t,route='servicos',role='owner',bridge={}){
 const h=await makeSuite({initialRoute:route,role,respond:responses,bridge});
 t.after(()=>h.close());await flush();return h;
}
function selectFile(h,input,bytes=128,mime='image/png'){
 const file=new h.window.File([new Uint8Array(bytes)],'foto.png',{type:mime});
 Object.defineProperty(input,'files',{value:[file],configurable:true});
 input.dispatchEvent(new h.window.Event('change',{bubbles:true}));
 return file;
}

test('serviço oferece seleção de foto, prévia de link legado e envia o arquivo no escopo da empresa',async t=>{
 const events=[];
 const h=await fixture(t,'servicos','owner',{uploadImage:async (path,file,kind)=>{
    events.push({path,kind,size:file.size});return {url:asset,content_type:'image/webp'};
 }});
 h.click('[data-os-service="'+sample.id+'"]');await flush();
 const input=h.document.querySelector('.os-image-upload[data-os-image-kind=service] input[type=file]');
 const box=input.closest('.os-image-upload');
 assert.ok(input);
 assert.equal(input.accept,'image/jpeg,image/png,image/webp,image/heic,image/heif');
 assert.ok(box.querySelector('.os-image-thumb img[src="https://example.invalid/photo.png"]'),'imagem antiga preservada');
 assert.equal(box.querySelector('input[name=image_url]').value,'https://example.invalid/photo.png');
 assert.match(box.textContent,/Escolher imagem/);
 selectFile(h,input);await flush();
 assert.deepEqual(events,[{path:'/api/v1/tenants/'+tenantA+'/media',kind:'service',size:128}]);
 assert.equal(box.querySelector('input[name=image_url]').value,asset);
 assert.equal(box.querySelector('.os-image-thumb img').getAttribute('src'),asset);
 assert.match(box.querySelector('[role=status]').textContent,/Imagem enviada/);
 assert.equal(box.querySelector('[data-os-action=image-pick]').disabled,false);
 assert.equal(box.querySelector('[data-os-action=image-remove]').disabled,false);
 h.click('.os-image-upload [data-os-action=image-remove]');
 assert.equal(box.querySelector('input[name=image_url]').value,'');
 assert.equal(box.querySelector('.os-image-thumb img'),null);
 assert.equal(h.errors.length,0);
});

test('cliente rejeita formato inválido e arquivo acima de 8MB antes da rede',async t=>{
 let attempts=0;
 const h=await fixture(t,'servicos','owner',{uploadImage:async()=>{attempts++;return {url:asset}}});
 h.click('[data-os-action=new-service]');await flush();
 const input=h.document.querySelector('.os-image-file');
 selectFile(h,input,8*1024*1024+1);await flush();
 assert.match(h.document.querySelector('.os-image-status').textContent,/até 8 MB/);
 selectFile(h,input,100,'image/svg+xml');await flush();
 assert.match(h.document.querySelector('.os-image-status').textContent,/JPG/);
 assert.equal(attempts,0);
 assert.equal(h.errors.length,0);
});

test('site usa mesma seleção no logo e na capa, preservando rascunho e upload sem auto-publicar',async t=>{
 const calls=[];
 const h=await fixture(t,'site','owner',{uploadImage:async(path,file,kind)=>{
   calls.push({path,kind});return {url:kind==='logo'?asset:another};
 }});
 h.click('[data-os-site-tab=settings]');await flush();
 const logo=h.document.querySelector('[data-os-image-kind=logo]'),
       cover=h.document.querySelector('[data-os-image-kind=cover]');
 assert.ok(logo&&cover);
 assert.equal(logo.querySelector('input[name=logo_url]').value,site.logo_url);
 assert.equal(cover.querySelector('input[name=cover_url]').value,'');
 selectFile(h,cover.querySelector('.os-image-file'));await flush();
 assert.deepEqual(calls,[{path:'/api/v1/tenants/'+tenantA+'/media',kind:'cover'}]);
 assert.equal(cover.querySelector('input[name=cover_url]').value,another);
 assert.equal(h.document.querySelector('#osSiteForm').dataset.dirty,'true');
 assert.equal(h.calls.some(x=>x.method!=='GET'),false,'não publica o site nem salva automaticamente');
 assert.equal(h.errors.length,0);
});

test('leitor não pode usar o seletor nem retirar fotos',async t=>{
 let calls=0;const h=await fixture(t,'site','viewer',{uploadImage:async()=>{calls++;return {url:asset}}});
 h.click('[data-os-site-tab=settings]');await flush();
 const box=h.document.querySelector('[data-os-image-kind=logo]');
 assert.equal(box.querySelector('.os-image-file').matches(':disabled'),true,'fieldset bloqueia campo de arquivo');
 assert.equal(box.querySelector('[data-os-action=image-pick]').matches(':disabled'),true,'botão não abre picker');
 assert.equal(box.querySelector('[data-os-action=image-remove]').matches(':disabled'),true,'botão não remove imagem');
 assert.equal(calls,0);
 assert.equal(h.errors.length,0);
});

test('resposta inválida ou de outra empresa não altera o cadastro',async t=>{
 const h=await fixture(t,'servicos','owner',{uploadImage:async()=>({url:'/media/'+tenantB+'/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa.webp'})});
 h.click('[data-os-service="'+sample.id+'"]');await flush();
 const box=h.document.querySelector('.os-image-upload');
 selectFile(h,box.querySelector('.os-image-file'));await flush();
 assert.equal(box.querySelector('input[name=image_url]').value,sample.image_url);
 assert.match(box.querySelector('.os-image-status').textContent,/endereço de imagem inválido/);
 assert.equal(h.errors.length,0);
});

test('upload pendente impede salvar um formulário antes de obter a URL final',async t=>{
 let done;
 const pending=new Promise(resolve=>{done=resolve});
 const h=await fixture(t,'servicos','owner',{uploadImage:()=>pending});
 h.click('[data-os-service="'+sample.id+'"]');await flush();
 const box=h.document.querySelector('.os-image-upload');
 selectFile(h,box.querySelector('.os-image-file'));await flush();
 assert.equal(box.dataset.osUploading,'true');
 assert.equal(box.querySelector('[data-os-action=image-pick]').disabled,true);
 const form=h.document.querySelector('[data-os-form=service]');
 h.submit('[data-os-form=service]');await flush();
 assert.match(form.querySelector('.os-form-message').textContent,/Aguarde o envio/);
 assert.equal(h.calls.some(x=>x.method!=='GET'),false);
 done({url:asset});await flush();
 assert.equal(box.dataset.osUploading,undefined);
 assert.equal(form.querySelector('button[type=submit]').disabled,false);
});

test('CSS dos uploads mantém upload responsivo, preview e hierarquia sem URLs editáveis',async ()=>{
 const css=await readFile(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
 const script=await readFile(new URL('../app/static/orion-suite.js',import.meta.url),'utf8');
 assert.match(css,/\.os-image-upload-row\{display:flex;align-items:center;gap:14px/);
 assert.match(css,/\.os-image-thumb img\{display:block;width:100%;height:100%;object-fit:cover\}/);
 assert.match(css,/\.os-image-thumb\.os-image-logo img\{object-fit:contain\}/);
 assert.match(css,/@media\(max-width:390px\)/);
 for(const name of ['service','package','professional','logo','cover'])assert.ok(script.includes("'"+name+"'"),'tipo de imagem '+name);
 assert.doesNotMatch(script,/URL da imagem \(opcional\)|URL da foto \(opcional\)/);
});
