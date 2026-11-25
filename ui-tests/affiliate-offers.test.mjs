import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from 'jsdom';
import {deferred, flush, tenantA} from './harness.mjs';

const script=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
const apiRoot='/api/v1/afiliados/'+tenantA+'/api/';
const OFFER='a'.repeat(32);
const ALTERNATE='b'.repeat(32);
const IDS={telegram:'c'.repeat(32),group:'d'.repeat(32),channel:'e'.repeat(32),blocked:'f'.repeat(32)};
const clone=value=>JSON.parse(JSON.stringify(value));
const defaultPhrase='Um toque de praticidade para a sua rotina.';
const choices=[
  {id:'one',text:defaultPhrase,source:'fallback'},
  {id:'two',text:'Conheça os detalhes e escolha a sua versão favorita.',source:'fallback'},
  {id:'three',text:'Uma opção para deixar o seu dia mais simples.',source:'fallback'},
  {id:'four',text:'Explore as possibilidades deste achado.',source:'fallback'},
  {id:'five',text:'Veja se esta novidade combina com você.',source:'fallback'},
];

function offer(overrides={}) {
  return {id:OFFER,title:'Fone sem fio com conexão estável e estojo de recarga',
    retailer:'shopee',retailer_name:'Shopee',price:'149.90',image:'https://orion.test/synthetic.png',
    affiliate_url:'https://s.shopee.com.br/synthetic-account-link',
    affiliate_status:'generated',link_status:'generated',phrase:defaultPhrase,
    phrase_source:'fallback',currency:'BRL',...overrides};
}

function emptyProductError(url) {
  return Object.assign(new Error('A loja não disponibilizou essa página.'),{status:422,detail:{
    code:'PRODUCT_EXTRACTION_EMPTY',message:'A loja não disponibilizou essa página.',source_url:url,
    manual_available:true,extraction:{status:'partial',code:'unavailable',http_status:503},
  }});
}

function workspace() {
  return {company:'Ofertas sintéticas',niches:[],offer:[],decision:[],product_flag:[],rule:[],jobs:[],
    sorts:{2:'Mais vendidos'},deal_template:'default',
    template:[
      {id:'default',name:'Oferta com card',media:'card',layout:'simple',style:'whatsapp',text:'{frase_ia}\n{produto}\n{preco}\n{link}'},
      {id:ALTERNATE,name:'Mensagem direta',media:'text',layout:'simple',style:'plain',text:'{produto}\n{link}'},
    ],
    retailers:[{id:'shopee',name:'Shopee',mode:'automatic'},{id:'amazon',name:'Amazon',mode:'own_link'},{id:'aliexpress',name:'AliExpress',mode:'own_link'}],
    connection:[{id:'shopee',status:'connected',account:'Conta sintética'}],
    destination:[
      {id:IDS.telegram,name:'Ofertas de tecnologia no Telegram',mode:'telegram',verified:true,test:false},
      {id:IDS.group,name:'Grupo dos melhores achados',mode:'whatsapp_group',verified:true},
      {id:IDS.channel,name:'Canal de ofertas da casa',mode:'whatsapp_channel',verified:true},
      {id:IDS.blocked,name:'Canal ainda não verificado',mode:'whatsapp_channel',verified:false},
    ],
  };
}

function preview(row,template='default',data=workspace()) {
  const media=template===ALTERNATE?'text':'card';
  const text=[row.phrase,row.title,row.price?'R$ '+row.price:'',row.affiliate_url].filter(Boolean).join('\n');
  const ready=!!(row.title&&row.price&&row.affiliate_url&&row.currency==='BRL');
  return {offer:clone(row),text,html:'<strong>'+row.title+'</strong>\n'+row.phrase+'\n'+(row.affiliate_url||''),
    media,template:{id:template,name:template===ALTERNATE?'Mensagem direta':'Oferta com card',media,layout:'simple'},
    suggestions:clone(choices),destinations:data.destination.map(row=>({...row,eligible:row.verified,
      reason:row.verified?null:'Verifique a administração deste canal.'})),
    send:{enabled:ready,reason:ready?null:'Adicione seu link de afiliado para enviar.',preview_token:'token-'+template+'-'+row.phrase+'-'+row.affiliate_url}};
}

