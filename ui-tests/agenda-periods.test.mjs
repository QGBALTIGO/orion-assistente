import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import vm from 'node:vm';
import { JSDOM } from 'jsdom';

const source=fs.readFileSync('app/static/orion-suite.js','utf8');
const helpers=source.slice(source.indexOf('  const agendaPeriod='),source.indexOf('  function agendaFrame('));
const renderer=source.slice(source.indexOf('  function agendaTeamRangeView('),source.indexOf('  function renderAgenda(){'));
const focused=source.slice(source.indexOf('  function agendaFocusQuery('),source.indexOf('  async function applyAgendaFilters(){'));
assert.ok(helpers.length>100&&renderer.length>2000&&focused.length>1000);

function context(day,period,whole=false,focus=''){
  return {
    state:{route:'agenda',filters:{agendaDay:day,agendaPeriod:period,agendaRangeList:whole,agendaMode:whole?'list':'calendar',agendaFocusDay:focus,agendaFocusOffset:0,agendaProfessional:'',agendaStatus:'all',agendaQuery:''},data:{},agendaFocusRead:0},
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
  assert.doesNotMatch(result.html,/data-os-action="agenda-range-list"/);
  assert.match(result.html,/data-os-action="agenda-prev"/);
  assert.match(result.html,/data-os-action="agenda-next"/);
  assert.match(result.html,/Mês anterior/);
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
  const document = JSDOM.fragment(result.html);
  const wrapper = document.querySelector('.os-agenda-week-overview-wrapper');
  const strip = wrapper.querySelector(':scope > .os-agenda-week-days');
  assert.deepEqual([...strip.children].map(button => button.dataset.osOpenDay),
    ['2026-11-08','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-14']);
  assert.equal(strip.querySelector('[data-os-action=agenda-prev]'),null);
  assert.ok(wrapper.querySelector(':scope > .os-agenda-range-heading [data-os-action=agenda-next]'));
  const css=fs.readFileSync('app/static/orion-suite.css','utf8');
  assert.match(css,/\.os-agenda-week-days\s*\{[^}]*grid-template-columns:repeat\(7,minmax\(0,1fr\)\)/);
  assert.doesNotMatch(css,/\.os-agenda-week-overview(?:[\s{,>:.]|$)/);
  assert.match(result.html,/data-os-action="agenda-prev"/);
  assert.match(result.html,/data-os-action="agenda-next"/);
  assert.match(result.html,/Próxima semana/);
  assert.match(result.html,/appointment-one/);
  assert.match(result.html,/appointment-two/);
  assert.match(result.html,/Agendamentos da semana/);
  assert.doesNotMatch(result.html,/osAgendaRangeFocusBody/);
});
test('tapping week day focuses the day without leaving the week',()=>{
  const result=inspect('2026-11-13','week',false,'2026-11-11');
  assert.equal(result.selected,'2026-11-11');
  assert.match(result.html,/osAgendaRangeFocusBody/);
  assert.match(result.html,/Dia selecionado/);
  assert.equal((result.html.match(/aria-pressed="true"/g)||[]).length,1);
});
test('weekly geometry fits all seven days and both arrows at phone, tablet and desktop widths',
  {skip:!process.env.ORION_LAYOUT_BROWSER,timeout:35000},()=>{
  const css=fs.readFileSync('app/static/orion-suite.css','utf8');
  const fixtures=[['2026-11-22',false],['2026-12-27',true]].flatMap(([start,yearEnd])=>
    [320,375,430,768,1280].flatMap(width=>['list','calendar','professional'].map(mode=>{
      const ctx=context(start,'week');
      ctx.state.filters.agendaMode=mode;
      ctx.state.filters.agendaDay=ctx.shiftDay(start,2);
      ctx.counts=Array.from({length:7},(_,i)=>({day:ctx.shiftDay(start,i),total:i===3?0:240,
        ...(i===3?{}:{confirmed:112,completed:78,cancelled:35,no_show:12,hold:3})}));
      vm.runInNewContext(helpers+renderer+
        'globalThis.fixture=agendaRangeView([],{days:counts});',ctx);
      return '<main class="os-main case" data-os-current-route="agenda" style="width:'+(width-32)+
        'px" data-case="'+width+'px '+mode+(yearEnd?' (virada do ano)':'')+'">'+ctx.fixture+'</main>';
    })));
  function measure(){
    const failures=[];
    for(const main of document.querySelectorAll('.case')){
      const heading=main.querySelector('.os-agenda-range-heading');
      const strip=main.querySelector('[aria-label="Dias da semana"]');
      const cells=[...strip.children],boxes=cells.map(el=>el.getBoundingClientRect());
      const board=strip.getBoundingClientRect(),outer=main.getBoundingClientRect();
      const title=heading.querySelector('h2').getBoundingClientRect();
      const nav=heading.querySelector('.os-agenda-period-nav').getBoundingClientRect();
      const buttons=[...heading.querySelectorAll('button')].map(el=>el.getBoundingClientRect());
      const fullWidth=Math.abs(board.width-outer.width)<1;
      const headerFits=heading.getBoundingClientRect().bottom<=board.top+1&&
        title.right<=nav.left+1&&nav.right<=outer.right+1&&buttons.length===2&&
        buttons.every(rect=>rect.width>=32&&rect.height>=32)&&buttons[0].right<=buttons[1].left;
      const daysFit=boxes.length===7&&boxes.every((rect,i)=>
        rect.width>=32&&rect.height>=44&&Math.abs(rect.top-boxes[0].top)<1&&
        rect.left>=board.left&&rect.right<=board.right+1&&(!i||rect.left>=boxes[i-1].right));
      const markersFit=cells.every((cell,i)=>[...cell.querySelectorAll('.os-agenda-range-dots i')].every(dot=>{
        const box=dot.getBoundingClientRect();
        return box.left>=boxes[i].left-1&&box.right<=boxes[i].right+1;
      }));
      if(!(fullWidth&&headerFits&&daysFit&&markersFit))
        failures.push({case:main.dataset.case,fullWidth,headerFits,daysFit,markersFit});
    }
    document.getElementById('test-result').textContent=JSON.stringify({
      failures,count:document.querySelectorAll('.case').length});
  }
  const temporary=fs.mkdtempSync(join(tmpdir(),'orion-week-layout-'));
  try{
    const file=join(temporary,'fixture.html');
    fs.writeFileSync(file,'<!doctype html><html lang="pt-BR"><meta charset="utf-8"><style>'+css+
      '\n*{box-sizing:border-box}body{margin:16px}</style><pre id="test-result"></pre>'+
      fixtures.join('')+'<script>('+measure.toString()+')()</script></html>');
    const output=execFileSync(process.env.ORION_LAYOUT_BROWSER,
      ['--headless','--no-sandbox','--disable-dev-shm-usage','--disable-gpu',
        '--disable-background-networking','--disable-extensions','--no-first-run',
        '--user-data-dir='+join(temporary,'browser-profile'),'--dump-dom',pathToFileURL(file).href],
      {encoding:'utf8',timeout:30000,maxBuffer:2*1024*1024});
    const match=output.match(/<pre id="test-result">([^<]+)<[/]pre>/);
    assert.ok(match,'o navegador precisa calcular a geometria da grade');
    const result=JSON.parse(match[1]);
    assert.equal(result.count,30);
    assert.deepEqual(result.failures,[]);
  }finally{
    fs.rmSync(temporary,{recursive:true,force:true});
  }
});
test('leap year, year crossover and month list',()=>{
  const leap=inspect('2028-02-13','month');
  const cross=inspect('2027-01-01','week');
  assert.equal(leap.bounds.end,'2028-02-29');
  assert.equal((leap.html.match(/data-os-open-day=/g)||[]).length,29);
  assert.deepEqual(JSON.parse(JSON.stringify(cross.bounds)),{start:'2026-12-27',end:'2027-01-02'});
  const expanded=inspect('2026-11-13','month',true,'',[{id:'all-one',start_at:'2026-11-13T12:00:00Z'}]);
  assert.match(expanded.html,/all-one/);
  assert.match(expanded.html,/Agendamentos do mês/);
  assert.match(expanded.html,/data-os-action="agenda-prev"/);
  assert.doesNotMatch(expanded.html,/os-agenda-month-calendar/);
  assert.doesNotMatch(expanded.html,/data-os-action="agenda-range-list"/);
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

test('three view tabs are shared and refresh is beside tabs in all agenda periods',()=>{
  const begin=source.indexOf('function agendaFrame(');
  const end=source.indexOf('function syncAgendaControls()',begin);
  assert.ok(begin>=0&&end>begin);
  const frame=source.slice(begin,end);
  assert.match(frame,/Visualização da agenda/);
  assert.match(frame,/\['list','list','Lista'\]/);
  assert.match(frame,/\['calendar','calendar','Calendário'\]/);
  assert.match(frame,/\['professional','users','Equipe'\]/);
  const tabs=frame.indexOf('os-agenda-view-row');
  const refresh=frame.indexOf('data-os-action="agenda-reload"');
  const count=frame.indexOf('id="osAgendaCount"');
  const clear=frame.indexOf('id="osAgendaClear"');
  assert.ok(tabs>=0&&tabs<refresh&&refresh<count&&count<clear);
  assert.equal((frame.match(/data-os-action="agenda-reload"/g)||[]).length,1);
  const css=fs.readFileSync('app/static/orion-suite.css','utf8');
  assert.match(css,/\.os-agenda-view-row>\.os-agenda-refresh/);
});

test('week and month team view groups real appointments by professional',()=>{
  const ctx=context('2026-11-13','week',true,'');
  ctx.state.filters.agendaMode='professional';
  ctx.appointmentProfessional=a=>a.professional_name;
  ctx.cleanColor=value=>value||'#2e7bf6';
  ctx.fixtures=[
    {id:'b1',professional_id:'a',professional_name:'Ana',start_at:'2026-11-09T13:00:00Z'},
    {id:'b2',professional_id:'b',professional_name:'Bruno',start_at:'2026-11-10T13:00:00Z'}
  ];
  vm.runInNewContext(helpers+renderer+`
    globalThis.teamHTML=agendaRangeView(fixtures,{total:2,days:[],offset:0,limit:100,has_more:false});`,ctx);
  assert.match(ctx.teamHTML,/Por profissional/);
  assert.match(ctx.teamHTML,/Ana/);
  assert.match(ctx.teamHTML,/Bruno/);
  assert.match(ctx.teamHTML,/b1/);
  assert.match(ctx.teamHTML,/b2/);
  assert.match(ctx.teamHTML,/data-testid="pages"/);
  assert.doesNotMatch(ctx.teamHTML,/osAgendaRangeFocusBody/);
});

test('month modes preserve navigation and show one clear purpose for each mode',()=>{
  const month=inspect('2026-11-13','month',false,'2026-11-13').html;
  const list=inspect('2026-11-13','month',true,'',[{id:'booked',start_at:'2026-11-13T12:00:00Z'}]).html;
  assert.match(month,/os-agenda-month-calendar/);
  assert.match(month,/osAgendaRangeFocusBody/);
  assert.match(list,/Agendamentos do mês/);
  assert.match(list,/data-id="booked"/);
  assert.doesNotMatch(list,/os-agenda-month-calendar/);
  assert.doesNotMatch(list,/osAgendaRangeFocusBody/);
  assert.match(list,/data-os-action="agenda-prev"/);
  assert.match(list,/data-os-action="agenda-next"/);
});

test('week and month team views retain period context and sort cards by date then time',()=>{
  for(const period of ['week','month']){
    const ctx=context('2026-11-13',period,true,'');
    ctx.state.filters.agendaMode='professional';
    ctx.appointmentProfessional=a=>a.professional_name;
    ctx.cleanColor=value=>value||'#2e7bf6';
    ctx.fixtures=[
      {id:'late',professional_id:'p1',professional_name:'Ana',start_at:'2026-11-13T16:00:00Z'},
      {id:'early',professional_id:'p1',professional_name:'Ana',start_at:'2026-11-13T14:00:00Z'},
      {id:'yesterday',professional_id:'p1',professional_name:'Ana',start_at:'2026-11-12T14:00:00Z'},
      {id:'second',professional_id:'p2',professional_name:'Bruno',start_at:'2026-11-13T15:00:00Z'}
    ];
    vm.runInNewContext(helpers+renderer+`
      globalThis.teamHTML=agendaRangeView(fixtures,{total:4,days:[],offset:0,limit:50,has_more:false});`,ctx);
    const html=ctx.teamHTML;
    assert.match(html,/Por profissional/);
    assert.match(html,/Ana/);
    assert.match(html,/Bruno/);
    assert.match(html,/os-agenda-team-date-group/);
    assert.ok(html.indexOf('data-id="yesterday"')<html.indexOf('data-id="early"'));
    assert.ok(html.indexOf('data-id="early"')<html.indexOf('data-id="late"'));
    assert.match(html,/data-os-action="agenda-prev"/);
    assert.match(html,/data-os-action="agenda-next"/);
    assert.doesNotMatch(html,/nesta página/);
    if(period==='week')assert.equal((html.match(/data-os-open-day=/g)||[]).length,7);
    else assert.match(html,/os-agenda-month-overview/);
  }
});

test('inactive clear-filters action stays hidden even if the button display is overridden by CSS',()=>{
  const css=fs.readFileSync('app/static/orion-suite.css','utf8');
  assert.match(css,/#osAgendaClear\[hidden\]\{display:none!important\}/);
});

test('out-of-range selected day page recovers after an appointment disappears',async()=>{
  const ctx=context('2026-11-13','month',false,'2026-11-13');
  ctx.state.filters.agendaFocusOffset=80;
  ctx.apiPath=path=>path;
  ctx.query=params=>new URLSearchParams(Object.entries(params).filter(([,v])=>v!==''&&v!=null)).toString();
  ctx.current=()=>true;
  ctx.context=()=>({});
  const target={innerHTML:'',setAttribute(){},removeAttribute(){}};
  ctx.$=id=>id==='osAgendaRangeFocusBody'?target:null;
  const seen=[];
  ctx.request=async path=>{
    seen.push(path);
    return seen.length===1
      ?{items:[],total:3,offset:80,limit:80,has_more:false}
      :{items:[{id:'recovered'}],total:3,offset:0,limit:80,has_more:false};
  };
  vm.runInNewContext(helpers+focused+'globalThis.fetchDay=loadAgendaFocus;',ctx);
  await ctx.fetchDay();
  assert.equal(seen.length,2,'a valid earlier page is consulted again');
  assert.match(seen[0],/offset=80/);
  assert.match(seen[1],/offset=0/);
  assert.equal(ctx.state.filters.agendaFocusOffset,0);
  assert.match(target.innerHTML,/recovered/);
  assert.doesNotMatch(target.innerHTML,/Nenhum agendamento nesta data/);
});

test('search input invalidates stale focused-day requests before debounce',()=>{
  const source=fs.readFileSync('app/static/orion-suite.js','utf8');
  assert.match(source,/if\(el\.id==='osAgendaSearch'&&!state\.busy\)\{[^\n]+state\.agendaRead=\(state\.agendaRead\|\|0\)\+1;state\.agendaFocusRead=\(state\.agendaFocusRead\|\|0\)\+1;/);
});
