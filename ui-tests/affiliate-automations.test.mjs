import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {deferred,flush,tenantA} from './harness.mjs';

const source=await readFile(new URL('../app/static/orion-affiliate-workspace.js',import.meta.url),'utf8');
const API='/api/v1/afiliados/'+tenantA+'/api/';
const RULE='a'.repeat(32),SECOND='f'.repeat(32),DEST=['b','c','d','e'].map(value=>value.repeat(32));
const copy=value=>JSON.parse(JSON.stringify(value));
const now=1795615200;
function rule(overrides={}){
  return {id:RULE,name:'Achados de tecnologia',active:true,next_at:now+3600,keyword:'fone bluetooth',niches:['eletronicos'],
    sort:2,blocked:[],min_price:null,max_price:null,min_rating:null,min_commission:null,min_sales:null,min_discount:null,
    destinations:DEST.slice(0,2),template_id:'default',days:[0,1,2,3,4,5,6],opens:'09:00',closes:'21:00',timezone:'America/Cuiaba',
    interval:90,daily:8,repeat_hours:48,mode:'automatic',revision:1,
    runtime:{status:'ready',reason:null,can_activate:true,destinations:[]},
    activity:{published_today:1,published_total:12,pending:1,approval:0,failed:0,unknown:0,last_published_at:now-300,
      last_execution:{rule_id:RULE,rule:'Achados de tecnologia',reason:'2 publicações preparadas.',executed_at:now-600,status:'prepared',jobs_created:2,job_ids:['one','two']}},...overrides};
}
function workspace(){
  const destinations=[
    {id:DEST[0],name:'Tecnologia no Telegram',mode:'telegram',verified:true,test:false,eligible:true},
    {id:DEST[1],name:'Achados do grupo WhatsApp',mode:'whatsapp_group',verified:true,eligible:true},
    {id:DEST[2],name:'Casa no canal WhatsApp',mode:'whatsapp_channel',verified:true,eligible:true},
    {id:DEST[3],name:'Destino sem verificação',mode:'telegram',verified:false,eligible:false,reason:'Verifique a administração deste destino.'},
  ];
  return {company:'Conta sintética',offer:[],decision:[rule().activity.last_execution],rule:[rule()],destination:destinations,
    automation_destinations:destinations,product_flag:[],jobs:[],niches:[{id:'eletronicos',name:'Áudio e Eletrônicos'},{id:'beleza',name:'Beleza'},{id:'casa',name:'Casa e Construção'}],
    sorts:{1:'Relevância',2:'Mais vendidos'},deal_template:'default',template:[{id:'default',name:'Oferta completa',media:'card',brand:'Achados da Orion'},
      {id:SECOND,name:'Mensagem direta',media:'text',brand:''}],connection:[{id:'shopee',status:'connected'}]};
}
async function harness(t,{data=workspace(),save,activate,getState}={}){
  const dom=new JSDOM('<main id="host"></main>',{url:'https://orion.test/panel?aba=automacoes',runScripts:'outside-only',pretendToBeVisual:true});
  const {window}=dom,{document}=window,host=document.querySelector('#host'),calls=[],notices=[],errors=[],jumps=[];
  window.CSS={escape:String};window.HTMLElement.prototype.scrollIntoView=()=>{};
  window.scrollTo=options=>jumps.push(options);
  Object.defineProperty(window.crypto,'randomUUID',{value:randomUUID});
  window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  window.HTMLDialogElement.prototype.close=function(value){this.returnValue=value||this.returnValue;this.open=false;this.dispatchEvent(new window.Event('close'));};
  window.addEventListener('error',event=>errors.push(event.error?.stack||event.message));
  window.eval(source.replace('export function mount(','function mountAutomations(')+'\nwindow.mountAutomations=mountAutomations;');
  let mounted,reads=0;
  mounted=window.mountAutomations({host,tenantId:tenantA,view:'automacoes',whatsappAvailable:true,
    icon:name=>'<svg class="os-icon" data-icon="'+name+'" aria-hidden="true"></svg>',open:async route=>mounted.setView(route),
    showNotice:(message,error)=>notices.push({message,error}),request:async(path,method,payload)=>{
      calls.push({path,method,payload:payload===undefined?undefined:copy(payload)});
      const route=path.slice(API.length);
      if(route==='state'&&method==='GET'){reads++;return copy(getState?await getState(data,reads):data);}
      const parts=route.split('/'),current=data.rule.find(row=>row.id===parts[1]);
      if(parts[0]==='rules'&&parts.length===2&&method==='PUT'){
        const saved=save?await save({id:parts[1],payload:copy(payload),current}):null;
        const row=saved||{...current,...copy(payload),id:parts[1],active:false,next_at:null,revision:(current?.revision||0)+1,
          runtime:{status:'paused',can_activate:true,reason:null},activity:current?.activity};
        const index=data.rule.findIndex(item=>item.id===row.id);
        if(index<0)data.rule.unshift(row);else data.rule[index]=row;
        return copy(row);
      }
      if(parts[0]==='rules'&&parts[2]==='activate'&&method==='POST'){
        const saved=activate?await activate({id:parts[1],payload:copy(payload),current}):null;
        const row=saved||{...current,active:payload.active,next_at:payload.active?now+600:null,
          runtime:{status:payload.active?(current.mode==='review'?'review':'ready'):'paused',can_activate:true,reason:null}};
        data.rule[data.rule.findIndex(item=>item.id===row.id)]=row;return copy(row);
      }
      if(parts[0]==='jobs'&&parts.length===2&&method==='POST'&&payload.action==='approve'){
        const job=data.jobs.find(row=>row.id===parts[1]);assert.ok(job);job.status='queued';return {ok:true};
      }
      throw Error('Unexpected request: '+method+' '+route);
    }});
  t.after(()=>{mounted.unmount();dom.window.close();assert.deepEqual(errors,[]);});await flush();
  return {window,document,host,data,calls,notices,jumps,mounted,
    get(selector){const node=host.querySelector(selector);assert.ok(node,'Missing '+selector);return node;},
    click(selector){this.get(selector).click();},
    input(selector,value){const node=this.get(selector);node.value=value;node.dispatchEvent(new window.Event('input',{bubbles:true}));return node;},
    change(selector,value){const node=this.get(selector);if(['radio','checkbox'].includes(node.type))node.checked=value;else node.value=value;node.dispatchEvent(new window.Event('change',{bubbles:true}));return node;},
    submit(active=false){const form=this.get('[data-form=rule]'),button=this.get(active?'#af-rule-save-active':'#af-rule-save-paused');form.dispatchEvent(new window.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:button}));},
    confirm(value='confirm'){this.get('#osAffiliateDialog').close(value);},
    writes(){return calls.filter(call=>call.method!=='GET');},
    prepare(){this.click('[data-act=new-rule]');this.input('#af-rule-name','Uma nova rotina');this.input('#af-rule-keyword','cafeteira');this.change('[name=destinations][value="'+DEST[0]+'"]',true);this.click('[data-rule-step=template]');},
  };
}

