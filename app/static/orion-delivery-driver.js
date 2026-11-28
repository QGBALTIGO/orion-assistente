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
  async function request(path,method='GET',body){
    const response=await fetch(path,{method,credentials:'same-origin',cache:'no-store',headers:{
      'Content-Type':'application/json','X-Orion-CSRF':csrf},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok){const err=new Error(typeof result.detail==='string'?result.detail:'Não foi possível concluir a operação.');err.status=response.status;throw err;}
    return result;
  }
  function stopWatch(){
    sharing++;if(watch!==null)navigator.geolocation.clearWatch(watch);watch=null;lastUpload=0;
    el('geo-button').textContent='Compartilhar localização';
  }
  function actionButton(text,action,secondary=false){
    return '<button type="button" data-action="'+action+'"'+(secondary?' class="secondary"':'')+'>'+text+'</button>';
  }
  function orderMarkup(o){
    const a=o.address||{},offered=o.status==='offered';
    const address=[a.street,a.no_number?'sem número':a.number,a.neighborhood,a.city,a.state,a.complement].filter(Boolean).join(', ');
    const total=Number(o.total_cents),change=o.change_for_cents;
    const reserved=o.status==='reserved';
    const controls=reserved?actionButton('Liberar pedido','release',true):offered?actionButton(model?.route_v2?'Separar pedido':'Aceitar','accept')+actionButton('Recusar','refuse',true):
      (o.status==='accepted'?actionButton('Informar chegada','arrive'):'')+
      (address?'<a rel="noreferrer" target="_blank" href="https://www.google.com/maps/dir/?api=1&amp;destination='+encodeURIComponent(address)+'">Abrir rota</a>':'')+
      (o.phone?'<a href="tel:'+esc(o.phone)+'">Ligar para o cliente</a>':'');
    const complete='<details><summary>Concluir entrega</summary><form data-delivery-confirm>'+
      (o.pix_state?'<p>'+ (o.pix_state==='confirmed'?'Pix conferido pelo proprietário.':'Aguardando o proprietário conferir o Pix.')+'</p>':
        total?'<label>Forma recebida<select name="payment" required><option value="">Selecione</option>'+
        Object.entries(methods).filter(([key])=>key!=='pix').map(([key,label])=>'<option value="'+key+'">'+label+'</option>').join('')+'</select></label>'+
        '<label><input type="checkbox" name="received" required> Confirmo o recebimento de '+money(total)+'</label>':'')+
      (o.containers?'<label><input type="checkbox" name="containers" required> Conferi a troca ou o empréstimo dos vasilhames listados</label>':'')+
      '<button'+(o.pix_state==='pending'?' disabled':'')+'>Concluir entrega</button></form></details>'+
      '<details><summary>Cliente ausente</summary><form data-absent>'+
      '<label><input type="checkbox" required> Os produtos retornaram ao estoque</label><button class="secondary">Registrar tentativa</button></form></details>';
    return '<article data-id="'+esc(o.id)+'"><h2>Pedido '+esc(o.id.slice(0,8).toUpperCase())+'</h2><p>'+esc(labels[o.status])+'</p>'+
      '<p>'+esc(o.customer_name||'Cliente')+(address?' · '+esc(address):'')+'</p>'+
      '<ul>'+(o.items||[]).map(i=>'<li>'+esc(i.quantity+' × '+[i.name,i.variant].filter(Boolean).join(' · '))+'</li>').join('')+'</ul>'+
      (o.container_items?.length?'<p>Vasilhames</p><ul>'+o.container_items.map(i=>'<li>'+esc(i.name+' · '+i.exchange+' em troca · '+i.loan+' em empréstimo')+'</li>').join('')+'</ul>':'')+
      '<p><strong>'+money(total)+'</strong> · '+esc(methods[o.payment_preference]||'Pagamento a combinar')+'</p>'+
      (change!=null?'<p>Levar '+money(Math.max(0,change-total))+' de troco para '+money(change)+'.</p>':'')+
      (['debit_card','credit_card'].includes(o.payment_preference)?'<p>Levar maquininha.</p>':'')+
      '<div class="actions">'+controls+'</div>'+(offered||reserved?'':complete)+'</article>';
  }
  function renderBatch(){
    el('batch-workspace').hidden=!model.route_v2;
    if(!model.route_v2){el('available-orders').replaceChildren();el('route-load').replaceChildren();return;}
    const list=model.available_orders||[], reserved=model.orders.filter(o=>o.status==='reserved'),
          onRoute=model.orders.some(o=>['accepted','arrived'].includes(o.status));
    for(const id of selected)if(!list.some(o=>o.id===id))selected.delete(id);
    const open=model.orders.filter(o=>['reserved','accepted','arrived'].includes(o.status)).length;
    const allowance=Math.max(0,model.max_stops-open);
    el('available-orders').innerHTML=list.length?'<p>Selecione até '+allowance+' pedidos. Os endereços completos ficam disponíveis após a reserva.</p><div class="actions">'+actionButton('Sugerir por bairro','suggest',true)+'</div>'+
      '<form id="claim-form">'+list.map(o=>'<label class="claim-row"><input type="checkbox" name="pick" value="'+esc(o.id)+'" '+(selected.has(o.id)?'checked ':'')+(allowance?'':'disabled ')+
      '><span><strong>'+esc([o.address?.neighborhood,o.address?.city].filter(Boolean).join(' · ')||'Localização a conferir')+'</strong><small>'+esc((o.items||[]).map(i=>i.quantity+' × '+i.name).join(' · '))+'</small></span><strong>'+money(o.total_cents)+'</strong></label>').join('')+
      '<p id="selection-summary" role="status"></p><button type="submit" id="claim-button" disabled>Assumir selecionados</button></form>':'<p>Nenhum pedido disponível agora.</p>';
    const totals=new Map();
    for(const o of reserved)for(const i of o.items||[]){
      const key=i.variant_id, prev=totals.get(key);totals.set(key,{name:[i.name,i.variant].filter(Boolean).join(' · '),quantity:(prev?.quantity||0)+i.quantity});
    }
    el('route-load').innerHTML=reserved.length?'<div class="card"><p>'+reserved.length+' pedido(s) separados. Confirme a quantidade efetivamente carregada.</p>'+
      (onRoute?'<p>Conclua a rota em andamento antes de iniciar outra.</p>':'')+
      '<form id="load-form"><ol>'+reserved.map(o=>'<li>'+esc(o.customer_name||'Cliente')+' · '+esc(o.address?.neighborhood||'')+'</li>').join('')+'</ol>'+
      [...totals].map(([id,i])=>'<label class="load-line"><span>'+esc(i.name)+'<small>Esperado: '+i.quantity+'</small></span><input aria-label="Quantidade carregada de '+esc(i.name)+'" name="load" data-variant="'+esc(id)+'" type="number" min="1" step="1" inputmode="numeric" required></label>').join('')+
      '<label><input type="checkbox" name="checked" required> Conferi fisicamente todos os produtos e a capacidade do veículo.</label><button '+(onRoute?'disabled':'')+'>Iniciar rota</button></form></div>':'<p>Nenhuma carga aguardando saída.</p>';
    updateSelection();
  }
  function updateSelection(){
    const f=el('claim-form');if(!f)return;
    const list=(model.available_orders||[]).filter(o=>selected.has(o.id));
    const open=model.orders.filter(o=>['reserved','accepted','arrived'].includes(o.status)).length;
    el('selection-summary').textContent=list.length+' selecionado(s) · '+money(list.reduce((n,o)=>n+Number(o.total_cents),0));
    el('claim-button').disabled=!list.length||open+list.length>model.max_stops||!navigator.onLine;
  }
  function suggest(){
    const list=model?.available_orders||[],open=model.orders.filter(o=>['reserved','accepted','arrived'].includes(o.status)).length;
    const n=Math.max(0,model.max_stops-open);if(!list.length||!n)return;
    // The server orders by waiting time. Group near the oldest request by known neighborhood.
    // No fabricated GPS, driving distance or vehicle weight.
    const first=list[0],same=o=>o.address?.neighborhood===first.address?.neighborhood&&o.address?.city===first.address?.city&&o.address?.state===first.address?.state;
    selected.clear();
    [...list.filter(same),...list.filter(o=>!same(o))].slice(0,n).forEach(o=>selected.add(o.id));
    el('claim-form')?.querySelectorAll('input[name=pick]').forEach(i=>i.checked=selected.has(i.value));
    updateSelection();notice('Sugestão por bairro e tempo de espera. Confira antes de assumir.');
  }
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
    el('person').textContent=data.name;el('shift-state').textContent=states[data.state];
    el('shift-actions').innerHTML=(data.state==='off'?actionButton('Iniciar expediente','available'):
      actionButton(data.state==='available'?'Ficar indisponível':'Ficar disponível',data.state==='available'?'unavailable':'available',true)+
      actionButton('Encerrar expediente','off',true));
    el('geo').hidden=data.state==='off';if(data.state==='off')stopWatch();
    const shape=JSON.stringify([data.orders,data.available_orders,data.route_v2,data.state]);
    if(shape!==lastShape){
      el('queue').innerHTML=data.orders.length?data.orders.map(orderMarkup).join(''):'<p>Nenhuma entrega atribuída.</p>';
      renderBatch();lastShape=shape;
    }
  }
  function fault(error){
    notice(error.message||'Sem conexão. Tente novamente.');
    if([401,403].includes(error.status)){
      stopWatch();model=null;lastShape='';el('queue').replaceChildren();el('available-orders').replaceChildren();el('route-load').replaceChildren();selected.clear();el('workspace').hidden=true;
      if(error.status===401){csrf='';el('login').hidden=false;el('logout').hidden=true;}
    }
    el('retry').hidden=false;
  }
  async function start(){
    try{const me=await request('/auth/me');csrf=me.csrf;personId=me.account_id||me.email||'';el('logout').hidden=false;
      el('login').hidden=true;await refresh();notice('');}catch(error){fault(error);}
  }
  async function perform(task){
    if(busy)return;busy=true;sequence++;
    try{await task();await refresh();notice('');}
    catch(error){fault(error);}finally{busy=false;}
  }
  async function orderAction(article,action,extra={}){
    const row=model?.orders.find(o=>o.id===article.dataset.id);if(!row)return;
    const data={action,expected_revision:row.revision,...extra};
    const signature=JSON.stringify([tenant,personId,row.id,data]);
    if(!actionKeys.has(signature))actionKeys.set(signature,crypto.randomUUID());
    await request(api+'/orders/'+row.id+'/actions','POST',{...data,idempotency_key:actionKeys.get(signature)});
  }
  el('login').addEventListener('submit',async event=>{
    event.preventDefault();const form=event.target;
    try{await request('/auth/login','POST',{username:form.username.value,password:form.password.value});form.password.value='';await start();}
    catch(error){fault(error);}
  });
  el('retry').onclick=start;
  el('logout').onclick=async()=>{
    try{stopWatch();if(model&&model.state!=='off')await request(api+'/shift','PUT',{state:'off'});
      await request('/auth/logout','POST');location.reload();}catch(error){fault(error);}
  };
  el('workspace').addEventListener('click',event=>{
    const btn=event.target.closest('[data-action]');if(!btn)return;
    const action=btn.dataset.action,article=btn.closest('article');
    if(action==='suggest'){suggest();return;}
    if(action==='release'&&article){const o=model?.orders.find(r=>r.id===article.dataset.id);if(o)void perform(()=>batchRequest('/reservations/release',{orders:[ref(o)]}));return;}
    void perform(()=>article?orderAction(article,action):request(api+'/shift','PUT',{state:action}));
  });
  el('batch-workspace').addEventListener('change',event=>{
    const input=event.target;if(input.name!=='pick')return;
    if(input.checked)selected.add(input.value);else selected.delete(input.value);updateSelection();
  });
  el('batch-workspace').addEventListener('submit',event=>{
    event.preventDefault();const f=event.target;
    if(!model||!navigator.onLine)return;
    if(f.id==='claim-form'){
      const orders=model.available_orders.filter(o=>selected.has(o.id)).map(ref);
      if(!orders.length||el('claim-button').disabled)return;
      void perform(async()=>{await batchRequest('/reservations',{orders});selected.clear();});
    }else if(f.id==='load-form'){
      const orders=model.orders.filter(o=>o.status==='reserved').map(ref);
      const load=[...f.querySelectorAll('input[name=load]')].map(i=>({variant_id:i.dataset.variant,quantity:Number(i.value)}));
      void perform(()=>batchRequest('/routes/start',{orders,load,checked:!!f.elements.checked.checked}));
    }
  });
  el('queue').addEventListener('submit',event=>{
    event.preventDefault();const f=event.target,o=f.closest('article');if(!o)return;
    void perform(()=>f.hasAttribute('data-absent')?orderAction(o,'absent',{returned_to_stock:true}):
      orderAction(o,'complete',{payment_method:f.elements.payment?.value||null,received:!!f.elements.received?.checked,
        containers_confirmed:!!f.elements.containers?.checked}));
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
  window.addEventListener('offline',()=>{notice('Sem conexão. Confira a fila ao reconectar.');updateSelection();});
  window.addEventListener('pagehide',stopWatch);
  window.setInterval(()=>{if(!busy&&model&&!document.hidden&&navigator.onLine)void refresh().catch(fault);},30000);
  void start();
})();
