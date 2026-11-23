import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { makeSuite, planState, flush, tenantA } from './harness.mjs';

function dentalPlan(overrides={}) {
  return planState('elite',{workspace_profile:'standard',layout_modes:['classic'],
    business_niche:'odontologia',revision:'synthetic-dental-v1',...overrides});
}

test('a verified dentist business gets a grouped Orion menu with only existing modules', async t => {
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan()});
  t.after(()=>h.close());await flush();
  const app=h.document.getElementById('appView');
  assert.ok(app.classList.contains('os-dental-workspace'));
  const rows=[...h.document.querySelectorAll('#osNav [data-os-route]')];
  const labels=Object.fromEntries(rows.map(el=>[el.dataset.osRoute,el.textContent.trim()]));
  assert.equal(labels.dashboard,'Recepção');
  assert.equal(labels.agenda,'Agenda');
  assert.equal(labels.clientes,'Pacientes');
  assert.equal(labels.servicos,undefined);
  assert.equal(labels['ajustes-odonto'],'Ajustes da clínica');
  assert.equal(labels.funcionarios,undefined);
  assert.equal(labels.inbox,'Conversas');
  assert.equal(rows.filter(el=>el.dataset.osRoute==='clientes').length,1);
  assert.equal(rows.filter(el=>el.dataset.osRoute==='ajustes-odonto').length,1);
  assert.ok([...h.document.querySelectorAll('#osNav .os-nav-section')].some(el=>el.textContent==='Operação da clínica'));
  assert.equal(h.document.querySelector('#osNav [data-os-route=odontograma]'),null);
  assert.equal(h.document.querySelector('#osNav [data-os-route=prontuarios]'),null);
  assert.equal(app.classList.contains('os-layout-app'),false);
  assert.equal(h.errors.length,0);
});

test('recepção usa o resumo administrativo, horários locais e ações de consulta e paciente',async t=>{
  const calls=[];
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan(),
    respond:(path)=>{calls.push(path);if(path.endsWith('/dental/reception/today'))
      return {day:'2026-11-22',timezone:'America/Campo_Grande',counts:{confirmed:7,completed:3,cancelled:1,no_show:0},
        upcoming:[{start_at:'2026-11-22T16:30:00Z',end_at:'2026-11-22T17:10:00Z',professional:'Profissional sintético'}]};
      return [];}});
  t.after(()=>h.close());await flush();
  const main=h.document.getElementById('osPageBody');
  assert.match(main.textContent,/Consultas de hoje/);
  assert.match(main.textContent,/Nova consulta/);
  assert.match(main.textContent,/Novo paciente/);
  assert.deepEqual([...main.querySelectorAll('#osReceptionCounts dd')].map(el=>el.textContent),['7','3','1','0']);
  assert.equal(main.querySelector('time').textContent,'12:30');
  assert.equal(main.querySelectorAll('[data-os-action=new-appointment]').length,1);
  assert.ok(calls.some(path=>path.endsWith('/dental/reception/today')));
  assert.ok(!calls.some(path=>path.includes('/records')||path.includes('/management/dashboard')));
  assert.ok(!main.textContent.includes('Sorriso Prime'));
  h.click('#osNav [data-os-route=clientes]');await flush();
  assert.match(h.document.querySelector('.os-client-heading h1').textContent,/Pacientes/);
  assert.ok(h.document.querySelector('[data-os-action=new-customer]'));
  assert.equal(h.document.querySelector('#osNav [data-os-route=clientes]')?.getAttribute('aria-current'),'page');
  assert.equal(h.errors.length,0);
});

