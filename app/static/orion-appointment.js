(function(){
'use strict';
const $=id=>document.getElementById(id);
const token=location.hash.slice(1);
let current=null,busy=false,operation=null,navigating=false;
// A second WhatsApp link can reuse this tab with a different fragment. Clear
// the old view immediately and authenticate the new capability in a fresh page.
window.addEventListener('hashchange',()=>{navigating=true;current=null;document.querySelector('main').hidden=true;location.reload();});
// Appearance is managed by the same orion-public-theme.js used on /s/*.
// It persists the site preference, follows the system, and updates the icon.
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
function accent(value){
 const color=/^#[0-9a-f]{6}$/i.test(value||'')?value:'#2e7bf6';
 const rgb=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
 const luminance=.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];
 document.documentElement.style.setProperty('--primary',color);
 document.documentElement.style.setProperty('--primary-ink',luminance>.179?'#000':'#fff');
}
function render(data){
 if(navigating||token!==location.hash.slice(1))return;
 current=data;operation=null;accent(data.brand_color);$('footer-title').textContent=data.company;const logo=$('company-logo');logo.hidden=!(data.logo_url&&/^\/media\/[0-9a-f-]{36}\/[0-9a-f-]{36}\.webp$/.test(data.logo_url));if(!logo.hidden){logo.src=data.logo_url;logo.alt=data.company;}else logo.removeAttribute('src');$('company').textContent=data.company;$('title').textContent=statusNames[data.status]||'Seu agendamento';
 $('message').textContent=data.status==='completed'?`Obrigado pela visita, ${data.customer.split(' ')[0]}! Foi um prazer receber você.`:data.can_change?'Você pode cancelar ou remarcar até 1 hora antes do atendimento.':data.status==='confirmed'?'Seu agendamento está próximo e não pode mais ser alterado por esta página. Para solicitar ajuda, entre em contato com a empresa.':'Confira abaixo o estado atual do seu atendimento.';
 const when=new Date(data.start_at);const date=new Intl.DateTimeFormat('pt-BR',{dateStyle:'long',timeZone:data.timezone}).format(when);const time=new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:data.timezone}).format(when);
 // Review links show only the information needed to recognize the service.
 // Other appointment states preserve the complete management receipt.
 const reviewMode=data.status==='completed' && (data.can_review===true || Boolean(data.review));
 document.querySelector('.card').classList.toggle('review-mode',reviewMode);
 const conciseDate=new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:data.timezone}).format(when);
 const facts=reviewMode?
   [['Serviço',data.services.join(' + ')],['Profissional',data.professional],['Data e horário',conciseDate+' às '+time]]:
   [['Cliente',data.customer],['Serviço',data.services.join(' + ')],['Profissional',data.professional],['Data',date],['Horário',time],['Duração',data.duration_min+' minutos'],['Protocolo',data.protocol],['Endereço',data.address]];
 $('details').replaceChildren();
 for(const [key,value] of facts){if(!value)continue;const row=node('div','');row.append(node('dt',key),node('dd',value));$('details').append(row);}
 const actions=$('actions');actions.replaceChildren();$('reschedule').hidden=true;$('review').hidden=!data.can_review;
 if(data.can_change&&data.pending_change){const proposed=new Intl.DateTimeFormat('pt-BR',{dateStyle:'long',timeStyle:'short',timeZone:data.timezone}).format(new Date(data.pending_change.new_start_at));actions.append(node('p','A equipe propôs '+proposed+'. Seu horário atual permanece válido até você aceitar. O novo horário será conferido na confirmação.'));for(const [title,action] of [['Aceitar novo horário','accept_change'],['Manter meu horário','decline_change']])actions.append(button(title,()=>void run(async()=>{const proposal=data.pending_change;render(await request(action,{expected_updated_at:current.updated_at,idempotency_key:crypto.randomUUID(),proposal_id:proposal.id}));}),action==='decline_change'));}
 if(data.can_change){actions.append(button('Remarcar atendimento',()=>{$('reschedule').hidden=false;$('day').focus();}),button('Cancelar agendamento',()=>$('confirmation').showModal(),true));}
 if(data.review)actions.append(node('p','Sua avaliação foi recebida. Obrigado!'));
 if(!reviewMode){
   if(data.status==='completed'&&data.has_future_booking)actions.append(node('p','Seu próximo horário já está garantido!'));
   else if(['completed','cancelled','no_show'].includes(data.status)){for(const suggestion of data.return_suggestions||[]){actions.append(node('p',suggestion.service+': retorno sugerido a partir de '+new Intl.DateTimeFormat('pt-BR',{dateStyle:'long',timeZone:'UTC'}).format(new Date(suggestion.from_date+'T12:00:00Z'))));}const next=link('Agendar novamente',data.new_booking_url);if(next)actions.append(next);}
 }
 if(data.contact_phone&&/^\+55[1-9][0-9]{9,10}$/.test(data.contact_phone)){const contact=link(data.central_whatsapp?'Falar com o Orion':'Falar com a empresa','https://wa.me/'+data.contact_phone.slice(1));if(contact){contact.classList.add('secondary');actions.append(contact);}}
}
$('confirmation').addEventListener('close',()=>{if($('confirmation').returnValue!=='cancel')return;void run(async()=>{operation=operation||crypto.randomUUID();render(await request('cancel',{expected_updated_at:current.updated_at,idempotency_key:operation}));});});
$('find-slots').onclick=()=>void run(async()=>{if(!$('day').value)throw Error('Escolha uma data.');const data=await request('slots',{day:$('day').value});$('slot').replaceChildren();const first=node('option',data.slots.length?'Escolha um horário':'Sem horários disponíveis nesta data');first.value='';$('slot').append(first);for(const value of data.slots){const n=node('option',new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit',timeZone:data.timezone}).format(new Date(value)));n.value=value;$('slot').append(n);}});
$('slot').onchange=()=>{$('confirm-reschedule').disabled=!$('slot').value;operation=null;};
$('confirm-reschedule').onclick=()=>void run(async()=>{if(!$('slot').value)throw Error('Escolha um horário.');operation=operation||crypto.randomUUID();render(await request('reschedule',{expected_updated_at:current.updated_at,idempotency_key:operation,start_at:$('slot').value}));});
// Fill every star up to the selected rating. Keep native radios so keyboard
// arrows, forms and screen readers retain their usual accessible behavior.
const stars=$('stars'),ratingFeedback=$('rating-feedback');
const ratingLabels=['','Muito ruim','Ruim','Regular','Bom','Excelente'];
function paintRating(preview=0){
 const selected=Number(stars.querySelector('input:checked')?.value||0);
 const filled=preview||selected;
 stars.querySelectorAll('label').forEach((label,index)=>{
   label.classList.toggle('is-filled',index<filled);
   label.classList.toggle('is-selected',index+1===selected);
 });
 ratingFeedback.textContent=selected?
   selected+' de 5 estrelas — '+ratingLabels[selected]:
   'Selecione de 1 a 5 estrelas.';
}
for(let n=1;n<=5;n++){
 const label=node('label',''),input=document.createElement('input');
 input.type='radio';input.name='rating';input.value=String(n);input.required=true;
 input.setAttribute('aria-label',n+(n===1?' estrela':' estrelas'));
 input.setAttribute('aria-describedby','rating-feedback');
 const icon=node('span','★');icon.setAttribute('aria-hidden','true');
 label.append(input,icon);
 input.addEventListener('change',()=>paintRating());
 label.addEventListener('pointerenter',event=>{if(event.pointerType==='mouse')paintRating(n);});
 stars.append(label);
}
stars.addEventListener('pointerleave',()=>paintRating());
paintRating();
$('review').onsubmit=e=>{e.preventDefault();void run(async()=>{const chosen=document.querySelector('input[name=rating]:checked');if(!chosen)throw Error('Escolha sua avaliação.');render(await request('review',{rating:Number(chosen.value),comment:$('comment').value}));});};
if(!token){$('message').textContent='Abra o link exclusivo enviado pela empresa para acompanhar seu atendimento.';}else void run(async()=>render(await request('view')));
})();
