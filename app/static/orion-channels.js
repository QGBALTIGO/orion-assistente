/* The company connection uses only the authenticated Orion API. The browser never
 * addresses the bridge or supplies a channel credential for session commands.
 * mount({host,request,tenantId,onStatus?}) -> controller:
 * {load,setTenant,setActive,unmount,getState}. Unmount on logout; setActive(false)
 * when navigating away. GETs never start sessions or enable the assistant.
 */
(function () {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const copy=value=>JSON.parse(JSON.stringify(value));
  const statuses={disabled:'Desativado',stopped:'Desconectado',starting:'Preparando conexão',connecting:'Preparando conexão',reconnecting:'Reconectando',qr_expired:'QR expirou',qr_delayed:'Aguardando QR',qr_ready:'Pronto para conectar',pairing:'Aguardando confirmação no WhatsApp',pair_expired:'Código expirado',pair_failed:'Vínculo não confirmado',connected:'Conectado',disconnected:'Desconectado',logged_out:'Desvinculado',error:'Precisa de atenção'};
  const icon=name=>'<svg class="os-icon qc-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'+({phone:'M22 16.9v3a2 2 0 0 1-2.2 2 20 20 0 0 1-8.7-3.1 20 20 0 0 1-6.2-6.2A20 20 0 0 1 1.8 4 2 2 0 0 1 3.8 2h3a2 2 0 0 1 2 1.7c.2 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L7.7 10a16 16 0 0 0 6.2 6.2l1.3-1.3a2 2 0 0 1 2.1-.5c1 .3 1.9.5 2.9.7a2 2 0 0 1 1.8 1.8',qr:'M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h2v2h4v4h-6v-2m6-6v2'}[name]||'M4 12h16m-6-6 6 6-6 6')+'"/></svg>';
  const button=(label,action,extra='',style='os-outline')=>'<button type="button" class="os-button os-large qc-button '+style+'" data-qc-action="'+esc(action)+'" '+extra+'>'+esc(label)+'</button>';
  const errorText=error=>({401:'Sua sessão precisa ser renovada. Entre novamente no painel.',403:'Somente o proprietário autorizado pode realizar esta ação.',409:'O canal mudou ou a operação não está disponível. Consulte o estado atual antes de continuar.',422:'Confira os dados antes de continuar.',423:'Os canais gerenciados ainda não estão disponíveis neste ambiente.',429:'Aguarde um pouco antes de consultar novamente.'}[Number(error?.status)]||'Não foi possível conferir a resposta. Consulte o estado atual antes de tentar outra ação.');
  let active=null;

  function mount(bridge) {
    unmount();
    const host=typeof bridge?.host==='string'?document.querySelector(bridge.host):bridge?.host;
    if(!host||typeof bridge?.request!=='function')throw Error('A conexão precisa do acesso autenticado ao painel.');
    const state={mounted:true,active:true,epoch:0,read:0,listRead:0,tenantId:bridge.tenantId||null,connection:null,selected:null,canonicalState:'loading',access:false,capabilities:{},loaded:false,loading:false,busy:false,stale:false,unavailable:false,feedback:'',error:false,confirmation:null,pending:null,runtime:null,qr:null,method:'code',pairDraft:'',pair:null,pairHidden:false,pairExpired:false,tutorialDone:{code:false,qr:false}};
    const fieldTools=window.OrionFields,releaseFields=fieldTools.bind(host),connectView=window.OrionConnectView;
    let pollTimer=null,qrTimer=null,pairTimer=null,clockTimer=null,lastHTML=null;
    host.classList.add('qc-root','os-connect');host.setAttribute('data-qc-single','');
    const base=()=>'/api/v1/tenants/'+encodeURIComponent(state.tenantId)+'/qr-channels';
    const selected=()=>state.connection;
    const current=epoch=>state.mounted&&epoch===state.epoch;
    const visible=()=>{if(!state.mounted||!state.active||document.hidden)return false;for(let el=host;el;el=el.parentElement)if(el.hidden||el.getAttribute('aria-hidden')==='true'||el.classList.contains('hidden')||getComputedStyle(el).display==='none')return false;return true;};
    const request=(path,method='GET',data)=>bridge.request(path,method,data);
    function feedback(message,error=false){state.feedback=message;state.error=error;const node=host.querySelector('[data-qc-feedback]');if(node){node.hidden=!message;node.className='os-notice qc-note'+(error?' os-error':'');node.textContent=message;if(error){node.tabIndex=-1;node.focus();}}}
    function clearQR(){state.qr=null;clearTimeout(qrTimer);qrTimer=null;host.querySelector('[data-qc-qr]')?.remove();if(!state.pair){clearTimeout(clockTimer);clockTimer=null;}}
    function clearPair(clearDraft=false){state.pair=null;state.pairHidden=false;state.pairExpired=false;clearTimeout(pairTimer);pairTimer=null;host.querySelector('[data-qc-pair-result],[data-qc-pair-hidden]')?.remove();if(clearDraft){state.pairDraft='';const input=host.querySelector('[data-qc-pair-phone]');if(input)input.value='';}if(!state.qr){clearTimeout(clockTimer);clockTimer=null;}}
    function pause(clearDraft=true){clearTimeout(pollTimer);pollTimer=null;state.read++;clearQR();clearPair(clearDraft);if(clearDraft)state.tutorialDone={code:false,qr:false};}
    function allowed(action){
      const item=selected();if(!state.access||!item?.can_manage)return false;
      if(item.legacy_transport&&['stop','enable','disable'].includes(action))return false;
      if(action==='status')return state.capabilities.status!==false;
      const key=['assistant_on','assistant_off','assistant'].includes(action)?'change_assistant':['enable','disable'].includes(action)?'change_state':action;
      return state.capabilities[key]===true&&(action!=='pair'||state.runtime?.capabilities?.pair===true);
    }
    function canWrite(){return visible()&&state.loaded&&!state.loading&&state.access&&!state.busy&&!state.pending&&!state.stale&&state.canonicalState!=='ambiguous';}
    function canItemWrite(){return canWrite()&&selected()?.can_manage===true;}
    function channel(row){
      if(!row||!uuid(row.id)||!uuid(row.owner_account_id)||row.tenant_id!==state.tenantId||row.provider!=='baileys'||typeof row.enabled!=='boolean'||typeof row.assistant_enabled!=='boolean'||!Number.isSafeInteger(row.revision)||row.revision<1)throw Error('invalid_connection_response');
      return {id:row.id,tenant_id:row.tenant_id,owner_account_id:row.owner_account_id,provider:'baileys',enabled:row.enabled,assistant_enabled:row.assistant_enabled,revision:row.revision,can_manage:row.can_manage===true,legacy_transport:row.legacy_transport===true,assistant_activation_allowed:row.assistant_activation_allowed===true};
    }
    function registry(result){
      if(!result||!['ready','missing','ambiguous'].includes(result.canonical_state)||!Array.isArray(result.items)||result.items.length>1||!Object.hasOwn(result,'connection'))throw Error('invalid_canonical_response');
      const item=result.connection===null?null:channel(result.connection);
      if(result.canonical_state==='ready'?(!item||result.canonical_id!==item.id||result.items.length!==1||result.items[0]?.id!==item.id):(item!==null||result.items.length!==0||result.canonical_id!==null))throw Error('invalid_canonical_identity');
      return {item,canonicalState:result.canonical_state,access:result.access?.can_manage===true,capabilities:result.capabilities||{}};
    }
    function applyRegistry(result){
      const next=registry(result),same=state.selected===next.item?.id&&selected()?.revision===next.item?.revision;
      if(!same){pause();state.runtime=null;state.method='code';}
      state.connection=next.item;state.selected=next.item?.id||null;state.canonicalState=next.canonicalState;state.access=next.access;state.capabilities=next.capabilities;state.loaded=true;state.stale=false;state.unavailable=false;
      if(!state.access||!next.item?.can_manage){state.runtime=null;clearPair(true);clearQR();}
      return next.item;
    }
    function update(row){
      const previous=selected(),safe=channel({...previous,...row});
      if(!previous||safe.id!==previous.id)throw Error('foreign_connection_response');
      state.connection=safe;return safe;
    }
    function transport(value,id){
      if(!value||value.channel_id!==id||!Number.isSafeInteger(value.revision)||value.revision<1||!Object.hasOwn(statuses,value.status))throw Error('invalid_connection_status');
      const verified=value.status==='connected'&&value.phone_verified===true&&typeof value.phone==='string'&&/^[1-9]\d{7,14}$/.test(value.phone);
      return {channel_id:id,revision:value.revision,status:value.status,capabilities:{pair:value.capabilities?.pair===true},phone:verified?value.phone:null,phone_verified:verified};
    }
    function codeAvailable(){return selected()?.enabled&&allowed('pair')&&state.runtime?.status!=='connected';}
    function codeVisible(){return codeAvailable()||!!state.pair||state.pending?.kind==='pair';}
    function method(){return state.method==='code'&&codeVisible()?'code':'qr';}
    function pairReady(){return canItemWrite()&&codeAvailable()&&state.runtime?.status==='qr_ready'&&!state.pair;}
    function qrReady(){return selected()?.enabled&&allowed('qr')&&state.runtime?.status==='qr_ready'&&!state.pair;}
    function startReady(){return !selected()?.enabled||!!state.runtime&&['disabled','stopped','disconnected','error'].includes(state.runtime.status);}
    function preparationReady(mode){return canItemWrite()&&selected()?.legacy_transport===true&&allowed(mode==='recover'?'recover':'prepare')&&!!state.runtime&&(mode==='recover'?['logged_out','pair_failed'].includes(state.runtime.status):(['qr_expired','qr_delayed','pair_expired','stopped'].includes(state.runtime.status)||(state.runtime.status==='qr_ready'&&state.pairExpired)));}
    function connectReady(){return canWrite()&&(state.canonicalState==='missing'?state.capabilities.provision===true:!!selected()&&!selected().legacy_transport&&allowed('start')&&startReady());}
    function capturePair(){const input=host.querySelector('[data-qc-pair-phone]');if(input)state.pairDraft=input.value;}
    function updateClocks(){clearTimeout(clockTimer);clockTimer=null;for(const [name,value] of [['qr',state.qr],['pair',state.pair]]){const node=host.querySelector('[data-qc-'+name+'-time]');if(node&&value)node.textContent=connectView.seconds(value.expiresAt-Date.now());}if(visible()&&(state.qr||state.pair))clockTimer=setTimeout(updateClocks,1000);}
    function confirmationHTML(){
      const c=state.confirmation;if(!c)return '';
      const texts={connect:['Conectar WhatsApp?','O Orion vai preparar a conexão desta empresa. QR Code e Código vinculam o mesmo número. Conectar não ativa a assistente.'],stop:['Parar a conexão?','Interrompe a conexão no Orion sem apagar os dados nem desvincular o aparelho no WhatsApp.'],assistant_on:['Ativar o atendimento automático?','A assistente atenderá somente os contatos permitidos pela política da empresa.'],prepare:['Preparar uma nova tentativa?','A conexão será preparada mantendo a sessão existente. O código anterior deixará de funcionar.'],recover:['Refazer vínculo?','O WhatsApp encerrou o vínculo anterior. Prepare uma nova conexão somente para esta empresa.'],assistant_off:['Desativar o atendimento automático?','Interrompe novas respostas automáticas. A conexão e o histórico permanecem.']},[title,description]=texts[c.kind];
      return '<section class="os-card qc-card"><form class="os-form qc-confirm" data-qc-form="confirm"><h3>'+title+'</h3><p>'+description+'</p>'+(c.kind==='assistant_on'?'<label class="os-check"><input type="checkbox" name="authorize_assistant" required><span>Autorizo o atendimento conforme a política da empresa.</span></label>':'')+'<div class="os-actions qc-actions">'+button('Voltar','cancel-confirm')+'<button type="submit" class="os-button os-large">Confirmar</button></div></form></section>';
    }
    function assistantHTML(){
      const item=selected();if(!item)return '';
      const permitted=item.assistant_activation_allowed===true,enabled=item.assistant_enabled;
      return '<section class="os-card os-section-gap qc-card" data-qc-assistant><div class="os-card-header"><h2>Atendimento automático</h2><span class="os-badge '+(enabled?'os-success':'os-neutral')+'">'+(enabled?'Ativado':'Desativado')+'</span></div><p>Conectar o WhatsApp não ativa a assistente.</p>'+(!enabled&&!permitted?'<p class="os-help" data-qc-assistant-reason>As permissões de atendimento ainda não estão disponíveis. A conexão pode ser usada com a assistente desativada.</p>':'')+(state.access&&item.can_manage?'<div class="os-actions qc-actions">'+button(enabled?'Desativar assistente':'Ativar assistente',enabled?'assistant_off':'assistant_on',!canItemWrite()||!allowed('assistant')||!item.enabled||(!enabled&&!permitted)?'disabled':'')+'</div>':'')+'<details class="os-more-fields" data-qc-disclosure="about"><summary><span class="os-disclosure-label">Sobre a conexão</span></summary><div class="os-disclosure-body"><p class="os-help">O atendimento automático segue a política privada da empresa.</p>'+(state.access&&item.can_manage&&!item.legacy_transport?'<div class="os-actions">'+button('Parar conexão','stop',!canItemWrite()||!allowed('stop')||!item.enabled?'disabled':'','os-danger')+'</div>':'')+'</div></details></section>';
    }
    function connectionHTML(item,runtime){
      if(!item.enabled||runtime?.status==='connected'||!item.can_manage||!state.access||state.confirmation||(!allowed('qr')&&!allowed('pair')))return '';
      const mode=method(),locked=state.busy||!!state.pending||state.stale;
      const methods=codeVisible()?'<div class="method-selector wizard-methods qc-methods" role="group" aria-label="Escolha a forma de conexão">'+connectView.methods(mode,'data-qc-method',locked)+'</div>':'';
      const ready=state.tutorialDone[mode]||(mode==='code'&&!!state.pair)||(mode==='qr'&&!!state.qr);
      if(!ready)return '<section class="qc-connection wizard-shell" data-qc-connection>'+connectView.heading(codeVisible())+methods+'<div class="wizard-card"><section class="wizard-pane" data-qc-tutorial><div class="wizard-tutorial-heading"><h3>Prepare o WhatsApp no seu celular</h3><p>Siga as orientações e continue quando estiver pronto.</p></div>'+connectView.instructions(mode)+button('Concluí os passos e estou pronto para prosseguir','tutorial-next',locked?'disabled':'','wizard-primary os-full wizard-continue')+'</section></div></section>';
      let content='';
      if(mode==='code'){
        if(state.pair&&state.pairHidden){
          content='<section data-qc-pair-hidden><h3>Código oculto</h3><p>O código continua válido até expirar.</p><p class="wizard-qr-time qc-countdown">Expira em <strong data-qc-pair-time>—</strong></p><div class="os-actions qc-actions">'+button('Mostrar código','show-code',locked?'disabled':'','')+'</div></section>';
        }else if(state.pair){
          content='<section class="wizard-code-result" data-qc-pair-result><h3>Digite este código no WhatsApp</h3><output class="code-box" data-qc-pair-code>'+esc(state.pair.code)+'</output><p class="wizard-qr-time qc-countdown">Expira em <strong data-qc-pair-time>—</strong></p><div class="os-actions qc-actions">'+button('Copiar código','copy-code',locked?'disabled':'','')+button('Ocultar código','cancel-pair',locked?'disabled':'')+'</div><p class="os-help">A conexão será confirmada pelo WhatsApp.</p></section>';
        }else{
          const waiting=state.pairExpired||runtime?.status==='pair_expired'?'O código expirou. Solicite outro quando a conexão estiver pronta.':runtime?.status==='connecting'?'Preparando a conexão. Aguarde para gerar o código.':runtime?.status==='pairing'?'Um código já foi solicitado. Confira o WhatsApp ou aguarde a expiração.':runtime?.status==='pair_failed'?'O vínculo não foi confirmado. Aguarde a expiração e consulte o estado.':runtime?.status==='logged_out'?'Sessão desvinculada. Atualize o estado para revisar a conexão.':'';
          content='<h3>Informe o número do WhatsApp</h3><p class="wizard-explain">O mesmo número que está no aparelho a conectar, com DDI e DDD.</p><form class="os-form qc-form" data-qc-form="pair"><div class="os-field"><label for="qc-pair-phone">Número do WhatsApp</label><input class="qc-pair-input" id="qc-pair-phone" data-qc-pair-phone name="phone" type="tel" data-orion-field="phone" data-orion-international="true" inputmode="tel" autocomplete="tel" required aria-describedby="qc-pair-help" placeholder="(00) 00000-0000" value="'+esc(state.pairDraft)+'" '+(locked?'disabled':'')+'><small id="qc-pair-help" class="os-help">Use o DDD. Para outro país, inclua + e o código do país.</small></div>'+(waiting?'<p class="os-notice qc-note" role="status">'+waiting+'</p>':'')+'<div class="os-actions qc-actions"><button type="submit" class="os-button os-large os-full qc-button" '+(!pairReady()?'disabled':'')+'>'+(state.busy?'Gerando código…':'Gerar código')+'</button></div></form>';
        }
      }else{
        content=state.qr?'<section class="qc-qr" data-qc-qr><h3>Escaneie com o WhatsApp</h3><div class="wizard-qr-wrap"><img src="'+esc(state.qr.image)+'" alt="QR temporário para vincular '+'WhatsApp da empresa'+'" width="240" height="240"></div><p class="wizard-qr-time qc-countdown">Expira em <strong data-qc-qr-time>—</strong></p><div class="os-actions qc-actions">'+button('Ocultar QR','hide-qr')+'</div></section>':'<h3>Conectar por QR Code</h3><p>'+(state.pair||['pairing','pair_failed'].includes(runtime?.status)?'Há um código em andamento. Aguarde a confirmação ou a expiração.':'Use o WhatsApp de outro aparelho para escanear.')+'</p><div class="os-actions qc-actions">'+button('Mostrar QR','qr',locked||!qrReady()?'disabled':'','')+'</div>';
      }
      return '<section class="qc-connection wizard-shell" data-qc-connection>'+connectView.heading(codeVisible())+methods+'<div class="wizard-card"><section class="wizard-pane" data-qc-final>'+(mode==='qr'&&runtime?.status==='logged_out'?'<p class="os-notice qc-note" role="status">Sessão desvinculada. Atualize o estado para revisar a conexão.</p>':'')+content+(!state.pair&&!state.qr?'<div class="wizard-steps-actions">'+button('Voltar','tutorial-back',locked?'disabled':'')+'</div>':'')+'</section></div></section>';
    }
    let renderedChannel=null;
    function paint(html){
      if(lastHTML===html){updateClocks();return;}
      const sameChannel=renderedChannel===state.selected,focused=document.activeElement;
      const focus=sameChannel&&host.contains(focused)?{id:focused.id,action:focused.dataset.qcAction,feedback:focused.hasAttribute('data-qc-feedback'),start:focused.selectionStart,end:focused.selectionEnd,direction:focused.selectionDirection}:null;
      const disclosures=sameChannel?[...host.querySelectorAll('details[data-qc-disclosure]')].map(node=>[node.dataset.qcDisclosure,node.open]):[];
      const oldQR=sameChannel&&state.qr?host.querySelector('[data-qc-qr]'):null;
      const oldPair=sameChannel&&state.pair?host.querySelector('[data-qc-pair-result]'):null;
      host.innerHTML=html;lastHTML=html;renderedChannel=state.selected;
      // A status change must not reload a still-valid QR or discard the code.
      if(oldQR&&oldQR.querySelector('img')?.getAttribute('src')===state.qr?.image)host.querySelector('[data-qc-qr]')?.replaceWith(oldQR);
      if(oldPair&&oldPair.querySelector('[data-qc-pair-code]')?.textContent===state.pair?.code)host.querySelector('[data-qc-pair-result]')?.replaceWith(oldPair);
      for(const [name,open] of disclosures){const detail=host.querySelector('[data-qc-disclosure="'+name+'"]');if(detail)detail.open=open;}
      fieldTools.refresh(host);
      if(state.busy)for(const el of host.querySelectorAll('button,input,textarea'))el.disabled=true;
      if(focus){const target=focus.id?document.getElementById(focus.id):focus.action?host.querySelector('[data-qc-action="'+focus.action+'"]'):focus.feedback?host.querySelector('[data-qc-feedback]'):null;
        if(target&&host.contains(target)&&!target.disabled){if(focus.feedback)target.tabIndex=-1;target.focus({preventScroll:true});if(typeof focus.start==='number'&&typeof target.setSelectionRange==='function')try{target.setSelectionRange(focus.start,focus.end,focus.direction);}catch{}}}
      updateClocks();
    }
    function render(){
      if(!state.mounted)return;if(renderedChannel===state.selected)capturePair();host.setAttribute('aria-busy',String(state.busy||state.loading));host.dataset.qcCanonicalState=state.canonicalState;
      const item=selected(),runtime=state.access&&item?.can_manage?state.runtime:null,tag=host.id==='osManagedChannels'?'h2':'h1';
      const label=!state.loaded?'Consultando conexão':state.canonicalState==='ambiguous'?'Revisão necessária':runtime?statuses[runtime.status]:item?.enabled?'Estado não confirmado':'Não conectado';
      const phone=runtime?.status==='connected'&&runtime.phone_verified?'<p data-qc-number>'+esc(fieldTools.phoneDisplay('+'+runtime.phone,{allowInternational:true}))+'</p>':'';
      const header='<header class="os-title-row qc-heading"><div><'+tag+' data-qc-title tabindex="-1">WhatsApp da empresa</'+tag+'>'+phone+'<p><span class="os-badge '+(runtime?.status==='connected'?'os-success':'os-neutral')+'" data-qc-status>'+esc(label)+'</span></p></div><div class="os-actions qc-header-actions">'+button('Atualizar estado','refresh',state.busy||state.loading?'disabled':'')+'</div></header>';
      const note='<div data-qc-feedback role="alert" class="os-notice qc-note'+(state.error?' os-error':'')+'"'+(!state.feedback?' hidden':'')+'>'+esc(state.feedback)+'</div>';
      let body='';
      if(state.unavailable)body='<section class="os-card"><h2>Conexão indisponível</h2><p>Não foi possível consultar a conexão desta empresa.</p></section>';
      else if(!state.loaded)body='<p class="os-help">Consultando a conexão…</p>';
      else if(state.canonicalState==='ambiguous')body='<section class="os-notice os-error" data-qc-ambiguous><p>A conexão da empresa precisa de revisão. Atualize o estado antes de continuar.</p></section>';
      else if(state.confirmation)body=confirmationHTML();
      else{
        if(state.pending)body+='<div class="os-notice os-error qc-note"><div><strong>Resultado ainda não confirmado.</strong><p>Consulte o estado antes de repetir uma ação.</p><div class="os-actions qc-actions">'+button('Conferir estado','refresh')+(state.pending.kind==='provision'?button('Retomar conexão','retry-provision'):'')+'</div></div></div>';
        if(!item)body+='<section class="os-card" data-qc-empty><h2>Conecte o WhatsApp da sua empresa</h2><p>Prepare a conexão para vincular o número usado pela empresa.</p>'+(state.access?'<div class="os-actions qc-actions">'+button('Conectar WhatsApp','connect',!connectReady()?'disabled':'','')+'</div>':'<p class="os-help">Somente o proprietário autorizado pode conectar o WhatsApp.</p>')+'</section>';
        else if(!state.access||!item.can_manage)body+='<p class="os-notice">Somente o proprietário autorizado pode gerenciar esta conexão.</p>';
        else{
          if(preparationReady('recover'))body+='<div class="os-notice qc-note"><div><p>O WhatsApp encerrou o vínculo anterior. Uma nova conexão exige sua confirmação.</p><div class="os-actions qc-actions">'+button('Refazer vínculo','recover','','')+'</div></div></div>';
          else if(preparationReady('reconnect'))body+='<div class="os-notice qc-note"><div><p>A conexão precisa ser preparada para gerar o código. Use Preparar novo código.</p><div class="os-actions qc-actions">'+button('Preparar novo código','prepare','','')+'</div></div></div>';
          if(connectReady())body+='<div class="os-actions qc-actions">'+button('Conectar WhatsApp','connect','','')+'</div>';
          if(item.enabled&&runtime?.status!=='connected'){
            const widget=connectionHTML(item,runtime);body+=widget||'<p class="os-notice">A conexão ainda não está pronta. Atualize o estado para conferir a disponibilidade.</p>';
          }
          body+=assistantHTML();
        }
      }
      paint(header+note+body);
    }
    function schedule(delay=5000){clearTimeout(pollTimer);pollTimer=null;if(!visible()||!selected()?.enabled||!allowed('status')||state.busy||state.loading||(state.pending&&state.pending.kind!=='pair')||state.stale||state.confirmation)return;pollTimer=setTimeout(()=>{pollTimer=null;void loadStatus();},delay);}
    function reconcilePending(){
      const pending=state.pending,item=selected();if(!pending||!item)return;
      const confirmed=pending.kind==='provision'||(pending.kind==='enable'&&item.enabled&&item.revision>pending.data.expected_revision)||(pending.kind==='assistant'&&item.revision>pending.data.expected_revision&&item.assistant_enabled===pending.data.assistant_enabled);
      if(confirmed){state.pending=null;state.feedback='Conexão conferida. Continue pela ação disponível.';state.error=false;}
    }
    async function loadStatus(){
      if(!visible()||state.busy||state.stale||!selected()||!allowed('status'))return;
      const epoch=state.epoch,id=state.selected,revision=selected().revision,read=++state.read;clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(base()+'/'+encodeURIComponent(id)+'/status');
        if(!current(epoch)||!visible()||read!==state.read||id!==state.selected||selected()?.revision!==revision)return;
        const checked=transport(result,id);
        if(checked.revision!==revision){state.confirmation=null;state.runtime=null;state.stale=true;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();feedback('A conexão mudou. Atualize o estado e revise a ação.',true);render();return;}
        let changed=JSON.stringify(state.runtime)!==JSON.stringify(checked);state.runtime=checked;
        if(checked.status!=='qr_ready'&&state.qr){clearQR();changed=true;}
        if(checked.status!=='pairing'&&state.pair){clearPair();changed=true;}
        const pending=state.pending;
        if(pending&&((pending.kind==='pair'&&!['pairing','pair_failed','pair_expired','connecting'].includes(checked.status))||(['start','prepare','recover'].includes(pending.kind)&&['connecting','qr_ready','pairing','pair_expired','pair_failed','connected'].includes(checked.status))||(pending.kind==='stop'&&['stopped','disabled'].includes(checked.status)))){state.pending=null;state.feedback='Estado da conexão confirmado.';state.error=false;changed=true;}
        if(changed)render();else updateClocks();try{await bridge.onStatus?.(copy(state.runtime));}catch{}schedule();
      }catch(error){if(current(epoch)&&visible()&&read===state.read&&id===state.selected){state.runtime=null;clearQR();clearPair();if([401,403].includes(Number(error.status)))state.access=false;feedback(errorText(error),true);render();schedule(15000);}}
    }
    async function load(){
      if(!visible()||state.loading||state.busy)return;const epoch=state.epoch,read=++state.listRead;state.loading=true;state.read++;clearTimeout(pollTimer);pollTimer=null;capturePair();
      try{const result=await request(base());if(!current(epoch)||!visible()||read!==state.listRead)return;applyRegistry(result);state.feedback='';state.error=false;state.confirmation=null;reconcilePending();render();if(selected()?.enabled)void loadStatus();}
      catch(error){if(current(epoch)&&visible()&&read===state.listRead){state.unavailable=true;state.access=false;state.runtime=null;clearQR();clearPair();state.feedback=errorText(error);state.error=true;render();}}
      finally{if(current(epoch)){state.loading=false;if(visible())render();schedule();}}
    }
    async function showQR(){
      if(!visible()||state.busy||state.pending||!state.tutorialDone.qr||!qrReady())return;
      clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();state.method='qr';const epoch=state.epoch,id=state.selected,revision=selected().revision,read=++state.read;
      try{const result=await request(base()+'/'+encodeURIComponent(id)+'/qr');if(!current(epoch)||!visible()||read!==state.read||id!==state.selected||selected()?.revision!==revision)return;
        if(result?.channel_id!==id||result.revision!==revision||typeof result.expires_in_ms!=='number'||!Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>45000||typeof result.image!=='string'||result.image.length>350000||!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(result.image))throw Error('invalid_connection_qr');
        state.qr={image:result.image,channel_id:id,expiresAt:Date.now()+result.expires_in_ms};render();qrTimer=setTimeout(()=>{clearQR();if(current(epoch)&&visible()&&id===state.selected){feedback('Este QR expirou. Atualize o estado da conexão.');render();}},result.expires_in_ms);schedule();
      }catch(error){if(current(epoch)&&visible()&&read===state.read){clearQR();feedback(Number(error.status)?errorText(error):'Não há um QR válido disponível. Atualize o estado.',true);render();schedule();}}
    }
    async function generatePair(raw){
      if(!state.tutorialDone.code||!pairReady())return;
      let phone;try{phone=fieldTools.phoneValue(raw,{required:true,allowInternational:true}).slice(1);}catch(error){feedback(error.message||'Informe um número completo com DDD.',true);return;}
      if(!/^[1-9]\d{7,14}$/.test(phone)){feedback('Informe um número completo com DDD.',true);return;}
      capturePair();const epoch=state.epoch,id=state.selected,revision=selected().revision;
      const operation={kind:'pair',id,path:base()+'/'+encodeURIComponent(id)+'/pair',method:'POST',data:{expected_revision:revision,phone}};
      state.busy=true;const read=++state.read;state.listRead++;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();state.feedback='';state.error=false;render();
      try{
        const result=await request(operation.path,operation.method,operation.data);
        if(!current(epoch)||!visible()||read!==state.read||id!==state.selected||selected()?.revision!==revision)return;
        const checked=transport(result,id),code=connectView.normalizeCode(result.code);
        if(checked.revision!==revision||checked.status!=='pairing'||typeof result.expires_in_ms!=='number'||!Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>90000)throw Error('invalid_pair_response');
        checked.capabilities=state.runtime?.capabilities||{pair:false};state.runtime=checked;
        state.pending=null;state.pair={code,channel_id:id,expiresAt:Date.now()+result.expires_in_ms};state.feedback='';state.error=false;
        pairTimer=setTimeout(()=>{clearPair();if(current(epoch)&&visible()&&id===state.selected){state.pairExpired=true;render();void loadStatus();}},result.expires_in_ms);
      }catch(error){
        if(current(epoch)&&visible()&&read===state.read&&id===state.selected){const status=Number(error.status);state.pending=(!status||status>=500)?{kind:'pair',id}:null;if(status===403)state.access=false;if([404,423].includes(status))state.unavailable=true;if(status===409)state.stale=true;state.feedback=errorText(error);state.error=true;}
      }finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}
    }
    async function performConnect(key){
      const retry=state.pending?.kind==='provision'&&state.pending.data.idempotency_key===key;
      if(!visible()||state.busy||(!retry&&!connectReady())||(retry&&(!state.access||state.canonicalState!=='missing'||state.capabilities.provision!==true)))return;
      const epoch=state.epoch;state.busy=true;state.read++;state.listRead++;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();state.confirmation=null;render();let operation=null;
      try{
        let item=selected();
        if(!item){
          operation={kind:'provision',path:base()+'/provision',method:'POST',data:{idempotency_key:key}};
          const result=await request(operation.path,operation.method,operation.data);if(!current(epoch))return;
          item=applyRegistry(result);if(!item||state.canonicalState!=='ready'||!state.access||!item.can_manage)throw Error('invalid_provision_response');state.pending=null;if(!visible())return;
        }
        if(item.legacy_transport||!allowed('start'))throw Error('connection_not_available');
        if(!item.enabled){
          if(!allowed('enable'))throw Error('connection_not_available');
          operation={kind:'enable',id:item.id,path:base()+'/'+encodeURIComponent(item.id),method:'PATCH',data:{expected_revision:item.revision,enabled:true}};
          const result=await request(operation.path,operation.method,operation.data);if(!current(epoch))return;
          item=update(result);if(!item.enabled||item.revision!==operation.data.expected_revision+1)throw Error('invalid_enable_response');state.runtime=null;state.pending=null;if(!visible())return;
        }
        operation={kind:'start',id:item.id,path:base()+'/'+encodeURIComponent(item.id)+'/start',method:'POST',data:{expected_revision:item.revision}};
        const result=await request(operation.path,operation.method,operation.data);if(!current(epoch))return;
        const checked=transport(result,item.id);if(checked.revision!==item.revision)throw Error('invalid_start_revision');state.runtime=checked;state.pending=null;state.feedback='Conexão preparada. Escolha como vincular seu WhatsApp.';state.error=false;
      }catch(error){if(current(epoch)){const status=Number(error.status);state.pending=(!status||status>=500)?operation:null;if(status===403)state.access=false;if(status===409)state.stale=true;state.feedback=errorText(error);state.error=true;}}
      finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}
    }
    async function performPreparation(mode){
      if(!preparationReady(mode))return;const epoch=state.epoch,id=state.selected,revision=selected().revision;state.busy=true;state.read++;state.listRead++;clearTimeout(pollTimer);pollTimer=null;state.confirmation=null;render();let operation=null;
      try{
        const status=await request(base()+'/'+encodeURIComponent(id)+'/status');if(!current(epoch)||!visible()||id!==state.selected)return;
        const checked=transport(status,id);if(checked.revision!==revision)throw Object.assign(Error('stale_connection'),{status:409});state.runtime=checked;
        if(mode==='reconnect'&&checked.status==='qr_ready'&&!state.pairExpired){state.feedback='A conexão já está pronta.';state.error=false;return;}
        const eligible=mode==='recover'?['logged_out','pair_failed'].includes(checked.status):['qr_ready','qr_expired','qr_delayed','pair_expired','stopped'].includes(checked.status);
        if(!eligible){state.feedback='O estado mudou. Confira a conexão antes de continuar.';state.error=false;return;}
        clearQR();clearPair();operation={kind:mode==='recover'?'recover':'prepare',id,path:base()+'/'+encodeURIComponent(id)+'/prepare',method:'POST',data:{expected_revision:revision,mode}};
        const result=await request(operation.path,operation.method,operation.data);if(!current(epoch)||!visible()||id!==state.selected)return;
        const runtime=transport(result,id);if(runtime.revision!==revision)throw Error('invalid_preparation_revision');state.runtime=runtime;state.pending=null;state.feedback='Preparando a conexão. Aguarde a disponibilidade.';state.error=false;
      }catch(error){if(current(epoch)){const status=Number(error.status);state.pending=operation&&(!status||status>=500)?operation:null;if(!operation)state.runtime=null;if(status===403)state.access=false;if(status===409)state.stale=true;state.feedback=errorText(error);state.error=true;}}
      finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}
    }
    async function perform(operation){
      if(!canItemWrite())return;const epoch=state.epoch,id=state.selected;state.busy=true;state.read++;state.listRead++;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();state.confirmation=null;render();
      try{const result=await request(operation.path,operation.method,operation.data);if(!current(epoch)||id!==state.selected)return;
        if(operation.method==='PATCH'){const item=update(result);if(item.revision!==operation.data.expected_revision+1)throw Error('invalid_state_revision');state.runtime=null;}
        else{const checked=transport(result,id);if(checked.revision!==selected().revision)throw Error('invalid_command_revision');state.runtime=checked;}
        state.pending=null;state.feedback='O servidor confirmou a alteração.';state.error=false;
      }catch(error){if(current(epoch)){const status=Number(error.status);state.pending=(!status||status>=500)?operation:null;if(status===403)state.access=false;if(status===409)state.stale=true;state.feedback=errorText(error);state.error=true;}}
      finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}
    }
    async function submit(event){
      const form=event.target.closest('[data-qc-form]');if(!form||!host.contains(form))return;event.preventDefault();if(!canWrite()||!form.reportValidity())return;
      if(form.dataset.qcForm==='pair'){await generatePair(form.elements.phone.value);return;}
      if(form.dataset.qcForm!=='confirm')return;
      const c=state.confirmation,item=selected();if(!c||c.id!==(item?.id||null)||c.revision!==(item?.revision||null)){state.confirmation=null;feedback('A conexão mudou. Atualize o estado e revise a ação.',true);render();return;}
      if(c.kind==='connect'){if(connectReady())await performConnect(c.key);return;}
      if(c.kind==='prepare'||c.kind==='recover'){await performPreparation(c.kind==='recover'?'recover':'reconnect');return;}
      if(!item||!canItemWrite()||!allowed(c.kind))return;
      if(c.kind==='assistant_on'&&(!item.assistant_activation_allowed||!form.elements.authorize_assistant.checked))return;
      if(c.kind==='stop'){await perform({kind:'stop',id:item.id,path:base()+'/'+encodeURIComponent(item.id)+'/stop',method:'POST',data:{expected_revision:item.revision}});return;}
      await perform({kind:'assistant',id:item.id,path:base()+'/'+encodeURIComponent(item.id),method:'PATCH',data:{expected_revision:item.revision,assistant_enabled:c.kind==='assistant_on'}});
    }
    async function click(event){
      const el=event.target.closest('[data-qc-action],[data-qc-method]');if(!el||!host.contains(el)||el.disabled||state.busy)return;const action=el.dataset.qcAction;
      if(el.dataset.qcMethod){if(!canItemWrite()||!codeVisible())return;capturePair();state.method=el.dataset.qcMethod==='code'?'code':'qr';state.read++;render();schedule();return;}
      if(action==='refresh'){await load();return;}
      if(action==='retry-provision'){if(state.pending?.kind==='provision')await performConnect(state.pending.data.idempotency_key);return;}
      if(action==='cancel-confirm'){state.confirmation=null;render();schedule();return;}
      if(action==='tutorial-next'){if(!canItemWrite())return;state.tutorialDone[method()]=true;render();if(method()==='qr'&&qrReady())await showQR();else host.querySelector('[data-qc-pair-phone]')?.focus();return;}
      if(action==='tutorial-back'){if(!canItemWrite()||state.pair||state.qr)return;capturePair();state.tutorialDone[method()]=false;render();return;}
      if(action==='qr'){await showQR();return;}
      if(action==='hide-qr'){clearQR();render();return;}
      if(action==='cancel-pair'){if(!state.pair)return;state.pairHidden=true;render();host.querySelector('[data-qc-action=show-code]')?.focus({preventScroll:true});return;}
      if(action==='show-code'){if(!state.pair||state.pair.expiresAt<=Date.now()||!visible())return;state.pairHidden=false;render();host.querySelector('[data-qc-action=cancel-pair]')?.focus({preventScroll:true});return;}
      if(action==='copy-code'){const pair=state.pair,epoch=state.epoch;if(!pair||state.pairHidden||pair.expiresAt<=Date.now()||!visible())return;try{await navigator.clipboard.writeText(pair.code);if(current(epoch)&&visible()&&state.pair===pair)feedback('Código copiado. Cole no WhatsApp.');}catch{if(current(epoch)&&visible()&&state.pair===pair)feedback('Selecione o código para copiar.',true);}return;}
      if(!canWrite())return;
      if(action==='connect'){if(!connectReady())return;state.confirmation={kind:'connect',id:selected()?.id||null,revision:selected()?.revision||null,key:crypto.randomUUID()};}
      else if(action==='prepare'||action==='recover'){if(!preparationReady(action==='recover'?'recover':'reconnect'))return;state.confirmation={kind:action,id:selected().id,revision:selected().revision};}
      else if(['assistant_on','assistant_off','stop'].includes(action)){const item=selected();if(!item||!canItemWrite()||!allowed(action)||!item.enabled||action==='assistant_on'&&!item.assistant_activation_allowed)return;state.confirmation={kind:action,id:item.id,revision:item.revision};}
      else return;
      state.read++;clearTimeout(pollTimer);pollTimer=null;if(action!=='prepare'&&action!=='recover'){clearQR();clearPair();}render();host.querySelector('.qc-confirm h3')?.setAttribute('tabindex','-1');host.querySelector('.qc-confirm h3')?.focus();
    }
    function input(event){if(host.contains(event.target)&&event.target.matches('[data-qc-pair-phone]'))capturePair();}
    function setActive(value){state.active=value===true;if(!state.active){pause();return;}if(visible())void load();}
    function setTenant(id){pause();state.epoch++;state.listRead++;state.tenantId=id;state.connection=null;state.selected=null;state.canonicalState='loading';state.runtime=null;state.loaded=false;state.loading=false;state.busy=false;state.stale=false;state.access=false;state.capabilities={};state.confirmation=null;state.pending=null;state.feedback='';state.error=false;state.unavailable=false;state.method='code';if(!uuid(id)){feedback('Selecione uma empresa autorizada.');render();return Promise.resolve();}render();return visible()?load():Promise.resolve();}
    function visibility(){if(!visible())pause();else void load();}
    host.addEventListener('click',click);host.addEventListener('submit',submit);host.addEventListener('input',input);host.addEventListener('change',input);document.addEventListener('visibilitychange',visibility);
    const observer=new MutationObserver(visibility);for(let el=host;el;el=el.parentElement)observer.observe(el,{attributes:true,attributeFilter:['hidden','class','style','aria-hidden']});
    const controller={load,setTenant,setActive,unmount(){if(!state.mounted)return;pause();releaseFields();state.mounted=false;state.epoch++;state.listRead++;observer.disconnect();host.removeEventListener('click',click);host.removeEventListener('submit',submit);host.removeEventListener('input',input);host.removeEventListener('change',input);document.removeEventListener('visibilitychange',visibility);host.replaceChildren();host.classList.remove('qc-root','os-connect');host.removeAttribute('data-qc-single');host.removeAttribute('data-qc-canonical-state');host.removeAttribute('aria-busy');state.connection=null;state.pending=null;},getState(){return copy({tenantId:state.tenantId,selected:state.selected,connection:state.connection,items:state.connection?[state.connection]:[],canonicalState:state.canonicalState,busy:state.busy,loaded:state.loaded,canManage:state.access,unavailable:state.unavailable,uncertain:!!state.pending,stale:state.stale,runtime:state.runtime,method:method(),hasQR:!!state.qr,hasPair:!!state.pair});}};
    active=controller;void setTenant(bridge.tenantId);return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionChannels=Object.freeze({mount,unmount});
})();