test('dental procedures remain the normal writable service catalogue, not a second booking engine',async t=>{
  const h=await makeSuite({initialRoute:'servicos',permissions:dentalPlan()});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelector('.os-title-row h1')?.textContent,'Procedimentos');
  assert.match(h.document.getElementById('osPageBody').textContent,/PROCEDIMENTOS CADASTRADOS/);
  assert.ok(h.document.querySelector('[data-os-action=new-service]'));
  assert.match(h.document.getElementById('osPageBody').textContent,/Modelos de procedimentos/);
  h.click('[data-os-action=toggle-service-models]');await flush();
  const presets=[...h.document.querySelectorAll('[data-os-service-model]')];
  assert.equal(presets.length,6);
  assert.ok(presets.some(el=>el.textContent.includes('Avaliação odontológica')));
  assert.ok(presets.some(el=>el.textContent.includes('Profilaxia e limpeza')));
  assert.ok(h.document.getElementById('appView').classList.contains('os-dental-workspace'));
  assert.equal(h.errors.length,0);
});

test('business fields from an unverified tenant cannot opt in to clinical presentation',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:planState('elite',{business_niche:null}),
    bridge:{tenants:[{id:tenantA,name:'Empresa A',niche:'odontologia',role:'owner',timezone:'America/Sao_Paulo'}]}});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('appView').classList.contains('os-dental-workspace'),false);
  assert.equal(h.document.querySelector('#osNav [data-os-route=clientes]')?.textContent.trim(),'Clientes');
  assert.equal(h.errors.length,0);
});

test('non-dental workspace keeps original navigation and barbershop service preset layout',async t=>{
  const h=await makeSuite({initialRoute:'servicos',permissions:planState('elite',{business_niche:null})});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('appView').classList.contains('os-dental-workspace'),false);
  assert.equal(h.document.querySelector('#osNav [data-os-route=servicos]')?.textContent.trim(),'Serviços');
  assert.ok(h.document.querySelector('.os-service-models'));
  assert.match(h.document.querySelector('.os-title-row').textContent,/Serviços/);
});

test('switching quickly between dental routes leaves a consistent single selection and no errors',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan()});
  t.after(()=>h.close());
  await flush();
  const routes=['dashboard','agenda','clientes','ajustes-odonto','financeiro'];
  for(let i=0;i<96;i++)h.click('#osNav [data-os-route='+routes[i%routes.length]+']');
  await flush();await flush();
  await h.window.OrionSuite.open('clientes');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'clientes');
  assert.equal(h.document.querySelectorAll('#osNav [aria-current=page]').length,1);
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'clientes');
  assert.equal(h.errors.length,0);
});

