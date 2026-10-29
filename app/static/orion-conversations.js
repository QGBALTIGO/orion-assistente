/* Uses the panel's authenticated tenant bridge. No socket or provider secrets. */
(function(){
  'use strict';let active=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const modes={ai:'IA ativa',human:'Atendimento humano',waiting:'Aguardando cliente',paused:'Pausada',closed:'Encerrada'};
  const receipts={received:'Recebida',queued:'Na fila',sending:'Envio em verificação',uncertain:'Resultado incerto',accepted:'Aceita pelo canal',delivered:'Entregue',read:'Lida',failed:'Não enviada ou recusada pelo canal'};
  function unmount(){if(active){active.alive=false;active.abort.abort();clearInterval(active.timer);if(active.layoutFrame)window.cancelAnimationFrame?.(active.layoutFrame);active.host.style.removeProperty('--os-central-workspace-height');active.host.style.removeProperty('--os-central-menu-max-height');active.host.removeAttribute('data-central-compact');active=null;}}
  async function mount({host,request,tenantId,canWrite=false,onCustomer,toolsEnabled=false}){
    unmount();const s={host,alive:true,abort:new AbortController(),channels:[],items:[],detail:null,selected:null,channel:'',mode:'',q:'',unread:false,offset:0,more:false,busy:false,seq:0,drafts:new Map(),preserved:new Map(),replies:[],pending:[],positions:new Map(),renderedKey:'',listScroll:0,filtersOpen:false,searchDraft:'',openSections:new Set(),replySelection:'',detailError:null};active=s;
    const current=()=>active===s&&s.alive&&host.isConnected;
    const path=v=>'/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/inbox'+v;
    const selectedPath=v=>path('/channels/'+encodeURIComponent(s.selected.channel_id)+v);
    const key=()=>s.selected?s.selected.channel_id+':'+s.selected.contact_e164:'';
    const draft=()=>s.drafts.get(key())||{text:'',operation:null};
    const notice=(msg,error=false)=>{const n=host.querySelector('[data-central-notice]');if(n){n.textContent=msg;n.setAttribute('role',error?'alert':'status');}};
    const btn=(label,action,disabled=false)=>'<button type="button" class="os-button os-outline" data-central-action="'+action+'" '+(disabled?'disabled':'')+'>'+esc(label)+'</button>';
  const glyphs={
    refresh:'M20 11a8 8 0 0 0-15-3M4 4v5h5M4 13a8 8 0 0 0 15 3m1 4v-5h-5',
    search:'M21 21l-4-4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0',
    back:'m15 18-6-6 6-6',
    info:'M12 11v6M12 7h.01',
    more:'M5 12h.01M12 12h.01M19 12h.01',
    check:'m5 12 4 4 10-10',
    message:'M6 3h12a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3ZM8 8h8M8 12h5',
    send:'M22 2 11 13M22 2 15 22l-4-9-9-4 20-7Z',
    chevron:'m9 6 6 6-6 6',
    plus:'M12 5v14M5 12h14',
    minus:'M5 12h14'
  };
  const ico=name=>'<svg class="os-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="'+(glyphs[name]||glyphs.info)+'"/></svg>';
  const initials=value=>String(value||'?').trim().split(/\s+/).slice(0,2).map(part=>Array.from(part)[0]||'').join('').toLocaleUpperCase('pt-BR');
  const clock=value=>{if(!value)return '';const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});};
  const modeClass=mode=>['human','waiting'].includes(mode)?'human':mode==='ai'?'ai':mode==='paused'?'paused':'neutral';
  const contactLabel=value=>{const raw=String(value||'');if(!/^\+55[1-9][0-9]{9,10}$/.test(raw))return raw;const digits=raw.slice(3),split=digits.length===11?7:6;return '('+digits.slice(0,2)+') '+digits.slice(2,split)+'-'+digits.slice(split);};
  const disclosureLabel=label=>'<span class="os-detail-toggle-icon" aria-hidden="true">'+ico('plus')+ico('minus')+'</span><span>'+esc(label)+'</span>';
    function closeControls(restoreFocus=false){
      const menu=host.querySelector('.os-central-control-menu');if(!menu?.open)return;
      menu.open=false;s.openSections.delete('os-central-control-menu');
      if(restoreFocus)menu.querySelector('summary')?.focus({preventScroll:true});
    }
    function updateLayout(){
      if(!current())return;
      const shell=host.querySelector('.os-central-shell');if(!shell)return;
      const timeline=host.querySelector('.os-central-thread-scroll'),atBottom=!!timeline&&timeline.scrollHeight-timeline.clientHeight-timeline.scrollTop<75,scrollTop=timeline?.scrollTop;
      const viewport=window.visualViewport,height=viewport?.height||window.innerHeight,offset=viewport?.offsetTop||0;
      host.toggleAttribute('data-central-compact',height<460);
      const top=Math.max(offset,shell.getBoundingClientRect().top),available=Math.floor(offset+height-top-12);
      if(available>0)host.style.setProperty('--os-central-workspace-height',Math.min(790,available)+'px');
      const editor=host.querySelector('#centralText');
      if(editor){editor.style.height='auto';editor.style.height=Math.min(height<460?72:120,Math.max(height<460?42:58,editor.scrollHeight+editor.offsetHeight-editor.clientHeight))+'px';}
      const options=host.querySelector('.os-central-control-menu[open] .os-central-control-options'),composer=host.querySelector('.os-central-compose');
      if(options&&composer){
        const limit=Math.min(composer.getBoundingClientRect().top-8,offset+height-12);
        host.style.setProperty('--os-central-menu-max-height',Math.max(0,Math.floor(limit-options.getBoundingClientRect().top))+'px');
      }else host.style.removeProperty('--os-central-menu-max-height');
      if(timeline)timeline.scrollTop=atBottom?timeline.scrollHeight:scrollTop;
      if(s.focusTarget==='thread'&&s.detail?.control){host.querySelector('.os-central-thread-header')?.focus({preventScroll:true});s.focusTarget=null;}
      if(s.focusTarget==='list'){[...host.querySelectorAll('[data-central-phone]')].find(node=>node.dataset.centralChannel+':'+node.dataset.centralPhone===s.returnTo)?.focus({preventScroll:true});s.focusTarget=null;}
      if(s.restoreControlsFocus){host.querySelector('.os-central-control-menu>summary')?.focus({preventScroll:true});s.restoreControlsFocus=false;}
    }
    function scheduleLayout(){
      if(!current())return;
      if(s.layoutFrame)window.cancelAnimationFrame?.(s.layoutFrame);
      if(window.requestAnimationFrame)s.layoutFrame=window.requestAnimationFrame(()=>{s.layoutFrame=null;updateLayout();});
      else updateLayout();
    }
    function render(){
      if(!current())return;
      if(!s.focusTarget&&s.renderedKey===key()&&host.contains(document.activeElement)&&document.activeElement?.matches('.os-central-control-menu>summary'))s.restoreControlsFocus=true;
      const previousList=host.querySelector('.os-central-thread-list');
      if(previousList)s.listScroll=previousList.scrollTop;
      const previousTimeline=host.querySelector('.os-central-thread-scroll');
      if(previousTimeline&&s.renderedKey){
        const atBottom=previousTimeline.scrollHeight-previousTimeline.clientHeight-previousTimeline.scrollTop<75;
        s.positions.set(s.renderedKey,{top:previousTimeline.scrollTop,atBottom});
      }
      const oldFilters=host.querySelector('.os-central-filters');
      if(oldFilters)s.filtersOpen=oldFilters.open;
      const oldSearch=host.querySelector('#centralSearch');
      if(oldSearch)s.searchDraft=oldSearch.value;
      if(s.renderedKey!==key())s.openSections=new Set();
      else s.openSections=new Set([...host.querySelectorAll('details[open]')].map(el=>[
        'os-central-control-menu','os-central-quick-reply','os-central-history-tools','os-central-preserved','os-central-pending-notices'
      ].find(name=>el.classList.contains(name))).filter(Boolean));
      const oldReply=host.querySelector('[data-central-reply]');if(oldReply)s.replySelection=oldReply.value;
      const findValue=s.renderedKey===key()?(host.querySelector('#centralFind')?.value||''):'';
      const c=s.detail?.control||null;
      const owned=!!s.account&&!!c&&['human','waiting'].includes(c.mode)&&c.assigned_account_id===s.account;
      const canControl=!!c&&!!s.account&&canWrite&&!s.busy&&!c.pending_operation;
      const selected=s.items.find(x=>s.selected&&x.channel_id===s.selected.channel_id&&x.contact_e164===s.selected.contact_e164)||s.selected;
      const name=selected?.name||contactLabel(selected?.contact_e164)||'Conversa';
      const totalUnread=s.items.reduce((sum,x)=>sum+Number(x.unread_count||0),0);
      const modeBtn=(id,label,visible,enabled)=>'<button type="button" class="os-button os-outline os-central-mode-action" data-central-action="mode-'+id+'" '+(!visible?'hidden ':'')+(!enabled?'disabled':'')+'>'+esc(label)+'</button>';
      const list=s.items.map(x=>{
        const chosen=!!selected&&selected.channel_id===x.channel_id&&selected.contact_e164===x.contact_e164;
        const unread=Math.max(0,Number(x.unread_count||0));
        return '<li class="os-central-thread-item"><button type="button" class="os-central-thread-item-button" data-central-phone="'+esc(x.contact_e164)+'" data-central-channel="'+esc(x.channel_id)+'" aria-pressed="'+chosen+'"><span class="os-central-avatar" aria-hidden="true">'+esc(initials(x.name||x.contact_e164))+'</span><span class="os-central-thread-copy"><span class="os-central-thread-title"><strong>'+esc(x.name||x.contact_e164)+'</strong><time>'+esc(clock(x.updated_at))+'</time></span><span class="os-central-thread-preview">'+esc((x.preview||'Sem prévia de mensagem').slice(0,120))+'</span><span class="os-central-thread-meta"><span class="os-central-mini-state os-central-mini-state-'+modeClass(x.mode)+'">'+esc(modes[x.mode]||'Em atendimento')+'</span><small>'+esc(x.provider==='qr'?'WhatsApp QR':'WhatsApp oficial')+'</small></span></span>'+(unread?'<span class="os-central-unread" aria-label="'+unread+' mensagens não lidas">'+unread+'</span>':'')+'</button></li>';
      }).join('');
      const activeFilters=Number(!!s.channel)+Number(!!s.mode)+Number(s.unread);
      const filters='<form data-central-search class="os-central-search-form" role="search">'+
        '<div class="os-central-search-top"><label class="os-central-search-field" for="centralSearch">'+ico('search')+
        '<span class="os-sr-only">Pesquisar conversas</span><input id="centralSearch" name="q" type="search" maxlength="100" placeholder="Nome, telefone ou mensagem" value="'+esc(s.searchDraft)+'"></label>'+
        '<button class="os-button os-central-search-button" type="submit" '+(s.busy?'disabled':'')+'>Buscar</button></div>'+
        '<details class="os-central-filters"'+(s.filtersOpen?' open':'')+'><summary><span>Filtros</span>'+
        (activeFilters?'<small>'+activeFilters+' ativo'+(activeFilters===1?'':'s')+'</small>':'')+
        '<span class="os-central-filter-chevron" aria-hidden="true">'+ico('chevron')+'</span></summary>'+
        '<div class="os-central-filter-content"><div class="os-central-filter-row '+(s.channels.length>1?'':'os-central-single-filter')+'">'+
        (s.channels.length>1?'<label class="os-central-filter-field"><span>Canal</span><select name="channel"><option value="">Todos os canais</option>'+
          s.channels.map(x=>'<option value="'+esc(x.id)+'" '+(s.channel===x.id?'selected':'')+'>'+
          esc((x.provider==='qr'?'QR · ':'Meta · ')+x.label+(x.enabled?'':' · indisponível'))+'</option>').join('')+'</select></label>':'')+
        '<label class="os-central-filter-field"><span>Atendimento</span><select name="mode"><option value="">Todos</option>'+
        Object.entries(modes).map(([id,label])=>'<option value="'+esc(id)+'" '+(s.mode===id?'selected':'')+'>'+esc(label)+'</option>').join('')+'</select></label></div>'+
        '<div class="os-central-filter-footer"><label class="os-central-unread-toggle"><input type="checkbox" name="unread" '+(s.unread?'checked':'')+'><span>Somente não lidas</span></label>'+
        '<div><button type="button" class="os-central-clear-filters" data-central-action="clear-filters">Limpar</button>'+
        '<button class="os-button os-central-filter-submit" type="submit" '+(s.busy?'disabled':'')+'>Aplicar</button></div></div></div></details>'+
      '</form>';
      const newConversation=(canWrite&&s.account?'<button type="button" class="os-button os-outline" data-central-action="new" '+(s.busy?'disabled':'')+'>Nova conversa</button>':'')+
        (s.newOpen?'<form data-central-new-contact class="os-form os-central-new-form"><div class="os-field"><label for="centralNewPhone">WhatsApp com DDD</label><input id="centralNewPhone" name="phone" type="tel" autocomplete="tel" maxlength="24" required value="'+esc(s.newPhone||'')+'"></div><div class="os-field"><label for="centralNewChannel">Canal</label><select id="centralNewChannel" name="channel">'+s.channels.filter(x=>x.provider==='qr'&&x.enabled).map(x=>'<option value="'+esc(x.id)+'">'+esc(x.label)+'</option>').join('')+'</select></div><p class="os-help">Abrir a conversa não envia mensagens. Use apenas contatos autorizados no canal.</p><button type="submit" class="os-button" '+(s.busy?'disabled':'')+'>Abrir conversa</button></form>':'');
      const listPanel='<aside class="os-central-list-panel" aria-label="Conversas da empresa">'+
        '<header class="os-central-list-header"><div><h2>Conversas</h2><p>'+s.items.length+' conversa'+(s.items.length===1?'':'s')+(totalUnread?' · '+totalUnread+' '+(totalUnread===1?'mensagem não lida':'mensagens não lidas'):'')+'</p></div><button type="button" class="os-icon-button os-central-refresh" data-central-action="refresh" aria-label="Atualizar conversas" title="Atualizar conversas" '+(s.busy?'disabled':'')+'>'+ico('refresh')+'</button></header>'+
        newConversation+filters+
        '<ul class="os-central-thread-list" aria-label="Lista de conversas">'+(list||'<li class="os-central-empty-list">'+ico('message')+'<strong>Nenhuma conversa encontrada</strong><span>Revise os filtros ou aguarde novas mensagens.</span></li>')+'</ul>'+
        '<footer class="os-central-pagination"><button type="button" class="os-button os-outline" data-central-action="previous" '+(s.offset===0||s.busy?'disabled':'')+'>Anterior</button><span>Página '+(Math.floor(s.offset/50)+1)+'</span><button type="button" class="os-button os-outline" data-central-action="next" '+(!s.more||s.busy?'disabled':'')+'>Próxima</button></footer></aside>';
      const controlLabel=c?({human:'Humano',waiting:'Em espera',ai:'IA ativa',paused:'Pausado',closed:'Encerrado'}[c.mode]||'Em atendimento'):'Sem status';
      const ownerText=c?(!s.account?'Somente consulta':owned?'Atendimento com você':c.assigned_account_id?'Com outro atendente':c.mode==='ai'?'Orion responde automaticamente':c.mode==='closed'?'Atendimento encerrado':c.mode==='paused'?'Atendimento pausado':'Aguardando alguém assumir'):'';
      const primary=modeBtn('human','Assumir atendimento',!!c&&!owned&&!!s.account,canControl&&!owned);
      const controlOptions=
        modeBtn('waiting','Aguardar cliente',owned&&c.mode!=='waiting',canControl&&owned&&c.mode!=='waiting')+
        modeBtn('ai','Retomar IA',!!c&&c.mode!=='ai',canControl&&c.mode!=='ai')+
        modeBtn('paused','Pausar atendimento',!!c&&c.mode!=='paused',canControl&&c.mode!=='paused')+
        modeBtn('closed','Encerrar atendimento',!!c&&c.mode!=='closed',canControl&&c.mode!=='closed')+
        (s.detail?.customer_id&&onCustomer?btn('Abrir ficha do cliente','customer',s.busy):'');
      const pending=c?.pending_operation?'<div class="os-central-pending" role="status">'+ico('info')+'<span>Há um envio em verificação. O controle da conversa fica protegido até existir um recibo seguro.</span>'+(s.channels.find(x=>x.id===s.selected?.channel_id)?.provider==='qr'?btn('Consultar recibo','reconcile',!canWrite||s.busy):'')+'</div>':'';
      const readTools='<section class="os-central-history-tools" aria-label="Ferramentas do histórico"><h3>Histórico</h3><div class="os-central-history-actions">'+
        (s.account&&s.detail?.items?.length&&Number(selected?.unread_count||0)>0?btn('Marcar como lida','read',s.busy):'')+
        (s.detail?.preserved_history_available?btn('Consultar histórico do dispositivo','preserved-history',s.busy):'')+
        '</div><label class="os-central-inthread-search" for="centralFind">'+ico('search')+'<span class="os-sr-only">Buscar nas mensagens carregadas</span><input id="centralFind" type="search" maxlength="100" placeholder="Buscar nesta conversa" value="'+esc(findValue)+'"></label></section>';
      const management='<details class="os-central-control-menu os-compact-disclosure"'+(s.openSections.has('os-central-control-menu')?' open':'')+'><summary aria-label="Detalhes do atendimento">'+disclosureLabel('Detalhes')+'</summary><div class="os-central-control-options"><h3>Atendimento</h3><div class="os-central-mode-options">'+controlOptions+'</div>'+readTools+'</div></details>';
      const historyTools=s.detail?.has_more?'<div class="os-central-timeline-tools">'+btn('Carregar mensagens anteriores','older',s.busy)+'</div>':'';
      const replies=s.replies.length&&s.detail?.customer_id?'<details class="os-central-quick-reply"'+(s.openSections.has('os-central-quick-reply')?' open':'')+'><summary>Usar resposta rápida</summary><div class="os-central-quick-reply-form"><label for="centralReplySelect">Modelo de resposta</label><div><select id="centralReplySelect" data-central-reply><option value="">Escolher resposta salva</option>'+s.replies.map(r=>'<option value="'+esc(r.id)+'" '+(s.replySelection===r.id?'selected':'')+'>'+esc(r.title)+'</option>').join('')+'</select>'+btn('Inserir no texto','prepare',s.busy)+'</div></div></details>':'';
      const composer='<footer class="os-central-compose">'+replies+
        '<form data-central-compose><label class="os-sr-only" for="centralText">Mensagem</label><textarea id="centralText" name="text" rows="2" maxlength="1200" placeholder="Escreva uma mensagem…" '+(!canWrite?'readonly':'')+'>'+esc(draft().text)+'</textarea><div class="os-central-compose-footer"><span class="os-central-composer-hint">'+esc(!s.sendEnabled?'O envio de mensagens está desativado.':!s.account?'Seu acesso está disponível somente para consulta.':!owned?'Assuma o atendimento para responder.':c?.pending_operation?'Aguarde a verificação do envio.':'Revise o texto antes de enviar.')+'</span><button type="submit" class="os-button" aria-label="'+(draft().operation?'Verificar envio':'Enviar mensagem')+'" '+(!canWrite||!owned||s.busy||!!c?.pending_operation||!s.sendEnabled?'disabled':'')+'>'+ico('send')+'<span>'+(draft().operation?'Verificar envio':'Enviar')+'</span></button></div></form></footer>';
      const thread=s.selected&&c?'<div class="os-central-thread-body">'+
        '<header class="os-central-thread-header" tabindex="-1" aria-label="Conversa com '+esc(name)+'"><button type="button" class="os-icon-button os-central-back" data-central-action="back" aria-label="Voltar para conversas">'+ico('back')+'</button><span class="os-central-avatar" aria-hidden="true">'+esc(initials(name))+'</span><div class="os-central-selected-copy"><h2 title="'+esc(name)+'">'+esc(name)+'</h2><p>'+esc(contactLabel(selected?.contact_e164))+'</p><small class="os-central-owner">'+esc(ownerText)+'</small></div><span class="os-central-state-badge os-central-state-'+modeClass(c.mode)+'">'+esc(controlLabel)+'</span></header>'+
        '<div class="os-central-control-bar"><div class="os-central-control-buttons">'+primary+management+'</div></div>'+
        pending+
        '<div class="os-central-search-active" hidden><span></span><button type="button" data-central-action="clear-find">Limpar busca</button></div>'+
        '<div class="os-central-thread-scroll">'+
        historyTools+
        '<ol class="os-central-messages" aria-label="Histórico da conversa">'+messagesHTML()+'</ol>'+
        preservedHTML()+pendingHTML()+
        '</div>'+composer+'</div>':
        s.selected?'<div class="os-central-loading" role="'+(s.detailError?'alert':'status')+'"><button type="button" class="os-icon-button os-central-back" data-central-action="back" aria-label="Voltar">'+ico('back')+'</button>'+
        '<p>'+esc(s.detailError||'Carregando histórico do atendimento…')+'</p>'+(s.detailError?btn('Tentar novamente','refresh'):'')+'</div>':
        '<div class="os-central-placeholder"><span class="os-central-placeholder-icon">'+ico('message')+'</span><h2>Suas conversas em um só lugar</h2><p>Selecione um contato à esquerda para consultar o histórico, acompanhar o atendimento e responder quando autorizado.</p></div>';
      host.innerHTML='<div class="os-central">'+(!s.account?'<div class="os-central-identity-notice" role="status">'+ico('info')+'<span>O administrador precisa estar habilitado na central para assumir atendimentos. A consulta continua disponível.</span></div>':'')+
        '<p class="os-central-notice" data-central-notice role="status" aria-live="polite"></p>'+
        '<div class="os-central-shell'+(s.selected?' os-central-show-thread':'')+'">'+listPanel+'<section class="os-central-thread-panel" aria-label="Atendimento selecionado">'+thread+'</section></div></div>';
      const updatedList=host.querySelector('.os-central-thread-list');
      if(updatedList)updatedList.scrollTop=s.listScroll||0;
      const updatedTimeline=host.querySelector('.os-central-thread-scroll');
      const identity=key();
      if(updatedTimeline){
        const position=s.positions.get(identity);
        if(identity===s.renderedKey&&position&&!position.atBottom)updatedTimeline.scrollTop=position.top;
        else updatedTimeline.scrollTop=updatedTimeline.scrollHeight;
      }
      s.renderedKey=identity;
      syncMessageSearch(findValue);
      scheduleLayout();
    }
    function messagesHTML(){
      let last='';
      const items=s.detail?.items||[];
      if(!items.length)return '<li class="os-central-empty-messages">Ainda não há mensagens registradas nesta conversa.</li>';
      return items.map(m=>{
        const when=new Date(m.created_at),day=Number.isNaN(when.getTime())?'Data não informada':when.toLocaleDateString('pt-BR');
        const heading=day!==last?'<li class="os-central-day"><span>'+esc(day)+'</span></li>':'';
        last=day;
        const from=m.source==='customer'?'Cliente':m.source==='manual'?'Atendente':m.source==='assistant'?'Orion IA':'Automação';
        const body=m.body||(m.kind==='audio'?'Áudio recebido · reprodução indisponível':'Mensagem sem texto');
        return heading+'<li class="os-central-message" data-direction="'+esc(m.direction)+'"><div class="os-central-message-meta"><span>'+esc(from)+'</span><time>'+esc(clock(m.created_at))+'</time></div><p>'+esc(body)+'</p><small class="os-central-receipt">'+esc(receipts[m.status]||m.status||'')+'</small></li>';
      }).join('');
    }
    function preservedHTML(){
      const rows=s.preserved.get(key());if(!rows)return '';
      return '<details open class="os-central-preserved"><summary>Histórico preservado no dispositivo</summary><p>Esta consulta não altera as contagens de leitura da central.</p><ol>'+rows.map(m=>'<li><small>'+esc(m.direction==='in'?'Recebida':'Enviada')+' · '+esc(new Date(m.at).toLocaleString('pt-BR'))+'</small><p class="os-inbox-text">'+esc(m.body)+'</p>'+(m.direction==='out'?'<small>'+esc(m.delivery==='read'||m.delivery==='played'?'Lida':m.delivery==='delivered'?'Entregue':'Sem confirmação de entrega')+'</small>':'')+'</li>').join('')+'</ol></details>';
    }
    function pendingHTML(){return s.pending.length?'<details class="os-central-pending-notices"'+(s.openSections.has('os-central-pending-notices')?' open':'')+'><summary>Avisos da equipe ('+s.pending.length+')</summary>'+window.OrionCommunication.pendingHTML(s.pending,canWrite,s.busy)+'</details>':'';}
    async function load({detail=true}={}){
      const seq=++s.seq,selection=key();
      const list=await request(path('/conversations?offset='+s.offset+(s.unread?'&unread=true':'')+'&q='+encodeURIComponent(s.q)+(s.channel?'&channel_id='+encodeURIComponent(s.channel):'')+(s.mode?'&mode='+encodeURIComponent(s.mode):'')));
      let data=null;
      if(detail&&s.selected){
        try{
          data=await request(selectedPath('/messages?phone='+encodeURIComponent(s.selected.contact_e164)));
          if(current()&&seq===s.seq&&selection===key())s.detailError=null;
        }catch(error){
          if(current()&&seq===s.seq&&selection===key()){
            s.detailError=error?.message||'Não foi possível carregar esta conversa.';
            render();
          }
          throw error;
        }
      }
      if(!current()||seq!==s.seq||selection!==key())return;
      s.items=list.items;s.more=list.has_more;if(s.selected){s.selected=s.items.find(x=>x.channel_id===s.selected.channel_id&&x.contact_e164===s.selected.contact_e164)||s.selected;}if(data)s.detail=data;s.pending=[];render();
      if(s.selected&&window.OrionCommunication){try{const pending=await request('/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/communication/pending?phone='+encodeURIComponent(s.selected.contact_e164));if(current()&&seq===s.seq&&selection===key()){s.pending=pending.items||[];render();}}catch{if(current()&&seq===s.seq)notice('Os avisos para a equipe não puderam ser consultados. Atualize antes de concluir o atendimento.',true);}}
    }
    async function work(fn,msg,refresh=true){
      if(s.busy)return;
      s.busy=true;
      try{
        await fn();
        if(!current())return;
        if(refresh&&fn!==load)await load();
        else if(!refresh)render();
        if(current()&&msg)notice(msg);
      }catch(e){
        if(current()){
          if(s.selected&&!s.detail)s.detailError=e?.message||'Não foi possível consultar esta conversa.';
          notice(e?.message||'Não foi possível concluir. O texto foi preservado.',true);
        }
      }finally{
        s.busy=false;
        if(current()){
          const n=host.querySelector('[data-central-notice]'),message=n?.textContent,error=n?.getAttribute('role')==='alert';
          render();
          if(message)notice(message,error);
        }
      }
    }
    function syncMessageSearch(value){
      const search=String(value||'').trim(),match=search.toLocaleLowerCase();
      host.querySelectorAll('.os-central-message').forEach(message=>{message.hidden=!!match&&!message.textContent.toLocaleLowerCase().includes(match);});
      const activeSearch=host.querySelector('.os-central-search-active');
      if(activeSearch){activeSearch.hidden=!search;activeSearch.querySelector('span').textContent=search?'Busca: '+search:'';}
    }
    host.addEventListener('input',e=>{
      if(e.target.id==='centralFind'){
        syncMessageSearch(e.target.value);
        return;
      }
      if(e.target.id==='centralNewPhone'){s.newPhone=e.target.value;return;}
      if(e.target.id==='centralSearch'){s.searchDraft=e.target.value;return;}
      if(e.target.id==='centralText'){s.drafts.set(key(),{text:e.target.value,operation:null});scheduleLayout();}
    },{signal:s.abort.signal});
    host.addEventListener('change',e=>{if(e.target.matches('[data-central-reply]'))s.replySelection=e.target.value;},{signal:s.abort.signal});
    host.addEventListener('submit',e=>{
      e.preventDefault();if(s.busy)return;const f=e.target;
      if(f.matches('[data-central-new-contact]')&&canWrite&&s.account){
        const v=Object.fromEntries(new FormData(f));
        let phone;try{phone=window.OrionFields?.phoneValue(v.phone)||('+'+String(v.phone).replace(/\D/g,''));}catch{notice('Revise o WhatsApp com DDD.',true);return;}
        if(!/^\+55[1-9][0-9]{9,10}$/.test(phone)){notice('Informe o WhatsApp brasileiro com DDD.',true);return;}
        void work(async()=>{const opened=await request(path('/channels/'+encodeURIComponent(v.channel)+'/conversations'),'POST',{contact_e164:phone});if(!current())return;s.selected=opened;s.detail=null;s.newOpen=false;s.newPhone='';s.focusTarget='thread';},'Conversa aberta. Assuma o atendimento para enviar.');return;
      }
      if(f.matches('[data-central-search]')){
        const v=Object.fromEntries(new FormData(f));
        s.channel=v.channel||'';s.mode=v.mode||'';s.q=(v.q||'').trim();
        s.searchDraft=s.q;s.unread=v.unread==='on';s.offset=0;
        void work(load);
      }
      if(f.matches('[data-central-compose]')&&canWrite&&s.account&&s.sendEnabled&&s.detail&&s.selected&&!s.detail.control.pending_operation&&['human','waiting'].includes(s.detail.control.mode)&&s.detail.control.assigned_account_id===s.account){
        const d=draft();if(!d.text.trim())return;
        d.operation=d.operation||crypto.randomUUID();s.drafts.set(key(),d);
        const selection=key(),data={contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,operation_id:d.operation,text:d.text};
        void work(async()=>{const result=await request(selectedPath('/messages'),'POST',data);if(!current()||selection!==key())return;s.drafts.delete(selection);if(!['queued','sending','uncertain','accepted','delivered','read','failed'].includes(result.status))throw Error('Recibo desconhecido');},'Solicitação registrada. Consulte a situação no histórico.');
      }
    },{signal:s.abort.signal});
    host.addEventListener('click',e=>{
      const pending=e.target.closest('[data-communication-ack]');if(pending&&canWrite&&!s.busy){const row=s.pending.find(x=>x.id===pending.dataset.communicationAck);if(row)void work(()=>request('/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/communication/pending/'+encodeURIComponent(row.id)+'/acknowledge','POST',{expected_version:row.version}),'Pendência marcada como vista. Nenhuma mensagem foi enviada; o controle da conversa permanece.');return;}
      const person=e.target.closest('[data-central-phone]');if(person&&!s.busy){
        const row=s.items.find(x=>x.channel_id===person.dataset.centralChannel&&x.contact_e164===person.dataset.centralPhone);
        if(!row)return;
        s.selected=row;s.detail=null;s.detailError=null;s.openSections.clear();s.replySelection='';s.focusTarget='thread';s.restoreControlsFocus=false;
        void work(load);return;
      }
      const a=e.target.closest('[data-central-action]');if(!a||a.disabled||s.busy)return;const action=a.dataset.centralAction;
      if(action==='back'){s.returnTo=key();s.selected=null;s.detail=null;s.detailError=null;s.openSections.clear();s.pending=[];s.focusTarget='list';s.restoreControlsFocus=false;render();return;}
      if(action==='clear-find'){const input=host.querySelector('#centralFind');if(input)input.value='';syncMessageSearch('');host.querySelector('.os-central-thread-header')?.focus({preventScroll:true});return;}
      if(a.closest('.os-central-control-options'))closeControls(true);
      if(action==='new'){s.newOpen=!s.newOpen;render();return;}
      if(action==='refresh'){void work(load);return;}
      if(action==='clear-filters'){
        s.channel='';s.mode='';s.unread=false;s.q='';s.searchDraft='';s.offset=0;
        void work(load);return;
      }
      if(action==='previous'||action==='next'){s.offset+=action==='next'?50:-50;void work(load);return;}
      if(action==='preserved-history'){const selection=key();void work(async()=>{const result=await request(selectedPath('/preserved-history?phone='+encodeURIComponent(s.selected.contact_e164)));if(current()&&key()===selection)s.preserved.set(selection,result.items);},'Histórico consultado sem enviar mensagens.',false);return;}
      if(action==='read'){const last=s.detail.items.at(-1);if(last)void work(()=>request(selectedPath('/read'),'POST',{contact_e164:s.selected.contact_e164,message_id:last.id}),'Leitura registrada somente na central.');return;}
      if(action==='customer'){onCustomer?.(s.detail.customer_id);return;}
      if(action.startsWith('mode-')&&canWrite){void work(()=>request(selectedPath('/control'),'PUT',{contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,mode:action.slice(5)}));return;}
      if(action==='reconcile'&&canWrite){void work(()=>request(selectedPath('/control/reconcile'),'POST',{contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,operation_id:s.detail.control.pending_operation}));return;}
      if(action==='older'){void work(async()=>{const older=await request(selectedPath('/messages?phone='+encodeURIComponent(s.selected.contact_e164)+'&before='+encodeURIComponent(s.detail.next_before)));if(current())s.detail={...s.detail,items:[...older.items,...s.detail.items],has_more:older.has_more,next_before:older.next_before};},'Mensagens anteriores carregadas.',false);return;}
      if(action==='prepare'){const id=host.querySelector('[data-central-reply]').value,r=s.replies.find(x=>x.id===id);if(!r)return;void work(async()=>{const prepared=await request(path('/quick-replies/'+encodeURIComponent(id)+'/preview'),'POST',{customer_id:s.detail.customer_id,expected_version:r.version});if(current())s.drafts.set(key(),{text:prepared.text,operation:null});},'Texto preparado. Revise antes de enviar.');}
    },{signal:s.abort.signal});
    document.addEventListener('click',event=>{if(current()&&!event.target.closest?.('.os-central-control-menu'))closeControls();},{signal:s.abort.signal});
    host.addEventListener('keydown',event=>{if(event.key==='Escape'&&host.querySelector('.os-central-control-menu[open]')){event.preventDefault();event.stopPropagation();closeControls(true);}},{signal:s.abort.signal});
    host.addEventListener('toggle',event=>{if(event.target.matches?.('.os-central-control-menu'))scheduleLayout();},{capture:true,signal:s.abort.signal});
    window.addEventListener('resize',scheduleLayout,{signal:s.abort.signal});
    window.visualViewport?.addEventListener?.('resize',scheduleLayout,{signal:s.abort.signal});
    window.visualViewport?.addEventListener?.('scroll',scheduleLayout,{signal:s.abort.signal});
    host.innerHTML='<p role="status">Carregando conversas…</p>';
    try{const config=await request(path('/channels'));if(!current())return;s.channels=config.items;s.account=config.account_id;s.sendEnabled=config.send_enabled;
      if(toolsEnabled)s.replies=(await request(path('/quick-replies'))).items;
      await load();if(!current())return;s.timer=setInterval(()=>{if(current()&&!s.busy&&document.visibilityState!=='hidden'&&!host.querySelector('input:focus,textarea:focus,select:focus,.os-central-control-menu[open],.os-central-filters[open]'))void load().catch(e=>{if(current())notice(e.message||'Atualização indisponível.',true);});},5000);
    }catch(e){if(current())host.textContent=e.message||'Não foi possível carregar as conversas.';}
  }
  const hasUnsentDraft=()=>!!active&&[...active.drafts.values()].some(item=>!!item.text.trim());
  window.OrionConversations={mount,unmount,hasUnsentDraft};
})();
