(function(){
'use strict';
const $=id=>document.getElementById(id);
const token=location.hash.slice(1);
let current=null,busy=false,operation=null;
let theme=matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';
document.documentElement.dataset.theme=theme;
$('theme').onclick=()=>{theme=theme==='dark'?'light':'dark';document.documentElement.dataset.theme=theme;};
const statusNames={hold:'Reserva temporária',confirmed:'Agendamento confirmado!',completed:'Atendimento concluído!',cancelled:'Agendamento cancelado',no_show:'Comparecimento não registrado'};
const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n;};
function showError(error){$('message').textContent=error.message||'Não foi possível conectar. Tente novamente em instantes.';}
async function request(action,extra={}){
 const response=await fetch('/api/appointment-access',{method:'POST',credentials:'same-origin',cache:'no-store',referrerPolicy:'no-referrer',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,action,...extra})});
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Não foi possível concluir. Atualize e tente novamente.');return data;
}
async function run(fn){if(busy)return;busy=true;document.querySelector('main').setAttribute('aria-busy','true');document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){showError(e);}finally{busy=false;document.querySelector('main').setAttribute('aria-busy','false');document.querySelectorAll('button').forEach(b=>b.disabled=false);$('confirm-reschedule').disabled=!$('slot').value;}}
function button(text,fn,secondary=false){const n=node('button',text);n.type='button';if(secondary)n.className='secondary';n.onclick=fn;return n;}
function link(text,url){if(!url)return;try{const u=new URL(url,location.origin);if(u.protocol!=='https:'||u.username||u.password)return;const n=node('a',text);n.href=u.href;n.className='action';n.rel='noreferrer noopener';return n;}catch{}}
function render(data){
 current=data;operation=null;$('company').textContent=data.company;$('title').textContent=statusNames[data.status]||'Seu agendamento';
 $('message').textContent=data.status==='completed'?`Obrigado pela visita, ${data.customer.split(' ')[0]}! Foi um prazer receber você.`:data.can_change?'Você pode cancelar ou remarcar até 1 hora antes do atendimento.':data.status==='confirmed'?'Seu agendamento está próximo e não pode mais ser alterado por esta página. Para solicitar ajuda, entre em contato com a empresa.':'Confira abaixo o estado atual do seu atendimento.';
 const when=new Date(data.start_at);const date=new Intl.DateTimeFormat('pt-BR',{dateStyle:'long',timeZone:data.timezone}).format(when);const time=new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:data.timezone}).format(when);
 $('details').replaceChildren();for(const [key,value] of [['Cliente',data.customer],['Serviço',data.services.join(' + ')],['Profissional',data.professional],['Data',date],['Horário',time],['Duração',data.duration_min+' minutos'],['Protocolo',data.protocol],['Endereço',data.address]]){if(!value)continue;const row=node('div','');row.append(node('dt',key),node('dd',value));$('details').append(row);}
 const actions=$('actions');actions.replaceChildren();$('reschedule').hidden=true;$('review').hidden=!data.can_review;
 if(data.can_change&&data.pending_change){const proposed=new Intl.DateTimeFormat('pt-BR',{dateStyle:'long',timeStyle:'short',timeZone:data.timezone}).format(new Date(data.pending_change.new_start_at));actions.append(node('p','A equipe propôs '+proposed+'. Seu horário atual permanece válido até você aceitar. O novo horário será conferido na confirmação.'));for(const [title,action] of [['Aceitar novo horário','accept_change'],['Manter meu horário','decline_change']])actions.append(button(title,()=>void run(async()=>{const proposal=data.pending_change;render(await request(action,{expected_updated_at:current.updated_at,idempotency_key:crypto.randomUUID(),proposal_id:proposal.id}));}),action==='decline_change'));}
 if(data.can_change){actions.append(button('Remarcar atendimento',()=>{$('reschedule').hidden=false;$('day').focus();}),button('Cancelar agendamento',()=>$('confirmation').showModal(),true));}
 if(data.review)actions.append(node('p','Sua avaliação foi recebida. Obrigado!'));
 if(data.status==='completed'&&data.has_future_booking)actions.append(node('p','Seu próximo horário já está garantido!'));
 else if(['completed','cancelled','no_show'].includes(data.status)){const next=link('Agendar novamente',data.new_booking_url);if(next)actions.append(next);}
 if(data.contact_phone&&/^\+55[1-9][0-9]{9,10}$/.test(data.contact_phone)){const contact=link('Falar com a empresa','https://wa.me/'+data.contact_phone.slice(1));if(contact){contact.classList.add('secondary');actions.append(contact);}}
}
$('confirmation').addEventListener('close',()=>{if($('confirmation').returnValue!=='cancel')return;void run(async()=>{operation=operation||crypto.randomUUID();render(await request('cancel',{expected_updated_at:current.updated_at,idempotency_key:operation}));});});
$('find-slots').onclick=()=>void run(async()=>{if(!$('day').value)throw Error('Escolha uma data.');const data=await request('slots',{day:$('day').value});$('slot').replaceChildren();const first=node('option',data.slots.length?'Escolha um horário':'Sem horários disponíveis nesta data');first.value='';$('slot').append(first);for(const value of data.slots){const n=node('option',new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:data.timezone}).format(new Date(value)));n.value=value;$('slot').append(n);}});
$('slot').onchange=()=>{$('confirm-reschedule').disabled=!$('slot').value;operation=null;};
$('confirm-reschedule').onclick=()=>void run(async()=>{if(!$('slot').value)throw Error('Escolha um horário.');operation=operation||crypto.randomUUID();render(await request('reschedule',{expected_updated_at:current.updated_at,idempotency_key:operation,start_at:$('slot').value}));});
for(let n=1;n<=5;n++){const label=node('label','');const input=document.createElement('input');input.type='radio';input.name='rating';input.value=String(n);input.required=true;input.setAttribute('aria-label',n+(n===1?' estrela':' estrelas'));const span=node('span','★');span.setAttribute('aria-hidden','true');label.append(input,span);$('stars').append(label);}
$('review').onsubmit=e=>{e.preventDefault();void run(async()=>{const chosen=document.querySelector('input[name=rating]:checked');if(!chosen)throw Error('Escolha sua avaliação.');render(await request('review',{rating:Number(chosen.value),comment:$('comment').value}));});};
if(!token){$('message').textContent='Abra o link exclusivo enviado pela empresa para acompanhar seu atendimento.';}else void run(async()=>render(await request('view')));
})();