test('dental styles use the existing Orion tokens and mobile breakpoints',()=>{
  const css=readFileSync(new URL('../app/static/orion-suite.css',import.meta.url),'utf8');
  assert.ok(!css.includes('.orion-suite-enabled.os-dental-workspace .os-service-models{display:none!important}'));
  assert.match(css,/\.os-service-models/);
  assert.match(css,/--os-primary:/);
  assert.match(css,/--os-font:/);
  assert.match(css,/@media\(max-width:767px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
});

test('o proprietário odontológico cadastra e renomeia uma cadeira nas configurações',async t=>{
  const id='10000000-0000-4000-8000-000000000003';
  const chairs=[];
  const h=await makeSuite({initialRoute:'configuracoes',
    permissions:dentalPlan({dental_clinical_enabled:true}),respond:(path,method,body)=>{
      if(path.endsWith('/dental/chair-settings')){
        if(method==='GET')return {items:[...chairs]};
        chairs.push({id,active:true,name:body.name});
        return chairs[0];
      }
      if(path.endsWith('/dental/chair-settings/'+id)&&method==='PATCH'){
        assert.equal(body.expected_name,chairs[0].name);
        chairs[0]={...chairs[0],name:body.name};
        return chairs[0];
      }
      return [];
    }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-settings="dental-chairs"]');await flush();
  assert.match(h.document.getElementById('osPageBody').textContent,/Nenhuma cadeira cadastrada/);
  h.click('[data-os-action=dental-chair-new]');
  h.fill('[data-os-form=dental-chair] input[name=name]','  Cadeira 1  ');
  h.submit('[data-os-form=dental-chair]');await flush();
  assert.equal(chairs[0].name,'Cadeira 1');
  assert.match(h.document.getElementById('osPageBody').textContent,/Cadeira 1/);
  h.click('[data-os-action=dental-chair-edit]');
  assert.equal(h.document.querySelector('[data-os-form=dental-chair] [name=name]').value,'Cadeira 1');
  h.fill('[data-os-form=dental-chair] input[name=name]','Sala de avaliação');
  h.submit('[data-os-form=dental-chair]');await flush();
  assert.match(h.document.getElementById('osPageBody').textContent,/Sala de avaliação/);
  assert.deepEqual(h.calls.filter(c=>c.path.includes('/dental/chair-settings')&&c.method!=='GET').map(c=>c.method),['POST','PATCH']);
  assert.equal(h.errors.length,0);
});

test('cadeiras não aparecem em outro nicho, manutenção, perfis da equipe ou sem habilitação clínica',async t=>{
  for(const options of [
    {permissions:planState('elite',{business_niche:'barbearia',dental_clinical_enabled:true})},
    {permissions:dentalPlan({dental_clinical_enabled:true}),role:'editor'},
    {permissions:dentalPlan({dental_clinical_enabled:true}),bridge:{user:{role:'admin',maintenance:{active:true}}}},
    {permissions:dentalPlan({dental_clinical_enabled:false})}
  ]){
    const h=await makeSuite({initialRoute:'configuracoes',...options});
    t.after(()=>h.close());await flush();
    assert.equal(h.document.querySelector('[data-os-settings="dental-chairs"]'),null);
    assert.equal(h.calls.some(c=>c.path.includes('/dental/chair-settings')),false);
  }
});

test('inativação exige confirmação e o filtro mostra ativas e inativas',async t=>{
  const chair={id:'10000000-0000-4000-8000-000000000005',name:'Cadeira de avaliação',active:true};
  const h=await makeSuite({initialRoute:'configuracoes',
    permissions:dentalPlan({dental_clinical_enabled:true}),respond:(path,method,body)=>{
      if(path.endsWith('/dental/chair-settings'))return {items:[{...chair}]};
      if(path.endsWith('/availability')&&method==='PATCH'){
        chair.active=body.active;
        return {...chair};
      }
      return [];
    }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-settings=dental-chairs]');await flush();
  h.click('[data-os-action=dental-chair-availability]');
  assert.equal(chair.active,true);
  h.submit('[data-os-form=dental-chair-availability]');await flush();
  assert.equal(chair.active,false);
  const filter=h.document.getElementById('osDentalChairFilter');
  filter.value='active';
  filter.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  assert.match(h.document.getElementById('osPageBody').textContent,/Nenhuma cadeira nesta situação/);
  const inactiveFilter=h.document.getElementById('osDentalChairFilter');
  inactiveFilter.value='inactive';
  inactiveFilter.dispatchEvent(new h.window.Event('change',{bubbles:true}));
  await flush();
  assert.match(h.document.querySelector('[data-os-action=dental-chair-availability]').getAttribute('aria-label'),/^Reativar/);
  h.click('[data-os-action=dental-chair-availability]');
  h.submit('[data-os-form=dental-chair-availability]');await flush();
  assert.equal(chair.active,true);
  assert.match(h.document.getElementById('osPageBody').textContent,/Nenhuma cadeira nesta situação/);
  assert.equal(h.errors.length,0);
});

test('modelos de anamnese preservam a ordem das perguntas, a situação e a versão',async t=>{
  let template;
  const h=await makeSuite({initialRoute:'configuracoes',
    permissions:dentalPlan({dental_clinical_enabled:true}),respond:(path,method,body)=>{
      if(path.endsWith('/dental/intake-templates')){
        if(method==='GET')return {items:template?[template]:[]};
        template={id:'10000000-0000-4000-8000-000000000007',version:1,...body};return template;
      }
      if(path.endsWith('/dental/intake-templates/'+template?.id)&&method==='PATCH'){
        assert.equal(body.expected_version,1);
        template={id:template.id,name:body.name,questions:body.questions,version:2};
        return template;
      }
      return [];
    }});
  t.after(()=>h.close());await flush();
  h.click('[data-os-settings=dental-intake]');await flush();
  assert.match(h.document.getElementById('osPageBody').textContent,/Nenhum modelo de anamnese/);
  h.click('[data-os-action=dental-intake-new]');
  h.fill('[data-os-form=dental-intake] input[name=name]','Modelo sintético');
  h.fill('#osIntakeQuestions [name=prompt]','Pergunta sintética A');
  h.click('[data-os-action=dental-intake-add-question]');
  h.fill('#osIntakeQuestions > :last-child [name=prompt]','Pergunta sintética B');
  h.click('#osIntakeQuestions > :last-child [name=question_active]');
  h.submit('[data-os-form=dental-intake]');await flush();
  assert.deepEqual(JSON.parse(JSON.stringify(template.questions)),
    [{prompt:'Pergunta sintética A',active:true},{prompt:'Pergunta sintética B',active:false}]);
  assert.match(h.document.getElementById('osPageBody').textContent,/1 pergunta ativa/);
  h.click('[data-os-action=dental-intake-edit]');
  h.fill('[data-os-form=dental-intake] input[name=name]','Modelo revisado');
  h.submit('[data-os-form=dental-intake]');await flush();
  assert.equal(template.version,2);
  assert.match(h.document.getElementById('osPageBody').textContent,/Modelo revisado/);
  assert.equal(h.errors.length,0);
});

test('recepção distingue falha de agenda vazia e retoma a consulta automaticamente',async t=>{
  let refresh,attempts=0;
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan(),configureWindow(w){
    const interval=w.setInterval.bind(w);
    w.setInterval=(func,ms,...args)=>{if(ms===30000){refresh=func;return 0;}return interval(func,ms,...args);};
    Object.defineProperty(w.document,'visibilityState',{value:'visible'});
  },respond(path){
    if(!path.endsWith('/dental/reception/today'))return [];
    attempts++;
    if(attempts===1||attempts===3)throw Error('Falha temporária');
    return {day:'2026-11-22',timezone:'America/Campo_Grande',counts:{confirmed:0,completed:0,cancelled:0,no_show:0},upcoming:[]};
  }});
  t.after(()=>h.close());await flush();
  assert.equal(h.document.querySelectorAll('#osReceptionCounts dd').length,0);
  assert.match(h.document.querySelector('#osReceptionNotice').textContent,/Não foi possível/);
  refresh();await flush();
  assert.equal(h.document.querySelectorAll('#osReceptionCounts dd').length,4);
  assert.match(h.document.querySelector('#osReceptionUpcoming').textContent,/Nenhuma consulta confirmada/);
  refresh();await flush();
  assert.equal(h.document.querySelectorAll('#osReceptionCounts dd').length,4);
  assert.match(h.document.querySelector('#osReceptionNotice').textContent,/última consulta/);
  h.click('[data-os-action=dashboard-agenda-full]');await flush();
  assert.equal(h.document.querySelector('#page-suite').dataset.osCurrentRoute,'agenda');
  refresh();await flush();assert.equal(attempts,3);
});

test('o menu de estoque pertence à odontologia habilitada e o endereço direto mantém a permissão', async t=>{
  for(const [plan,visible] of [[dentalPlan({dental_clinical_enabled:true}),true],
    [dentalPlan({dental_clinical_enabled:false}),false],
    [planState('elite',{business_niche:'barbearia',dental_clinical_enabled:true}),false]]){
    const h=await makeSuite({initialRoute:'dashboard',permissions:plan});
    t.after(()=>h.close());await flush();
    assert.equal(Boolean(h.document.querySelector('#osNav [data-os-route="estoque-odonto"]')),visible);
    if(!visible){
      const btn=h.document.createElement('button');btn.dataset.osRoute='estoque-odonto';
      h.document.getElementById('appView').append(btn);btn.click();await flush();
      assert.ok(h.calls.every(r=>!r.path.includes('/dental/supplies')));
      assert.equal(h.document.querySelector('#osDentalClinic'),null);
    }
  }
});


test('o caixa exibe o dinheiro pago e a receita pendente do período selecionado', async t=>{
  const requests=[];
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan(),respond(path){
    if(path.includes('/management/finance/summary?')){
      const q=new URL(path,'https://orion.test').searchParams;
      const start=q.get('start_date'),end=q.get('end_date');requests.push({start,end});
      return {period:{start_date:start,end_date:end},summary:{income_cents:12345,expense_cents:14800,
        pending_income_cents:2000,balance_cents:-2455}};
    }
    return [];
  }});
  t.after(()=>h.close());await flush();
  const amounts=()=>[...h.document.querySelectorAll('#osCashAmounts dd')].map(x=>x.textContent.replace(/\s/g,' '));
  assert.deepEqual(amounts(),['R$ 123,45','R$ 148,00','-R$ 24,55','R$ 20,00']);
  assert.equal(requests[0].start,requests[0].end);
  h.click('[data-cash-range=month]');await flush();
  assert.ok(requests.at(-1).start.endsWith('-01'));
  assert.equal(h.document.querySelector('[data-cash-range=month]').getAttribute('aria-pressed'),'true');
  assert.equal(h.document.querySelectorAll('#osPageBody [data-os-route=financeiro]').length,1);
});

