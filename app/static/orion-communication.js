/* Same factual server composition as jobs. No provider or send API is used here. */
(function(){
  'use strict';let active=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const events={booking_confirmed:'Confirmação',reminder_24h:'Lembrete 24h',reminder_2h:'Lembrete 2h',reminder_1h:'Lembrete 1h',rescheduled:'Remarcação',cancelled:'Cancelamento imediato',inactive_60d:'Retorno',cancelled_7d:'Pós-cancelamento',birthday_7d:'Aniversário 7 dias',birthday_2d:'Aniversário 2 dias',loyalty_reward:'Fidelidade',after_service:'Pós-atendimento'};
  const reasons={before_activation:'O evento é anterior à autorização atual. Não será reenviado automaticamente.',quiet_window_expired:'A janela permitida começa depois do prazo deste lembrete. O aviso precisa de outra política revisada ou da equipe.',expired:'O prazo do aviso já passou.',birthday_calendar_policy:'A data depende da política de 29/02 ou ainda não está no período do aniversário.',not_eligible_or_not_due:'O gatilho ou o estado atual ainda não permite este aviso.',human_paused:'A conversa está com a equipe; o aviso permanece como pendência.',missing_or_unapproved_fact:'Falta um dado obrigatório ou um link aprovado.'};
  function unmount(){if(active){active.alive=false;active.abort.abort();active=null;}}
  async function mount({host,request,tenantId,canWrite=false,canAcknowledge=canWrite}){
    unmount();if(!host)return;
    const s={alive:true,abort:new AbortController(),epoch:0,config:null,business:null,customers:[],search:'',timezone:null,appointments:[],pending:[],customer:'',appointment:'',event:'booking_confirmed',result:null,busy:false};active=s;
    const current=()=>active===s&&s.alive&&host.isConnected;
    const base='/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management';
    const call=(path,method,payload)=>request(base+path,method,payload);
    const status=(value,error=false)=>{const el=host.querySelector('[data-communication-status]');if(el){el.textContent=value;el.setAttribute('role',error?'alert':'status');}};
    function render(){
      if(!current())return;
      const policy=s.config?.configuration?.delivery_policy||{},disabled=s.busy?'disabled':'';
      host.innerHTML='<h2>Conferir mensagens e pendências</h2><p>Esta área prepara e confere o texto. Nenhuma ação aqui envia mensagens, inicia campanhas ou devolve uma conversa à IA.</p><p data-communication-status role="status" aria-live="polite"></p>'+
        '<form data-communication-search class="os-form"><label>Buscar cliente pelo nome ou telefone<input name="q" maxlength="120" value="'+esc(s.search)+'"></label><button class="os-button os-outline" '+disabled+'>Buscar</button></form><p>Até 50 resultados por busca.</p><form data-communication-preview class="os-form"><label>Modelo<select aria-label="Modelo" name="event">'+Object.entries(events).map(([key,label])=>'<option value="'+key+'" '+(s.event===key?'selected':'')+'>'+label+'</option>').join('')+'</select></label><label>Cliente<select aria-label="Cliente" name="customer" required><option value="">Escolha um cliente</option>'+s.customers.map(c=>'<option value="'+esc(c.id)+'" '+(s.customer===c.id?'selected':'')+'>'+esc(c.display_name||c.name||c.phone)+'</option>').join('')+'</select></label><label>Agendamento<select aria-label="Agendamento" name="appointment"><option value="">Não se aplica / escolha um agendamento</option>'+s.appointments.map(a=>'<option value="'+esc(a.id)+'" '+(s.appointment===a.id?'selected':'')+'>'+esc(a.protocol+' · '+(s.timezone?new Intl.DateTimeFormat('pt-BR',{timeZone:s.timezone,dateStyle:'short',timeStyle:'short'}).format(new Date(a.start_at)):'Horário a conferir')+' · '+a.professional+' · '+({confirmed:'Confirmado',hold:'Em conferência',cancelled:'Cancelado',completed:'Concluído',no_show:'Não compareceu'}[a.status]||'Conferir situação'))+'</option>').join('')+'</select></label><button class="os-button" '+disabled+'>Conferir prévia com dados reais</button></form>'+
        (s.result?'<section class="os-card"><h3>Prévia sem envio</h3><p>'+esc(s.result.unsaved_draft?'Modelo de referência: ainda não salvo nesta versão.':'Texto da configuração salva, com a mesma composição usada na fila.')+'</p><pre class="os-inbox-text" style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(s.result.body)+'</pre><p>'+esc(reasons[s.result.schedule_reason]||'A programação foi calculada; envio e entrega ainda exigem todas as autorizações.')+'</p><p>Versão '+esc(s.result.preferences_version)+' · '+esc(s.result.presentation.utf16_units)+' unidades no limite do texto. A aparência no WhatsApp precisa ser conferida antes da ativação.</p></section>':'')+
        '<details class="os-card"><summary>Horários e datas de envio</summary><p>A janela operacional é separada da promocional. Alterações exigem nova revisão dos envios. A opção de 29/02 não presume uma data alternativa.</p><form data-communication-policy class="os-form">'+
        [['operational_start_hour','Avisos de agenda: início',9],['operational_end_hour','Avisos de agenda: fim',18],['marketing_start_hour','Promoções: início',9],['marketing_end_hour','Promoções: fim',18]].map(([name,label,fallback])=>'<label>'+label+'<input name="'+name+'" type="number" min="'+(name.includes('start')?'0':'1')+'" max="'+(name.includes('start')?'23':'24')+'" required value="'+esc(policy[name]??fallback)+'"></label>').join('')+
        '<fieldset><legend>Dias permitidos</legend>'+['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo'].map((day,index)=>'<label class="os-checkbox"><input type="checkbox" name="weekdays" value="'+index+'" '+((policy.weekdays||[0,1,2,3,4,5,6]).includes(index)?'checked':'')+'> '+day+'</label>').join('')+'</fieldset><label>Feriados sem envio (AAAA-MM-DD, um por linha)<textarea aria-label="Feriados sem envio" name="holidays" rows="3">'+esc((policy.holidays||[]).join('\n'))+'</textarea></label><label>Aniversário em 29/02<select aria-label="Aniversário em 29/02" name="birthday_leap_day">'+[['skip','Não enviar em ano não bissexto'],['feb28','Usar 28/02, se revisado pela empresa'],['mar01','Usar 01/03, se revisado pela empresa']].map(([key,label])=>'<option value="'+key+'" '+((policy.birthday_leap_day||'skip')===key?'selected':'')+'>'+label+'</option>').join('')+'</select></label><button class="os-button os-outline" '+(!canWrite||s.busy?'disabled':'')+'>Salvar política para revisão</button></form></details>'+
        '<details class="os-card"><summary>Domínios de links aprovados</summary><p>Links de avaliação Google conhecidos já são aceitos. Para outro destino publicado pela empresa, revise e informe o domínio completo, sem caminho ou curinga.</p><form data-communication-links class="os-form"><label>Domínios autorizados<textarea aria-label="Domínios autorizados" name="hosts" rows="3">'+esc((s.business?.business?.approved_link_hosts||[]).join('\n'))+'</textarea></label><button class="os-button os-outline" '+(!canWrite||s.busy?'disabled':'')+'>Salvar domínios para revisão</button></form></details>'+
        '<section class="os-card"><h3>Avisos que precisam da equipe</h3>'+pendingHTML(s.pending,canAcknowledge,s.busy)+'</section>';
    }
    async function refresh(){const epoch=++s.epoch;const [config,customers,pending,business]=await Promise.all([call('/marketing/automations'),call('/customers?limit=50&status=active'),call('/communication/pending'),call('/preferences')]);if(!current()||epoch!==s.epoch)return;s.config=config;s.business=business;s.customers=customers.items||[];s.pending=pending.items||[];render();}
    host.addEventListener('change',event=>{
      if(event.target.name==='event'){s.event=event.target.value;s.result=null;++s.epoch;render();}
      if(event.target.name==='appointment'){s.appointment=event.target.value;s.result=null;++s.epoch;render();}
      if(event.target.name==='customer'){
        s.customer=event.target.value;s.appointment='';s.result=null;s.appointments=[];
        const customer=s.customer,epoch=++s.epoch;
        if(!customer){render();return;}
        void call('/communication/customers/'+encodeURIComponent(customer)+'/appointments').then(result=>{if(current()&&epoch===s.epoch&&s.customer===customer){s.appointments=result.items||[];s.timezone=result.timezone||null;render();}}).catch(error=>{if(current()&&epoch===s.epoch)status(error.message,true);});
      }
    },{signal:s.abort.signal});
    host.addEventListener('submit',event=>{
      if(!event.target.matches('[data-communication-search],[data-communication-preview],[data-communication-policy],[data-communication-links]'))return;
      event.preventDefault();if(s.busy||!s.config)return;
      const form=event.target,fields=Object.fromEntries(new FormData(form)),epoch=++s.epoch;s.busy=true;
      const work=async()=>{
        if(form.matches('[data-communication-search]')){
          const result=await call('/customers?limit=50&status=active&q='+encodeURIComponent(fields.q));
          if(current()&&epoch===s.epoch){s.search=fields.q;s.customers=result.items||[];s.customer='';s.appointment='';s.appointments=[];s.result=null;render();}
        }else if(form.matches('[data-communication-preview]')){
          s.appointment=fields.appointment||'';
          const result=await call('/marketing/automations/preview','POST',{event:fields.event,customer_id:fields.customer,appointment_id:fields.appointment||null,expected_preferences_version:s.config.version});
          if(current()&&epoch===s.epoch){s.result=result;s.event=fields.event;if(result.timezone)s.timezone=result.timezone;if(result.appointment){s.appointments=s.appointments.filter(a=>a.id!==result.appointment.id);s.appointments.unshift(result.appointment);}render();}
        }else if(canWrite&&form.matches('[data-communication-links]')){
          await call('/preferences','PUT',{expected_version:s.business.version,business:{...s.business.business,approved_link_hosts:fields.hosts.split(/\s+/).filter(Boolean)}});
          if(current()&&epoch===s.epoch){s.result=null;await refresh();status('Domínios salvos. Revise novamente as regras antes de autorizar qualquer envio.');}
        }else if(canWrite){
          const previous=s.config.configuration||{},policy={...(previous.delivery_policy||{})};
          for(const key of ['operational_start_hour','operational_end_hour','marketing_start_hour','marketing_end_hour'])policy[key]=Number(fields[key]);
          policy.weekdays=new FormData(form).getAll('weekdays').map(Number);policy.holidays=fields.holidays.split(/\s+/).filter(Boolean);policy.birthday_leap_day=fields.birthday_leap_day;
          await call('/marketing/automations','PUT',{expected_version:s.config.version,configuration:{...previous,delivery_policy:policy}});
          if(current()&&epoch===s.epoch){s.result=null;await refresh();status('Política salva para nova revisão. Nenhum envio foi ativado.');}
        }
      };
      void work().catch(error=>{if(current()&&epoch===s.epoch){s.result=null;status(error.message||'Não foi possível concluir.',true);}}).finally(()=>{s.busy=false;if(current())host.querySelectorAll('button').forEach(b=>{if(b.closest('[data-communication-preview],[data-communication-search]')||canWrite||b.matches('[data-communication-ack]')&&canAcknowledge)b.disabled=false;});});
    },{signal:s.abort.signal});
    host.addEventListener('click',event=>{
      const button=event.target.closest('[data-communication-ack]');if(!button||!canAcknowledge||s.busy)return;
      const row=s.pending.find(x=>x.id===button.dataset.communicationAck);if(!row)return;
      const epoch=++s.epoch;s.busy=true;
      void call('/communication/pending/'+encodeURIComponent(row.id)+'/acknowledge','POST',{expected_version:row.version}).then(async()=>{if(current()&&epoch===s.epoch){await refresh();status('Pendência marcada como vista. Nenhuma mensagem enviada.');}}).catch(error=>{if(current()&&epoch===s.epoch)status(error.message,true);}).finally(()=>{s.busy=false;if(current()){const message=host.querySelector('[data-communication-status]')?.textContent||'';render();status(message);}});
    },{signal:s.abort.signal});
    host.textContent='Consultando a comunicação…';try{await refresh();}catch(error){if(current())host.textContent=error.message||'Não foi possível consultar. Atualize para tentar novamente.';}
  }
  function pendingHTML(rows,canWrite=false,busy=false){return rows.length?'<ul>'+rows.map(row=>'<li><strong>'+esc(events[row.event]||'Aviso de agenda')+'</strong> · '+esc(row.contact_e164)+'<p>'+esc(row.status==='obsolete'?'O agendamento já mudou; confira a situação atual.':reasons[row.reason]||'Confira o evento antes de orientar o cliente.')+'</p>'+ (canWrite?'<button type="button" class="os-button os-outline" '+(busy?'disabled ':'')+'data-communication-ack="'+esc(row.id)+'">Marcar como vista</button>':'')+'</li>').join('')+'</ul>':'<p>Nenhum aviso pendente neste recorte.</p>';}
  window.OrionCommunication={mount,unmount,pendingHTML};
})();
