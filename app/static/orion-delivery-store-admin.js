/* Settings, publication and product order for the Orion Delivery storefront. */
(() => {
  'use strict';

  const paymentOptions = [
    ['cash', 'Dinheiro'], ['debit_card', 'Cartão de débito'],
    ['credit_card', 'Cartão de crédito'], ['pix', 'Pix com conferência manual'],
  ];
  const publicKinds = [
    ['water','Água'],['prepared_food','Comida pronta'],['grocery','Mercado'],['household','Utilidades'],
  ];
  const weekdays = ['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo'];
  const itemFields = ['variant_id','product_revision','variant_revision','label','description','kind'];
  const settingsFields = ['revision','slug','title','description','enabled','accepting_orders','closed_message',
    'logo_url','cover_url','brand_color','public_phone','layout','hide_unavailable','minimum_subtotal_cents',
    'estimate_min_minutes','estimate_max_minutes','closed_dates','allow_pickup','allow_delivery',
    'pickup_details','opening_hours','payment_methods','max_open_orders'];
  const pick = (source, keys) => Object.fromEntries(keys.map(key => [key, source[key]]));
  const moneyInput = cents => cents == null ? '' :
    new Intl.NumberFormat('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100);
  const root = '/storefront';

  async function mount({host, filters, call, allowed, canWrite, ui, openEditor, notify}) {
    const {esc,icon,loading,button,title,field,moneyField,selectField,textField,checkField,empty,
      imageUploadField,money,query,routeButton} = ui;
    if (!host || !allowed()) return;
    let state, results = [], requestSequence = 0;
    let offset = 0, busy = false, hourId = 0;
    const current = () => allowed() && host.isConnected;
    const writeAllowed = () => canWrite() && current() && !busy;
    const statusError = message => '<div class="os-notice os-error" role="alert">' + esc(message) + '</div>';
    const smallButton = (label,action,symbol,extra = '') => button(label,action,'os-outline os-small',symbol,extra);
    const targetAttr = id => 'data-store-variant="' + esc(id) + '"';
    const tags = [['products','Vitrine'],['settings','Configurações']];
    const grid = inner => '<div class="os-form-grid">'+inner+'</div>';
    const group = (heading,inner) => '<section class="os-card os-form"><h2>'+heading+'</h2>'+inner+'</section>';
    const valueOrNull = v => v.trim() || null;
    const thumbnail = value => window.OrionDeliveryEditor?.picture(value,esc)||'';

    function endpointPayload(patch) {
      return {...pick(state,settingsFields),items:state.items.map(item=>pick(item,itemFields)),...patch};
    }

    async function save(patch) {
      const result = await call(root,'PUT',endpointPayload(patch));
      if (typeof result?.revision !== 'number') throw Error('Não foi possível confirmar as alterações da loja.');
      return result;
    }

    const confirmField = () => checkField('Confirmo a publicação dos produtos selecionados','store_confirm',false,
      'Os meios de pagamento selecionados serão oferecidos aos clientes.');
    const confirmed = form => !!form.elements.store_confirm?.checked;

    function storefrontHeader() {
      const status = state.public_url ? 'Publicada' : 'Despublicada';
      const ordering = state.accepting_orders === false ? 'Pedidos pausados' :
        !state.public_url ? 'Atendimento configurado' : state.open ? 'Aceitando pedidos' : 'Fora do horário';
      const preview = state.preview_url ?
        '<a class="os-button os-outline" href="'+esc(state.preview_url)+'" target="_blank" rel="noopener">'+icon('eye')+'Prévia da loja</a>' : '';
      const publicActions = state.public_url ?
        '<a class="os-button os-outline" href="'+esc(state.public_url)+'" target="_blank" rel="noopener">'+icon('globe')+'Abrir loja</a>'+
        button('Copiar link','store-copy','os-outline','copy') : '';
      const publish = !canWrite() ? '' : state.enabled ? button('Despublicar','store-unpublish','os-outline','close') :
        state.publication_available ? button('Publicar loja','store-publish','','globe') : '';
      return '<section class="os-card os-form os-delivery-shop-status"><div class="os-card-header"><h2>'+esc(state.title)+'</h2>'+
        '<div class="os-actions"><span class="os-badge">'+status+'</span><span class="os-badge os-neutral">'+ordering+'</span></div></div>'+
        (state.public_url?'<a href="'+esc(state.public_url)+'" target="_blank" rel="noopener">'+esc(state.public_url)+'</a>':'')+
        (!state.publication_available?'<p class="os-help">Nesta empresa, a loja está em modo de avaliação. A prévia é acessível pelo painel.</p>':'')+
        '<div class="os-actions">'+preview+publicActions+publish+'</div>'+
        (state.public_url&&canWrite()?'<div class="os-actions">'+smallButton(state.accepting_orders?'Pausar pedidos':'Retomar pedidos',
          'store-pause',state.accepting_orders?'pause':'check')+'</div>':'')+'</section>';
    }

    function hour(row={}) {
      const index=hourId++;
      return '<div data-store-hour class="os-delivery-hours-row">'+
        selectField('Dia da semana','weekday_'+index,weekdays.map((label,n)=>[String(n),label]),String(row.weekday??0))+
        field('Abre','opens_'+index,'time',(row.opens||'').slice(0,5),'required')+
        field('Fecha','closes_'+index,'time',(row.closes||'').slice(0,5),'required')+
        '<button type="button" class="os-button os-outline os-small" data-store-remove-hour>Remover horário</button></div>';
    }

    function closedDay(date='') {
      return '<div class="os-delivery-hours-date" data-store-closed-day>'+
        field('Data sem atendimento','closed_'+hourId++,'date',date,'required')+
        '<button type="button" class="os-button os-outline os-small" data-store-remove-date>Remover data</button></div>';
    }

    function settings() {
      if (!canWrite()) return '<div class="os-card os-help">O proprietário gerencia as configurações da loja.</div>';
      hourId=0;
      return '<form class="os-form os-delivery-shop-form" id="osDeliveryShopSettings">' +
        group('Identificação e marca',grid(
          field('Nome da loja','store_title','text',state.title,'required maxlength="160"')+
          field('Endereço do link','store_slug','text',state.slug,'required minlength="3" maxlength="80" pattern="[a-z0-9]+(?:-[a-z0-9]+)*"')+
          field('WhatsApp da loja','public_phone','tel',state.public_phone||'','data-orion-field="phone" maxlength="40"')+
          field('Cor principal','brand_color','color',state.brand_color||'#2e7bf6'))+
          textField('Descrição da loja','store_description',state.description||'','maxlength="600"')+
          grid(imageUploadField('Logotipo','logo_url',state.logo_url||'','logo')+
            imageUploadField('Capa da loja','cover_url',state.cover_url||'','cover')))+
        group('Horários e funcionamento',checkField('Aceitar novos pedidos','accepting_orders',state.accepting_orders??true)+
          textField('Mensagem quando fechada','closed_message',state.closed_message||'','maxlength="300"')+
          '<div class="os-form"><h3>Horários de atendimento</h3>'+
          '<p class="os-help">Sem horários cadastrados, a loja fica aberta enquanto aceitar pedidos.</p>'+
          '<div class="os-form" data-store-hours>'+state.opening_hours.map(hour).join('')+'</div>'+
          '<div class="os-actions"><button type="button" class="os-button os-outline os-small" data-store-add-hour>'+
            icon('plus')+'Adicionar horário</button></div></div>'+
          '<div class="os-form"><h3>Feriados e dias sem atendimento</h3><div class="os-form" data-store-dates>'+
            (state.closed_dates||[]).map(closedDay).join('')+'</div><div class="os-actions">'+
            '<button type="button" class="os-button os-outline os-small" data-store-add-date>'+icon('plus')+
            'Adicionar data</button></div></div>')+
        group('Entrega e retirada',grid(checkField('Aceitar retirada no local','allow_pickup',state.allow_pickup)+
          checkField('Aceitar entrega','allow_delivery',state.allow_delivery))+
          textField('Endereço de retirada','pickup_details',state.pickup_details||'','maxlength="300"')+
          grid(field('Previsão mínima em minutos','estimate_min','number',state.estimate_min_minutes??'','min="5" max="1440" step="1"')+
            field('Previsão máxima em minutos','estimate_max','number',state.estimate_max_minutes??'','min="5" max="1440" step="1"'))+
          '<div class="os-actions">'+routeButton('Bairros, taxas e agendamentos','logistica-delivery','os-outline os-small')+'</div>')+
        group('Pagamento e checkout',grid(
          paymentOptions.map(([key,label])=>checkField(label,'method_'+key,state.payment_methods.includes(key))).join(''))+
          '<div class="os-actions">'+smallButton('Chave Pix da distribuidora','store-pix','wallet')+'</div>'+
          grid(moneyField('Pedido mínimo','minimum_subtotal','text',moneyInput(state.minimum_subtotal_cents))+
            field('Limite de pedidos em aberto','max_open_orders','number',state.max_open_orders,'required min="1" max="300" step="1"')))+
        group('Exibição da vitrine',grid(selectField('Exibição dos produtos','layout',[['grid','Grade'],['list','Lista']],state.layout||'grid')+
          checkField('Ocultar produtos sem estoque','hide_unavailable',!!state.hide_unavailable)))+
        '<div class="os-form-message" role="alert" data-store-settings-error></div>'+
        '<div class="os-form-actions"><button type="submit" class="os-button">'+icon('check')+
          'Salvar configurações</button></div></form>';
    }

    function itemTable(list, selected) {
      const actionTitle = canWrite() ? '<th scope="col">Ações</th>' : '';
      return '<div class="os-table-scroll"><table class="os-table os-delivery-shop-item-table"><thead><tr>'+
        '<th scope="col">Produto</th><th scope="col">'+(selected?'Situação':'Preço')+'</th>'+actionTitle+'</tr></thead><tbody>'+
        list.map((item,index)=>{
          const entry = selected?item:state.items.find(row=>row.variant_id===item.variant_id);
          const label = selected?item.label:[item.name,item.variant_name==='Unidade'?'':item.variant_name].filter(Boolean).join(' · ');
          const attr = targetAttr(item.variant_id);
          const actions = selected ?
            smallButton('Subir','store-up','arrowUp',attr+(index===0?' disabled':''))+
            smallButton('Descer','store-down','down',attr+(index===list.length-1?' disabled':''))+
            smallButton('Retirar','store-remove','close',attr) :
            smallButton(entry?(entry.selection_valid?'Editar exibição':'Revisar e atualizar'):'Selecionar',
              'store-add',entry?'edit':'plus',attr);
          return '<tr><td><div class="os-delivery-shop-item-name">'+thumbnail(item.image_url)+esc(label)+'</div></td><td>'+
            (selected?(item.selection_valid===false?'Revisar alterações':'Selecionado'):esc(money(item.unit_price_cents)))+
            '</td>'+(canWrite()?'<td><div class="os-actions">'+actions+'</div></td>':'')+'</tr>';
        }).join('')+'</tbody></table></div>';
    }

    function productPanel() {
      return '<section class="os-card os-form"><h2>Produtos na vitrine</h2>'+
        (state.items.length?itemTable(state.items,true):'<p class="os-help">Selecione os produtos que deseja exibir na loja.</p>')+
        '</section><section class="os-card os-section-gap os-form"><h2>Selecionar produtos</h2>'+
        '<form id="osStoreFilters" class="os-filter-row os-delivery-filters">'+
        field('Buscar produto','store_search','search',filters.search||'','maxlength="120" placeholder="Nome do produto"')+
        '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
        '<div id="osStoreItems" aria-live="polite"></div></section>';
    }

    function showTab(key, focus) {
      filters.tab=key;
      for (const control of host.querySelectorAll('[data-store-tab]')) {
        const selected=control.dataset.storeTab===key;
        control.setAttribute('aria-selected',String(selected));
        control.tabIndex=selected?0:-1;
        if (selected && focus) control.focus();
      }
      for (const panel of host.querySelectorAll('[data-store-panel]')) panel.hidden=panel.dataset.storePanel!==key;
    }

    function render() {
      host.innerHTML=title('Loja rápida','',canWrite()?button('Configurar loja','store-settings','','settings'):'')+
        storefrontHeader()+'<div class="os-pill-tabs os-delivery-editor-tabs" role="tablist" aria-label="Loja rápida">'+
        tags.map(([key,label])=>'<button type="button" role="tab" class="os-pill-tab" id="osStoreTab-'+key+
          '" data-store-tab="'+key+'" aria-controls="osStore-'+key+'">'+label+'</button>').join('')+'</div>'+
        tags.map(([key])=>'<section id="osStore-'+key+'" role="tabpanel" data-store-panel="'+key+
          '" aria-labelledby="osStoreTab-'+key+'">'+(key==='products'?productPanel():settings())+'</section>').join('');
      showTab(filters.tab||'products',false);
      window.OrionFields.refresh(host);
      void readItems();
    }

    function changeShop(kind) {
      if (kind==='pause') {
        const next=!state.accepting_orders;
        openEditor(next?'Retomar pedidos':'Pausar pedidos',
          '<p class="os-help">'+(next?'A loja volta a aceitar pedidos dentro dos horários configurados.':
            'O link continua disponível e novos pedidos ficam pausados.')+'</p>',
          () => save({accepting_orders:next,replace_items:false}),next?'Pedidos retomados.':'Pedidos pausados.',
          {label:next?'Retomar pedidos':'Pausar pedidos'});
        return;
      }
      if (kind==='unpublish') {
        openEditor('Despublicar loja','<p class="os-help">O endereço público ficará indisponível. A configuração será preservada.</p>',
          () => call(root+'/unpublish','POST',{revision:state.revision}),'Loja despublicada.',{label:'Despublicar'});
        return;
      }
      openEditor('Publicar loja',confirmField(),form=>save({enabled:true,confirm_catalogue:confirmed(form)}),
        'Loja publicada.',{label:'Publicar loja'});
    }

    function editItem(source) {
      if (!source) return;
      const entry = state.items.find(item=>item.variant_id===source.variant_id);
      const kinds = state.offering==='gas_agua'?publicKinds.slice(0,1):publicKinds;
      const kind = entry?.kind || (source.category==='Água'?'water':
        source.category==='Alimentos preparados'?'prepared_food':kinds[0][0]);
      const label = source.variant_name==='Unidade'?source.name:source.name+' · '+source.variant_name;
      openEditor(entry?'Editar exibição do produto':'Selecionar produto para a loja',
        field('Nome na loja','store_label','text',entry?.label||label.slice(0,160),'required maxlength="160"')+
        textField('Descrição na loja','store_item_description',entry?.description||source.product_description?.slice(0,600)||'','maxlength="600"')+
        selectField('Categoria pública','store_item_kind',kinds,kind),
        async (form,values)=>{
          const next={...pick(source,itemFields.slice(0,3)),label:values.store_label.trim(),
            kind:values.store_item_kind,description:valueOrNull(values.store_item_description)};
          const items=state.items.map(row=>row.variant_id===next.variant_id?next:pick(row,itemFields));
          if (!entry) items.push(next);
          await save({items,confirm_catalogue:state.enabled});
        },'Vitrine atualizada.');
    }

    function removeItem(item) {
      if (!item) return;
      const items=state.items.filter(row=>row.variant_id!==item.variant_id).map(row=>pick(row,itemFields));
      const enabled=state.enabled&&items.length>0;
      openEditor('Retirar produto da loja','<p class="os-help">Retirar '+esc(item.label)+' da vitrine.'+
        (state.enabled&&!enabled?' A loja será despublicada ao retirar o último produto.':'')+'</p>',
        ()=>save({items,enabled,confirm_catalogue:enabled}),'Produto retirado da loja.',{label:'Retirar'});
    }

    async function moveItem(id, direction) {
      const items=state.items.map(item=>pick(item,itemFields));
      const from=items.findIndex(item=>item.variant_id===id);
      const to=from+direction;
      if (from<0 || to<0 || to>=items.length) return;
      [items[from],items[to]]=[items[to],items[from]];
      busy=true;
      try {
        const next=await save({items,confirm_catalogue:state.enabled});
        if (current()) {state=next;render();notify('Ordem da vitrine salva.');}
      } catch(ex) {if (current()) notify(ex.message,true);}
      finally {busy=false;}
    }

    async function readItems() {
      const output=host.querySelector('#osStoreItems');
      const sequence=++requestSequence;
      output.innerHTML=loading('Buscando produtos…');
      try {
        const response=await call(root+'/items?'+query({search:filters.search||'',offset}));
        if (!current() || sequence!==requestSequence) return;
        results=response.items;
        output.innerHTML=(results.length?itemTable(results,false):
          empty('box','Nenhuma variação disponível','Ative o preço fixo e a disponibilidade para a Loja rápida no cadastro do produto.',
            routeButton('Ver produtos','produtos-delivery','os-outline os-small')))+
          (offset||response.has_more?'<div class="os-pagination"><div class="os-actions">'+
            smallButton('Anterior','store-prev','left',offset?'':'disabled')+
            smallButton('Próxima','store-next','chevron',response.has_more?'':'disabled')+'</div></div>':'');
      } catch(ex) {if (current() && sequence===requestSequence) output.innerHTML=statusError(ex.message);}
    }

    function settingsValues(form) {
      const e=form.elements;
      const optionalNumber=field=>field.value===''?null:Number(field.value);
      return {replace_items:false,slug:e.store_slug.value.trim(),title:e.store_title.value.trim(),
        description:valueOrNull(e.store_description.value),accepting_orders:e.accepting_orders.checked,
        closed_message:valueOrNull(e.closed_message.value),logo_url:valueOrNull(e.logo_url.value),cover_url:valueOrNull(e.cover_url.value),
        public_phone:valueOrNull(e.public_phone.value),brand_color:e.brand_color.value,layout:e.layout.value,
        hide_unavailable:e.hide_unavailable.checked,allow_pickup:e.allow_pickup.checked,allow_delivery:e.allow_delivery.checked,
        pickup_details:valueOrNull(e.pickup_details.value),estimate_min_minutes:optionalNumber(e.estimate_min),
        estimate_max_minutes:optionalNumber(e.estimate_max),max_open_orders:Number(e.max_open_orders.value),
        minimum_subtotal_cents:window.OrionFields.parseMoney(e.minimum_subtotal.value,{required:false,min:0,max:1000000000})||0,
        closed_dates:[...form.querySelectorAll('[data-store-closed-day] input')].map(input=>input.value),
        opening_hours:[...form.querySelectorAll('[data-store-hour]')].map(row=>({
          weekday:Number(row.querySelector('select').value),opens:row.querySelector('[name^=opens_]').value,
          closes:row.querySelector('[name^=closes_]').value})),
        payment_methods:paymentOptions.filter(([key])=>e['method_'+key].checked).map(([key])=>key),
      };
    }

    host.addEventListener('submit',async event=>{
      const form=event.target;
      if (!['osStoreFilters','osDeliveryShopSettings'].includes(form.id)) return;
      event.preventDefault();
      if (form.id==='osStoreFilters') {
        filters.search=form.elements.store_search.value.trim();
        offset=0;void readItems();return;
      }
      if (!writeAllowed() || form.querySelector('[data-os-uploading=true]')) return;
      const error=form.querySelector('[data-store-settings-error]');
      const submit=form.querySelector('[type=submit]');
      busy=true;submit.disabled=true;error.textContent='';
      try {
        const next=await save(settingsValues(form));
        if (current()) {state=next;render();notify('Configurações salvas.');}
      } catch(ex) {if(current()) error.textContent=ex.message||'Não foi possível salvar as configurações.';}
      finally {busy=false;submit.disabled=false;}
    });

    host.addEventListener('keydown',event=>{
      if (!event.target.dataset.storeTab||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
      event.preventDefault();
      showTab(event.key==='Home'?'products':event.key==='End'?'settings':
        filters.tab==='products'?'settings':'products',true);
    });

    host.addEventListener('click',event=>{
      if (!current()) return;
      const control=event.target.closest('button');
      if (!control) return;
      if (control.dataset.storeTab) {showTab(control.dataset.storeTab,true);return;}
      if (control.matches('[data-store-add-hour],[data-store-add-date],[data-store-remove-hour],[data-store-remove-date]')) {
        if (!writeAllowed()) return;
        if (control.matches('[data-store-remove-hour],[data-store-remove-date]')) {
          control.closest('[data-store-hour],[data-store-closed-day]')?.remove();return;
        }
        const addHour=control.hasAttribute('data-store-add-hour');
        const rows=host.querySelector(addHour?'[data-store-hours]':'[data-store-dates]');
        if (rows.children.length>=(addHour?28:60)) {notify('Limite de registros atingido.',true);return;}
        rows.insertAdjacentHTML('beforeend',addHour?hour():closedDay());
        window.OrionFields.refresh(rows);
        rows.lastElementChild.querySelector('select,input')?.focus();
        return;
      }
      const action=control.dataset.osAction;
      if (!action?.startsWith('store-')) return;
      event.stopPropagation();
      if (action==='store-prev'||action==='store-next') {
        offset=Math.max(0,offset+(action==='store-next'?25:-25));void readItems();return;
      }
      if (action==='store-settings') {showTab('settings',true);return;}
      if (action==='store-copy' && state.public_url) {
        if (!navigator.clipboard?.writeText) {notify('Selecione e copie o link da loja.',true);return;}
        void navigator.clipboard.writeText(state.public_url).then(()=>notify('Link copiado.'),
          ()=>notify('Selecione e copie o link da loja.',true));return;
      }
      if (!writeAllowed()) return;
      if (action==='store-pix') {
        void editPix();
        return;
      }
      if (action==='store-pause'||action==='store-publish'||action==='store-unpublish') changeShop(action.slice(6));
      const id=control.dataset.storeVariant;
      if (action==='store-add') editItem(results.find(item=>item.variant_id===id));
      if (action==='store-remove') removeItem(state.items.find(item=>item.variant_id===id));
      if (action==='store-up'||action==='store-down') void moveItem(id,action==='store-up'?-1:1);
    });

    async function editPix() {
      try {
        const saved=await call('/payments/pix');
        if (!current()) return;
        const types=[['random','Chave aleatória'],['email','E-mail'],['phone','Telefone'],['cpf','CPF'],['cnpj','CNPJ']];
        const instructions=saved.suffix?'Chave atual com final '+esc(saved.suffix)+' · '+esc(saved.holder):
          'Nenhuma chave cadastrada. Você pode usar a Orion sem cadastrar uma chave.';
        const remove=saved.suffix?checkField('Remover a chave para novos pedidos','remove_pix',false):'';
        openEditor('Chave Pix da distribuidora','<p>'+instructions+'</p>'+
          '<p class="os-help">O cliente verá a chave no pedido. Confira o crédito no extrato antes de registrar o recebimento.</p>'+
          selectField('Tipo de chave','pix_type',types,saved.key_type||'random')+
          field('Chave Pix','pix_key','text','','maxlength="100" autocomplete="off"')+
          field('Nome do titular no banco','pix_holder','text',saved.holder||'','maxlength="120"')+remove,
          async(form,values)=>{
            const removeKey=!!form.elements.remove_pix?.checked;
            if(!removeKey&&(!values.pix_key.trim()||!values.pix_holder.trim()))throw Error('Preencha a chave Pix e o titular.');
            await call('/payments/pix','PUT',{expected_revision:saved.revision,...(removeKey?{remove:true}:
              {key_type:values.pix_type,key:values.pix_key.trim(),holder:values.pix_holder.trim()})});
          },'Configuração de Pix salva.',{label:'Salvar'});
      } catch(error){if(current())notify(error.message,true);}
    }

    host.innerHTML=title('Loja rápida','')+loading('Carregando loja…');
    try {
      state=await call(root);
      if (current()) render();
    } catch(ex) {if (current()) host.innerHTML=title('Loja rápida','')+statusError(ex.message);}
  }

  window.OrionDeliveryStorefront = Object.freeze({mount});
})();
