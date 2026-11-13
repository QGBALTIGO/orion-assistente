import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync('app/static/orion-suite.js','utf8');
const helpers=source.slice(source.indexOf('  const agendaPeriod='),source.indexOf('  function agendaFrame('));
const renderer=source.slice(source.indexOf('  function agendaRangeView('),source.indexOf('  function renderAgenda(){'));
const focused=source.slice(source.indexOf('  function agendaFocusQuery('),source.indexOf('  async function applyAgendaFilters(){'));
assert.ok(helpers.length>100&&renderer.length>2000&&focused.length>1000);

function context(day,period,whole=false,focus=''){
  return {
    state:{route:'agenda',filters:{agendaDay:day,agendaPeriod:period,agendaRangeList:whole,agendaFocusDay:focus,agendaFocusOffset:0,agendaProfessional:'',agendaStatus:'all',agendaQuery:''},data:{},agendaFocusRead:0},
    dateKey(value){return value?new Date(value).toISOString().slice(0,10):'2026-11-13';},
    dayDate(day){return new Date((day||'2026-11-13')+'T12:00:00Z');},
    shiftDay(day,n){const x=new Date(day+'T12:00:00Z');x.setUTCDate(x.getUTCDate()+n);return x.toISOString().slice(0,10);},
    fmtDate(day){return day.slice(8,10)+'/'+day.slice(5,7)+'/'+day.slice(0,4);},
    longDay(day){return 'sexta-feira, '+day;},
    esc(value){return String(value);},
    number(value){return Number(value)||0;},
    appointmentRow(item){return '<article data-id="'+item.id+'"></article>';},
    agendaPagination(){return '<nav data-testid="pages">Pages</nav>';},
    agendaEmpty(){return '<p>Sem agendamentos</p>';},
    icon(){return '';},
    button(label,action){return '<button data-os-action="'+action+'">'+label+'</button>';},
    loading(){return '<p>Carregando...</p>';},
    Date,Intl,
  };
}
function inspect(day,period,whole=false,focus='',list=[]){
  const ctx=context(day,period,whole,focus);
  vm.runInNewContext(helpers+renderer+`
    globalThis.testResult={
      bounds:agendaBounds(),next:agendaShiftMonth(state.filters.agendaDay,1),
      prev:agendaShiftMonth(state.filters.agendaDay,-1),
      selected:agendaSelectedDay(),
      html:agendaRangeView(globalThis.fixtures||[],{
        days:[{day:'2026-11-13',total:14,confirmed:12,completed:1,cancelled:1,no_show:0,hold:0}],
        total:14,offset:0,has_more:true
      })
    };`,Object.assign(ctx,{fixtures:list}));
  return ctx.testResult;
}