test('financeiro restrito não é consultado pela recepção',async t=>{
  const plan=dentalPlan();plan.permissions['finance.basic']=false;
  const h=await makeSuite({initialRoute:'dashboard',permissions:plan});t.after(()=>h.close());await flush();
  assert.equal(h.document.getElementById('osCashAmounts'),null);
  assert.ok(!h.calls.some(c=>c.path.includes('/management/finance/summary')));
});

test('resposta mensal atrasada não substitui o caixa do dia selecionado', async t=>{
  let finishMonth;
  let dayReads=0;
  const h=await makeSuite({
    initialRoute:'dashboard',permissions:dentalPlan(),
    configureWindow(w){
      const BrowserDate=w.Date;
      w.Date=class extends BrowserDate{
        constructor(...args){super(...(args.length?args:['2026-11-22T22:00:00Z']));}
      };
    },
    respond(path){
      if(!path.includes('/management/finance/summary?'))return [];
      const query=new URL(path,'https://orion.test').searchParams;
      const period={start_date:query.get('start_date'),end_date:query.get('end_date')};
      function report(cents){
        return {period,summary:{income_cents:cents,expense_cents:0,balance_cents:cents,pending_income_cents:0}};
      }
      if(period.start_date===period.end_date)return report(++dayReads*10000);
      return new Promise(resolve=>{finishMonth=()=>resolve(report(999900));});
    }
  });
  t.after(()=>h.close());
  await flush();
  h.click('[data-cash-range=month]');
  await flush();
  assert.equal(typeof finishMonth,'function');
  assert.equal(h.document.querySelector('#osCashAmounts dd'),null);
  h.click('[data-cash-range=day]');
  await flush();
  finishMonth();
  await flush();
  assert.equal(h.document.querySelector('#osCashAmounts dd').textContent.replace(/\s/g,' '),'R$ 200,00');
  assert.equal(h.document.querySelector('[data-cash-range=day]').getAttribute('aria-pressed'),'true');
  assert.equal(h.document.querySelector('#osCashAmounts').getAttribute('aria-busy'),'false');
});

