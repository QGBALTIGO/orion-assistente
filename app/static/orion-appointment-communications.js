(function(){'use strict';
const labels={confirmation:'Confirmação após receber o protocolo',professional_notice:'Avisar o profissional responsável',cancellation:'Notificar cancelamentos',rescheduling:'Notificar remarcações',reminder:'Lembrete uma hora antes',review:'Pedir avaliação após conclusão',return_invitation:'Convidar para agendar novamente'};
const states={pending:'Aguardando envio',sending:'Em processamento',accepted:'Aceito pelo transporte',delivered:'Entregue',read:'Lido',failed:'Falhou',unknown:'Resultado desconhecido',cancelled:'Cancelado'};
const events={professional_new:'Novo agendamento',professional_cancelled:'Cancelamento pelo cliente',professional_rescheduled:'Remarcação pelo cliente',customer_cancelled:'Cancelamento pela equipe',customer_rescheduled:'Remarcação pela equipe',reminder:'Lembrete de 1 hora',review:'Convite de avaliação'};
const reasons={transport_recipient_not_authorized:'Contato sem autorização no transporte',professional_contact_unavailable:'Profissional sem contato ativo',communication_not_authorized:'Comunicação não autorizada pelo contato',review_not_authorized:'Convite de avaliação não autorizado',transport_result_unknown:'Aguardando conferência do recibo',worker_interrupted:'Processamento interrompido; sem reenvio automático',expired:'Prazo de envio expirado',appointment_changed:'Agendamento alterado',settings_changed:'Configuração alterada',transport_refused:'Transporte recusou a mensagem',message_facts_invalid:'Dados insuficientes para compor a mensagem'};
const node=(tag,text,cls)=>{const n=document.createElement(tag);n.textContent=text||'';if(cls)n.className=cls;return n;};
async function mount({host,request,tenantId,canWrite=false}){
 if(!host)return;host.replaceChildren(node('p','Carregando comunicação…','os-help'));let data;
 const path='/api/v1/tenants/'+encodeURIComponent(tenantId)+'/appointment-communications';
 try{data=await request(path);}catch{host.replaceChildren(node('p','Não foi possível consultar a comunicação. Atualize a página.','os-help'));return;}
 host.replaceChildren();if(!data.available){host.append(node('p','O acompanhamento por WhatsApp está em preparação para esta empresa. Os avisos da agenda continuam disponíveis no painel.','os-help'));return;}
 const card=node('section','','os-card os-section-gap');card.append(node('h3','Comunicação de agendamentos'),node('p','Revise os contatos autorizados e as preferências antes de ativar. O envio usa a conexão atual da empresa.','os-help'));
 const form=document.createElement('form'),fields={};const values=data.settings;
 const toggle=(key,text)=>{const label=node('label','','os-check');const input=document.createElement('input');input.type='checkbox';input.checked=values[key]===true;input.disabled=!canWrite;fields[key]=input;label.append(input,document.createTextNode(' '+text));form.append(label);};
 toggle('enabled','Ativar comunicação de agendamentos');for(const [key,label] of Object.entries(labels))toggle(key,label);
 const signatureLabel=node('label','Assinatura das mensagens','os-field');const signature=document.createElement('input');signature.value=values.signature;signature.maxLength=80;signature.required=true;signature.disabled=!canWrite;signatureLabel.append(signature);form.append(signatureLabel);
 const delayLabel=node('label','Minutos após a conclusão para pedir avaliação','os-field');const delay=document.createElement('input');delay.type='number';delay.min='1';delay.max='1440';delay.value=String(values.review_delay_min);delay.disabled=!canWrite;delayLabel.append(delay);form.append(delayLabel);
 const consentLabel=node('label','','os-check');const consent=document.createElement('input');consent.type='checkbox';consent.disabled=!canWrite;consentLabel.append(consent,document.createTextNode(' Revisei as mensagens e tenho autorização para avisar os contatos cadastrados.'));form.append(consentLabel);
 form.append(node('p','O pedido de avaliação e o convite de retorno dependem das preferências do cliente. Atendimentos cancelados ou faltas não recebem avaliações.','os-help'));
 const status=node('p','','os-help');status.setAttribute('role','status');form.append(status);
 const save=node('button','Salvar comunicação','os-btn');save.type='submit';save.disabled=!canWrite;form.append(save);
 form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{const body={expected_version:values.version,channel_id:data.canonical_channel_id,signature:signature.value,review_delay_min:Number(delay.value),acknowledged:consent.checked};for(const [key,input] of Object.entries(fields))body[key]=input.checked;await request(path,'PUT',body);status.textContent='Configuração salva.';await mount({host,request,tenantId,canWrite});}catch(error){status.textContent=error.message||'Não foi possível salvar. Atualize e tente novamente.';save.disabled=!canWrite;}};
 card.append(form);host.append(card);
 const list=node('section','','os-card os-section-gap');list.append(node('h3','Acompanhamento dos envios'),node('p',data.transport_limit,'os-help'));
 if(!data.notifications.length)list.append(node('p','Os próximos avisos aparecerão aqui.','os-help'));
 for(const item of data.notifications){const row=node('div','','os-summary-row');const left=node('div','');left.append(node('strong',events[item.event]||'Aviso de agendamento'),node('p',item.protocol+' · '+(item.recipient_kind==='professional'?'Profissional':'Cliente'),'os-help'));const right=node('div','');right.append(node('strong',states[item.status]||'Em conferência'));if(item.last_error)right.append(node('p',reasons[item.last_error]||'Envio não concluído; consulte a configuração do canal.','os-help'));row.append(left,right);list.append(row);}host.append(list);
 const reviews=node('section','','os-card os-section-gap');reviews.append(node('h3','Avaliações privadas'));
 if(!data.reviews.length)reviews.append(node('p','Ainda não há avaliações recebidas.','os-help'));
 for(const item of data.reviews){const row=node('div','','os-summary-row');row.append(node('strong',item.rating+'/5 · '+item.professional),node('p',item.comment||'Sem comentário','os-help'));reviews.append(row);}host.append(reviews);
}
window.OrionAppointmentCommunications={mount};
})();
