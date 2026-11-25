import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {deferred,flush,tenantA} from './harness.mjs';

const source=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
const API='/api/v1/afiliados/'+tenantA+'/api/';
const EPOCH=1795635390;
const copy=value=>JSON.parse(JSON.stringify(value));
const DEST=['b'.repeat(32),'c'.repeat(32)];
const STATES=['approval','queued','preparing','dispatching','published','paused','failed','expired','cancelled','unknown'];
const id=index=>(index+1).toString(16).padStart(32,'0');
const rowSelector=index=>'[data-history-job="'+id(index)+'"]';

function job(index,status=STATES[index%STATES.length]){
  return {id:id(index),destination:DEST[index%2],status,due:EPOCH+3600,changed:EPOCH-index*60,
    published_at:status==='published'?EPOCH-300:null,attempts:status==='published'?1:0,
    receipt:status==='published'?{message_id:'private-provider-receipt-'+index}:null,
    error:status==='failed'?'A conexão do destino precisa ser verificada.':null,
    doc:{automation:{rule_id:'d'.repeat(32),mode:'review'},rule:{name:'Achados da tarde',timezone:'America/Campo_Grande'},
      template:{name:'Oferta completa',media:'card'},offer:{title:index===0?'Cafeteira elétrica com jarra de aço e café fresco':'Oferta '+index+' com acentuação',
        image:null,price:'89.90',currency:'BRL',retailer_name:'Shopee',identity:'shopee:1:'+index,
        affiliate_url:'https://s.shopee.com.br/synthetic-'+index}}};
}

function workspace(){
  return {company:'Conta sintética',sending:false,offer:[],decision:[],rule:[],product_flag:[],niches:[],sorts:{},
    connection:[],template:[],destination:[
      {id:DEST[0],name:'Tecnologia no Telegram',mode:'telegram',verified:true},
      {id:DEST[1],name:'Promoções no WhatsApp',mode:'whatsapp_channel',verified:true}],
    jobs:STATES.map((status,index)=>job(index,status))};
}

async function harness(t,{data=workspace(),getState,write}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/panel?aba=afiliados-fila',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,{document}=window,host=document.querySelector('#host'),calls=[],errors=[],notices=[],jumps=[];
  window.CSS={escape:String};window.HTMLElement.prototype.scrollIntoView=()=>{};
  window.scrollTo=options=>jumps.push(options);
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  window.HTMLDialogElement.prototype.close=function(value){this.returnValue=value||this.returnValue;this.open=false;this.dispatchEvent(new window.Event('close'));};
  window.addEventListener('error',event=>errors.push(event.error?.stack||event.message));
  window.eval(source.replace('export function mount(','function mountHistory(')+'\nwindow.mountHistory=mountHistory;');
  let mounted,reads=0;
  mounted=window.mountHistory({host,tenantId:tenantA,view:'fila',whatsappAvailable:true,
    icon:name=>'<svg class="os-icon" data-icon="'+name+'" aria-hidden="true"></svg>',
    open:async route=>mounted.setView(route),showNotice:(message,error)=>notices.push({message,error}),
    request:async(path,method,payload)=>{
      calls.push({path,method,payload:payload===undefined?undefined:copy(payload)});
      const route=path.slice(API.length);
      if(route==='state'&&method==='GET'){reads++;return copy(getState?await getState(data,reads):data);}
      if(/^jobs\/[0-9a-f]{32}$/.test(route)&&method==='POST'){
        assert.ok(['approve','cancel'].includes(payload.action),'Only synthetic approval/cancellation may be requested');
        const row=data.jobs.find(item=>item.id===route.slice(5));assert.ok(row);
        if(write)await write(row,payload);
        row.status=payload.action==='approve'?'queued':'cancelled';return {ok:true};
      }
      throw Error('Unexpected request: '+method+' '+route);
    }});
  t.after(()=>{mounted.unmount();dom.window.close();assert.deepEqual(errors,[]);});
  await flush();
  return {window,document,host,data,calls,notices,jumps,mounted,
    get(selector){const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);return node;},
    click(selector){this.get(selector).click();},
    input(value){const node=this.get('#filter-history');node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    confirm(value='confirm'){this.get('#osAffiliateDialog').close(value);},
    rows(){return [...host.querySelectorAll('[data-history-job]')].filter(node=>!node.closest('[hidden]'));},
    writes(){return calls.filter(call=>call.method!=='GET');}};
}