test('falha de caixa preserva a agenda e não apresenta dinheiro fictício',async t=>{
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan(),respond(path){
    if(path.endsWith('/dental/reception/today'))return {day:'2026-11-22',timezone:'America/Campo_Grande',
      counts:{confirmed:2,completed:0,cancelled:0,no_show:0},upcoming:[]};
    if(path.includes('/finance/summary'))throw Error('Caixa temporariamente indisponível');
    return [];
  }});
  t.after(()=>h.close());await flush();
  assert.match(h.document.getElementById('osCashNotice').textContent,/Não foi possível/);
  assert.equal(h.document.querySelectorAll('#osCashAmounts dd').length,0);
  assert.equal(h.document.querySelector('#osReceptionCounts dd').textContent,'2');
});


test('ajustes da clínica abre os campos existentes e conserva um único destino selecionado', async t=>{
  const h=await makeSuite({initialRoute:'ajustes-odonto',permissions:dentalPlan({dental_clinical_enabled:true})});
  t.after(()=>h.close());await flush();
  const main=()=>h.document.querySelector('#page-suite');
  assert.equal(main().querySelector('h1').textContent,'Ajustes da clínica');
  assert.equal(h.document.querySelector('#osNav [data-os-route=clinica]').textContent,'Prontuários');
  assert.equal(h.document.querySelector('#osNav [data-os-route="pacientes-odonto"]').textContent,'Pacientes');
  assert.equal(h.document.querySelector('#osNav [data-os-route="proteses-odonto"]').textContent,'Controle de próteses');
  assert.ok(main().querySelector('[data-os-route=clientes]').textContent.includes('Contatos da agenda'));
  assert.equal(h.calls.some(c=>c.path.includes('/dental/patients')||c.path.includes('/dental/access')),false);
  h.click('#osPageBody [data-os-route=servicos]');await flush();
  assert.equal(main().querySelector('h1').textContent,'Procedimentos');
  assert.equal(h.document.querySelector('#osNav [aria-current=page]').dataset.osRoute,'ajustes-odonto');
  h.click('#osNav [data-os-route="ajustes-odonto"]');await flush();
  h.click('[data-os-dental-setting="dental-chairs"]');await flush();
  assert.ok(h.calls.some(c=>c.path.endsWith('/dental/chair-settings')));
  h.click('#osNav [data-os-route="ajustes-odonto"]');await flush();
  h.click('[data-os-dental-setting="dental-access"]');await flush();
  assert.equal(main().querySelector('h1').textContent,'Acessos clínicos');
  assert.equal(h.errors.length,0);
});