test('Lista usa entregas reais, separa buscas e expõe bloqueio sem alegar envio',async t=>{
  const data=workspace();data.rule.push(rule({id:SECOND,name:'Conexão a revisar',runtime:{status:'blocked',reason:'Confirme sua conexão do WhatsApp.',can_activate:false},activity:null}));
  const h=await harness(t,{data});
  assert.match(h.get('[data-automation-card="'+RULE+'"] [data-automation-kpi=published]').textContent,/1 enviadas hoje/);
  assert.match(h.get('[data-automation-card="'+RULE+'"] [data-automation-kpi=pending]').textContent,/1 na fila/);
  assert.match(h.get('[data-automation-card="'+RULE+'"]').textContent,/2 publicações preparadas/);
  assert.match(h.get('[data-automation-card="'+SECOND+'"]').textContent,/Confirme sua conexão/);
  assert.equal(h.writes().length,0);
  data.rule[1].active=false;h.click('[data-act=refresh-automations]');await flush();
  assert.equal(h.get('[data-automation-card="'+SECOND+'"] [data-act=start-rule]').disabled,true);
});

test('Editor inclui destinos normais verificados e quatro etapas sem desmontar campos',async t=>{
  const h=await harness(t);h.prepare();
  const form=h.get('[data-form=rule]'),name=h.get('#af-rule-name');
  for(const step of ['products','destinations','schedule','template']){
    h.click('[data-rule-step='+step+']');
    assert.equal(h.get('[data-form=rule]'),form);assert.equal(h.get('#af-rule-name'),name);assert.equal(name.value,'Uma nova rotina');
    assert.equal(h.get('[data-rule-panel='+step+']').hidden,false);
  }
  assert.equal(h.get('[name=destinations][value="'+DEST[1]+'"]').disabled,false);
  assert.equal(h.get('[name=destinations][value="'+DEST[3]+'"]').disabled,true);
  assert.equal(h.get('#af-rule-timezone').value,'America/Campo_Grande');
  assert.equal(h.get('[name=mode][value=automatic]').checked,true);
  assert.equal(h.writes().length,0);
});

