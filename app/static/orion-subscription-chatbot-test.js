/* Conversational simulation. Its history belongs only to this mounted panel. */
(function () {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const paths={bot:'M8 4h8v3H8zM4 7h16v13H4zM8 11h.01M16 11h.01M8 16h8M12 1v3M1 11h3m16 0h3',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',send:'m22 2-7 20-4-9-9-4 20-7M22 2 11 13',expand:'M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5',collapse:'M3 8h5V3m8 0v5h5M8 21v-5H3m18 0h-5v5',refresh:'M20 11a8 8 0 1 0-2.4 6M20 4v7h-7'};
  const icon=name=>'<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="'+(paths[name]||paths.bot)+'"/></svg>';
  const button=(label,action,symbol,extra='')=>'<button type="button" class="os-button os-outline" data-sct-action="'+action+'" '+extra+'>'+icon(symbol)+'<span>'+esc(label)+'</span></button>';
  const mediaTypes={image:{mime:'image/jpeg',limit:5000000,label:'Imagem'},sticker:{mime:'image/webp',limit:500000,label:'Figurinha'},video:{mime:'video/mp4',limit:16000000,label:'Vídeo'},ptv:{mime:'video/mp4',limit:16000000,label:'Recado de vídeo'},audio:{mime:'audio/mpeg',limit:16000000,label:'Áudio'},document:{mimes:['text/plain','application/pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.android.package-archive'],limit:10000000,label:'Documento'}};
  const matchLabels={equal:'Exata',like:'Contém',array:'Lista'};
  const string=(value,max=65536)=>typeof value==='string'&&value.length<=max*2&&[...value].length<=max;
  const positive=value=>Number.isSafeInteger(value)&&value>0;
  const revision=value=>Number.isSafeInteger(value)&&value>=0;
  const wellFormed=value=>[...value].every(character=>{const point=character.codePointAt(0);return point<0xd800||point>0xdfff;});
  const validText=value=>typeof value==='string'&&value.length<=400&&!/[\u0000-\u001f\u007f]/.test(value)&&wellFormed(value);
  const mime=value=>String(value||'').split(';')[0].trim().toLowerCase();
  const timeLabel=value=>new Intl.DateTimeFormat('pt-BR',{hour:'2-digit',minute:'2-digit'}).format(new Date(value));
  const copy=value=>JSON.parse(JSON.stringify(value));
  function deviceDTO(value){
    if(!object(value)||!uuid(value.id)||!string(value.name,100)||!string(value.status,64)||(value.phone_e164!==null&&!/^\+[1-9][0-9]{6,14}$/.test(value.phone_e164||'')))throw Error('invalid_device');
    return value;
  }
  function bootstrapDTO(value){
    if(!object(value)||!revision(value.chatbot_revision)||!Array.isArray(value.devices)||value.devices.length>1000||!object(value.capabilities)||typeof value.capabilities.simulate!=='boolean'||typeof value.capabilities.private_media!=='boolean'||!object(value.limits)||value.limits.message_utf16!==400||!positive(value.limits.max_outputs)||value.limits.max_outputs>30)throw Error('invalid_bootstrap');
    value.devices.forEach(deviceDTO);if(new Set(value.devices.map(item=>item.id)).size!==value.devices.length)throw Error('invalid_devices');
    return copy(value);
  }
  function messageDTO(value){
    if(!object(value)||!uuid(value.id)||!Number.isInteger(value.delay_seconds)||value.delay_seconds<0||value.delay_seconds>30)throw Error('invalid_message');
    if(['text','unavailable'].includes(value.type)){
      if(!string(value.text))throw Error('invalid_text');
    }else if(value.type==='list'){
      if(['title','description','footer','button_text'].some(key=>!string(value[key],4096))||!string(value.selection_token,8192)||!value.selection_token||!Number.isFinite(Date.parse(value.expires_at))||!Array.isArray(value.sections)||!value.sections.length||value.sections.length>10)throw Error('invalid_list');
      const rows=[];
      for(const section of value.sections){if(!object(section)||!uuid(section.id)||!string(section.title,4096)||!Array.isArray(section.rows)||!section.rows.length)throw Error('invalid_list');for(const row of section.rows){if(!object(row)||!uuid(row.id)||!string(row.title,4096)||!string(row.description,4096))throw Error('invalid_row');rows.push(row.id);}}
      if(rows.length>10||new Set(rows).size!==rows.length)throw Error('invalid_rows');
    }else if(value.type==='media'){
      const type=mediaTypes[value.kind],asset=value.asset;
      if(!type||!string(value.caption,4096)||!object(asset)||!uuid(asset.id)||asset.kind!==value.kind||!(type.mimes||[type.mime]).includes(mime(asset.mime))||!positive(asset.size)||asset.size>type.limit||!string(asset.file_name,255)||!string(asset.content_url,16384))throw Error('invalid_asset');
      if(asset.thumbnail_url!==null&&asset.thumbnail_url!==undefined&&(!string(asset.thumbnail_url,16384)||mime(asset.thumbnail_mime)!=='image/jpeg'||!positive(asset.thumbnail_size)||asset.thumbnail_size>100000))throw Error('invalid_thumbnail');
    }else if(value.type==='location'){
      if(!Number.isFinite(value.latitude)||!Number.isFinite(value.longitude)||Math.abs(value.latitude)>90||Math.abs(value.longitude)>180)throw Error('invalid_location');
    }else if(value.type==='carousel'){
      if(!string(value.body,4096)||!string(value.notice,4096)||!Array.isArray(value.cards)||!value.cards.length||value.cards.length>10||value.cards.some(card=>!object(card)||['title','body','button_text'].some(key=>!string(card[key],4096))||card.image_status!=='not_requested'))throw Error('invalid_carousel');
    }else if(value.type==='external_request'){
      if(!['GET','POST'].includes(value.method)||!string(value.url,2048)||!string(value.custom_template,32000)||typeof value.as_file!=='boolean'||value.executed!==false||!string(value.text,4096))throw Error('invalid_external_request');
    }else throw Error('invalid_message_type');
    return value;
  }
  function simulationDTO(value,payload,maxOutputs){
    if(!object(value)||value.request_id!==payload.request_id||value.context_id!==payload.context_id||value.chatbot_revision!==payload.expected_chatbot_revision||deviceDTO(value.device).id!==payload.device_id||!object(value.input)||value.input.type!==payload.input.type||!(payload.input.type==='text'?validText(value.input.text):string(value.input.text,payload.input.type==='rule'?255:24)&&wellFormed(value.input.text))||!Array.isArray(value.messages)||value.messages.length<1||value.messages.length>maxOutputs||!Array.isArray(value.keywords)||value.keywords.length>10000)throw Error('invalid_simulation');
    if(payload.input.type==='text'&&value.input.text!==payload.input.text.trim()||payload.input.type==='rule'&&value.input.rule_id!==payload.input.rule_id||payload.input.type==='selection'&&value.input.row_id!==payload.input.row_id)throw Error('invalid_input');
    if(value.matched_rule!==null&&(!object(value.matched_rule)||!uuid(value.matched_rule.id)||typeof value.matched_rule.enabled!=='boolean'))throw Error('invalid_rule');
    value.messages.forEach(messageDTO);
    if(new Set(value.messages.map(item=>item.id)).size!==value.messages.length)throw Error('duplicate_messages');
    if(value.keywords.some(item=>!object(item)||!uuid(item.rule_id)||!string(item.keyword,255)||!Object.hasOwn(matchLabels,item.match_mode)||typeof item.enabled!=='boolean'))throw Error('invalid_keywords');
    return copy(value);
  }
  const errorMessage=error=>({401:'Sua sessão expirou. Entre novamente no Orion.',403:'Seu acesso não permite testar o Chatbot desta empresa.',404:'Este dispositivo ou resposta não está mais disponível.',409:'As respostas do Chatbot mudaram. Atualize o teste antes de continuar.',413:'A mensagem excede o tamanho permitido.',422:'Confira o dispositivo e a mensagem antes de continuar.',429:'Aguarde um pouco antes de testar novamente.',503:'Não foi possível concluir a simulação. Tente novamente.'}[Number(error?.status)]||'Não foi possível concluir a simulação. Sua conversa foi preservada.');
  function createSurface(host){
    host.classList.add('os-chatbot-test');
    host.innerHTML='<header class="os-title-row"><div><h1>Testar Chatbot</h1><p>Confira a correspondência e o conteúdo das respostas. Horários e frequências são aplicados nos envios reais.</p></div><div class="os-actions">'+
      button('Limpar Chat','clear','trash','disabled')+button('Entrar em tela cheia','fullscreen','expand','aria-pressed="false"')+'</div></header>'+
      '<div class="os-notice os-error" data-sct-feedback role="alert" hidden></div>'+
      '<section class="os-card os-chatbot-test-card" aria-label="Simulador de conversa">'+
        '<div class="os-chatbot-test-toolbar"><div class="os-chatbot-test-device"><label for="sct-device">Dispositivo</label><select id="sct-device" data-sct-device disabled><option value="">Carregando dispositivos…</option></select></div><p class="os-chatbot-test-count" data-sct-count aria-live="polite">0 mensagens</p></div>'+
        '<div class="os-chatbot-test-conversation-heading" data-sct-heading>Escolha um dispositivo para começar</div>'+
        '<div class="os-chatbot-test-scroll" data-sct-scroll><div class="os-chatbot-test-empty" data-sct-empty>'+icon('bot')+'<strong>Inicie uma conversa de teste</strong><p>Escolha um dispositivo e envie uma mensagem para conferir a resposta.</p></div><ol class="os-chatbot-test-messages" data-sct-messages role="log" aria-label="Conversa de teste" aria-live="polite" aria-relevant="additions text"></ol><div class="os-chatbot-test-pending" data-sct-pending role="status" hidden><span class="os-spinner" aria-hidden="true"></span>Preparando a resposta…</div></div>'+
        '<section class="os-chatbot-test-keywords" data-sct-keywords aria-labelledby="sct-keywords-title" hidden><h2 id="sct-keywords-title">Palavras-chave disponíveis</h2><div class="os-chatbot-test-keyword-list" data-sct-keyword-list></div></section>'+
        '<div class="os-chatbot-test-composer"><form data-sct-form novalidate><label for="sct-message">Mensagem de teste</label><input id="sct-message" class="os-chatbot-test-input" data-sct-input type="text" maxlength="400" autocomplete="off" placeholder="Digite uma mensagem…" aria-describedby="sct-help sct-character-count" disabled><button type="submit" class="os-button" data-sct-send disabled>'+icon('send')+'<span>Enviar</span></button><div class="os-chatbot-test-composer-meta"><span id="sct-help">Pressione Enter para enviar. A conversa fica apenas neste painel.</span><span id="sct-character-count" class="os-chatbot-test-character-count" data-sct-character-count>0/400</span></div><div class="os-form-message" data-sct-input-error role="alert"></div></form></div>'+
      '</section>';
    const one=name=>host.querySelector('[data-sct-'+name+']');
    return {host,device:one('device'),count:one('count'),heading:one('heading'),scroll:one('scroll'),empty:one('empty'),messages:one('messages'),pending:one('pending'),keywords:one('keywords'),keywordList:one('keyword-list'),input:one('input'),send:one('send'),form:one('form'),characters:one('character-count'),inputError:one('input-error'),feedback:one('feedback'),clear:host.querySelector('[data-sct-action="clear"]'),fullscreen:host.querySelector('[data-sct-action="fullscreen"]')};
  }
  let active=null;
  function mount(bridge){
    unmount();
    const host=typeof bridge?.host==='string'?document.querySelector(bridge.host):bridge?.host;
    if(!host||typeof bridge?.request!=='function'||!uuid(bridge.tenantId))throw Error('Não foi possível confirmar a empresa deste teste.');
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/chatbot-test';
    const state={mounted:true,epoch:1,read:0,contextId:crypto.randomUUID(),selected:'',loaded:false,loading:false,data:null,pending:null,history:[],keywords:null,draft:'',feedback:'',inputError:'',fullscreen:false,initial:true,initialRule:uuid(bridge.initialRuleId)?bridge.initialRuleId:null};
    const ui=createSurface(host),mediaReads=new Set(),blobs=new Set();let menuTimer=null,mediaRunning=0,overflowBefore='',inertBefore=[];
    const request=(suffix='',method='GET',payload)=>bridge.request(base+suffix,method,payload);
    const current=(epoch,contextId)=>state.mounted&&state.epoch===epoch&&(contextId===undefined||state.contextId===contextId);
    const selected=()=>state.data?.devices.find(item=>item.id===state.selected)||null;
    const canSimulate=()=>state.loaded&&!state.loading&&state.data?.capabilities.simulate===true&&!!selected();
    function assetURL(asset,variant){
      const source=variant==='thumbnail'?asset.thumbnail_url:asset.content_url;
      if(typeof source!=='string'||source.length>16384||!source.startsWith('/')||source.startsWith('//'))throw Error('invalid_asset_url');
      const url=new URL(source,location.origin),keys=[...url.searchParams.keys()],ticket=url.searchParams.get('ticket');
      if(url.origin!==location.origin||url.username||url.password||url.hash||url.pathname!==base+'/assets/'+asset.id+'/content'||keys.length!==2||new Set(keys).size!==2||!keys.includes('ticket')||!keys.includes('variant')||url.searchParams.get('variant')!==variant||!ticket||!/^[-_A-Za-z0-9.]{1,8192}$/.test(ticket))throw Error('invalid_asset_url');
      return url.pathname+url.search;
    }
    async function readAsset(asset,variant,signal){
      const thumbnail=variant==='thumbnail',type=thumbnail?{mime:'image/jpeg',limit:100000}:{...mediaTypes[asset.kind],mime:mime(asset.mime)},expected=thumbnail?asset.thumbnail_size:asset.size;
      if(!positive(expected)||expected>type.limit)throw Error('invalid_asset_size');
      const response=await fetch(assetURL(asset,variant),{method:'GET',credentials:'same-origin',mode:'same-origin',cache:'no-store',redirect:'error',headers:{Accept:type.mime},signal});
      if(!response.ok||response.status!==200){await response.body?.cancel();throw Error('asset_http_error');}
      if(mime(response.headers.get('Content-Type'))!==type.mime){await response.body?.cancel();throw Error('asset_mime_error');}
      const length=response.headers.get('Content-Length'),encoded=response.headers.get('Content-Encoding');
      if(length!==null&&(!/^[0-9]+$/.test(length)||!positive(Number(length))||Number(length)>type.limit||(!encoded||encoded==='identity')&&Number(length)!==expected)){await response.body?.cancel();throw Error('asset_length_error');}
      const reader=response.body?.getReader();if(!reader)throw Error('asset_stream_missing');
      const chunks=[];let size=0;
      try{
        for(;;){if(signal.aborted)throw Error('asset_aborted');const {done,value}=await reader.read();if(done)break;if(!value||!Number.isSafeInteger(value.byteLength))throw Error('asset_chunk_error');size+=value.byteLength;if(size>expected||size>type.limit)throw Error('asset_too_large');chunks.push(value);}
      }catch(error){await reader.cancel().catch(()=>{});throw error;}finally{reader.releaseLock();}
      if(signal.aborted||size!==expected)throw Error('asset_length_error');
      const data=new Uint8Array(size);let offset=0;for(const chunk of chunks){data.set(chunk,offset);offset+=chunk.byteLength;}
      if(type.mime==='image/jpeg'&&(data[0]!==0xff||data[1]!==0xd8||data[2]!==0xff)||type.mime==='image/webp'&&(new TextDecoder().decode(data.slice(0,4))!=='RIFF'||new TextDecoder().decode(data.slice(8,12))!=='WEBP')||type.mime==='video/mp4'&&new TextDecoder().decode(data.slice(4,8))!=='ftyp')throw Error('asset_format_error');
      if(type.mime==='text/plain')new TextDecoder('utf-8',{fatal:true}).decode(data);
      if(type.mime==='audio/mpeg'&&!(new TextDecoder().decode(data.slice(0,3))==='ID3'||data[0]===0xff&&(data[1]&0xe0)===0xe0))throw Error('asset_format_error');
      if(type.mime==='application/pdf'&&new TextDecoder().decode(data.slice(0,5))!=='%PDF-')throw Error('asset_format_error');
      if(['application/vnd.openxmlformats-officedocument.wordprocessingml.document','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','application/vnd.android.package-archive'].includes(type.mime)&&!(data[0]===0x50&&data[1]===0x4b&&data[2]===3&&data[3]===4))throw Error('asset_format_error');
      return new Blob([data],{type:type.mime});
    }
    function renderMedia(entry){
      if(entry.renderedMediaStatus===entry.mediaStatus)return;entry.renderedMediaStatus=entry.mediaStatus;
      const container=entry.node?.querySelector('[data-sct-media-content]');if(!container)return;container.replaceChildren();
      if(entry.mediaStatus!=='ready'){
        const text=document.createElement('p');text.className='os-chatbot-test-rich-notice';text.textContent=entry.mediaStatus==='error'?entry.mediaError||'Não foi possível carregar este arquivo. Envie uma nova mensagem para tentar novamente.':'Carregando arquivo…';container.appendChild(text);return;
      }
      const value=entry.message;let element;
      if(value.kind==='image'||value.kind==='sticker'){element=document.createElement('img');element.src=entry.mediaURL;element.alt=value.caption||mediaTypes[value.kind].label;element.loading='lazy';element.decoding='async';if(value.kind==='sticker')element.className='os-chatbot-test-sticker';}
      else if(value.kind==='video'||value.kind==='ptv'){element=document.createElement('video');element.src=entry.mediaURL;element.controls=true;element.preload='metadata';element.setAttribute('playsinline','');element.setAttribute('aria-label',value.caption||mediaTypes[value.kind].label);if(entry.thumbnailURL)element.poster=entry.thumbnailURL;if(value.kind==='ptv')element.className='os-chatbot-test-ptv';}
      else if(value.kind==='audio'){element=document.createElement('audio');element.src=entry.mediaURL;element.controls=true;element.preload='metadata';element.setAttribute('aria-label','Áudio do template');}
      else{element=document.createElement('a');element.href=entry.mediaURL;element.download=value.asset.file_name.split(/[\\/]/).pop().replace(/[\u0000-\u001f\u007f]/g,'')||'arquivo.txt';element.className='os-button os-outline';element.textContent='Baixar '+(value.asset.file_name||'arquivo de texto');}
      container.appendChild(element);
    }
    async function loadMedia(entry){
      const epoch=state.epoch,contextId=entry.contextId,abort=new AbortController();mediaReads.add(abort);mediaRunning++;entry.mediaStatus='loading';renderMedia(entry);
      const timer=setTimeout(()=>abort.abort(),45000);
      try{
        const original=await readAsset(entry.message.asset,'original',abort.signal);let thumbnail=null;
        if(!current(epoch,contextId)||abort.signal.aborted)return;
        if(['video','ptv'].includes(entry.message.kind)&&entry.message.asset.thumbnail_url){try{thumbnail=await readAsset(entry.message.asset,'thumbnail',abort.signal);}catch{if(abort.signal.aborted)return;}}
        if(!current(epoch,contextId)||abort.signal.aborted)return;
        entry.mediaURL=URL.createObjectURL(original);blobs.add(entry.mediaURL);if(thumbnail){entry.thumbnailURL=URL.createObjectURL(thumbnail);blobs.add(entry.thumbnailURL);}entry.mediaStatus='ready';renderMedia(entry);
      }catch{if(current(epoch,contextId)){entry.mediaStatus='error';renderMedia(entry);}}
      finally{clearTimeout(timer);mediaReads.delete(abort);mediaRunning--;if(current(epoch,contextId)&&entry.mediaStatus==='loading'){entry.mediaStatus='error';renderMedia(entry);}if(state.mounted)queueMedia();}
    }
    function queueMedia(){
      if(!state.mounted)return;
      for(const entry of state.history){
        if(entry.message.type!=='media'||entry.mediaStatus!=='queued'||entry.contextId!==state.contextId)continue;
        if(state.data?.capabilities.private_media!==true){entry.mediaStatus='error';entry.mediaError='A prévia privada deste arquivo está indisponível.';renderMedia(entry);continue;}
        if(mediaRunning>=2)break;void loadMedia(entry);
      }
    }
    function syncURL(){
      if(!state.mounted)return;
      try{const url=new URL(location.href);if(state.selected)url.searchParams.set('device_id',state.selected);else url.searchParams.delete('device_id');if(state.initialRule)url.searchParams.set('reply_id',state.initialRule);else url.searchParams.delete('reply_id');history.replaceState(history.state,'',url.pathname+url.search);}catch{}
    }
    function invalidateContext(){
      state.contextId=crypto.randomUUID();state.pending=null;state.keywords=null;clearTimeout(menuTimer);menuTimer=null;
      for(const read of mediaReads)read.abort();
      for(const entry of state.history)if(entry.message.type==='media'&&['loading','queued'].includes(entry.mediaStatus)){entry.mediaStatus='error';entry.mediaError='A prévia deste arquivo foi interrompida.';}
    }
    function updateComposer(){
      const enabled=canSimulate()&&!state.pending;
      ui.input.disabled=!enabled;ui.send.disabled=!enabled||!state.draft.trim()||state.draft.length>400;
      if(ui.input.value!==state.draft)ui.input.value=state.draft;
      ui.characters.textContent=state.draft.length+'/400';ui.inputError.textContent=state.inputError;
      ui.input.setAttribute('aria-invalid',String(!!state.inputError));
    }
    function menuReason(entry){
      if(entry.contextId!==state.contextId||entry.deviceId!==state.selected)return 'Esta lista pertence a uma conversa anterior. Envie uma nova mensagem para continuar.';
      if(Date.parse(entry.message.expires_at)<=Date.now())return 'Esta lista expirou. Envie uma nova mensagem para continuar.';
      if(!canSimulate())return 'Selecione um dispositivo disponível para continuar.';
      return '';
    }
    function messageBody(entry){
      const value=entry.message;
      if(['text','unavailable'].includes(value.type))return '<p>'+esc(value.text)+'</p>';
      if(value.type==='list')return '<div class="os-chatbot-test-list"><strong>'+esc(value.title)+'</strong><p>'+esc(value.description)+'</p>'+(value.footer?'<small>'+esc(value.footer)+'</small>':'')+'<details><summary>'+esc(value.button_text)+'</summary>'+value.sections.map(section=>'<section>'+(section.title?'<h3>'+esc(section.title)+'</h3>':'')+section.rows.map(row=>'<button type="button" class="os-chatbot-test-menu-row" data-sct-action="select-row" data-sct-entry="'+entry.id+'" data-sct-row="'+row.id+'"><span>'+esc(row.title)+'</span>'+(row.description?'<small>'+esc(row.description)+'</small>':'')+'</button>').join('')+'</section>').join('')+'</details><p class="os-chatbot-test-rich-notice" data-sct-menu-notice hidden></p></div>';
      if(value.type==='media')return '<div class="os-chatbot-test-media"><span class="os-chatbot-test-media-label">'+esc(mediaTypes[value.kind].label)+'</span><div data-sct-media-content><p class="os-chatbot-test-rich-notice">Carregando arquivo…</p></div>'+(value.caption?'<p>'+esc(value.caption)+'</p>':'')+'</div>';
      if(value.type==='location')return '<div class="os-chatbot-test-location"><strong>Localização</strong><p>Latitude: '+esc(value.latitude)+'<br>Longitude: '+esc(value.longitude)+'</p></div>';
      if(value.type==='carousel')return '<div class="os-chatbot-test-carousel"><p>'+esc(value.body)+'</p><div class="os-chatbot-test-carousel-cards">'+value.cards.map(card=>'<section><strong>'+esc(card.title)+'</strong><p>'+esc(card.body)+'</p>'+(card.button_text?'<span class="os-chatbot-test-declared-button">'+esc(card.button_text)+'</span>':'')+'</section>').join('')+'</div><p class="os-chatbot-test-rich-notice">'+esc(value.notice)+'</p></div>';
      if(value.type==='external_request')return '<div class="os-chatbot-test-external"><strong>'+esc(value.method)+' · URL</strong><code>'+esc(value.url)+'</code><p>'+esc(value.text)+'</p>'+(value.custom_template?'<p>'+esc(value.custom_template)+'</p>':'')+(value.as_file?'<small>Retorno configurado como arquivo de texto.</small>':'')+'</div>';
      return '';
    }
    function renderHistory(scrollToEnd=false){
      const keepBottom=ui.scroll.scrollHeight-ui.scroll.scrollTop-ui.scroll.clientHeight<64;
      for(const entry of state.history){
        if(!entry.node){const node=document.createElement('li');node.className='os-chatbot-test-message';node.dataset.sctMessage=entry.id;node.dataset.direction=entry.direction;node.dataset.type=entry.message.type;
          node.innerHTML='<div class="os-chatbot-test-message-meta"><span>'+esc(entry.direction==='out'?'Você':entry.inactive?'Chatbot · Resposta inativa':'Chatbot')+'</span><time datetime="'+new Date(entry.at).toISOString()+'">'+esc(timeLabel(entry.at))+'</time></div>'+messageBody(entry);entry.node=node;ui.messages.appendChild(node);}
        if(entry.message.type==='list'){
          const reason=menuReason(entry);for(const row of entry.node.querySelectorAll('[data-sct-row]'))row.disabled=!!reason||!!state.pending;
          const notice=entry.node.querySelector('[data-sct-menu-notice]');notice.textContent=reason;notice.hidden=!reason;
        }
        if(entry.message.type==='media')renderMedia(entry);
      }
      ui.empty.hidden=state.history.length>0;ui.count.textContent=state.history.length+' '+(state.history.length===1?'mensagem':'mensagens');
      if(scrollToEnd||keepBottom)ui.scroll.scrollTop=ui.scroll.scrollHeight;
    }
    function scheduleMenus(){
      clearTimeout(menuTimer);menuTimer=null;
      const expiry=state.history.filter(entry=>entry.message.type==='list'&&entry.contextId===state.contextId).map(entry=>Date.parse(entry.message.expires_at)).filter(value=>value>Date.now()).sort((a,b)=>a-b)[0];
      if(expiry)menuTimer=setTimeout(()=>{if(state.mounted){renderHistory();scheduleMenus();}},Math.min(2147483647,Math.max(1,expiry-Date.now()+10)));
    }
    function render(scrollToEnd=false){
      if(!state.mounted)return;
      const device=selected();ui.device.disabled=state.loading||!state.loaded;ui.device.value=state.selected;
      ui.heading.textContent=device?device.name+(device.phone_e164?' · '+device.phone_e164:''):'Escolha um dispositivo para começar';
      ui.pending.hidden=!state.pending;ui.clear.disabled=!state.history.length&&!state.pending&&!state.initialRule;
      ui.feedback.textContent=state.feedback;ui.feedback.hidden=!state.feedback;
      if(state.feedback&&!state.loaded&&!state.loading)ui.feedback.insertAdjacentHTML('beforeend',button('Tentar novamente','reload','refresh'));
      ui.keywords.hidden=state.keywords===null;
      ui.keywordList.innerHTML=(state.keywords||[]).map((keyword,index)=>'<button type="button" class="os-chatbot-test-keyword" data-sct-action="keyword" data-sct-keyword="'+index+'"'+(!canSimulate()||state.pending?' disabled':'')+'><span>'+esc(keyword.keyword)+'</span><small>'+esc(matchLabels[keyword.match_mode])+'</small></button>').join('')||(state.keywords!==null?'<p class="os-chatbot-test-rich-notice">Nenhuma palavra-chave disponível.</p>':'');
      updateComposer();renderHistory(scrollToEnd);scheduleMenus();host.setAttribute('aria-busy',String(state.loading));
    }
    async function load(){
      if(!state.mounted||state.loading)return;
      const epoch=state.epoch,read=++state.read;state.loading=true;render();
      try{
        const data=bootstrapDTO(await request());if(!current(epoch)||state.read!==read)return;
        state.data=data;state.loaded=true;
        if(state.initial){
          const initial=uuid(bridge.initialDeviceId)?bridge.initialDeviceId:'';
          state.selected=data.devices.some(item=>item.id===initial)?initial:state.initialRule&&!bridge.initialDeviceId&&data.devices.length===1?data.devices[0].id:'';
          if(bridge.initialDeviceId&&!state.selected)state.feedback='O dispositivo informado não está disponível. Escolha um dispositivo para continuar.';
          state.initial=false;
        }else if(state.selected&&!selected()){invalidateContext();state.selected='';state.feedback='Este dispositivo não está mais disponível. Escolha outro para continuar.';}
        ui.device.innerHTML='<option value="">Escolha um dispositivo...</option>'+data.devices.map(item=>'<option value="'+item.id+'">'+esc(item.name)+'</option>').join('');
        if(!data.devices.length)state.feedback='Nenhum dispositivo disponível. Cadastre um na aba Dispositivos para começar.';
        else if(!data.capabilities.simulate)state.feedback='O teste do Chatbot está indisponível para esta empresa no momento.';
        syncURL();
      }catch(error){if(current(epoch)&&state.read===read){state.loaded=false;state.feedback=errorMessage(error);ui.device.innerHTML='<option value="">Escolha um dispositivo...</option>';}}
      finally{if(current(epoch)&&state.read===read){state.loading=false;render();if(state.initialRule&&canSimulate())void runInitial();}}
    }
    function append(message,direction,{inactive=false}={}){
      const entry={id:crypto.randomUUID(),message,deviceId:state.selected,contextId:state.contextId,direction,inactive,at:Date.now(),node:null,mediaStatus:message.type==='media'?'queued':null};state.history.push(entry);return entry;
    }
    async function simulate(input,displayText){
      if(!canSimulate()||state.pending)return;
      if(input.type==='text'&&(!validText(input.text)||!input.text.trim()))return;
      const epoch=state.epoch,contextId=state.contextId,payload={request_id:crypto.randomUUID(),context_id:contextId,device_id:state.selected,expected_chatbot_revision:state.data.chatbot_revision,input};
      state.feedback='';state.inputError='';state.pending=payload.request_id;state.draft='';
      const outgoing=displayText!==undefined?append({type:'text',text:displayText},'out'):null;render(true);
      try{
        const raw=await request('/simulate','POST',payload);if(!current(epoch,contextId)||state.pending!==payload.request_id)return;
        const value=simulationDTO(raw,payload,state.data.limits.max_outputs);
        if(outgoing){outgoing.message.text=value.input.text;outgoing.node?.querySelector('p')?.replaceChildren(document.createTextNode(value.input.text));}
        else append({type:'text',text:value.input.text},'out');
        for(const message of value.messages)append(message,'in',{inactive:value.matched_rule?.enabled===false});
        state.keywords=value.keywords;state.pending=null;render(true);ui.input.focus({preventScroll:true});queueMedia();
      }catch(error){
        if(!current(epoch,contextId)||state.pending!==payload.request_id)return;
        state.pending=null;state.feedback=errorMessage(error);if(input.type==='text')state.draft=input.text;
        if(Number(error?.status)===403||Number(error?.status)===401){invalidateContext();state.loaded=false;}
        if(Number(error?.status)===409||Number(error?.status)===410){invalidateContext();state.feedback='As respostas do Chatbot ou esta lista mudaram. Confira os dados atuais e envie uma nova mensagem.';render();void load();}
        else render();
      }
    }
    function runInitial(){
      const id=state.initialRule;if(!id||!canSimulate()||state.pending)return;
      state.initialRule=null;syncURL();return simulate({type:'rule',rule_id:id});
    }
    function sendDraft(){
      if(!canSimulate()||state.pending)return;
      const text=state.draft;
      if(!text.trim()){state.inputError='Digite uma mensagem para testar.';updateComposer();return;}
      if(!validText(text)){state.inputError=text.length>400?'Use até 400 caracteres na mensagem de teste.':'Revise os caracteres da mensagem.';updateComposer();return;}
      void simulate({type:'text',text:text.trim()},text.trim());
    }
    function clearChat(){
      invalidateContext();state.initialRule=null;state.history=[];state.feedback='';state.inputError='';ui.messages.replaceChildren();for(const url of blobs)URL.revokeObjectURL(url);blobs.clear();syncURL();render();if(canSimulate())ui.input.focus({preventScroll:true});
    }
    function setFullscreen(value,focus=true){
      if(state.fullscreen===value)return;state.fullscreen=value;host.classList.toggle('os-is-fullscreen',value);ui.fullscreen.innerHTML=icon(value?'collapse':'expand')+'<span>'+(value?'Sair da tela cheia':'Entrar em tela cheia')+'</span>';ui.fullscreen.setAttribute('aria-pressed',String(value));
      if(value){overflowBefore=document.body.style.overflow;document.body.style.overflow='hidden';host.setAttribute('role','dialog');host.setAttribute('aria-modal','true');host.setAttribute('aria-label','Testar Chatbot');for(let node=host;node.parentElement;node=node.parentElement){for(const sibling of node.parentElement.children)if(sibling!==node&&!['SCRIPT','STYLE','LINK'].includes(sibling.tagName)){inertBefore.push([sibling,sibling.inert]);sibling.inert=true;}if(node.parentElement===document.body)break;}if(focus)(ui.input.disabled?ui.fullscreen:ui.input).focus({preventScroll:true});}
      else{document.body.style.overflow=overflowBefore;for(const [node,value] of inertBefore)node.inert=value;inertBefore=[];host.removeAttribute('role');host.removeAttribute('aria-modal');host.removeAttribute('aria-label');if(focus)ui.fullscreen.focus({preventScroll:true});}
    }
    function click(event){
      const node=event.target.closest('[data-sct-action]');if(!node||!host.contains(node)||node.disabled||!state.mounted)return;event.preventDefault();
      const action=node.dataset.sctAction;
      if(action==='clear'){clearChat();return;}
      if(action==='fullscreen'){setFullscreen(!state.fullscreen);return;}
      if(action==='reload'){state.feedback='';void load();return;}
      if(action==='keyword'){const keyword=state.keywords?.[Number(node.dataset.sctKeyword)];if(keyword){if(!validText(keyword.keyword)){state.feedback='Esta palavra-chave excede o limite de 400 caracteres do teste. Abra a resposta pelo atalho Testar Chatbot para conferi-la.';render();return;}void simulate({type:'text',text:keyword.keyword.trim()},keyword.keyword.trim());}return;}
      if(action==='select-row'){
        const entry=state.history.find(item=>item.id===node.dataset.sctEntry);if(!entry||menuReason(entry)||state.pending)return;
        const row=entry.message.sections.flatMap(section=>section.rows).find(item=>item.id===node.dataset.sctRow);if(row)void simulate({type:'selection',selection_token:entry.message.selection_token,row_id:row.id},row.title);
      }
    }
    function change(event){
      if(event.target!==ui.device||!state.mounted)return;const value=ui.device.value;
      if(value===state.selected||value&&!state.data?.devices.some(item=>item.id===value))return;
      invalidateContext();state.selected=value;state.feedback='';state.inputError='';syncURL();render();if(state.initialRule&&canSimulate())void runInitial();
    }
    function inputChanged(event){if(event.target===ui.input&&state.mounted){state.draft=ui.input.value;state.inputError='';updateComposer();}}
    function submit(event){if(event.target===ui.form){event.preventDefault();sendDraft();}}
    function keydown(event){
      if(!state.mounted)return;
      if(event.key==='Escape'&&state.fullscreen){event.preventDefault();setFullscreen(false);return;}
      if(event.target===ui.input&&event.key==='Enter'){if(event.isComposing||event.keyCode===229)return;event.preventDefault();sendDraft();return;}
      if(event.key==='Tab'&&state.fullscreen){const nodes=[...host.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),summary,a[href]')].filter(node=>!node.closest('[hidden]'));const first=nodes[0],last=nodes.at(-1);if(!nodes.length)return;if(event.shiftKey&&(document.activeElement===first||!host.contains(document.activeElement))){event.preventDefault();last.focus();}else if(!event.shiftKey&&(document.activeElement===last||!host.contains(document.activeElement))){event.preventDefault();first.focus();}}
    }
    host.addEventListener('click',click);host.addEventListener('change',change);host.addEventListener('input',inputChanged);host.addEventListener('submit',submit);document.addEventListener('keydown',keydown);
    const controller={load,unmount(){if(!state.mounted)return;setFullscreen(false,false);state.mounted=false;state.epoch++;invalidateContext();for(const url of blobs)URL.revokeObjectURL(url);blobs.clear();state.history=[];state.data=null;state.draft='';state.initialRule=null;host.removeEventListener('click',click);host.removeEventListener('change',change);host.removeEventListener('input',inputChanged);host.removeEventListener('submit',submit);document.removeEventListener('keydown',keydown);host.replaceChildren();host.classList.remove('os-chatbot-test');host.removeAttribute('aria-busy');},getState:()=>({loaded:state.loaded,pending:!!state.pending,deviceId:state.selected,messages:state.history.length,fullscreen:state.fullscreen,contextId:state.contextId})};
    active=controller;render();void load();return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionSubscriptionChatbotTest=Object.freeze({mount,unmount});
})();