test('ajustes não oferece funções privativas à recepção e a barbearia não possui essa rota',async t=>{
  const editor=await makeSuite({role:'editor',initialRoute:'ajustes-odonto',permissions:dentalPlan({dental_clinical_enabled:true})});
  t.after(()=>editor.close());await flush();
  for(const tab of ['dental-chairs','dental-intake','dental-access'])assert.equal(editor.document.querySelector('[data-os-dental-setting="'+tab+'"]'),null);
  const barber=await makeSuite({initialRoute:'ajustes-odonto',permissions:planState('essencial',{business_niche:'barbearia'})});
  t.after(()=>barber.close());await flush();
  assert.equal(barber.document.querySelector('#osNav [data-os-route="ajustes-odonto"]'),null);
  assert.notEqual(barber.document.querySelector('#page-suite').dataset.osCurrentRoute,'ajustes-odonto');
  assert.equal(barber.calls.some(c=>c.path.includes('/dental/')),false);
});

test('ocupação da recepção usa o expediente real e recupera a leitura sem bloquear a agenda',async t=>{
  let refresh,requests=0;
  const day=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  const counts={confirmed:3,completed:0,cancelled:0,no_show:0};
  const samples=[
    {active_chairs:2,hours_status:'open',capacity_seconds:57600,reserved_seconds:18900},
    {active_chairs:2,hours_status:'open',capacity_seconds:50,reserved_seconds:75},
    {active_chairs:2,hours_status:'unconfigured',capacity_seconds:null,reserved_seconds:null},
    {active_chairs:2,hours_status:'closed',capacity_seconds:0,reserved_seconds:0},
    {active_chairs:0,hours_status:'open',capacity_seconds:0,reserved_seconds:0},
    {active_chairs:2,hours_status:'open',capacity_seconds:57600,reserved_seconds:0}
  ];
  const h=await makeSuite({initialRoute:'dashboard',permissions:dentalPlan({dental_clinical_enabled:true}),
    configureWindow(w){
      const original=w.setInterval.bind(w);
      w.setInterval=(fn,ms,...args)=>{if(ms===30000){refresh=fn;return 0;}return original(fn,ms,...args);};
      Object.defineProperty(w.document,'visibilityState',{value:'visible'});
    },respond(path){
      if(path.endsWith('/dental/reception/today'))return {day,timezone:'America/Campo_Grande',counts,upcoming:[]};
      if(path.endsWith('/dental/reception/chair-occupancy'))return {day,...samples[requests++]};
      return [];
    }});
  t.after(()=>h.close());await flush();
  const card=h.document.querySelector('#osChairOccupancy'),notice=h.document.querySelector('#osChairNotice');
  assert.equal(card.querySelector('strong').textContent,'32,8%');
  assert.equal(card.querySelector('progress').value,18900);
  assert.match(card.textContent,/5 h 15 min de 16 h/);
  refresh();await flush();assert.match(notice.textContent,/última leitura/);
  assert.equal(card.querySelector('progress').value,18900);
  assert.equal(h.document.querySelector('#osReceptionCounts dd').textContent,'3');
  for(const message of [/Cadastre o expediente/,/Sem expediente/,/Nenhuma cadeira ativa/]){
    refresh();await flush();assert.match(card.textContent,message);assert.equal(card.querySelector('progress'),null);
    assert.equal(notice.textContent,'');
  }
  refresh();await flush();assert.equal(card.querySelector('progress').value,0);
  h.click('[data-os-action=dashboard-agenda-full]');await flush();refresh();await flush();
  assert.equal(requests,6);
});

