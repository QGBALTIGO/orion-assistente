/* Admin catalog and stock history in the Orion workspace. */
(function () {
  'use strict';

  const prices = [['fixed','Preço fixo'],['from','A partir de'],['on_request','Sob consulta'],['custom','Personalizado']];
  const movements = [['purchase','Entrada'],['out','Saída'],['count','Contagem de estoque']];
  const movementLabels = Object.fromEntries([...movements,['initial','Saldo inicial'],['reserved','Reserva'],['released','Liberação'],['sold','Venda concluída'],['sale_returned','Devolução de pedido']]);
  const cap = 10000000;

  async function mount({host, filters, call, allowed, canWrite, canManagePolicy=()=>false, ui, openEditor, notify}) {
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
    const tabs = [['catalog','Produtos'],['history','Movimentações']];
    if (canManagePolicy()) tabs.push(['suppliers','Fornecedores'],['purchases','Compras']);
    if (!tabs.some(([key])=>key===filters.tab)) filters.tab='catalog';
    const purchasing = () => ['suppliers','purchases'].includes(filters.tab);

    function renderFilters() {
      if (purchasing()) return field('Buscar',filters.tab+'_search','search',filters[filters.tab+'_search']||'',
        'maxlength="120" placeholder="'+(filters.tab==='suppliers'?'Nome, CNPJ ou marca':'Fornecedor ou documento')+'"');
      if (!catalogTab()) {
        return field('De','start','date',filters.start)+field('Até','end','date',filters.end);
      }
      return '<div class="od-search-line">'+field('Buscar','search','search',filters.search,'maxlength="120" placeholder="Nome ou código"')+
        '<button type="submit" class="os-button os-outline">'+icon('search')+'Filtrar</button></div>'+
        '<details class="od-advanced"'+(filters.category||filters.state!=='all'||filters.low_stock?' open':'')+'><summary>'+icon('filter')+'<span>Categoria, situação e estoque</span>'+icon('chevron')+'</summary><div class="od-advanced-grid">'+
        selectField('Categoria','category',[['','Todas'],...(result?.categories || []).map(x=>[x,x])],filters.category)+
        selectField('Status','state',[['all','Todos'],['active','Ativos'],['inactive','Inativos']],filters.state)+
        '<label class="os-check"><input type="checkbox" name="low_stock" '+(filters.low_stock?'checked':'')+'> Estoque baixo</label></div>'+
        '<div class="od-filter-footer"><button type="submit" class="os-button">Aplicar filtros</button></div></details>';
    }

    function renderShell() {
      const newButton=filters.tab==='suppliers'?button('Novo fornecedor','delivery-supplier','','plus'):
        filters.tab==='purchases'?button('Receber compra','delivery-purchase','','plus'):button('Novo produto','delivery-create','','plus');
      host.innerHTML = title('Produtos e estoque','Gerencie o catálogo, as reservas e os produtos oferecidos no atendimento.',(canWrite() ? newButton : '')+
        (catalogTab()&&ui.routeButton?ui.routeButton('Vitrine do atendimento','loja-delivery','os-outline','deliveryStorefront'):''))+
        '<div class="os-pill-tabs od-tab-strip" role="tablist" aria-label="Estoque">'+
          tabs.map(([key,label])=>
            '<button type="button" class="os-pill-tab" role="tab" id="osDeliveryTab-'+key+'" aria-controls="osDeliveryPanel" aria-selected="'+(filters.tab===key)+'" tabindex="'+(filters.tab===key?'0':'-1')+'" data-delivery-tab="'+key+'">'+label+'</button>').join('')+
        '</div><section id="osDeliveryPanel" role="tabpanel" aria-labelledby="osDeliveryTab-'+filters.tab+'">'+
          '<form id="osDeliveryFilters" class="os-filter-row os-delivery-filters'+(catalogTab()?' od-compact-filters':'')+'">'+renderFilters()+
            (catalogTab()?'':'<button type="submit" class="os-button os-outline">Filtrar</button>')+'</form>'+
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
      const available=item.quantity_on_hand==null?null:item.quantity_on_hand-(item.reserved_quantity||0);
      const tracked=item.mode==='tracked'&&Date.parse(item.valid_until)>Date.now();
      const channelLabels={ready:['Apto no catálogo','success'],stock_unavailable:['Sem estoque válido','warning'],review:['Revisar aprovação','warning'],
        not_selected:['Não selecionado','neutral'],disabled:['Canal desabilitado','neutral'],price_required:['Requer preço fixo','neutral'],
        not_eligible:['Não elegível para a atendente','neutral'],no_mode:['Sem modalidade compatível','warning']};
      const channel=channelLabels[item.whatsapp_catalogue_status];
      const channelStatus=channel?'<div class="os-delivery-catalog-channel"><span>'+icon('phone')+' WhatsApp</span><strong data-channel-tone="'+channel[1]+'">'+channel[0]+'</strong></div>':'';
      const presentation=window.OrionDeliveryEditor?.picture(item.image_url,esc)||'<div class="os-square-icon">'+icon('box')+'</div>';
      const actions=canWrite()?'<div class="os-actions">'+
        control('Editar','delivery-edit','os-ghost os-small','edit',id)+
        control('Ajustar estoque','delivery-adjust','os-outline os-small','box',id)+
        control('Nova variação','delivery-variant','os-ghost os-small','plus',id)+'</div>':'';
      return '<article class="os-card os-item-card os-delivery-product-card"><div class="os-item-top">'+
        presentation+'<div><h3>'+esc(item.name)+'</h3>'+
        '<p class="os-help">'+esc([item.variant_name,item.brand,item.category].filter(Boolean).join(' · '))+'</p>'+
        '<strong class="os-delivery-product-price">'+esc(price||'Sob consulta')+'</strong><div class="os-actions">'+badge(active?'active':'inactive')+stockLabel(item)+'</div>'+
        '</div></div><div class="os-delivery-stock-grid"><div><small>Saldo</small><strong>'+esc(item.quantity_on_hand??'—')+'</strong></div>'+
        '<div><small>Reservado</small><strong>'+esc(item.reserved_quantity??'—')+'</strong></div>'+
        '<div class="od-available-stock"><small>Disponível</small><strong>'+esc(tracked?(available??'—'):'—')+'</strong></div></div>'+
        (!tracked?'<p class="os-help">Atualize a contagem para liberar o saldo disponível.</p>':'')+channelStatus+actions+'</article>';
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
      if (purchasing()) {
        const supplier=filters.tab==='suppliers';
        const cards=items.map(item=>supplier?'<article class="os-card"><h3>'+esc(item.name)+'</h3>'+
          badge(item.active?'active':'inactive')+'<p>'+esc([item.cnpj,item.phone,item.brands].filter(Boolean).join(' · '))+'</p>'+
          control('Editar','delivery-supplier-edit','os-outline os-small','edit',item.id)+'</article>':
          '<article class="os-card"><h3>'+esc(item.reference)+'</h3><p>'+esc(item.supplier_name)+' · '+esc(fmtDate(dateKey(new Date(item.received_at))))+'</p>'+
          '<strong>'+esc(money(item.amount_cents))+'</strong> '+badge(item.payment_status==='paid'?'active':'pending',item.payment_status==='paid'?'Pago':'A pagar')+
          (item.due_on?'<p>Vencimento '+esc(fmtDate(item.due_on))+'</p>':'')+
          '<ul>'+item.items.map(line=>'<li>'+esc(line.quantity+' × '+line.label+' · '+money(line.unit_cost_cents))+'</li>').join('')+'</ul></article>');
        html=cards.length?'<div class="os-cards-grid os-services-grid">'+cards.join('')+'</div>':
          '<div class="os-card">'+empty('box',supplier?'Nenhum fornecedor encontrado':'Nenhuma compra encontrada',
            supplier?'Cadastre um fornecedor ou ajuste a busca.':'Registre o recebimento de mercadorias ou ajuste a busca.')+'</div>';
      }
      else if (items.length) html = catalogTab() ? '<div class="od-results-label"><h2>Catálogo</h2><span>'+items.length+' nesta página</span></div><div class="os-cards-grid os-services-grid od-product-list">'+items.map(productCard).join('')+'</div>' : movementTable(items);
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
      output.innerHTML = loading('Carregando '+tabs.find(([key])=>key===filters.tab)[1].toLowerCase()+'…');
      const {search,category,state,offset,start,end,low_stock}=filters;
      const params = catalog ? {search,category,state,low_stock,offset,include_channels:true} : {start,end,offset};
      try {
        const response = await call(purchasing()?'/'+filters.tab+'?'+query({search:filters[filters.tab+'_search']||'',offset}):
          '/catalog'+(catalog?'':'/history')+'?'+query(params));
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

    function editSupplier(item) {
      const content='<div class="os-form-grid">'+field('Nome do fornecedor','supplier_name','text',item?.name||'','required maxlength="140"')+
        field('CNPJ','supplier_cnpj','text',item?.cnpj||'','maxlength="18"')+
        field('Telefone','supplier_phone','tel',item?.phone||'','maxlength="40"')+
        field('Marcas','supplier_brands','text',item?.brands||'','maxlength="240"')+
        selectField('Situação','supplier_active',[['yes','Ativo'],['no','Inativo']],item?.active===false?'no':'yes')+'</div>';
      openEditor(item?'Editar fornecedor':'Novo fornecedor',content,async(form,values,key)=>{
        const payload={name:values.supplier_name.trim(),cnpj:values.supplier_cnpj.trim()||null,
          phone:values.supplier_phone.trim()||null,brands:values.supplier_brands.trim()||null,active:values.supplier_active==='yes'};
        if (item) await call('/suppliers/'+encodeURIComponent(item.id),'PUT',{...payload,expected_revision:item.revision});
        else await call('/suppliers','POST',{...payload,idempotency_key:key(payload)});
      },'Fornecedor salvo.');
    }

    function purchaseEditor() {
      const lines=new Map();
      const searchBox=(label,name)=>field('Buscar '+label,'find_'+name,'search','','maxlength="120"')+
        '<button type="button" class="os-button os-outline os-small" data-purchase-find="'+name+'">Buscar '+label+'</button>'+
        selectField(label==='fornecedor'?'Fornecedor':'Produto','selected_'+name,[['','Selecione']], '');
      const content='<div class="os-form-grid">'+searchBox('fornecedor','supplier')+
        field('Documento ou referência','purchase_reference','text','','required maxlength="80"')+'</div><hr>'+
        '<div class="os-form-grid">'+searchBox('produto','product')+field('Quantidade','line_quantity','number','1','min="1" max="10000000"')+
        ui.moneyField('Custo unitário','line_cost','text','')+'</div>'+
        '<button type="button" class="os-button os-outline" data-purchase-add>Adicionar item</button>'+
        '<div aria-live="polite" data-purchase-lines></div><p role="status" data-purchase-feedback></p>'+
        selectField('Pagamento','purchase_payment',[['pending','A pagar'],['pix','Pix pago'],['cash','Dinheiro pago'],
          ['debit_card','Débito pago'],['credit_card','Crédito pago'],['bank_transfer','Transferência paga'],['other','Outro pagamento realizado']],'pending')+
        '<div data-purchase-due>'+field('Vencimento','purchase_due','date','','required')+'</div>';
      openEditor('Receber compra',content,async(form,values,key)=>{
        if (!values.selected_supplier || !lines.size) throw Error('Selecione um fornecedor e adicione os produtos recebidos.');
        const payload={supplier_id:values.selected_supplier,reference:values.purchase_reference.trim(),
          items:[...lines.values()].map(({variant_id,quantity,unit_cost_cents})=>({variant_id,quantity,unit_cost_cents})),
          due_on:values.purchase_payment==='pending'?values.purchase_due:null,
          payment_method:values.purchase_payment==='pending'?null:values.purchase_payment};
        await call('/purchases','POST',{...payload,idempotency_key:key(payload)});
      },'Compra recebida e despesa registrada.',{label:'Registrar recebimento',bind:dialog=>{
        const form=dialog.querySelector('form'), f=form.elements, feedback=form.querySelector('[data-purchase-feedback]');
        const versions={supplier:0,product:0};
        const find=async kind=>{
          const stamp=++versions[kind], supplier=kind==='supplier', search=f['find_'+kind].value.trim(), select=f['selected_'+kind];
          select.disabled=true;
          try {
            const data=await call((supplier?'/suppliers?':'/catalog?')+query({search,...(supplier?{active_only:true}:{state:'active'})}));
            if (!form.isConnected||versions[kind]!==stamp) return;
            select.replaceChildren(new Option('Selecione',''),...data.items.map(row=>supplier?new Option(row.name,row.id):
              new Option([row.name,row.variant_name,row.sku].filter(Boolean).join(' · '),row.variant_id)));
            feedback.textContent=data.has_more?'Refine a busca para localizar outros resultados.':data.items.length?'':'Nenhum resultado para esta busca.';
          } catch (error) {if(form.isConnected&&versions[kind]===stamp)feedback.textContent=error.message;}
          finally {if(versions[kind]===stamp)select.disabled=false;}
        };
        const renderLines=()=>{
          const total=[...lines.values()].reduce((sum,line)=>sum+line.quantity*line.unit_cost_cents,0);
          form.querySelector('[data-purchase-lines]').innerHTML=[...lines.values()].map(line=>
            '<p>'+esc(line.quantity+' × '+line.label+' · '+money(line.unit_cost_cents))+
            ' <button type="button" class="os-button os-ghost os-small" aria-label="Remover '+esc(line.label)+'" data-purchase-remove="'+esc(line.variant_id)+'">Remover</button></p>').join('')+
            '<strong>Total '+esc(money(total))+'</strong>';
        };
        form.addEventListener('click',event=>{
          const btn=event.target.closest('button');
          if (!btn) return;
          if (btn.dataset.purchaseFind) {void find(btn.dataset.purchaseFind);return;}
          if (btn.dataset.purchaseRemove) {lines.delete(btn.dataset.purchaseRemove);renderLines();return;}
          if (!btn.hasAttribute('data-purchase-add')) return;
          try {
            const select=f.selected_product,id=select.value;
            if (!id||select.disabled) throw Error('Selecione um produto.');
            if (lines.has(id)) throw Error('Este produto já está na compra. Remova o item para alterar a quantidade.');
            const quantity=window.OrionFields.parseDecimal(f.line_quantity.value,{required:true,scale:0,min:1,max:cap});
            const cost=window.OrionFields.parseMoney(f.line_cost.value,{required:true,min:0,max:100000000});
            lines.set(id,{variant_id:id,label:select.selectedOptions[0].textContent,quantity,unit_cost_cents:cost});
            feedback.textContent='';f.line_cost.value='';f.line_quantity.value='1';select.value='';renderLines();
          } catch (error) {feedback.textContent=error.message;}
        });
        f.purchase_payment.addEventListener('change',()=>{
          const pending=f.purchase_payment.value==='pending';
          form.querySelector('[data-purchase-due]').hidden=!pending;f.purchase_due.disabled=!pending;
        });
        void find('supplier');void find('product');
      }});
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
      if (canManagePolicy() && purchasing()) {
        if(action==='delivery-supplier') editSupplier(null);
        if(action==='delivery-supplier-edit'){
          const supplier=result?.items.find(row=>row.id===element.dataset.deliveryItem);
          if(supplier) editSupplier(supplier);
        }
        if(action==='delivery-purchase') purchaseEditor();
        return;
      }
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
      const keys=tabs.map(([key])=>key),index=keys.indexOf(filters.tab);
      changeTab(event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):
        keys[(index+(event.key==='ArrowRight'?1:-1)+keys.length)%keys.length],true);
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
