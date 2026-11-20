import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';

const script=readFileSync(new URL('../app/static/orion-agenda-preview.js',import.meta.url),'utf8');
const html=readFileSync(new URL('../app/static/orion-agenda-preview.html',import.meta.url),'utf8');
const css=readFileSync(new URL('../app/static/orion-agenda-preview.css',import.meta.url),'utf8');
const iso=/^\d{4}-\d{2}-\d{2}$/;
class El {
  constructor(id='') {this.id=id;this.dataset={};this.hidden=false;this.value='';this.textContent='';this.innerHTML='';this.disabled=false;this.listeners={};this.attributes={};}
  addEventListener(name,handler){this.listeners[name]=handler;}
  setAttribute(name,value){this.attributes[name]=String(value);}
  getAttribute(name){return this.attributes[name];}
  replaceChildren(){this.innerHTML='';}
  close(){this.isOpen=false;}
  showModal(){this.isOpen=true;}
  fire(name,event={}){const fn=this.listeners[name];assert.equal(typeof fn,'function','Missing listener '+name+' on '+this.id);fn({target:this,...event});}
}

function createHarness() {
  const ids=[...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]);
  const elements=Object.fromEntries(ids.map(id=>[id,new El(id)]));
  const modes=['day','week','month'].map(mode=>{const b=new El('mode-'+mode);b.dataset.mode=mode;return b;});
  const views=['list','timeline','team'].map(view=>{const b=new El('view-'+view);b.dataset.view=view;return b;});
  const document={
    documentElement:{dataset:{}},body:{dataset:{}},
    getElementById:id=>{assert.ok(elements[id],'Missing HTML id: '+id);return elements[id];},
    querySelectorAll:sel=>sel==='[data-mode]'?modes:sel==='[data-view]'?views:[]
  };
  const visits=[],appointments=[
    {id:'a1',customer_name:'Maria',professional_id:'p1',start_at:'2026-11-20T13:00:00Z',end_at:'2026-11-20T13:40:00Z',status:'confirmed',items:[{name_snapshot:'Corte'}],total_cents:3500,protocol:'OR-123'},
    {id:'a2',customer_name:'João',professional_id:'p1',start_at:'2026-11-20T13:40:00Z',end_at:'2026-11-20T14:20:00Z',status:'completed',items:[{name_snapshot:'Barba'}],total_cents:4000},
    {id:'a3',customer_name:'Marta',professional_id:'p1',start_at:'2026-11-21T15:00:00Z',end_at:'2026-11-21T15:40:00Z',status:'confirmed',items:[{name_snapshot:'Corte'}],total_cents:3500}
  ];
  const fetch=async(path,opts={})=>{
    visits.push({path,method:opts.method||'GET'});
    assert.equal(opts.method,'GET','Agenda preview must never mutate data');
    if(path==='/auth/me')return {ok:true,json:async()=>({role:'admin'})};
    if(path==='/api/v1/tenants')return {ok:true,json:async()=>[{id:'t1',name:'Peixoto',timezone:'America/Campo_Grande'}]};
    if(path.endsWith('/professionals'))return {ok:true,json:async()=>[{id:'p1',name:'Peixoto',active:true}]};
    if(path.includes('/appointments?')){
      const params=new URL('https://example.test'+path).searchParams;
      const first=params.get('start_date'),last=params.get('end_date');
      assert.ok(iso.test(first)&&iso.test(last));
      const rows=appointments.filter(a=>{
        const d=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.start_at));
        return d>=first&&d<=last;
      });
      const days=[...new Set(rows.map(a=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.start_at))))].map(d=>({
        day:d,total:rows.filter(a=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.start_at))===d).length,
        confirmed:rows.filter(a=>a.status==='confirmed'&&new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.start_at))===d).length,
        completed:rows.filter(a=>a.status==='completed'&&new Intl.DateTimeFormat('en-CA',{timeZone:'America/Campo_Grande',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(a.start_at))===d).length
      }));
      const offset=+(params.get('offset')||0),limit=+(params.get('limit')||200);
      return {ok:true,json:async()=>({items:rows.slice(offset,offset+limit),total:rows.length,days,has_more:offset+limit<rows.length})};
    }
    throw Error('Unexpected GET '+path);
  };
  const store=new Map([['orion-theme','light']]);
  const localStorage={getItem:key=>store.get(key)||null,setItem:(key,value)=>store.set(key,value)};
  const window={matchMedia:()=>({matches:false,addEventListener:()=>{}})};
  const context={document,window,fetch,localStorage,URL,URLSearchParams,Intl,Date,Math,Number,Object,String,RegExp,console,clearTimeout,setTimeout};
  vm.runInNewContext(script,context,{filename:'orion-agenda-preview.js'});
  const flush=async()=>{for(let i=0;i<7;i++)await new Promise(resolve=>setImmediate(resolve));};
  return {elements,modes,views,store,visits,flush};
}

test('Admin preview has no redundant Today/date picker or Google Agenda card; native styles used',()=>{
  assert.doesNotMatch(html,/id="today"|id="datePicker"|Google Agenda|id="dateTrigger"/);
  assert.match(html,/id="dayStrip"/);
  assert.match(html,/id="rangeRow"/);
  assert.match(html,/data-mode="week"/);
  assert.match(css,/--os-primary:#2e7bf6/);
  assert.match(css,/--os-bg:#f5f8fc/);
  assert.match(css,/data-theme=dark/);
  assert.match(html,/orion-brand-light\.svg/);
});

test('Admin preview switches Day, Week and Month and only performs GET requests',async()=>{
  const h=createHarness();
  await h.flush();
  assert.match(h.elements.dayStripGrid.innerHTML,/day-cell/);
  assert.equal(h.elements.rangeRow.hidden,true);
  h.modes[1].fire('click');await h.flush();
  assert.match(h.elements.calendarSurface.innerHTML,/week-board/);
  assert.match(h.elements.calendarSurface.innerHTML,/week-mobile-strip/);
  assert.equal(h.elements.rangeRow.hidden,false);
  h.modes[2].fire('click');await h.flush();
  assert.match(h.elements.calendarSurface.innerHTML,/month-grid/);
  assert.match(h.elements.calendarSurface.innerHTML,/month-cell/);
  assert.match(h.elements.rangeLabel.textContent,/2026/);
  h.modes[0].fire('click');await h.flush();
  assert.equal(h.elements.rangeRow.hidden,true);
  assert.match(h.elements.dayStripGrid.innerHTML,/day-cell/);
  assert.ok(h.visits.every(v=>v.method==='GET'));
  assert.ok(h.visits.some(v=>v.path.includes('/appointments?')));
});

test('Light/dark toggle follows the existing Orion theme preference',async()=>{
  const h=createHarness();await h.flush();
  assert.equal(h.elements.themeColor.content,'#f5f8fc');
  h.elements.themeToggle.fire('click');await h.flush();
  assert.equal(h.store.get('orion-theme'),'dark');
  assert.equal(h.elements.themeColor.content,'#05080f');
});

test('Preview never introduces a mutating API endpoint or unrestricted assets',()=>{
  assert.doesNotMatch(script,/method\s*:\s*["'](?:POST|PATCH|PUT|DELETE)["']/);
  assert.match(html,/agenda_original=1/);
  assert.match(html,/Prévia administrativa/);
});
