/* Subscription device workspace. All requests remain on the authenticated,
 * tenant-scoped API. Rendering never starts a session or sends a message.
 * QR codes, pairing codes and proxy passwords remain in memory only.
 */
(function () {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const copy=value=>JSON.parse(JSON.stringify(value));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const weekdays=['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo'];
  const statuses={disabled:'Não conectado',stopped:'Desconectado',starting:'Preparando conexão',connecting:'Preparando conexão',reconnecting:'Reconectando',qr_ready:'Pronto para conectar',qr_delayed:'Aguardando QR Code',qr_expired:'QR Code expirou',pairing:'Aguardando confirmação no WhatsApp',pair_expired:'Código expirou',pair_failed:'Vínculo não confirmado',connected:'Conectado',disconnected:'Desconectado',logged_out:'Desvinculado',error:'Precisa de atenção',unavailable:'Estado indisponível',removing:'Exclusão em andamento'};
  const paths={phone:'M6 2h12v20H6zM10 5h4m-3 14h2',plus:'M12 5v14M5 12h14',more:'M4 7h16M4 12h16M4 17h16',settings:'M4 7h16M4 17h16M8 4v6m8 4v6',refresh:'M20 11a8 8 0 1 0-2.4 6M20 4v7h-7',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',chevron:'m6 9 6 6 6-6',left:'m15 18-6-6 6-6',close:'m6 6 12 12M6 18 18 6',check:'m5 12 4 4L19 6',mic:'M9 2h6v13H9zM5 10a7 7 0 0 0 14 0M12 17v5m-4 0h8',shield:'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Zm-3-10 2 2 4-4',link:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2',copy:'M9 9h12v12H9zM5 15H3V3h12v2',info:'M12 8v.01M12 11v6'};
  const icon=name=>'<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+(name==='info'?'<circle cx="12" cy="12" r="10"/>':'')+'<path d="'+(paths[name]||paths.phone)+'"/></svg>';
  const button=(label,action,style='os-outline',symbol='',extra='')=>'<button type="button" class="os-button '+style+'" data-sd-action="'+action+'" '+extra+'>'+(symbol?icon(symbol):'')+'<span>'+esc(label)+'</span></button>';
  const loading=label=>'<div class="os-loading"><span class="os-spinner" aria-hidden="true"></span>'+esc(label)+'</div>';
  const fieldId=path=>'sd-'+path.replace(/[^a-z0-9]+/gi,'-');
  const input=(label,path,value,type='text',extra='',help='')=>'<div class="os-field"><label for="'+fieldId(path)+'">'+esc(label)+'</label><input id="'+fieldId(path)+'" data-sd-field="'+path+'" type="'+type+'" value="'+esc(value)+'" '+extra+(help?' aria-describedby="'+fieldId(path)+'-help"':'')+'>'+(help?'<small class="os-help" id="'+fieldId(path)+'-help">'+esc(help)+'</small>':'')+'</div>';
  const textarea=(label,path,value,limit,help='')=>'<div class="os-field"><label for="'+fieldId(path)+'">'+esc(label)+'</label><textarea id="'+fieldId(path)+'" data-sd-field="'+path+'" maxlength="'+limit+'"'+(help?' aria-describedby="'+fieldId(path)+'-help"':'')+'>'+esc(value)+'</textarea>'+(help?'<small class="os-help" id="'+fieldId(path)+'-help">'+esc(help)+'</small>':'')+'</div>';
  const checkbox=(label,path,value,help='',disabled=false)=>'<div><label class="os-check"><input type="checkbox" data-sd-field="'+path+'"'+(value?' checked':'')+(disabled?' disabled':'')+'><span>'+esc(label)+'</span></label>'+(help?'<small class="os-help">'+esc(help)+'</small>':'')+'</div>';
  const section=(key,title,description,html,opened=false)=>'<details class="os-card os-device-setting-section" data-sd-section="'+key+'"'+(opened?' open':'')+'><summary><span><strong>'+esc(title)+'</strong><small>'+esc(description)+'</small></span>'+icon('chevron')+'</summary><div class="os-device-setting-body">'+html+'</div></details>';
  const get=(value,path)=>path.split('.').reduce((v,key)=>v?.[key],value);
  const stable=value=>JSON.stringify(value,(_key,current)=>object(current)?Object.fromEntries(Object.entries(current).sort(([a],[b])=>a.localeCompare(b))):current);
  function set(value,path,next){const parts=path.split('.'),last=parts.pop();let target=value;for(const key of parts)target=target[key];target[last]=next;}
  const errorMessage=error=>({401:'Sua sessão expirou. Entre novamente no Orion.',403:'Seu acesso não permite gerenciar este dispositivo.',404:'O dispositivo não está mais disponível. Atualize a lista.',409:'O dispositivo mudou ou a ação está indisponível. Confira o estado antes de continuar.',422:'Confira os campos informados antes de salvar.',423:'A conexão está temporariamente indisponível.',429:'Aguarde um pouco antes de tentar novamente.',503:'Não foi possível consultar a conexão. Confira o estado antes de repetir uma ação.'}[Number(error?.status)]||'Não foi possível confirmar a resposta do servidor. Seus dados foram preservados.');
  const uncertain=error=>!Number(error?.status)||Number(error.status)>=500;
  const count=value=>Number.isSafeInteger(value)&&value>=0?new Intl.NumberFormat('pt-BR').format(value):'—';
  const dateTime=(value,timezone)=>{if(!value)return '';try{const date=new Date(value);if(!Number.isFinite(date.getTime()))return '';return new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeStyle:'short',...(timezone?{timeZone:timezone}:{})}).format(date);}catch{return '';}};
  function safeImage(value){try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password?url.href:null;}catch{return null;}}
  function deviceDTO(value){
    if(!object(value)||!uuid(value.id)||typeof value.name!=='string'||!Number.isSafeInteger(value.revision)||value.revision<1||!Object.hasOwn(statuses,value.status)||!object(value.capabilities))throw Error('invalid_device_response');
    const operation=value.pending_operation;
    if(operation!==undefined&&operation!==null&&(!object(operation)||!uuid(operation.id)||operation.device_id!==value.id||!['connect','restart','relink','remove','pair'].includes(operation.action)||!['pending','running','uncertain'].includes(operation.status)))throw Error('invalid_pending_operation');
    return {...copy(value),phone:value.status==='connected'&&value.phone_verified===true&&typeof value.phone==='string'&&/^\+?[1-9]\d{7,14}$/.test(value.phone)?value.phone.replace(/^\+/,''):null};
  }
  function settingsDTO(value){
    if(!object(value)||!Number.isSafeInteger(value.revision)||value.revision<1||!object(value.settings)||!object(value.capabilities))throw Error('invalid_settings_response');
    const settings=value.settings;
    if(typeof settings.name!=='string'||typeof settings.timezone!=='string'||['webhook','proxy','transcription','calls','business_hours'].some(key=>!object(settings[key]))||Object.hasOwn(settings.proxy,'password'))throw Error('invalid_settings_response');
    for(const group of ['calls','business_hours']){
      const days=settings[group].days;
      if(!Array.isArray(days)||days.length!==7||new Set(days.map(day=>day.weekday)).size!==7||days.some(day=>!Number.isInteger(day.weekday)||day.weekday<0||day.weekday>6||typeof day.enabled!=='boolean'))throw Error('invalid_settings_schedule');
    }
    return copy(value);
  }
  let active=null;

  function mount(bridge){
    unmount();
    const host=typeof bridge?.host==='string'?document.querySelector(bridge.host):bridge?.host;
    if(!host||typeof bridge?.request!=='function'||!uuid(bridge.tenantId))throw Error('Não foi possível confirmar a empresa deste dispositivo.');
    const fields=window.OrionFields,connectView=window.OrionConnectView;
    if(!fields||!connectView)throw Error('Atualize o painel para abrir Dispositivos.');
    const state={mounted:true,epoch:1,read:0,view:'list',id:null,items:[],usage:null,capabilities:{},loaded:false,loading:false,busy:false,feedback:'',error:false,dialog:null,pending:null,operation:null,settings:null,settingsRevision:null,settingsCapabilities:{},settingsStale:false,formFingerprint:null,method:'code',tutorial:{code:false,qr:false},phoneDraft:'',qr:null,pair:null,pairHidden:false};
    const releaseFields=fields.bind(host);
    let pollTimer=null,expiryTimer=null,clockTimer=null,lastHTML='';
    host.classList.add('os-devices');
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/devices';
    const path=(id,suffix='')=>base+'/'+encodeURIComponent(id)+suffix;
    const request=(url,method='GET',payload)=>bridge.request(url,method,payload);
    const current=(epoch,read)=>state.mounted&&state.epoch===epoch&&(read===undefined||state.read===read);
    const device=()=>state.items.find(item=>item.id===state.id)||null;
    const visible=()=>{if(!state.mounted||document.hidden)return false;for(let node=host;node;node=node.parentElement)if(node.hidden||node.classList.contains('hidden')||node.getAttribute('aria-hidden')==='true'||getComputedStyle(node).display==='none')return false;return true;};
    const canManage=()=>state.loaded&&state.capabilities.manage===true;
    const canWrite=()=>canManage()&&!state.busy&&!state.pending&&!state.stale;
    const canDevice=(action,item=device())=>canWrite()&&item?.capabilities?.[action]===true;
    const feedbackHTML=()=>'<div class="os-notice os-devices-feedback'+(state.error?' os-error':'')+'" data-sd-feedback role="'+(state.error?'alert':'status')+'"'+(!state.feedback?' hidden':'')+'>'+esc(state.feedback)+'</div>';
    function feedback(message,error=false){state.feedback=message;state.error=error;const node=host.querySelector('[data-sd-feedback]');if(node){node.hidden=!message;node.className='os-notice os-devices-feedback'+(error?' os-error':'');node.setAttribute('role',error?'alert':'status');node.textContent=message;if(error){node.tabIndex=-1;node.focus({preventScroll:true});}}}
    function clearCredentials(){state.qr=null;state.pair=null;state.pairHidden=false;clearTimeout(expiryTimer);expiryTimer=null;clearTimeout(clockTimer);clockTimer=null;host.querySelector('[data-sd-qr],[data-sd-pair]')?.remove();}
    function pause(){clearTimeout(pollTimer);pollTimer=null;state.read++;state.loading=false;clearCredentials();}
    function adoptOperation(item){
      if(!item?.pending_operation)return;
      state.operation=copy(item.pending_operation);
      state.pending={kind:state.operation.action==='pair'?'pair':'action',id:item.id,action:state.operation.action};
    }
    function updateDevice(value){
      const next=deviceDTO(value),index=state.items.findIndex(item=>item.id===next.id),old=index>=0?state.items[index]:null;
      if(next.id===state.id&&old&&(old.revision!==next.revision||next.status==='connected'||next.status==='logged_out'||next.status==='pair_failed'))clearCredentials();
      if(index<0)state.items.push(next);else state.items[index]=next;
      if(next.id===state.id&&state.qr&&next.status!=='qr_ready')clearCredentials();
      if(next.id===state.id&&state.pair&&next.status!=='pairing')clearCredentials();
      adoptOperation(next);
      return next;
    }
    function clock(){
      clearTimeout(clockTimer);clockTimer=null;
      const value=state.pair||state.qr,node=host.querySelector('[data-sd-expiry]');
      if(value&&node)node.textContent=connectView.seconds(value.expiresAt-Date.now());
      if(value&&visible())clockTimer=setTimeout(clock,1000);
    }
    function paint(html){
      if(lastHTML===html){clock();return;}
      const focused=document.activeElement,inside=host.contains(focused);
      const focus=inside?{id:focused.id,action:focused.dataset.sdAction,start:focused.selectionStart,end:focused.selectionEnd,direction:focused.selectionDirection}:null;
      const qr=state.qr?host.querySelector('[data-sd-qr]'):null,pair=state.pair?host.querySelector('[data-sd-pair]'):null,phone=state.view==='connect'?host.querySelector('#sd-pair-phone'):null;
      const details=[...host.querySelectorAll('details[open][data-sd-device]')].map(node=>node.dataset.sdDevice);
      host.innerHTML=html;lastHTML=html;fields.refresh(host);
      if(qr&&qr.querySelector('img')?.getAttribute('src')===state.qr?.image)host.querySelector('[data-sd-qr]')?.replaceWith(qr);
      if(pair&&pair.querySelector('[data-sd-code]')?.textContent===state.pair?.code&&!state.pairHidden)host.querySelector('[data-sd-pair]')?.replaceWith(pair);
      const nextPhone=host.querySelector('#sd-pair-phone');if(phone&&nextPhone){phone.disabled=nextPhone.disabled;nextPhone.replaceWith(phone);}
      for(const id of details){const node=host.querySelector('details[data-sd-device="'+id+'"]');if(node)node.open=true;}
      if(focus){const target=focus.id?host.querySelector('#'+focus.id):focus.action?host.querySelector('[data-sd-action="'+focus.action+'"]'):null;if(target&&!target.disabled){target.focus({preventScroll:true});if(focus.start!==null&&focus.start!==undefined)try{target.setSelectionRange(focus.start,focus.end,focus.direction);}catch{}}}
      clock();
    }
    function pendingHTML(){
      if(!state.pending)return '';
      const retry=['create','action'].includes(state.pending.kind)&&!state.operation;
      return '<section class="os-notice os-devices-feedback"><div><strong>Resultado ainda não confirmado</strong><p>Confira o estado antes de repetir a ação. Nenhum novo comando será enviado automaticamente.</p><div class="os-actions">'+button('Conferir estado','reconcile')+(retry?button('Retomar a mesma solicitação','retry-pending','os-ghost'):'')+'</div></div></section>';
    }
    function card(item){
      const avatar=safeImage(item.profile?.avatar_url),verifiedPhone=item.phone?fields.phoneDisplay('+'+item.phone,{allowInternational:true}):'',connected=item.status==='connected',stats=item.metrics||{},caps=item.capabilities||{};
      const badge='<span class="os-badge '+(connected?'os-success':['error','logged_out','pair_failed','unavailable'].includes(item.status)?'os-warning':'os-neutral')+'">'+esc(statuses[item.status])+'</span>';
      const actionEnabled=canWrite(),attr='data-sd-device="'+item.id+'"';
      const menu=[button('Abrir conexão','open-connect','','link',attr),...(caps.settings?[button('Configurações','settings','','settings',attr)]:[]),...(caps.restart?[button('Reiniciar conexão','restart','','refresh',attr+(!actionEnabled?' disabled':''))]:[]),...(caps.relink?[button('Refazer vínculo','relink','','link',attr+(!actionEnabled?' disabled':''))]:[]),...(caps.remove?[button('Excluir dispositivo','remove','os-device-danger','trash',attr+(!actionEnabled?' disabled':''))]:[])].join('');
      const lastSeen=dateTime(item.last_seen_at,stats.timezone),tracking=dateTime(stats.tracking_since,stats.timezone);
      const health=item.last_error?'<p class="os-device-health" data-error="true">'+esc(typeof item.last_error==='string'?item.last_error:'A conexão precisa de atenção. Atualize o estado.')+'</p>':lastSeen?'<p class="os-device-health">Última atualização: '+esc(lastSeen)+'</p>':'';
      const featureSettings=caps.settings===true&&canManage();
      const features=object(item.features)?'<div class="os-device-features">'+[['transcription','Transcrição de áudio','mic','transcription'],['call_blocking','Bloqueio de ligações','shield','calls']].map(([key,label,symbol,section])=>'<button type="button" class="os-device-feature" '+(featureSettings?'data-sd-action="settings" '+attr+' data-sd-open-section="'+section+'"':'disabled')+'><span>'+icon(symbol)+esc(label)+'</span><span class="os-badge '+(item.features[key]===true?'os-success':'os-neutral')+'" title="Preferência salva do dispositivo">'+(item.features[key]===true?'Ativado':'Desativado')+'</span></button>').join('')+'</div>':'';
      return '<article class="os-card os-device-card" data-sd-card="'+item.id+'"><div class="os-device-card-top"><div class="os-device-identity"><div class="os-device-avatar">'+(avatar?'<img src="'+esc(avatar)+'" alt="" loading="lazy" referrerpolicy="no-referrer">':icon('phone'))+'</div><div><h2>'+esc(item.name)+'</h2><div class="os-device-status-line">'+badge+'</div>'+(verifiedPhone?'<p class="os-device-phone">'+esc(verifiedPhone)+'</p>':'')+'</div></div><details class="os-device-menu" data-sd-device="'+item.id+'"><summary class="os-button os-outline" aria-label="Opções de '+esc(item.name)+'">'+icon('more')+'<span>Opções</span></summary><div>'+menu+'</div></details></div><div class="os-device-metrics" aria-label="Mensagens registradas">'+[['today','Hoje'],['last_7_days','7 dias'],['last_30_days','30 dias'],['total','Total']].map(([key,label])=>'<div class="os-device-metric"><strong>'+count(stats.available===true?stats[key]:null)+'</strong><span>'+label+'</span></div>').join('')+'</div><p class="os-help os-device-metrics-note">'+(stats.available===true?'Mensagens registradas pelo Orion'+(tracking?' desde '+esc(tracking):'')+'.':'As métricas ainda não estão disponíveis.')+'</p>'+health+'<div class="os-device-card-actions">'+button(connected?'Ver conexão':'Conectar','open-connect','', 'link',attr)+(caps.settings?button('Configurações','settings','os-outline','settings',attr):'')+(caps.restart?button('Reiniciar','restart','os-outline','refresh',attr+(!actionEnabled?' disabled':'')):'')+'</div>'+features+'</article>';
    }
    function renderList(){
      const usage=state.usage,limit=usage?'<div class="os-devices-usage"><span class="os-badge os-neutral">'+count(usage.used)+' de '+count(usage.limit)+'</span><span>'+((usage.limit===1)?'dispositivo em uso':'dispositivos em uso')+'</span></div>':'';
      const create=canManage()?button('Criar dispositivo','create','','plus',!canWrite()?'disabled':''):'';
      const header='<header class="os-title-row os-devices-header"><div><h1>Dispositivos</h1><p>Conecte e configure o WhatsApp da sua empresa.</p></div><div class="os-actions">'+button('Atualizar','refresh','os-outline','refresh',state.busy||state.loading?'disabled':'')+create+'</div></header>';
      const body=!state.loaded?loading('Conferindo os dispositivos…'):state.items.length?'<div class="os-devices-list">'+state.items.map(card).join('')+'</div>':'<section class="os-card os-empty"><div class="os-square-icon">'+icon('phone')+'</div><h2>Nenhum dispositivo cadastrado</h2><p>Crie o dispositivo da sua empresa para começar a conexão com o WhatsApp.</p>'+(canManage()?button('Criar dispositivo','create','','plus',!canWrite()?'disabled':''):'')+'</section>';
      paint(header+limit+feedbackHTML()+pendingHTML()+body);
    }
    function connectionBody(item){
      const locked=!canWrite(),codeVisible=item.capabilities.pair===true||!!state.pair||state.pending?.kind==='pair'||item.status==='pairing',mode=state.method==='code'&&codeVisible?'code':'qr';
      if(item.status==='connected')return '<section class="os-card os-device-connected"><div class="os-square-icon">'+icon('check')+'</div><h2>WhatsApp conectado</h2><p>'+(item.phone?esc(fields.phoneDisplay('+'+item.phone,{allowInternational:true})):'O WhatsApp confirmou a conexão.')+'</p><div class="os-actions">'+button('Conferir estado','refresh','os-outline','refresh')+(item.capabilities.relink?button('Atualizar conexão','relink','os-outline','link',locked?'disabled':''):'')+'</div></section>';
      if(item.capabilities.qr!==true&&!codeVisible&&!state.qr){
        return '<section class="os-card"><h2>'+esc(statuses[item.status])+'</h2><p>'+(item.capabilities.connect?'Prepare a conexão para vincular este dispositivo ao WhatsApp.':['starting','connecting','reconnecting'].includes(item.status)?'Aguarde enquanto a conexão fica pronta.':'Confira o estado para ver as ações disponíveis para este dispositivo.')+'</p><div class="os-actions">'+(item.capabilities.connect?button('Preparar conexão','connect','','link',locked?'disabled':''):'')+(item.capabilities.relink?button('Refazer vínculo','relink','os-outline','refresh',locked?'disabled':''):'')+button('Conferir estado','refresh','os-outline','refresh')+'</div></section>';
      }
      const methods=codeVisible?'<div class="wizard-methods" role="group" aria-label="Escolha a forma de conexão">'+connectView.methods(mode,'data-qc-method',locked).replaceAll('data-qc-method','data-sd-method')+'</div>':'';
      if(!state.tutorial[mode])return connectView.heading(codeVisible)+methods+'<section class="wizard-card"><div class="wizard-tutorial-heading"><h3>Prepare o WhatsApp no seu celular</h3><p>Continue quando estiver pronto para vincular este dispositivo.</p></div>'+connectView.instructions(mode)+button('Concluí os passos e estou pronto para prosseguir','tutorial-next','wizard-primary os-full wizard-continue','',locked?'disabled':'')+'</section>';
      let content='';
      if(mode==='qr'){
        content=state.qr?'<section data-sd-qr><h3>Escaneie com o WhatsApp</h3><div class="wizard-qr-wrap"><img src="'+esc(state.qr.image)+'" alt="QR temporário para conectar o WhatsApp da sua empresa" width="240" height="240"></div><p class="wizard-qr-time">Expira em <strong data-sd-expiry>—</strong></p><div class="os-device-code-actions">'+button('Ocultar QR Code','hide-qr')+'</div></section>':'<h3>Conectar por QR Code</h3><p>Use o WhatsApp do aparelho que será conectado para escanear o código.</p><div class="os-actions">'+button('Mostrar QR Code','qr','','',locked||item.capabilities.qr!==true||!!state.pair?'disabled':'')+'</div>';
      }else if(state.pair){
        content=state.pairHidden?'<h3>Código oculto</h3><p>O código continua válido até expirar.</p><p class="wizard-qr-time">Expira em <strong data-sd-expiry>—</strong></p><div class="os-device-code-actions">'+button('Mostrar código','show-code')+'</div>':'<section class="wizard-code-result" data-sd-pair><h3>Digite este código no WhatsApp</h3><output class="code-box" data-sd-code>'+esc(state.pair.code)+'</output><p class="wizard-qr-time">Expira em <strong data-sd-expiry>—</strong></p><div class="os-device-code-actions">'+button('Copiar código','copy-code','','copy')+button('Ocultar código','hide-code')+'</div></section>';
      }else{
        content='<h3>Informe o número do WhatsApp</h3><p>Use o número do aparelho que será conectado, com DDD.</p><form class="os-form" data-sd-form="pair">'+input('Número do WhatsApp','pair_phone',state.phoneDraft,'tel','data-orion-field="phone" data-orion-international="true" inputmode="tel" autocomplete="tel" required placeholder="(00) 00000-0000"','Para outro país, inclua + e o código do país.')+'<div class="os-form-actions"><button type="submit" class="os-button"'+(locked||item.capabilities.pair!==true||item.status!=='qr_ready'?' disabled':'')+'>Gerar código</button></div></form>';
      }
      return connectView.heading(codeVisible)+methods+'<section class="wizard-card">'+content+(!state.qr&&!state.pair?'<div class="wizard-steps-actions">'+button('Voltar aos passos','tutorial-back')+'</div>':'')+'</section>';
    }
    function renderConnection(){
      const item=device();if(!item){paint(feedbackHTML()+loading('Conferindo a conexão…'));return;}
      const header=button('Dispositivos','back','os-ghost os-device-back','left')+'<div class="os-device-connection-heading"><div><h1>'+esc(item.name)+'</h1><p><span class="os-badge '+(item.status==='connected'?'os-success':'os-neutral')+'" data-sd-status>'+esc(statuses[item.status])+'</span></p></div>'+button('Atualizar estado','refresh','os-outline','refresh',state.busy?'disabled':'')+'</div>';
      paint('<div class="os-device-connection os-connect">'+header+feedbackHTML()+pendingHTML()+connectionBody(item)+'</div>');
    }
    function scheduleHTML(group,rows){
      const business=group==='business_hours',labels=business?[['start','Início'],['break_start','Pausa'],['break_end','Retorno'],['end','Fim']]:[['start','Das'],['end','Até']];
      return '<div class="os-device-schedule">'+weekdays.map((day,index)=>{const row=rows.find(item=>item.weekday===index);return '<div class="os-device-day" data-sd-schedule="'+group+'" data-sd-weekday="'+index+'"><label class="os-device-day-title"><input type="checkbox" data-sd-day-enabled'+(row.enabled?' checked':'')+'><span>'+day+'</span></label><div class="os-device-day-fields">'+labels.map(([key,label])=>'<label>'+label+'<input type="time" data-sd-time="'+key+'" value="'+esc(row[key]||'')+'" aria-label="'+esc(label+' de '+day)+(business?' no atendimento':' para receber ligações')+'"'+(!row.enabled?' disabled':'')+'></label>').join('')+'</div><div class="os-device-day-actions"><select data-sd-copy aria-label="Copiar '+esc(day)+'"><option value="">Copiar para…</option><option value="weekdays">Dias úteis</option><option value="weekend">Fim de semana</option><option value="all">Todos os dias</option>'+weekdays.map((label,target)=>target===index?'':'<option value="'+target+'">'+label+'</option>').join('')+'</select>'+button('Limpar','clear-day','os-ghost')+'</div></div>';}).join('')+'</div>';
    }
    function timezoneHTML(value){
      let values=['America/Sao_Paulo','America/Cuiaba','America/Campo_Grande','America/Manaus','America/Rio_Branco','America/Noronha','Europe/Lisbon','UTC'];
      try{if(Intl.supportedValuesOf)values=[...values,...Intl.supportedValuesOf('timeZone')];}catch{}
      values=[...new Set([value,...values])];
      return '<div class="os-field"><label for="sd-timezone">Fuso horário</label><select id="sd-timezone" data-sd-field="timezone">'+values.map(zone=>'<option value="'+esc(zone)+'"'+(zone===value?' selected':'')+'>'+esc(zone.replaceAll('_',' '))+'</option>').join('')+'</select><small class="os-help">Os horários de atendimento e de ligações seguem este fuso.</small></div>';
    }
    function renderSettings(openSection){
      const s=state.settings,caps=state.settingsCapabilities;if(!s){paint(feedbackHTML()+loading('Carregando as configurações…'));return;}
      // Settings are rendered only after an explicit load/save. Rebuild even
      // when the public values did not change so write-only secrets are erased.
      lastHTML='';
      const webhookUnavailable=caps.webhook!==true,proxyUnavailable=caps.proxy!==true,transcriptionUnavailable=caps.transcription!==true;
      const unavailable=label=>'<p class="os-notice">'+esc(label+' ainda não está disponível para ativação nesta conexão.')+'</p>';
      const general='<section class="os-card"><div class="os-card-header"><div><h2>Identificação e horário</h2><p>Preferências deste dispositivo.</p></div></div><div class="os-form-grid">'+input('Nome do dispositivo','name',s.name,'text','required maxlength="100"')+timezoneHTML(s.timezone)+input('Número para teste fora do horário','test_phone_e164',s.test_phone_e164?fields.phoneDisplay(s.test_phone_e164,{allowInternational:true}):'','tel','data-orion-field="phone" data-orion-international="true" inputmode="tel" autocomplete="tel"','Opcional. Esse número pode testar as respostas de ausência sem aguardar o intervalo de repetição.')+'</div></section>';
      const webhook=section('webhook','Webhook','Envie eventos deste dispositivo para uma integração.',(webhookUnavailable?unavailable('O webhook'):'')+checkbox('Ativar webhook','webhook.enabled',s.webhook.enabled,'Os eventos deste dispositivo serão enviados por POST à URL configurada.',webhookUnavailable&&!s.webhook.enabled)+'<div class="os-device-dependent" data-sd-dependent="webhook.enabled"'+(!s.webhook.enabled?' hidden':'')+'>'+input('URL do webhook','webhook.url',s.webhook.url||'','url','maxlength="2048" placeholder="https://…"')+checkbox('Desativar após falhas repetidas','webhook.disable_on_failure',s.webhook.disable_on_failure,'Interrompe as tentativas quando a integração falha repetidamente.')+'</div>');
      const proxy=section('proxy','Conexão e proxy','Use a conexão padrão do Orion ou configure seu proxy.',(proxyUnavailable?unavailable('O proxy personalizado'):'')+checkbox('Usar proxy personalizado','proxy.enabled',s.proxy.enabled,'Desativado: usa a conexão padrão do Orion.',proxyUnavailable&&!s.proxy.enabled)+'<div class="os-device-dependent" data-sd-dependent="proxy.enabled"'+(!s.proxy.enabled?' hidden':'')+'>'+input('Endereço do proxy','proxy.url',s.proxy.url||'','url','maxlength="2048" placeholder="https://…"','Use HTTPS e informe o endereço sem usuário ou senha na URL.')+'<div class="os-form-grid">'+input('Usuário do proxy','proxy.username',s.proxy.username||'','text','maxlength="200" autocomplete="off"')+'<div class="os-field"><label for="sd-proxy-password">Senha do proxy</label><input id="sd-proxy-password" type="password" data-sd-secret maxlength="512" autocomplete="new-password" placeholder="'+(s.proxy.password_configured?'Senha já salva':'Opcional')+'"><small class="os-help">'+(s.proxy.password_configured?'Deixe vazio para manter a senha salva. A senha não é exibida.':'A senha será armazenada sem ser exibida novamente.')+'</small></div></div>'+(s.proxy.password_configured?'<label class="os-check"><input type="checkbox" data-sd-clear-password><span>Remover a senha salva</span></label>':'')+'</div>');
      const maxMinutes=Number.isSafeInteger(caps.transcription_max_minutes)?caps.transcription_max_minutes:null;
      const transcription=section('transcription','Transcrição de áudio','Defina a duração e a apresentação das transcrições.',(transcriptionUnavailable?unavailable('A transcrição de áudio'):'')+checkbox('Transcrever áudios recebidos','transcription.enabled',s.transcription.enabled,'A transcrição segue o formato abaixo.',transcriptionUnavailable&&!s.transcription.enabled)+'<div class="os-device-dependent" data-sd-dependent="transcription.enabled"'+(!s.transcription.enabled?' hidden':'')+'>'+input('Duração máxima do áudio (minutos)','transcription.max_minutes',s.transcription.max_minutes,'number','min="1" max="30" step="1" required',maxMinutes?'Disponível nesta conexão: até '+maxMinutes+' minutos.':'Informe de 1 a 30 minutos.')+textarea('Modelo da transcrição','transcription.template',s.transcription.template,2000)+'<div class="os-device-variables"><span>Campos disponíveis:</span><code>{name}</code><code>{transcript}</code><code>{footer}</code></div>'+textarea('Rodapé','transcription.footer',s.transcription.footer,500)+checkbox('Responder ao áudio','transcription.reply_enabled',s.transcription.reply_enabled,'Depois de transcrever, procura uma resposta correspondente à mensagem. Se não encontrar, não envia uma resposta adicional.')+'</div>',openSection==='transcription');
      const calls=section('calls','Ligações','Controle chamadas recebidas, exceções e horários.',checkbox('Rejeitar ligações recebidas','calls.reject_enabled',s.calls.reject_enabled)+'<div class="os-device-dependent" data-sd-dependent="calls.reject_enabled"'+(!s.calls.reject_enabled?' hidden':'')+'>'+checkbox('Enviar mensagem após rejeitar uma ligação','calls.reply_enabled',s.calls.reply_enabled)+'<div data-sd-dependent="calls.reply_enabled"'+(!s.calls.reply_enabled?' hidden':'')+'>'+textarea('Mensagem após rejeitar','calls.reply_message',s.calls.reply_message,1200)+'</div>'+textarea('Números que sempre podem ligar','calls.whitelist',s.calls.whitelist.map(phone=>fields.phoneDisplay('+'+String(phone).replace(/^\+/,''),{allowInternational:true})).join('\n'),4000,'Um número por linha, com DDD. Para outro país, inclua + e o código do país.')+checkbox('Permitir ligações apenas nos horários definidos','calls.schedule_enabled',s.calls.schedule_enabled)+'<div data-sd-dependent="calls.schedule_enabled"'+(!s.calls.schedule_enabled?' hidden':'')+'>'+scheduleHTML('calls',s.calls.days)+'</div>'+checkbox('Rejeitar ligações em feriados nacionais brasileiros','calls.holiday_off',s.calls.holiday_off)+'</div>',openSection==='calls');
      const hours=section('business_hours','Horário de atendimento','Configure expediente, pausas e respostas de ausência.',checkbox('Usar horário de atendimento','business_hours.enabled',s.business_hours.enabled)+'<div class="os-device-dependent" data-sd-dependent="business_hours.enabled"'+(!s.business_hours.enabled?' hidden':'')+'>'+scheduleHTML('business_hours',s.business_hours.days)+checkbox('Não atender em feriados nacionais brasileiros','business_hours.holiday_off',s.business_hours.holiday_off)+checkbox('Bloquear respostas automáticas fora do expediente','business_hours.block_replies_outside_hours',s.business_hours.block_replies_outside_hours)+textarea('Mensagem fora do horário','business_hours.outside_message',s.business_hours.outside_message,1200)+textarea('Mensagem durante a pausa','business_hours.break_message',s.business_hours.break_message,1200)+'<p class="os-help">As mensagens de ausência não se repetem para o mesmo contato por 24 horas. O número de teste é uma exceção.</p></div>',openSection==='business_hours');
      paint('<div class="os-device-settings">'+button('Dispositivos','back','os-ghost os-device-back','left')+'<header class="os-title-row os-device-settings-intro"><div><h1>Configurações do dispositivo</h1><p>'+esc(device()?.name||s.name)+'</p></div></header>'+feedbackHTML()+pendingHTML()+'<form class="os-device-settings-form" data-sd-form="settings">'+general+webhook+proxy+transcription+calls+hours+'<div class="os-form-message" data-sd-form-error role="alert"></div><div class="os-device-settings-actions"><p class="os-help" data-sd-save-status>As alterações só entram em vigor depois de salvar.</p>'+button('Cancelar','back','os-outline')+'<button type="submit" class="os-button"'+(!canWrite()||state.settingsStale?' disabled':'')+'>'+icon('check')+'Salvar configurações</button></div></form></div>');
      state.formFingerprint=formFingerprint();
    }
    function render(){
      if(!state.mounted)return;
      host.setAttribute('aria-busy',String(state.busy||state.loading));
      if(state.view==='settings')return;
      if(state.view==='connect')renderConnection();else renderList();
    }
    function refreshPending(){
      host.querySelector('[data-sd-pending]')?.remove();
      if(!state.pending)return;
      const wrapper=document.createElement('div');wrapper.dataset.sdPending='';wrapper.innerHTML=pendingHTML();
      const form=host.querySelector('[data-sd-form="settings"]');
      if(form)form.before(wrapper);
    }
    function setBusy(value){
      state.busy=value;host.setAttribute('aria-busy',String(value||state.loading));
      for(const root of [host,state.dialog?.element].filter(Boolean))for(const node of root.querySelectorAll('button,input,select,textarea')){
        if(value){if(node.dataset.sdWasDisabled===undefined)node.dataset.sdWasDisabled=String(node.disabled);node.disabled=true;}
        else if(node.dataset.sdWasDisabled!==undefined){node.disabled=node.dataset.sdWasDisabled==='true';delete node.dataset.sdWasDisabled;}
      }
      const submit=host.querySelector('[data-sd-form="settings"] button[type="submit"]');
      if(submit&&!value)submit.disabled=!canWrite()||state.settingsStale;
    }
    function schedule(delay=5000){
      clearTimeout(pollTimer);pollTimer=null;
      if(!visible()||state.busy||state.loading||state.dialog||state.view==='settings')return;
      if(state.pending&&!state.operation&&state.pending.kind!=='pair')return;
      pollTimer=setTimeout(()=>{pollTimer=null;if(state.operation)void pollOperation();else if(state.view==='connect')void loadDetail();else void loadList(true);},delay);
    }
    async function loadList(quiet=false){
      if(!visible()||state.busy||state.loading)return;
      const epoch=state.epoch,read=++state.read;state.loading=true;if(!quiet)render();
      try{
        const result=await request(base);
        if(!current(epoch,read))return;
        if(!object(result)||!Array.isArray(result.items)||result.items.length>1||!object(result.usage)||!Number.isSafeInteger(result.usage.used)||result.usage.used<0||result.usage.limit!==1||result.usage.used>result.usage.limit||typeof result.can_create!=='boolean'||!object(result.capabilities))throw Error('invalid_devices_response');
        const items=result.items.map(deviceDTO);
        if(new Set(items.map(item=>item.id)).size!==items.length)throw Error('invalid_devices_response');
        const existing=device(),next=items.find(item=>item.id===state.id);
        if(existing&&(!next||existing.revision!==next.revision||next.status==='connected'||next.status==='logged_out'))clearCredentials();
        state.items=items;state.usage=copy(result.usage);state.capabilities=copy(result.capabilities);state.canCreate=result.can_create;state.loaded=true;state.stale=false;
        for(const item of items)adoptOperation(item);
        if(state.capabilities.manage!==true){clearCredentials();closeDialog(true);}
        if(state.pending?.kind==='create'&&items.length){state.pending=null;feedback('O dispositivo está cadastrado. Confira a conexão antes de continuar.');}
        if(!quiet&&!state.pending)feedback('');
      }catch(error){if(current(epoch,read)){state.stale=true;feedback(errorMessage(error),true);}}
      finally{if(current(epoch,read)){state.loading=false;render();schedule();}}
    }
    async function loadDetail(){
      if(!visible()||state.busy||state.loading||!state.id)return;
      const epoch=state.epoch,read=++state.read,id=state.id;state.loading=true;
      try{
        const result=await request(path(id));
        if(!current(epoch,read)||id!==state.id)return;
        if(result?.device?.id!==id)throw Error('foreign_device_response');
        const item=updateDevice(result.device);state.stale=false;
        if(state.pending?.kind==='pair'&&!['pairing','pair_failed','connecting','reconnecting'].includes(item.status)){state.pending=null;feedback('Estado conferido. Continue pela ação disponível.');}
      }catch(error){if(current(epoch,read)){state.stale=true;feedback(errorMessage(error),true);}}
      finally{if(current(epoch,read)){state.loading=false;render();schedule();}}
    }
    function navigate(view,id=null){
      pause();state.epoch++;state.view=view;state.id=id;state.loading=false;state.stale=false;state.method='code';state.tutorial={code:false,qr:false};state.phoneDraft='';state.settings=null;state.settingsRevision=null;state.settingsStale=false;state.formFingerprint=null;lastHTML='';closeDialog(true);feedback('');
    }
    async function openConnection(id){
      if(!state.items.some(item=>item.id===id))return;
      navigate('connect',id);render();await loadDetail();
    }
    async function back(){
      if(state.busy)return;
      if(state.view==='settings'&&isDirty()){openDialog('discard','Descartar alterações?','<p>As alterações deste formulário ainda não foram salvas.</p>',{confirm:'Descartar alterações'});return;}
      navigate('list');render();await loadList();
    }
    async function openSettings(id,sectionName){
      const item=state.items.find(row=>row.id===id);
      if(!canDevice('settings',item))return;
      navigate('settings',id);paint(button('Dispositivos','back','os-ghost os-device-back','left')+feedbackHTML()+loading('Carregando as configurações…'));
      const epoch=state.epoch,read=++state.read;state.loading=true;
      try{
        const result=settingsDTO(await request(path(id,'/settings')));
        if(!current(epoch,read)||state.id!==id)return;
        applySettings(result);renderSettings(sectionName);
      }catch(error){if(current(epoch,read)){feedback(errorMessage(error),true);paint(button('Dispositivos','back','os-ghost os-device-back','left')+feedbackHTML()+'<div class="os-actions">'+button('Tentar novamente','reload-settings')+'</div>');}}
      finally{if(current(epoch,read)){state.loading=false;host.setAttribute('aria-busy','false');}}
    }
    function applySettings(result){
      state.settings=copy(result.settings);state.settingsRevision=result.revision;state.settingsCapabilities=copy(result.capabilities);state.settingsStale=false;
      const item=device();if(item){item.name=result.settings.name;item.revision=result.revision;item.features={transcription:result.settings.transcription.enabled===true,call_blocking:result.settings.calls.reject_enabled===true};}
      state.restartRequired=result.restart_required===true;
    }
    function formFingerprint(){
      const form=host.querySelector('[data-sd-form="settings"]');if(!form)return null;
      return JSON.stringify([...form.querySelectorAll('input,select,textarea')].map(node=>[node.dataset.sdField||node.dataset.sdTime||node.hasAttribute('data-sd-day-enabled')||node.hasAttribute('data-sd-secret')||node.hasAttribute('data-sd-clear-password')||'',node.type==='checkbox'?node.checked:node.value]));
    }
    const isDirty=()=>state.formFingerprint!==null&&formFingerprint()!==state.formFingerprint;
    function readDay(node){
      const day={weekday:Number(node.dataset.sdWeekday),enabled:node.querySelector('[data-sd-day-enabled]').checked,start:null,break_start:null,break_end:null,end:null};
      for(const input of node.querySelectorAll('[data-sd-time]'))day[input.dataset.sdTime]=input.value||null;
      return day;
    }
    function validateDay(day,label){
      if(day.enabled&&(!/^\d{2}:\d{2}$/.test(day.start||'')||!/^\d{2}:\d{2}$/.test(day.end||'')||day.start>=day.end))throw Error('Confira o início e o fim de '+label+'. O fim deve ser depois do início.');
      if(Boolean(day.break_start)!==Boolean(day.break_end))throw Error('Preencha o início e o fim da pausa de '+label+'.');
      if(day.break_start&&!(day.start<day.break_start&&day.break_start<day.break_end&&day.break_end<day.end))throw Error('A pausa de '+label+' deve ficar dentro do expediente.');
    }
    function collectSettings(){
      const form=host.querySelector('[data-sd-form="settings"]'),settings=copy(state.settings);
      for(const node of form.querySelectorAll('[data-sd-field]')){
        const key=node.dataset.sdField;
        let value=node.type==='checkbox'?node.checked:node.type==='number'?Number(node.value):node.value;
        if(key==='calls.whitelist'){
          const lines=String(value).split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
          try{value=lines.map(phone=>fields.phoneValue(phone,{required:true,allowInternational:true}).replace(/^\+/,''));}catch{throw Error('Confira os números que sempre podem ligar. Use um número completo por linha.');}
          if(new Set(value).size!==value.length)throw Error('Há números repetidos na lista de exceções de ligações.');
        }else if(key==='test_phone_e164'){
          try{value=String(value).trim()?fields.phoneValue(value,{required:true,allowInternational:true}):null;}catch{throw Error('Confira o número de teste. Informe um número completo com DDD.');}
        }else if(['webhook.url','proxy.url','proxy.username'].includes(key))value=String(value).trim()||null;
        else if(key==='name'||key==='timezone')value=String(value).trim();
        set(settings,key,value);
      }
      for(const group of ['calls','business_hours']){
        settings[group].days=[...form.querySelectorAll('[data-sd-schedule="'+group+'"]')].map(node=>readDay(node));
        for(const day of settings[group].days)validateDay(day,weekdays[day.weekday].toLowerCase());
      }
      delete settings.proxy.password_configured;
      const password=form.querySelector('[data-sd-secret]').value,clear=form.querySelector('[data-sd-clear-password]')?.checked===true;
      if(password&&clear)throw Error('Escolha entre informar uma nova senha do proxy ou remover a senha salva.');
      if(password&&!settings.proxy.username)throw Error('Informe o usuário para salvar a senha do proxy.');
      if(password)settings.proxy.password=password;
      if(clear)settings.proxy.clear_password=true;
      if(!settings.name)throw Error('Informe o nome do dispositivo.');
      try{new Intl.DateTimeFormat('pt-BR',{timeZone:settings.timezone});}catch{throw Error('Escolha um fuso horário válido.');}
      for(const key of ['webhook','proxy'])if(settings[key].enabled&&!settings[key].url)throw Error('Informe '+(key==='webhook'?'a URL do webhook.':'o endereço do proxy.'));
      const caps=state.settingsCapabilities;
      for(const [key,label] of [['webhook','o webhook'],['proxy','o proxy personalizado'],['transcription','a transcrição']])if(settings[key].enabled&&caps[key]!==true)throw Error('Não é possível ativar '+label+' nesta conexão.');
      if(!Number.isInteger(settings.transcription.max_minutes)||settings.transcription.max_minutes<1||settings.transcription.max_minutes>30)throw Error('A duração máxima do áudio deve ser de 1 a 30 minutos.');
      if(settings.transcription.enabled&&Number.isInteger(caps.transcription_max_minutes)&&settings.transcription.max_minutes>caps.transcription_max_minutes)throw Error('A duração disponível nesta conexão é de até '+caps.transcription_max_minutes+' minutos.');
      const template=settings.transcription.template.replace(/\{\{|\}\}/g,''),tags=template.match(/\{[^}]*\}/g)||[];
      if(tags.some(tag=>!['{name}','{transcript}','{footer}'].includes(tag))||tags.filter(tag=>tag==='{transcript}').length!==1||/[{}]/.test(template.replace(/\{(?:name|transcript|footer)\}/g,'')))throw Error('Use {transcript} uma vez e apenas os campos {name}, {transcript} e {footer} no modelo de transcrição.');
      if(settings.calls.reject_enabled&&settings.calls.reply_enabled&&!settings.calls.reply_message.trim())throw Error('Informe a mensagem enviada após rejeitar a ligação.');
      return settings;
    }
    function settingsError(message){
      const node=host.querySelector('[data-sd-form-error]');
      if(node){node.className='os-notice os-error';node.textContent=message;if(state.settingsStale&&!state.pending)node.insertAdjacentHTML('beforeend',button('Recarregar configurações','reload-settings'));node.tabIndex=-1;node.focus();node.scrollIntoView({block:'nearest'});}else feedback(message,true);
    }
    async function saveSettings(form){
      if(!canWrite()||state.settingsStale||!form.reportValidity())return;
      let settings;try{settings=collectSettings();}catch(error){settingsError(error.message);return;}
      const epoch=state.epoch,id=state.id,revision=state.settingsRevision;
      host.querySelector('[data-sd-form-error]').textContent='';setBusy(true);clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=settingsDTO(await request(path(id,'/settings'),'PUT',{expected_revision:revision,settings}));
        if(!current(epoch)||state.id!==id)return;
        if(result.revision<=revision)throw Error('invalid_settings_revision');
        state.pending=null;applySettings(result);setBusy(false);renderSettings();
        feedback(result.restart_required?'Configurações salvas. Reinicie a conexão para aplicar a alteração de conexão.':'Configurações salvas.');
        if(result.restart_required&&device()?.capabilities.restart){const note=host.querySelector('[data-sd-feedback]');note.insertAdjacentHTML('beforeend','<div class="os-actions">'+button('Reiniciar conexão','restart','os-outline','refresh')+'</div>');}
      }catch(error){if(current(epoch)){state.settingsStale=Number(error.status)===409||uncertain(error);if(uncertain(error))state.pending={kind:'settings',id,revision,settings};settingsError(errorMessage(error));refreshPending();}}
      finally{if(current(epoch))setBusy(false);}
    }
    function closeDialog(force=false){
      if(state.busy&&!force)return;
      const dialog=state.dialog;if(!dialog)return;state.dialog=null;
      dialog.element.removeEventListener('click',click);dialog.element.removeEventListener('submit',submit);dialog.element.removeEventListener('cancel',cancelDialog);
      if(dialog.element.open)dialog.element.close();dialog.element.remove();dialog.trigger?.isConnected&&dialog.trigger.focus({preventScroll:true});schedule();
    }
    function cancelDialog(event){event.preventDefault();closeDialog();}
    function openDialog(kind,title,body,meta={}){
      if(state.busy)return;closeDialog();clearTimeout(pollTimer);pollTimer=null;
      const element=document.createElement('dialog');element.className='os-dialog os-device-dialog';element.setAttribute('aria-labelledby','sd-dialog-title');
      const danger=['remove','discard'].includes(kind),confirm=meta.confirm||({create:'Criar dispositivo',connect:'Preparar conexão',restart:'Reiniciar conexão',relink:'Atualizar conexão',remove:'Excluir dispositivo',discard:'Descartar alterações'}[kind]||'Confirmar');
      element.innerHTML='<button type="button" class="os-icon-button os-close" data-sd-action="close-dialog" aria-label="Fechar">'+icon('close')+'</button><h2 id="sd-dialog-title">'+esc(title)+'</h2><form class="os-form" data-sd-form="dialog">'+body+'<div class="os-form-message" role="alert"></div><div class="os-form-actions">'+button('Cancelar','close-dialog','os-outline')+'<button type="submit" class="os-button'+(danger?' os-danger':'')+'">'+esc(confirm)+'</button></div></form>';
      state.dialog={kind,element,trigger:document.activeElement,key:crypto.randomUUID(),id:state.id,...meta};
      element.addEventListener('click',click);element.addEventListener('submit',submit);element.addEventListener('cancel',cancelDialog);document.body.append(element);fields.refresh(element);element.showModal();
      element.querySelector('input:not([disabled]),button:not(.os-close)')?.focus({preventScroll:true});
    }
    function confirmAction(action,id=state.id){
      const item=state.items.find(row=>row.id===id);
      if(!canDevice(action,item))return;
      const texts={connect:['Preparar conexão?','O Orion vai preparar este dispositivo. Depois, você poderá vincular o WhatsApp por QR Code ou código.'],restart:['Reiniciar conexão?','A conexão será interrompida por alguns instantes. O vínculo com o WhatsApp será preservado.'],relink:['Atualizar conexão?','O vínculo atual será encerrado para permitir uma nova conexão. Você precisará vincular o WhatsApp novamente.'],remove:['Excluir dispositivo?','O dispositivo e suas configurações serão removidos do Orion. A conexão será encerrada. Para conectar novamente, será necessário criar outro dispositivo.']};
      const [title,description]=texts[action];
      let content='<p>'+esc(description)+'</p><div class="os-notice"><strong>'+esc(item.name)+'</strong></div>';
      if(action==='remove')content+='<p>Se você precisa corrigir a conexão ou alterar uma preferência, use uma destas opções:</p><div class="os-device-dialog-alternatives">'+(item.capabilities.settings?button('Editar configurações','alternative-settings','os-outline','settings'):'')+(item.capabilities.relink?button('Refazer vínculo','alternative-relink','os-outline','link'):'')+'</div>';
      openDialog(action,title,content,{id,revision:item.revision});
    }
    async function createDevice(name,key){
      if(!canManage()||state.busy||state.pending&&!(state.pending.kind==='create'&&state.pending.key===key))return;
      const epoch=state.epoch,operation={kind:'create',name,key};setBusy(true);clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(base,'POST',{name,idempotency_key:key});if(!current(epoch))return;
        const item=deviceDTO(result?.device);state.pending=null;updateDevice(item);state.usage={used:1,limit:1};state.canCreate=false;setBusy(false);closeDialog(true);state.view='list';state.id=null;feedback('Dispositivo criado. Abra a conexão para vincular o WhatsApp.');render();schedule();
      }catch(error){
        if(current(epoch)){setBusy(false);if(uncertain(error)){state.pending=operation;closeDialog(true);feedback('Não foi possível confirmar a criação. Confira a lista antes de tentar novamente.',true);render();}else if(state.dialog){state.dialog.element.querySelector('.os-form-message').textContent=errorMessage(error);}else feedback(errorMessage(error),true);}
      }finally{if(current(epoch))setBusy(false);}
    }
    function operationDTO(result,id,action){
      const op=result?.operation;
      if(!object(op)||!uuid(op.id)||op.device_id!==id||op.action!==action||!['pending','running','succeeded','failed','uncertain'].includes(op.status)||!Object.hasOwn(result,'device'))throw Error('invalid_operation_response');
      if(result.device!==null&&result.device.id!==id)throw Error('foreign_operation_response');
      return copy(op);
    }
    async function acceptOperation(result,id,action,expectedId){
      const op=operationDTO(result,id,action),next=result.device===null?null:deviceDTO(result.device);
      if(expectedId&&op.id!==expectedId||action==='remove'&&op.status==='succeeded'&&next!==null||next===null&&!(action==='remove'&&op.status==='succeeded'))throw Error('invalid_operation_response');
      state.operation=op;
      if(next)updateDevice(next);
      if(op.status==='succeeded'){
        state.pending=null;state.operation=null;
        if(action==='remove'){
          state.items=state.items.filter(item=>item.id!==id);state.usage={used:state.items.length,limit:1};state.canCreate=true;clearCredentials();state.view='list';state.id=null;
          feedback('Dispositivo excluído.');return;
        }
        feedback(action==='pair'?'Solicitação de vínculo confirmada. Acompanhe o estado do WhatsApp.':action==='restart'?'Conexão reiniciada. Acompanhe o estado do WhatsApp.':action==='relink'?'Conexão atualizada. Vincule o WhatsApp novamente.':'Conexão preparada. Escolha como vincular o WhatsApp.');
      }else if(op.status==='failed'){
        state.pending=null;state.operation=null;feedback(typeof op.error==='string'?op.error:op.error?.message||'A ação não foi concluída. Confira o estado antes de tentar novamente.',true);
      }else if(op.status==='uncertain')feedback('O resultado da ação ainda não foi confirmado. Use Conferir estado antes de continuar.',true);
      else feedback('A solicitação está em andamento. Aguardando confirmação.');
    }
    async function performAction(action,id,revision,key){
      const retry=state.pending?.kind==='action'&&state.pending.key===key;
      const item=state.items.find(row=>row.id===id);
      if(state.busy||!canManage()||(!retry&&!canDevice(action,item)))return;
      const epoch=state.epoch;state.pending={kind:'action',action,id,revision,key};setBusy(true);state.read++;clearCredentials();clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(path(id,'/actions'),'POST',{expected_revision:revision,idempotency_key:key,action});
        if(!current(epoch))return;
        await acceptOperation(result,id,action);
        setBusy(false);closeDialog(true);
        if(action==='connect'||action==='relink'){state.view='connect';state.id=id;state.method='code';state.tutorial={code:false,qr:false};}
      }catch(error){if(current(epoch)){if(!uncertain(error))state.pending=null;state.stale=Number(error.status)===409;setBusy(false);closeDialog(true);feedback(errorMessage(error),true);}}
      finally{if(current(epoch)){setBusy(false);render();schedule(state.operation?2000:5000);}}
    }
    async function pollOperation(){
      if(!visible()||state.busy||state.loading||!state.operation)return;
      const epoch=state.epoch,op=state.operation;state.loading=true;
      try{
        const result=await request(path(op.device_id,'/operations/'+encodeURIComponent(op.id)));
        if(!current(epoch)||state.operation?.id!==op.id)return;
        await acceptOperation(result,op.device_id,op.action,op.id);
      }catch(error){if(current(epoch)){feedback(errorMessage(error),true);}}
      finally{if(current(epoch)){state.loading=false;render();if(state.operation?.status!=='uncertain')schedule(state.operation?2000:5000);}}
    }
    async function reconcile(){
      if(state.busy)return;
      if(state.operation){await pollOperation();return;}
      if(state.pending?.kind==='settings'){
        const epoch=state.epoch,id=state.id,attempt=state.pending;setBusy(true);
        try{
          const result=settingsDTO(await request(path(id,'/settings')));if(!current(epoch))return;
          const expected=copy(attempt.settings),saved=copy(result.settings);delete saved.proxy.password_configured;
          const secretChange=Object.hasOwn(expected.proxy,'password')||expected.proxy.clear_password===true;
          if(!secretChange&&stable(expected)===stable(saved)){
            state.pending=null;applySettings(result);setBusy(false);renderSettings();feedback('O servidor confirmou que as configurações foram salvas.');
          }else{
            state.pending=null;state.settingsStale=true;feedback('Os valores salvos precisam ser conferidos. Seu formulário foi preservado. Recarregue as configurações antes de salvar novamente.',true);
            host.querySelector('[data-sd-feedback]').insertAdjacentHTML('beforeend','<div class="os-actions">'+button('Recarregar configurações','reload-settings')+'</div>');refreshPending();
          }
        }catch(error){if(current(epoch))settingsError(errorMessage(error));}
        finally{if(current(epoch))setBusy(false);}
        return;
      }
      if(state.view==='connect')await loadDetail();else await loadList();
    }
    async function showQR(){
      const item=device();
      if(state.view!=='connect'||!state.tutorial.qr||!canDevice('qr',item)||state.pair)return;
      const epoch=state.epoch,id=item.id,revision=item.revision,read=++state.read;setBusy(true);clearCredentials();clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(path(id,'/qr'));
        if(!current(epoch,read)||device()?.id!==id||device()?.revision!==revision||!visible())return;
        if(result?.device_id!==id||result.revision!==revision||typeof result.expires_in_ms!=='number'||!Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>45000||typeof result.image!=='string'||result.image.length>240000||!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(result.image))throw Error('invalid_qr_response');
        state.qr={image:result.image,expiresAt:Date.now()+result.expires_in_ms};feedback('');
        expiryTimer=setTimeout(()=>{clearCredentials();if(current(epoch)&&state.id===id){feedback('Este QR Code expirou. Confira o estado antes de mostrar outro.');render();}},result.expires_in_ms);
      }catch(error){if(current(epoch,read)){clearCredentials();feedback(Number(error.status)?errorMessage(error):'Não há um QR Code válido disponível. Atualize o estado da conexão.',true);}}
      finally{if(current(epoch)){setBusy(false);render();schedule();}}
    }
    async function pair(form){
      const item=device();
      if(!form.reportValidity()||!state.tutorial.code||!canDevice('pair',item)||item.status!=='qr_ready'||state.pair)return;
      let phone;try{phone=fields.phoneValue(form.querySelector('[data-sd-field="pair_phone"]').value,{required:true,allowInternational:true}).slice(1);}catch(error){feedback(error.message,true);return;}
      if(!/^[1-9]\d{7,14}$/.test(phone)){feedback('Informe um número completo com DDD.',true);return;}
      const epoch=state.epoch,id=item.id,revision=item.revision,read=++state.read;
      setBusy(true);clearCredentials();clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(path(id,'/pair'),'POST',{expected_revision:revision,phone});
        if(!current(epoch,read)||!visible()||device()?.id!==id||device()?.revision!==revision)return;
        if(result?.device_id!==id||result.revision!==revision||result.status!=='pairing'||typeof result.expires_in_ms!=='number'||!Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>90000)throw Error('invalid_pair_response');
        const code=connectView.normalizeCode(result.code);item.status='pairing';state.pending=null;state.pair={code,expiresAt:Date.now()+result.expires_in_ms};state.pairHidden=false;feedback('');
        expiryTimer=setTimeout(()=>{clearCredentials();if(current(epoch)&&state.id===id){feedback('O código expirou. Confira a conexão antes de gerar outro.');render();void loadDetail();}},result.expires_in_ms);
      }catch(error){if(current(epoch,read)){if(uncertain(error))state.pending={kind:'pair',id};feedback(errorMessage(error),true);}}
      finally{if(current(epoch)){setBusy(false);render();schedule();}}
    }
    function updateDay(node,value){
      node.querySelector('[data-sd-day-enabled]').checked=value.enabled;
      for(const field of node.querySelectorAll('[data-sd-time]')){field.value=value[field.dataset.sdTime]||'';field.disabled=!value.enabled;}
    }
    function change(event){
      const target=event.target;if(!host.contains(target)||state.busy)return;
      if(target.hasAttribute('data-sd-copy')){
        const node=target.closest('[data-sd-schedule]'),row=readDay(node),choice=target.value;
        const indices=choice==='all'?[0,1,2,3,4,5,6]:choice==='weekdays'?[0,1,2,3,4]:choice==='weekend'?[5,6]:/^[0-6]$/.test(choice)?[Number(choice)]:[];
        for(const index of indices){const next=host.querySelector('[data-sd-schedule="'+node.dataset.sdSchedule+'"][data-sd-weekday="'+index+'"]');if(next)updateDay(next,{...row,weekday:index});}
        target.value='';
      }else if(target.hasAttribute('data-sd-day-enabled')){
        for(const field of target.closest('[data-sd-schedule]').querySelectorAll('[data-sd-time]'))field.disabled=!target.checked;
      }else if(target.dataset.sdField&&target.type==='checkbox'){
        for(const section of host.querySelectorAll('[data-sd-dependent="'+target.dataset.sdField+'"]'))section.hidden=!target.checked;
      }else if(target.hasAttribute('data-sd-clear-password')){
        const password=host.querySelector('[data-sd-secret]');password.disabled=target.checked;if(target.checked)password.value='';
      }
      inputChanged(event);
    }
    function inputChanged(event){
      if(!host.contains(event.target))return;
      if(event.target.dataset.sdField==='pair_phone')state.phoneDraft=event.target.value;
      if(state.view==='settings'){
        const message=host.querySelector('[data-sd-save-status]');if(message)message.textContent=isDirty()?'Você tem alterações não salvas.':'As alterações só entram em vigor depois de salvar.';
      }
    }
    async function click(event){
      const target=event.target.closest('[data-sd-action],[data-sd-method]');
      if(!target||target.disabled||!host.contains(target)&&!state.dialog?.element.contains(target))return;
      const action=target.dataset.sdAction,id=target.dataset.sdDevice||state.id;
      if(action==='close-dialog'){closeDialog();return;}
      if(state.busy)return;
      if(target.dataset.sdMethod){
        const item=device(),codeVisible=item?.capabilities.pair===true||!!state.pair;
        if(state.view!=='connect'||!canWrite()||!codeVisible)return;
        state.method=target.dataset.sdMethod==='code'?'code':'qr';render();schedule();return;
      }
      if(action==='refresh'){if(state.pending)await reconcile();else if(state.view==='connect')await loadDetail();else await loadList();return;}
      if(action==='back'){await back();return;}
      if(action==='reconcile'){await reconcile();return;}
      if(action==='retry-pending'){
        const pending=state.pending;if(!pending||state.operation)return;
        if(pending.kind==='create')await createDevice(pending.name,pending.key);
        else if(pending.kind==='action')await performAction(pending.action,pending.id,pending.revision,pending.key);
        return;
      }
      if(action==='open-connect'){await openConnection(id);return;}
      if(action==='settings'){await openSettings(id,target.dataset.sdOpenSection);return;}
      if(action==='reload-settings'){
        if(isDirty())openDialog('reload','Recarregar configurações?','<p>Os valores atuais do servidor substituirão as alterações deste formulário.</p>',{confirm:'Recarregar configurações',id:state.id});
        else await openSettings(state.id);
        return;
      }
      if(action==='alternative-settings'||action==='alternative-relink'){
        const alternate=state.dialog?.id;closeDialog();
        if(action==='alternative-settings')await openSettings(alternate);else confirmAction('relink',alternate);
        return;
      }
      if(action==='create'){
        if(!canWrite())return;
        if(!state.canCreate||state.usage?.used>=state.usage?.limit){feedback('Sua empresa já utiliza o limite de '+state.usage.limit+' dispositivo. Gerencie o dispositivo existente.');return;}
        openDialog('create','Criar dispositivo','<p>Escolha um nome para identificar o WhatsApp da sua empresa.</p>'+input('Nome do dispositivo','new_name','WhatsApp','text','required maxlength="100"'));
        return;
      }
      if(['connect','restart','relink','remove'].includes(action)){confirmAction(action,id);return;}
      if(action==='tutorial-next'){
        if(!canWrite()||!device())return;const mode=state.method==='code'&&(device().capabilities.pair||state.pair)?'code':'qr';state.tutorial[mode]=true;render();if(mode==='qr')await showQR();else host.querySelector('#sd-pair-phone')?.focus();return;
      }
      if(action==='tutorial-back'){if(state.qr||state.pair)return;state.tutorial[state.method]=false;render();return;}
      if(action==='qr'){await showQR();return;}
      if(action==='hide-qr'){clearCredentials();render();return;}
      if(action==='hide-code'){if(!state.pair)return;state.pairHidden=true;render();host.querySelector('[data-sd-action="show-code"]')?.focus({preventScroll:true});return;}
      if(action==='show-code'){if(!state.pair||state.pair.expiresAt<=Date.now()||!visible())return;state.pairHidden=false;render();host.querySelector('[data-sd-action="hide-code"]')?.focus({preventScroll:true});return;}
      if(action==='copy-code'){
        const value=state.pair,epoch=state.epoch;if(!value||state.pairHidden||value.expiresAt<=Date.now()||!visible())return;
        try{await navigator.clipboard.writeText(value.code);if(current(epoch)&&state.pair===value)feedback('Código copiado. Cole no WhatsApp.');}catch{if(current(epoch)&&state.pair===value)feedback('Selecione o código para copiar.',true);}return;
      }
      if(action==='clear-day'){
        const node=target.closest('[data-sd-schedule]');if(!node)return;
        updateDay(node,{enabled:false,start:null,end:null,break_start:null,break_end:null});inputChanged(event);
      }
    }
    async function submit(event){
      const form=event.target.closest('[data-sd-form]');
      if(!form||!host.contains(form)&&!state.dialog?.element.contains(form))return;
      event.preventDefault();if(state.busy)return;
      if(form.dataset.sdForm==='settings'){await saveSettings(form);return;}
      if(form.dataset.sdForm==='pair'){await pair(form);return;}
      const dialog=state.dialog;if(!dialog||!form.reportValidity())return;
      if(dialog.kind==='discard'){state.pending?.kind==='settings'&&(state.pending=null);closeDialog();navigate('list');render();await loadList();return;}
      if(dialog.kind==='reload'){const id=dialog.id;state.pending?.kind==='settings'&&(state.pending=null);closeDialog();await openSettings(id);return;}
      if(dialog.kind==='create'){const name=form.querySelector('[data-sd-field="new_name"]').value.trim();if(!name)return;await createDevice(name,dialog.key);return;}
      if(['connect','restart','relink','remove'].includes(dialog.kind)){
        const item=state.items.find(row=>row.id===dialog.id);
        if(!item||item.revision!==dialog.revision){closeDialog();feedback('O dispositivo mudou. Confira o estado antes de continuar.',true);return;}
        await performAction(dialog.kind,dialog.id,dialog.revision,dialog.key);
      }
    }
    let lastVisible=visible();
    function visibility(){
      const now=visible();if(now===lastVisible)return;lastVisible=now;
      if(!now){pause();if(state.view==='connect')render();return;}
      if(state.view==='settings'&&!state.settings&&!state.busy&&state.id){void openSettings(state.id);return;}
      if(state.view!=='settings'){if(state.operation)void pollOperation();else if(state.view==='connect')void loadDetail();else void loadList(true);}
    }
    function imageError(event){
      const image=event.target;if(image.tagName!=='IMG'||!image.closest('.os-device-avatar'))return;
      image.parentElement.innerHTML=icon('phone');
    }
    host.addEventListener('click',click);host.addEventListener('submit',submit);host.addEventListener('change',change);host.addEventListener('input',inputChanged);host.addEventListener('error',imageError,true);document.addEventListener('visibilitychange',visibility);
    const observer=new MutationObserver(visibility);for(let node=host;node;node=node.parentElement)observer.observe(node,{attributes:true,attributeFilter:['hidden','class','style','aria-hidden']});
    const controller={
      load:()=>state.view==='connect'?loadDetail():loadList(),
      unmount(){if(!state.mounted)return;pause();state.epoch++;state.busy=false;state.mounted=false;closeDialog(true);releaseFields();observer.disconnect();host.removeEventListener('click',click);host.removeEventListener('submit',submit);host.removeEventListener('change',change);host.removeEventListener('input',inputChanged);host.removeEventListener('error',imageError,true);document.removeEventListener('visibilitychange',visibility);host.replaceChildren();host.classList.remove('os-devices');host.removeAttribute('aria-busy');state.settings=null;state.pending=null;state.operation=null;state.items=[];},
      getState:()=>({view:state.view,deviceId:state.id,loaded:state.loaded,busy:state.busy,pending:state.pending?.kind||null,operationStatus:state.operation?.status||null,hasQR:!!state.qr,hasPair:!!state.pair,method:state.method})
    };
    active=controller;render();void loadList();return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionSubscriptionDevices=Object.freeze({mount,unmount});
})();
