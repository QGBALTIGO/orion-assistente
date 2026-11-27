/* Admin catalog and stock history in the Orion workspace. */
(function () {
  'use strict';

  const prices = [['fixed','Preço fixo'],['from','A partir de'],['on_request','Sob consulta'],['custom','Personalizado']];
  const movements = [['purchase','Entrada'],['out','Saída'],['count','Contagem de estoque']];
  const movementLabels = Object.fromEntries([...movements,['initial','Saldo inicial'],['reserved','Reserva'],['released','Liberação'],['sold','Venda concluída'],['sale_returned','Devolução de pedido']]);
  const cap = 10000000;

  async function mount({host, filters, call, allowed, canWrite, ui, openEditor, notify}) {
    if (!host || !allowed()) return;
    const {esc,icon,title,button,field,selectField,textField,empty,loading,badge,money,fmtDate,dateKey,timeLabel,query} = ui;
    const fallbackFilters = {tab:'catalog',search:'',category:'',state:'all',low_stock:false,offset:0,start:'',end:''};
    for (const [key,value] of Object.entries(fallbackFilters)) if (filters[key] == null) filters[key] = value;
    let result, read = 0;
    const current = stamp => stamp === read && host.isConnected && allowed();
    const byId = id => host.querySelector('#'+id);
    const control = (text,action,kind,symbol,id) => button(text,action,kind,symbol,
      id ? 'data-delivery-item="'+esc(id)+'"' : '');
    const catalogTab = () => filters.tab === 'catalog';
    const writeAllowed = () => canWrite() && allowed() && host.isConnected;

    function renderFilters() {
      if (!catalogTab()) {
        return field('De','start','date',filters.start)+field('Até','end','date',filters.end);
      }
      return field('Buscar','search','search',filters.search,'maxlength="120" placeholder="Nome ou código"')+
        selectField('Categoria','category',[['','Todas'],...(result?.categories || []).map(x=>[x,x])],filters.category)+
        selectField('Status','state',[['all','Todos'],['active','Ativos'],['inactive','Inativos']],filters.state)+
        '<label class="os-check"><input type="checkbox" name="low_stock" '+(filters.low_stock?'checked':'')+'> Estoque baixo</label>';
    }

    function renderShell() {
      host.innerHTML = title('Produtos e estoque','',canWrite() ? button('Novo produto','delivery-create','','plus') : '')+
        '<div class="os-pill-tabs" role="tablist" aria-label="Estoque">'+
          [['catalog','Produtos'],['history','Movimentações']].map(([key,label])=>
            '<button type="button" class="os-pill-tab" role="tab" id="osDeliveryTab-'+key+'" aria-controls="osDeliveryPanel" aria-selected="'+(filters.tab===key)+'" tabindex="'+(filters.tab===key?'0':'-1')+'" data-delivery-tab="'+key+'">'+label+'</button>').join('')+
        '</div><section id="osDeliveryPanel" role="tabpanel" aria-labelledby="osDeliveryTab-'+filters.tab+'">'+
          '<form id="osDeliveryFilters" class="os-filter-row os-delivery-filters">'+renderFilters()+
            '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
          '<div id="osDeliveryResults" aria-live="polite"></div></section>';
      window.OrionFields.refresh(host);
    }

    function stockLabel(item) {
      if (item.mode !== 'tracked') return badge('pending','Sem contagem');
      if (Date.parse(item.valid_until) <= Date.now()) return badge('pending','Contagem vencida');
      if (item.quantity_on_hand - item.reserved_quantity <= item.minimum_quantity) return badge('pending','Estoque baixo');
      return '';
    }

    function productCard(item) {
      const id=item.variant_id, active=item.product_active && item.variant_active;
      const price=item.price_kind==='from'?'A partir de '+money(item.unit_price_cents):
        item.price_kind==='fixed'?money(item.unit_price_cents):
          prices.find(([key])=>key===item.price_kind)?.[1];
      const balance=item.quantity_on_hand == null?'Sem saldo registrado':
        `${item.quantity_on_hand} ${item.unit} em estoque · ${item.reserved_quantity} reservado(s)`;
      const presentation=window.OrionDeliveryEditor?.picture(item.image_url,esc)||'<div class="os-square-icon">'+icon('box')+'</div>';
      const actions=canWrite()?'<div class="os-actions">'+
        control('Editar','delivery-edit','os-ghost os-small','edit',id)+
        control('Ajustar estoque','delivery-adjust','os-outline os-small','box',id)+
        control('Nova variação','delivery-variant','os-ghost os-small','plus',id)+'</div>':'';
      return '<article class="os-card os-item-card"><div class="os-item-top">'+
        presentation+'<div><h3>'+esc(item.name)+'</h3>'+
        '<p class="os-help">'+esc([item.variant_name,item.brand,item.category,price].filter(Boolean).join(' · '))+'</p>'+
        '<p>'+esc(balance)+'</p><div class="os-actions">'+badge(active?'active':'inactive')+stockLabel(item)+'</div>'+
        '</div></div>'+actions+'</article>';
    }

    function movementTable(items) {
      return '<div class="os-card os-no-pad"><div class="os-table-scroll"><table class="os-table">'+
        '<thead><tr><th>Data</th><th>Produto</th><th>Movimento</th><th>Quantidade</th><th>Saldo</th><th>Observação</th></tr></thead><tbody>'+
        items.map(item=>'<tr><td>'+esc(fmtDate(dateKey(new Date(item.created_at)))+' '+timeLabel(item.created_at))+'</td>'+
          '<td><span class="os-delivery-shop-item-name">'+(window.OrionDeliveryEditor?.picture(item.image_url,esc)||'')+
          esc([item.name,item.variant_name].filter(Boolean).join(' · '))+'</span></td><td>'+esc(movementLabels[item.details.movement||item.action])+'</td>'+
          '<td>'+esc(item.details.quantity??'—')+'</td><td>'+esc(item.details.after??'—')+'</td><td>'+esc(item.details.note||'—')+'</td></tr>').join('')+
        '</tbody></table></div></div>';
    }

    function renderResults() {
      const {items,offset,limit,has_more:next}=result;
      let html;
      if (items.length) html = catalogTab() ? '<div class="os-cards-grid os-services-grid">'+items.map(productCard).join('')+'</div>' : movementTable(items);
      else html = '<div class="os-card">'+(catalogTab()
        ? empty('box','Nenhum produto encontrado','Cadastre um produto ou confira os filtros.')
        : empty('list','Nenhuma movimentação neste período','Os ajustes e as reservas de estoque aparecem aqui.'))+'</div>';
      if (offset > 0 || next) html+='<div class="os-pagination"><span>Página '+(offset/limit+1)+'</span><div class="os-actions">'+
        button('Anterior','delivery-prev','os-outline os-small','left',offset>0?'':'disabled')+
        button('Próxima','delivery-next','os-outline os-small','chevron',next?'':'disabled')+'</div></div>';
      byId('osDeliveryResults').innerHTML=html;
    }

    async function readPage() {
      const stamp = ++read, output = byId('osDeliveryResults'), catalog = catalogTab();
      output.setAttribute('aria-busy','true');
      output.innerHTML = loading(catalog?'Carregando produtos…':'Carregando movimentações…');
      const {search,category,state,offset,start,end,low_stock}=filters;
      const params = catalog ? {search,category,state,low_stock,offset} : {start,end,offset};
      try {
        const response = await call('/catalog'+(catalog?'':'/history')+'?'+query(params));
        if (!current(stamp)) return;
        result = response;
        if (catalog) {
          const select=byId('osDeliveryFilters').elements.category;
          select.replaceChildren(new Option('Todas',''),...result.categories.map(v=>new Option(v,v)));
          select.value=filters.category;
        }
        renderResults();
      } catch (error) {
        if (current(stamp)) output.innerHTML='<div class="os-notice os-error" role="alert"><p>'+esc(error.message||'Não foi possível carregar os dados.')+'</p>'+button('Tentar novamente','delivery-retry','os-ghost os-small')+'</div>';
      } finally {
        if (current(stamp)) output.removeAttribute('aria-busy');
      }
    }

    function productEditor(item, addVariant = false) {
      if (!window.OrionDeliveryEditor) { notify('Atualize a página para abrir o cadastro de produtos.',true); return; }
      read++;
      const back = () => {
        renderShell();
        host.querySelector('[data-os-action=delivery-create]')?.focus();
        void readPage();
      };
      window.OrionDeliveryEditor.mount({host,item,addVariant,categories:result?.categories||[],
        call,allowed,canWrite,ui,onBack:back,onSaved:message=>{notify(message);back();}});
    }

    function stockEditor(item) {
      const isFresh=item.mode==='tracked'&&Date.parse(item.valid_until)>Date.now();
      const content='<p class="os-help">'+esc(item.name+' · '+item.variant_name)+'</p><div class="os-form-grid">'+
        selectField('Movimento','movement',isFresh?movements:movements.slice(2),isFresh?'purchase':'count')+
        field('Quantidade','quantity','number','','required min="0" max="10000000" step="1"')+
        '</div>'+textField('Observação','note','','maxlength="300"');
      openEditor('Ajustar estoque',content,async(form,values,key)=>{
        const payload={movement:values.movement,quantity:window.OrionFields.parseDecimal(values.quantity,{required:true,scale:0,min:0,max:cap}),
          note:values.note.trim()||null,expected_revision:item.inventory_revision};
        const saved=await call('/catalog/variants/'+encodeURIComponent(item.variant_id)+'/adjustments','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.variant_id!==item.variant_id) throw Error('Não foi possível confirmar a movimentação.');
      },'Estoque atualizado.');
    }

    function changeTab(tab, focus) {
      if (tab===filters.tab) return;
      filters.tab=tab;
      filters.offset=0;
      result=null;
      renderShell();
      if (focus) byId('osDeliveryTab-'+tab).focus({preventScroll:true});
      void readPage();
    }

    host.addEventListener('click',event=>{
      const element=event.target.closest('button');
      if (!element || !allowed()) return;
      if (element.dataset.deliveryTab) { changeTab(element.dataset.deliveryTab,true); return; }
      const action=element.dataset.osAction;
      if (!action?.startsWith('delivery-')) return;
      event.stopPropagation();
      if (action==='delivery-retry') { void readPage(); return; }
      if (action==='delivery-prev'||action==='delivery-next') {
        filters.offset=Math.max(0,result.offset+(action==='delivery-next'?result.limit:-result.limit));
        void readPage();
        return;
      }
      if (!writeAllowed()) return;
      const item=result?.items?.find(row=>row.variant_id===element.dataset.deliveryItem);
      if (action==='delivery-create') productEditor(null);
      if (!item) return;
      if (action==='delivery-edit') productEditor(item);
      if (action==='delivery-variant') productEditor(item,true);
      if (action==='delivery-adjust') stockEditor(item);
    });
    host.addEventListener('keydown',event=>{
      if (!event.target.dataset.deliveryTab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      changeTab(event.key==='Home'?'catalog':event.key==='End'?'history':catalogTab()?'history':'catalog',true);
    });
    host.addEventListener('submit',event=>{
      if (event.target!==byId('osDeliveryFilters')) return;
      event.preventDefault();
      const data=Object.fromEntries(new FormData(event.target));
      Object.assign(filters,data,{offset:0});
      if (catalogTab()) filters.low_stock=data.low_stock==='on';
      void readPage();
    });
    renderShell();
    await readPage();
  }

  window.OrionDeliveryCatalog = Object.freeze({mount});
})();