test('Filtro de categorias e refresh preservam rascunho, foco, cursor, seleção e scroll',async t=>{
  const h=await harness(t);h.click('[data-act=edit-rule]');
  const form=h.get('[data-form=rule]'),name=h.input('#af-rule-name','Texto ainda em edição'),keyword=h.input('#af-rule-keyword','meu fone');
  h.input('[data-niche-filter]','bele');h.change('[name=niches][value=beleza]',true);
  assert.equal(h.get('[data-rule-niche=eletronicos]').hidden,true);
  assert.equal(h.get('[name=niches][value=eletronicos]').checked,true);
  name.focus();name.setSelectionRange(3,9);
  Object.defineProperty(h.window,'scrollY',{value:360,writable:true});
  h.click('[data-act=refresh-automations]');await flush();
  assert.equal(h.get('[data-form=rule]'),form);assert.equal(h.get('#af-rule-name'),name);assert.equal(h.document.activeElement,name);
  assert.equal(name.selectionStart,3);assert.equal(name.selectionEnd,9);assert.equal(name.value,'Texto ainda em edição');
  assert.equal(h.get('#af-rule-keyword'),keyword);assert.equal(keyword.value,'meu fone');
  assert.equal(h.get('[data-niche-filter]').value,'bele');assert.equal(h.get('[name=niches][value=beleza]').checked,true);
  assert.equal(h.get('#af-rule-timezone').value,'America/Cuiaba');assert.equal(h.window.scrollY,360);assert.equal(h.jumps.length,0);
});

test('Filtro de lista preserva o editor e o ID dos cards que continuam visíveis',async t=>{
  const data=workspace();data.rule.push(rule({id:SECOND,name:'Pausada',active:false,next_at:null,runtime:{status:'paused',can_activate:true}}));
  const h=await harness(t,{data});h.click('[data-act=edit-rule]');h.input('#af-rule-name','Edição preservada');
  const form=h.get('[data-form=rule]'),card=h.get('[data-automation-card="'+RULE+'"]');
  h.click('[data-rule-filter=active]');assert.equal(h.get('[data-automation-card="'+RULE+'"]'),card);
  assert.equal(h.host.querySelector('[data-automation-card="'+SECOND+'"]'),null);
  assert.equal(h.get('[data-form=rule]'),form);assert.equal(h.get('#af-rule-name').value,'Edição preservada');assert.equal(h.writes().length,0);
});

