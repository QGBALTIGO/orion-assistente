/* The Delivery overview uses the same order editor and detail dialog as Pedidos. */
(function () {
  'use strict';

  async function mount({host,filters,company,call,allowed,canWrite,ui,openDetails,openEditor,openPage}) {
    if (!host || !allowed()) return;
    const {esc,icon,button,loading,money,fmtDate,dateKey,timeLabel,query,badge,seriesChart}=ui;
    const {openOrderEditor,showOrderDetail,code,modeLabel,statusBadge}=window.OrionDeliveryOrders;
    const current=()=>allowed()&&host.isConnected;
    let today=dateKey(), selected=filters.day||today, revision=0, detailRevision=0;
    let visibleOrders=new Set();

    const newOrder=(className='')=>canWrite()?button('Novo pedido','delivery-dashboard-new',className,'plus'):'';
    const shortcut=(label,route,symbol,className='',scope='')=>
      '<button type="button" class="os-button '+className+'" data-delivery-page="'+route+'"'+
      (scope?' data-delivery-scope="'+scope+'"':'')+'>'+icon(symbol)+'<span>'+esc(label)+'</span></button>';

    host.innerHTML='<div class="os-delivery-dashboard">'+
      '<header class="os-delivery-dashboard-header"><div><h1>Dashboard</h1><p>'+esc(company||'Delivery e Distribuição')+'</p></div>'+
      '<div class="os-delivery-dashboard-toolbar">'+
      '<div class="os-delivery-dashboard-date"><label for="osDeliveryDashboardDay">Data</label>'+
      '<input id="osDeliveryDashboardDay" type="date" lang="pt-BR" min="1900-01-01" max="2100-12-31" required value="'+esc(selected)+'">'+
      button('Hoje','delivery-dashboard-today','os-ghost')+'</div>'+newOrder('os-delivery-dashboard-new')+'</div></header>'+
      '<div data-delivery-dashboard-content></div>'+
      '<nav class="os-delivery-dashboard-dock" aria-label="Atalhos de Delivery">'+
      shortcut('Pedidos','orders','deliveryReceipt','os-ghost')+
      shortcut('Entregas','deliveries','deliveryTruck','os-ghost')+
      shortcut('Loja','store','deliveryStorefront','os-ghost')+newOrder('os-delivery-dashboard-dock-new')+'</nav></div>';

    const picker=host.querySelector('#osDeliveryDashboardDay');
    const todayButton=host.querySelector('[data-os-action="delivery-dashboard-today"]');
    const content=host.querySelector('[data-delivery-dashboard-content]');
    const orderTime=(order,delivery)=>{
      if (delivery&&order.scheduled_at) return 'Agendada às '+timeLabel(order.scheduled_at);
      return timeLabel(order.created_at);
    };

    function metric(label,value,symbol,tone,hint='') {
      return '<article class="os-card os-delivery-dashboard-metric" data-tone="'+tone+'">'+
        '<div class="os-delivery-dashboard-metric-label"><span class="os-square-icon">'+icon(symbol)+'</span><span>'+esc(label)+'</span></div>'+
        '<strong>'+esc(value)+'</strong>'+(hint?'<p>'+esc(hint)+'</p>':'')+'</article>';
    }

    function section(label,heading,inner,action='',caption='') {
      return '<section class="os-card os-delivery-dashboard-panel" data-delivery-section="'+label+'" aria-labelledby="osDeliveryHeading-'+label+'">'+
        '<header><div><h2 id="osDeliveryHeading-'+label+'">'+heading+'</h2>'+(caption?'<p>'+caption+'</p>':'')+'</div>'+action+'</header>'+inner+'</section>';
    }

    function emptyMessage(symbol,message) {
      return '<p class="os-delivery-dashboard-empty">'+icon(symbol)+'<span>'+message+'</span></p>';
    }

    function orderRows(orders,delivery=false) {
      if (!orders.length) return emptyMessage(delivery?'deliveryTruck':'deliveryReceipt',
        delivery?'Nenhuma entrega para esta data.':'Nenhum pedido para esta data.');
      return '<ul class="os-delivery-dashboard-rows">'+orders.map(order=>{
        const reference='Pedido '+code(order);
        const description=[reference,delivery?order.courier_name:modeLabel(order),orderTime(order,delivery)].filter(Boolean).join(' · ');
        return '<li><button class="os-delivery-dashboard-row" type="button" data-delivery-dashboard-order="'+esc(order.id)+'">'+
          '<span class="os-delivery-dashboard-row-copy"><strong>'+esc(order.customer_name||'Venda avulsa')+
          '</strong><small>'+esc(description)+'</small></span><span class="os-delivery-dashboard-row-amount"><strong>'+money(order.total_cents)+'</strong>'+
          statusBadge(order.status,badge)+'</span>'+icon('chevron')+'</button></li>';
      }).join('')+'</ul>';
    }

    function creditRows(customers) {
      if (!customers.length) return emptyMessage('deliveryDeferred','Nenhum saldo a receber.');
      return '<ul class="os-delivery-dashboard-rows">'+customers.map(customer=>
        '<li class="os-delivery-dashboard-row"><span class="os-delivery-dashboard-row-copy"><strong>'+esc(customer.name)+'</strong><small>'+
        esc(customer.orders+(customer.orders===1?' pedido a prazo':' pedidos a prazo'))+'</small></span><span class="os-delivery-dashboard-row-amount">'+
        '<strong>'+money(customer.outstanding_cents)+'</strong>'+(customer.overdue_cents?'<small>'+money(customer.overdue_cents)+' em atraso</small>':'')+
        '</span></li>').join('')+'</ul>';
    }

    function stockRows(items) {
      if (!items.length) return emptyMessage('box','Sem alertas de reposição.');
      return '<ul class="os-delivery-dashboard-rows">'+items.map(item=>
        '<li class="os-delivery-dashboard-row"><span class="os-delivery-dashboard-row-copy"><strong>'+esc(item.name)+'</strong><small>'+esc(item.variant_name)+
        '</small></span><span class="os-delivery-dashboard-row-amount"><strong>'+esc((item.quantity_on_hand-item.reserved_quantity)+' '+item.unit)+
        '</strong><small>disponíveis</small></span></li>').join('')+'</ul>';
    }

    function weeklyChart(daily) {
      if (!daily.some(day=>day.sales_cents||day.income_cents)) return emptyMessage('chart','Sem vendas concluídas ou recebimentos no período.');
      return seriesChart(daily,[['sales_cents','Faturado','primary'],['income_cents','Recebido','success']])+
        '<details class="os-delivery-dashboard-values"><summary>Valores por dia</summary><table><thead><tr><th scope="col">Dia</th>'+
        '<th scope="col">Faturado</th><th scope="col">Recebido</th></tr></thead><tbody>'+daily.map(day=>
        '<tr><th scope="row">'+esc(fmtDate(day.date).slice(0,5))+'</th><td>'+money(day.sales_cents)+'</td><td>'+money(day.income_cents)+'</td></tr>'
        ).join('')+'</tbody></table></details>';
    }

    function render(data) {
      const {orders,fulfilled,open_deliveries,sales_cents,paid_cents,credit_cents}=data.metrics;
      visibleOrders=new Set([...data.recent,...data.deliveries].map(order=>order.id));
      content.innerHTML='<div class="os-delivery-dashboard-metrics" aria-label="Indicadores do dia">'+
        metric('Pedidos do dia',orders,'deliveryReceipt','primary',fulfilled+(fulfilled===1?' concluído':' concluídos'))+
        metric('Faturamento',money(sales_cents),'chart','success',money(paid_cents)+' recebidos')+
        metric('Entregas pendentes',open_deliveries,'deliveryTruck','warning')+
        metric('Vendas a prazo',money(credit_cents),'deliveryDeferred','primary','Geradas no dia')+'</div>'+
        '<div class="os-delivery-dashboard-grid">'+
        section('orders','Pedidos do dia',orderRows(data.recent),shortcut('Ver pedidos','orders','chevron','os-ghost os-small','day'))+
        section('deliveries','Entregas do dia',orderRows(data.deliveries,true),shortcut('Ver entregas','deliveries','chevron','os-ghost os-small','day'))+
        section('week','Faturamento dos últimos 7 dias',weeklyChart(data.daily),'',
          esc(fmtDate(data.daily[0].date)+' a '+fmtDate(data.date)))+
        section('credit','Clientes com vendas a prazo',creditRows(data.credit_customers),shortcut('Ver saldos','credit','chevron','os-ghost os-small'),'Saldos em aberto')+
        section('stock','Estoque baixo',stockRows(data.low_stock),shortcut('Ver estoque','stock','chevron','os-ghost os-small','low'))+'</div>';
    }

    async function read() {
      const attempt=++revision;
      const latest=()=>current()&&attempt===revision;
      detailRevision++;
      visibleOrders.clear();
      todayButton.hidden=selected===today;
      content.setAttribute('aria-busy','true');
      content.innerHTML=loading('Carregando indicadores…');
      try {
        const data=await call('/overview?'+query({day:filters.day}));
        if (!latest()) return;
        today=data.today;
        selected=picker.value=data.date;
        todayButton.hidden=selected===today;
        render(data);
      } catch (error) {
        if (latest()) content.innerHTML='<div class="os-notice os-error" role="alert"><p>'+esc(error.message)+'</p>'+
          button('Tentar novamente','delivery-dashboard-retry','os-outline','refresh')+'</div>';
      } finally {
        if (latest()) content.removeAttribute('aria-busy');
      }
    }

    picker.addEventListener('change',()=>{
      if (!current()||!picker.reportValidity()) return;
      selected=filters.day=picker.value;
      void read();
    });

    host.addEventListener('click',event=>{
      const target=event.target.closest('button');
      if (!target||!current()) return;
      const {osAction,deliveryPage,deliveryScope,deliveryDashboardOrder}=target.dataset;
      if (osAction==='delivery-dashboard-new'&&canWrite()) {
        openOrderEditor({call,current,ui,openEditor,onCreated:()=>{filters.day='';}});
      } else if (osAction==='delivery-dashboard-today') {
        selected=picker.value=today;
        filters.day='';
        void read();
      } else if (osAction==='delivery-dashboard-retry') {
        void read();
      } else if (deliveryPage) {
        openPage(deliveryPage,{day:deliveryScope==='day'?selected:'',lowStock:deliveryScope==='low'});
      } else if (visibleOrders.has(deliveryDashboardOrder)) {
        const attempt=++detailRevision;
        void showOrderDetail({id:deliveryDashboardOrder,call,current:()=>current()&&attempt===detailRevision,openDetails,ui});
      }
    });
    await read();
  }

  window.OrionDeliveryDashboard=Object.freeze({mount});
})();
