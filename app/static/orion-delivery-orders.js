/* Manual orders and delivery dispatch in the Orion management workspace. */
(function () {
  'use strict';

  const statusLabels={confirmed:'Reservado',dispatched:'Em rota',fulfilled:'Concluído',cancelled:'Cancelado'};
  const modes=[['counter','Balcão'],['pickup','Retirada'],['delivery','Entrega']];
  const payments=[['','Selecione'],['cash','Dinheiro'],['debit_card','Cartão de débito'],['credit_card','Cartão de crédito']];
  const creditStatuses=[['all','Todos'],['pending','Pendentes'],['overdue','Em atraso'],['paid','Recebidos']];
  const command = (button,label,action,symbol,extra='') => button(label,action,'os-outline os-small',symbol,extra);
  const code = order => order.id.slice(0,8).toUpperCase();
  const modeLabel = order => modes.find(([key])=>key===order.mode)?.[1];

  async function dashboard({host,filters,call,allowed,ui}) {
    const {esc,loading,field,title,money,metric,seriesChart,routeButton,dateKey,query}=ui;
    if (!host || !allowed()) return;
    filters.day ??= '';
    host.innerHTML=title('Dashboard','Delivery e Distribuição')+
      '<form class="os-filter-row">'+field('Data','delivery_day','date',filters.day||dateKey(),'required min="1900-01-01" max="2100-12-31"')+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
      '<div data-delivery-overview aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const form=host.querySelector('form'), panel=host.querySelector('[data-delivery-overview]');
    let revision=0;
    async function read() {
      const attempt=++revision;
      const current = () => allowed()&&host.isConnected&&attempt===revision;
      panel.innerHTML=loading('Carregando indicadores…');
      try {
        const data=await call('/overview?'+query({day:filters.day}));
        if (!current()) return;
        form.elements.delivery_day.value=data.date;
        const {orders,fulfilled,open_deliveries,paid_cents}=data.metrics;
        const summaries='<div class="os-metrics">'+metric('Pedidos do dia',orders,'list')+
          metric('Concluídos',fulfilled,'check')+metric('Entregas pendentes',open_deliveries,'send')+
          metric('Valor recebido',money(paid_cents),'wallet')+'</div>';
        const listSection=(heading,rows,noRows,action)=>'<section class="os-card os-section-gap">'+
          '<div class="os-card-header"><h2>'+heading+'</h2>'+action+'</div>'+
          (rows.length?rows.join(''):'<p class="os-help">'+noRows+'</p>')+'</section>';
        const recent=data.recent.map(order=>'<div class="os-summary-row"><span>'+esc('Pedido '+code(order)+' · '+
          modeLabel(order)+' · '+statusLabels[order.status])+'</span><strong>'+money(order.total_cents)+'</strong></div>');
        const stock=data.low_stock.map(item=>'<div class="os-summary-row"><span>'+esc(item.name+' · '+item.variant_name)+
          '</span><strong>'+esc((item.quantity_on_hand-item.reserved_quantity)+' '+item.unit+' disponíveis')+'</strong></div>');
        panel.innerHTML=summaries+'<section class="os-card os-section-gap"><h2>Recebimentos nos últimos 7 dias</h2>'+
          seriesChart(data.daily,[['income_cents','Recebido','success']])+'</section>'+
          listSection('Pedidos recentes',recent,'Nenhum pedido nesta data.',routeButton('Ver pedidos','pedidos-delivery','os-outline os-small','list'))+
          listSection('Estoque baixo',stock,'Nenhum alerta de reposição.',routeButton('Ver estoque','produtos-delivery','os-outline os-small','box'));
      } catch (error) {
        if (current()) panel.innerHTML='<div class="os-notice os-error" role="alert"><p>'+esc(error.message)+'</p></div>';
      }
    }
    form.addEventListener('submit',event=>{
      event.preventDefault();
      filters.day=form.elements.delivery_day.value;
      void read();
    });
    await read();
  }

  async function mount({host,filters,call,allowed,canWrite,ui,openDetails,openEditor,deliveryOnly=false,creditOnly=false}) {
    if (!host || !allowed()) return;
    const {esc,loading,empty,field,moneyField,selectField,checkField,button,title,money,badge,fmtDate,dateKey,timeLabel,query,brazilianStates,metric,routeButton}=ui;
    filters.search ??= '';
    filters.state ??= 'all';
    filters.start ??= '';
    filters.end ??= '';
    filters.offset ??= 0;
    let data, stamp=0;
    const current = () => allowed() && host.isConnected;
    const when = value => fmtDate(dateKey(new Date(value)))+' '+timeLabel(value);
    const badgeFor = state => badge(state==='fulfilled'?'completed':['confirmed','dispatched'].includes(state)?'pending':state,statusLabels[state]);
    const itemAttr = (fieldName, id) => 'data-delivery-'+fieldName+'="'+esc(id)+'"';
    const err = error => '<div class="os-notice os-error" role="alert"><p>'+esc(error.message || 'Não foi possível concluir a ação.')+'</p></div>';

    host.innerHTML=title(creditOnly?'Vendas a prazo':deliveryOnly?'Entregas':'Pedidos',
      creditOnly?'Vencimentos e recebimentos':deliveryOnly?'Despacho e acompanhamento':'Balcão, retirada e entrega',
      creditOnly?routeButton('Ver pedidos','pedidos-delivery','os-outline os-small','list'):
      canWrite()?button('Novo pedido','delivery-new-order','','plus'):'')+
      '<form id="osDeliveryOrderFilters" class="os-filter-row">'+
      field('Buscar','search','search',filters.search,'maxlength="120" placeholder="Número ou cliente"')+
      selectField('Status','state',creditOnly?creditStatuses:[['all','Todos'],...Object.entries(statusLabels)],filters.state)+
      (creditOnly?'':field('De','start','date',filters.start)+field('Até','end','date',filters.end))+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form><div id="osDeliveryOrderList" aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const list=host.querySelector('#osDeliveryOrderList');

    function orderCard(order) {
      const actions = itemAttr('order',order.id);
      let controls=command(button,'Detalhes','delivery-order-detail','list',actions);
      if (canWrite()&&['confirmed','dispatched'].includes(order.status)) {
        controls+=order.mode==='delivery'&&order.status==='confirmed'
          ?command(button,'Despachar','delivery-order-dispatch','send',actions)
          :command(button,'Concluir','delivery-order-fulfill','check',actions);
        controls+=button('Cancelar pedido','delivery-order-cancel','os-ghost os-small','close',actions);
      }
      const pending=order.receipt_status==='pending';
      const late=pending&&order.payment_due_date<dateKey();
      if (canWrite()&&order.payment_due_date&&pending) {
        controls+=command(button,'Registrar recebimento','delivery-order-settle','wallet',actions);
      }
      const payment=order.payment_due_date?'<p class="os-help">Vencimento '+fmtDate(order.payment_due_date)+'</p>'+
        badge(pending?(late?'expired':'pending'):'paid',pending?(late?'Em atraso':'A receber'):'Recebido'):'';
      return '<article class="os-card os-item-card"><h3>Pedido '+esc(code(order))+'</h3><p class="os-help">'+
        esc([order.customer_name||'Venda avulsa',modeLabel(order),when(order.created_at)].join(' · '))+'</p>'+
        (order.courier_name?'<p class="os-help">Entregador: '+esc(order.courier_name)+'</p>':'')+
        '<div class="os-actions">'+badgeFor(order.status)+' <strong>'+money(order.total_cents)+'</strong></div>'+
        payment+
        '<div class="os-actions">'+controls+'</div></article>';
    }

    async function load() {
      const sequence=++stamp;
      list.innerHTML=loading('Carregando pedidos…');
      try {
        const suffix=creditOnly?'/credit?'+query({search:filters.search,status:filters.state,offset:filters.offset}):
          '/orders?'+query({...filters,mode:deliveryOnly?'delivery':'all'});
        const response=await call(suffix);
        if (!current()||sequence!==stamp) return;
        data=response;
        let html=data.items.length?'<div class="os-cards-grid">'+data.items.map(orderCard).join('')+'</div>':
          '<div class="os-card">'+empty('list',creditOnly?'Nenhuma venda a prazo encontrada':'Nenhum pedido encontrado',
            creditOnly?'Confira os filtros de cliente e vencimento.':'Confira o filtro ou registre um novo pedido.')+'</div>';
        if(creditOnly) html='<div class="os-metrics">'+metric('A receber',money(data.summary.open_cents),'wallet')+
          metric('Em atraso',money(data.summary.overdue_cents),'clock')+'</div>'+html;
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
      const addressFields='<fieldset data-delivery-address hidden disabled class="os-form">'+
        '<legend>Endereço de entrega</legend><div class="os-form-grid">'+
        field('CEP','address_postal','text','','data-orion-field="postal-code" inputmode="numeric" autocomplete="postal-code"')+
        field('Rua','address_street','text','','required maxlength="160"')+
        field('Número','address_number','text','','required maxlength="30"')+checkField('Sem número','address_no_number')+
        field('Bairro','address_neighborhood','text','','required maxlength="120"')+
        field('Cidade','address_city','text','','required maxlength="120"')+
        selectField('UF','address_state',[['','Selecione'],...brazilianStates.map(state=>[state,state])],'','required')+
        field('Complemento ou referência','address_complement','text','','maxlength="160"')+
        moneyField('Taxa de entrega','delivery_fee','text',window.OrionFields.moneyDisplay(0))+
        '</div></fieldset>';
      const content=selectField('Recebimento','mode',deliveryOnly?modes.slice(2):modes,deliveryOnly?'delivery':'counter')+
        '<div class="os-form-grid">'+field('Buscar produto','product_search','search','','maxlength="120"')+
        field('Buscar cliente','customer_search','search','','maxlength="120" placeholder="Nome ou telefone"')+'</div>'+
        '<div class="os-actions">'+command(button,'Buscar produtos','delivery-choose-items','box')+
        command(button,'Buscar clientes','delivery-choose-customer','users')+'</div>'+
        '<div data-delivery-search-results aria-live="polite"></div><div data-delivery-customer aria-live="polite"></div>'+
        '<h3>Itens do pedido</h3><div class="os-form" data-delivery-cart></div>'+
        addressFields+
        '<div class="os-actions">'+command(button,'Conferir total','delivery-estimate','check')+'</div>'+
        '<div data-delivery-estimate aria-live="polite"></div>';
      const items=()=>[...cart].map(([id,item])=>({variant_id:id,quantity:window.OrionFields.parseDecimal(item.quantity,{required:true,scale:0,min:1,max:1000})}));
      const estimateInput=form=>{
        const mode=form.elements.mode.value;
        return {mode,items:items(),delivery_fee_cents:mode==='delivery'?
          window.OrionFields.parseMoney(form.elements.delivery_fee.value,{required:true,min:0,max:1000000000}):0};
      };
      openEditor('Novo pedido',content,async(form,values,key)=>{
        const quoted=estimateInput(form);
        if (quoted.mode!=='counter'&&!customer) throw Error('Selecione um cliente para a retirada ou entrega.');
        if (!estimate||estimate.key!==JSON.stringify(quoted)) throw Error('Confira o total antes de confirmar o pedido.');
        const address=quoted.mode==='delivery'?{
          postal_code:window.OrionFields.postalCodeValue(values.address_postal),
          street:values.address_street,number:values.address_number||null,no_number:form.elements.address_no_number.checked,
          neighborhood:values.address_neighborhood,city:values.address_city,state:values.address_state,
          complement:values.address_complement||null,
        }:null;
        const payload={...quoted,address,customer_id:customer?.id||null,estimate_version:estimate.version};
        const saved=await call('/orders','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.status!=='confirmed') throw Error('Não foi possível confirmar a reserva.');
        filters.offset=0;
      },'Pedido reservado.',{label:'Confirmar pedido',bind:dialog=>{
        const form=dialog.querySelector('form'), matches=form.querySelector('[data-delivery-search-results]');
        const quote=form.querySelector('[data-delivery-estimate]'), basket=form.querySelector('[data-delivery-cart]');
        const customerInfo=form.querySelector('[data-delivery-customer]');
        const addressBox=form.querySelector('[data-delivery-address]');
        let products=[], customers=[], searchVersion=0, quoteVersion=0;
        const dialogActive = () => current()&&dialog.open&&form.isConnected;
        const invalidate = () => {quoteVersion++;estimate=null;quote.replaceChildren();};
        function updateAddress() {
          addressBox.hidden=addressBox.disabled=form.elements.mode.value!=='delivery';
          form.elements.address_number.disabled=form.elements.address_no_number.checked;
        }
        function showCart() {
          basket.innerHTML=cart.size?Array.from(cart,([id,row])=>
            '<div class="os-form-grid">'+field(row.label,'qty-'+id,'number',row.quantity,
              'required min="1" max="1000" step="1" '+itemAttr('quantity',id))+
            '<div class="os-actions">'+command(button,'Remover','delivery-remove-item','close',itemAttr('match',id))+'</div></div>').join(''):
            '<p class="os-help">Busque um produto para adicionar itens.</p>';
          window.OrionFields.refresh(basket);
        }
        showCart();
        updateAddress();
        form.addEventListener('change',event=>{
          if (event.target.name==='mode') {updateAddress();invalidate();}
          if (event.target.name==='address_no_number') updateAddress();
        });
        form.addEventListener('input',event=>{
          if (event.target.name==='delivery_fee') invalidate();
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
            let input;
            try {input=estimateInput(form);} catch(error) {quote.innerHTML=err(error);return;}
            if (!input.items.length) {quote.innerHTML=err(Error('Adicione pelo menos um produto.'));return;}
            const key=JSON.stringify(input), sequence=quoteVersion;
            const latest = () => dialogActive()&&sequence===quoteVersion;
            quote.innerHTML=loading('Conferindo o total…');
            try {
              const result=await call('/orders/estimate','POST',input);
              if (!latest()) return;
              if (!result.confirmation_requirements_met) throw Error('Confira o preço e o estoque dos itens selecionados.');
              estimate={version:result.estimate_version,key};
              quote.innerHTML=(input.mode==='delivery'?'<p>Produtos '+money(result.subtotal_cents)+
                ' · Taxa '+money(result.delivery_fee_cents)+'</p>':'')+'<p><strong>Total</strong> '+money(result.total_cents)+'</p>';
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
      const settle=action==='settle';
      const dispatch=action==='dispatch';
      const returning=action==='cancel'&&order.status==='dispatched';
      const hasAmount=order.total_cents>0;
      const methods=complete&&order.customer_id?[...payments,['on_account','A prazo']]:payments;
      const deferred=complete&&order.customer_id?
        '<fieldset class="hidden" data-delivery-due hidden disabled>'+
        field('Vencimento','payment_due_date','date',dateKey(),'required min="'+dateKey()+'" max="2100-12-31"')+
        checkField('Confirmo que o valor ficará a receber','confirmed_credit')+'</fieldset>':'';
      const content='<p>Pedido '+esc(code(order))+' · '+money(order.total_cents)+'</p>'+
        ((complete&&hasAmount)||settle?selectField('Forma de pagamento','payment_method',methods,'','required')+
          '<fieldset data-delivery-received>'+checkField('Confirmo que recebi o pagamento','received')+'</fieldset>'+deferred:
          complete?'<p>Confirme que o cliente recebeu os itens.</p>':
          dispatch?field('Entregador','courier_name','text','','required maxlength="120"'):
          returning?checkField('Confirmo que os itens voltaram ao estoque','returned_to_stock'):
          '<p>O estoque reservado será liberado.</p>');
      const label=complete?'Concluir pedido':settle?'Registrar recebimento':dispatch?'Despachar pedido':'Cancelar pedido';
      openEditor(label,content,async(form,values,key)=>{
        const credit=values.payment_method==='on_account';
        if ((complete&&hasAmount)||settle) {
          const checked=credit?form.elements.confirmed_credit.checked:form.elements.received.checked;
          if (!checked) throw Error(credit?'Confirme a venda a prazo.':'Confirme que recebeu o pagamento.');
        }
        const payload={action,expected_revision:order.revision,payment_method:values.payment_method||null};
        if (credit) payload.payment_due_date=values.payment_due_date;
        if (dispatch) payload.courier_name=values.courier_name;
        if (returning) {
          if (!form.elements.returned_to_stock.checked) throw Error('Confirme a devolução antes de liberar o estoque.');
          payload.returned_to_stock=true;
        }
        const saved=await call('/orders/'+encodeURIComponent(order.id)+'/transitions','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.status!==((complete||settle)?'fulfilled':dispatch?'dispatched':'cancelled')) throw Error('Confira o estado do pedido antes de repetir.');
      },complete?'Pedido concluído.':settle?'Recebimento registrado.':dispatch?'Pedido em rota.':'Pedido cancelado.',{label,bind:dialog=>{
        const due=dialog.querySelector('[data-delivery-due]');
        if (!due) return;
        const form=dialog.querySelector('form'), received=dialog.querySelector('[data-delivery-received]');
        form.elements.payment_method.addEventListener('change',()=>{
          const credit=form.elements.payment_method.value==='on_account';
          due.hidden=due.disabled=!credit;
          due.classList.toggle('hidden',!credit);
          received.hidden=received.disabled=credit;
          received.classList.toggle('hidden',credit);
          form.elements.received.checked=false;
          form.elements.confirmed_credit.checked=false;
        });
      }});
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
          const address=result.address;
          const addressText=address?'<div class="os-card"><h3>Entrega</h3><p>'+esc([
            address.street+(address.number?', '+address.number:' (sem número)'),address.neighborhood,address.city+' - '+address.state,
            address.postal_code,address.complement,
          ].filter(Boolean).join(' · '))+'</p><p>Taxa '+money(result.delivery_fee_cents)+'</p>'+
          (result.courier_name?'<p>Entregador: '+esc(result.courier_name)+'</p>':'')+'</div>':'';
          openDetails('Pedido '+code(order),'<p>'+badgeFor(result.status)+' · '+money(result.total_cents)+'</p>'+addressText+'<ul>'+result.items.map(item=>
            '<li>'+esc(item.quantity+' × '+item.product_name+' · '+item.variant_name)+' · '+money(item.total_cents)+'</li>').join('')+
            '</ul><h3>Histórico</h3><ul>'+result.history.map(event=>
              '<li>'+esc((event.action==='received'?'Recebimento registrado':statusLabels[event.action]||event.action)+' · '+when(event.created_at))+'</li>').join('')+'</ul>');
        } catch(error) {if (current()) openDetails('Pedido '+code(order),err(error));}
      } else if (action==='delivery-order-settle'&&canWrite()&&order.receipt_status==='pending') {
        move(order,'settle');
      } else if (canWrite()&&['confirmed','dispatched'].includes(order.status)) {
        const transition={'delivery-order-fulfill':'fulfill','delivery-order-dispatch':'dispatch','delivery-order-cancel':'cancel'}[action];
        if (transition) move(order,transition);
      }
    });
    await load();
  }
  window.OrionDeliveryOrders=Object.freeze({mount,dashboard});
})();