async function harness(t,{prepare=()=>offer(),getPreview,send,getBatch,initialOffer}={}) {
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/panel?aba=ofertas',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,{document}=window,host=document.querySelector('#host');
  const data=workspace(),calls=[],notices=[],errors=[],copies=[],timers=new Map(),batches=new Map();
  if(initialOffer)data.offer=[clone(initialOffer)];
  let mounted,previewCount=0,sendCount=0,timerId=0;
  window.HTMLElement.prototype.scrollIntoView=function(){};
  window.scrollTo=()=>{};
  window.CSS={escape:String};
  window.setTimeout=(callback,delay)=>{const key=++timerId;timers.set(key,{callback,delay});return key;};
  window.clearTimeout=key=>timers.delete(key);
  Object.defineProperty(window.navigator,'clipboard',{value:{writeText:async text=>copies.push(text)}});
  window.addEventListener('error',event=>errors.push(event.error?.message||event.message));
  window.eval(script.replace('export function mount(','function mountOffers(')+'\nwindow.mountOffers=mountOffers;');
  mounted=window.mountOffers({
    host,tenantId:tenantA,view:'ofertas',whatsappAvailable:true,
    icon:name=>'<svg class="os-icon" data-test-icon="'+name+'" aria-hidden="true"></svg>',
    open:async route=>mounted.setView(route),
    showNotice:(message,error)=>notices.push({message,error}),
    request:async(path,method,payload)=>{
      calls.push({path,method,payload:payload===undefined?undefined:clone(payload)});
      const url=new URL(path,'https://orion.test'),relative=url.pathname.slice(apiRoot.length);
      if(relative==='state'&&method==='GET')return clone(data);
      if(relative==='whatsapp/status'&&method==='GET')return {status:'connected'};
      if(relative==='whatsapp/groups'&&method==='GET')return {groups:[{address:'120363099001@g.us',title:'Grupo da conta sintética',role:'admin'}],version:'orion:synthetic'};
      if(relative.startsWith('destinations/')&&method==='PUT') {
        const saved={id:relative.split('/')[1],...payload,verified:payload.mode==='whatsapp_group'};
        data.destination.push(saved);return clone(saved);
      }
      if(relative.endsWith('/verify-channel')&&method==='POST') {
        const saved=data.destination.find(item=>item.id===relative.split('/')[1]);saved.verified=true;return clone(saved);
      }
      if(relative.startsWith('send-batches/')&&method==='GET') {
        const batch=batches.get(relative.split('/')[1]);
        return getBatch?getBatch(clone(batch)):clone(batch);
      }
      if(relative==='offers'&&method==='POST') {
        const row=await prepare(payload);
        const index=data.offer.findIndex(old=>old.id===row.id);
        if(index<0)data.offer.unshift(clone(row));else data.offer[index]=clone(row);
        return clone(row);
      }
      if(relative==='offers/demo'&&method==='POST') {
        const row=offer({id:'demo',demo:true,affiliate_url:null});
        data.offer.unshift(row);return clone(row);
      }
      const parts=relative.split('/');
      const row=data.offer.find(item=>item.id===parts[1]);
      if(parts[0]==='offers'&&parts[2]==='preview'&&method==='GET') {
        assert.ok(row,'Preview requires a saved draft');
        const result=preview(row,url.searchParams.get('template'),data);
        return getPreview?await getPreview(result,++previewCount):result;
      }
      if(parts[0]==='offers'&&parts[2]==='phrases'&&method==='POST') {
        return {suggestions:choices.map((item,index)=>({...item,id:'new-'+index,text:index?item.text+' Confira.':item.text}))};
      }
      if(parts[0]==='offers'&&parts[2]==='phrase'&&method==='PUT') {
        row.phrase=payload.text;row.phrase_source='manual';return clone(row);
      }
      if(parts[0]==='offers'&&parts.length===2&&method==='PUT') {
        Object.assign(row,payload);
        if(Object.hasOwn(payload,'price'))row.currency='BRL';
        if(Object.hasOwn(payload,'affiliate_url'))row.affiliate_status='provided';
        return clone(row);
      }
      if(parts[0]==='offers'&&parts[2]==='send'&&method==='POST') {
        ++sendCount;
        const result=send?await send(payload,sendCount):
          {batch_id:payload.request_id,created:true,jobs:payload.destinations.map((destination,index)=>({id:'job-'+index,destination,status:'queued'}))};
        batches.set(payload.request_id,result);
        return result;
      }
      throw new Error('Unexpected synthetic request: '+method+' '+relative);
    },
  });
  t.after(()=>{
    mounted.unmount();dom.window.close();
    assert.deepEqual(errors,[],'No uncaught browser errors');
    assert.equal(timers.size,0,'Unmount cancels pending receipt checks');
  });
  await flush();
  const h={window,document,host,data,calls,notices,copies,timers,controller:mounted,
    get(selector){const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);return node;},
    click(selector){this.get(selector).click();},
    input(selector,value){const node=this.get(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    paste(selector,text){const node=this.get(selector),event=new window.Event('paste',{bubbles:true,cancelable:true});
      Object.defineProperty(event,'clipboardData',{value:{getData:()=>text}});node.dispatchEvent(event);return node;},
    change(selector,value){const node=this.get(selector);node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    check(selector,checked=true){const node=this.get(selector);node.checked=checked;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(selector){const form=this.get(selector);form.dispatchEvent(new window.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:form.querySelector('[type="submit"]')}));},
    writes(){return calls.filter(call=>call.method!=='GET');},
    sends(){return calls.filter(call=>call.path.endsWith('/send'));},
    async tick(){const [key,timer]=timers.entries().next().value;timers.delete(key);await timer.callback();await flush();},
    async prepare(url='https://s.shopee.com.br/short-synthetic'){this.input('#af-offer-url',url);this.submit('[data-form="offer"]');await flush();},
  };
  return h;
}

test('Ofertas começa com entrada compacta e rascunhos recolhidos, sem solicitar publicação',async t=>{
  const h=await harness(t);
  assert.equal(h.get('h1').textContent,'Ofertas');
  assert.equal(h.get('[data-offer-section="composer"]').hidden,true);
  assert.equal(h.get('[data-offer-disclosure="drafts"]').open,false);
  assert.match(h.get('[data-form="offer"]').textContent,/Gerar prévia/);
  assert.equal(h.get('#af-offer-url').placeholder,'Cole o link ou a mensagem da oferta');
  assert.equal(h.get('#af-offer-url').type,'text');
  assert.equal(h.get('#af-offer-url').maxLength,8192);
  assert.equal(h.get('[data-offer-disclosure="stores"] summary').textContent,'Links de 3 lojas');
  assert.equal(h.writes().length,0);
});

test('Preparar um link curto abre template, frases, prévia e três tipos de destino sem enviar',async t=>{
  const h=await harness(t);
  const source=h.get('[data-form="offer"]'),url=h.get('#af-offer-url');
  await h.prepare();
  assert.equal(h.get('[data-offer-section="composer"]').hidden,false);
  assert.equal(h.get('[data-form="offer"]'),source);
  assert.equal(h.get('#af-offer-url'),url);
  assert.equal(h.get('#preview-template').value,'default');
  assert.equal(h.host.querySelectorAll('[data-offer-phrase]').length,3);
  assert.equal(h.host.querySelectorAll('[data-offer-destination]:not(:disabled)').length,3);
  assert.equal(h.get('[data-offer-destination="'+IDS.blocked+'"]').disabled,true);
  assert.match(h.get('#offer-text').textContent,/Fone sem fio/);
  assert.match(h.get('#offer-text').textContent,/synthetic-account-link/);
  assert.equal(h.get('[data-act="offer-send"]').disabled,true);
  assert.equal(h.sends().length,0);
  assert.deepEqual(h.writes().map(call=>call.path.slice(apiRoot.length)),['offers']);
});

test('Seleção exata, múltipla e todos preservam controles e excluem destino inelegível',async t=>{
  const h=await harness(t);
  await h.prepare();
  const composer=h.get('[data-offer-section="composer"]'),template=h.get('#preview-template');
  const control=h.get('[data-offer-destination="'+IDS.group+'"]');
  control.focus();h.check('[data-offer-destination="'+IDS.group+'"]');
  assert.equal(h.get('[data-offer-destination="'+IDS.group+'"]'),control);
  assert.equal(h.document.activeElement,control);
  assert.equal(h.get('[data-offer-all]').indeterminate,true);
  assert.match(h.get('[data-act="offer-send"]').textContent,/Enviar para 1 destino/);
  h.check('[data-offer-all]');
  assert.match(h.get('[data-offer-target-count]').textContent,/3 selecionados/);
  assert.equal(h.get('[data-offer-destination="'+IDS.blocked+'"]').checked,false);
  h.check('[data-offer-destination="'+IDS.group+'"]',false);
  h.click('[data-act="offer-send"]');
  await flush();
  const sent=h.sends();
  assert.equal(sent.length,1);
  assert.deepEqual(sent[0].payload.destinations,[IDS.telegram,IDS.channel].sort());
  assert.equal(sent[0].payload.template,'default');
  assert.match(sent[0].payload.request_id,/^[a-f0-9]{32}$/);
  assert.ok(sent[0].payload.preview_token);
  assert.equal(h.get('[data-offer-section="composer"]'),composer);
  assert.equal(h.get('#preview-template'),template);
  assert.match(h.get('[data-offer-send-result]').textContent,/2 publicações na fila/);
  assert.doesNotMatch(h.get('[data-offer-send-result]').textContent,/publicadas|enviadas com sucesso/);
});

test('Trocar template e frase mantém seleção, foco e prévia no lugar',async t=>{
  const h=await harness(t);
  await h.prepare();
  h.check('[data-offer-destination="'+IDS.telegram+'"]');
  const select=h.get('#preview-template'),panel=h.get('[data-offer-section="preview"]');
  select.focus();h.change('#preview-template',ALTERNATE);await flush();
  assert.equal(h.get('#preview-template'),select);
  assert.equal(h.document.activeElement,select);
  assert.equal(h.get('[data-offer-section="preview"]'),panel);
  assert.equal(h.get('[data-offer-destination="'+IDS.telegram+'"]').checked,true);
  assert.equal(h.host.querySelector('.af-offer-card-image'),null);
  const phrase=h.get('[data-offer-phrase="two"]');phrase.focus();phrase.click();await flush();
  assert.equal(h.get('[data-offer-phrase="two"]'),phrase);
  assert.equal(h.document.activeElement,phrase);
  assert.match(h.get('#offer-text').textContent,/Conheça os detalhes/);
  assert.equal(h.get('[data-offer-destination="'+IDS.telegram+'"]').checked,true);
  assert.equal(h.sends().length,0);
});

test('Outras frases não aplicam texto nem enviam; frase personalizada é salva só ao aplicar',async t=>{
  const h=await harness(t);
  await h.prepare();
  h.click('[data-act="offer-phrases"]');await flush();
  assert.equal(h.data.offer[0].phrase,defaultPhrase);
  assert.equal(h.calls.filter(call=>call.method==='PUT').length,0);
  const editor=h.get('[data-offer-disclosure="phrase"]');editor.open=true;
  const text=h.input('#af-offer-phrase','Conexão e praticidade para você.');
  text.focus();text.setSelectionRange(3,9);
  h.check('[data-offer-destination="'+IDS.telegram+'"]');
  assert.equal(h.get('#af-offer-phrase'),text);
  assert.equal(text.value,'Conexão e praticidade para você.');
  assert.equal(text.selectionStart,3);
  assert.equal(h.get('[data-act="offer-send"]').disabled,true);
  h.submit('[data-form="phrase"]');await flush();
  assert.equal(h.data.offer[0].phrase,'Conexão e praticidade para você.');
  assert.equal(h.get('[data-act="offer-send"]').disabled,false);
  assert.equal(h.sends().length,0);
});

test('Produto de outra loja importa sem inventar afiliação e permite completar dados e link próprio',async t=>{
  const h=await harness(t,{prepare:()=>offer({retailer:'amazon',retailer_name:'Amazon',title:'Luminária de escritório',price:null,
    image:null,affiliate_url:null,affiliate_status:'not_converted',link_status:'required',
    extraction:{status:'partial',missing:['price','image','rating']}})});
  await h.prepare('https://www.amazon.com.br/dp/SYNTHETIC');
  assert.equal(h.get('[data-offer-disclosure="details"]').open,true);
  assert.match(h.get('[data-offer-notice="extraction"]').textContent,/preço e imagem/);
  assert.doesNotMatch(h.get('[data-offer-notice="extraction"]').textContent,/avaliação|rating/);
  assert.ok(h.get('[data-form="offer-own-link"]'));
  assert.doesNotMatch(h.get('[data-offer-section="product"]').textContent,/gerado pela Shopee|Link gerado/);
  h.input('#af-offer-title','Luminária de escritório com conexão USB');
  h.input('#af-offer-price','189,90');
  h.input('#af-offer-image','https://orion.test/luminaria.png');
  h.submit('[data-form="offer-details"]');await flush();
  assert.equal(h.data.offer[0].price,'189.90');
  assert.equal(h.host.querySelector('[data-offer-notice="extraction"]'),null);
  assert.match(h.get('[data-offer-section="product"]').textContent,/189,90/);
  h.input('#af-offer-affiliate','https://amzn.to/synthetic-own-link');
  h.submit('[data-form="offer-own-link"]');await flush();
  assert.equal(h.data.offer[0].affiliate_status,'provided');
  assert.equal(h.data.offer[0].affiliate_url,'https://amzn.to/synthetic-own-link');
  assert.match(h.get('#offer-text').textContent,/amzn.to\/synthetic-own-link/);
  h.check('[data-offer-destination="'+IDS.channel+'"]');
  assert.equal(h.get('[data-act="offer-send"]').disabled,false);
  assert.equal(h.sends().length,0);
});

test('Falha de extração e nova tentativa preservam rascunho, edição, link, nota, foco e seleção',async t=>{
  const h=await harness(t,{initialOffer:offer(),prepare:payload=>{throw emptyProductError(payload.url);}});
  h.click('[data-act="open-offer"]');await flush();
  h.change('#preview-template',ALTERNATE);await flush();
  h.check('[data-offer-destination="'+IDS.telegram+'"]');
  const composer=h.get('[data-offer-section="composer"]');
  const own=h.input('#af-offer-affiliate-edit','https://s.shopee.com.br/my-unsaved-link');
  const phrase=h.input('#af-offer-phrase','Uma frase ainda em edição.');
  h.input('#af-offer-note','Minha observação');
  const input=h.input('#af-offer-url','https://amzn.to/synthetic-unavailable');
  input.focus();input.setSelectionRange(8,15);
  let jumps=0;
  h.window.HTMLElement.prototype.scrollIntoView=()=>{jumps++;};
  h.submit('[data-form="offer"]');await flush();
  assert.equal(h.data.offer.length,1);
  assert.equal(h.get('[data-offer-section="composer"]'),composer);
  assert.equal(h.get('#af-offer-affiliate-edit'),own);
  assert.equal(own.value,'https://s.shopee.com.br/my-unsaved-link');
  assert.equal(h.get('#af-offer-phrase'),phrase);
  assert.equal(phrase.value,'Uma frase ainda em edição.');
  assert.equal(h.get('#af-offer-note').value,'Minha observação');
  assert.equal(h.get('#preview-template').value,ALTERNATE);
  assert.equal(h.get('[data-offer-destination="'+IDS.telegram+'"]').checked,true);
  assert.equal(h.document.activeElement,input);
  assert.equal(input.selectionStart,8);
  assert.match(h.get('[data-offer-notice="prepare"]').textContent,/não disponibilizou/);
  const retry=h.get('[data-act="offer-retry"]');retry.focus();retry.click();await flush();
  assert.equal(h.get('[data-act="offer-retry"]'),retry);
  assert.equal(h.document.activeElement,retry);
  assert.equal(h.data.offer.length,1);
  assert.equal(h.get('#af-offer-affiliate-edit').value,'https://s.shopee.com.br/my-unsaved-link');
  assert.equal(jumps,0);
  const attempts=h.calls.filter(call=>call.path===apiRoot+'offers');
  assert.equal(attempts.length,2);
  assert.ok(attempts.every(call=>call.payload.allow_manual!==true));
  assert.equal(h.sends().length,0);
});

test('Preenchimento manual só começa por escolha explícita após falha e nunca vale para o próximo link',async t=>{
  const h=await harness(t,{prepare:payload=>{
    if (!payload.allow_manual) throw emptyProductError(payload.url);
    return offer({title:null,price:null,image:null,currency:null,affiliate_url:null,
      source_url:payload.url,extraction:{status:'partial',manual_requested:true}});
  }});
  const first='https://amzn.to/synthetic-first',second='https://amzn.to/synthetic-second';
  await h.prepare(first);
  assert.equal(h.data.offer.length,0);
  assert.equal(h.get('[data-offer-section="composer"]').hidden,true);
  h.input('#af-offer-url',second);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  h.input('#af-offer-url',first);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  await h.prepare(first);
  h.click('[data-act="offer-manual"]');await flush();
  assert.equal(h.data.offer.length,1);
  assert.match(h.get('[data-offer-notice="extraction"]').textContent,/Preenchimento manual/);
  assert.equal(h.get('[data-act="offer-send"]').disabled,true);
  const manual=h.calls.filter(call=>call.payload?.allow_manual===true);
  assert.equal(manual.length,1);
  assert.equal(manual[0].payload.url,first);
  await h.prepare(second);
  assert.equal(h.data.offer.length,1);
  assert.equal(h.calls.filter(call=>call.payload?.allow_manual===true).length,1);
  assert.equal(h.calls.filter(call=>call.path===apiRoot+'offers').at(-1).payload.allow_manual,undefined);
  assert.equal(h.sends().length,0);
});

test('Erro de um link anterior não autoriza preenchimento manual do endereço digitado durante a consulta',async t=>{
  const pending=deferred();
  const first='https://amzn.to/synthetic-first',second='https://amzn.to/synthetic-second';
  const h=await harness(t,{prepare:()=>pending.promise});
  h.input('#af-offer-url',first);h.submit('[data-form="offer"]');await flush();
  const input=h.input('#af-offer-url',second);input.focus();
  pending.reject(emptyProductError(first));await flush();
  assert.equal(h.get('#af-offer-url').value,second);
  assert.equal(h.document.activeElement,input);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  assert.equal(h.host.querySelector('[data-offer-notice="prepare"]'),null);
  assert.equal(h.data.offer.length,0);
});

test('Mensagem com vários links pede escolha sem perder edição, foco, cursor ou posição da tela',async t=>{
  const amazon='https://amzn.to/synthetic?tag=owner%2B20+X',network='https://tidd.ly/synthetic';
  const raw='Duas ofertas: '+amazon+' e '+network;
  const candidates=[{url:amazon,retailer:'amazon',retailer_name:'Amazon'},
    {url:network,retailer:null,retailer_name:'Link de oferta'}];
  const h=await harness(t,{initialOffer:offer(),prepare:payload=>{
    if (payload.url===raw) throw Object.assign(new Error('Escolha o produto para gerar a prévia.'),{status:422,
      detail:{code:'PRODUCT_LINK_AMBIGUOUS',candidates}});
    throw emptyProductError(payload.url);
  }});
  h.click('[data-act="open-offer"]');await flush();
  const composer=h.get('[data-offer-section="composer"]');
  const own=h.input('#af-offer-affiliate-edit','https://s.shopee.com.br/my-unsaved-link');
  const phrase=h.input('#af-offer-phrase','Minha frase ainda em edição.');
  h.input('#af-offer-note','Nota preservada');
  const input=h.input('#af-offer-url',raw);input.focus();input.setSelectionRange(7,19);
  Object.defineProperty(h.window,'scrollY',{value:340,writable:true});
  let jumps=0;h.window.scrollTo=()=>{jumps++;};
  h.window.HTMLElement.prototype.scrollIntoView=()=>{jumps++;};
  h.submit('[data-form="offer"]');await flush();
  assert.equal(h.document.activeElement,input);
  assert.equal(input.selectionStart,7);assert.equal(input.selectionEnd,19);
  assert.equal(input.value,raw);assert.equal(h.window.scrollY,340);assert.equal(jumps,0);
  assert.equal(h.get('[data-offer-section="composer"]'),composer);
  assert.equal(h.get('#af-offer-affiliate-edit'),own);
  assert.equal(h.get('#af-offer-phrase'),phrase);
  assert.equal(phrase.value,'Minha frase ainda em edição.');
  assert.equal(h.host.querySelectorAll('[data-offer-link-choice]').length,2);
  assert.match(h.get('[data-offer-link-choice="1"]').textContent,/Link de oferta/);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  h.click('[data-offer-link-choice="1"]');await flush();
  const attempts=h.calls.filter(call=>call.path===apiRoot+'offers');
  assert.deepEqual(attempts.map(call=>call.payload.url),[raw,network]);
  assert.ok(attempts.every(call=>call.payload.note==='Nota preservada'&&!call.payload.allow_manual));
  assert.equal(h.get('#af-offer-url'),input);
  assert.equal(input.value,network);
  assert.equal(h.get('[data-offer-section="composer"]'),composer);
  assert.equal(own.value,'https://s.shopee.com.br/my-unsaved-link');
  assert.equal(h.data.offer.length,1);assert.equal(h.sends().length,0);assert.equal(jumps,0);
});

test('Escolhas de mensagem anterior são descartadas quando o campo muda durante a consulta',async t=>{
  const pending=deferred(),first='Ofertas: amzn.to/first e meli.la/second',second='amzn.to/current';
  const h=await harness(t,{prepare:()=>pending.promise});
  h.input('#af-offer-url',first);h.submit('[data-form="offer"]');await flush();
  const input=h.input('#af-offer-url',second);input.focus();input.setSelectionRange(5,9);
  pending.reject(Object.assign(new Error('Escolha um produto.'),{detail:{code:'PRODUCT_LINK_AMBIGUOUS',
    candidates:[{url:'https://amzn.to/first',retailer_name:'Amazon'}]}}));await flush();
  assert.equal(input.value,second);assert.equal(h.document.activeElement,input);
  assert.equal(input.selectionStart,5);assert.equal(input.selectionEnd,9);
  assert.equal(h.host.querySelector('[data-offer-link-choices]'),null);
  assert.equal(h.host.querySelector('[data-offer-notice="prepare"]'),null);
  assert.equal(h.data.offer.length,0);assert.equal(h.sends().length,0);
});

test('Recuperação manual fica vinculada à mensagem original mesmo quando a API normaliza o URL',async t=>{
  const source='https://amzn.to/normalized?tag=owner%2B20',raw='  Oferta: amzn.to/normalized?tag=owner%2B20  ';
  const h=await harness(t,{prepare:payload=>{
    if (!payload.allow_manual) throw emptyProductError(source);
    return offer({source_url:source,title:null,price:null,image:null,currency:null,affiliate_url:null,
      extraction:{manual_requested:true}});
  }});
  await h.prepare(raw);
  assert.equal(h.get('#af-offer-url').value,raw);
  assert.ok(h.get('[data-act="offer-manual"]'));
  h.input('#af-offer-url',source);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  h.input('#af-offer-url',raw);
  assert.equal(h.host.querySelector('[data-act="offer-manual"]'),null);
  await h.prepare(raw);
  h.click('[data-act="offer-manual"]');await flush();
  const manual=h.calls.filter(call=>call.payload?.allow_manual===true);
  assert.equal(manual.length,1);assert.equal(manual[0].payload.url,raw);
  assert.equal(h.data.offer[0].source_url,source);assert.equal(h.sends().length,0);
});

test('Colar mensagem multilinha mantém separadores e cursor; excesso de tamanho não trunca o link',async t=>{
  const h=await harness(t);
  const input=h.get('#af-offer-url');input.focus();
  const message='Oferta de hoje\r\namzn.to/first\nhttps://t.me/group\rCupom: EXEMPLO';
  h.paste('#af-offer-url',message);
  const expected='Oferta de hoje amzn.to/first https://t.me/group Cupom: EXEMPLO';
  assert.equal(input.value,expected);
  assert.equal(input.selectionStart,expected.length);assert.equal(input.selectionEnd,expected.length);
  assert.equal(input.validity.typeMismatch,false);
  assert.equal(h.document.activeElement,input);assert.equal(h.writes().length,0);
  input.setSelectionRange(8,12);
  h.paste('#af-offer-url','x'.repeat(8193));
  assert.equal(input.value,expected);assert.equal(input.selectionStart,8);assert.equal(input.selectionEnd,12);
  assert.match(h.get('[data-offer-notice="prepare"]').textContent,/8\.192/);
  assert.equal(h.document.activeElement,input);assert.equal(h.writes().length,0);
  input.setSelectionRange(0,input.value.length);
  const large='Oferta '+ 'x'.repeat(3000)+' amzn.to/first';
  h.paste('#af-offer-url',large);
  h.submit('[data-form="offer"]');await flush();
  assert.equal(h.calls.find(call=>call.path===apiRoot+'offers').payload.url,large);
  assert.equal(h.sends().length,0);
});

test('Avaliação ausente não gera aviso e imagem opcional some do aviso no template de texto',async t=>{
  const h=await harness(t,{prepare:()=>offer({image:null,rating:null,
    extraction:{status:'partial',missing:['image','rating']}})});
  await h.prepare();
  assert.match(h.get('[data-offer-notice="extraction"]').textContent,/imagem/);
  h.change('#preview-template',ALTERNATE);await flush();
  assert.equal(h.host.querySelector('[data-offer-notice="extraction"]'),null);
});

test('Erro ao preparar mantém o link editável e oferece contexto junto ao formulário',async t=>{
  const h=await harness(t,{prepare:()=>{throw Error('Não foi possível ler este link. Cole o endereço completo do produto.');}});
  await h.prepare();
  assert.equal(h.get('#af-offer-url').value,'https://s.shopee.com.br/short-synthetic');
  assert.match(h.get('[data-offer-notice="prepare"]').textContent,/Cole o endereço completo/);
  assert.equal(h.get('[data-form="offer"] [type="submit"]').disabled,false);
  assert.equal(h.get('[data-offer-section="composer"]').hidden,true);
  assert.equal(h.sends().length,0);
});

test('Prévia atrasada não sobrescreve template mais recente nem uma rota abandonada',async t=>{
  const old=deferred(),latest=deferred(),left=deferred();
  const h=await harness(t,{getPreview:(result,count)=>count===1?result:count===2?old.promise:count===3?latest.promise:left.promise});
  await h.prepare();
  h.change('#preview-template',ALTERNATE);await flush();
  h.change('#preview-template','default');await flush();
  const template=h.get('#preview-template');template.focus();
  latest.resolve(preview(offer({title:'Resultado mais recente'})));await flush();
  old.resolve(preview(offer({title:'Resultado antigo'}),ALTERNATE));await flush();
  assert.equal(h.get('#preview-template'),template);
  assert.equal(h.document.activeElement,template);
  assert.match(h.get('#offer-text').textContent,/Resultado mais recente/);
  assert.doesNotMatch(h.get('#offer-text').textContent,/Resultado antigo/);
  h.change('#preview-template',ALTERNATE);await flush();
  h.controller.setView('conexoes');
  left.resolve(preview(offer({title:'Resposta de outra tela'}),ALTERNATE));await flush();
  assert.equal(h.get('h1').textContent,'Conexões e destinos');
  assert.equal(h.host.querySelector('.af-offers-workspace'),null);
});

test('Falha temporária no envio mantém a intenção idempotente e não duplica cliques em andamento',async t=>{
  const pending=deferred();
  const h=await harness(t,{send:(payload,count)=>{
    if(count===1)throw Error('Não foi possível confirmar o envio. Tente novamente.');
    return pending.promise;
  }});
  await h.prepare();
  h.check('[data-offer-destination="'+IDS.telegram+'"]');
  h.click('[data-act="offer-send"]');await flush();
  assert.match(h.get('[data-offer-notice="send"]').textContent,/Tente novamente/);
  assert.equal(h.get('[data-offer-destination="'+IDS.telegram+'"]').checked,true);
  h.click('[data-act="offer-send"]');h.click('[data-act="offer-send"]');await flush();
  assert.equal(h.sends().length,2);
  assert.deepEqual(h.sends()[0].payload,h.sends()[1].payload);
  pending.resolve({batch_id:h.sends()[1].payload.request_id,created:false,jobs:[{id:'one',destination:IDS.telegram,status:'queued'}]});
  await flush();
  assert.match(h.get('[data-offer-send-result]').textContent,/1 publicação na fila/);
  assert.equal(h.host.querySelector('[data-act="offer-send"]'),null);
});

test('Prévia aceita somente marcação de texto e preserva acentos',async t=>{
  const h=await harness(t,{getPreview:result=>({...result,html:'<strong>Conexão e você</strong><img src=x onerror="window.unwanted=true"><script>window.unwanted=true</script><em>Prático</em>'})});
  await h.prepare();
  assert.ok(h.get('#offer-text strong'));
  assert.ok(h.get('#offer-text em'));
  assert.equal(h.get('#offer-text').querySelector('img,script'),null);
  assert.equal(h.window.unwanted,undefined);
  assert.match(h.get('#offer-text').textContent,/Conexão e você/);
});

test('Preço estrangeiro permanece na moeda de origem até confirmação explícita em reais',async t=>{
  const h=await harness(t,{prepare:()=>offer({retailer:'aliexpress',retailer_name:'AliExpress',price:'19.90',currency:'USD'})});
  await h.prepare('https://pt.aliexpress.com/item/synthetic.html');
  assert.match(h.get('.af-offer-product-price').textContent,/US\$/);
  assert.doesNotMatch(h.get('.af-offer-product-price').textContent,/R\$\s*19,90/);
  assert.equal(h.get('#af-offer-price').value,'');
  assert.equal(h.get('[data-offer-disclosure="details"]').open,true);
  h.input('#af-offer-price','119,90');
  h.submit('[data-form="offer-details"]');await flush();
  assert.equal(h.data.offer[0].currency,'BRL');
  assert.match(h.get('.af-offer-product-price').textContent,/119,90/);
  const update=h.writes().find(call=>call.method==='PUT');
  assert.deepEqual(update.payload,{price:'119.90'});
});

test('Editar somente título não reenvia nem reclassifica o link nativo já gerado',async t=>{
  const h=await harness(t);
  await h.prepare();
  h.input('#af-offer-title','Fone sem fio revisado');
  h.submit('[data-form="offer-details"]');await flush();
  const update=h.writes().find(call=>call.method==='PUT');
  assert.deepEqual(update.payload,{title:'Fone sem fio revisado'});
  assert.equal(h.data.offer[0].affiliate_status,'generated');
  assert.equal(h.data.offer[0].affiliate_url,'https://s.shopee.com.br/synthetic-account-link');
});

test('Acompanhamento consulta recibos e encerra em estados finais sem reenviar desconhecidos',async t=>{
  const h=await harness(t,{getBatch:batch=>({...batch,jobs:batch.jobs.map((job,index)=>({...job,
    status:index?'unknown':'published',error:index?'Confira o destino antes de tentar novamente.':null}))})});
  await h.prepare();h.check('[data-offer-all]');h.click('[data-act="offer-send"]');await flush();
  assert.equal(h.timers.size,1);
  const link=h.get('[data-offer-send-result] a');link.focus();
  await h.tick();
  assert.equal(h.get('[data-offer-send-result] a'),link);
  assert.equal(h.document.activeElement,link);
  assert.match(h.get('[data-offer-send-result]').textContent,/Publicada/);
  assert.match(h.get('[data-offer-send-result]').textContent,/Envio sem confirmação/);
  assert.equal(h.timers.size,0);
  assert.equal(h.sends().length,1);
  assert.equal(h.calls.filter(call=>call.path.includes('/send-batches/')).length,1);
});

test('Navegar cancela consultas de recibo de publicação pendentes',async t=>{
  const h=await harness(t);
  await h.prepare();h.check('[data-offer-all]');h.click('[data-act="offer-send"]');await flush();
  assert.equal(h.timers.size,1);
  h.controller.setView('conexoes');
  assert.equal(h.timers.size,0);
  assert.equal(h.calls.filter(call=>call.path.includes('/send-batches/')).length,0);
  assert.equal(h.sends().length,1);
});

test('Conexões consulta grupos da conta, salva seleção exata e verifica canal sem gate de teste',async t=>{
  const h=await harness(t);
  h.controller.setView('conexoes');
  h.change('#af-mode','whatsapp_group');
  assert.equal(h.get('[data-wa-group-picker]').hidden,false);
  assert.equal(h.get('[data-destination-address]').hidden,true);
  h.click('[data-act="load-wa-groups"]');await flush();
  h.change('#af-whatsapp-group','120363099001@g.us');
  assert.equal(h.get('[data-form="destination"] input[name="name"]').value,'Grupo da conta sintética');
  h.submit('[data-form="destination"]');await flush();
  const saved=h.writes().find(call=>call.path.includes('/destinations/')&&call.method==='PUT');
  assert.equal(saved.payload.address,'120363099001@g.us');
  assert.equal(saved.payload.mode,'whatsapp_group');
  assert.equal(saved.payload.test,false);
  h.click('[data-act="verify-wa-channel"][data-value="'+IDS.blocked+'"]');await flush();
  assert.equal(h.data.destination.find(row=>row.id===IDS.blocked).verified,true);
  assert.equal(h.sends().length,0);
});

test('Trocar template com resposta lenta preserva frase personalizada ainda não aplicada e cursor',async t=>{
  const later=deferred();
  const h=await harness(t,{getPreview:(result,count)=>count===1?result:later.promise});
  await h.prepare();
  h.get('[data-offer-disclosure="phrase"]').open=true;
  const textarea=h.input('#af-offer-phrase','Uma frase personalizada ainda em edição.');
  textarea.focus();textarea.setSelectionRange(4,10);
  h.change('#preview-template',ALTERNATE);
  await flush();
  later.resolve(preview(offer(),ALTERNATE));
  await flush();
  assert.equal(h.get('#af-offer-phrase'),textarea);
  assert.equal(textarea.value,'Uma frase personalizada ainda em edição.');
  assert.equal(h.document.activeElement,textarea);
  assert.equal(textarea.selectionStart,4);
  assert.equal(textarea.selectionEnd,10);
  assert.equal(h.data.offer[0].phrase,defaultPhrase);
  assert.equal(h.calls.filter(call=>call.method==='PUT').length,0);
  assert.equal(h.sends().length,0);
});
