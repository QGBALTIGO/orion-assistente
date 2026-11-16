/* Subscription message history. Reads never send; retries retain their operation identity. */
(function () {
  'use strict';
  let active = null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const version = value => typeof value === 'string' && /^[0-9a-f]{64}$/i.test(value);
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  const seconds = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const instant = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  const text = value => value === null || typeof value === 'string';
  const states = {sent:'Enviado',failed:'Falhou',uncertain:'Resultado incerto',pending:'Pendente'};
  const statuses = [['','Todos'],['failed','Falharam'],['failed_resendable','Falharam e podem ser reenviadas']];
  const outcomes = new Set(['accepted','not_sent','uncertain','pending','running','failed']);
  const historyKey = 'orionSentRecovery';
  const paths = {refresh:'M20 11a8 8 0 0 0-15.5-2M4 4v5h5M4 13a8 8 0 0 0 15.5 2M20 20v-5h-5',search:'m21 21-4.3-4.3M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',close:'m6 6 12 12M6 18 18 6',left:'m15 5-7 7 7 7',right:'m9 5 7 7-7 7',send:'m22 2-7 20-4-9-9-4 20-7ZM11 13 22 2',clock:'M12 6v6l4 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',audio:'M12 3v12M8 7v4M16 7v4M4 9v2M20 9v2M8 21h8M12 15v6'};
  const icon = name => '<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="'+paths[name]+'"/></svg>';
  const button = (label, action, extra='', cls='os-outline') => '<button type="button" class="os-button '+cls+'" data-sm-action="'+action+'" '+extra+'>'+esc(label)+'</button>';
  const loading = label => '<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>'+esc(label)+'</div>';
  const number = value => new Intl.NumberFormat('pt-BR').format(value);
  function duration(value) {
    if (!seconds(value)) return 'Não informado';
    if (value > 0 && value < 1) return 'Menos de 1 s';
    const total = Math.floor(value), hours = Math.floor(total / 3600), minutes = Math.floor(total % 3600 / 60), remainder = total % 60;
    return ((hours ? hours+' h ' : '')+(minutes ? minutes+' min ' : '')+(remainder || !total ? remainder+' s' : '')).trim();
  }
  function date(value, timezone) {
    if (!instant(value)) return 'Não informado';
    try { return new Intl.DateTimeFormat('pt-BR',{timeZone:timezone,dateStyle:'short',timeStyle:'short'}).format(new Date(value)); }
    catch { return 'Não informado'; }
  }
  function relative(value, reference) {
    if (!instant(value) || !instant(reference)) return '';
    const diff = (Date.parse(value)-Date.parse(reference))/1000, absolute = Math.abs(diff);
    if (absolute < 60) return 'Agora';
    const unit = absolute < 3600 ? 'minute' : absolute < 86400 ? 'hour' : 'day';
    const divisor = {minute:60,hour:3600,day:86400}[unit];
    return new Intl.RelativeTimeFormat('pt-BR',{numeric:'auto'}).format(Math.trunc(diff/divisor),unit);
  }
  function timezoneValid(value) {
    if (typeof value !== 'string' || !value) return false;
    try { new Intl.DateTimeFormat('pt-BR',{timeZone:value}).format(0); return true; } catch { return false; }
  }
  function itemDTO(value) {
    if (!object(value) || !uuid(value.id) || !uuid(value.root_effect_id) || value.retry_of_effect_id !== null && !uuid(value.retry_of_effect_id) || !version(value.version) || !Object.hasOwn(states,value.status) || !outcomes.has(value.outcome) || !object(value.device) || !uuid(value.device.id) || typeof value.device.name !== 'string' || typeof value.device.removed !== 'boolean' || !object(value.recipient) || !text(value.recipient.display_name) || !text(value.recipient.phone_e164) || !object(value.origin) || typeof value.origin.label !== 'string' || typeof value.origin.is_resend !== 'boolean' || !object(value.received) || !text(value.received.text) || typeof value.received.truncated !== 'boolean' || !object(value.sent) || !text(value.sent.text) || !text(value.sent.caption) || typeof value.sent.truncated !== 'boolean' || value.sent.media !== null && !object(value.sent.media) || value.failure !== null && (!object(value.failure) || typeof value.failure.message !== 'string') || !Array.isArray(value.actions) || !object(value.resend) || typeof value.resend.eligible !== 'boolean' || !text(value.resend.blocked_reason) || !instant(value.created_at) || value.finished_at !== null && !instant(value.finished_at)) throw Error('invalid_message');
    return value;
  }
  function listDTO(value, expected) {
    if (!object(value) || !Array.isArray(value.items) || value.items.length > 30 || !integer(value.page) || value.page < 1 || value.per_page !== 30 || !integer(value.filtered_total) || !integer(value.total_pages) || !timezoneValid(value.timezone) || !instant(value.generated_at) || !instant(value.snapshot) || expected.snapshot && Date.parse(value.snapshot) !== Date.parse(expected.snapshot) || !object(value.capabilities) || typeof value.capabilities.resend !== 'boolean' || !Array.isArray(value.devices) || !object(value.counts) || value.counts.scope !== 'message_attempts' || !['total','today','last_30_days'].every(key=>integer(value.counts[key])) || !object(value.transcription)) throw Error('invalid_history');
    for (const item of value.items) itemDTO(item);
    if (new Set(value.items.map(item=>item.id)).size !== value.items.length) throw Error('duplicate_messages');
    for (const device of value.devices) if (!object(device) || !uuid(device.id) || typeof device.name !== 'string' || typeof device.removed !== 'boolean') throw Error('invalid_device');
    for (const key of ['total','month']) { const metric=value.transcription[key]; if (!object(metric) || !seconds(metric.known_seconds) || !integer(metric.unknown_completions) || typeof metric.complete !== 'boolean') throw Error('invalid_audio_history'); }
    const daily=value.transcription.daily;
    if (!object(daily) || !['limit_seconds','reserved_seconds','remaining_seconds'].every(key=>seconds(daily[key])) || !instant(daily.reset_at) || typeof daily.label !== 'string') throw Error('invalid_audio_balance');
    return value;
  }
  function detailDTO(value,id) {
    if (!object(value) || !timezoneValid(value.timezone) || !instant(value.generated_at) || itemDTO(value.item).id !== id) throw Error('invalid_detail');
    return value;
  }
  function operationDTO(value,pending) {
    if (!object(value) || !uuid(value.request_id) || value.original_effect_id !== pending.original_effect_id || value.root_effect_id !== pending.root_effect_id || !uuid(value.effect_id) || value.effect_id === pending.original_effect_id || typeof value.replayed !== 'boolean' || !Object.hasOwn(states,value.status) || !outcomes.has(value.outcome)) throw Error('invalid_operation');
    return value;
  }
  function recoveryRecord(value) {
    return object(value) && uuid(value.tenant_id) && version(value.account_scope) && uuid(value.idempotency_key) && uuid(value.original_effect_id) && uuid(value.root_effect_id) && version(value.expected_version);
  }
  function navigationState() {
    try { const value=history.state?.[historyKey]; return recoveryRecord(value) ? {[historyKey]:Object.fromEntries(['tenant_id','account_scope','idempotency_key','original_effect_id','root_effect_id','expected_version'].map(key=>[key,value[key]]))} : {}; } catch { return {}; }
  }
  function clearRecovery() {
    try { const value={...history.state}; delete value[historyKey]; history.replaceState(value,'',location.href); } catch {}
  }
  function clearContext() {
    clearRecovery();
    try { const url=new URL(location.href);if(url.searchParams.get('aba')!=='mensagens-enviadas')return;for(const key of ['page','status','device_id','contact','q','snapshot'])url.searchParams.delete(key);history.replaceState({...history.state},'',url.pathname+url.search+url.hash); } catch {}
  }
  function errorMessage(error) {
    const status=Number(error?.status);
    if (status===401) return 'Sua sessão expirou. Entre novamente no Orion.';
    if (status===403) return 'O acesso às mensagens desta empresa não está mais disponível.';
    if (status===404) return 'Este registro não está mais disponível.';
    if (status===409) return 'A situação da mensagem mudou. Consulte o registro atualizado antes de reenviar.';
    if (status===429) return 'Aguarde um momento antes de consultar novamente.';
    if ([400,422].includes(status) && typeof error?.message==='string' && error.message.length<=500) return error.message;
    return 'Não foi possível consultar as mensagens. Tente novamente.';
  }
  function mount(bridge) {
    unmount();
    const host=bridge?.host;
    if (!host || !uuid(bridge.tenantId) || typeof bridge.request!=='function') throw Error('Não foi possível confirmar a empresa desta conta.');
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/sent-messages';
    const defaults=()=>({page:1,status:'',device_id:'',contact:'',q:'',snapshot:''});
    const state={alive:true,read:0,filters:defaults(),data:null,loading:true,error:'',notice:'',dialog:null,pending:null,busy:false,revoked:false,accountScope:null};
    const fields=window.OrionFields, releaseFields=fields?.bind(host)||(()=>{});
    let searchTimer;
    try { const p=new URL(location.href).searchParams; for(const key of ['device_id','contact','q','snapshot'])state.filters[key]=p.get(key)||''; state.filters.page=/^\d{1,6}$/.test(p.get('page')||'')?Math.max(1,Number(p.get('page'))):1; state.filters.status=statuses.some(([value])=>value===p.get('status'))?p.get('status'):''; if(!uuid(state.filters.device_id))state.filters.device_id=''; if(!instant(state.filters.snapshot))state.filters.snapshot=''; state.filters.contact=state.filters.contact.slice(0,40);state.filters.q=state.filters.q.slice(0,255); } catch {}
    state.contactDraft=state.filters.contact;
    const current=()=>state.alive&&host.isConnected&&(typeof bridge.canAccess!=='function'||bridge.canAccess());
    const writable=()=>current()&&!state.revoked&&(typeof bridge.canWrite==='function'?bridge.canWrite():bridge.canWrite!==false);
    const eligible=item=>writable()&&state.data?.capabilities.resend===true&&item.status==='failed'&&['not_sent','failed'].includes(item.outcome)&&item.resend.eligible===true&&item.actions.some(action=>action?.type==='resend');
    const entryCurrent=(entry,read)=>!!entry&&current()&&state.dialog===entry&&entry.element.isConnected&&(read===undefined||entry.read===read);
    const request=(suffix,method='GET',payload)=>{if(!current()||state.revoked)throw Object.assign(Error('Acesso indisponível.'),{status:403});return bridge.request(base+suffix,method,payload);};
    function query(filters=state.filters) { const p=new URLSearchParams({page:String(filters.page)}); for(const key of ['status','device_id','contact','q','snapshot'])if(filters[key])p.set(key,filters[key]);return p.toString(); }
    function pageURL(page=state.filters.page) { const url=new URL(location.href);url.searchParams.set('aba','mensagens-enviadas');for(const key of Object.keys(defaults()))url.searchParams.delete(key);for(const [key,value] of new URLSearchParams(query({...state.filters,page})))url.searchParams.set(key,value);return url.pathname+url.search+url.hash; }
    function syncURL() { try { history.replaceState({...history.state},'',pageURL()); } catch {} }
    function persist() { if(!state.accountScope||!state.pending)return;try{history.replaceState({...history.state,[historyKey]:{tenant_id:bridge.tenantId,account_scope:state.accountScope,...state.pending}},'',location.href);}catch{} }
    function revoke() { state.revoked=true;state.data=null;state.pending=null;state.filters=defaults();state.contactDraft='';state.notice='';clearRecovery();closeDialog(true);syncURL(); }
    function metric(label,value,note,key,type='send') { return '<article class="os-card os-sent-metric" data-sm-card="'+key+'"><div><span>'+esc(label)+'</span>'+icon(type)+'</div><strong>'+esc(value)+'</strong><p>'+esc(note)+'</p></article>'; }
    function audioNote(value) { return value.unknown_completions?'Histórico incompleto: '+number(value.unknown_completions)+' transcrição(ões) concluída(s) sem duração registrada.':value.complete?'Duração das transcrições concluídas.':'Histórico de duração incompleto.'; }
    function audioValue(value) { const incomplete=value.complete!==true||value.unknown_completions>0;return incomplete?(value.known_seconds>0?'Pelo menos '+duration(value.known_seconds):'Não informado'):duration(value.known_seconds); }
    function metrics() {
      const d=state.data, counts=d?.counts, audio=d?.transcription;
      return '<section class="os-sent-metrics" aria-label="Resumo global de envios">'+metric('Total de mensagens',counts?number(counts.total):'—','Envios e tentativas registrados.','total')+metric('Hoje',counts?number(counts.today):'—','No fuso da empresa.','today','clock')+metric('Últimos 30 dias',counts?number(counts.last_30_days):'—','Envios e tentativas registrados.','last_30_days','clock')+'</section><section class="os-sent-metrics os-sent-audio-metrics" aria-label="Transcrição de áudio">'+metric('Áudio transcrito · total',audio?audioValue(audio.total):'—',audio?audioNote(audio.total):'Conferindo o histórico de duração.','asr_total','audio')+metric('Áudio transcrito · mês',audio?audioValue(audio.month):'—',audio?audioNote(audio.month):'Conferindo o histórico do mês.','asr_month','audio')+metric('Saldo diário de transcrição',audio?(audio.daily.limit_seconds===0?'Desativado':duration(audio.daily.remaining_seconds)):'—',audio?'Conta · UTC. Reservado: '+duration(audio.daily.reserved_seconds)+' de '+duration(audio.daily.limit_seconds)+'. Renova em '+date(audio.daily.reset_at,'UTC')+' UTC.':'Conferindo o limite diário da conta.','asr_daily','audio')+'</section>';
    }
    function statusHTML(item) { const note=item.status==='sent'?'Aceito pelo transporte; recebimento não confirmado.':item.status==='uncertain'?'Não foi possível confirmar o resultado do envio.':'';return '<span class="os-sent-status os-sent-status-'+item.status+'">'+esc(states[item.status])+'</span>'+(note?'<small class="os-sent-status-note">'+esc(note)+'</small>':''); }
    function contentHTML(content,sent=false) {
      const label=sent?content.type_label:({text:'Texto',audio:'Áudio',image:'Imagem',video:'Vídeo',document:'Documento',sticker:'Figurinha',location:'Localização',call:'Chamada'}[content.type]||'Mensagem');
      let html='<span class="os-sent-kind">'+esc(label||'Mensagem')+'</span><p class="os-sent-message">'+esc(content.text||(!sent?'Conteúdo não registrado.':content.media?.file_name||'Conteúdo sem texto.'))+'</p>';
      if(sent&&content.caption)html+='<p class="os-sent-message os-sent-caption"><span>Legenda</span>'+esc(content.caption)+'</p>';
      if(sent&&content.media)html+='<p class="os-help">Arquivo: '+esc(content.media.file_name||'Nome não registrado')+'</p>';
      if(content.truncated)html+='<small class="os-sent-truncated">Exibição parcial do conteúdo registrado.</small>';
      return html;
    }
    function timeHTML(value,timezone,reference) { return '<time datetime="'+esc(value)+'">'+esc(date(value,timezone))+'</time><small>'+esc(relative(value,reference))+'</small>'; }
    function rowsHTML() { const d=state.data;return '<div class="os-table-scroll os-sent-table-scroll" tabindex="0" role="region" aria-label="Mensagens enviadas; role para consultar todas as colunas"><table class="os-table os-sent-table"><thead><tr><th scope="col">Dispositivo</th><th scope="col">Destinatário</th><th scope="col">Recebido</th><th scope="col">Enviado</th><th scope="col">Origem</th><th scope="col">Quando</th><th scope="col">Ações</th></tr></thead><tbody>'+d.items.map(item=>'<tr data-sm-row="'+item.id+'"><td><strong>'+esc(item.device.name)+'</strong>'+(item.device.phone_e164?'<small>'+esc(item.device.phone_e164)+'</small>':'')+(item.device.removed?'<small>Dispositivo removido</small>':'')+'</td><td><button type="button" class="os-sent-detail-link" data-sm-action="detail" data-sm-id="'+item.id+'" aria-label="Ver mensagem para '+esc(item.recipient.display_name||item.recipient.phone_e164||'contato')+'">'+esc(item.recipient.display_name||item.recipient.phone_e164||'Contato sem nome')+'</button><small>'+esc(item.recipient.phone_e164||'Número não registrado')+'</small></td><td>'+contentHTML(item.received)+'</td><td>'+statusHTML(item)+contentHTML(item.sent,true)+(item.failure?'<p class="os-sent-failure">'+esc(item.failure.message)+'</p>':'')+'</td><td>'+esc(item.origin.label)+(item.origin.is_resend?'<small>Reenvio</small>':'')+'</td><td class="os-sent-time">'+timeHTML(item.finished_at||item.created_at,d.timezone,d.generated_at)+'</td><td class="os-sent-actions">'+(eligible(item)?button('Reenviar','resend','data-sm-id="'+item.id+'"'+(state.pending||state.busy?' disabled':''),'os-outline os-small'):'')+'</td></tr>').join('')+'</tbody></table></div>'; }
    function pagination() {
      const d=state.data;if(!d||d.total_pages<=1)return '';
      const pages=new Set([1,d.total_pages]);for(let n=d.page-2;n<=d.page+2;n++)if(n>0&&n<=d.total_pages)pages.add(n);
      const link=(label,action,page,disabled=false)=>'<a class="os-button os-outline os-small" data-sm-action="'+action+'" data-sm-page="'+page+'" href="'+esc(pageURL(page))+'"'+(disabled?' aria-disabled="true" tabindex="-1"':'')+'>'+label+'</a>';
      let previous=0,numbers='';for(const page of [...pages].sort((a,b)=>a-b)){if(previous&&page>previous+1)numbers+='<span aria-hidden="true">…</span>';numbers+='<a class="os-button '+(page===d.page?'':'os-outline ')+'os-small" data-sm-action="page" data-sm-page="'+page+'" href="'+esc(pageURL(page))+'" aria-label="Página '+page+'"'+(page===d.page?' aria-current="page"':'')+'>'+page+'</a>';previous=page;}
      return '<nav class="os-pagination os-sent-pagination" data-sm-pagination aria-label="Páginas de mensagens"><span>'+number((d.page-1)*30+1)+'–'+number((d.page-1)*30+d.items.length)+' de '+number(d.filtered_total)+'</span><div>'+link('Anterior','prev',Math.max(1,d.page-1),d.page<=1)+numbers+link('Próxima','next',Math.min(d.total_pages,d.page+1),d.page>=d.total_pages)+'</div></nav>';
    }
    function render() {
      if(!state.alive)return;
      const focused=document.activeElement?.dataset?.smField,position=document.activeElement?.selectionStart;
      const f=state.filters,d=state.data,hasFilters=!!(f.status||f.device_id||f.contact||f.q),hasDraft=hasFilters||!!state.contactDraft;
      const deviceOptions=d?.devices||[],missing=f.device_id&&!deviceOptions.some(device=>device.id===f.device_id);
      host.className='os-sent-messages';host.setAttribute('aria-busy',String(state.loading||state.busy));
      host.innerHTML='<header class="os-title-row"><div><h1>Mensagens Enviadas</h1><p>Acompanhe os envios e as tentativas de envio da sua empresa.</p></div><div class="os-actions">'+button('Atualizar','refresh',state.busy?'disabled':'')+'</div></header><div class="os-notice" data-sm-feedback role="status"'+(!state.notice?' hidden':'')+'>'+esc(state.notice)+'</div>'+(state.pending?'<div class="os-notice os-sent-recovery" role="status"><p>Há um reenvio aguardando confirmação. Consulte o resultado antes de iniciar outro.</p>'+button('Consultar reenvio','reconcile',state.busy?'disabled':'')+'</div>':'')+metrics()+'<p class="os-help os-sent-scope">Os cartões mostram o histórico global, independentemente dos filtros.'+(d?' Atualizado em '+esc(date(d.generated_at,d.timezone))+' · '+esc(d.timezone)+'.':'')+'</p><section class="os-card os-sent-history"><form class="os-sent-filters" data-sm-form="filters" role="search" aria-label="Filtrar mensagens"><div class="os-field"><label for="sm-device">Dispositivo</label><select id="sm-device" data-sm-field="device_id"><option value="">Todos os dispositivos</option>'+(missing?'<option value="'+esc(f.device_id)+'" selected>Dispositivo selecionado</option>':'')+deviceOptions.map(device=>'<option value="'+device.id+'"'+(f.device_id===device.id?' selected':'')+'>'+esc(device.name)+(device.removed?' (removido)':'')+'</option>').join('')+'</select></div><div class="os-field"><label for="sm-status">Situação</label><select id="sm-status" data-sm-field="status">'+statuses.map(([value,label])=>'<option value="'+value+'"'+(f.status===value?' selected':'')+'>'+label+'</option>').join('')+'</select></div><div class="os-field"><label for="sm-contact">Contato</label><input id="sm-contact" type="tel" maxlength="40" data-sm-field="contact" value="'+esc(state.contactDraft)+'" placeholder="Número do contato" autocomplete="off"></div><div class="os-field"><label for="sm-query">Pesquisar</label><input id="sm-query" type="search" maxlength="255" data-sm-field="q" value="'+esc(f.q)+'" placeholder="Pesquisar conteúdo…" autocomplete="off"></div><div class="os-sent-filter-actions"><button type="submit" class="os-button os-outline" data-sm-action="filter">Filtrar</button>'+button('Limpar filtros','reset',!hasDraft?'disabled':'','os-ghost')+'</div></form><div data-sm-results aria-busy="'+state.loading+'" tabindex="-1">'+(state.loading?loading('Carregando mensagens…'):state.error?'<div class="os-empty"><p class="os-notice os-error" role="alert">'+esc(state.error)+'</p>'+(!state.revoked?button('Tentar novamente','retry-read'):'')+'</div>':d?.items.length?rowsHTML()+pagination():'<div class="os-empty" data-sm-empty><div class="os-square-icon">'+icon('send')+'</div><h2>'+esc(hasFilters?'Nenhuma mensagem corresponde aos filtros':'Nenhuma mensagem registrada')+'</h2><p>'+esc(hasFilters?'Ajuste a pesquisa ou limpe os filtros para consultar o histórico.':'Os envios e as tentativas aparecerão aqui quando forem registrados.')+'</p></div>')+'</div></section>';
      fields?.refresh(host);
      if(focused){const node=host.querySelector('[data-sm-field="'+focused+'"]');node?.focus({preventScroll:true});try{node?.setSelectionRange(position,position);}catch{}}
    }
    async function load({fresh=false,focus=false}={}) {
      if(!current()||state.revoked)return;
      clearTimeout(searchTimer);if(fresh){state.filters.page=1;state.filters.snapshot='';}
      const read=++state.read,expected={...state.filters};state.loading=true;state.error='';syncURL();render();
      try { const data=listDTO(await request('?'+query(expected)),expected);if(!current()||read!==state.read)return;state.data=data;state.filters.page=data.page;state.filters.snapshot=data.snapshot;syncURL(); }
      catch(error){if(!current()||read!==state.read)return;if([401,403].includes(Number(error?.status)))revoke();state.error=errorMessage(error);}
      finally {if(state.alive&&read===state.read){state.loading=false;render();if(focus)host.querySelector('[data-sm-results]')?.focus({preventScroll:true});}}
    }
    function closeDialog(force=false) {
      const entry=state.dialog;if(!entry||state.busy&&!force)return;
      entry.read++;entry.element.removeEventListener('click',click);entry.element.removeEventListener('cancel',cancel);if(entry.element.open)entry.element.close();entry.element.remove();state.dialog=null;
      if(!force){const target=entry.trigger?.isConnected&&entry.trigger!==document.body?entry.trigger:host.querySelector('[data-sm-action="detail"][data-sm-id="'+entry.id+'"]')||host.querySelector('[data-sm-results]');target?.focus({preventScroll:true});}
    }
    function dialog(id,trigger=document.activeElement) {
      closeDialog();const element=document.createElement('dialog'),title='sm-dialog-'+crypto.randomUUID();element.className='os-dialog os-sheet os-sent-dialog';element.setAttribute('data-sm-dialog','');element.setAttribute('aria-labelledby',title);
      const entry={element,title,id,read:0,trigger,detail:null,mode:'loading',message:'',retry:false};state.dialog=entry;
      element.addEventListener('click',click);element.addEventListener('cancel',cancel);document.body.append(element);renderDialog(entry);element.showModal();element.querySelector('h2').focus({preventScroll:true});return entry;
    }
    function detailBody(value) {
      const item=value.item;
      return '<div class="os-sent-detail-state">'+statusHTML(item)+'</div><dl class="os-sent-details"><div><dt>Destinatário</dt><dd>'+esc(item.recipient.display_name||'Contato sem nome')+'<small>'+esc(item.recipient.phone_e164||'Número não registrado')+'</small></dd></div><div><dt>Dispositivo</dt><dd>'+esc(item.device.name)+(item.device.removed?' · removido':'')+(item.device.phone_e164?'<small>'+esc(item.device.phone_e164)+'</small>':'')+'</dd></div><div><dt>Origem</dt><dd>'+esc(item.origin.label)+(item.origin.is_resend?' · Reenvio':'')+'</dd></div><div><dt>Registrado em</dt><dd>'+esc(date(item.created_at,value.timezone))+'</dd></div><div><dt>Conclusão da tentativa</dt><dd>'+esc(date(item.finished_at,value.timezone))+'</dd></div></dl><p class="os-help">Horários em '+esc(value.timezone)+'.</p><section class="os-sent-content"><h3>Recebido</h3>'+contentHTML(item.received)+'</section><section class="os-sent-content"><h3>Enviado</h3>'+contentHTML(item.sent,true)+'</section>'+(item.failure?'<section class="os-sent-detail-failure"><h3>Motivo registrado</h3><p>'+esc(item.failure.message)+'</p></section>':'')+(!eligible(item)&&item.resend.blocked_reason?'<p class="os-help">'+esc(item.resend.blocked_reason)+'</p>':'');
    }
    function renderDialog(entry) {
      if(!entryCurrent(entry))return;
      const focused=entry.element.contains(document.activeElement),focusedAction=document.activeElement?.dataset?.smAction;
      const title=entry.mode==='confirm'?'Reenviar mensagem?':entry.mode==='pending'?'Conferir reenvio':entry.mode==='result'?'Reenvio registrado':'Detalhes da mensagem';
      entry.element.dataset.smView=entry.mode;entry.element.setAttribute('aria-busy',String(state.busy||entry.mode==='loading'));
      let body=entry.mode==='loading'?loading('Consultando o registro atualizado…'):entry.detail?detailBody(entry.detail):'';
      if(entry.mode==='confirm')body='<p class="os-sent-confirm-notice">Confira o destinatário, o dispositivo e o conteúdo abaixo. Ao confirmar, uma nova tentativa de envio será registrada.</p>'+body;
      if(entry.message)body+='<div class="os-notice'+(entry.mode==='error'?' os-error':'')+'" data-sm-dialog-feedback role="'+(entry.mode==='error'?'alert':'status')+'">'+esc(entry.message)+'</div>';
      let actions=button(entry.mode==='confirm'?'Cancelar':'Fechar','close-dialog',state.busy?'disabled':'');
      if(entry.mode==='detail'&&entry.detail&&eligible(entry.detail.item)&&!state.pending)actions+=button('Reenviar','resend','data-sm-id="'+entry.id+'"','');
      if(entry.mode==='confirm'&&entry.detail&&eligible(entry.detail.item)&&!state.pending)actions+=button('Confirmar reenvio','confirm-resend',state.busy?'disabled':'','');
      if(entry.mode==='pending'&&state.pending){actions+=button('Consultar resultado','reconcile',state.busy?'disabled':'','');if(entry.retry)actions+=button('Tentar novamente o mesmo reenvio','retry-resend',state.busy?'disabled':'');}
      if(entry.mode==='error'&&entry.id&&!state.revoked)actions+=button('Consultar novamente','retry-detail','data-sm-id="'+entry.id+'"');
      entry.element.innerHTML='<header class="os-sent-dialog-header"><h2 id="'+entry.title+'" tabindex="-1">'+title+'</h2><button type="button" class="os-icon-button os-close" data-sm-action="close-dialog" aria-label="Fechar"'+(state.busy?' disabled':'')+'>'+icon('close')+'</button></header><div class="os-sent-dialog-body">'+body+'</div><footer class="os-sent-dialog-footer">'+actions+'</footer>';
      if(focused)(focusedAction&&entry.element.querySelector('[data-sm-action="'+focusedAction+'"]:not([disabled])')||entry.element.querySelector('h2'))?.focus({preventScroll:true});
    }
    async function openDetail(id,confirm=false,trigger=document.activeElement) {
      if(!current()||state.busy||!uuid(id))return;
      if(confirm&&state.pending){await reconcile(true);return;}
      const entry=state.dialog?.id===id?state.dialog:dialog(id,trigger),read=++entry.read;entry.mode='loading';entry.detail=null;entry.message='';renderDialog(entry);
      try {const value=detailDTO(await request('/'+id),id);if(!entryCurrent(entry,read))return;entry.detail=value;entry.mode=confirm&&eligible(value.item)?'confirm':'detail';if(confirm&&entry.mode!=='confirm')entry.message=value.item.resend.blocked_reason||'Este registro não permite um reenvio neste momento.';}
      catch(error){if(!entryCurrent(entry,read))return;if([401,403].includes(Number(error?.status))){revoke();state.error=errorMessage(error);render();return;}entry.mode='error';entry.message=errorMessage(error);}
      finally {if(entryCurrent(entry,read)){renderDialog(entry);entry.element.querySelector('h2')?.focus({preventScroll:true});}}
    }
    function operationMessage(operation) {
      if(operation.outcome==='accepted'||operation.status==='sent')return 'O transporte aceitou a tentativa. O recebimento pelo contato não está confirmado.';
      if(operation.status==='uncertain'||operation.outcome==='uncertain')return 'A tentativa foi registrada, mas o resultado do envio permanece incerto. Confira o histórico antes de agir novamente.';
      if(operation.status==='failed')return 'A tentativa foi registrada e apresentou falha. Consulte o histórico para verificar o motivo e a possibilidade de outro reenvio.';
      return 'Reenvio registrado. A tentativa está pendente de processamento; o recebimento ainda não foi confirmado.';
    }
    async function accepted(value,pending,entry) {
      const result=operationDTO(value,pending);state.pending=null;clearRecovery();state.notice=operationMessage(result);state.busy=false;
      if(entryCurrent(entry)){entry.mode='result';entry.message=state.notice;entry.detail=null;entry.retry=false;renderDialog(entry);entry.element.querySelector('h2')?.focus({preventScroll:true});}
      await load({fresh:true});
    }
    async function send(pending,entry) {
      if(!writable()||state.busy||!pending)return;
      state.pending=pending;persist();state.busy=true;entry.mode='pending';entry.message='Registrando a solicitação de reenvio…';entry.retry=false;renderDialog(entry);render();
      try {const value=await request('/'+pending.original_effect_id+'/resend','POST',{idempotency_key:pending.idempotency_key,expected_version:pending.expected_version});if(!current()||state.pending!==pending)return;await accepted(value,pending,entry);}
      catch(error){if(!current()||state.pending!==pending)return;state.busy=false;
        if([401,403].includes(Number(error?.status))){revoke();state.error=errorMessage(error);render();return;}
        if(Number(error?.status)>=400&&Number(error?.status)<500){state.pending=null;clearRecovery();if(entryCurrent(entry)){entry.mode='error';entry.detail=null;entry.message=errorMessage(error);renderDialog(entry);}}
        else if(entryCurrent(entry)){entry.mode='pending';entry.message='Não foi possível confirmar a solicitação. Consulte o resultado; nenhum novo envio será iniciado automaticamente.';renderDialog(entry);}
        render();
      }finally{if(current()){state.busy=false;render();if(entryCurrent(entry))renderDialog(entry);}}
    }
    async function reconcile(open=true) {
      const pending=state.pending;if(!current()||!pending||state.busy)return;
      const entry=open?(state.dialog||dialog(pending.original_effect_id)):state.dialog;state.busy=true;
      if(entry){entry.mode='pending';entry.message='Consultando a solicitação registrada…';entry.retry=false;renderDialog(entry);}render();
      try {const value=await request('/resend-requests/'+pending.idempotency_key);if(!current()||state.pending!==pending)return;await accepted(value,pending,entry);}
      catch(error){if(!current()||state.pending!==pending)return;state.busy=false;if([401,403].includes(Number(error?.status))){revoke();state.error=errorMessage(error);render();return;}
        const missing=Number(error?.status)===404;state.notice=missing?'A solicitação ainda não foi encontrada. Você pode repetir explicitamente o mesmo reenvio.':'Não foi possível conferir o reenvio. Consulte novamente antes de iniciar outro.';
        if(entryCurrent(entry)){entry.mode='pending';entry.message=state.notice;entry.retry=missing;renderDialog(entry);}render();
      }finally{if(current()){state.busy=false;render();if(entryCurrent(entry))renderDialog(entry);}}
    }
    function filtersChanged({contact=false}={}) {
      clearTimeout(searchTimer);state.read++;state.loading=true;state.error='';state.filters.page=1;state.filters.snapshot='';
      for(const key of ['status','device_id','q'])state.filters[key]=host.querySelector('[data-sm-field="'+key+'"]')?.value.trim()||'';
      if(contact){state.filters.contact=(host.querySelector('[data-sm-field="contact"]')?.value||'').trim();state.contactDraft=state.filters.contact;}
      if(state.dialog&&!state.busy)closeDialog();syncURL();
    }
    function input(event) { if(event.target.matches('[data-sm-field=contact]')){state.contactDraft=event.target.value;const reset=host.querySelector('[data-sm-action=reset]');if(reset)reset.disabled=!(state.contactDraft||state.filters.status||state.filters.device_id||state.filters.contact||state.filters.q);return;}if(!event.target.matches('[data-sm-field=q]'))return;filtersChanged();searchTimer=setTimeout(()=>void load(),300); }
    function change(event) {if(!event.target.matches('select[data-sm-field]'))return;filtersChanged();void load();}
    function submit(event) {if(!event.target.matches('[data-sm-form=filters]'))return;event.preventDefault();filtersChanged({contact:true});void load();}
    function cancel(event) {event.preventDefault();closeDialog();}
    function click(event) {
      const node=event.target.closest('[data-sm-action]');if(!node||node.disabled||!current())return;
      if(node.tagName==='A'&&(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey))return;
      const action=node.dataset.smAction;if(action==='filter')return;event.preventDefault();if(node.getAttribute('aria-disabled')==='true')return;
      if(action==='close-dialog'){closeDialog();return;}if(state.busy)return;
      if(['page','next','prev'].includes(action)){const page=Number(node.dataset.smPage);if(!integer(page)||page<1||page===state.filters.page||state.loading)return;state.filters.page=page;void load({focus:true});return;}
      if(action==='reset'){clearTimeout(searchTimer);state.filters=defaults();state.contactDraft='';closeDialog();void load();return;}
      if(action==='refresh'){void load({fresh:true});return;}if(action==='retry-read'){void load();return;}
      if(action==='detail'||action==='retry-detail'){void openDetail(node.dataset.smId,false,node);return;}
      if(action==='resend'){if(!state.loading&&state.data?.capabilities.resend&&writable())void openDetail(node.dataset.smId,true,node);return;}
      if(action==='confirm-resend'){const entry=state.dialog,item=entry?.detail?.item;if(!entry||entry.mode!=='confirm'||!item||!eligible(item)||state.pending)return;void send({idempotency_key:crypto.randomUUID(),original_effect_id:item.id,root_effect_id:item.root_effect_id,expected_version:item.version},entry);return;}
      if(action==='reconcile'){void reconcile(true);return;}
      if(action==='retry-resend'&&state.dialog?.retry&&state.pending)void send(state.pending,state.dialog);
    }
    const controller={load,unmount(){if(!state.alive)return;state.alive=false;state.read++;clearTimeout(searchTimer);closeDialog(true);releaseFields();for(const [name,fn] of [['click',click],['input',input],['change',change],['submit',submit]])host.removeEventListener(name,fn);state.data=null;state.pending=null;host.replaceChildren();host.removeAttribute('aria-busy');}};
    active=controller;
    for(const [name,fn] of [['click',click],['input',input],['change',change],['submit',submit]])host.addEventListener(name,fn);
    render();controller.ready=(async()=>{
      try {if(bridge.accountKey&&crypto.subtle){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(bridge.accountKey));if(!current())return;state.accountScope=Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');}}catch{}
      if(!current())return;const saved=navigationState()[historyKey];if(saved&&saved.tenant_id===bridge.tenantId&&saved.account_scope===state.accountScope){const {tenant_id,account_scope,...pending}=saved;state.pending=pending;}else if(saved)clearRecovery();
      await load();if(current()&&state.pending&&!state.revoked)await reconcile(false);
    })();return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionSubscriptionSentMessages=Object.freeze({mount,unmount,navigationState,clearRecovery,clearContext});
})();