test('o painel sem cadeiras habilitadas não consulta nem exibe a métrica',async t=>{
  for(const plan of [dentalPlan({dental_clinical_enabled:false}),planState('essencial',{business_niche:'barbearia'})]){
    const h=await makeSuite({initialRoute:'dashboard',permissions:plan});
    t.after(()=>h.close());await flush();
    assert.equal(h.document.querySelector('#osChairOccupancy'),null);
    assert.equal(h.calls.some(call=>call.path.endsWith('/chair-occupancy')),false);
  }
});

test('ajustes da clínica salvam a etapa de vendas e a recarregam ao voltar',async t=>{
  const stages=[{id:'open',name:'Em aberto',custom:false},{id:'in_progress',name:'Em andamento',custom:false},
    {id:'won',name:'Ganha',custom:false},{id:'lost',name:'Perdida',custom:false}];
  let creates=0;
  const h=await makeSuite({initialRoute:'ajustes-odonto',permissions:dentalPlan({dental_clinical_enabled:true}),respond(path,method,payload){
    if(!path.endsWith('/dental/sales/stages'))return [];
    if(method==='POST'){
      assert.match(payload.id,/^[a-f\d-]{36}$/i);assert.equal(payload.name,'Aguardando guia');creates++;
      stages.splice(2,0,{id:payload.id,name:payload.name,custom:true});
    }
    return {items:stages.map(s=>({...s}))};
  }});t.after(()=>h.close());await flush();
  const open=()=>h.click('[data-os-dental-setting=dental-sales-stages]');
  open();await flush();
  assert.equal(h.document.querySelectorAll('.os-sales-stages li').length,4);
  h.click('[data-os-action=dental-sales-stage-new]');h.fill('[data-os-form=dental-sales-stage] input[name=name]','Aguardando guia');
  h.submit('[data-os-form=dental-sales-stage]');await flush();
  assert.equal(creates,1);assert.match(h.document.querySelector('.os-sales-stages').textContent,/Aguardando guiaPersonalizada/);
  h.click('#osNav [data-os-route=ajustes-odonto]');await flush();open();await flush();
  assert.equal(h.document.querySelectorAll('.os-sales-stages li').length,5);
  const editor=await makeSuite({role:'editor',initialRoute:'ajustes-odonto',permissions:dentalPlan({dental_clinical_enabled:true})});
  t.after(()=>editor.close());await flush();
  assert.equal(editor.document.querySelector('[data-os-dental-setting=dental-sales-stages]'),null);
});
