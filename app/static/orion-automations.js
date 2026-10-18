/* Owner-reviewed execution on the existing campaign/consent domain. No token storage. */
(function () {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const list=v=>Array.isArray(v)?v:[];
  let active;
  function mount({host,request,tenantId,canManage=false,campaigns=[],onExecutionChange}){
    unmount();if(!host||!tenantId||typeof request!=='function')return;
    let alive=true,read=0,busy=false,pending=null,execution=null,jobs=null,channels=[],review=null,consent=null,feedback='',failed=false;
    let readReady=false,loaded=false,phoneDraft='',pauseReview=null;
    const base='/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/marketing';
    const current=epoch=>alive&&epoch===read;
    const validVersion=value=>Number.isSafeInteger(value)&&value>=0;
    const writable=()=>canManage&&readReady&&execution?.available===true&&execution.reconciliation_only!==true;
    const button=(label,action,extra='')=>'<button type="button" class="os-button os-outline os-small" data-oa-action="'+action+'" '+extra+'>'+esc(label)+'</button>';
    const input=(name,label,attrs='')=>'<label class="os-field"><span>'+esc(label)+'</span><input name="'+name+'" '+attrs+'></label>';
    const checkbox=(name,label,checked=false)=>'<label class="os-check"><input type="checkbox" name="'+name+'"'+(checked?' checked':'')+'><span>'+esc(label)+'</span></label>';
    function render(){if(!alive)return;
      let html='<header class="os-card-header"><div><h2>Envios revisados</h2><p>Salvar uma regra prepara o conteúdo. A autorização de envio é separada.</p></div>'+button('Atualizar','refresh')+'</header>';
      if(feedback)html+='<p role="'+(failed?'alert':'status')+'">'+esc(feedback)+'</p>';
      if(pending)html+='<p role="alert">O resultado da alteração ainda não foi confirmado. Repita somente o mesmo pedido para recuperar a resposta.</p>'+button('Conferir o mesmo pedido','retry');
      if(!execution)html+='<p role="status">'+(loaded?'A autorização atual não pôde ser consultada. Atualize antes de alterar os envios.':'Consultando a autorização de envio…')+'</p>';
      else if(execution.available!==true)html+='<p>A execução ainda precisa ser habilitada nesta instalação. Suas regras e campanhas continuam em rascunho.</p>';
      else{
        html+='<p><strong>'+(!readReady?'Autorização a verificar':execution.reconciliation_only===true?'Somente acompanhamento de envios anteriores':execution.enabled===true?'Envios autorizados':'Envios pausados')+'</strong>'+(execution.window?' · Janela de '+esc(execution.window):'')+(execution.timezone?' · '+esc(execution.timezone):'')+'</p><p class="os-help">Somente contatos com o consentimento correspondente podem receber mensagens. Respostas do cliente ou atendimento humano interrompem os envios pendentes.</p>';
        if(canManage&&execution.reconciliation_only!==true){
          html+='<form data-oa-form="execution"><fieldset '+(busy||pending||!writable()?'disabled':'')+'><legend>Autorizar as rotinas desta empresa</legend><label class="os-field"><span>Conexão WhatsApp</span><select name="channel_id"><option value="">Escolha uma conexão autorizada</option>'+channels.filter(c=>c.enabled===true).map(c=>'<option value="'+esc(c.id)+'"'+(c.id===execution.channel_id?' selected':'')+'>'+esc(c.label)+'</option>').join('')+'</select></label>'+input('daily_limit','Limite de tentativas por dia','type="number" min="1" max="100" required value="'+esc(execution.daily_limit||20)+'"')+checkbox('enabled','Autorizar envios das regras salvas',execution.enabled===true)+checkbox('acknowledged','Revisei as regras salvas, a configuração, o canal, os consentimentos, o limite diário e a janela de 9h às 18h.')+'<button class="os-button" type="submit">Salvar autorização</button></fieldset></form>';
          html+='<details class="os-section-gap"><summary>Consentimentos e pausa dos contatos</summary><form data-oa-form="load-consent">'+input('phone','WhatsApp do cliente','required type="tel" value="'+esc(phoneDraft)+'" placeholder="+55 com DDD" pattern="\\+55[1-9][0-9]{9,10}"')+'<button class="os-button os-outline" type="submit" '+(busy||pending||!writable()?'disabled':'')+'>Consultar contato</button></form>';
          if(consent){
            html+='<form data-oa-form="consent"><fieldset '+(busy||pending||!writable()?'disabled':'')+'><legend>Contato final '+esc(consent.phone.slice(-4))+'</legend>'+checkbox('operational_opt_in','Autorizou avisos dos agendamentos',consent.operational_opt_in)+checkbox('marketing_opt_in','Autorizou campanhas e ofertas',consent.marketing_opt_in)+input('evidence','Referência da autorização recebida','required minlength="8" maxlength="200" placeholder="Ex.: autorização registrada no atendimento"')+'<p class="os-help">Registre apenas autorizações recebidas do próprio cliente. Cadastrar o telefone não concede consentimento.</p><button class="os-button" type="submit">Salvar consentimentos</button></fieldset></form>';
            html+='<p><strong>'+(consent.human_paused===true?'Envios deste contato pausados':consent.human_paused===false?'Sem pausa de atendimento registrada para este contato':'Pausa deste contato a verificar')+'</strong></p><p class="os-help">Retomar não concede consentimento, não recria envios antigos e não reenvia mensagens. Um atendimento humano ainda ativo pode manter a pausa.</p>';
            if(typeof consent.human_paused==='boolean')html+=button(consent.human_paused?'Revisar retomada deste contato':'Revisar pausa deste contato','contact-review',busy||pending||!writable()?'disabled':'');
            if(typeof pauseReview==='boolean')html+='<form data-oa-form="contact-pause"><fieldset '+(busy||pending||!writable()?'disabled':'')+'><legend>'+(pauseReview?'Pausar o contato':'Solicitar retomada para o contato')+' final '+esc(consent.phone.slice(-4))+'</legend>'+checkbox('pause_acknowledged',pauseReview?'Quero interromper os envios pendentes deste contato.':'Entendi que esta ação não recria nem reenvia mensagens anteriores.')+'<button class="os-button" type="submit">'+(pauseReview?'Confirmar pausa':'Solicitar retomada')+'</button></fieldset></form>';
          }
          html+='</details><details class="os-section-gap"><summary>Revisar uma campanha para envio</summary><label class="os-field"><span>Campanha salva</span><select data-oa-campaign '+(busy||pending||!writable()?'disabled':'')+'><option value="">Escolha uma campanha</option>'+list(campaigns).filter(c=>c.status!=='archived').map(c=>'<option value="'+esc(c.id)+'"'+(c.id===review?.id?' selected':'')+'>'+esc(c.name)+'</option>').join('')+'</select></label>';
          if(review){html+='<div class="os-marketing-output">'+esc(review.message)+'</div><p>'+Number(review.eligible_count||0)+' contatos com consentimento no público revisado.</p>';
            if(review.status==='draft')html+='<form data-oa-form="approve"><fieldset '+(busy||pending||!writable()?'disabled':'')+'>'+checkbox('acknowledged','Revisei esta mensagem e autorizo o envio ao público indicado.')+'<button class="os-button" type="submit" '+(execution.enabled!==true||!review.eligible_count?'disabled':'')+'>Autorizar campanha</button></fieldset></form>';
            else if(review.status==='approved')html+=button('Pausar campanha','pause',busy||pending||!writable()?'disabled':'');
          }html+='</details>';
        }
        const labels={pending:'Programado',sending:'Tentativa em andamento',sent:'Aceito pelo conector',cancelled:'Interrompido',uncertain:'Resultado incerto',failed:'Falha'};
        html+='<section class="os-section-gap"><h3>Acompanhamento</h3><p class="os-help">Aceito pelo conector não comprova leitura. Resultados incertos são consultados pelo mesmo identificador e nunca reenviados automaticamente.</p>'+(jobs===null?'<p>Acompanhamento indisponível. Não foi possível consultar os envios.</p>':jobs.length?'<ul class="os-feature-list">'+jobs.map(j=>'<li><div><strong>'+esc(labels[j.status]||'A verificar')+'</strong> · contato final '+esc(j.recipient_suffix)+'<br><small>'+esc(j.delivery_status==='read'?'WhatsApp informou leitura':j.delivery_status==='delivered'?'WhatsApp informou entrega':'Entrega ainda não comprovada')+'</small>'+(canManage&&readReady&&['sent','uncertain'].includes(j.status)?'<div>'+button('Consultar entrega','reconcile','data-oa-job="'+esc(j.id)+'" '+(busy||pending?'disabled':''))+'</div>':'')+'</div></li>').join('')+'</ul>':'<p>Nenhum envio registrado.</p>')+'</section>';
      }
      host.innerHTML=html;host.setAttribute('aria-busy',String(busy));
    }
    async function refresh(){const epoch=++read;busy=true;loaded=false;readReady=false;execution=null;jobs=null;channels=[];consent=null;review=null;pauseReview=null;render();try{
      const results=await Promise.allSettled([request(base+'/execution'),request(base+'/jobs'),request('/api/v1/tenants/'+encodeURIComponent(tenantId)+'/qr-channels')]);
      if(!current(epoch))return;
      if(results[0].status==='rejected')throw results[0].reason;
      const value=results[0].value;
      if(typeof value?.available!=='boolean'||value.available===true&&(!validVersion(value.version)||typeof value.enabled!=='boolean'))throw Error('A autorização atual não pôde ser conferida.');
      execution=value;jobs=results[1].status==='fulfilled'&&Array.isArray(results[1].value?.items)?results[1].value.items:null;
      const channelsReady=results[2].status==='fulfilled'&&Array.isArray(results[2].value?.items);channels=channelsReady?results[2].value.items:[];
      readReady=jobs!==null&&channelsReady;
      if(execution.available===true&&!readReady){feedback='Parte da situação atual está indisponível. Atualize antes de alterar os envios.';failed=true;}
    }catch(error){if(current(epoch)){feedback=error.message||'Não foi possível consultar os envios.';failed=true;}}finally{if(current(epoch)){loaded=true;busy=false;render();}}}
    async function loadConsent(phone,message=''){const epoch=read;consent=null;pauseReview=null;phoneDraft=phone;busy=true;feedback=message;failed=false;render();try{
      const data=await request(base+'/consent?contact_e164='+encodeURIComponent(phone));if(!current(epoch))return;
      if(data?.available!==true||typeof data.marketing_opt_in!=='boolean'||typeof data.operational_opt_in!=='boolean'||!(data.updated_at===null||typeof data.updated_at==='string'))throw Error('Não foi possível conferir os consentimentos atuais deste contato.');
      consent={...data,phone};
    }catch(error){if(current(epoch)){feedback=error.message||'Não foi possível consultar este contato.';failed=true;}}finally{if(current(epoch)){busy=false;render();}}}
    async function mutate(operation){if(!alive||busy)return;const epoch=read;let executionChanged=false;pending=operation;busy=true;feedback='Salvando sua revisão…';failed=false;render();try{
      const data=await request(operation.path,operation.method,operation.payload);if(!current(epoch))return;
      pending=null;executionChanged=operation.kind==='execution';feedback=operation.kind==='contact-pause'?'Solicitação registrada. Confira a pausa atual; mensagens antigas não são recriadas.':operation.kind==='reconcile'?'Consulta registrada. Confira o acompanhamento atualizado.':'Alteração registrada. Confira a situação atual.';
      if(operation.kind==='consent')consent=null;if(operation.kind==='approve'||operation.kind==='pause')review=null;
    }catch(error){if(!current(epoch))return;failed=true;feedback=error.message||'Não foi possível confirmar a alteração.';if(error.status&&error.status<500)pending=null;
    }finally{if(current(epoch)){const reloadContact=!pending&&!failed&&['consent','contact-pause'].includes(operation.kind);busy=false;await refresh();if(alive&&reloadContact&&writable())await loadConsent(operation.payload.contact_e164,feedback);if(alive&&executionChanged)try{await onExecutionChange?.();}catch{if(alive){feedback='Autorização registrada. Atualize as regras para conferir a situação atual.';failed=true;render();}}}}}
    async function submit(event){const form=event.target.closest('[data-oa-form]');if(!form||!host.contains(form))return;event.preventDefault();if(busy||pending||!canManage||!form.reportValidity())return;
      if(!writable())return;
      const value=Object.fromEntries(new FormData(form)),kind=form.dataset.oaForm;
      if(kind==='load-consent'){await loadConsent(value.phone);return;}
      let payload,path,method='POST';
      if(kind==='execution'){if(value.enabled&&(!value.acknowledged||!validVersion(execution.preferences_version)||!channels.some(c=>c.id===value.channel_id&&c.enabled===true))){feedback='Atualize a situação, escolha uma conexão autorizada e confirme a revisão antes de autorizar envios.';failed=true;render();return;}payload={expected_version:execution.version,idempotency_key:crypto.randomUUID(),enabled:!!value.enabled,channel_id:value.channel_id||null,daily_limit:Number(value.daily_limit),acknowledged:!!value.acknowledged,...(value.enabled?{expected_preferences_version:execution.preferences_version}:{})};path=base+'/execution';method='PUT';}
      else if(kind==='consent'&&consent){payload={contact_e164:consent.phone,marketing_opt_in:!!value.marketing_opt_in,operational_opt_in:!!value.operational_opt_in,expected_updated_at:consent.updated_at,evidence:value.evidence};path=base+'/consent';method='PUT';}
      else if(kind==='contact-pause'&&consent&&typeof pauseReview==='boolean'&&value.pause_acknowledged){payload={contact_e164:consent.phone,paused:pauseReview};path=base+'/contact-pause';method='PUT';}
      else if(kind==='approve'&&review?.status==='draft'&&execution.enabled===true&&review.eligible_count>0&&value.acknowledged){payload={expected_version:review.version,expected_snapshot:review.snapshot,idempotency_key:crypto.randomUUID(),acknowledged:true};path=base+'/'+encodeURIComponent(review.id)+'/approve';}
      if(path)await mutate({kind,path,method,payload});
    }
    async function click(event){const target=event.target.closest('[data-oa-action]');if(!target||!host.contains(target)||busy)return;const action=target.dataset.oaAction;
      if(action==='refresh'){await refresh();return;}if(action==='retry'&&pending){await mutate(pending);return;}
      if(pending||!canManage||!readReady)return;
      if(action==='contact-review'&&writable()&&consent&&typeof consent.human_paused==='boolean'){pauseReview=!consent.human_paused;render();}
      if(action==='pause'&&writable()&&review?.status==='approved')await mutate({kind:'pause',path:base+'/'+encodeURIComponent(review.id)+'/pause',method:'POST',payload:{}});
      if(action==='reconcile'&&jobs?.some(j=>j.id===target.dataset.oaJob&&['sent','uncertain'].includes(j.status)))await mutate({kind:'reconcile',path:base+'/jobs/'+encodeURIComponent(target.dataset.oaJob)+'/reconcile',method:'POST',payload:{}});
    }
    async function change(event){if(!event.target.matches('[data-oa-campaign]')||busy||pending||!writable())return;const identity=event.target.value,epoch=read;review=null;if(!list(campaigns).some(c=>c.id===identity)){render();return;}busy=true;feedback='';failed=false;render();
      try{const data=await request(base+'/'+encodeURIComponent(identity)+'/review');if(!current(epoch))return;if(data?.id!==identity||!validVersion(data.version)||!/^[a-f0-9]{64}$/i.test(data.snapshot||'')||typeof data.message!=='string'||!validVersion(data.eligible_count)||!['draft','approved','paused'].includes(data.status))throw Error('A revisão desta campanha está indisponível. Consulte novamente.');review=data;}catch(error){if(current(epoch)){feedback=error.message;failed=true;}}finally{if(current(epoch)){busy=false;render();}}
    }
    host.addEventListener('click',click);host.addEventListener('submit',submit);host.addEventListener('change',change);
    active={unmount(){alive=false;read++;pending=null;consent=null;review=null;phoneDraft='';host.removeEventListener('click',click);host.removeEventListener('submit',submit);host.removeEventListener('change',change);host.replaceChildren();},refresh};
    render();void refresh();return active;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionAutomations=Object.freeze({mount,unmount});
})();