test('Filtros mantêm contagens de estados reais e não confundem preparação com publicação',async t=>{
  const h=await harness(t);
  assert.equal(h.get('.af-history-workspace h1').textContent,'Fila e histórico');
  assert.equal(h.rows().length,10);
  for(const [filter,states] of [['pending',STATES.slice(0,4)],['published',['published']],['attention',['paused','failed','expired','unknown']],['cancelled',['cancelled']],['all',STATES]]){
    h.click('[data-history-filter="'+filter+'"]');
    assert.deepEqual(h.rows().map(node=>node.dataset.historyStatus).sort(),[...states].sort());
    const button=h.get('[data-history-filter="'+filter+'"]');
    assert.equal(button.getAttribute('aria-pressed'),'true');assert.equal(Number(button.textContent.match(/\d+\s*$/)?.[0]),states.length);
  }
  assert.equal(h.calls.length,1);assert.equal(h.writes().length,0);
});

test('Busca por oferta ou destino ignora acentos e preserva o campo, foco e seleção',async t=>{
  const h=await harness(t),field=h.get('#filter-history'),root=h.get('.af-history-workspace');
  assert.equal(h.get('label[for="filter-history"]').textContent.trim(),'Buscar oferta ou destino');
  field.focus();h.input('CAFE');field.setSelectionRange(1,3);await flush();
  assert.equal(h.rows().length,1);assert.match(h.rows()[0].textContent,/Cafeteira elétrica/);
  assert.equal(h.get('#filter-history'),field);assert.equal(h.get('.af-history-workspace'),root);
  assert.equal(h.document.activeElement,field);assert.equal(field.selectionStart,1);assert.equal(field.selectionEnd,3);
  h.input('promocoes');assert.equal(h.rows().length,5);
  h.click('[data-history-filter=attention]');assert.equal(h.rows().length,3);
  assert.equal(h.get('#filter-history'),field);assert.equal(field.value,'promocoes');assert.equal(h.calls.length,1);
});

test('Atualização preserva busca, filtro, foco, cursor e posição da tela com novos registros',async t=>{
  const h=await harness(t);h.click('[data-history-filter=published]');const field=h.input('oferta');
  field.focus();field.setSelectionRange(2,5);Object.defineProperty(h.window,'scrollY',{value:470,writable:true});
  const existing=h.get(rowSelector(4)),root=h.get('.af-history-workspace');
  h.data.jobs.unshift(job(10,'published'));h.click('[data-act=refresh-history]');await flush();
  assert.equal(h.get('.af-history-workspace'),root);assert.equal(h.get('#filter-history'),field);
  assert.equal(h.get(rowSelector(4)),existing);assert.equal(h.document.activeElement,field);
  assert.equal(field.selectionStart,2);assert.equal(field.selectionEnd,5);assert.equal(field.value,'oferta');
  assert.equal(h.get('[data-history-filter=published]').getAttribute('aria-pressed'),'true');assert.equal(h.rows().length,2);
  assert.equal(h.window.scrollY,470);assert.equal(h.jumps.length,0);assert.equal(h.writes().length,0);
});