test('Salvar pausada envia somente PUT completo e preserva modo revisão e fuso existente',async t=>{
  const h=await harness(t);h.click('[data-act=edit-rule]');h.click('[data-rule-step=template]');
  h.change('[name=mode][value=review]',true);h.submit();await flush();
  const writes=h.writes();assert.equal(writes.length,1);assert.equal(writes[0].method,'PUT');
  assert.equal(writes[0].payload.mode,'review');assert.equal(writes[0].payload.timezone,'America/Cuiaba');
  assert.deepEqual(writes[0].payload.destinations,DEST.slice(0,2));
  assert.equal(writes[0].payload.interval,90);assert.equal(writes[0].payload.min_price,null);assert.deepEqual(writes[0].payload.blocked,[]);
  assert.equal(h.data.rule[0].active,false);assert.equal(h.host.querySelector('[data-form=rule]'),null);
  assert.equal(h.calls.filter(call=>call.path.endsWith('/state')).length,1);
});

test('Salvar e ativar exige submit explícito e não chama endpoints de envio',async t=>{
  const h=await harness(t);h.prepare();assert.equal(h.writes().length,0);h.submit(true);await flush();
  const writes=h.writes();assert.equal(writes.length,2);assert.equal(writes[0].method,'PUT');assert.equal(writes[1].method,'POST');
  assert.equal(writes[1].path,writes[0].path+'/activate');assert.deepEqual(writes[1].payload,{active:true});
  assert.equal(h.data.rule[0].active,true);assert.equal(h.host.querySelector('[data-form=rule]'),null);
  assert.ok(writes.every(call=>!call.path.includes('/send')&&!call.path.includes('/jobs')));
});

test('Salvar em revisão e ativar mantém aprovação manual como modo escolhido',async t=>{
  const h=await harness(t);h.prepare();h.change('[name=mode][value=review]',true);h.submit(true);await flush();
  assert.equal(h.writes()[0].payload.mode,'review');assert.equal(h.data.rule[0].runtime.status,'review');
  assert.match(h.get('[data-automation-card="'+h.data.rule[0].id+'"]').textContent,/Revisão antes do envio/);
});

test('Erro no save não perde formulário nem cursor e submit pendente não duplica PUT',async t=>{
  const waiting=deferred();const h=await harness(t,{save:()=>waiting.promise});h.prepare();
  const form=h.get('[data-form=rule]'),name=h.get('#af-rule-name');name.focus();name.setSelectionRange(2,7);
  h.submit(true);h.submit(true);await flush();assert.equal(h.writes().length,1);
  waiting.reject(Error('Não foi possível salvar agora.'));await flush();
  assert.equal(h.get('[data-form=rule]'),form);assert.equal(h.get('#af-rule-name'),name);assert.equal(name.value,'Uma nova rotina');
  assert.equal(h.document.activeElement,name);assert.equal(name.selectionStart,2);assert.equal(name.selectionEnd,7);
  assert.match(h.get('[data-rule-feedback]').textContent,/salvar agora/);assert.equal(h.writes().length,1);
});

test('Falha de ativação mantém regra pausada, edição e mesmo ID na nova tentativa',async t=>{
  let attempts=0;const h=await harness(t,{activate:()=>{if(++attempts===1)throw Error('Verifique o Telegram atual.');}});h.prepare();
  const form=h.get('[data-form=rule]');h.submit(true);await flush();
  assert.equal(h.get('[data-form=rule]'),form);assert.match(h.get('[data-rule-feedback]').textContent,/Configuração salva e pausada.*Telegram/);
  assert.equal(h.data.rule[0].active,false);const savedId=h.data.rule[0].id;
  assert.equal(h.get('#af-rule-name').value,'Uma nova rotina');h.submit(true);await flush();
  const puts=h.writes().filter(call=>call.method==='PUT');assert.equal(puts.length,2);assert.equal(puts[0].path,puts[1].path);
  assert.equal(h.data.rule.filter(row=>row.id===savedId).length,1);assert.equal(h.data.rule[0].active,true);
});

