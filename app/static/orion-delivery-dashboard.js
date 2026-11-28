/* The Delivery overview uses the same order editor and detail dialog as Pedidos. */
(function () {
  'use strict';

  async function mount({host,filters,company,call,allowed,canWrite,ui,openDetails,openEditor,openPage}) {
    if (!host || !allowed()) return;
    const {esc,icon,button,loading,money,fmtDate,dateKey,timeLabel,query,badge,seriesChart}=ui;
    const {openOrderEditor,showOrderDetail,code,modeLabel,statusBadge}=window.OrionDeliveryOrders;
    const current=()=>allowed()&&host.isConnected;
    let today=dateKey(), selected=filters.day||today, revision=0, detailRevision=0;
    let visibleOrders=new Set(),performanceRevision=0,lastPerformance=null,chartIndex=null;
    const chartMetrics={sales_cents:'Faturado',income_cents:'Recebido',orders:'Pedidos'};
    const formatInt=n=>new Intl.NumberFormat('pt-BR').format(n);
    const numeric=n=>Number.isFinite(Number(n))?Math.max(0,Number(n)):0;
    const metricValue=(n,key)=>key==='orders'?formatInt(n):money(n);
    const periods=[['today','Hoje'],['yesterday','Ontem'],['7d','Últimos 7 dias'],['30d','Últimos 30 dias'],
      ['90d','Últimos 90 dias'],['custom','Personalizado']];
    filters.performancePeriod??='7d';

    const newOrder=(className='')=>canWrite()?button('Novo pedido','delivery-dashboard-new',className,'plus'):'';
    const shortcut=(label,route,symbol,className='',scope='')=>
      '<button type="button" class="os-button '+className+'" data-delivery-page="'+route+'"'+
      (scope?' data-delivery-scope="'+scope+'"':'')+'>'+icon(symbol)+'<span>'+esc(label)+'</span></button>';

    // Decorative vectors are local and inherit theme colors. No example
    // sales, placeholder charts, fake company switcher or extra navigation.
    const heroArt='<svg class="odd-hero-art" viewBox="0 0 240 122" aria-hidden="true" fill="none">'+
      '<circle cx="177" cy="61" r="55" fill="currentColor" opacity=".07"/><circle cx="177" cy="61" r="39" stroke="currentColor" opacity=".12"/>'+
      '<path d="M15 99h207M22 99V72h19v27M48 99V56h18v43M199 99V51h15v48" stroke="currentColor" opacity=".17" stroke-width="2" stroke-linecap="round"/>'+
      '<path d="M27 45h61m-77 15h64m-55 17h34" stroke="currentColor" opacity=".23" stroke-width="3" stroke-linecap="round"/>'+
      '<rect x="80" y="38" width="83" height="55" rx="9" fill="currentColor" opacity=".94"/>'+
      '<path d="M163 55h22l20 21v17h-42z" fill="currentColor" opacity=".62"/><path d="M174 62h10l13 14h-23z" fill="var(--os-card)" opacity=".92"/>'+
      '<rect x="77" y="88" width="132" height="6" rx="3" fill="currentColor"/>'+
      '<circle cx="103" cy="94" r="12" fill="var(--os-card)" stroke="currentColor" stroke-width="5"/><circle cx="183" cy="94" r="12" fill="var(--os-card)" stroke="currentColor" stroke-width="5"/>'+
      '<path d="M108 55h26v17h-26zM108 55l13 8 13-8M121 63v9" stroke="white" stroke-width="2.2" stroke-linejoin="round"/>'+
      '<path d="M188 15a12 12 0 0 1 12 12c0 8-12 19-12 19s-12-11-12-19a12 12 0 0 1 12-12Z" fill="currentColor" opacity=".8"/><circle cx="188" cy="27" r="4" fill="var(--os-card)"/></svg>';
    host.innerHTML='<div class="os-delivery-dashboard">'+
      '<header class="os-delivery-dashboard-header"><div class="odd-heading">'+
      '<span class="odd-eyebrow">'+icon('deliveryStorefront')+' '+esc(company||'Delivery e Distribuição')+'</span>'+
      '<h1>Dashboard</h1><p>Vendas, pedidos e entregas em um só lugar.</p></div>'+
      heroArt+'<div class="os-delivery-dashboard-toolbar">'+newOrder('os-delivery-dashboard-new')+'</div></header>'+
      '<div class="odd-day-toolbar"><div><h2>Operação do dia</h2><p>Indicadores e atividades da data selecionada</p></div>'+
      '<div class="os-delivery-dashboard-date"><label for="osDeliveryDashboardDay">Data</label>'+
      '<div class="odd-date-input">'+icon('calendar')+'<input id="osDeliveryDashboardDay" type="date" lang="pt-BR" min="1900-01-01" max="2100-12-31" required value="'+esc(selected)+'"></div>'+
      button('Hoje','delivery-dashboard-today','os-outline')+'</div></div>'+
      '<div data-delivery-dashboard-content></div></div>';

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
      return '<div class="os-delivery-dashboard-empty"><div class="odd-empty-art" aria-hidden="true">'+
        '<svg viewBox="0 0 104 66" fill="none"><rect x="21" y="12" width="62" height="48" rx="15" fill="currentColor" opacity=".07"/>'+
        '<path d="M11 25h7m-4-4v8M87 12h7m-4-4v8M17 55h8M78 57h14" stroke="currentColor" stroke-width="2" stroke-linecap="round" opacity=".5"/></svg>'+icon(symbol)+'</div>'+
        '<strong>'+esc(message)+'</strong></div>';
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

    function pointLabel(row,hourly) {
      return hourly?String(row.hour).padStart(2,'0')+'h':fmtDate(row.date).slice(0,5);
    }

    function changeLabel(value,previous,key) {
      if(value==null||previous==null)return 'Sem base de comparação';
      if(!previous)return value?'Sem base no período anterior':'Sem movimento nos dois períodos';
      const change=(value-previous)/previous*100;
      const rounded=new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1,signDisplay:'exceptZero'}).format(change);
      return rounded+'% vs. anterior';
    }

    function periodStat(data,key,label) {
      const value=data.current[key],previous=data.previous[key];
      const shown=value==null?'—':metricValue(numeric(value),key);
      const tone=value!=null&&previous>0&&value!==previous?(value>previous?'up':'down'):'neutral';
      return '<div class="odd-period-stat"><span>'+label+'</span><strong>'+esc(shown)+'</strong><small data-delta="'+tone+'">'+
        esc(changeLabel(value,previous,key))+'</small></div>';
    }

    function performanceChart(data) {
      const hourly=data.granularity==='hour',rows=data.series||[];
      const key=Object.hasOwn(chartMetrics,filters.dashboardMetric)?filters.dashboardMetric:'sales_cents';
      const values=rows.map(row=>numeric(row[key]));
      const max=Math.max(key==='orders'?1:100,...values);
      const step=Math.pow(10,Math.floor(Math.log10(max/3)));
      const ceiling=Math.max(1,Math.ceil(max/(step||1))*(step||1));
      const top=12,bottom=190,left=12,right=588;
      const x=i=>values.length<=1?300:left+i*(right-left)/(values.length-1);
      const y=v=>bottom-v/ceiling*(bottom-top);
      const xy=values.map((v,i)=>[x(i),y(v)]);
      const line=xy.map(([a,b],i)=>(i?'L':'M')+a.toFixed(2)+' '+b.toFixed(2)).join(' ');
      const area=xy.length?line+' L'+xy.at(-1)[0]+' '+bottom+' L'+xy[0][0]+' '+bottom+' Z':'';
      const grid=[0,1,2,3].map(i=>'<line x1="0" x2="600" y1="'+(top+(bottom-top)*i/3)+'" y2="'+(top+(bottom-top)*i/3)+'"/>').join('');
      const ticks=[0,1,2,3].map(i=>metricValue(Math.round(ceiling*(3-i)/3),key));
      const indexes=[...new Set(Array.from({length:Math.min(hourly?5:6,rows.length)},(_,i)=>
        Math.round(i*(rows.length-1)/Math.max(1,Math.min(hourly?5:6,rows.length)-1))))];
      const selectedIndex=Math.max(0,Math.min(rows.length-1,chartIndex??Math.max(0,values.findLastIndex(v=>v>0))));
      chartIndex=selectedIndex;
      const marker=xy[selectedIndex]||[300,bottom];
      const bars=hourly?values.map((v,i)=>{
        const barWidth=576/Math.max(1,values.length);
        return '<rect x="'+(12+i*barWidth+barWidth*.15).toFixed(2)+'" y="'+y(v).toFixed(2)+'" width="'+(barWidth*.7).toFixed(2)+
          '" height="'+Math.max(0,bottom-y(v)).toFixed(2)+'" rx="2" class="odd-plot-bar'+(i===selectedIndex?' is-selected':'')+'"/>';
      }).join(''):'';
      return '<div class="odd-chart-controls"><div class="odd-segments" role="group" aria-label="Indicador do gráfico">'+
        Object.entries(chartMetrics).map(([id,label])=>'<button type="button" data-dashboard-metric="'+id+'" aria-pressed="'+(id===key)+'">'+label+'</button>').join('')+'</div>'+
        '<p class="odd-chart-readout" data-chart-readout aria-live="polite">'+esc(rows[selectedIndex]?pointLabel(rows[selectedIndex],hourly)+' · '+metricValue(values[selectedIndex],key):'Sem dados')+'</p></div>'+
        '<div class="odd-plot"><div class="odd-y-labels" aria-hidden="true">'+ticks.map(label=>'<span>'+esc(label)+'</span>').join('')+'</div>'+
        '<div class="odd-plot-main"><svg data-dashboard-plot viewBox="0 0 600 202" preserveAspectRatio="none" role="img" aria-label="'+esc(chartMetrics[key]+' por '+(hourly?'hora':'dia'))+'">'+
        '<defs><linearGradient id="oddChartFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="var(--os-primary)" stop-opacity=".23"/>'+
        '<stop offset="1" stop-color="var(--os-primary)" stop-opacity=".025"/></linearGradient></defs><g class="odd-plot-grid">'+grid+'</g>'+
        (hourly?bars:'<path d="'+area+'" fill="url(#oddChartFill)"/><path d="'+line+'" class="odd-plot-line"/>')+
        (!hourly&&xy.length?'<g data-chart-marker><line x1="'+marker[0]+'" x2="'+marker[0]+'" y1="'+top+'" y2="'+bottom+'" class="odd-plot-marker"/>'+
        '<circle cx="'+marker[0]+'" cy="'+marker[1]+'" r="4.5" class="odd-plot-dot"/></g>':'')+'</svg>'+
        '<div class="odd-x-labels">'+indexes.map(i=>'<span>'+esc(pointLabel(rows[i],hourly))+'</span>').join('')+'</div></div></div>'+
        (rows.length>1?'<label class="odd-scrubber"><span>Explorar '+(hourly?'horas':'datas')+'</span><input type="range" min="0" max="'+(rows.length-1)+'" value="'+selectedIndex+'" data-dashboard-scrub aria-label="Ponto do gráfico"></label>':'')+
        (!values.some(Boolean)?'<p class="odd-no-movement">Nenhum movimento para este indicador no período.</p>':'')+
        '<details class="os-delivery-dashboard-values"><summary>Valores por '+(hourly?'hora':'dia')+'</summary><div class="os-table-scroll"><table><thead><tr><th scope="col">'+(hourly?'Hora':'Dia')+'</th>'+
        '<th scope="col">Faturado</th><th scope="col">Recebido</th><th scope="col">Pedidos</th></tr></thead><tbody>'+rows.map(row=>
        '<tr><th scope="row">'+esc(pointLabel(row,hourly))+'</th><td>'+money(row.sales_cents)+'</td><td>'+money(row.income_cents)+'</td><td>'+formatInt(row.orders)+'</td></tr>'
        ).join('')+'</tbody></table></div></details>';
    }

    function updateChart(index) {
      if(!lastPerformance)return;
      const rows=lastPerformance.series||[],key=Object.hasOwn(chartMetrics,filters.dashboardMetric)?filters.dashboardMetric:'sales_cents';
      if(!rows.length)return;
      chartIndex=Math.max(0,Math.min(rows.length-1,Math.round(index)));
      const target=content.querySelector('[data-chart-readout]'),slider=content.querySelector('[data-dashboard-scrub]');
      if(target)target.textContent=pointLabel(rows[chartIndex],lastPerformance.granularity==='hour')+' · '+metricValue(numeric(rows[chartIndex][key]),key);
      if(slider)slider.value=String(chartIndex);
      const plot=content.querySelector('[data-dashboard-plot]');
      if(!plot)return;
      if(lastPerformance.granularity==='hour'){
        plot.querySelectorAll('.odd-plot-bar').forEach((bar,i)=>bar.classList.toggle('is-selected',i===chartIndex));
      }else{
        const values=rows.map(row=>numeric(row[key])),max=Math.max(key==='orders'?1:100,...values);
        const step=Math.pow(10,Math.floor(Math.log10(max/3))),ceiling=Math.max(1,Math.ceil(max/(step||1))*(step||1));
        const x=rows.length<=1?300:12+chartIndex*576/(rows.length-1),y=190-values[chartIndex]/ceiling*178;
        const line=plot.querySelector('.odd-plot-marker'),dot=plot.querySelector('.odd-plot-dot');
        if(line){line.setAttribute('x1',x);line.setAttribute('x2',x);}
        if(dot){dot.setAttribute('cx',x);dot.setAttribute('cy',y);}
      }
    }

    function statusChart(data) {
      if(!data.order_status)return emptyMessage('chart','Dados de status indisponíveis.');
      const specs=[['fulfilled','Concluídos','success'],['dispatched','Em rota','primary'],['confirmed','Reservados','warning'],['cancelled','Cancelados','muted'],['other','Outros','other']];
      const items=specs.map(([key,label,tone])=>({label,tone,value:numeric(data.order_status[key])})).filter(item=>item.tone!=='other'||item.value);
      const total=items.reduce((sum,item)=>sum+item.value,0);
      const circumference=2*Math.PI*47;
      let offset=0;
      const arcs=items.map(item=>{
        const length=total?item.value/total*circumference:0;
        const arc=length?'<circle cx="64" cy="64" r="47" class="odd-ring-part" data-ring-tone="'+item.tone+'" stroke-dasharray="'+length+' '+(circumference-length)+'" stroke-dashoffset="'+(-offset)+'"/>':'';
        offset+=length;return arc;
      }).join('');
      return '<div class="odd-status-content"><div class="odd-ring"><svg viewBox="0 0 128 128" aria-hidden="true"><circle cx="64" cy="64" r="47" class="odd-ring-track"/>'+
        '<g transform="rotate(-90 64 64)">'+arcs+'</g></svg><div><strong>'+formatInt(total)+'</strong><span>Pedidos</span></div></div>'+
        '<ul class="odd-status-legend">'+items.map(item=>'<li><i data-ring-tone="'+item.tone+'"></i><span>'+item.label+'</span><strong>'+formatInt(item.value)+'</strong></li>').join('')+'</ul></div>'+
        '<p class="odd-panel-note">Situação atual dos pedidos criados no período.</p>';
    }

    function peakHours(data) {
      const rows=data.hourly_orders;
      if(!Array.isArray(rows)||rows.length!==24)return emptyMessage('chart','Horários indisponíveis.');
      const max=Math.max(1,...rows.map(row=>numeric(row.orders)));
      const peak=rows.reduce((best,row)=>numeric(row.orders)>numeric(best.orders)?row:best,rows[0]);
      const fmt=row=>String(row.hour).padStart(2,'0')+'h';
      return '<div class="odd-peak-head"><strong>'+esc(peak.orders?fmt(peak):'—')+'</strong><span>'+esc(peak.orders?formatInt(peak.orders)+' pedidos na hora de pico':'Sem pedidos no período')+'</span></div>'+
        '<div class="odd-hour-bars" role="img" aria-label="Quantidade de pedidos por hora da empresa">'+rows.map(row=>'<div><span style="height:'+
          (numeric(row.orders)/max*100)+'%" title="'+esc(fmt(row)+' · '+formatInt(row.orders)+' pedidos')+'"'+(row.orders&&row.hour===peak.hour?' class="is-peak"':'')+'></span></div>').join('')+'</div>'+
        '<div class="odd-x-labels"><span>00h</span><span>06h</span><span>12h</span><span>18h</span><span>23h</span></div>'+
        '<details class="os-delivery-dashboard-values"><summary>Pedidos por horário</summary><table><thead><tr><th>Hora</th><th>Pedidos</th></tr></thead><tbody>'+
        rows.map(row=>'<tr><th scope="row">'+fmt(row)+'</th><td>'+formatInt(row.orders)+'</td></tr>').join('')+'</tbody></table></details>';
    }

    function productRanking(data) {
      if(!Array.isArray(data.top_products)||!data.top_products.length)return emptyMessage('box','Nenhum produto vendido no período.');
      const max=Math.max(1,...data.top_products.map(row=>numeric(row.quantity)));
      return '<ol class="odd-ranking">'+data.top_products.map((row,i)=>'<li><span class="odd-rank-number">'+(i+1)+'</span>'+
        '<span class="odd-rank-product"><strong>'+esc(row.product||'Produto')+'</strong><small>'+esc(row.variant||'')+'</small>'+
        '<span class="odd-rank-track"><i style="width:'+numeric(row.quantity)/max*100+'%"></i></span></span>'+
        '<span class="odd-rank-total"><strong>'+money(row.revenue_cents)+'</strong><small>'+formatInt(row.quantity)+' un.</small></span></li>').join('')+'</ol>'+
        '<p class="odd-panel-note">Vendas brutas por quantidade; valores dos produtos sem frete.</p>';
    }

    function periodForm(){
      return '<form data-delivery-performance class="os-filter-row odd-period-form">'+
        '<div class="odd-period-quick" role="group" aria-label="Períodos rápidos">'+[['today','Hoje'],['yesterday','Ontem'],['7d','7 dias'],['30d','30 dias']].map(([key,label])=>
          '<button type="button" data-dashboard-period="'+key+'" aria-label="'+label+' (desempenho)" aria-pressed="'+(filters.performancePeriod===key)+'">'+label+'</button>').join('')+'</div>'+
        ui.selectField('Período do desempenho','performance_period',periods,filters.performancePeriod)+
        '<fieldset class="os-filter-row" data-period-dates '+(filters.performancePeriod==='custom'?'':'hidden disabled')+'>'+
        ui.field('Início','performance_start','date',filters.performanceStart||selected,'required min="1900-01-01" max="2100-12-31"')+
        ui.field('Fim','performance_end','date',filters.performanceEnd||selected,'required min="1900-01-01" max="2100-12-31"')+
        '<button class="os-button os-outline" type="submit">Aplicar período</button></fieldset></form>'+
        '<div aria-live="polite" data-performance-results></div>';
    }

    async function loadPerformance(){
      const target=content.querySelector('[data-performance-results]'),attempt=++performanceRevision;
      if(!target)return;
      const params={period:filters.performancePeriod};
      if(params.period==='custom')Object.assign(params,{start:filters.performanceStart||selected,end:filters.performanceEnd||selected});
      target.setAttribute('aria-busy','true');
      lastPerformance=null;chartIndex=null;
      target.innerHTML=loading('Carregando desempenho…');
      try{
        const data=await call('/overview/performance?'+query(params));
        if(!current()||attempt!==performanceRevision||!target.isConnected)return;
        const metrics=[['Pedidos','orders'],['Concluídos','fulfilled'],['Cancelados ou devolvidos','cancelled'],
          ['Faturamento bruto','sales_cents'],['Recebido bruto','income_cents'],['Estornos','refunded_cents'],['Ticket médio','average_ticket_cents']];
        const display=(value,key)=>value==null?'—':key.endsWith('_cents')?money(value):String(value);
        lastPerformance=data;
        target.innerHTML='<p class="odd-period-date">'+icon('calendar')+' '+esc(fmtDate(data.start)+' a '+fmtDate(data.end))+'</p>'+
          '<div class="odd-period-stats">'+periodStat(data,'sales_cents','Faturamento bruto')+periodStat(data,'income_cents','Recebido bruto')+
          periodStat(data,'orders','Pedidos criados')+'</div>'+
          '<div class="odd-analytics-grid"><section class="odd-analysis-panel odd-main-chart"><h3>Evolução do desempenho</h3><div data-dashboard-chart>'+performanceChart(data)+'</div></section>'+
          '<section class="odd-analysis-panel"><h3>Status dos pedidos</h3>'+statusChart(data)+'</section>'+
          '<section class="odd-analysis-panel"><h3>Horários de maior movimento</h3>'+peakHours(data)+'</section>'+
          '<section class="odd-analysis-panel"><h3>Produtos mais vendidos</h3>'+productRanking(data)+'</section></div>'+
          '<details class="os-delivery-period-comparison"><summary>Comparar indicadores</summary><p class="os-help">Período anterior de '+
          esc(fmtDate(data.previous_start)+' a '+fmtDate(data.previous_end))+'</p><div class="os-table-scroll"><table class="os-table">'+
          '<thead><tr><th>Indicador</th><th>Período</th><th>Anterior</th></tr></thead><tbody>'+
          metrics.map(([label,key])=>'<tr><th scope="row">'+label+'</th><td>'+esc(display(data.current[key],key))+'</td><td>'+
            esc(display(data.previous[key],key))+'</td></tr>').join('')+'</tbody></table></div></details>';
      }catch(error){
        if(current()&&attempt===performanceRevision&&target.isConnected)target.innerHTML='<p role="alert">'+esc(error.message)+'</p>'+
          button('Tentar novamente','delivery-performance-retry','os-outline');
      }finally{if(attempt===performanceRevision)target.removeAttribute('aria-busy');}
    }

    function render(data) {
      const {orders,fulfilled,open_deliveries,sales_cents,paid_cents,credit_cents}=data.metrics;
      const operations=data.operations||{};
      const inRoute=operations.in_route==null?'—':formatInt(operations.in_route);
      const ticket=operations.average_ticket_cents==null?'—':money(operations.average_ticket_cents);
      visibleOrders=new Set([...data.recent,...data.deliveries].map(order=>order.id));
      content.innerHTML='<div class="os-delivery-dashboard-metrics" aria-label="Indicadores do dia">'+
        metric('Faturamento',money(sales_cents),'chart','revenue',money(paid_cents)+' recebidos')+
        metric('Pedidos do dia',orders,'deliveryReceipt','primary',fulfilled+(fulfilled===1?' concluído':' concluídos'))+
        metric('Entregas pendentes',open_deliveries,'deliveryTruck','warning','Inclui entregas em rota')+
        metric('Em rota',inRoute,'deliveryRoute','success','Entregas despachadas')+
        metric('Vendas a prazo',money(credit_cents),'deliveryDeferred','primary','Geradas no dia')+
        metric('Ticket médio',ticket,'box','violet',operations.average_ticket_cents==null?'Sem vendas concluídas':'Por venda concluída')+'</div>'+
        '<div class="os-delivery-dashboard-grid">'+
        section('week','Desempenho de vendas',periodForm(),'','Resultados por período, com comparação anterior')+
        section('orders','Pedidos do dia',orderRows(data.recent),shortcut('Ver pedidos','orders','chevron','os-ghost os-small','day'))+
        section('deliveries','Entregas do dia',orderRows(data.deliveries,true),shortcut('Ver entregas','deliveries','chevron','os-ghost os-small','day'))+
        section('credit','Clientes com vendas a prazo',creditRows(data.credit_customers),shortcut('Ver saldos','credit','chevron','os-ghost os-small'),'Saldos em aberto')+
        section('stock','Estoque baixo',stockRows(data.low_stock),shortcut('Ver estoque','stock','chevron','os-ghost os-small','low'))+'</div>';
    }

    async function read() {
      const attempt=++revision;
      const latest=()=>current()&&attempt===revision;
      detailRevision++;performanceRevision++;lastPerformance=null;
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
        void loadPerformance();
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
      filters.performancePeriod='custom';filters.performanceStart=selected;filters.performanceEnd=selected;
      void read();
    });

    host.addEventListener('input',event=>{
      if(event.target.matches('[data-dashboard-scrub]')&&current())updateChart(Number(event.target.value));
    });
    host.addEventListener('click',event=>{
      const plot=event.target.closest('[data-dashboard-plot]');
      if(!plot||!current()||!lastPerformance)return;
      const rect=plot.getBoundingClientRect();
      if(!rect.width)return;
      const ratio=Math.max(0,Math.min(1,((event.clientX-rect.left)/rect.width*600-12)/576));
      const n=lastPerformance.series.length;
      updateChart(lastPerformance.granularity==='hour'?Math.min(n-1,Math.floor(ratio*n)):ratio*(n-1));
    });
    host.addEventListener('change',event=>{
      if(event.target.name!=='performance_period'||!current())return;
      const custom=event.target.value==='custom', dates=event.target.form.querySelector('[data-period-dates]');
      dates.hidden=dates.disabled=!custom;
      filters.performancePeriod=event.target.value;
      content.querySelectorAll('[data-dashboard-period]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.dashboardPeriod===filters.performancePeriod)));
      if(!custom)void loadPerformance();
    });
    host.addEventListener('submit',event=>{
      if(!event.target.matches('[data-delivery-performance]'))return;
      event.preventDefault();
      const {performance_start:start,performance_end:end}=event.target.elements;
      if(start.value>end.value){end.setCustomValidity('O fim deve ser igual ou posterior ao início.');end.reportValidity();end.setCustomValidity('');return;}
      filters.performanceStart=start.value;filters.performanceEnd=end.value;
      void loadPerformance();
    });

    host.addEventListener('click',event=>{
      const target=event.target.closest('button');
      if (!target||!current()) return;
      const {osAction,deliveryPage,deliveryScope,deliveryDashboardOrder}=target.dataset;
      if(target.dataset.dashboardPeriod){
        const select=content.querySelector('[name="performance_period"]');
        if(select){select.value=target.dataset.dashboardPeriod;select.dispatchEvent(new Event('change',{bubbles:true}));}
      }else if(target.dataset.dashboardMetric&&lastPerformance&&Object.hasOwn(chartMetrics,target.dataset.dashboardMetric)){
        filters.dashboardMetric=target.dataset.dashboardMetric;
        const chart=content.querySelector('[data-dashboard-chart]');
        if(chart){
          chart.innerHTML=performanceChart(lastPerformance);
          chart.querySelector('[data-dashboard-metric="'+filters.dashboardMetric+'"]')?.focus({preventScroll:true});
        }
      }else if (osAction==='delivery-dashboard-new'&&canWrite()) {
        openOrderEditor({call,current,ui,openEditor,onCreated:()=>{filters.day='';}});
      } else if (osAction==='delivery-performance-retry'){
        void loadPerformance();
      } else if (osAction==='delivery-dashboard-today') {
        selected=picker.value=today;
        filters.day='';
        filters.performancePeriod='today';
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
