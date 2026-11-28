/* Tabbed Delivery reports with the same criteria for the panel and CSV. */
(function () {
  'use strict';

  const modes=[['all','Todos'],['counter','Balcão'],['pickup','Retirada'],['delivery','Entrega']];
  const tabs={
    sales:{label:'Vendas'},
    products:{label:'Produtos', heading:'Vendas por produto', description:'Itens de pedidos concluídos criados no período', mode:true, dated:true, search:'Nome, variação ou SKU'},
    customers:{label:'Clientes', heading:'Vendas por cliente', description:'Pedidos concluídos criados no período', mode:true, dated:true, search:'Nome do cliente'},
    payments:{label:'Recebimentos', heading:'Recebimentos por forma de pagamento', description:'Entradas confirmadas e estornos no período. Não confunde preferências com recebimentos.', dated:true, search:'Cliente ou código do pedido', statusLabel:'Forma de pagamento',
      statuses:[['cash','Dinheiro'],['pix','Pix'],['debit_card','Débito'],['credit_card','Crédito'],['other','Outros / estorno misto']]},
    reorder:{label:'Recompra', heading:'Oportunidades de recompra', description:'Estimativas pelo intervalo entre pelo menos três dias distintos de compras do produto, nos últimos 365 dias. Não são notificações automáticas.', search:'Cliente ou produto', statusLabel:'Oportunidade',
      statuses:[['due','Na janela de recompra'],['soon','Nos próximos 2 dias'],['later','Mais adiante']]},
    inventory:{label:'Estoque', heading:'Posição de estoque', description:'Saldos registrados no momento da consulta', search:'Produto, variação, categoria ou SKU',
      statuses:[['low','Estoque baixo'],['stale','Contagem vencida'],['unknown','Sem contagem'],['inactive','Inativos']]},
    movements:{label:'Movimentações', heading:'Movimentações de estoque', description:'Registros de entrada, saída, reserva e baixa no período', dated:true, search:'Produto, variação ou SKU',
      statuses:[['initial','Saldo inicial'],['purchase','Entrada'],['count','Contagem'],['out','Saída'],['reserved','Reserva'],
        ['released','Liberação'],['sold','Venda'],['sale_returned','Devolução de pedido']]},
    credit:{label:'A receber', heading:'Vendas a prazo por vencimento', description:'Saldo atual dos pedidos com vencimento no período', dated:true, search:'Cliente ou código do pedido',
      statuses:[['pending','A receber'],['overdue','Em atraso'],['paid','Recebidos'],['refunded','Estornados'],['cancelled','Cancelados']]},
    containers:{label:'Vasilhames', heading:'Saldos de vasilhames', description:'Vazios no estoque e unidades com clientes no momento da consulta',
      search:'Nome do vasilhame', statusLabel:'Tipo', statuses:[['cylinder','Botijão'],['jug','Garrafão']]},
    activity:{label:'Atividades', heading:'Rastro de atividades', dated:true, search:'Produto, vasilhame ou código do pedido',
      statusLabel:'Área', statuses:[['orders','Pedidos'],['catalog','Catálogo'],['stock','Estoque'],['packaging','Vasilhames']]},
  };
  const names=Object.keys(tabs);
  const numbers=new Intl.NumberFormat('pt-BR');

  async function mount({host,filters,call,allowed,ui,exportPath}) {
    if (!host || !allowed()) return;
    const {button,esc,field,fmtDate,dateKey,loading,metric,money,query,selectField,timeLabel,title}=ui;
    filters.tab=Object.hasOwn(tabs,filters.tab)?filters.tab:'sales';
    filters.views??={};
    if (!filters.views.sales) {
      filters.views.sales=Object.fromEntries(['start','end','mode','state','receipt','offset']
        .filter(key=>Object.hasOwn(filters,key)).map(key=>[key,filters[key]]));
    }
    host.innerHTML=title('Relatórios','Analise vendas, recebimentos e estoque com os mesmos dados da operação.')+
      '<div class="os-pill-tabs od-tab-strip" role="tablist" aria-label="Relatórios de Delivery">'+names.map(name=>{
      const selected=name===filters.tab;
      return '<button type="button" class="os-pill-tab" id="osDeliveryReportTab-'+name+'" role="tab" aria-controls="osDeliveryReportPanel" '+
        'aria-selected="'+selected+'" tabindex="'+(selected?'0':'-1')+'" data-delivery-report-tab="'+name+'">'+esc(tabs[name].label)+'</button>';
    }).join('')+'</div><section id="osDeliveryReportPanel" role="tabpanel" aria-labelledby="osDeliveryReportTab-'+filters.tab+'"></section>';
    const bar=host.querySelector('[role="tablist"]');
    const shell=host.querySelector('#osDeliveryReportPanel');
    let revision=0;
    const current=()=>allowed()&&host.isConnected;

    function cell(style,value) {
      if (value===null||value===undefined||value==='') return '—';
      if (style==='money') return money(value);
      if (style==='number') return numbers.format(value);
      if (style==='date') return fmtDate(value);
      if (style==='datetime') return fmtDate(dateKey(new Date(value)))+' '+timeLabel(value);
      return value;
    }

    function operational(name,work,valid) {
      const view=tabs[name], values=filters.views[name]??={};
      values.offset??=0;
      const options=(offset=false)=>{
        const selected={search:values.search||''};
        if (view.dated) Object.assign(selected,{start:values.start||'',end:values.end||''});
        if (view.statuses) selected.status=values.status||'all';
        if (view.mode) selected.mode=values.mode||'all';
        if (offset) selected.offset=values.offset;
        return query(selected);
      };
      work.innerHTML='<div class="od-report-heading"><h2>'+esc(view.heading)+'</h2><p>'+esc(view.description||'')+'</p></div>'+
        '<form class="os-filter-row os-delivery-filters" aria-label="'+esc('Filtrar '+view.label.toLowerCase())+'">'+
        (view.dated?field('De','start','date',values.start||'')+field('Até','end','date',values.end||''):'')+
        field('Buscar','search','search',values.search||'','maxlength="120" placeholder="'+esc(view.search)+'"')+
        (view.mode?selectField('Atendimento','mode',modes,values.mode||'all'):'')+
        (view.statuses?selectField(view.statusLabel||'Situação','status',[['all','Todos'],...view.statuses],values.status||'all'):'')+
        '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
        '<div data-delivery-operational-result aria-live="polite"></div>';
      window.OrionFields.refresh(work);
      const form=work.querySelector('form'), panel=work.querySelector('[data-delivery-operational-result]');
      let request=0, result;
      function table(columns,items) {
        return '<div class="os-table-scroll" tabindex="0" role="region" aria-label="Tabela de dados"><table class="os-table"><thead><tr>'+
          columns.map(({label})=>'<th scope="col">'+esc(label)+'</th>').join('')+'</tr></thead><tbody>'+
          items.map(row=>'<tr>'+columns.map(({key,label,style})=>{
            const value=esc(cell(style,row[key]));
            const photo=key==='product' && window.OrionDeliveryEditor?.picture(row.image_url,esc);
            return '<td data-label="'+esc(label)+'">'+(photo?'<span class="os-delivery-shop-item-name">'+photo+value+'</span>':value)+'</td>';
          }).join('')+'</tr>').join('')+
          '</tbody></table></div>';
      }
      async function read() {
        const attempt=++request;
        const still=()=>valid()&&attempt===request;
        panel.innerHTML=loading('Carregando relatório…');
        try {
          const data=await call('/reports/operational/'+name+'?'+options(true));
          if (!still()) return;
          result=data;
          if (data.period) {
            form.elements.start.value=values.start=data.period.start_date;
            form.elements.end.value=values.end=data.period.end_date;
          }
          const csv=exportPath+'/operational/'+name+'/csv?'+options();
          const metrics='<div class="os-metrics">'+data.summary.map(({label,style,value,icon})=>
            metric(label,cell(style,value),icon)).join('')+'</div>';
          const rows=data.items.length?table(data.columns,data.items):'<p class="os-help">Nenhum registro encontrado para esses filtros.</p>';
          const navigation=(data.offset||data.has_more?'<div class="os-pagination"><span>Página '+(data.offset/data.limit+1)+'</span><div class="os-actions">'+
            button('Anterior','delivery-ops-prev','os-outline os-small','left',data.offset?'':'disabled')+
            button('Próxima','delivery-ops-next','os-outline os-small','chevron',data.has_more?'':'disabled')+'</div></div>':'');
          panel.innerHTML=metrics+'<section class="os-card os-section-gap"><div class="os-card-header"><h2>Resultados</h2>'+
            '<a class="os-button os-outline os-small" href="'+esc(csv)+'" download>Exportar CSV</a></div>'+rows+'</section>'+navigation;
        } catch (error) {
          if (still()) panel.innerHTML='<div class="os-notice os-error" role="alert"><p>'+esc(error?.message||'Não foi possível consultar o relatório.')+'</p></div>';
        }
      }
      form.addEventListener('submit',event=>{
        event.preventDefault();
        if (!valid()) return;
        Object.assign(values,Object.fromEntries(new FormData(form)),{offset:0});
        void read();
      });
      panel.addEventListener('click',event=>{
        const action=event.target.closest('button')?.dataset.osAction;
        if (!['delivery-ops-prev','delivery-ops-next'].includes(action)||!valid()||!result) return;
        event.stopPropagation();
        values.offset=Math.max(0,result.offset+(action==='delivery-ops-next'?result.limit:-result.limit));
        void read();
      });
      return read();
    }

    async function show(name,focus=false) {
      if (!Object.hasOwn(tabs,name)||!current()) return;
      const token=++revision;
      filters.tab=name;
      for (const tab of bar.querySelectorAll('[role="tab"]')) {
        const selected=tab.dataset.deliveryReportTab===name;
        tab.setAttribute('aria-selected',String(selected));
        tab.tabIndex=selected?0:-1;
        if (selected&&focus) tab.focus();
      }
      shell.setAttribute('aria-labelledby','osDeliveryReportTab-'+name);
      const work=document.createElement('div');
      shell.replaceChildren(work);
      const valid=()=>current()&&revision===token&&work.isConnected;
      if (name==='sales') {
        await window.OrionDeliveryOrders.salesReport({
          host:work,filters:filters.views.sales,call,allowed:valid,ui,exportPath:exportPath+'/sales.csv',
        });
      } else {
        await operational(name,work,valid);
      }
    }
    bar.addEventListener('click',event=>{
      const button=event.target.closest('[data-delivery-report-tab]');
      if (button&&button.dataset.deliveryReportTab!==filters.tab) void show(button.dataset.deliveryReportTab,true);
    });
    bar.addEventListener('keydown',event=>{
      if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      const from=names.indexOf(filters.tab);
      let next;
      if (event.key==='Home') next=0;
      else if (event.key==='End') next=names.length-1;
      else next=(from+(event.key==='ArrowRight'?1:-1)+names.length)%names.length;
      void show(names[next],true);
    });
    await show(filters.tab);
  }

  window.OrionDeliveryReports=Object.freeze({mount});
})();