test('Resposta lenta usa a busca atual e atualizar repetidamente não duplica a consulta',async t=>{
  const pending=deferred();const h=await harness(t,{getState:(data,read)=>read===1?data:pending.promise});
  h.click('[data-act=refresh-history]');await flush();h.click('[data-act=refresh-history]');await flush();
  const field=h.input('cafe');field.focus();field.setSelectionRange(1,4);
  pending.resolve(h.data);await flush();
  assert.equal(h.calls.length,2);assert.equal(h.rows().length,1);assert.equal(h.get('#filter-history'),field);
  assert.equal(h.document.activeElement,field);assert.equal(field.selectionStart,1);assert.equal(field.selectionEnd,4);
});

test('Destino combina com a busca e situação e permanece selecionado depois de atualizar',async t=>{
  const h=await harness(t),select=h.get('#history-destination');
  select.value=DEST[1];select.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  assert.equal(h.rows().length,5);h.input('oferta');h.click('[data-history-filter=attention]');
  assert.equal(h.rows().length,3);h.click('[data-act=refresh-history]');await flush();
  assert.equal(h.get('#history-destination'),select);assert.equal(select.value,DEST[1]);
  assert.equal(h.get('#filter-history').value,'oferta');assert.equal(h.rows().length,3);assert.equal(h.writes().length,0);
});

test('Falha ao atualizar mantém as publicações carregadas e permite nova tentativa',async t=>{
  const h=await harness(t,{getState:(data,read)=>{if(read===2)throw Error('Atualização temporariamente indisponível.');return data;}});
  const row=h.get(rowSelector(0)),field=h.input('cafe');field.focus();
  h.click('[data-act=refresh-history]');await flush();
  assert.equal(h.get(rowSelector(0)),row);assert.equal(h.get('#filter-history'),field);assert.equal(h.rows().length,1);
  assert.match(h.host.textContent+h.notices.map(row=>row.message).join(' '),/temporariamente indisponível/);
  h.click('[data-act=refresh-history]');await flush();assert.equal(h.calls.length,3);assert.equal(h.rows().length,1);
});

test('Mais publicações aumenta a lista sem duplicar linhas ou remontar busca e cards',async t=>{
  const data=workspace();data.jobs=Array.from({length:35},(_,index)=>job(index,'published'));
  const h=await harness(t,{data}),field=h.get('#filter-history'),first=h.rows()[0];
  assert.equal(h.rows().length,20);h.click('[data-history-more]');await flush();
  assert.equal(h.rows().length,35);assert.equal(new Set(h.rows().map(node=>node.dataset.historyJob)).size,35);
  assert.equal(h.get('#filter-history'),field);assert.equal(h.rows()[0],first);assert.equal(h.calls.length,1);
  h.input('cafe');assert.equal(h.rows().length,1);h.input('nenhum produto sintético');assert.equal(h.rows().length,0);
  h.click('[data-history-reset]');assert.equal(field.value,'');assert.equal(h.get('[data-history-filter=all]').getAttribute('aria-pressed'),'true');
  assert.ok(h.rows().length>=20);assert.equal(h.writes().length,0);
});

test('Detalhes mantêm a publicação montada e ocultam recibo técnico do conteúdo visível',async t=>{
  const h=await harness(t),row=h.get(rowSelector(4));
  h.click('[data-history-details="'+id(4)+'"]');
  assert.equal(h.get('[data-history-details="'+id(4)+'"]').getAttribute('aria-expanded'),'true');
  assert.equal(h.get('[data-history-detail="'+id(4)+'"]').hidden,false);assert.equal(h.get(rowSelector(4)),row);
  assert.doesNotMatch(h.host.textContent,/private-provider-receipt/);
  assert.match(row.textContent,/Telegram/);assert.match(row.textContent,/confirmado/);
  h.click('[data-act=refresh-history]');await flush();assert.equal(h.get('[data-history-details="'+id(4)+'"]').getAttribute('aria-expanded'),'true');
  assert.equal(h.get(rowSelector(4)),row);assert.equal(h.writes().length,0);
});

