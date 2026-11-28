(function(){
  'use strict';
  const root=location.pathname.replace(/\/$/,''),tenant=root.split('/').pop(),api='/api/v1/entregador/'+encodeURIComponent(tenant);
  const manifest=document.createElement('link');manifest.rel='manifest';manifest.href=root+'/manifest.webmanifest';document.head.append(manifest);
  if('serviceWorker' in navigator) navigator.serviceWorker.register(root+'/sw.js',{scope:root+'/'}).catch(()=>{});
  const el=id=>document.getElementById(id), notice=msg=>{el('notice').textContent=msg;};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money=n=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(n/100);
  const methods={cash:'Dinheiro',pix:'Pix',credit_card:'Cartão de crédito',debit_card:'Cartão de débito'};
  const states={off:'Fora do expediente',available:'Disponível para pedidos',unavailable:'Indisponível para novos pedidos'};
  const labels={offered:'Aguardando seu aceite',reserved:'Separado · aguardando conferência',accepted:'Em rota',arrived:'No endereço'};
  let csrf='',personId='',model=null,busy=false,sequence=0,watch=null,lastUpload=0,lastShape='',sharing=0;
  const actionKeys=new Map(), selected=new Set();
  let lastLoadShape='';
  async function request(path,method='GET',body){
    const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',headers:{
      'Content-Type':'application/json','X-Orion-CSRF':csrf},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok){const err=new Error(typeof result.detail==='string'?result.detail:result.detail?.message||'Não foi possível concluir a operação.');err.status=response.status;err.detail=result.detail;throw err;}
    return result;
  }
  function stopWatch(){
    sharing++;if(watch!==null)navigator.geolocation.clearWatch(watch);watch=null;lastUpload=0;
    el('geo-button').textContent='Compartilhar localização';
  }
  function actionButton(text,action,secondary=false){
    return '<button type="button" data-action="'+action+'"'+(secondary?' class="secondary"':'')+'>'+text+'</button>';
  }
  const icons={home:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',orders:'<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 3h6v4H9zM9 11h6M9 15h6"/>',route:'<circle cx="5" cy="18" r="3"/><circle cx="19" cy="6" r="3"/><path d="M8 18h6a4 4 0 0 0 0-8H9a4 4 0 0 1 0-8h4"/>',cash:'<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/>',more:'<circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/>',pin:'<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',check:'<path d="m5 12 4 4 10-10"/>',package:'<path d="m3 7 9-4 9 4v10l-9 4-9-4zM3 7l9 5 9-5M12 12v9"/>',sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>',moon:'<path d="M20.9 13.4a9 9 0 0 1-10.3-10.3 9 9 0 1 0 10.3 10.3Z"/>'};
  const icon=n=>'<svg class="icon" aria-hidden="true" viewBox="0 0 24 24">'+(icons[n]||icons.orders)+'</svg>';
  document.querySelectorAll('[data-icon]').forEach(e=>e.innerHTML=icon(e.dataset.icon));
  let viewName='home',focusId=null,historyData=null,historySequence=0,loadOrder=[],availableFilter='',lastQueueShape='',lastOfferShape='',lastFocusShape='';
  let theme='auto',contrast=false;
  try{theme=localStorage.getItem('orion-driver-theme')||'auto';contrast=localStorage.getItem('orion-driver-contrast')==='high';}catch{}
  function appearance(){const dark=theme==='dark'||theme==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.dataset.theme=dark?'dark':'light';document.documentElement.dataset.contrast=contrast?'high':'normal';el('theme').innerHTML=icon(dark?'sun':'moon');document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===theme)));el('contrast').setAttribute('aria-pressed',String(contrast));document.querySelector('meta[name=theme-color]').content=dark?'#0b0b0b':'#f5f8fc'}
  function storeAppearance(){try{localStorage.setItem('orion-driver-theme',theme);localStorage.setItem('orion-driver-contrast',contrast?'high':'normal')}catch{}appearance()}
  appearance();matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change',appearance);
  el('theme').onclick=()=>{theme=document.documentElement.dataset.theme==='dark'?'light':'dark';storeAppearance()};
  const active=()=>model?.orders.filter(o=>['accepted','arrived'].includes(o.status))||[];
  const reserved=()=>model?.orders.filter(o=>o.status==='reserved')||[];
  const offers=()=>model?.orders.filter(o=>o.status==='offered')||[];
  const available=()=>model?.available_orders||[];
  const area=o=>[o.address?.neighborhood,o.address?.city,o.address?.state].filter(Boolean).join(' · ');
  const address=o=>{const a=o.address||{};return [a.street,a.no_number?'sem número':a.number,a.neighborhood,a.city,a.state,a.complement].filter(Boolean).join(', ')};
  const code=o=>o.id.slice(0,8).toUpperCase();
  const itemsText=o=>(o.items||[]).map(i=>i.quantity+' × '+[i.name,i.variant].filter(Boolean).join(' · ')).join(' · ');
  const itemList=o=>'<ul class="order-items">'+(o.items||[]).map(i=>'<li><strong>'+esc(i.quantity)+'×</strong><span>'+esc([i.name,i.variant].filter(Boolean).join(' · '))+'</span></li>').join('')+'</ul>';
  const empty=(t,d='',action='')=>'<div class="card empty">'+icon('check')+'<h3>'+esc(t)+'</h3>'+(d?'<p>'+esc(d)+'</p>':'')+action+'</div>';
  const navButton=(t,v)=>'<button type="button" data-view="'+v+'">'+esc(t)+'</button>';
  const statePill=o=>'<span class="pill '+(o.status==='arrived'?'good':o.status==='reserved'?'warn':'')+'">'+esc(labels[o.status])+'</span>';
  function currentFocus(){return active().find(o=>o.id===focusId)||active().find(o=>o.status==='arrived')||active()[0]}
  function paymentOptions(){return '<option value="">Selecione</option>'+Object.entries(methods).filter(([key])=>key!=='pix').map(([key,label])=>'<option value="'+key+'">'+label+'</option>').join('')}
  function completion(o){
    const total=Number(o.total_cents);
    return '<form data-delivery-confirm>'+
      (o.pix_state?'<p class="hint '+(o.pix_state==='pending'?'warn':'')+'">'+(o.pix_state==='confirmed'?'Pix conferido pelo proprietário.':'Aguardando o proprietário conferir o Pix.')+'</p>':
      total?'<label>Pagamento recebido<select name="payment" required>'+paymentOptions()+'<option value="split">Pagamento misto (sem Pix)</option></select></label><div data-split hidden><p class="hint">Informe duas formas distintas. Pix permanece sob conferência da empresa.</p><label>Primeira forma<select name="method1">'+paymentOptions()+'</select></label><label>Valor (R$)<input name="amount1" type="number" min="0.01" step="0.01" inputmode="decimal"></label><label>Segunda forma<select name="method2">'+paymentOptions()+'</select></label><p data-split-total></p></div><label><input type="checkbox" name="received" required> Confirmo o recebimento de '+money(total)+'</label>':'')+
      (o.containers?'<div class="hint"><strong>Vasilhames</strong><ul class="order-items">'+(o.container_items||[]).map(i=>'<li>'+esc(i.name)+' · '+esc(i.exchange)+' troca(s) · '+esc(i.loan)+' empréstimo(s)</li>').join('')+'</ul></div><label><input type="checkbox" name="containers" required> Conferi os vasilhames conforme o pedido.</label>':'')+
      '<button type="submit"'+(o.pix_state==='pending'?' disabled':'')+'>Concluir entrega</button></form>';
  }
  function orderMarkup(o){
    const offered=o.status==='offered', held=o.status==='reserved';
    return '<article data-id="'+esc(o.id)+'"><div class="row"><span class="code">'+(o.route_position?'Parada '+esc(o.route_position)+' · ':'')+'Pedido '+esc(code(o))+'</span>'+statePill(o)+'</div>'+
      '<h3>'+esc(offered?area(o)||'Localização a conferir':o.customer_name||'Cliente')+'</h3>'+
      (!offered?'<p class="address">'+icon('pin')+'<span>'+esc(address(o)||'Endereço a conferir')+'</span></p>':'')+
      '<p>'+esc(itemsText(o))+'</p><div class="row"><span class="muted">'+esc(methods[o.payment_preference]||'Pagamento a combinar')+'</span><strong class="price">'+money(o.total_cents)+'</strong></div>'+
      '<div class="actions">'+(offered?actionButton(model.route_v2?'Separar pedido':'Aceitar','accept')+actionButton('Recusar','refuse',true):held?actionButton('Liberar pedido','release',true):actionButton('Abrir entrega','focus'))+'</div></article>';
  }
  function focusMarkup(o){
    const a=active(),position=o.route_position||a.findIndex(x=>x.id===o.id)+1;
    return '<article class="focus-card" data-id="'+esc(o.id)+'"><div class="row"><span class="code">Parada '+esc(position)+' · '+a.length+' restante(s)</span>'+statePill(o)+'</div><div class="focus-summary"><h1>'+esc(o.customer_name||'Cliente')+'</h1><p class="address">'+icon('pin')+'<span>'+esc(address(o)||'Endereço não informado')+'</span></p>'+(o.operator_note?'<p class="hint">'+esc(o.operator_note)+'</p>':'')+'</div>'+
      '<div class="actions">'+(address(o)?'<a rel="noreferrer noopener" target="_blank" href="https://www.google.com/maps/dir/?api=1&amp;destination='+encodeURIComponent(address(o))+'">Navegar</a>':'')+(o.phone?'<a href="tel:'+esc(o.phone)+'">Ligar</a>':'')+'</div>'+
      '<h2>Mercadorias</h2>'+itemList(o)+'<div class="payment-total row"><div><small>A receber · '+esc(methods[o.payment_preference]||'A combinar')+'</small><div class="price">'+money(o.total_cents)+'</div></div>'+icon('cash')+'</div>'+
      (o.change_for_cents!=null?'<p class="hint">Troco para '+money(o.change_for_cents)+' · levar '+money(Math.max(0,o.change_for_cents-o.total_cents))+'</p>':'')+
      (['debit_card','credit_card'].includes(o.payment_preference)?'<p class="hint">Levar maquininha.</p>':'')+
      '<details class="detail"><summary>Cliente ausente ou devolução</summary><form data-absent><p class="hint warn">Registre somente após retornar fisicamente todos os produtos ao depósito.</p><label><input type="checkbox" required> Os produtos retornaram ao estoque</label><button class="secondary">Registrar tentativa</button></form></details>'+
      '<div class="focus-actions">'+(o.status==='arrived'?completion(o):actionButton('Cheguei ao endereço','arrive'))+'</div></article>';
  }
  function renderFocus(){const o=currentFocus(),shape=JSON.stringify(o);if(shape===lastFocusShape)return;focusId=o?.id||null;lastFocusShape=shape;el('focus-content').innerHTML=o?focusMarkup(o):empty('Rota concluída','Nenhuma entrega em andamento.',navButton('Voltar ao Dashboard','home'));}
  function showView(name){if(!model)return;if(name==='focus'&&!active().length){name='route'}viewName=name;document.body.classList.toggle('focus-mode',name==='focus');document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!=='view-'+name);document.querySelectorAll('.nav button').forEach(b=>{if(b.dataset.view===name||name==='focus'&&b.dataset.view==='route')b.setAttribute('aria-current','page');else b.removeAttribute('aria-current')});if(name==='focus')renderFocus();if(name==='cash'||name==='more')void refreshHistory();window.scrollTo(0,0)}
  function renderHome(){
    const count=available().length+offers().length,n=active().length+reserved().length,done=historyData?.items?.length??'—',o=currentFocus();
    el('stats').innerHTML=[['Disponíveis',count,'orders','orders'],['Na sua rota',n,'route','route'],['Concluídas (7 dias)',done,'check','more']].map(x=>'<button type="button" class="stat" data-view="'+x[3]+'">'+icon(x[2])+'<small>'+x[0]+'</small><strong>'+esc(x[1])+'</strong></button>').join('');
    el('next-delivery').innerHTML=o?'<div class="card next"><p class="eyebrow">PRÓXIMA ENTREGA</p><h2>'+esc(o.customer_name||'Cliente')+'</h2><p>'+esc(address(o))+'</p><p>'+esc(itemsText(o))+'</p><div class="row"><span class="price">'+money(o.total_cents)+'</span>'+statePill(o)+'</div>'+actionButton('Continuar entrega','focus')+'</div>':
      reserved().length?'<div class="card next"><p class="eyebrow">CARGA A CONFERIR</p><h2>'+reserved().length+' pedido(s) separados</h2>'+navButton('Conferir carga','route')+'</div>':empty('Tudo pronto','Escolha os pedidos disponíveis para montar sua rota.',navButton('Ver pedidos','orders'));
    el('home-shortcuts').innerHTML='<div class="card row"><div><h2>Meu caixa</h2><p>Recebimentos das suas entregas</p></div><button type="button" class="secondary" data-view="cash">Consultar</button></div>';
  }
  function renderAvailable(){
    const list=available(),open=active().length+reserved().length,allowance=Math.max(0,model.max_stops-open);
    for(const id of selected)if(!list.some(o=>o.id===id))selected.delete(id);
    const query=availableFilter.toLocaleLowerCase('pt-BR'),visible=list.filter(o=>(area(o)+' '+itemsText(o)+' '+code(o)).toLocaleLowerCase('pt-BR').includes(query));
    const rows=visible.map(o=>'<label class="claim-row"><input type="checkbox" name="pick" value="'+esc(o.id)+'" '+(selected.has(o.id)?'checked ':'')+(allowance?'':'disabled ')+'><span><strong>'+esc(area(o)||'Localização a conferir')+'</strong><small>'+esc(code(o)+' · '+itemsText(o))+'</small></span><strong>'+money(o.total_cents)+'</strong></label>').join('');
    el('available-orders').innerHTML=list.length?'<div class="card"><div class="row"><span class="pill">'+list.length+' disponíveis</span>'+actionButton('Sugerir pedidos','suggest',true)+'</div><label>Buscar por bairro ou produto<input id="available-filter" value="'+esc(availableFilter)+'" placeholder="Buscar pedidos"></label><small>Limite de '+allowance+' novo(s) pedido(s). Endereços completos após a reserva.</small><form id="claim-form"><div id="claim-rows">'+(rows||'<p>Nenhum resultado.</p>')+'</div><div class="selection-bar"><p id="selection-summary" role="status"></p><button type="submit" id="claim-button" disabled>Assumir selecionados</button></div></form></div>':empty('Nenhum pedido disponível',model.state==='available'?'Novos pedidos aparecerão aqui.':'Fique disponível para assumir pedidos.');
    updateSelection();
  }
  function updateSelection(){const f=el('claim-form');if(!f)return;const list=available().filter(o=>selected.has(o.id));const units=list.reduce((n,o)=>n+(o.items||[]).reduce((s,i)=>s+i.quantity,0),0);el('selection-summary').textContent=list.length+' selecionado(s) · '+units+' unidade(s) · '+money(list.reduce((n,o)=>n+Number(o.total_cents),0));el('claim-button').disabled=!list.length||active().length+reserved().length+list.length>model.max_stops||!navigator.onLine||busy;}
  function suggest(){
    const list=available(),n=Math.max(0,model.max_stops-active().length-reserved().length);if(!list.length||!n)return;
    const first=list[0],same=o=>area(o)===area(first);selected.clear();[...list.filter(same),...list.filter(o=>!same(o))].slice(0,n).forEach(o=>selected.add(o.id));renderAvailable();notice('Sugestão por bairro e tempo de espera. Confirme a seleção.');
  }
  function groupedReserved(){const map=new Map();for(const o of reserved())for(const i of o.items||[]){const prev=map.get(i.variant_id);map.set(i.variant_id,{name:[i.name,i.variant].filter(Boolean).join(' · '),quantity:(prev?.quantity||0)+i.quantity})}return map}
  function orderedReserved(){const rows=reserved();loadOrder=loadOrder.filter(id=>rows.some(o=>o.id===id));for(const o of rows)if(!loadOrder.includes(o.id))loadOrder.push(o.id);return loadOrder.map(id=>rows.find(o=>o.id===id))}
  function drawLoadOrder(){const n=el('load-sequence');if(!n)return;n.innerHTML=orderedReserved().map((o,i)=>'<div class="receipt-row"><span>'+String(i+1)+'. '+esc(o.customer_name||'Cliente')+'<br><small>'+esc(area(o))+'</small></span><div class="actions"><button type="button" class="secondary" aria-label="Subir parada '+(i+1)+'" data-move="'+esc(o.id)+'" data-direction="-1" '+(i===0?'disabled':'')+'>↑</button><button type="button" class="secondary" aria-label="Descer parada '+(i+1)+'" data-move="'+esc(o.id)+'" data-direction="1" '+(i===loadOrder.length-1?'disabled':'')+'>↓</button></div></div>').join('');}
  function renderLoad(){
    const rows=reserved(),onRoute=active().length>0,shape=JSON.stringify([rows,onRoute]);if(shape===lastLoadShape)return;
    lastLoadShape=shape;el('route-load').innerHTML=rows.length?'<div class="card"><h2>Conferir carga</h2><p>'+rows.length+' pedido(s) separados. Informe a quantidade carregada de cada produto.</p>'+(onRoute?'<p class="hint warn">Conclua a rota atual antes de iniciar outra.</p>':'')+
    '<form id="load-form">'+[...groupedReserved()].map(([id,i])=>'<label class="load-line"><span>'+esc(i.name)+'<small>Esperado: '+i.quantity+'</small><small id="mark-'+esc(id)+'"></small></span><input aria-label="Quantidade carregada de '+esc(i.name)+'" name="load" data-variant="'+esc(id)+'" type="number" min="1" step="1" inputmode="numeric" required></label>').join('')+
    '<label><input type="checkbox" name="checked" required> Conferi a carga e a capacidade do veículo.</label><div class="row"><h3>Sequência das entregas</h3>'+actionButton('Agrupar por bairro','suggest-load',true)+'</div><div id="load-sequence"></div><button id="start-route" type="submit" disabled>Iniciar rota</button></form></div>':'';
    drawLoadOrder();checkLoad();
  }
  function checkLoad(){const f=el('load-form');if(!f)return;const expected=groupedReserved();const valid=[...f.querySelectorAll('[data-variant]')].every(i=>{const ok=i.value!==''&&Number(i.value)===expected.get(i.dataset.variant)?.quantity,m=el('mark-'+i.dataset.variant);if(m){m.textContent=ok?'Quantidade conferida':'Pendente';m.className=ok?'load-ok':'load-bad'}return ok});el('start-route').disabled=!valid||!f.elements.checked.checked||active().length>0||!navigator.onLine||busy;}
  function renderBatch(){el('batch-workspace').hidden=!model.route_v2;el('orders-legacy').hidden=model.route_v2;if(model.route_v2)renderAvailable();else el('available-orders').replaceChildren();renderLoad();}
  function renderOrders(){let o=offers(),r=reserved(),a=active(),oshape=JSON.stringify(o),qshape=JSON.stringify([r,a]);if(oshape!==lastOfferShape){el('offers').innerHTML=o.length?'<h2>Pedidos atribuídos</h2>'+o.map(orderMarkup).join(''):'';lastOfferShape=oshape}if(qshape!==lastQueueShape){el('queue').innerHTML=r.length||a.length?[...a,...r].map(orderMarkup).join(''):empty('Nenhuma entrega em rota','Assuma um pedido para começar.',navButton('Ver pedidos','orders'));lastQueueShape=qshape}el('focus-button').hidden=!a.length;renderFocus();}
  async function refreshHistory(){if(!model||!csrf)return;const seq=++historySequence;try{const data=await request(api+'/history');if(seq!==historySequence||!model)return;historyData=data;renderHistory();renderHome()}catch(error){if(seq!==historySequence)return;if([401,403].includes(error.status)){fault(error);return}el('cash-content').innerHTML=empty('Não foi possível consultar recebimentos','Tente novamente com conexão.',actionButton('Tentar novamente','history',true));}}
  function renderHistory(){const data=historyData;if(!data)return;const total=Object.values(data.totals||{}).reduce((a,b)=>a+Number(b),0);el('cash-content').innerHTML='<section class="card"><small>Recebimentos registrados '+(data.has_more?'nas últimas 100 entregas':'nos últimos 7 dias')+'</small><div class="price">'+money(total)+'</div><div class="amount-grid">'+['cash','pix','credit_card','debit_card'].map(k=>'<div class="cash-amount"><small>'+methods[k]+'</small><strong>'+money(data.totals?.[k]||0)+'</strong></div>').join('')+'</div><p class="hint">Pix é conferido pela distribuidora. Valores não representam saldo de acerto de caixa.</p></section>';el('history-content').innerHTML=data.items.length?data.items.map(o=>'<div class="history-row"><div class="row"><strong>Pedido '+esc(code(o))+'</strong><strong>'+money(o.total_cents)+'</strong></div><small>'+esc(new Date(o.completed_at).toLocaleString('pt-BR'))+'</small><small>'+esc((o.payments||[]).map(p=>(methods[p.method]||'Outro')+' '+money(p.amount_cents)).join(' · '))+'</small></div>').join(''):'<p>Nenhuma entrega concluída nos últimos 7 dias.</p>';}
  async function batchRequest(path,data){
    const signature=JSON.stringify([tenant,personId,path,data]);
    if(!actionKeys.has(signature))actionKeys.set(signature,crypto.randomUUID());
    await request(api+path,'POST',{...data,idempotency_key:actionKeys.get(signature)});
  }
  const ref=o=>({order_id:o.id,expected_revision:o.revision});
  async function refresh(){
    const attempt=++sequence;
    const data=await request(api+'/queue');
    if(attempt!==sequence)return;
    model=data;el('login').hidden=true;el('workspace').hidden=false;el('logout').hidden=false;el('retry').hidden=true;
    el('person').textContent='Olá, '+data.name.split(' ')[0]+'!';el('shift-state').textContent=states[data.state];
    el('shift-actions').innerHTML=(data.state==='off'?actionButton('Iniciar expediente','available'):
      actionButton(data.state==='available'?'Ficar indisponível':'Ficar disponível',data.state==='available'?'unavailable':'available',true)+
      actionButton('Encerrar expediente','off',true));
    el('geo').hidden=data.state==='off';if(data.state==='off')stopWatch();
    const shape=JSON.stringify([data.orders,data.available_orders,data.route_v2,data.state]);
    if(shape!==lastShape){
      renderOrders();renderBatch();renderHome();lastShape=shape;
    }
    if(viewName==='focus')renderFocus();
  }
  function fault(error){
    notice(error.message||'Sem conexão. Tente novamente.');
    if([401,403].includes(error.status)){
      stopWatch();model=null;historySequence++;historyData=null;focusId=null;lastShape='';lastQueueShape='';lastOfferShape='';lastFocusShape='';lastLoadShape='';selected.clear();loadOrder=[];actionKeys.clear();for(const id of ['queue','available-orders','route-load','offers','focus-content','history-content','cash-content','stats','next-delivery','home-shortcuts'])el(id).replaceChildren();el('workspace').hidden=true;viewName='home';document.body.classList.remove('focus-mode');
      csrf='';el('login').hidden=false;el('activation').hidden=true;el('logout').hidden=true;
    }
    el('retry').hidden=false;
  }
  async function start(){
    try{const me=await request('/auth/me');csrf=me.csrf;personId=me.account_id||me.email||'';el('logout').hidden=false;
      el('login').hidden=true;el('activation').hidden=true;await refresh();showView('home');void refreshHistory();notice('');}catch(error){fault(error);}
  }
  async function perform(task){
    if(busy)return;busy=true;sequence++;
    try{await task();await refresh();notice('');}
    catch(error){fault(error);if(error.status===409&&model)try{await refresh()}catch(reloadError){fault(reloadError)}}finally{busy=false;updateSelection();checkLoad();}
  }
  async function orderAction(article,action,extra={}){
    const row=model?.orders.find(o=>o.id===article.dataset.id);if(!row)return;
    const data={action,expected_revision:row.revision,...extra};
    const signature=JSON.stringify([tenant,personId,row.id,data]);
    if(!actionKeys.has(signature))actionKeys.set(signature,crypto.randomUUID());
    await request(api+'/orders/'+row.id+'/actions','POST',{...data,idempotency_key:actionKeys.get(signature)});
  }
  const inviteMode=new URLSearchParams(location.search).get('primeiro-acesso')==='1';
  el('activate-link').hidden=!inviteMode;
  function showActivation(verifyEmail=''){
    el('login').hidden=true;el('activation').hidden=false;el('retry').hidden=true;
    el('activate-form').hidden=!!verifyEmail;el('activate-verify').hidden=!verifyEmail;
    if(verifyEmail)el('activate-verify').elements.email.value=verifyEmail;
    notice('');
  }
  el('activate-link').onclick=async()=>{
    try{const info=await request('/auth/features');if(!info.registration){notice('A criação de conta está temporariamente indisponível. Use uma conta Orion existente ou fale com a distribuidora.');return}showActivation()}
    catch(error){notice(error.message);}
  };
  el('activate-back').onclick=()=>{el('activation').hidden=true;el('login').hidden=false;notice('');};
  let activating=false;
  el('activate-form').addEventListener('submit',async event=>{
    event.preventDefault();if(activating)return;const f=event.target;activating=true;
    try{const email=f.elements.email.value.trim().toLowerCase();
      await request('/auth/register','POST',{name:f.elements.name.value,email,password:f.elements.password.value});
      f.elements.password.value='';showActivation(email);notice('Confirme o código enviado ao seu e-mail.');
    }catch(error){notice(error.message);}finally{activating=false}
  });
  el('activate-verify').addEventListener('submit',async event=>{
    event.preventDefault();if(activating)return;activating=true;const f=event.target;
    try{const email=f.elements.email.value.trim().toLowerCase();
      await request('/auth/verify','POST',{email,code:f.elements.code.value});
      f.elements.code.value='';el('activation').hidden=true;el('login').hidden=false;el('login').elements.username.value=email;
      notice('E-mail confirmado. Entre com sua senha para acessar as entregas.');
    }catch(error){notice(error.message);}finally{activating=false}
  });
  el('activate-resend').onclick=async()=>{
    if(activating)return;const f=el('activate-verify');if(!f.elements.email.reportValidity())return;activating=true;
    try{await request('/auth/verify/resend','POST',{email:f.elements.email.value.trim().toLowerCase()});notice('Solicitação recebida. Confira o e-mail.');}
    catch(error){notice(error.message);}finally{activating=false}
  };
  el('login').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;
    try{await request('/auth/login','POST',{username:form.username.value,password:form.password.value});form.password.value='';await start();}
    catch(error){if(error.detail?.code==='email_confirmation_required'){showActivation(form.username.value.trim().toLowerCase());notice('Confirme seu e-mail para entrar.')}else fault(error);}
  });
  el('retry').onclick=start;
  el('logout').onclick=async()=>{
    try{stopWatch();if(model&&model.state!=='off'&&!active().length&&!reserved().length)await request(api+'/shift','PUT',{state:'off'});
      await request('/auth/logout','POST');location.reload();}catch(error){fault(error);}
  };
  el('workspace').addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b||!model)return;
    if(b.dataset.view){showView(b.dataset.view);return}
    if(b.dataset.themeChoice){theme=b.dataset.themeChoice;storeAppearance();return}
    if(b.id==='contrast'){contrast=!contrast;storeAppearance();return}
    if(b.dataset.move){const i=loadOrder.indexOf(b.dataset.move),j=i+Number(b.dataset.direction);if(i>=0&&j>=0&&j<loadOrder.length){[loadOrder[i],loadOrder[j]]=[loadOrder[j],loadOrder[i]];drawLoadOrder()}return}
    const action=b.dataset.action,article=b.closest('article');if(!action)return;
    if(action==='focus'){focusId=article?.dataset.id||null;lastFocusShape='';showView('focus');return}
    if(action==='history'){void refreshHistory();return}
    if(action==='suggest'){suggest();return}
    if(action==='suggest-load'){loadOrder=orderedReserved().slice().sort((a,b)=>area(a).localeCompare(area(b),'pt-BR')).map(o=>o.id);drawLoadOrder();notice('Paradas agrupadas por bairro. Confira a sequência.');return}
    if(!navigator.onLine){notice('Sem conexão. Reconecte antes de confirmar operações.');return}
    if(action==='release'&&article){const o=model.orders.find(r=>r.id===article.dataset.id);if(o&&confirm('Liberar o pedido para outros entregadores?'))void perform(()=>batchRequest('/reservations/release',{orders:[ref(o)]}));return;}
    void perform(async()=>{if(article){await orderAction(article,action)}else await request(api+'/shift','PUT',{state:action})});
  });
  el('workspace').addEventListener('input',event=>{
    const t=event.target;
    if(t.id==='available-filter'){availableFilter=t.value;const pos=t.selectionStart;renderAvailable();const n=el('available-filter');if(n){n.focus();n.setSelectionRange(pos,pos)}}
    if(t.name==='load'||t.name==='checked')checkLoad();
    if(t.name==='amount1'){const form=t.form,o=model?.orders.find(x=>x.id===form.closest('article')?.dataset.id);if(o){const a=Math.round(Number(t.value)*100);form.querySelector('[data-split-total]').textContent='Segunda forma: '+money(Math.max(0,o.total_cents-a))}}
  });
  el('workspace').addEventListener('change',event=>{
    const t=event.target;if(t.name==='pick'){if(t.checked)selected.add(t.value);else selected.delete(t.value);updateSelection()}
    if(t.name==='checked')checkLoad();
    if(t.name==='payment'){const box=t.form.querySelector('[data-split]');if(box){const split=t.value==='split';box.hidden=!split;box.querySelectorAll('input,select').forEach(i=>i.required=split)}}
  });
  el('workspace').addEventListener('submit',event=>{
    event.preventDefault();const f=event.target;if(!model||!navigator.onLine){notice('Reconecte para continuar.');return}
    if(f.id==='claim-form'){const orders=available().filter(o=>selected.has(o.id)).map(ref);if(!orders.length||el('claim-button').disabled)return;void perform(async()=>{await batchRequest('/reservations',{orders});selected.clear();});return}
    if(f.id==='load-form'){if(el('start-route').disabled)return;const orders=orderedReserved().map(ref),load=[...f.querySelectorAll('[data-variant]')].map(i=>({variant_id:i.dataset.variant,quantity:Number(i.value)}));void perform(async()=>{await batchRequest('/routes/start',{orders,load,checked:!!f.elements.checked.checked})}).then(()=>{if(active().length&&!reserved().length){focusId=null;showView('focus')}});return}
    const article=f.closest('article'),o=model.orders.find(r=>r.id===article?.dataset.id);if(!o)return;
    if(f.hasAttribute('data-absent')){void perform(()=>orderAction(article,'absent',{returned_to_stock:true}));return}
    if(!f.hasAttribute('data-delivery-confirm'))return;
    let method=f.elements.payment?.value||null,tenders=[];
    if(method==='split'){const a=Math.round(Number(f.elements.amount1.value)*100),m1=f.elements.method1.value,m2=f.elements.method2.value;if(!m1||!m2||m1===m2||!Number.isSafeInteger(a)||a<=0||a>=Number(o.total_cents)){notice('Confira os dois pagamentos.');return}tenders=[{method:m1,amount_cents:a},{method:m2,amount_cents:Number(o.total_cents)-a}];method=null}
    void perform(()=>orderAction(article,'complete',{payment_method:method,tenders,received:!!f.elements.received?.checked,containers_confirmed:!!f.elements.containers?.checked})).then(()=>{if(!active().some(x=>x.id===o.id)){focusId=null;renderFocus();void refreshHistory()}});
  });
  el('geo-button').onclick=async()=>{
    if(watch!==null){
      stopWatch();try{await request(api+'/location','DELETE');el('geo-status').textContent='Compartilhamento encerrado.';}catch(error){fault(error);}return;
    }
    if(!navigator.geolocation){notice('O navegador não oferece geolocalização.');return;}
    const epoch=++sharing;
    let key;
    el('geo-button').disabled=true;
    try{key=(await request(api+'/location','POST')).sharing_key;}
    catch(error){fault(error);return;}finally{el('geo-button').disabled=false;}
    if(epoch!==sharing)return;
    watch=navigator.geolocation.watchPosition(async position=>{
      if(epoch!==sharing||document.hidden||Date.now()-lastUpload<15000||model?.state==='off')return;
      lastUpload=Date.now();
      try{await request(api+'/location','PUT',{sharing_key:key,latitude:position.coords.latitude,longitude:position.coords.longitude,
        observed_at:new Date(position.timestamp).toISOString()});
        if(epoch===sharing)el('geo-status').textContent='Última posição enviada às '+new Date().toLocaleTimeString('pt-BR')+'.';}
      catch(error){if(epoch===sharing)el('geo-status').textContent=error.message;}
    },error=>{if(epoch===sharing){stopWatch();el('geo-status').textContent=error.message;}},{maximumAge:0,timeout:20000});
    el('geo-button').textContent='Parar compartilhamento';
  };
  window.addEventListener('online',()=>{if(csrf&&!busy)void refresh().catch(fault);});
  window.addEventListener('offline',()=>{notice('Sem conexão. Consulte apenas a tela já carregada e reconecte para confirmar.');updateSelection();checkLoad();});
  window.addEventListener('pagehide',stopWatch);
  window.setInterval(()=>{if(!busy&&model&&!document.hidden&&navigator.onLine)void refresh().catch(fault);},15000);
  void start();
})();