test('Mudanças digitadas durante save bem-sucedido continuam no editor como não salvas',async t=>{
  const waiting=deferred();const h=await harness(t,{save:async()=>{await waiting.promise;}});h.prepare();h.submit();await flush();
  const name=h.input('#af-rule-name','Mudança depois do clique');name.focus();name.setSelectionRange(4,12);
  waiting.resolve();await flush();assert.equal(h.get('#af-rule-name'),name);assert.equal(name.value,'Mudança depois do clique');
  assert.match(h.get('[data-rule-feedback]').textContent,/ainda estão em edição/);assert.equal(h.data.rule[0].name,'Uma nova rotina');
});

test('Pausar e ativar na lista exigem confirmar e mantêm edição e o card montado',async t=>{
  const h=await harness(t);h.click('[data-act=edit-rule]');const name=h.input('#af-rule-name','Rascunho independente');
  const card=h.get('[data-automation-card="'+RULE+'"]'),toggle=card.querySelector('[data-rule-toggle]');
  toggle.focus();h.click('[data-act=pause-rule]');await flush();
  h.confirm('cancel');await flush();assert.equal(h.writes().length,0);
  h.click('[data-act=pause-rule]');await flush();h.confirm();await flush();
  assert.equal(h.get('[data-automation-card="'+RULE+'"]'),card);assert.equal(h.get('#af-rule-name'),name);assert.equal(name.value,'Rascunho independente');
  assert.equal(h.get('[data-act=start-rule]'),toggle);assert.equal(h.document.activeElement,toggle);
  h.click('[data-act=start-rule]');await flush();h.confirm();await flush();
  assert.equal(h.get('[data-act=pause-rule]'),toggle);assert.equal(h.document.activeElement,toggle);
  assert.deepEqual(h.writes().map(call=>call.payload.active),[false,true]);assert.equal(h.get('#af-rule-name').value,'Rascunho independente');
});

test('Atualizar uma automação preserva a posição do card quando o conteúdo anterior muda de altura',async t=>{
  const pending=deferred(),h=await harness(t,{activate:()=>pending.promise});
  const card=h.get('[data-automation-card="'+RULE+'"]'),toggle=h.get('[data-rule-toggle="'+RULE+'"]');
  let scroll=500;
  Object.defineProperty(h.window,'scrollY',{get:()=>scroll,configurable:true});
  h.window.scrollTo=options=>{scroll=options.top;h.jumps.push(options);};
  card.getBoundingClientRect=()=>{
    const top=600-(h.host.querySelector('[data-act=start-rule]')?22:0)-scroll;
    return {top,bottom:top+450,height:450};
  };
  toggle.focus();h.click('[data-act=pause-rule]');await flush();h.confirm();await flush();
  const before=card.getBoundingClientRect().top;
  pending.resolve({...h.data.rule[0],active:false,next_at:null,runtime:{status:'paused',can_activate:true,reason:null}});await flush();
  assert.equal(h.get('[data-act=start-rule]'),toggle);assert.equal(h.document.activeElement,toggle);
  assert.equal(card.getBoundingClientRect().top,before);assert.equal(scroll,478);
  assert.equal(h.jumps.length,1);assert.equal(h.writes().length,1);
});

test('Validação local identifica a etapa e não consulta APIs com dados incompletos',async t=>{
  const h=await harness(t);h.click('[data-act=new-rule]');h.click('[data-rule-step=template]');h.submit(true);await flush();
  assert.match(h.get('[data-rule-feedback]').textContent,/Dê um nome/);assert.equal(h.get('[data-rule-panel=products]').hidden,false);
  h.input('#af-rule-name','Rotina');h.click('[data-rule-step=template]');h.submit(true);await flush();
  assert.match(h.get('[data-rule-feedback]').textContent,/categoria ou.*palavra-chave/);
  h.input('#af-rule-keyword','produto');h.click('[data-rule-step=template]');h.submit(true);await flush();
  assert.equal(h.get('[data-rule-panel=destinations]').hidden,false);assert.equal(h.writes().length,0);
});