test('Publicada usa data de confirmação; ocorrência encerrada não usa agendamento futuro',async t=>{
  const h=await harness(t);
  for(const index of [4,6]){
    const row=h.get(rowSelector(index));h.click('[data-history-details="'+id(index)+'"]');
    const expected=new h.window.Date((index===4?h.data.jobs[index].published_at:h.data.jobs[index].changed)*1000)
      .toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short',timeZone:'America/Campo_Grande'});
    assert.ok(row.textContent.includes(expected),row.textContent+' should contain '+expected);
  }
});

test('Revisão recorrente requer confirmação explícita mesmo com piloto desabilitado',async t=>{
  const h=await harness(t);h.click(rowSelector(0)+' [data-act=approve]');await flush();
  assert.equal(h.get('#osAffiliateDialog').open,true);assert.equal(h.writes().length,0);
  h.confirm('cancel');await flush();assert.equal(h.writes().length,0);
  h.get(rowSelector(0)+' [data-act=approve]').focus();h.click(rowSelector(0)+' [data-act=approve]');await flush();h.confirm();await flush();
  assert.deepEqual(h.writes().map(call=>call.payload),[{action:'approve'}]);
  assert.equal(h.data.jobs[0].status,'queued');assert.equal(h.get(rowSelector(0)).dataset.historyStatus,'queued');
  assert.equal(h.document.activeElement,h.get('[data-history-details="'+id(0)+'"]'));
  assert.equal(h.jumps.length,0);
});

test('Aprovação legada respeita flag e estados confirmados/ambíguos não oferecem reenvio',async t=>{
  const data=workspace();delete data.jobs[0].doc.automation;
  const h=await harness(t,{data});assert.equal(h.host.querySelector(rowSelector(0)+' [data-act=approve]'),null);
  for(const index of [3,4,9]){
    const row=h.get(rowSelector(index));
    assert.equal(row.querySelector('[data-act=approve],[data-act=cancel-job],[data-act=retry],[data-act=resend]'),null);
  }
  data.sending=true;h.click('[data-act=refresh-history]');await flush();
  h.click(rowSelector(0)+' [data-act=approve]');await flush();h.confirm();await flush();
  assert.deepEqual(h.writes()[0].payload,{action:'approve',confirm_test:true});
});

test('Cancelar requer confirmação e uma requisição pendente não permite ação duplicada',async t=>{
  const pending=deferred(),h=await harness(t,{write:()=>pending.promise});
  h.click(rowSelector(1)+' [data-act=cancel-job]');await flush();h.confirm('cancel');await flush();assert.equal(h.writes().length,0);
  h.click(rowSelector(1)+' [data-act=cancel-job]');await flush();h.confirm();await flush();
  h.click(rowSelector(1)+' [data-act=cancel-job]');await flush();assert.equal(h.writes().length,1);
  pending.resolve();await flush();assert.equal(h.data.jobs[1].status,'cancelled');
  assert.equal(h.get(rowSelector(1)).dataset.historyStatus,'cancelled');
});

test('Vazio, busca sem resultado e campos externos são renderizados sem HTML executável',async t=>{
  const data=workspace();data.jobs[0].doc.offer.title='<img data-injected="title" src=x onerror="alert(1)"> Café & chá';
  data.destination[0].name='<svg data-injected="destination" onload="alert(1)">';
  data.jobs[0].error='<script data-injected="error">alert(1)</script>';
  const h=await harness(t,{data});
  assert.equal(h.host.querySelector('[data-injected]'),null);assert.match(h.get(rowSelector(0)).textContent,/<img data-injected/);
  h.input('produto inexistente');assert.equal(h.rows().length,0);assert.equal(h.get('#history-empty').hidden,false);
  h.click('[data-history-reset]');assert.equal(h.rows().length,10);
  data.jobs=[];h.click('[data-act=refresh-history]');await flush();
  assert.equal(h.rows().length,0);assert.equal(h.get('#history-empty').hidden,false);assert.equal(h.writes().length,0);
});
