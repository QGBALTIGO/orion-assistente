/* Counter and pickup sales in the Orion management workspace. */
(function () {
  'use strict';

  const statusLabels={confirmed:'Reservado',fulfilled:'Concluído',cancelled:'Cancelado'};
  const modes=[['counter','Balcão'],['pickup','Retirada']];
  const payments=[['','Selecione'],['cash','Dinheiro'],['debit_card','Cartão de débito'],['credit_card','Cartão de crédito']];
  const command = (button,label,action,symbol,extra='') => button(label,action,'os-outline os-small',symbol,extra);
  const code = order => order.id.slice(0,8).toUpperCase();
  const modeLabel = order => modes.find(([key])=>key===order.mode)?.[1];

  async function mount({host,filters,call,allowed,canWrite,ui,openDetails,openEditor}) {
    if (!host || !allowed()) return;
    const {esc,loading,empty,field,selectField,checkField,button,title,money,badge,fmtDate,dateKey,timeLabel,query}=ui;
    filters.search ??= '';
    filters.state ??= 'all';
    filters.start ??= '';
    filters.end ??= '';
    filters.offset ??= 0;
    let data, stamp=0;
    const current = () => allowed() && host.isConnected;
    const when = value => fmtDate(dateKey(new Date(value)))+' '+timeLabel(value);
    const badgeFor = state => badge(state==='fulfilled'?'completed':state==='confirmed'?'pending':state,statusLabels[state]);
    const itemAttr = (fieldName, id) => 'data-delivery-'+fieldName+'="'+esc(id)+'"';
    const err = error => '<div class="os-notice os-error" role="alert"><p>'+esc(error.message || 'Não foi possível concluir a ação.')+'</p></div>';

    host.innerHTML=title('Pedidos','Balcão e retirada',canWrite()?button('Novo pedido','delivery-new-order','','plus'):'')+
      '<form id="osDeliveryOrderFilters" class="os-filter-row">'+
      field('Buscar','search','search',filters.search,'maxlength="120" placeholder="Número ou cliente"')+
      selectField('Status','state',[['all','Todos'],...Object.entries(statusLabels)],filters.state)+
      field('De','start','date',filters.start)+field('Até','end','date',filters.end)+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form><div id="osDeliveryOrderList" aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const list=host.querySelector('#osDeliveryOrderList');

    function orderCard(order) {
      const controls=command(button,'Detalhes','delivery-order-detail','list',itemAttr('order',order.id))+
        (order.status==='confirmed'&&canWrite()?command(button,'Concluir','delivery-order-fulfill','check',itemAttr('order',order.id))+
          button('Cancelar pedido','delivery-order-cancel','os-ghost os-small','close',itemAttr('order',order.id)):'');
      return '<article class="os-card os-item-card"><h3>Pedido '+esc(code(order))+'</h3><p class="os-help">'+
        esc([order.customer_name||'Venda avulsa',modeLabel(order),when(order.created_at)].join(' · '))+'</p>'+
        '<div class="os-actions">'+badgeFor(order.status)+' <strong>'+money(order.total_cents)+'</strong></div>'+
        '<div class="os-actions">'+controls+'</div></article>';
    }

    async function load() {
      const sequence=++stamp;
      list.innerHTML=loading('Carregando pedidos…');
      try {
        const response=await call('/orders?'+query(filters));
        if (!current()||sequence!==stamp) return;
        data=response;
        let html=data.items.length?'<div class="os-cards-grid">'+data.items.map(orderCard).join('')+'</div>':
          '<div class="os-card">'+empty('list','Nenhum pedido encontrado','Confira o filtro ou registre um novo pedido.')+'</div>';
        if (data.offset>0||data.has_more) html+='<div class="os-pagination"><span>Página '+(data.offset/data.limit+1)+'</span><div class="os-actions">'+
          command(button,'Anterior','delivery-order-prev','left',data.offset?'':'disabled')+
          command(button,'Próxima','delivery-order-next','chevron',data.has_more?'':'disabled')+'</div></div>';
        list.innerHTML=html;
      } catch (error) {
        if (current()&&sequence===stamp) list.innerHTML=err(error)+command(button,'Tentar novamente','delivery-order-retry','refresh');
      }
    }

    function editor() {
      const cart=new Map();
      let customer=null, estimate=null;
      const content=selectField('Recebimento','mode',modes,'counter')+
        '<div class="os-form-grid">'+field('Buscar produto','product_search','search','','maxlength="120"')+
        field('Buscar cliente','customer_search','search','','maxlength="120" placeholder="Nome ou telefone"')+'</div>'+
        '<div class="os-actions">'+command(button,'Buscar produtos','delivery-choose-items','box')+
        command(button,'Buscar clientes','delivery-choose-customer','users')+'</div>'+
        '<div data-delivery-search-results aria-live="polite"></div><div data-delivery-customer aria-live="polite"></div>'+
        '<h3>Itens do pedido</h3><div class="os-form" data-delivery-cart></div>'+
        '<div class="os-actions">'+command(button,'Conferir total','delivery-estimate','check')+'</div>'+
        '<div data-delivery-estimate aria-live="polite"></div>';
      const items=()=>[...cart].map(([id,item])=>({variant_id:id,quantity:window.OrionFields.parseDecimal(item.quantity,{required:true,scale:0,min:1,max:1000})}));
      openEditor('Novo pedido',content,async(form,values,key)=>{
        const lines=items();
        if (values.mode==='pickup'&&!customer) throw Error('Selecione um cliente para a retirada.');
        if (!estimate||estimate.lines!==JSON.stringify(lines)) throw Error('Confira o total antes de confirmar o pedido.');
        const payload={mode:values.mode,customer_id:customer?.id||null,items:lines,estimate_version:estimate.version};
        const saved=await call('/orders','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.status!=='confirmed') throw Error('Não foi possível confirmar a reserva.');
        filters.offset=0;
      },'Pedido reservado.',{label:'Confirmar pedido',bind:dialog=>{
        const form=dialog.querySelector('form'), matches=form.querySelector('[data-delivery-search-results]');
        const quote=form.querySelector('[data-delivery-estimate]'), basket=form.querySelector('[data-delivery-cart]');
        const customerInfo=form.querySelector('[data-delivery-customer]');
        let products=[], customers=[], searchVersion=0, quoteVersion=0;
        const dialogActive = () => current()&&dialog.open&&form.isConnected;
        const invalidate = () => {quoteVersion++;estimate=null;quote.replaceChildren();};
        function showCart() {
          basket.innerHTML=cart.size?Array.from(cart,([id,row])=>
            '<div class="os-form-grid">'+field(row.label,'qty-'+id,'number',row.quantity,
              'required min="1" max="1000" step="1" '+itemAttr('quantity',id))+
            '<div class="os-actions">'+command(button,'Remover','delivery-remove-item','close',itemAttr('match',id))+'</div></div>').join(''):
            '<p class="os-help">Busque um produto para adicionar itens.</p>';
          window.OrionFields.refresh(basket);
        }
        showCart();
        form.addEventListener('input',event=>{
          const id=event.target.dataset.deliveryQuantity;
          if (!id||!cart.has(id)) return;
          cart.get(id).quantity=event.target.value;
          invalidate();
        });
        form.addEventListener('click',async event=>{
          const node=event.target.closest('button'), action=node?.dataset.osAction;
          if (!action?.startsWith('delivery-')||!current()) return;
          event.stopPropagation();
          const id=node.dataset.deliveryMatch;
          if (action==='delivery-remove-item') {cart.delete(id);invalidate();showCart();return;}
          if (action==='delivery-add-item') {
            const item=products.find(p=>p.variant_id===id);
            if (!item||(!cart.has(id)&&cart.size>=30)) return;
            cart.set(id,{label:item.name+' · '+item.variant_name,quantity:cart.get(id)?.quantity||'1'});
            invalidate();showCart();basket.querySelector('[data-delivery-quantity="'+id+'"]')?.focus();return;
          }
          if (action==='delivery-pick-customer') {
            customer=customers.find(c=>c.id===id)||null;
            customerInfo.textContent=customer?'Cliente: '+customer.name:'';
            matches.replaceChildren();return;
          }
          if (action==='delivery-estimate') {
            invalidate();
            let lines;
            try {lines=items();} catch(error) {quote.innerHTML=err(error);return;}
            if (!lines.length) {quote.innerHTML=err(Error('Adicione pelo menos um produto.'));return;}
            const key=JSON.stringify(lines), sequence=quoteVersion;
            const latest = () => dialogActive()&&sequence===quoteVersion;
            quote.innerHTML=loading('Conferindo o total…');
            try {
              const result=await call('/orders/estimate','POST',{items:lines});
              if (!latest()) return;
              if (!result.confirmation_requirements_met) throw Error('Confira o preço e o estoque dos itens selecionados.');
              estimate={version:result.estimate_version,lines:key};
              quote.innerHTML='<p><strong>Total</strong> '+money(result.total_cents)+'</p>';
            } catch(error) {if (latest()) quote.innerHTML=err(error);}
            return;
          }
          if (!['delivery-choose-items','delivery-choose-customer'].includes(action)) return;
          const sequence=++searchVersion;
          const latest = () => dialogActive()&&sequence===searchVersion;
          matches.innerHTML=loading('Buscando…');
          try {
            if (action==='delivery-choose-items') {
              const result=await call('/catalog?'+query({state:'active',search:form.elements.product_search.value}));
              if (!latest()) return;
              products=result.items;
              matches.innerHTML=products.length?'<div class="os-actions">'+products.map(item=>
                command(button,item.name+' · '+item.variant_name,'delivery-add-item','plus',itemAttr('match',item.variant_id))).join('')+'</div>':
                '<p class="os-help">Nenhum produto encontrado.</p>';
              if (result.has_more) matches.insertAdjacentHTML('beforeend','<p class="os-help">Refine a busca para ver outros produtos.</p>');
            } else {
              const search=form.elements.customer_search.value.trim();
              if (search.length<2) throw Error('Digite ao menos dois caracteres para buscar o cliente.');
              const result=await call('/order-customers?'+query({search}));
              if (!latest()) return;
              customers=result.items;
              matches.innerHTML=customers.length?'<div class="os-actions">'+customers.map(c=>
                command(button,[c.name,c.whatsapp_e164].filter(Boolean).join(' · '),'delivery-pick-customer','user',itemAttr('match',c.id))).join('')+'</div>':
                '<p class="os-help">Nenhum cliente encontrado.</p>';
            }
          } catch(error) {if (latest()) matches.innerHTML=err(error);}
        });
      }});
    }

    function move(order, action) {
      const complete=action==='fulfill';
      const hasAmount=order.total_cents>0;
      const content='<p>Pedido '+esc(code(order))+' · '+money(order.total_cents)+'</p>'+
        (complete&&hasAmount?selectField('Forma recebida','payment_method',payments,'','required')+
          checkField('Confirmo que recebi o pagamento','received'):
          complete?'<p>Confirme a entrega no balcão e a saída dos itens.</p>':'<p>O estoque reservado será liberado.</p>');
      const label=complete?'Concluir pedido':'Cancelar pedido';
      openEditor(label,content,async(form,values,key)=>{
        if (complete&&hasAmount&&!form.elements.received.checked) throw Error('Confirme o recebimento para concluir o pedido.');
        const payload={action,expected_revision:order.revision,payment_method:complete&&hasAmount?values.payment_method:null};
        const saved=await call('/orders/'+encodeURIComponent(order.id)+'/transitions','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.status!==(complete?'fulfilled':'cancelled')) throw Error('Confira o estado do pedido antes de repetir.');
      },complete?'Pedido concluído.':'Pedido cancelado.',{label});
    }

    host.addEventListener('submit',event=>{
      if (event.target.id!=='osDeliveryOrderFilters') return;
      event.preventDefault();
      Object.assign(filters,Object.fromEntries(new FormData(event.target)),{offset:0});
      void load();
    });
    host.addEventListener('click',async event=>{
      const el=event.target.closest('button'), action=el?.dataset.osAction;
      if (!action?.startsWith('delivery-')||!current()) return;
      event.stopPropagation();
      if (action==='delivery-order-retry') {await load();return;}
      if (action==='delivery-order-prev'||action==='delivery-order-next') {
        filters.offset=Math.max(0,data.offset+(action==='delivery-order-next'?data.limit:-data.limit));
        await load();return;
      }
      if (action==='delivery-new-order'&&canWrite()) {editor();return;}
      const order=data?.items?.find(item=>item.id===el.dataset.deliveryOrder);
      if (!order) return;
      if (action==='delivery-order-detail') {
        try {
          const result=await call('/orders/'+encodeURIComponent(order.id));
          if (!current()) return;
          openDetails('Pedido '+code(order),'<p>'+badgeFor(result.status)+' · '+money(result.total_cents)+'</p><ul>'+result.items.map(item=>
            '<li>'+esc(item.quantity+' × '+item.product_name+' · '+item.variant_name)+' · '+money(item.total_cents)+'</li>').join('')+
            '</ul><h3>Histórico</h3><ul>'+result.history.map(event=>
              '<li>'+esc((statusLabels[event.action]||event.action)+' · '+when(event.created_at))+'</li>').join('')+'</ul>');
        } catch(error) {if (current()) openDetails('Pedido '+code(order),err(error));}
      } else if (['delivery-order-fulfill','delivery-order-cancel'].includes(action)&&order.status==='confirmed'&&canWrite()) {
        move(order,action==='delivery-order-fulfill'?'fulfill':'cancel');
      }
    });
    await load();
  }
  window.OrionDeliveryOrders=Object.freeze({mount});
})();