test('Destinos que perderam acesso permanecem visíveis e podem ser removidos da seleção',async t=>{
  const data=workspace();data.rule[0].destinations.push(DEST[3]);const h=await harness(t,{data});h.click('[data-act=edit-rule]');h.click('[data-rule-step=destinations]');
  assert.equal(h.get('[name=destinations][value="'+DEST[3]+'"]').disabled,false);
  h.change('[name=destinations][value="'+DEST[3]+'"]',false);assert.equal(h.get('[name=destinations][value="'+DEST[3]+'"]').disabled,true);
  h.click('[data-rule-step=template]');h.submit();await flush();assert.deepEqual(h.writes()[0].payload.destinations,DEST.slice(0,2));
});

test('Seleção em massa não ultrapassa 5 destinos e filtros avançados preservam valores',async t=>{
  const data=workspace();for(let i=0;i<3;i++)data.destination.push({id:String(i+1).repeat(32),name:'Destino '+i,mode:'telegram',verified:true,eligible:true});
  data.automation_destinations=data.destination;
  const h=await harness(t,{data});h.prepare();assert.equal(h.host.querySelector('[data-rule-all-destinations]'),null);
  for(const row of data.destination.filter(row=>row.eligible))h.change('[name=destinations][value="'+row.id+'"]',true);
  h.submit();await flush();assert.match(h.get('[data-rule-feedback]').textContent,/até 5 destinos/);assert.equal(h.writes().length,0);
  h.change('[name=destinations][value="'+'3'.repeat(32)+'"]',false);
  h.click('[data-rule-step=products]');h.input('#af-rule-min_price','200');h.input('#af-rule-max_price','100');
  h.click('[data-rule-step=template]');h.submit();await flush();assert.equal(h.writes().length,0);
  assert.match(h.get('[data-rule-feedback]').textContent,/preço máximo/);
  assert.equal(h.get('[data-rule-panel=products]').hidden,false);
});

test('Resposta antiga de refresh não restaura regra anterior depois de salvar',async t=>{
  const pending=deferred(),original=workspace();
  const h=await harness(t,{data:original,getState:async(data,reads)=>reads===1?data:pending.promise});
  const previous=copy(original);
  h.click('[data-act=edit-rule]');h.input('#af-rule-name','Nome recém-salvo');h.click('[data-rule-step=template]');
  h.click('[data-act=refresh-automations]');await flush();h.submit();await flush();
  assert.match(h.get('[data-automation-card="'+RULE+'"]').textContent,/Nome recém-salvo/);
  pending.resolve(previous);await flush();
  assert.match(h.get('[data-automation-card="'+RULE+'"]').textContent,/Nome recém-salvo/);
  assert.equal(h.get('[data-automation-card="'+RULE+'"] [data-act=start-rule]').textContent.trim(),'Ativar');
});

test('Save pendente bloqueia mudança concorrente de ativação e mantém o destino explícito',async t=>{
  const pending=deferred();const h=await harness(t,{save:async()=>{await pending.promise;}});
  h.click('[data-act=edit-rule]');h.click('[data-rule-step=template]');h.submit(true);await flush();
  h.click('[data-act=pause-rule]');await flush();assert.equal(h.get('#osAffiliateDialog').open,false);
  assert.equal(h.writes().length,1);pending.resolve();await flush();
  assert.deepEqual(h.writes().map(call=>call.method),['PUT','POST']);
  assert.deepEqual(h.writes()[1].payload,{active:true});
});

