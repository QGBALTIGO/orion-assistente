/* Managed channels use only the authenticated Orion API. The browser never
 * addresses the bridge or supplies a channel credential for session commands.
 * mount({host,request,tenantId,onStatus?,onLegacy?}) -> controller:
 * {load,setTenant,setActive,unmount,getState}. Unmount on logout; setActive(false)
 * when navigating away. GETs never start sessions or enable the assistant.
 */
(function () {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const copy=value=>JSON.parse(JSON.stringify(value));
  const statuses={disabled:'Canal desativado',stopped:'Parado',connecting:'Preparando conexão',qr_ready:'Pronto para conectar',pairing:'Aguardando confirmação no WhatsApp',pair_expired:'Código expirado',pair_failed:'Vínculo não confirmado',connected:'Conectado',disconnected:'Desconectado',logged_out:'Desvinculado',error:'Precisa de atenção'};
  const icon=name=>'<svg class="os-icon qc-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="'+({phone:'M22 16.9v3a2 2 0 0 1-2.2 2 20 20 0 0 1-8.7-3.1 20 20 0 0 1-6.2-6.2A20 20 0 0 1 1.8 4 2 2 0 0 1 3.8 2h3a2 2 0 0 1 2 1.7c.2 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L7.7 10a16 16 0 0 0 6.2 6.2l1.3-1.3a2 2 0 0 1 2.1-.5c1 .3 1.9.5 2.9.7a2 2 0 0 1 1.8 1.8',qr:'M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h2v2h4v4h-6v-2m6-6v2'}[name]||'M4 12h16m-6-6 6 6-6 6')+'"/></svg>';
  const button=(label,action,extra='',style='os-outline')=>'<button type="button" class="os-button os-large qc-button '+style+'" data-qc-action="'+esc(action)+'" '+extra+'>'+esc(label)+'</button>';
  const errorText=error=>({401:'Sua sessão precisa ser renovada. Entre novamente no painel.',403:'Somente o proprietário autorizado pode realizar esta ação.',409:'O canal mudou ou a operação não está disponível. Consulte o estado atual antes de continuar.',422:'Confira o nome, a lista de números e os dados da operação.',423:'Os canais gerenciados ainda não estão disponíveis neste ambiente.',429:'Aguarde um pouco antes de consultar novamente.'}[Number(error?.status)]||'Não foi possível conferir a resposta. Consulte o estado atual antes de tentar outra ação.');
  let active=null;

  function mount(bridge) {
    unmount();
    const host=typeof bridge?.host==='string'?document.querySelector(bridge.host):bridge?.host;
    if(!host||typeof bridge?.request!=='function')throw Error('A área dos canais precisa do acesso autenticado ao painel.');
    const state={mounted:true,active:true,epoch:0,read:0,listRead:0,tenantId:bridge.tenantId||null,items:[],selected:null,access:false,capabilities:{},loaded:false,loading:false,busy:false,stale:false,unavailable:false,feedback:'',error:false,editor:false,createDraft:{label:'',senders:''},confirmation:null,pending:null,runtime:null,qr:null,linkFlow:null,method:'code',pairDraft:'',pair:null,pairHidden:false,pairExpired:false,tutorialDone:{code:false,qr:false}};
    const fieldTools=window.OrionFields,releaseFields=fieldTools.bind(host);
    const connectView=window.OrionConnectView;
    let pollTimer=null,qrTimer=null,pairTimer=null,clockTimer=null,lastHTML=null;
    host.classList.add('qc-root','os-connect');
    const base=()=>'/api/v1/tenants/'+encodeURIComponent(state.tenantId)+'/qr-channels';
    const selected=()=>state.items.find(item=>item.id===state.selected)||null;
    const current=epoch=>state.mounted&&epoch===state.epoch;
    const visible=()=>{if(!state.mounted||!state.active||document.hidden)return false;for(let el=host;el;el=el.parentElement)if(el.hidden||el.getAttribute('aria-hidden')==='true'||el.classList.contains('hidden')||getComputedStyle(el).display==='none')return false;return true;};
    const request=(path,method='GET',data)=>bridge.request(path,method,data);
    function feedback(message,error=false){state.feedback=message;state.error=error;const node=host.querySelector('[data-qc-feedback]');if(node){node.hidden=!message;node.className='os-notice qc-note'+(error?' os-error':'');node.textContent=message;if(error){node.tabIndex=-1;node.focus();}}}
    function clearQR(){state.qr=null;clearTimeout(qrTimer);qrTimer=null;host.querySelector('[data-qc-qr]')?.remove();if(!state.pair){clearTimeout(clockTimer);clockTimer=null;}}
    function clearPair(clearDraft=false){state.pair=null;state.pairHidden=false;state.pairExpired=false;clearTimeout(pairTimer);pairTimer=null;host.querySelector('[data-qc-pair-result],[data-qc-pair-hidden]')?.remove();if(clearDraft){state.pairDraft='';const input=host.querySelector('[data-qc-pair-phone]');if(input)input.value='';}if(!state.qr){clearTimeout(clockTimer);clockTimer=null;}}
    function pause(clearDraft=true){state.linkFlow=null;clearTimeout(pollTimer);pollTimer=null;state.read++;clearQR();clearPair(clearDraft);if(clearDraft)state.tutorialDone={code:false,qr:false};}
    function allowed(action){if(selected()?.legacy_transport&&['start','stop','qr','pair','enable','disable'].includes(action))return false;const key=['enable','disable','assistant_on','assistant_off','assistant'].includes(action)?'change_state':action;return state.access===true&&selected()?.can_manage===true&&(action==='pair'?state.runtime?.capabilities?.pair===true:state.capabilities[key]!==false);}
    function canItemWrite(){return canWrite()&&selected()?.can_manage===true;}
    function canWrite(){return state.mounted&&visible()&&state.access&&!state.busy&&!state.pending&&!state.stale;}
    function channel(row){if(!row||!uuid(row.id)||!uuid(row.owner_account_id)||row.tenant_id!==state.tenantId||row.provider!=='baileys'||typeof row.label!=='string'||row.label.length>100||typeof row.enabled!=='boolean'||typeof row.assistant_enabled!=='boolean'||!Number.isSafeInteger(row.revision)||row.revision<1)throw Error('invalid_channel_response');return {legacy_transport:row.legacy_transport===true,id:row.id,tenant_id:row.tenant_id,owner_account_id:row.owner_account_id,provider:row.provider,label:row.label,enabled:row.enabled,assistant_enabled:row.assistant_enabled,revision:row.revision,can_manage:row.can_manage===true,...(Array.isArray(row.allowed_senders)?{allowed_senders:row.allowed_senders.filter(value=>typeof value==='string'&&/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(value))}:{})};}
    function update(row){const safe=channel(row),index=state.items.findIndex(item=>item.id===safe.id);if(index<0)state.items.push(safe);else{if(row.can_manage===undefined)safe.can_manage=state.items[index].can_manage;if(row.legacy_transport===undefined)safe.legacy_transport=state.items[index].legacy_transport;if(!safe.allowed_senders&&state.items[index].allowed_senders)safe.allowed_senders=copy(state.items[index].allowed_senders);state.items[index]=safe;}return safe;}
    function transport(value,id){if(!value||value.channel_id!==id||!Number.isSafeInteger(value.revision)||value.revision<1||!Object.prototype.hasOwnProperty.call(statuses,value.status))throw Error('invalid_channel_status');return {channel_id:id,revision:value.revision,status:value.status,capabilities:{pair:value.capabilities?.pair===true}};}
    function codeAvailable(){return selected()?.enabled&&allowed('pair')&&state.runtime?.status!=='connected';}
    function codeVisible(){return codeAvailable()||!!state.pair||state.pending?.kind==='pair';}
    function method(){return state.method==='code'&&codeVisible()?'code':'qr';}
    function pairReady(){return canItemWrite()&&codeAvailable()&&state.runtime?.status==='qr_ready'&&!state.pair;}
    function qrReady(){return selected()?.enabled&&allowed('qr')&&state.runtime?.status==='qr_ready'&&!state.pair;}
    function startReady(){return !selected()?.enabled||!!state.runtime&&['disabled','stopped','disconnected','error'].includes(state.runtime.status);}
    function capturePair(){const input=host.querySelector('[data-qc-pair-phone]');if(input)state.pairDraft=input.value;}
    function updateClocks(){
      clearTimeout(clockTimer);clockTimer=null;
      for(const [name,value] of [['qr',state.qr],['pair',state.pair]]){
        const node=host.querySelector('[data-qc-'+name+'-time]');
        if(node&&value)node.textContent=connectView.seconds(value.expiresAt-Date.now());
      }
      if(visible()&&(state.qr||state.pair))clockTimer=setTimeout(updateClocks,1000);
    }
    function createForm(){return '<section class="os-card qc-card"><p class="os-help qc-eyebrow">1 · Identificar dispositivo</p><h2>Adicionar dispositivo</h2><p>Dê um nome para reconhecer este WhatsApp. Depois, escolha como conectar seu WhatsApp.</p><form class="os-form qc-form" data-qc-form="create"><fieldset class="os-form"><div class="os-field qc-field"><label for="qc-label">Nome do dispositivo</label><input id="qc-label" name="label" maxlength="100" required autocomplete="off" placeholder="Ex.: Atendimento da loja" value="'+esc(state.createDraft.label)+'"></div><div class="os-field qc-field" data-field-wrapper><label for="qc-senders">Números autorizados</label><textarea id="qc-senders" name="allowed_senders" data-qc-phone-list required rows="3" inputmode="tel" autocomplete="off" spellcheck="false" aria-describedby="qc-senders-help" placeholder="(00) 00000-0000">'+esc(state.createDraft.senders)+'</textarea><p id="qc-senders-help" class="os-help">Informe de 1 a 10 números brasileiros com DDD, um por linha ou separados por vírgula ou ponto e vírgula. Você também pode colar números com +55. Somente estes contatos poderão conversar com a assistente deste dispositivo.</p></div><div class="os-actions qc-actions">'+button('Cancelar','cancel-create')+'<button type="submit" class="os-button os-large qc-button" '+(!canWrite()?'disabled':'')+'>Adicionar dispositivo</button></div></fieldset></form></section>';}
    function confirmationHTML(item){const c=state.confirmation;if(!c||c.id!==item.id)return '';const texts={connect:['Conectar este WhatsApp?','Habilita este dispositivo e prepara a conexão por Código ou QR Code, conforme disponível. A assistente só atende quando autorizada separadamente.'],enable:['Habilitar este canal?','Habilitar libera a conexão deste cadastro. Não inicia uma sessão e não ativa a assistente.'],disable:['Desativar este canal?','O canal deixará de aceitar operações. Seu cadastro e histórico serão preservados.'],start:['Iniciar a conexão deste canal?','Prepara a conexão por Código ou QR Code, conforme disponível. A configuração da assistente permanece independente.'],stop:['Parar a conexão deste canal?','Interrompe a sessão no Orion sem apagar os dados. Esta ação não desvincula o aparelho na lista de dispositivos do WhatsApp.'],assistant_on:['Ativar a assistente neste canal?','Autoriza respostas automáticas de texto somente aos números autorizados deste canal, quando ele estiver conectado e os requisitos operacionais estiverem atendidos.'],assistant_off:['Desativar a assistente neste canal?','Interrompe as novas respostas automáticas deste canal. A conexão e o histórico são independentes.']},[title,description]=texts[c.kind];return '<form class="os-form qc-confirm qc-form" data-qc-form="confirm"><h3>'+title+'</h3><p><strong>'+esc(item.label)+'</strong></p><p>'+description+'</p>'+(c.kind==='assistant_on'?'<label class="os-check"><input type="checkbox" name="authorize_assistant" required><span>Autorizo esse atendimento privado por texto para os números revisados.</span></label>':'')+'<div class="os-actions qc-actions">'+button('Voltar','cancel-confirm')+'<button type="submit" class="os-button os-large qc-button'+(['disable','stop','assistant_off'].includes(c.kind)?' os-danger':'')+'">Confirmar ação</button></div></form>';}
    function connectionHTML(item,runtime){
      if(item.legacy_transport||!item.enabled||runtime?.status==='connected'||!item.can_manage||!state.access||state.confirmation)return '';
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
        content=state.qr?'<section class="qc-qr" data-qc-qr><h3>Escaneie com o WhatsApp</h3><div class="wizard-qr-wrap"><img src="'+esc(state.qr.image)+'" alt="QR temporário para vincular somente o dispositivo '+esc(item.label)+'" width="240" height="240"></div><p class="wizard-qr-time qc-countdown">Expira em <strong data-qc-qr-time>—</strong></p><div class="os-actions qc-actions">'+button('Ocultar QR','hide-qr')+'</div></section>':'<h3>Conectar por QR Code</h3><p>'+(state.pair||['pairing','pair_failed'].includes(runtime?.status)?'Há um código em andamento. Aguarde a confirmação ou a expiração.':'Use o WhatsApp de outro aparelho para escanear.')+'</p><div class="os-actions qc-actions">'+button('Mostrar QR','qr',locked||!qrReady()?'disabled':'','')+'</div>';
      }
      return '<section class="qc-connection wizard-shell" data-qc-connection>'+connectView.heading(codeVisible())+methods+'<div class="wizard-card"><section class="wizard-pane" data-qc-final>'+(mode==='qr'&&runtime?.status==='logged_out'?'<p class="os-notice qc-note" role="status">Sessão desvinculada. Atualize o estado para revisar a conexão.</p>':'')+content+(!state.pair&&!state.qr?'<div class="wizard-steps-actions">'+button('Voltar','tutorial-back',locked?'disabled':'')+'</div>':'')+'</section></div></section>';
    }
    function detail(){
      const item=selected();if(!item)return '<section class="os-card os-empty qc-card qc-welcome">'+icon('phone')+'<h2>Conecte seu primeiro WhatsApp</h2><p>Adicione um dispositivo para acompanhar a conexão e preparar o atendimento da sua empresa.</p></section>';
      const disabled=!canItemWrite()?'disabled':'',runtime=state.runtime?.channel_id===item.id?state.runtime:null,connected=runtime?.status==='connected';
      const connectAction=item.enabled?'start':'connect',canConnect=!runtime||['disabled','stopped','disconnected','error'].includes(runtime.status);
      return '<section class="os-card qc-card"><div class="os-card-header qc-card-head"><div><p class="os-help qc-eyebrow">Seu dispositivo</p><h2>'+esc(item.label)+'</h2></div><span class="os-badge '+(connected?'os-success':'os-neutral')+'">'+esc(runtime?statuses[runtime.status]:'Não verificada')+'</span></div>'+
        '<dl class="os-summary qc-statuses"><div class="os-summary-row"><dt class="os-help">Dispositivo</dt><dd><strong>'+(item.enabled?'Habilitado':'Desativado')+'</strong></dd></div><div class="os-summary-row"><dt class="os-help">Conexão</dt><dd><strong>'+esc(runtime?statuses[runtime.status]:'Não verificada')+'</strong></dd></div><div class="os-summary-row"><dt class="os-help">Assistente</dt><dd><strong>'+(item.assistant_enabled?'Autorizada'+(!item.enabled?' (dispositivo desativado)':''):'Desativada')+'</strong></dd></div></dl>'+
        (item.can_manage&&state.access?'<div class="os-actions qc-actions qc-main-actions">'+(item.legacy_transport||!canConnect?'':button('Conectar WhatsApp',connectAction,disabled+(!startReady()||!allowed('start')||(!item.enabled&&!allowed('change_state'))?' disabled':''),''))+button('Consultar status','status',!allowed('status')?'disabled':'')+'</div>':'<div class="os-notice qc-note">Você pode consultar este cadastro. As alterações exigem o proprietário autorizado.</div>')+
        (item.legacy_transport?'<p class="os-notice qc-note">Este é o WhatsApp que já estava conectado. A central usa a sessão existente. Você pode acompanhar o atendimento e controlar a assistente sem ler outro QR.</p>':'')+
        confirmationHTML(item)+'</section>'+connectionHTML(item,runtime)+
        '<section class="os-card os-section-gap qc-card qc-assistant"><h3>Atendimento automático</h3><p>Os serviços, preços, profissionais e horários vêm do cadastro desta empresa, sem precisar copiar os dados para cada dispositivo.</p>'+(item.can_manage&&state.access?'<div class="os-actions qc-actions">'+button(item.assistant_enabled?'Desativar assistente':'Ativar assistente',item.assistant_enabled?'assistant_off':'assistant_on',disabled+(!item.enabled||!allowed('assistant')?' disabled':''))+'</div>':'')+
        '<details class="os-more-fields qc-settings" data-qc-disclosure="preferences"><summary><span class="os-disclosure-label">Preferências do dispositivo</span></summary><div class="os-disclosure-body"><h3>Números autorizados</h3>'+(item.allowed_senders?'<ul class="qc-authorized">'+item.allowed_senders.map(phone=>'<li><span class="os-badge os-neutral">'+esc(fieldTools.phoneDisplay(phone))+'</span></li>').join('')+'</ul>':'<p class="os-help">A lista é exibida somente quando autorizada pelo servidor.</p>')+(item.legacy_transport?'':'<p class="os-help">Para usar outro nome ou outra lista nesta fase privada, adicione um dispositivo e desative o anterior.</p>')+(item.can_manage&&state.access&&!item.legacy_transport?'<div class="os-actions qc-actions">'+button(item.enabled?'Desativar dispositivo':'Habilitar dispositivo',item.enabled?'disable':'enable',disabled+(!allowed(item.enabled?'disable':'enable')?' disabled':''))+button('Parar conexão','stop',disabled+(!item.enabled||!allowed('stop')?' disabled':''),'os-danger')+'</div>':'')+'</div></details><details class="os-more-fields qc-settings" data-qc-disclosure="about"><summary><span class="os-disclosure-label">Sobre esta conexão</span></summary><div class="os-disclosure-body"><p>Conectar não ativa a assistente. Ao vincular, você pode receber um aviso no próprio WhatsApp.</p><p class="os-help">Fase privada: atendimento somente aos números autorizados. Áudio depende da disponibilidade da conta; chamadas e importação de histórico permanecem desativadas.</p></div></details></section>';
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
      fieldTools.refresh(host);formatSenderList(host.querySelector('[data-qc-phone-list]'));
      if(state.busy)for(const el of host.querySelectorAll('button,input,textarea'))el.disabled=true;
      if(focus){const target=focus.id?document.getElementById(focus.id):focus.action?host.querySelector('[data-qc-action="'+focus.action+'"]'):focus.feedback?host.querySelector('[data-qc-feedback]'):null;
        if(target&&host.contains(target)&&!target.disabled){if(focus.feedback)target.tabIndex=-1;target.focus({preventScroll:true});if(typeof focus.start==='number'&&typeof target.setSelectionRange==='function')try{target.setSelectionRange(focus.start,focus.end,focus.direction);}catch{}}}
      updateClocks();
    }
    function render(){
      if(!state.mounted)return;if(state.editor)captureCreate();if(renderedChannel===state.selected)capturePair();host.setAttribute('aria-busy',String(state.busy));
      const titleTag=host.id==='osManagedChannels'?'h2':'h1';
      const header='<header class="os-title-row qc-heading"><div><p class="os-help qc-eyebrow">'+icon('phone')+'WhatsApp da empresa</p><'+titleTag+' data-qc-title tabindex="-1">Conectar WhatsApp</'+titleTag+'><p>Gerencie seus dispositivos e acompanhe a conexão.</p></div><div class="os-actions qc-actions qc-header-actions">'+(state.access?button('Adicionar dispositivo','new',!canWrite()?'disabled':'',''):'')+button('Atualizar','refresh',state.busy?'disabled':'')+'</div></header>',
        feedbackHTML='<div data-qc-feedback role="alert" class="os-notice qc-note'+(state.error?' os-error':'')+'"'+(!state.feedback?' hidden':'')+'>'+esc(state.feedback)+'</div>';
      if(state.unavailable){paint(header+feedbackHTML+'<section class="os-card qc-card"><h2>Recurso ainda não disponível</h2><p>A conexão de dispositivos ainda não foi liberada neste ambiente.</p>'+(bridge.onLegacy?'<div class="os-actions qc-actions">'+button('Abrir conector existente','legacy')+'</div>':'')+'</section>');return;}
      paint(header+feedbackHTML+(state.pending?'<div class="os-notice os-error qc-note"><div><strong>Resultado ainda não confirmado.</strong><p>Confira o estado atual antes de repetir uma ação.</p><div class="os-actions qc-actions">'+button('Conferir estado','refresh')+(state.pending.kind==='create'?button('Recuperar o mesmo cadastro','retry-create'):'')+'</div></div></div>':'')+(state.editor?createForm():'<div class="qc-layout"><section class="os-card qc-card"><div class="os-card-header qc-card-head"><h2>Seus dispositivos</h2><span class="os-badge os-neutral qc-count">'+state.items.length+'</span></div>'+(state.items.length?'<ul class="qc-list">'+state.items.map(item=>'<li><button type="button" class="os-service-option qc-channel" data-qc-channel="'+esc(item.id)+'" aria-current="'+(item.id===state.selected)+'" aria-pressed="'+(item.id===state.selected)+'">'+icon('phone')+'<strong>'+esc(item.label)+'</strong><small>Conexão: '+(state.runtime?.channel_id===item.id?esc(statuses[state.runtime.status]):item.enabled?'Não consultada':'Desativado')+'</small><small>Assistente: '+(item.assistant_enabled?'autorizada':'desativada')+'</small></button></li>').join('')+'</ul>':'<p class="os-help qc-empty">Nenhum dispositivo cadastrado.</p>')+'</section><div>'+detail()+'</div></div>'));
    }
    function schedule(delay=5000){clearTimeout(pollTimer);pollTimer=null;if(!visible()||!selected()?.enabled||!allowed('status')||state.busy||(state.pending&&state.pending.kind!=='pair')||state.stale||state.editor||state.confirmation)return;pollTimer=setTimeout(()=>{pollTimer=null;void loadStatus();},delay);}
    async function loadStatus(){
      if(!visible()||state.busy||!selected()||!allowed('status'))return;
      const epoch=state.epoch,id=state.selected,revision=selected().revision,read=++state.read;
      clearTimeout(pollTimer);pollTimer=null;
      try{
        const result=await request(base()+'/'+encodeURIComponent(id)+'/status');
        if(!current(epoch)||!visible()||read!==state.read||id!==state.selected||selected()?.revision!==revision)return;
        const checked=transport(result,id);
        if(checked.revision!==revision){state.confirmation=null;state.runtime=null;state.stale=true;clearQR();clearPair();feedback('A configuração do canal mudou. Atualize a lista e revise a ação novamente.',true);render();return;}
        let changed=JSON.stringify(state.runtime)!==JSON.stringify(checked);
        state.runtime=checked;
        if(checked.status!=='qr_ready'&&state.qr){clearQR();changed=true;}
        if(checked.status!=='pairing'&&state.pair){clearPair();changed=true;}
        if(state.pending?.kind==='pair'&&!['pairing','pair_failed','pair_expired','connecting'].includes(checked.status)){
          state.pending=null;state.feedback=checked.status==='connected'?'WhatsApp conectado.':'Estado conferido. Uma nova tentativa exige sua ação.';state.error=false;changed=true;
        }
        if(checked.status==='connected')state.linkFlow=null;
        // Repeated status reads must not remount focused fields or refresh QR images.
        if(changed)render();else updateClocks();
        if(checked.status==='qr_ready'&&state.linkFlow===id&&method()==='qr'&&state.tutorialDone.qr&&!state.qr){await showQR();return;}
        try{await bridge.onStatus?.(copy(state.runtime));}catch{}
        schedule();
      }catch(error){if(current(epoch)&&visible()&&read===state.read&&id===state.selected){state.runtime=null;clearQR();clearPair();if([401,403].includes(Number(error.status)))state.access=false;feedback(errorText(error),true);render();schedule(15000);}}
    }
    async function load(){if(!visible()||state.loading||state.busy)return;const epoch=state.epoch,read=++state.listRead;state.loading=true;capturePair();pause(false);state.feedback='';state.error=false;try{const result=await request(base());if(!current(epoch)||!visible()||read!==state.listRead)return;if(!result||!Array.isArray(result.items))throw Error('invalid_channel_list');const items=result.items.map(channel);if(new Set(items.map(item=>item.id)).size!==items.length)throw Error('invalid_channel_list');state.items=items;state.access=result.access?.can_manage===true;state.capabilities=result.capabilities||{};state.unavailable=false;state.stale=false;state.loaded=true;state.editor=false;state.confirmation=null;if(!['create','pair'].includes(state.pending?.kind)){state.feedback=state.pending?'Dispositivos atualizados. Confira a conexão antes de continuar.':'';state.pending=null;}if(!items.some(item=>item.id===state.selected)){state.selected=items[0]?.id||null;state.pairDraft='';state.method='code';}state.runtime=null;render();if(selected()?.enabled)void loadStatus();}
      catch(error){if(!current(epoch)||!visible()||read!==state.listRead)return;state.unavailable=[404,423].includes(Number(error.status));state.access=false;state.runtime=null;state.feedback=errorText(error);state.error=true;render();}finally{if(current(epoch))state.loading=false;}}
    async function showQR(){if(!visible()||state.busy||state.pending||!state.tutorialDone.qr||!qrReady())return;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();state.method='qr';state.linkFlow=null;const epoch=state.epoch,id=state.selected,revision=selected().revision,read=++state.read;try{const result=await request(base()+'/'+encodeURIComponent(id)+'/qr');if(!current(epoch)||!visible()||read!==state.read||id!==state.selected||selected()?.revision!==revision)return;if(result?.channel_id!==id||result.revision!==revision||typeof result.expires_in_ms!=='number'||!Number.isFinite(result.expires_in_ms)||result.expires_in_ms<=0||result.expires_in_ms>45000||typeof result.image!=='string'||result.image.length>350000||!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(result.image))throw Error('invalid_channel_qr');state.qr={image:result.image,channel_id:id,expiresAt:Date.now()+result.expires_in_ms};render();qrTimer=setTimeout(()=>{clearQR();if(current(epoch)&&visible()&&id===state.selected){feedback('Este QR expirou. Consulte um novo QR para continuar.');render();}},result.expires_in_ms);schedule();}
      catch(error){if(current(epoch)&&visible()&&read===state.read){clearQR();feedback(Number(error.status)?errorText(error):'Não há um QR válido disponível. Consulte o status e tente novamente.',true);render();schedule();}}}
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
        state.pending=null;state.linkFlow=null;state.pair={code,channel_id:id,expiresAt:Date.now()+result.expires_in_ms};state.feedback='';state.error=false;
        pairTimer=setTimeout(()=>{clearPair();if(current(epoch)&&visible()&&id===state.selected){state.pairExpired=true;render();void loadStatus();}},result.expires_in_ms);
      }catch(error){
        if(current(epoch)&&visible()&&read===state.read&&id===state.selected){const status=Number(error.status);state.pending=(!status||status>=500)?{kind:'pair',id}:null;if(status===403)state.access=false;if([404,423].includes(status))state.unavailable=true;if(status===409)state.stale=true;state.feedback=errorText(error);state.error=true;}
      }finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}
    }
    function captureCreate(){const form=host.querySelector('[data-qc-form=create]');if(form)state.createDraft={label:form.elements.label.value,senders:form.elements.allowed_senders.value};}
    function formatSenderList(input){
      if(!input||input.disabled||input.readOnly)return;
      const raw=input.value,start=input.selectionStart,end=input.selectionEnd,direction=input.selectionDirection;
      let result='',offset=0;const positions=[start,end];
      for(const [index,part] of raw.split(/([\n,;]+)/).entries()){
        let next=part,project=position=>position;
        if(index%2===0&&part.trim()){
          const leading=part.match(/^[ \t\r]*/)[0],trailing=part.match(/[ \t\r]*$/)[0],middle=part.slice(leading.length,part.length-trailing.length);
          // Reuse the single-phone formatter for each segment and its caret, preserving delimiters.
          const probe=document.createElement('input');probe.type='tel';probe.dataset.orionField='phone';probe.value=middle;
          const local=position=>Math.max(0,Math.min(middle.length,position-offset-leading.length));
          probe.setSelectionRange(local(start),local(end),direction);fieldTools.formatInput(probe);
          next=leading+probe.value+trailing;
          project=(position,which)=>position<=leading.length?position:position>=part.length-trailing.length?next.length-(part.length-position):leading.length+(which===0?probe.selectionStart:probe.selectionEnd);
        }
        for(const [which,position] of [start,end].entries())if(position>=offset&&position<=offset+part.length)positions[which]=result.length+project(position-offset,which);
        result+=next;offset+=part.length;
      }
      if(result!==raw){input.value=result;input.setSelectionRange(positions[0],positions[1],direction);}
    }
    function senderError(input,message=''){
      host.querySelector('#qc-senders-error')?.remove();input.setCustomValidity(message);
      const ids=(input.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&id!=='qc-senders-error');
      if(message){const node=document.createElement('small');node.id='qc-senders-error';node.className='orion-field-error';node.setAttribute('role','alert');node.textContent=message;input.insertAdjacentElement('afterend',node);input.setAttribute('aria-invalid','true');ids.push(node.id);}else input.removeAttribute('aria-invalid');
      if(ids.length)input.setAttribute('aria-describedby',ids.join(' '));else input.removeAttribute('aria-describedby');
    }
    function validateSenders(input){try{senders(input.value);senderError(input);return true;}catch(error){senderError(input,error.message);return false;}}
    function validateListEvent(event){if(event.target.matches?.('[data-qc-phone-list]')&&host.contains(event.target)&&!event.target.disabled&&(event.type==='invalid'||event.target.value.trim()))validateSenders(event.target);}
    function input(event){
      if(!host.contains(event.target))return;
      if(event.target.matches('[data-qc-pair-phone]')){capturePair();return;}
      if(!event.target.closest('[data-qc-form=create]'))return;
      if(event.target.matches('[data-qc-phone-list]')){senderError(event.target);if(!event.isComposing)formatSenderList(event.target);}
      captureCreate();
    }
    function beforeInput(event){
      const input=event.target,backward=event.inputType==='deleteContentBackward',forward=event.inputType==='deleteContentForward';
      if(!input.matches?.('[data-qc-phone-list]')||!host.contains(input)||input.disabled||input.readOnly||(!backward&&!forward)||input.selectionStart!==input.selectionEnd)return;
      const caret=input.selectionStart,index=backward?caret-1:caret,raw=input.value;
      if(index<0||index>=raw.length||/[\d\n,;]/.test(raw[index]))return;
      let left=index,right=index;while(left>0&&!/[\n,;]/.test(raw[left-1]))left--;while(right<raw.length&&!/[\n,;]/.test(raw[right]))right++;
      if(!fieldTools.phoneParts(raw.slice(left,right)))return;
      let digit=index;while(digit>=left&&digit<right&&!/\d/.test(raw[digit]))digit+=backward?-1:1;
      event.preventDefault();
      if(digit<left||digit>=right){input.setSelectionRange(backward?left:right,backward?left:right);return;}
      input.setRangeText('',digit,digit+1,'end');if(forward)input.setSelectionRange(caret,caret);formatSenderList(input);input.dispatchEvent(new Event('input',{bubbles:true}));
    }
    function senders(raw){const values=String(raw||'').split(/[\n,;]+/).map(value=>value.trim()).filter(Boolean);if(!values.length||values.length>10)throw Error('Informe de 1 a 10 números autorizados.');const phones=values.map((value,index)=>{let phone;try{phone=fieldTools.phoneValue(value,{required:true}).slice(1);}catch{throw Error('Confira o número '+(index+1)+': informe um telefone brasileiro completo com DDD.');}if(!/^55[1-9]\d(?:9\d{8}|[2-5]\d{7})$/.test(phone))throw Error('Confira o número '+(index+1)+': informe um celular ou telefone fixo brasileiro válido.');return phone;});if(new Set(phones).size!==phones.length)throw Error('Remova os números repetidos da lista.');return phones;}
    async function perform(operation){if(state.busy||!visible())return;const epoch=state.epoch;state.busy=true;state.read++;state.listRead++;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();render();feedback('Conferindo a operação…');try{let result=await request(operation.path,operation.method,operation.data);if(!current(epoch))return;if(operation.kind==='connect'){const item=update(result);if(!item.enabled||item.revision!==operation.data.expected_revision+1)throw Error('invalid_connect_state');state.selected=item.id;state.runtime=null;if(!visible()){state.confirmation=null;return;}operation={kind:'start',id:item.id,path:base()+'/'+encodeURIComponent(item.id)+'/start',method:'POST',data:{expected_revision:item.revision}};result=await request(operation.path,operation.method,operation.data);if(!current(epoch))return;}if(operation.kind==='create'||operation.method==='PATCH'){const item=update(result);state.selected=item.id;if(operation.kind==='create'){item.can_manage=true;if(!item.allowed_senders)item.allowed_senders=copy(operation.data.allowed_senders);}state.runtime=null;}else {state.runtime=transport(result,operation.id);if(operation.kind==='start'&&visible())state.linkFlow=operation.id;}state.pending=null;state.confirmation=null;state.editor=false;state.createDraft={label:'',senders:''};state.feedback=operation.kind==='create'?(result.credential_issued===true?'Dispositivo adicionado. Clique em Conectar WhatsApp para continuar.':'Dispositivo recuperado. Clique em Conectar WhatsApp para continuar.'):'O servidor confirmou a operação. Confira os estados do canal e da assistente.';state.error=false;if(visible())render();}
      catch(error){if(current(epoch)){const code=Number(error.status);state.pending=(!code||code>=500)?operation:null;if(code===403)state.access=false;if([404,423].includes(code))state.unavailable=true;if(code===409)state.stale=true;state.feedback=errorText(error);state.error=true;state.confirmation=null;if(visible())render();}}finally{if(current(epoch)){state.busy=false;if(visible())render();schedule();}}}
    async function submit(event){const form=event.target.closest('[data-qc-form]');if(!form||!host.contains(form))return;event.preventDefault();if(!canWrite())return;if(form.dataset.qcForm==='create'&&!validateSenders(form.elements.allowed_senders)){feedback(form.elements.allowed_senders.validationMessage,true);form.elements.allowed_senders.focus();return;}if(!form.reportValidity())return;try{if(form.dataset.qcForm==='pair'){await generatePair(form.elements.phone.value);return;}if(form.dataset.qcForm==='create'){state.createDraft={label:form.elements.label.value,senders:form.elements.allowed_senders.value};const label=form.elements.label.value.trim();if(!label||label.length>100||/[\u0000-\u001f]/.test(label))throw Error('Informe um nome válido para identificar o canal.');const data={label,provider:'baileys',allowed_senders:senders(form.elements.allowed_senders.value),idempotency_key:crypto.randomUUID()};await perform({kind:'create',path:base(),method:'POST',data});return;}const c=state.confirmation,item=selected();if(!c||!item||!canItemWrite()||item.id!==c.id||item.revision!==c.revision){state.confirmation=null;feedback('A configuração mudou. Consulte o canal e revise a ação novamente.',true);render();return;}if(c.kind==='assistant_on'&&!form.elements.authorize_assistant.checked)return;const flags={connect:{enabled:true},enable:{enabled:true},disable:{enabled:false},assistant_on:{assistant_enabled:true},assistant_off:{assistant_enabled:false}},isState=!!flags[c.kind];await perform({kind:c.kind,id:item.id,path:base()+'/'+encodeURIComponent(item.id)+(isState?'':'/'+c.kind),method:isState?'PATCH':'POST',data:{expected_revision:item.revision,...(flags[c.kind]||{})}});}catch(error){feedback(error.message||'Confira os dados antes de salvar.',true);}}
    async function click(event){
      const el=event.target.closest('[data-qc-action],[data-qc-channel],[data-qc-method]');if(!el||!host.contains(el)||el.disabled||state.busy)return;
      const action=el.dataset.qcAction;
      if(action==='tutorial-next'){if(!canItemWrite())return;state.tutorialDone[method()]=true;state.linkFlow=null;render();if(method()==='qr'&&qrReady())await showQR();else host.querySelector('[data-qc-pair-phone]')?.focus();return;}
      if(action==='tutorial-back'){if(!canItemWrite()||state.pair||state.qr)return;capturePair();state.tutorialDone[method()]=false;state.linkFlow=null;render();return;}
      if(el.dataset.qcMethod){if(!canItemWrite()||!codeVisible())return;capturePair();state.method=el.dataset.qcMethod==='code'?'code':'qr';state.linkFlow=null;state.read++;render();schedule();return;}
      if(el.dataset.qcChannel){if(state.pending)return;pause();state.method='code';state.selected=el.dataset.qcChannel;state.confirmation=null;state.runtime=null;state.feedback='';render();if(selected()?.enabled)void loadStatus();return;}
      if(action==='legacy'){await bridge.onLegacy?.();return;}
      if(action==='hide-qr'){state.linkFlow=null;clearQR();render();return;}
      if(action==='cancel-pair'){if(!state.pair)return;state.pairHidden=true;render();host.querySelector('[data-qc-action=show-code]')?.focus({preventScroll:true});return;}
      if(action==='show-code'){if(!state.pair||state.pair.expiresAt<=Date.now()||!visible())return;state.pairHidden=false;render();host.querySelector('[data-qc-action=cancel-pair]')?.focus({preventScroll:true});return;}
      if(action==='copy-code'){const pair=state.pair,epoch=state.epoch;if(!pair||state.pairHidden||pair.expiresAt<=Date.now()||!visible())return;try{await navigator.clipboard.writeText(pair.code);if(current(epoch)&&visible()&&state.pair===pair)feedback('Código copiado. Cole no WhatsApp.');}catch{if(current(epoch)&&visible()&&state.pair===pair)feedback('Selecione o código para copiar.',true);}return;}
      if(action==='refresh'){await load();return;}if(action==='retry-create'){if(state.pending?.kind==='create')await perform(state.pending);return;}
      if(action==='status'){await loadStatus();return;}if(action==='qr'){await showQR();return;}
      if(action==='cancel-create'){state.editor=false;render();return;}if(action==='cancel-confirm'){state.confirmation=null;render();schedule();return;}
      if(!canWrite())return;
      if(action==='new'){pause();state.editor=true;state.confirmation=null;render();host.querySelector('input')?.focus();return;}
      if(['connect','enable','disable','start','stop','assistant_on','assistant_off'].includes(action)){const item=selected();if(!item||!canItemWrite()||!allowed(action==='connect'?'start':action)||action==='connect'&&!allowed('change_state'))return;if(['connect','start'].includes(action)&&!startReady())return;if(['start','stop','assistant_on','assistant_off'].includes(action)&&!item.enabled)return;state.confirmation={kind:action,id:item.id,revision:item.revision};state.read++;clearTimeout(pollTimer);pollTimer=null;clearQR();clearPair();render();host.querySelector('.qc-confirm h3')?.setAttribute('tabindex','-1');host.querySelector('.qc-confirm h3')?.focus();}
    }
    function setActive(value){state.active=value===true;if(!state.active){pause();return;}if(visible()){void load();}}
    function setTenant(id){pause();state.epoch++;state.listRead++;state.tenantId=id;state.items=[];state.selected=null;state.method='code';state.runtime=null;state.loaded=false;state.loading=false;state.busy=false;state.stale=false;state.access=false;state.capabilities={};state.editor=false;state.createDraft={label:'',senders:''};state.confirmation=null;state.pending=null;state.feedback='';state.error=false;state.unavailable=false;if(!uuid(id)){feedback('Selecione uma empresa autorizada para consultar seus canais.');render();return Promise.resolve();}render();return visible()?load():Promise.resolve();}
    function visibility(){if(!visible())pause();else void load();}
    host.addEventListener('click',click);host.addEventListener('submit',submit);host.addEventListener('input',input);host.addEventListener('change',input);host.addEventListener('compositionend',input);host.addEventListener('beforeinput',beforeInput);host.addEventListener('focusout',validateListEvent);host.addEventListener('invalid',validateListEvent,true);document.addEventListener('visibilitychange',visibility);
    const observer=new MutationObserver(visibility);for(let el=host;el;el=el.parentElement)observer.observe(el,{attributes:true,attributeFilter:['hidden','class','style','aria-hidden']});
    const controller={load,setTenant,setActive,unmount(){if(!state.mounted)return;pause();releaseFields();state.mounted=false;state.epoch++;state.listRead++;observer.disconnect();host.removeEventListener('click',click);host.removeEventListener('submit',submit);host.removeEventListener('input',input);host.removeEventListener('change',input);host.removeEventListener('compositionend',input);host.removeEventListener('beforeinput',beforeInput);host.removeEventListener('focusout',validateListEvent);host.removeEventListener('invalid',validateListEvent,true);document.removeEventListener('visibilitychange',visibility);host.replaceChildren();host.classList.remove('qc-root','os-connect');host.removeAttribute('aria-busy');state.items=[];state.pending=null;},getState(){return copy({tenantId:state.tenantId,selected:state.selected,items:state.items,busy:state.busy,loaded:state.loaded,canManage:state.access,unavailable:state.unavailable,uncertain:!!state.pending,stale:state.stale,runtime:state.runtime,method:method(),hasQR:!!state.qr,hasPair:!!state.pair});}};
    active=controller;void setTenant(bridge.tenantId);return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionChannels=Object.freeze({mount,unmount});
})();
