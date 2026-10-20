/* Uses the panel's authenticated tenant bridge. No socket or provider secrets. */
(function(){
  'use strict';let active=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const modes={ai:'IA ativa',human:'Atendimento humano',waiting:'Aguardando cliente',paused:'Pausada',closed:'Encerrada'};
  const receipts={received:'Recebida',queued:'Na fila',sending:'Envio em verificação',uncertain:'Resultado incerto',accepted:'Aceita pelo canal',delivered:'Entregue',read:'Lida',failed:'Não enviada ou recusada pelo canal'};
  function unmount(){if(active){active.alive=false;active.abort.abort();clearInterval(active.timer);active=null;}}
  async function mount({host,request,tenantId,canWrite=false,onCustomer,toolsEnabled=false}){
    unmount();const s={host,alive:true,abort:new AbortController(),channels:[],items:[],detail:null,selected:null,channel:'',mode:'',q:'',offset:0,more:false,busy:false,seq:0,drafts:new Map(),replies:[]};active=s;
    const current=()=>active===s&&s.alive&&host.isConnected;
    const path=v=>'/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/inbox'+v;
    const selectedPath=v=>path('/channels/'+encodeURIComponent(s.selected.channel_id)+v);
    const key=()=>s.selected?s.selected.channel_id+':'+s.selected.contact_e164:'';
    const draft=()=>s.drafts.get(key())||{text:'',operation:null};
    const notice=(msg,error=false)=>{const n=host.querySelector('[data-central-notice]');if(n){n.textContent=msg;n.setAttribute('role',error?'alert':'status');}};
    const btn=(label,action,disabled=false)=>'<button type="button" class="os-button os-outline" data-central-action="'+action+'" '+(disabled?'disabled':'')+'>'+esc(label)+'</button>';
    function render(){
      if(!current())return;const c=s.detail?.control,owned=c&&['human','waiting'].includes(c.mode)&&c.assigned_account_id===s.account;
      host.innerHTML='<div class="os-central"><form data-central-search class="os-central-filters"><label>Canal<select name="channel"><option value="">Todos os canais</option>'+s.channels.map(x=>'<option value="'+esc(x.id)+'" '+(s.channel===x.id?'selected':'')+'>'+esc((x.provider==='qr'?'QR · ':'Meta · ')+x.label+(x.enabled?'':' · indisponível'))+'</option>').join('')+'</select></label><label>Situação<select name="mode"><option value="">Todas</option>'+Object.entries(modes).map(([id,name])=>'<option value="'+id+'" '+(s.mode===id?'selected':'')+'>'+name+'</option>').join('')+'</select></label><label>Nome ou telefone<input name="q" maxlength="100" value="'+esc(s.q)+'"></label><button class="os-button" '+(s.busy?'disabled':'')+'>Filtrar</button>'+btn('Atualizar','refresh',s.busy)+'</form><p data-central-notice role="status" aria-live="polite"></p><div class="os-central-grid"><section class="os-card"><h2>Conversas</h2><div class="os-inbox-list">'+(s.items.length?s.items.map(x=>'<button type="button" class="os-inbox-person" data-central-phone="'+esc(x.contact_e164)+'" data-central-channel="'+esc(x.channel_id)+'" aria-pressed="'+(s.selected?.channel_id===x.channel_id&&s.selected?.contact_e164===x.contact_e164)+'"><strong>'+esc(x.name||x.contact_e164)+'</strong><small>'+esc((x.provider==='qr'?'QR':'Meta')+' · '+modes[x.mode])+'</small><span>'+esc((x.preview||'Mensagem sem texto').slice(0,100))+'</span></button>').join(''):'<p>Nenhuma conversa neste recorte.</p>')+'</div><div class="os-actions">'+btn('Anterior','previous',s.offset===0||s.busy)+btn('Próxima','next',!s.more||s.busy)+'</div></section><section class="os-card os-central-conversation"><h2>'+esc(s.selected?.name||s.selected?.contact_e164||'Atendimento')+'</h2>'+(c?'<p>'+esc(modes[c.mode])+' · revisão '+esc(c.revision)+(c.assigned_account_id===s.account?' · com você':c.assigned_account_id?' · com outro operador':'')+'</p><div class="os-actions">'+[['human','Assumir'],['waiting','Aguardar cliente'],['ai','Retomar IA'],['paused','Pausar'],['closed','Encerrar']].map(([id,label])=>btn(label,'mode-'+id,!canWrite||s.busy||!!c.pending_operation)).join('')+(s.detail.customer_id&&onCustomer?btn('Dados e etiquetas','customer',s.busy):'')+'</div>'+(c.pending_operation?'<p class="os-notice">Um envio está em verificação. A troca de controle ficará bloqueada até existir um recibo seguro.</p>'+btn('Consultar recibo do QR','reconcile',!canWrite||s.busy||s.channels.find(x=>x.id===s.selected.channel_id)?.provider!=='qr'):'')+
        (s.detail.has_more?btn('Carregar mensagens anteriores','older',s.busy):'')+'<ol class="os-central-messages" aria-label="Histórico da conversa">'+s.detail.items.map(m=>'<li class="os-central-message" data-direction="'+esc(m.direction)+'"><small>'+esc(m.source==='customer'?'Cliente':m.source==='manual'?'Atendente':m.source==='assistant'?'Orion IA':'Automação')+' · '+esc(new Date(m.created_at).toLocaleString('pt-BR'))+'</small><p>'+esc(m.body||(m.kind==='audio'?'Áudio recebido · reprodução indisponível':'Mensagem sem texto'))+'</p><small>'+esc(receipts[m.status]||m.status)+'</small></li>').join('')+'</ol>'+
        (s.replies.length&&s.detail.customer_id?'<label>Resposta rápida<select data-central-reply><option value="">Escolha uma resposta</option>'+s.replies.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.title)+'</option>').join('')+'</select></label>'+btn('Preparar resposta','prepare',s.busy):'')+
        '<form data-central-compose><label for="centralText">Mensagem para revisão</label><textarea id="centralText" name="text" rows="4" maxlength="1200" '+(!canWrite?'readonly':'')+'>'+esc(draft().text)+'</textarea><p class="os-help">Envie após assumir a conversa. A aceitação pelo canal será exibida separadamente de entrega e leitura.</p><button class="os-button" '+(!canWrite||!owned||s.busy||!!c.pending_operation||!s.sendEnabled?'disabled':'')+'>'+(draft().operation?'Verificar solicitação de envio':'Enviar mensagem revisada')+'</button></form>'+(!s.sendEnabled?'<p>O envio pela central está desativado nesta instalação.</p>':''):'<p>Selecione uma conversa para ver o histórico e assumir o atendimento.</p>')+'</section></div></div>';
    }
    async function load({detail=true}={}){
      const seq=++s.seq,selection=key();
      const list=await request(path('/conversations?offset='+s.offset+'&q='+encodeURIComponent(s.q)+(s.channel?'&channel_id='+encodeURIComponent(s.channel):'')+(s.mode?'&mode='+encodeURIComponent(s.mode):'')));
      const data=detail&&s.selected?await request(selectedPath('/messages?phone='+encodeURIComponent(s.selected.contact_e164))):null;
      if(!current()||seq!==s.seq||selection!==key())return;
      s.items=list.items;s.more=list.has_more;if(data)s.detail=data;render();
    }
    async function work(fn,msg,refresh=true){if(s.busy)return;s.busy=true;try{await fn();if(current()){if(refresh)await load();else render();notice(msg||'Atendimento atualizado.');}}catch(e){if(current())notice(e.message||'Não foi possível concluir. O texto foi preservado.',true);}finally{s.busy=false;if(current()){const n=host.querySelector('[data-central-notice]'),msg=n?.textContent,error=n?.getAttribute('role')==='alert';render();if(msg)notice(msg,error);}}}
    host.addEventListener('input',e=>{if(e.target.id==='centralText')s.drafts.set(key(),{text:e.target.value,operation:null});},{signal:s.abort.signal});
    host.addEventListener('submit',e=>{
      e.preventDefault();if(s.busy)return;const f=e.target;
      if(f.matches('[data-central-search]')){const v=Object.fromEntries(new FormData(f));s.channel=v.channel;s.mode=v.mode;s.q=v.q;s.offset=0;void work(()=>load());}
      if(f.matches('[data-central-compose]')&&canWrite&&s.detail&&s.selected){
        const d=draft();if(!d.text.trim())return;
        d.operation=d.operation||crypto.randomUUID();s.drafts.set(key(),d);
        const selection=key(),data={contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,operation_id:d.operation,text:d.text};
        void work(async()=>{const result=await request(selectedPath('/messages'),'POST',data);if(!current()||selection!==key())return;s.drafts.delete(selection);if(!['queued','sending','uncertain','accepted','delivered','read','failed'].includes(result.status))throw Error('Recibo desconhecido');},'Solicitação registrada. Consulte a situação no histórico.');
      }
    },{signal:s.abort.signal});
    host.addEventListener('click',e=>{
      const person=e.target.closest('[data-central-phone]');if(person&&!s.busy){s.selected=s.items.find(x=>x.channel_id===person.dataset.centralChannel&&x.contact_e164===person.dataset.centralPhone);s.detail=null;void work(()=>load());return;}
      const a=e.target.closest('[data-central-action]');if(!a||a.disabled||s.busy)return;const action=a.dataset.centralAction;
      if(action==='refresh'){void work(()=>load());return;}
      if(action==='previous'||action==='next'){s.offset+=action==='next'?50:-50;void work(()=>load());return;}
      if(action==='customer'){onCustomer?.(s.detail.customer_id);return;}
      if(action.startsWith('mode-')&&canWrite){void work(()=>request(selectedPath('/control'),'PUT',{contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,mode:action.slice(5)}));return;}
      if(action==='reconcile'&&canWrite){void work(()=>request(selectedPath('/control/reconcile'),'POST',{contact_e164:s.selected.contact_e164,expected_revision:s.detail.control.revision,operation_id:s.detail.control.pending_operation}));return;}
      if(action==='older'){void work(async()=>{const older=await request(selectedPath('/messages?phone='+encodeURIComponent(s.selected.contact_e164)+'&before='+encodeURIComponent(s.detail.next_before)));if(current())s.detail={...s.detail,items:[...older.items,...s.detail.items],has_more:older.has_more,next_before:older.next_before};},'Mensagens anteriores carregadas.',false);return;}
      if(action==='prepare'){const id=host.querySelector('[data-central-reply]').value,r=s.replies.find(x=>x.id===id);if(!r)return;void work(async()=>{const prepared=await request(path('/quick-replies/'+encodeURIComponent(id)+'/preview'),'POST',{customer_id:s.detail.customer_id,expected_version:r.version});if(current())s.drafts.set(key(),{text:prepared.text,operation:null});},'Texto preparado. Revise antes de enviar.');}
    },{signal:s.abort.signal});
    host.innerHTML='<p role="status">Carregando conversas…</p>';
    try{const config=await request(path('/channels'));if(!current())return;s.channels=config.items;s.account=config.account_id;s.sendEnabled=config.send_enabled;
      if(toolsEnabled)s.replies=(await request(path('/quick-replies'))).items;
      await load();s.timer=setInterval(()=>{if(current()&&!s.busy&&document.visibilityState!=='hidden'&&!host.querySelector('input:focus,textarea:focus,select:focus'))void load().catch(e=>{if(current())notice(e.message||'Atualização indisponível.',true);});},5000);
    }catch(e){if(current())host.textContent=e.message||'Não foi possível carregar as conversas.';}
  }
  window.OrionConversations={mount,unmount};
})();