test('Dia inteiro preserva a janela personalizada ao voltar e presets mantêm os demais campos',async t=>{
  const h=await harness(t);h.prepare();h.click('[data-rule-step=schedule]');
  h.input('#af-rule-opens','11:30');h.input('#af-rule-closes','18:45');
  h.change('[data-rule-all-day]',true);assert.equal(h.get('#af-rule-opens').value,'00:00');
  h.change('[data-rule-all-day]',false);assert.equal(h.get('#af-rule-opens').value,'11:30');assert.equal(h.get('#af-rule-closes').value,'18:45');
  h.click('[data-rule-interval="30"]');h.click('[data-rule-days=weekdays]');
  h.click('[data-rule-step=template]');h.submit();await flush();
  const payload=h.writes()[0].payload;
  assert.equal(payload.interval,30);assert.deepEqual(payload.days,[0,1,2,3,4]);assert.equal(payload.opens,'11:30');assert.equal(payload.closes,'18:45');
});

test('Fila de revisão recorrente permite aprovação explícita sem liberar o piloto global',async t=>{
  const data=workspace();data.sending=false;
  data.jobs=[{id:'1'.repeat(32),status:'approval',destination:DEST[0],due:now,changed:now,
    doc:{offer:{title:'Oferta em revisão'},rule:rule(),automation:{mode:'review'}}}];
  const h=await harness(t,{data});h.mounted.setView('fila');await flush();
  assert.equal(h.get('[data-act=approve]').textContent,'Aprovar envio');assert.equal(h.writes().length,0);
  h.click('[data-act=approve]');await flush();assert.match(h.get('#confirm-detail').textContent,/Oferta em revisão.*Telegram/);
  h.confirm('cancel');await flush();assert.equal(h.writes().length,0);
  h.click('[data-act=approve]');await flush();h.confirm();await flush();
  assert.equal(h.writes().length,1);assert.equal(h.writes()[0].path,API+'jobs/'+'1'.repeat(32));
  assert.deepEqual(h.writes()[0].payload,{action:'approve'});assert.equal(data.jobs[0].status,'queued');
  assert.equal(data.sending,false);assert.equal(h.host.querySelector('[data-act=approve]'),null);
});

test('Fila legada continua sem aprovação com flag desligada e mantém confirm_test no piloto autorizado',async t=>{
  for(const sending of [false,true]){
    const data=workspace();data.sending=sending;
    data.jobs=[{id:'2'.repeat(32),status:'approval',destination:DEST[0],due:now,changed:now,doc:{offer:{title:'Oferta legada'},rule:rule()}}];
    const h=await harness(t,{data});h.mounted.setView('fila');await flush();
    if(!sending){assert.equal(h.host.querySelector('[data-act=approve]'),null);assert.equal(h.writes().length,0);continue;}
    assert.equal(h.get('[data-act=approve]').textContent,'Aprovar teste');h.click('[data-act=approve]');await flush();h.confirm();await flush();
    assert.deepEqual(h.writes()[0].payload,{action:'approve',confirm_test:true});
  }
});

test('Histórico mantém recibo técnico fora da tela e preserva erro legível',async t=>{
  const data=workspace(),receipt="{'message_id': 'synthetic-internal-id', 'provider': 'whatsapp', 'metadata': {'long': true}}";
  data.jobs=[{id:'3'.repeat(32),status:'published',destination:DEST[1],due:now,changed:now,receipt,
    doc:{offer:{title:'Oferta confirmada'},rule:rule()}},
    {id:'4'.repeat(32),status:'failed',destination:DEST[0],due:now,changed:now,receipt,error:'A conexão precisa ser verificada.',
    doc:{offer:{title:'Oferta com falha'},rule:rule()}}];
  const h=await harness(t,{data});h.mounted.setView('fila');await flush();
  assert.match(h.get('#history-table').textContent,/Envio confirmado pelo WhatsApp/);
  assert.match(h.get('#history-table').textContent,/A conexão precisa ser verificada/);
  assert.doesNotMatch(h.get('#history-table').textContent,/synthetic-internal-id|metadata|message_id/);
  assert.equal(data.jobs[0].receipt,receipt);assert.equal(h.writes().length,0);
});
