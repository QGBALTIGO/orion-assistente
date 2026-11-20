/* Orion Agenda LAB — somente leitura, acessível em rota protegida. */
(()=>{
"use strict";
const $=id=>document.getElementById(id);
const S={companies:[],company:null,people:[],mode:"day",day:"",view:"list",q:"",professional:"",status:"",range:null,focus:null,epoch:0,focusEpoch:0};
const STATE={confirmed:"Confirmado",completed:"Concluído",cancelled:"Cancelado",no_show:"Não compareceu",hold:"Reserva temporária"};
const WEEK=["Dom","Seg","Ter","Qua","Qui","Sex","Sáb"];
const ESC=v=>String(v??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const N=v=>Number.isFinite(Number(v))?Number(v):0;
const PAD=v=>String(v).padStart(2,"0");
const ISO=(y,m,d)=>y+"-"+PAD(m)+"-"+PAD(d);
const PARTS=d=>d.split("-").map(Number);
const UTC=d=>{const p=PARTS(d);return new Date(Date.UTC(p[0],p[1]-1,p[2],12))};
const SHIFT=(d,n)=>{const x=UTC(d);x.setUTCDate(x.getUTCDate()+n);return ISO(x.getUTCFullYear(),x.getUTCMonth()+1,x.getUTCDate())};
const MONTH=(d,n)=>{const p=PARTS(d),x=new Date(Date.UTC(p[0],p[1]-1+n,1,12)),y=x.getUTCFullYear(),m=x.getUTCMonth()+1,days=new Date(Date.UTC(y,m,0,12)).getUTCDate();return ISO(y,m,Math.min(p[2],days))};
const TODAY=zone=>{const p=Object.fromEntries(new Intl.DateTimeFormat("en-CA",{timeZone:zone||"America/Campo_Grande",day:"2-digit",month:"2-digit",year:"numeric"}).formatToParts(new Date()).map(x=>[x.type,x.value]));return p.year+"-"+p.month+"-"+p.day};
const FMT=(day,opts)=>new Intl.DateTimeFormat("pt-BR",Object.assign({timeZone:"UTC"},opts)).format(UTC(day));
const CAP=t=>t.charAt(0).toLocaleUpperCase("pt-BR")+t.slice(1);
const ZONE=()=>S.company?.timezone||"America/Campo_Grande";
const BOUNDS=()=>{
 if(S.mode==="day")return {start:S.day,end:S.day};
 if(S.mode==="week"){const a=SHIFT(S.day,-UTC(S.day).getUTCDay());return {start:a,end:SHIFT(a,6)}}
 const a=S.day.slice(0,7)+"-01";return {start:a,end:SHIFT(MONTH(a,1),-1)};
};
const LABEL=()=>{const b=BOUNDS();if(S.mode==="day")return CAP(FMT(S.day,{day:"numeric",month:"long",year:"numeric"}));if(S.mode==="week")return FMT(b.start,{day:"numeric",month:"short"})+" – "+FMT(b.end,{day:"numeric",month:"short",year:"numeric"});return CAP(FMT(b.start,{month:"long",year:"numeric"}));};
async function GET(path){const r=await fetch(path,{method:"GET",credentials:"same-origin",cache:"no-store",headers:{"Accept":"application/json"}});if(!r.ok)throw Error(r.status===401?"Sessão expirada. Entre novamente no Orion.":r.status===403?"Acesso não autorizado para esta empresa.":"Falha ao consultar a agenda (HTTP "+r.status+").");return r.json();}
function URLAPI(s){return "/api/v1/tenants/"+encodeURIComponent(S.company.id)+s}
function PARAM(day){const b=day?{start:day,end:day}:BOUNDS(),p=new URLSearchParams({start_date:b.start,end_date:b.end,paged:"true",limit:day||S.mode==="day"?"200":"30",offset:"0"});if(S.q)p.set("q",S.q);if(S.professional)p.set("professional_id",S.professional);if(S.status)p.set("status",S.status);return p.toString()}
function ERROR(t){$("errorRegion").hidden=!t;$("errorRegion").textContent=t||"";}
function HEAD(){
 $("rangeLabel").textContent=LABEL();$("datePicker").value=S.day;
 const b=BOUNDS(),now=TODAY(ZONE());$("today").hidden=now>=b.start&&now<=b.end;
 document.body.className="mode-"+S.mode;
 document.querySelectorAll("[data-mode]").forEach(btn=>{const active=btn.dataset.mode===S.mode;btn.setAttribute("aria-selected",String(active));btn.tabIndex=active?0:-1});
 $("viewSelectWrap").hidden=S.mode!=="day";$("viewSelect").value=S.view;
 const n=[S.q,S.professional,S.status].filter(Boolean).length;$("filterCount").hidden=!n;$("filterCount").textContent=n;
}
function DAYS(){return Array.isArray(S.range?.days)?S.range.days:[]}
function COUNTS(day){const x=DAYS().find(r=>String(r.day)===day)||{};return {total:N(x.total),confirmed:N(x.confirmed),completed:N(x.completed),cancelled:N(x.cancelled),no_show:N(x.no_show),hold:N(x.hold)}}
function SUMMARY(){
 const rows=DAYS(),s={total:0,confirmed:0,completed:0,cancelled:0,no_show:0};
 for(const day of rows)for(const k in s)s[k]+=N(day[k]);
 if(!rows.length&&S.range){s.total=N(S.range.total);for(const a of S.range.items||[])if(Object.hasOwn(s,a.status)&&a.status!=="total")s[a.status]++}
 let h='<strong class="summary-total">'+s.total+' '+(s.total===1?"agendamento":"agendamentos")+'</strong><span class="summary-note">'+(S.mode==="day"?"no dia":S.mode==="week"?"na semana":"no mês")+'</span>';
 for(const [key,label] of [["confirmed","Confirmados"],["completed","Concluídos"],["cancelled","Cancelados"],["no_show","Faltas"]])if(s[key])h+='<span class="stat-chip" data-kind="'+key+'"><i class="stat-dot"></i><b>'+s[key]+'</b>'+label+'</span>';
 $("summary").innerHTML=h;
}
function DOTS(count){let h="";for(const key of ["confirmed","completed","cancelled","no_show"])if(count[key])h+='<i data-status="'+key+'"></i>';return h}
function CALENDAR(){
 if(S.mode==="day"){$("calendarSurface").replaceChildren();return}
 const b=BOUNDS(),today=TODAY(ZONE());
 if(S.mode==="week"){
  let h="";for(let i=0;i<7;i++){const day=SHIFT(b.start,i),count=COUNTS(day).total;
   h+='<button type="button" data-select-day="'+day+'" aria-pressed="'+(day===S.day)+'" aria-label="'+ESC(FMT(day,{weekday:"long",day:"numeric",month:"long"})+", "+count+" agendamentos")+'" class="week-tile'+(day===S.day?" is-selected":"")+(day===today?" is-today":"")+'"><small>'+WEEK[UTC(day).getUTCDay()]+'</small><strong>'+PARTS(day)[2]+'</strong><span class="week-count">'+(count?count+" agend.":"Livre")+'</span></button>';
  }
  $("calendarSurface").innerHTML='<section class="calendar-card"><h2>Visão da semana</h2><div class="week-grid">'+h+'</div><p class="week-description">Toque em um dia para consultar seus atendimentos.</p></section>';return;
 }
 const first=UTC(b.start),start=SHIFT(b.start,-first.getUTCDay()),cells=Math.ceil((first.getUTCDay()+UTC(b.end).getUTCDate())/7)*7;
 let h="";for(let i=0;i<cells;i++){const day=SHIFT(start,i),other=day<b.start||day>b.end,c=COUNTS(day);
  h+='<button type="button" data-select-day="'+day+'" aria-pressed="'+(day===S.day)+'" class="month-cell'+(day===S.day?" is-selected":"")+(day===today?" is-today":"")+(other?" is-other":"")+'" aria-label="'+ESC(FMT(day,{day:"numeric",month:"long",weekday:"long"})+", "+c.total+" agendamentos")+'"><span class="month-number">'+PARTS(day)[2]+'</span>'+(c.total?'<span class="month-count">'+c.total+'</span>':"")+'<span class="month-dots" aria-hidden="true">'+(other?"":DOTS(c))+'</span></button>';
 }
 $("calendarSurface").innerHTML='<section class="calendar-card"><div class="weekday-row" aria-hidden="true">'+["D","S","T","Q","Q","S","S"].map(w=>"<span>"+w+"</span>").join("")+'</div><div class="month-grid">'+h+'</div><p class="week-description">Os pontos indicam os estados dos agendamentos. Toque em uma data para consultar.</p></section>';
}
const NAME=a=>String(a.customer_name||a.customer?.name||a.name||"Cliente");
const SERVICES=a=>(Array.isArray(a.items)?a.items:[]).map(x=>x.name_snapshot||x.name||"").filter(Boolean).join(" + ")||"Serviço não informado";
const PROFESSIONAL=a=>String(a.professional_name||S.people.find(p=>String(p.id)===String(a.professional_id))?.name||"Profissional");
const MONEY=a=>new Intl.NumberFormat("pt-BR",{style:"currency",currency:"BRL"}).format(N(a.total_cents)/100);
function CLOCK(t){try{return new Intl.DateTimeFormat("pt-BR",{timeZone:ZONE(),hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(t))}catch{return "--:--"}}
function ROW(a,i){
 const status=Object.hasOwn(STATE,a.status)?a.status:"confirmed";
 return '<button type="button" class="apt-row" data-appointment-index="'+i+'" aria-label="'+ESC("Ver atendimento de "+NAME(a)+", "+CLOCK(a.start_at))+'"><span class="apt-time"><strong>'+ESC(CLOCK(a.start_at))+'</strong><small>'+ESC(CLOCK(a.end_at))+'</small></span><span class="apt-main"><strong>'+ESC(NAME(a))+'</strong><small>'+ESC(SERVICES(a))+'</small><small class="apt-person"><i class="apt-dot"></i>'+ESC(PROFESSIONAL(a))+'</small></span><span class="apt-meta"><span class="status-badge" data-status="'+status+'">'+ESC(STATE[status])+'</span><strong class="apt-price">'+ESC(MONEY(a))+'</strong></span></button>';
}
function FOCUS(){
 $("focusEyebrow").textContent=S.mode==="day"?"AGENDA DO DIA":"DIA SELECIONADO";
 $("focusHeading").textContent=CAP(FMT(S.day,{weekday:"long",day:"numeric",month:"long"}));
 if(!S.focus)return;
 const data=S.focus,items=[...data.items].sort((a,b)=>String(a.start_at||"").localeCompare(String(b.start_at||"")));
 $("focusSubheading").textContent=N(data.total)+" "+(N(data.total)===1?"agendamento":"agendamentos")+" nesta data";
 if(!items.length){$("appointments").innerHTML='<div class="empty-state"><div class="empty-icon">▦</div><strong>Nenhum agendamento encontrado</strong><p>Selecione outra data ou ajuste os filtros. Para criar, use a agenda atual.</p></div>';return}
 let h="";
 if(S.view==="team"&&S.mode==="day"){
  const groups=new Map();for(const a of items){const name=PROFESSIONAL(a);if(!groups.has(name))groups.set(name,[]);groups.get(name).push(a)}
  h='<div class="team-list">'+[...groups].map(([name,list])=>'<section><h3 class="team-heading">'+ESC(name)+'<span>'+list.length+' atendimentos</span></h3><div class="apt-list">'+list.map(a=>ROW(a,data.items.indexOf(a))).join("")+'</div></section>').join("")+'</div>';
 }else if(S.view==="timeline"&&S.mode==="day"){
  const groups=new Map();for(const a of items){const hr=CLOCK(a.start_at).slice(0,2)+":00";if(!groups.has(hr))groups.set(hr,[]);groups.get(hr).push(a)}
  h='<div class="hour-grid">'+[...groups].sort(([a],[b])=>a.localeCompare(b)).map(([hr,list])=>'<div class="hour-row"><span class="hour-mark">'+ESC(hr)+'</span><div class="hour-cells">'+list.map(a=>ROW(a,data.items.indexOf(a))).join("")+'</div></div>').join("")+'</div>';
 }else h='<div class="apt-list">'+items.map(a=>ROW(a,data.items.indexOf(a))).join("")+'</div>';
 if(data.has_more)h+='<p class="week-description">Há mais atendimentos. Veja a lista completa na agenda atual.</p>';
 $("appointments").innerHTML=h;
}
async function loadFocus(){
 const epoch=++S.focusEpoch,day=S.day;
 $("appointments").innerHTML='<div class="loading-state">Consultando horários do dia…</div>';
 try{
  const d=S.mode==="day"&&S.range?S.range:await GET(URLAPI("/appointments?"+PARAM(day)));
  if(epoch!==S.focusEpoch||day!==S.day)return;
  if(!d||!Array.isArray(d.items))throw Error("Resposta de atendimento inválida.");
  S.focus=d;FOCUS();
 }catch(ex){if(epoch!==S.focusEpoch)return;S.focus=null;ERROR(ex.message);$("appointments").innerHTML='<div class="empty-state"><strong>Não foi possível consultar os horários</strong><p>Tente Atualizar.</p></div>'}
}
async function refresh(){
 if(!S.company)return;const epoch=++S.epoch;++S.focusEpoch;S.range=null;S.focus=null;ERROR("");HEAD();
 $("summary").textContent="Consultando agenda real…";$("appointments").innerHTML='<div class="loading-state">Carregando atendimentos do Orion…</div>';$("calendarSurface").replaceChildren();$("refresh").disabled=true;
 try{
  const d=await GET(URLAPI("/appointments?"+PARAM()));
  if(epoch!==S.epoch)return;
  if(!d||!Array.isArray(d.items)||!Array.isArray(d.days))throw Error("Dados do período indisponíveis.");
  S.range=d;SUMMARY();CALENDAR();await loadFocus();
 }catch(ex){if(epoch!==S.epoch)return;ERROR(ex.message);$("summary").textContent="Dados indisponíveis";$("appointments").innerHTML='<div class="empty-state"><strong>Não foi possível carregar a agenda</strong><p>Confira a empresa e tente novamente.</p></div>'}
 finally{if(epoch===S.epoch)$("refresh").disabled=false}
}
async function selectCompany(id){
 const c=S.companies.find(x=>String(x.id)===String(id));if(!c)return;S.company=c;S.day=TODAY(c.timezone);S.people=[];S.professional="";
 $("professional").innerHTML='<option value="">Toda a equipe</option>';$("businessSelect").value=c.id;
 const companyEpoch=++S.epoch;
 try{const result=await GET(URLAPI("/professionals"));if(companyEpoch!==S.epoch||S.company!==c)return;S.people=Array.isArray(result)?result:(result.items||[]);$("professional").innerHTML='<option value="">Toda a equipe</option>'+S.people.filter(p=>p.active!==false).map(p=>'<option value="'+ESC(p.id)+'">'+ESC(p.name||"Profissional")+'</option>').join("")}catch(ex){if(companyEpoch===S.epoch)ERROR("Profissionais indisponíveis: "+ex.message)}
 void refresh();
}
function details(i){
 const a=S.focus?.items?.[i];if(!a)return;
 $("detailTitle").textContent=NAME(a);
 const fields=[["Situação",STATE[a.status]||a.status||"—"],["Data",FMT(S.day,{day:"numeric",month:"long",year:"numeric"})],["Horário",CLOCK(a.start_at)+" – "+CLOCK(a.end_at)],["Serviços",SERVICES(a)],["Profissional",PROFESSIONAL(a)],["Valor",MONEY(a)],["Protocolo",a.protocol||"—"]];
 $("detailContent").innerHTML=fields.map(([k,v])=>'<div class="detail-content-row"><span>'+ESC(k)+'</span><strong>'+ESC(v)+'</strong></div>').join("");
 $("detailDialog").showModal();
}
function move(n){S.day=S.mode==="month"?MONTH(S.day,n):SHIFT(S.day,n*(S.mode==="week"?7:1));void refresh()}
function bind(){
 document.querySelectorAll("[data-mode]").forEach(btn=>btn.addEventListener("click",()=>{if(S.mode===btn.dataset.mode)return;S.mode=btn.dataset.mode;void refresh()}));
 $("businessSelect").addEventListener("change",e=>void selectCompany(e.target.value));
 $("previous").addEventListener("click",()=>move(-1));$("next").addEventListener("click",()=>move(1));
 $("today").addEventListener("click",()=>{S.day=TODAY(ZONE());void refresh()});
 $("dateTrigger").addEventListener("click",()=>{try{$("datePicker").showPicker()}catch{$("datePicker").focus();$("datePicker").click()}});
 $("datePicker").addEventListener("change",e=>{if(/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)){S.day=e.target.value;void refresh()}});
 $("filtersToggle").addEventListener("click",()=>{$("filtersPanel").hidden=!$("filtersPanel").hidden;$("filtersToggle").setAttribute("aria-expanded",String(!$("filtersPanel").hidden))});
 let timer; $("search").addEventListener("input",e=>{clearTimeout(timer);timer=setTimeout(()=>{S.q=e.target.value.trim();void refresh()},350)});
 $("professional").addEventListener("change",e=>{S.professional=e.target.value;void refresh()});
 $("status").addEventListener("change",e=>{S.status=e.target.value;void refresh()});
 $("clearFilters").addEventListener("click",()=>{S.q="";S.professional="";S.status="";$("search").value="";$("professional").value="";$("status").value="";void refresh()});
 $("viewSelect").addEventListener("change",e=>{S.view=e.target.value;FOCUS()});
 $("refresh").addEventListener("click",()=>void refresh());
 $("calendarSurface").addEventListener("click",e=>{const b=e.target.closest("button[data-select-day]");if(!b)return;const day=b.dataset.selectDay,range=BOUNDS();S.day=day;if(day<range.start||day>range.end){void refresh();return}HEAD();CALENDAR();void loadFocus()});
 $("appointments").addEventListener("click",e=>{const b=e.target.closest("button[data-appointment-index]");if(b)details(Number(b.dataset.appointmentIndex))});
 $("detailClose").addEventListener("click",()=>$("detailDialog").close());
 $("detailDialog").addEventListener("click",e=>{if(e.target===$("detailDialog"))$("detailDialog").close()});
}
async function boot(){
 bind();
 try{
  const me=await GET("/auth/me");if(me.role!=="admin"||me.maintenance?.active)throw Error("Esta prévia é exclusiva da administração principal.");
  const result=await GET("/api/v1/tenants");if(!Array.isArray(result))throw Error("Lista de empresas indisponível.");
  S.companies=result.filter(x=>x&&x.id&&x.name);
  $("businessSelect").innerHTML=S.companies.map(c=>'<option value="'+ESC(c.id)+'">'+ESC(c.name)+'</option>').join("")||'<option value="">Nenhuma empresa</option>';
  if(S.companies.length)await selectCompany(S.companies[0].id);
  else{$("summary").textContent="Nenhuma empresa cadastrada";$("appointments").innerHTML='<div class="empty-state">Cadastre uma empresa no painel para ver a agenda.</div>'}
 }catch(ex){ERROR(ex.message);$("summary").textContent="Acesso indisponível";$("appointments").innerHTML='<div class="empty-state"><strong>Não foi possível abrir a prévia</strong><p>Confira sua sessão no Orion.</p><p><a href="/painel?aba=agenda">Voltar à agenda atual</a></p></div>'}
}
void boot();
})();
