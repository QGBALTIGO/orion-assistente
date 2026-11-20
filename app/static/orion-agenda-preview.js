/* Orion Agenda: admin-only responsive redesign. Read-only, same-origin GETs only. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const state={
  companies:[],company:null,people:[],mode:"day",day:"",view:"list",
  q:"",professional:"",status:"",period:null,focus:null,epoch:0,focusEpoch:0,companyEpoch:0
};
const STATUSES={
  confirmed:"Confirmado",completed:"Concluído",cancelled:"Cancelado",
  no_show:"Não compareceu",hold:"Reserva temporária"
};
const WEEK=["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"];
const esc=value=>String(value??"").replace(/[&<>"']/g,character=>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));
const number=value=>Number.isFinite(Number(value))?Number(value):0;
const pad=value=>String(value).padStart(2,"0");
const iso=(year,month,day)=>year+"-"+pad(month)+"-"+pad(day);
const parts=day=>day.split("-").map(Number);
const utc=day=>{const p=parts(day);return new Date(Date.UTC(p[0],p[1]-1,p[2],12))};
const shift=(day,delta)=>{const d=utc(day);d.setUTCDate(d.getUTCDate()+delta);return iso(d.getUTCFullYear(),d.getUTCMonth()+1,d.getUTCDate())};
const shiftMonth=(day,delta)=>{const p=parts(day),d=new Date(Date.UTC(p[0],p[1]-1+delta,1,12));const y=d.getUTCFullYear(),m=d.getUTCMonth()+1,last=new Date(Date.UTC(y,m,0,12)).getUTCDate();return iso(y,m,Math.min(p[2],last))};
const zone=()=>state.company?.timezone||"America/Campo_Grande";
function localDate(value=new Date()){
  const p=Object.fromEntries(new Intl.DateTimeFormat("en-CA",{timeZone:zone(),day:"2-digit",month:"2-digit",year:"numeric"}).formatToParts(value).map(x=>[x.type,x.value]));
  return p.year+"-"+p.month+"-"+p.day;
}
const format=(day,options)=>new Intl.DateTimeFormat("pt-BR",{timeZone:"UTC",...options}).format(utc(day));
const capitalize=text=>text.charAt(0).toLocaleUpperCase("pt-BR")+text.slice(1);
function bounds(){
  if(state.mode==="day")return {start:state.day,end:state.day};
  if(state.mode==="week"){const start=shift(state.day,-utc(state.day).getUTCDay());return {start,end:shift(start,6)};}
  const start=state.day.slice(0,7)+"-01";
  return {start,end:shift(shiftMonth(start,1),-1)};
}
function periodLabel(){
  const b=bounds();
  if(state.mode==="day")return capitalize(format(state.day,{day:"numeric",month:"long",year:"numeric"}));
  if(state.mode==="week")return format(b.start,{day:"numeric",month:"short"})+" – "+format(b.end,{day:"numeric",month:"short",year:"numeric"});
  return capitalize(format(b.start,{month:"long",year:"numeric"}));
}
function params(forDay,offset=0){
  const b=forDay?{start:forDay,end:forDay}:bounds();
  const p=new URLSearchParams({start_date:b.start,end_date:b.end,paged:"true",limit:"200",offset:String(offset)});
  if(state.q)p.set("q",state.q);
  if(state.professional)p.set("professional_id",state.professional);
  if(state.status)p.set("status",state.status);
  return p.toString();
}
async function GET(path){
  const response=await fetch(path,{method:"GET",credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});
  if(!response.ok){
    if(response.status===401)throw Error("Sua sessão expirou. Acesse o Orion novamente.");
    if(response.status===403)throw Error("Esta conta não tem permissão para consultar a empresa.");
    throw Error("Falha ao consultar dados reais da agenda (HTTP "+response.status+").");
  }
  return response.json();
}
function endpoint(path){return "/api/v1/tenants/"+encodeURIComponent(state.company.id)+path;}
async function readAppointments(forDay){
  const initial=await GET(endpoint("/appointments?"+params(forDay)));
  if(!initial||!Array.isArray(initial.items)||!Number.isFinite(Number(initial.total)))throw Error("Resposta de agendamentos inválida.");
  const items=[...initial.items],max=1200;
  while(initial.total>items.length&&items.length<max){
    const page=await GET(endpoint("/appointments?"+params(forDay,items.length)));
    if(!page||!Array.isArray(page.items)||!page.items.length)break;
    items.push(...page.items);
  }
  return {...initial,items,partial:number(initial.total)>items.length};
}
const setError=message=>{$("errorRegion").hidden=!message;$("errorRegion").textContent=message||""};
function applyTheme(){
  let mode="auto";
  try{const v=localStorage.getItem("orion-theme");if(["auto","light","dark"].includes(v))mode=v;}catch{}
  const systemDark=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches;
  const dark=mode==="auto"?systemDark:mode==="dark";
  document.documentElement.dataset.theme=dark?"dark":"light";
  document.documentElement.dataset.themePreference=mode;
  $("themeToggle").setAttribute("aria-label",dark?"Ativar tema claro":"Ativar tema escuro");
  $("themeColor").content=dark?"#05080f":"#f5f8fc";
}
function toggleTheme(){
  const current=document.documentElement.dataset.theme;
  try{localStorage.setItem("orion-theme",current==="dark"?"light":"dark");}catch{}
  applyTheme();
}
function renderTabs(){
  for(const button of document.querySelectorAll("[data-mode]")){
    const selected=button.dataset.mode===state.mode;
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;
  }
  $("dayStrip").hidden=state.mode!=="day";
  $("rangeRow").hidden=state.mode==="day";
  $("rangeLabel").textContent=periodLabel();
  $("viewSelectWrap").hidden=state.mode!=="day";
  for(const button of document.querySelectorAll("[data-view]")){
    const selected=button.dataset.view===state.view;
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;
  }
  const count=[state.q,state.professional,state.status].filter(Boolean).length;
  $("filterCount").hidden=count===0;$("filterCount").textContent=String(count);
  document.body.dataset.agendaPeriod=state.mode;
  renderDayStrip();
}
function renderDayStrip(){
  if(state.mode!=="day"){ $("dayStripGrid").replaceChildren();return; }
  const weekStart=shift(state.day,-utc(state.day).getUTCDay()),today=localDate();
  $("dayStripGrid").innerHTML=Array.from({length:7},(_,i)=>{
    const day=shift(weekStart,i),selected=state.day===day;
    return '<button type="button" class="day-cell'+(day===today?" is-today":"")+'" data-select-day="'+day+'" aria-pressed="'+selected+'" aria-label="'+esc(format(day,{weekday:"long",day:"numeric",month:"long",year:"numeric"}))+'"><small>'+WEEK[i]+'</small><strong>'+parts(day)[2]+'</strong></button>';
  }).join("");
}
const daily=()=>Array.isArray(state.period?.days)?state.period.days:[];
function dailyCounts(day){
  const value=daily().find(x=>String(x.day)===day)||{};
  return {total:number(value.total),confirmed:number(value.confirmed),completed:number(value.completed),cancelled:number(value.cancelled),no_show:number(value.no_show),hold:number(value.hold)};
}
function summary(){
  const totals={total:0,confirmed:0,completed:0,cancelled:0,no_show:0};
  if(daily().length){
    for(const day of daily())for(const key in totals)totals[key]+=number(day[key]);
  }else if(state.period){
    totals.total=number(state.period.total);
    for(const item of state.period.items||[])if(Object.hasOwn(totals,item.status)&&item.status!=="total")totals[item.status]++;
  }
  let html='<strong class="summary-total">'+totals.total+' '+(totals.total===1?"agendamento":"agendamentos")+'</strong><span class="summary-note">'+(state.mode==="day"?"no dia":state.mode==="week"?"na semana":"no mês")+'</span>';
  for(const [key,label] of [["confirmed","Confirmados"],["completed","Concluídos"],["cancelled","Cancelamentos"],["no_show","Faltas"]]){
    if(totals[key])html+='<span class="stat-chip" data-kind="'+key+'"><i class="stat-dot"></i><b>'+totals[key]+'</b>'+label+'</span>';
  }
  $("summary").innerHTML=html;
}
function appointmentDay(item){
  const date=new Date(item.start_at);
  return Number.isFinite(date.getTime())?localDate(date):"";
}
function time(value){
  try{return new Intl.DateTimeFormat("pt-BR",{timeZone:zone(),hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(value));}
  catch{return "--:--";}
}
const name=item=>String(item.customer_name||item.customer?.name||item.name||"Cliente");
const service=item=>(Array.isArray(item.items)?item.items:[]).map(x=>x.name_snapshot||x.name||"").filter(Boolean).join(" + ")||"Serviço não informado";
const professional=item=>String(item.professional_name||state.people.find(p=>String(p.id)===String(item.professional_id))?.name||"Profissional");
const price=item=>new Intl.NumberFormat("pt-BR",{style:"currency",currency:"BRL"}).format(number(item.total_cents)/100);
const status=item=>Object.hasOwn(STATUSES,item.status)?item.status:"confirmed";
function dayEvents(day){
  return (state.period?.items||[]).filter(item=>appointmentDay(item)===day)
    .sort((a,b)=>String(a.start_at||"").localeCompare(String(b.start_at||"")));
}
function dots(counts){
  let content="";
  for(const key of ["confirmed","completed","cancelled","no_show"])if(counts[key])content+='<i data-status="'+key+'"></i>';
  return content;
}
function monthCalendar(){
  const b=bounds(),first=utc(b.start),gap=first.getUTCDay();
  const slots=Math.ceil((gap+utc(b.end).getUTCDate())/7)*7,start=shift(b.start,-gap),today=localDate();
  const cells=[];
  for(let i=0;i<slots;i++){
    const day=shift(start,i),out=day<b.start||day>b.end,counts=dailyCounts(day),events=out?[]:dayEvents(day),more=Math.max(0,counts.total-Math.min(events.length,2));
    let previews=events.slice(0,2).map(item=>
      '<span class="month-event" data-status="'+status(item)+'" title="'+esc(time(item.start_at)+" · "+name(item))+'"><strong>'+esc(time(item.start_at))+'</strong> '+esc(name(item))+'</span>').join("");
    if(more)previews+='<span class="month-more">mais +'+more+'</span>';
    const selected=state.day===day;
    cells.push('<button type="button" class="month-cell'+(selected?" is-selected":"")+
      (day===today?" is-today":"")+(out?" is-other":"")+'" data-select-day="'+day+'" aria-pressed="'+selected+'" aria-label="'+
      esc(format(day,{weekday:"long",day:"numeric",month:"long",year:"numeric"})+", "+counts.total+" agendamentos")+'">'+
      '<span class="month-top"><span class="month-number">'+parts(day)[2]+'</span>'+(counts.total?'<span class="month-count">'+counts.total+'</span>':"")+'</span>'+
      '<span class="month-events">'+previews+'</span>'+
      '<span class="month-dots" aria-hidden="true">'+(out?"":dots(counts))+'</span></button>');
  }
  $("calendarSurface").innerHTML='<div class="month-calendar" role="group" aria-label="Calendário mensal de '+esc(periodLabel())+'">'+
    '<div class="month-weekdays" aria-hidden="true">'+WEEK.map(value=>'<span>'+value+'</span>').join("")+'</div>'+
    '<div class="month-grid">'+cells.join("")+'</div></div>';
}
function clockMinutes(value){
  const label=time(value),m=/^(\d{2}):(\d{2})$/.exec(label);
  return m?Math.min(1439,+m[1]*60+(+m[2])):0;
}
function weekWindow(){
  const items=state.period?.items||[];
  let min=8*60,max=19*60;
  for(const a of items){
    if(!a.start_at)continue;
    const start=clockMinutes(a.start_at);
    const dur=Math.max(10,(Date.parse(a.end_at)-Date.parse(a.start_at))/60000||40);
    min=Math.min(min,start);max=Math.max(max,Math.min(24*60,start+dur));
  }
  return {first:Math.max(0,Math.floor(min/60)-1),last:Math.min(24,Math.ceil(max/60)+1)};
}
function weekDayEvents(day,firstHour,lastHour){
  const items=dayEvents(day).map(item=>{
    const start=Math.max(firstHour*60,clockMinutes(item.start_at));
    const duration=Math.max(10,(Date.parse(item.end_at)-Date.parse(item.start_at))/60000||40);
    return {item,start,end:Math.min(lastHour*60,start+duration)};
  }).filter(event=>event.end>firstHour*60&&event.start<lastHour*60).sort((a,b)=>a.start-b.start||a.end-b.end);
  // Group overlapping bookings and allocate at most two visual lanes. Everything
  // remains accessible in the full day list, rather than squeezing names.
  const groups=[];let group=[],until=-1;
  for(const event of items){
    if(group.length&&event.start>=until){groups.push(group);group=[];until=-1;}
    group.push(event);until=Math.max(until,event.end);
  }
  if(group.length)groups.push(group);
  const result=[];
  for(const group of groups){
    const ends=[],hidden=[];
    for(const event of group){
      let lane=ends.findIndex(end=>end<=event.start);
      if(lane<0)lane=ends.length;
      ends[lane]=event.end;event.lane=lane;
    }
    const laneCount=Math.min(2,ends.length);
    group.forEach(event=>{if(event.lane<laneCount){event.lanes=laneCount;result.push(event);}else hidden.push(event)});
    if(hidden.length){
      const first=hidden.reduce((m,e)=>Math.min(m,e.start),Infinity);
      result.push({aggregate:true,start:first,end:Math.min(lastHour*60,first+38),hidden:hidden.length,lanes:2,lane:1});
    }
  }
  return result;
}
function weekCalendar(){
  const b=bounds(),today=localDate(),hours=weekWindow(),height=(hours.last-hours.first)*76;
  let head='<div class="week-head-time">HORA</div>',axis="";
  for(let hour=hours.first;hour<hours.last;hour++)axis+='<div class="week-hour">'+pad(hour)+':00</div>';
  let columns="";
  for(let i=0;i<7;i++){
    const day=shift(b.start,i),count=dailyCounts(day).total,selected=day===state.day;
    head+='<button type="button" class="week-head-day'+(selected?" is-selected":"")+(day===today?" is-today":"")+
      '" data-select-day="'+day+'" aria-label="'+esc(format(day,{weekday:"long",day:"numeric",month:"long"})+", "+count+" agendamentos")+'">'+
      '<span class="week-head-name">'+WEEK[i]+'</span><strong>'+parts(day)[2]+'/'+pad(parts(day)[1])+'</strong>'+
      '<small>'+count+(count===1?" agendamento":" agendamentos")+'</small></button>';
    const events=weekDayEvents(day,hours.first,hours.last);
    let blocks="";
    for(const event of events){
      const top=Math.max(1,(event.start-hours.first*60)*76/60),tall=Math.max(25,(event.end-event.start)*76/60-2);
      const left=event.lane*(100/event.lanes),width=100/event.lanes;
      const style='top:'+top.toFixed(1)+'px;height:'+tall.toFixed(1)+'px;left:calc('+left+'% + 2px);width:calc('+width+'% - 4px);';
      if(event.aggregate){
        blocks+='<button class="week-booking" type="button" data-select-day="'+day+'" style="'+style+'" title="Ver outros atendimentos"><span class="week-booking-name">+'+event.hidden+' atend.</span></button>';
      }else{
        const item=event.item,id=esc(item.id);
        blocks+='<button class="week-booking" data-appointment-id="'+id+'" data-status="'+status(item)+'" type="button" style="'+style+'" title="'+
          esc(time(item.start_at)+" · "+name(item)+" · "+service(item))+'">'+
          '<span class="week-booking-time">'+esc(time(item.start_at))+'</span>'+
          '<span class="week-booking-name">'+esc(name(item))+'</span>'+
          '<span class="week-booking-service">'+esc(service(item))+'</span></button>';
      }
    }
    columns+='<div class="week-column'+(day===today?" is-today":"")+'" style="grid-column:'+(i+2)+';height:'+height+'px">'+blocks+'</div>';
  }
  const legend='<div class="week-legend"><span><i class="legend-swatch"></i>Confirmados</span>'+
    '<span><i class="legend-swatch completed"></i>Concluídos</span>'+
    '<span><i class="legend-swatch cancelled"></i>Cancelados</span>'+
    '<span><i class="legend-swatch no_show"></i>Faltas</span></div>';
  let mobile="";
  for(let i=0;i<7;i++){
    const day=shift(b.start,i),count=dailyCounts(day).total;
    mobile+='<button class="week-mobile-day'+(day===state.day?" is-selected":"")+'" data-select-day="'+day+'" type="button" aria-pressed="'+(day===state.day)+'" aria-label="'+esc(format(day,{weekday:"long",day:"numeric",month:"long"})+", "+count+" agendamentos")+'"><small>'+WEEK[i]+'</small><strong>'+parts(day)[2]+'</strong>'+(count?'<i aria-hidden="true"></i>':"")+'</button>';
  }
  $("calendarSurface").innerHTML='<div class="week-overview" role="group" aria-label="Grade de horários da semana">'+
    legend+'<div class="week-scroll" tabindex="0" aria-label="Grade semanal; role horizontalmente para ver todos os dias">'+
    '<div class="week-board">'+head+'<div class="week-axis">'+axis+'</div>'+columns+'</div></div>'+
    '<div class="week-mobile"><div class="week-mobile-strip" role="group" aria-label="Selecionar dia desta semana">'+mobile+'</div>'+
    '<p class="week-mobile-help">Selecione um dia para ver os atendimentos e horários abaixo.</p></div></div>';
}
function renderCalendar(){
  if(state.mode==="day"){$("calendarSurface").replaceChildren();return;}
  if(state.mode==="week")weekCalendar();else monthCalendar();
}
function row(item,index){
  return '<button type="button" class="apt-row" data-appointment-index="'+index+'" aria-label="'+esc("Ver agendamento de "+name(item)+" às "+time(item.start_at))+'">'+
    '<span class="apt-time"><strong>'+esc(time(item.start_at))+'</strong><small>'+esc(time(item.end_at))+'</small></span>'+
    '<span class="apt-main"><strong>'+esc(name(item))+'</strong><small>'+esc(service(item))+'</small><small class="apt-person"><i class="apt-dot"></i>'+esc(professional(item))+'</small></span>'+
    '<span class="apt-meta"><span class="status-badge" data-status="'+status(item)+'">'+esc(STATUSES[status(item)])+'</span>'+
    '<strong class="apt-price">'+esc(price(item))+'</strong></span></button>';
}
function renderFocus(){
  $("focusEyebrow").textContent=state.mode==="day"?"AGENDA DO DIA":"DIA SELECIONADO";
  $("focusHeading").textContent=capitalize(format(state.day,{weekday:"long",day:"numeric",month:"long"}));
  if(!state.focus)return;
  const data=state.focus,items=[...data.items].sort((a,b)=>String(a.start_at||"").localeCompare(String(b.start_at||"")));
  $("focusSubheading").textContent=number(data.total)+" "+(number(data.total)===1?"agendamento":"agendamentos")+" nesta data";
  if(!items.length){
    $("appointments").innerHTML='<div class="empty-state"><div class="empty-icon" aria-hidden="true">▦</div>'+
      '<strong>Nenhum agendamento encontrado</strong><p>Selecione outra data ou ajuste os filtros.</p></div>';
    return;
  }
  let html="";
  if(state.view==="team"&&state.mode==="day"){
    const groups=new Map();
    for(const a of items){const key=professional(a);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(a);}
    html='<div class="team-list">'+[...groups].map(([key,list])=>'<section>'+
      '<h3 class="team-heading">'+esc(key)+'<span>'+list.length+' '+(list.length===1?"atendimento":"atendimentos")+'</span></h3>'+
      '<div class="apt-list">'+list.map(a=>row(a,data.items.indexOf(a))).join("")+'</div></section>').join("")+'</div>';
  }else if(state.view==="timeline"&&state.mode==="day"){
    const grouped=new Map();
    for(const a of items){const hour=time(a.start_at).slice(0,2)+":00";if(!grouped.has(hour))grouped.set(hour,[]);grouped.get(hour).push(a);}
    html='<div class="hour-grid">'+[...grouped].sort(([a],[b])=>a.localeCompare(b)).map(([hour,list])=>
      '<div class="hour-row"><span class="hour-mark">'+esc(hour)+'</span><div class="hour-cells">'+
      list.map(a=>row(a,data.items.indexOf(a))).join("")+'</div></div>').join("")+'</div>';
  }else{
    html='<div class="apt-list">'+items.map(a=>row(a,data.items.indexOf(a))).join("")+'</div>';
  }
  if(data.partial)html+='<p class="summary-note">Há mais registros nesta data. Para consultar toda a lista, abra a Agenda original.</p>';
  $("appointments").innerHTML=html;
}
async function loadFocus(){
  if(!state.company)return;
  const epoch=++state.focusEpoch,day=state.day;
  $("appointments").innerHTML='<div class="loading-state">Consultando atendimentos deste dia…</div>';
  try{
    const data=state.mode==="day"&&state.period?state.period:await readAppointments(day);
    if(epoch!==state.focusEpoch||day!==state.day)return;
    state.focus=data;renderFocus();
  }catch(error){
    if(epoch!==state.focusEpoch)return;
    state.focus=null;setError(error.message);
    $("appointments").innerHTML='<div class="empty-state"><strong>Não foi possível carregar os atendimentos</strong><p>Use Atualizar para tentar novamente.</p></div>';
  }
}
async function refresh(){
  if(!state.company)return;
  const epoch=++state.epoch;++state.focusEpoch;
  state.period=null;state.focus=null;setError("");renderTabs();
  $("summary").textContent="Consultando agendamentos…";
  $("appointments").innerHTML='<div class="loading-state">Carregando dados reais do Orion…</div>';
  $("calendarSurface").replaceChildren();$("refresh").disabled=true;
  try{
    const data=await readAppointments();
    if(epoch!==state.epoch)return;
    state.period=data;summary();renderCalendar();
    await loadFocus();
  }catch(error){
    if(epoch!==state.epoch)return;
    setError(error.message);$("summary").textContent="Não foi possível consultar o período.";
    $("appointments").innerHTML='<div class="empty-state"><strong>Agenda indisponível</strong><p>Confira a empresa e tente novamente.</p></div>';
  }finally{
    if(epoch===state.epoch)$("refresh").disabled=false;
  }
}
async function selectCompany(id){
  const company=state.companies.find(c=>String(c.id)===String(id));
  if(!company)return;
  state.company=company;state.day=localDate();state.people=[];state.professional="";
  $("businessSelect").value=company.id;$("professional").innerHTML='<option value="">Toda a equipe</option>';
  const seq=++state.companyEpoch;
  try{
    const result=await GET(endpoint("/professionals"));
    if(seq!==state.companyEpoch)return;
    state.people=Array.isArray(result)?result:Array.isArray(result.items)?result.items:[];
    $("professional").innerHTML='<option value="">Toda a equipe</option>'+
      state.people.filter(p=>p.active!==false).map(p=>'<option value="'+esc(p.id)+'">'+esc(p.name||"Profissional")+'</option>').join("");
  }catch(error){
    if(seq===state.companyEpoch)setError("Profissionais indisponíveis: "+error.message);
  }
  if(seq===state.companyEpoch)await refresh();
}
function details(item){
  if(!item)return;
  $("detailTitle").textContent=name(item);
  const fields=[
    ["Situação",STATUSES[status(item)]],
    ["Data",format(appointmentDay(item)||state.day,{weekday:"long",day:"numeric",month:"long",year:"numeric"})],
    ["Horário",time(item.start_at)+" – "+time(item.end_at)],
    ["Serviços",service(item)],["Profissional",professional(item)],
    ["Valor",price(item)],["Protocolo",item.protocol||"—"]
  ];
  $("detailContent").innerHTML=fields.map(([key,value])=>
    '<div class="detail-content-row"><span>'+esc(key)+'</span><strong>'+esc(value)+'</strong></div>').join("");
  $("detailDialog").showModal();
}
function changePeriod(delta){
  state.day=state.mode==="month"?shiftMonth(state.day,delta):shift(state.day,delta*(state.mode==="week"?7:1));
  void refresh();
}
function changeWeek(delta){state.day=shift(state.day,7*delta);void refresh();}
function selectDay(day){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(day))return;
  const b=bounds(),outside=day<b.start||day>b.end;
  state.day=day;
  if(outside){void refresh();return;}
  renderTabs();renderCalendar();void loadFocus();
}
function bind(){
  for(const button of document.querySelectorAll("[data-mode]"))button.addEventListener("click",()=>{
    if(state.mode===button.dataset.mode)return;
    state.mode=button.dataset.mode;void refresh();
  });
  for(const button of document.querySelectorAll("[data-view]"))button.addEventListener("click",()=>{
    state.view=button.dataset.view;renderTabs();renderFocus();
  });
  $("themeToggle").addEventListener("click",toggleTheme);
  if(window.matchMedia){const media=window.matchMedia("(prefers-color-scheme: dark)");media.addEventListener?.("change",()=>{
    if(document.documentElement.dataset.themePreference==="auto")applyTheme();
  });}
  $("businessSelect").addEventListener("change",event=>void selectCompany(event.target.value));
  $("rangePrevious").addEventListener("click",()=>changePeriod(-1));
  $("rangeNext").addEventListener("click",()=>changePeriod(1));
  $("dayPrev").addEventListener("click",()=>changeWeek(-1));
  $("dayNext").addEventListener("click",()=>changeWeek(1));
  $("dayStripGrid").addEventListener("click",event=>{
    const button=event.target.closest("[data-select-day]");if(button)selectDay(button.dataset.selectDay);
  });
  $("filtersToggle").addEventListener("click",()=>{
    const panel=$("filtersPanel");panel.hidden=!panel.hidden;
    $("filtersToggle").setAttribute("aria-expanded",String(!panel.hidden));
  });
  let timer;
  $("search").addEventListener("input",event=>{
    clearTimeout(timer);timer=setTimeout(()=>{state.q=event.target.value.trim();void refresh();},320);
  });
  $("professional").addEventListener("change",event=>{state.professional=event.target.value;void refresh();});
  $("status").addEventListener("change",event=>{state.status=event.target.value;void refresh();});
  $("clearFilters").addEventListener("click",()=>{
    state.q="";state.professional="";state.status="";
    $("search").value="";$("professional").value="";$("status").value="";void refresh();
  });
  $("refresh").addEventListener("click",()=>void refresh());
  $("calendarSurface").addEventListener("click",event=>{
    const appointment=event.target.closest("[data-appointment-id]");
    if(appointment){
      const item=(state.period?.items||[]).find(a=>String(a.id)===appointment.dataset.appointmentId);
      if(item)details(item);return;
    }
    const day=event.target.closest("[data-select-day]");if(day)selectDay(day.dataset.selectDay);
  });
  $("appointments").addEventListener("click",event=>{
    const button=event.target.closest("[data-appointment-index]");
    if(button)details(state.focus?.items?.[Number(button.dataset.appointmentIndex)]);
  });
  $("detailClose").addEventListener("click",()=>$("detailDialog").close());
  $("detailDialog").addEventListener("click",event=>{
    if(event.target===$("detailDialog"))$("detailDialog").close();
  });
}
async function boot(){
  applyTheme();bind();
  try{
    const account=await GET("/auth/me");
    if(account.role!=="admin"||account.maintenance?.active)throw Error("Esta prévia é exclusiva do administrador principal.");
    const result=await GET("/api/v1/tenants");
    if(!Array.isArray(result))throw Error("Empresas indisponíveis.");
    state.companies=result.filter(c=>c&&c.id&&c.name);
    $("businessSelect").innerHTML=state.companies.map(c=>'<option value="'+esc(c.id)+'">'+esc(c.name)+'</option>').join("")||'<option value="">Nenhuma empresa</option>';
    if(state.companies.length)await selectCompany(state.companies[0].id);
    else{
      $("summary").textContent="Nenhuma empresa encontrada.";
      $("appointments").innerHTML='<div class="empty-state"><strong>Sem empresas cadastradas</strong><p>Cadastre uma empresa antes de consultar a agenda.</p></div>';
    }
  }catch(error){
    setError(error.message);$("summary").textContent="Acesso indisponível";
    $("appointments").innerHTML='<div class="empty-state"><strong>Não foi possível abrir a prévia</strong><p>Confira seu acesso administrativo no Orion.</p><p><a href="/painel?aba=agenda&agenda_original=1">Abrir Agenda original</a></p></div>';
  }
}
void boot();
})();