test('compact month has 30 selectable days and inline selected-day details',()=>{
  const result=inspect('2026-11-13','month');
  assert.deepEqual(JSON.parse(JSON.stringify(result.bounds)),{start:'2026-11-01',end:'2026-11-30'});
  assert.equal((result.html.match(/data-os-open-day=/g)||[]).length,30);
  assert.equal((result.html.match(/aria-pressed="true"/g)||[]).length,1);
  assert.match(result.html,/os-agenda-range-layout/);
  assert.match(result.html,/osAgendaRangeFocusBody/);
  assert.match(result.html,/14 agendamentos/);
  assert.match(result.html,/Ver mês em lista/);
  assert.match(result.html,/Abrir dia/);
  assert.equal(result.next,'2026-12-01');
  assert.equal(result.prev,'2026-10-01');
});
test('week is 7 days in one strip and whole-week list is available',()=>{
  const items=[
    {id:'appointment-one',start_at:'2026-11-09T13:00:00Z'},
    {id:'appointment-two',start_at:'2026-11-13T13:00:00Z'}
  ];
  const result=inspect('2026-11-13','week',true,'',items);
  assert.deepEqual(JSON.parse(JSON.stringify(result.bounds)),{start:'2026-11-08',end:'2026-11-14'});
  assert.equal((result.html.match(/data-os-open-day=/g)||[]).length,7);
  assert.match(result.html,/os-agenda-week-overview/);
  assert.match(result.html,/appointment-one/);
  assert.match(result.html,/appointment-two/);
  assert.match(result.html,/Ver dia selecionado/);
  assert.doesNotMatch(result.html,/osAgendaRangeFocusBody/);
});
test('tapping week day focuses the day without leaving the week',()=>{
  const result=inspect('2026-11-13','week',false,'2026-11-11');
  assert.equal(result.selected,'2026-11-11');
  assert.match(result.html,/osAgendaRangeFocusBody/);
  assert.match(result.html,/Ver semana completa/);
  assert.equal((result.html.match(/aria-pressed="true"/g)||[]).length,1);
});
test('leap year, year crossover and month list',()=>{
  const leap=inspect('2028-02-13','month');
  const cross=inspect('2027-01-01','week');
  assert.equal(leap.bounds.end,'2028-02-29');
  assert.equal((leap.html.match(/data-os-open-day=/g)||[]).length,29);
  assert.deepEqual(JSON.parse(JSON.stringify(cross.bounds)),{start:'2026-12-27',end:'2027-01-02'});
  const expanded=inspect('2026-11-13','month',true,'',[{id:'all-one',start_at:'2026-11-13T12:00:00Z'}]);
  assert.match(expanded.html,/all-one/);
  assert.match(expanded.html,/Ver dia selecionado/);
  assert.match(expanded.html,/data-testid="pages"/);
  assert.doesNotMatch(expanded.html,/osAgendaRangeFocusBody/);
});
test('focused-day request uses same search, status and professional filters without loading the whole month',async()=>{
  const ctx=context('2026-11-13','month',false,'2026-11-13');
  ctx.state.filters.agendaStatus='confirmed';
  ctx.state.filters.agendaProfessional='prof-test';
  ctx.state.filters.agendaQuery='Ana';
  ctx.apiPath=path=>'/api/v1/tenants/test'+path;
  ctx.query=params=>new URLSearchParams(Object.entries(params).filter(([,v])=>v!==''&&v!=null)).toString();
  ctx.$=id=>id==='osAgendaRangeFocusBody'?target:null;
  ctx.current=()=>true;
  ctx.context=()=>({});
  let called='';
  const target={innerHTML:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];}};
  ctx.request=async url=>{
    called=url;
    return {items:[{id:'selected-one',start_at:'2026-11-13T13:00:00Z'}],total:1,offset:0,limit:80,has_more:false};
  };
  vm.runInNewContext(helpers+focused+'globalThis.fetchDay=loadAgendaFocus;',ctx);
  await ctx.fetchDay();
  assert.match(called,/day=2026-11-13/);
  assert.match(called,/q=Ana/);
  assert.match(called,/status=confirmed/);
  assert.match(called,/professional_id=prof-test/);
  assert.doesNotMatch(called,/start_date=/);
  assert.match(target.innerHTML,/selected-one/);
  assert.equal(target.attrs['aria-busy'],undefined);
});
test('selected day request ignores stale results after selecting another day',async()=>{
  const ctx=context('2026-11-13','month',false,'2026-11-13');
  ctx.apiPath=path=>path;
  ctx.query=params=>new URLSearchParams(Object.entries(params).filter(([,v])=>v!==''&&v!=null)).toString();
  ctx.current=()=>true;ctx.context=()=>({});
  let firstResolve;
  const target={innerHTML:'',setAttribute(){},removeAttribute(){}};
  ctx.$=id=>id==='osAgendaRangeFocusBody'?target:null;
  let calls=0;
  ctx.request=()=>{
    calls++;
    if(calls===1)return new Promise(resolve=>{firstResolve=resolve;});
    return Promise.resolve({items:[{id:'newer'}],total:1,limit:80,offset:0,has_more:false});
  };
  vm.runInNewContext(helpers+focused+'globalThis.fetchDay=loadAgendaFocus;',ctx);
  const old=ctx.fetchDay();
  ctx.state.filters.agendaDay='2026-11-14';
  ctx.state.filters.agendaFocusDay='2026-11-14';
  await ctx.fetchDay();
  firstResolve({items:[{id:'old'}],total:1,limit:80,offset:0,has_more:false});
  await old;
  assert.match(target.innerHTML,/newer/);
  assert.doesNotMatch(target.innerHTML,/data-id="old"/);
});
