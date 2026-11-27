/* Manual orders and delivery dispatch in the Orion management workspace. */
(function () {
  'use strict';

  const statusLabels={confirmed:'Reservado',dispatched:'Em rota',fulfilled:'Concluído',cancelled:'Cancelado'};
  const modes=[['counter','Balcão'],['pickup','Retirada'],['delivery','Entrega']];
  const payments=[['','Selecione'],['cash','Dinheiro'],['pix','Pix confirmado'],['debit_card','Cartão de débito'],['credit_card','Cartão de crédito']];
  const storefrontPaymentLabels={cash:'Dinheiro',debit_card:'Cartão de débito',credit_card:'Cartão de crédito',pix:'Pix na entrega'};
  const intakeChannels=[['pos','Ponto de venda'],['phone','Telefone'],['attendant','Atendimento humano']];
  const salesChannels={...Object.fromEntries(intakeChannels),storefront:'Loja pública',manual:'Painel'};
  function paymentInstructions(order,money) {
    const method=order.tender_preference||order.storefront?.payment_preference;
    const change=order.change_for_cents??order.storefront?.change_for_cents;
    if (!method) return '';
    return '<p>Preferência de pagamento: '+storefrontPaymentLabels[method]+
      (['debit_card','credit_card'].includes(method)&&order.mode==='delivery'?' · Levar maquininha':'')+'</p>'+
      (method==='cash'&&change!=null?'<p>Troco para '+money(change)+' · Troco previsto '+money(change-order.total_cents)+'</p>':'');
  }
  const creditStatuses=[['all','Todos'],['pending','Pendentes'],['overdue','Em atraso'],['paid','Recebidos']];
  const command = (button,label,action,symbol,extra='') => button(label,action,'os-outline os-small',symbol,extra);
  const code = order => order.id.slice(0,8).toUpperCase();
  const modeLabel = order => modes.find(([key])=>key===order.mode)?.[1];
  const picture = (item,esc) => window.OrionDeliveryEditor?.picture(item.image_url,esc)||'';

  const orderDateTime=(value,{fmtDate,dateKey,timeLabel})=>fmtDate(dateKey(new Date(value)))+' '+timeLabel(value);
  const statusBadge=(state,badge)=>badge(state==='fulfilled'?'completed':['confirmed','dispatched'].includes(state)?'pending':state,statusLabels[state]);
  const orderAttribute=(name,id,esc)=>'data-delivery-'+name+'="'+esc(id)+'"';
  const orderError=(error,esc)=>'<div class="os-notice os-error" role="alert"><p>'+esc(error.message || 'Não foi possível concluir a ação.')+'</p></div>';

  function openOrderEditor({call,current,ui,openEditor,deliveryOnly=false,onCreated,repeat=null}) {
    const {esc,loading,field,moneyField,selectField,checkField,button,money,query,brazilianStates,requestKey,localISO}=ui;
    const containerUi=window.OrionDeliveryOrderContainers;
    const itemAttr=(name,id)=>orderAttribute(name,id,esc);
    const err=error=>orderError(error,esc);
    const cart=new Map();
    let customer=null, estimate=null, customerSaving=false;
    let packaging, policy;
    const addressFields='<fieldset data-delivery-address hidden disabled class="os-form">'+
      '<legend>Endereço de entrega</legend>'+
      '<div class="os-actions hidden" data-delivery-previous-control hidden>'+
      command(button,'Usar último endereço','delivery-use-address','left')+
      command(button,'Endereços do cliente','delivery-address-book','users')+'</div>'+
      '<div class="os-form hidden" data-delivery-address-book hidden>'+
      selectField('Endereço salvo','saved_address',[['','Novo endereço']],'')+
      field('Identificação do endereço','address_label','text','','maxlength="60" placeholder="Casa ou Trabalho"')+
      '<div class="os-actions">'+command(button,'Salvar endereço','delivery-address-save','check')+
      command(button,'Excluir endereço','delivery-address-remove','trash')+'</div></div>'+
      '<p data-delivery-address-status class="os-help" aria-live="polite"></p><div class="os-form-grid">'+
      field('CEP','address_postal','text','','data-orion-field="postal-code" inputmode="numeric" autocomplete="postal-code"')+
      '<div class="os-actions">'+command(button,'Buscar CEP','delivery-lookup-cep','search')+'</div>'+
      field('Rua','address_street','text','','required maxlength="160"')+
      field('Número','address_number','text','','required maxlength="30"')+checkField('Sem número','address_no_number')+
      field('Bairro','address_neighborhood','text','','required maxlength="120"')+
      field('Cidade','address_city','text','','required maxlength="120"')+
      selectField('UF','address_state',[['','Selecione'],...brazilianStates.map(state=>[state,state])],'','required')+
      field('Complemento ou referência','address_complement','text','','maxlength="160"')+
      moneyField('Taxa de entrega','delivery_fee','text',window.OrionFields.moneyDisplay(0))+
      '<div class="hidden" data-delivery-schedule-box hidden>'+
      field('Agendar para','delivery_schedule','datetime-local','','aria-describedby="osDeliveryScheduleHint"')+
      '<p id="osDeliveryScheduleHint" class="os-help">Horário da empresa</p></div>'+
      '</div></fieldset>';
    const quickCustomer='<fieldset data-delivery-quick-customer hidden disabled class="os-form hidden">'+
      '<legend>Novo cliente</legend><div class="os-form-grid">'+
      field('Nome do cliente','new_customer_name','text','','maxlength="120"')+
      field('Telefone do cliente','customer_phone','tel','','maxlength="40"')+'</div>'+
      '<div data-delivery-customer-feedback aria-live="polite"></div><div class="os-actions">'+
      command(button,'Salvar cliente','delivery-save-customer','check')+'</div></fieldset>';
    const content=selectField('Recebimento','mode',deliveryOnly?modes.slice(2):modes,repeat?.mode||(deliveryOnly?'delivery':'counter'))+
      '<div class="os-form-grid">'+field('Buscar produto','product_search','search','','maxlength="120"')+
      field('Buscar cliente','customer_search','search','','maxlength="120" placeholder="Nome ou telefone"')+'</div>'+
      '<div class="os-actions">'+command(button,'Buscar produtos','delivery-choose-items','box')+
      command(button,'Buscar clientes','delivery-choose-customer','users')+
      command(button,'Novo cliente','delivery-new-customer','userPlus')+'</div>'+quickCustomer+
      '<div data-delivery-search-results aria-live="polite"></div><div data-delivery-customer aria-live="polite"></div>'+
      '<h3>Itens do pedido</h3><div class="os-form" data-delivery-cart></div>'+
      (containerUi?.fields()||'')+
      addressFields+
      '<details class="os-more-fields"><summary>Origem, pagamento e observações</summary><div class="os-form-grid">'+
      selectField('Origem do pedido','intake_channel',[['','Painel'],...intakeChannels],'')+
      selectField('Pagamento combinado','tender_preference',[['','A combinar'],...Object.entries(storefrontPaymentLabels)],'')+
      '<div hidden data-delivery-cash-change>'+moneyField('Troco para','cash_change','text','')+'</div>'+
      field('Observação do pedido','operator_note','text','','maxlength="300"')+'</div></details>'+
      '<div class="os-actions">'+command(button,'Conferir total','delivery-estimate','check')+'</div>'+
      '<div data-delivery-estimate aria-live="polite"></div>';
    const items=()=>[...cart].map(([id,item])=>({variant_id:id,quantity:window.OrionFields.parseDecimal(item.quantity,{required:true,scale:0,min:1,max:1000})}));
    const estimateInput=form=>{
      const mode=form.elements.mode.value;
      const base={mode,items:items(),delivery_fee_cents:0};
      if(mode!=='delivery')return base;
      const values=form.elements, [day,time]=(values.delivery_schedule.value||'').split('T');
      return {...base,delivery_fee_cents:policy?.coverage_mode==='areas'?0:
        window.OrionFields.parseMoney(values.delivery_fee.value,{required:true,min:0,max:1000000000}),
        address:{postal_code:window.OrionFields.postalCodeValue(values.address_postal.value),
          street:values.address_street.value,number:values.address_no_number.checked?null:values.address_number.value||null,
          no_number:values.address_no_number.checked,neighborhood:values.address_neighborhood.value,
          city:values.address_city.value,state:values.address_state.value,complement:values.address_complement.value||null},
        scheduled_at:day?localISO(day,time):null};
    };
    openEditor(repeat?'Repetir pedido':'Novo pedido',content,async(form,values,key)=>{
      if (customerSaving) throw Error('Aguarde o cadastro do cliente.');
      const quoted=estimateInput(form);
      if (quoted.mode!=='counter'&&!customer) throw Error('Selecione um cliente para a retirada ou entrega.');
      if (!estimate||estimate.key!==JSON.stringify(quoted)) throw Error('Confira o total antes de confirmar o pedido.');
      const payload={...quoted,customer_id:customer?.id||null,estimate_version:estimate.version,
        container_plan:packaging?.collect(quoted.items,customer?.id)||[]};
      if (values.intake_channel) payload.intake_channel=values.intake_channel;
      if (values.tender_preference) payload.tender_preference=values.tender_preference;
      if (values.operator_note?.trim()) payload.operator_note=values.operator_note.trim();
      if (values.tender_preference==='cash'&&values.cash_change) {
        const change=window.OrionFields.parseMoney(values.cash_change,{min:0,max:1000000000});
        if(change) {
          if(change<estimate.total)throw Error('O valor para troco deve cobrir o total do pedido.');
          payload.change_for_cents=change;
        }
      }
      const saved=await call('/orders','POST',{...payload,idempotency_key:key(payload)});
      if (saved?.status!=='confirmed') throw Error('Não foi possível confirmar a reserva.');
      onCreated?.();
    },'Pedido reservado.',{label:'Confirmar pedido',bind:dialog=>{
      const form=dialog.querySelector('form'), matches=form.querySelector('[data-delivery-search-results]');
      const quote=form.querySelector('[data-delivery-estimate]'), basket=form.querySelector('[data-delivery-cart]');
      const customerInfo=form.querySelector('[data-delivery-customer]');
      const addressBox=form.querySelector('[data-delivery-address]');
      const previous=form.querySelector('[data-delivery-previous-control]');
      const addressStatus=form.querySelector('[data-delivery-address-status]');
      const book=form.querySelector('[data-delivery-address-book]');
      const bookChoice=form.elements.saved_address, labelField=form.elements.address_label;
      const removeAddress=book.querySelector('[data-os-action="delivery-address-remove"]');
      let savedAddresses=[], bookVersion=0, unsavedId, deletionPending=false;
      const quick=form.querySelector('[data-delivery-quick-customer]');
      const feedback=form.querySelector('[data-delivery-customer-feedback]');
      const addressNames=['street','number','neighborhood','city','state','complement'];
      let products=[], customers=[], searchVersion=0, quoteVersion=0, addressVersion=0, addressOwner=null;
      const dialogActive = () => current()&&dialog.open&&form.isConnected;
      const invalidate = () => {quoteVersion++;estimate=null;quote.replaceChildren();};
      packaging=containerUi?.bind(form,{call,ui,current:dialogActive});
      const scheduleBox=form.querySelector('[data-delivery-schedule-box]');
      function resetAddressBook() {
        bookVersion++;
        book.hidden=true;
        book.classList.add('hidden');
        savedAddresses=[];
        bookChoice.replaceChildren(new Option('Novo endereço',''));
        labelField.value='';
        unsavedId=null;
        deletionPending=false;
        removeAddress.hidden=true;
      }
      function selectBookEntry(row) {
        unsavedId=null;
        deletionPending=false;
        removeAddress.hidden=!row;
        labelField.value=row?.label||'';
        addressStatus.textContent='';
        if (row) fillAddress(row.address);
      }
      function fillAddress(address) {
        addressVersion++;
        addressOwner=customer?.id;
        form.elements.address_postal.value=address.postal_code||'';
        for (const name of addressNames) form.elements['address_'+name].value=address[name]||'';
        form.elements.address_no_number.checked=!!address.no_number;
        updateAddress();
        invalidate();
        window.OrionFields.refresh(addressBox);
        addressStatus.textContent='Confira o endereço antes de confirmar.';
      }
      function updateAddress() {
        addressBox.hidden=addressBox.disabled=form.elements.mode.value!=='delivery';
        addressBox.classList.toggle('hidden',addressBox.hidden);
        previous.hidden=!customer;
        previous.classList.toggle('hidden',previous.hidden);
        form.elements.address_number.disabled=form.elements.address_no_number.checked;
        form.elements.delivery_fee.readOnly=policy?.coverage_mode==='areas';
        scheduleBox.hidden=!policy?.scheduling_enabled;
        scheduleBox.classList.toggle('hidden',scheduleBox.hidden);
        form.elements.delivery_schedule.disabled=scheduleBox.hidden;
      }
      function setCustomer(next) {
        if (customer?.id!==next?.id) {
          addressVersion++;
          resetAddressBook();
        }
        if ((customer||addressOwner)&&customer?.id!==next?.id) {
          addressOwner=null;
          form.elements.address_postal.value='';
          for (const name of addressNames) form.elements['address_'+name].value='';
          form.elements.address_no_number.checked=false;
        }
        customer=next;
        customerInfo.textContent=customer?'Cliente: '+customer.name:'';
        addressStatus.textContent='';
        invalidate();
        updateAddress();
      }
      function toggleQuick(show) {
        quick.hidden=quick.disabled=!show;
        quick.classList.toggle('hidden',!show);
        if (show) form.elements.new_customer_name.focus();
      }
      function showCart() {
        basket.innerHTML=cart.size?Array.from(cart,([id,row])=>
          '<div class="os-form-grid"><div class="os-delivery-shop-item-name">'+picture(row,esc)+field(row.label,'qty-'+id,'number',row.quantity,
            'required min="1" max="1000" step="1" '+itemAttr('quantity',id))+
          '</div><div class="os-actions">'+command(button,'Remover','delivery-remove-item','close',itemAttr('match',id))+'</div></div>').join(''):
          '<p class="os-help">Busque um produto para adicionar itens.</p>';
        window.OrionFields.refresh(basket);
        packaging?.render(cart);
      }
      if (repeat) {
        for (const item of repeat.items) {
          cart.set(item.variant_id,{quantity:item.quantity,label:item.product_name+' · '+item.variant_name,image_url:item.image_url});
        }
        if (repeat.customer_id) setCustomer({id:repeat.customer_id,name:repeat.customer_name||'Cliente do pedido'});
      }
      showCart();
      updateAddress();
      void call('/logistics').then(value=>{
        if(!dialogActive())return;
        policy=value;
        updateAddress();
      }).catch(()=>{});
      form.addEventListener('change',event=>{
        if (event.target.name==='tender_preference') {
          const hide=event.target.value!=='cash';
          form.querySelector('[data-delivery-cash-change]').hidden=hide;
          if(hide)form.elements.cash_change.value='';
        }
        if (event.target.name==='mode') {addressVersion++;resetAddressBook();updateAddress();invalidate();products=[];matches.replaceChildren();}
        if (event.target===bookChoice) {bookVersion++;selectBookEntry(savedAddresses.find(row=>row.id===bookChoice.value));}
        if (event.target.name==='address_no_number') {addressVersion++;updateAddress();invalidate();}
      });
      form.addEventListener('input',event=>{
        if (event.target.name?.startsWith('address_')) {addressVersion++;addressStatus.textContent='';invalidate();}
        if (['delivery_fee','delivery_schedule'].includes(event.target.name)) invalidate();
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
          cart.set(id,{label:item.name+' · '+item.variant_name,quantity:cart.get(id)?.quantity||'1',
            image_url:item.image_url,exchange_container_id:item.exchange_container_id,exchange_container_name:item.exchange_container_name});
          invalidate();showCart();basket.querySelector('[data-delivery-quantity="'+id+'"]')?.focus();return;
        }
        if (action==='delivery-pick-customer') {
          setCustomer(customers.find(c=>c.id===id)||null);
          matches.replaceChildren();return;
        }
        if (action==='delivery-new-customer') {toggleQuick(quick.hidden);return;}
        if (action==='delivery-save-customer'&&!customerSaving) {
          feedback.replaceChildren();
          try {
            const name=form.elements.new_customer_name.value.trim();
            if (!name) throw Error('Informe o nome do cliente.');
            const phone=window.OrionFields.phoneValue(form.elements.customer_phone.value,{allowInternational:true})||null;
            const input={name,phone};
            customerSaving=node.disabled=true;
            feedback.innerHTML=loading('Salvando cliente…');
            const created=await call('/order-customers','POST',{...input,idempotency_key:requestKey(form,input)});
            if (!dialogActive()) return;
            if (!created?.id) throw Error('Confira o cadastro antes de repetir.');
            setCustomer(created);
            feedback.replaceChildren();
            form.elements.new_customer_name.value=form.elements.customer_phone.value='';
            toggleQuick(false);
          } catch (error) {if (dialogActive()) feedback.innerHTML=err(error);}
          finally {customerSaving=node.disabled=false;}
          return;
        }
        if (action==='delivery-use-address'&&customer) {
          const selected=customer.id;
          const version=++addressVersion;
          node.disabled=true;
          addressStatus.textContent='Buscando o endereço…';
          try {
            const result=await call('/order-customers/'+encodeURIComponent(selected)+'/previous-address');
            if (!dialogActive()||selected!==customer?.id||addressVersion!==version) return;
            const address=result.address;
            if (address) {resetAddressBook();fillAddress(address);}
            addressStatus.textContent=address?'Confira o endereço antes de confirmar.':'Nenhum endereço de entrega concluída.';
          } catch (error) {if (dialogActive()&&selected===customer?.id&&addressVersion===version) addressStatus.textContent=error.message;}
          finally {node.disabled=false;}
          return;
        }
        if (['delivery-address-book','delivery-address-save','delivery-address-remove'].includes(action)&&customer) {
          const owner=customer.id, path='/order-customers/'+encodeURIComponent(owner)+'/addresses';
          const version=++bookVersion;
          const active=()=>dialogActive()&&customer?.id===owner&&version===bookVersion;
          const row=savedAddresses.find(entry=>entry.id===bookChoice.value);
          if (action==='delivery-address-book'&&!book.hidden) {resetAddressBook();return;}
          node.disabled=true;
          try {
            if (action==='delivery-address-book') {
              const result=await call(path);
              if (!active()) return;
              savedAddresses=result.items;
              bookChoice.replaceChildren(new Option('Novo endereço',''),...savedAddresses.map(entry=>new Option(entry.label,entry.id)));
              book.hidden=false;
              book.classList.remove('hidden');
              selectBookEntry(null);
              bookChoice.focus();
            } else if (action==='delivery-address-save') {
              const inputVersion=addressVersion;
              const v=form.elements;
              if (!labelField.value.trim()) {labelField.focus();throw Error('Informe um nome para o endereço.');}
              const address={postal_code:window.OrionFields.postalCodeValue(v.address_postal.value),no_number:v.address_no_number.checked};
              for (const name of addressNames) address[name]=v['address_'+name].value.trim()||null;
              if (address.no_number) address.number=null;
              if (!address.street||!address.city||!address.state||!address.neighborhood||!(address.number||address.no_number))
                throw Error('Preencha rua, número, bairro, cidade e UF antes de salvar.');
              unsavedId ||= crypto.randomUUID();
              const saved=await call(path+'/'+encodeURIComponent(row?.id||unsavedId),'PUT',
                {label:labelField.value.trim(),address,expected_revision:row?.revision||0});
              if (!active()) return;
              savedAddresses=savedAddresses.filter(entry=>entry.id!==saved.id).concat(saved);
              bookChoice.replaceChildren(new Option('Novo endereço',''),...savedAddresses.map(entry=>new Option(entry.label,entry.id)));
              bookChoice.value=saved.id;
              if (inputVersion===addressVersion) selectBookEntry(saved);
              else {removeAddress.hidden=false;deletionPending=false;}
              addressStatus.textContent='Endereço salvo no cadastro do cliente.';
            } else if (row) {
              if (!deletionPending) {deletionPending=true;addressStatus.textContent='Para confirmar a exclusão de '+row.label+', toque em Excluir endereço novamente.';return;}
              await call(path+'/'+encodeURIComponent(row.id)+'?revision='+row.revision,'DELETE');
              if (!active()) return;
              savedAddresses=savedAddresses.filter(entry=>entry.id!==row.id);
              bookChoice.selectedOptions[0]?.remove();
              bookChoice.value='';
              selectBookEntry(null);
              addressStatus.textContent='Endereço excluído do cadastro.';
            }
          } catch(error) {if(active())addressStatus.textContent=error.message;}
          finally {node.disabled=false;}
          return;
        }
        if (action==='delivery-lookup-cep') {
          let cep;
          try {cep=window.OrionFields.postalCodeValue(form.elements.address_postal.value,{required:true});}
          catch(error) {addressStatus.textContent=error.message;return;}
          if (!cep) {addressStatus.textContent='Informe o CEP com oito dígitos.';return;}
          const version=++addressVersion;
          node.disabled=true;
          addressStatus.textContent='Consultando CEP…';
          try {
            const address=await call('/cep/'+encodeURIComponent(cep));
            if (!dialogActive()||addressVersion!==version) return;
            for (const name of ['street','neighborhood','city','state']) {
              if (address[name]) form.elements['address_'+name].value=address[name];
            }
            addressOwner=null;
            invalidate();
            window.OrionFields.refresh(addressBox);
            addressStatus.textContent='Confira o endereço e informe o número.';
          } catch(error) {if(dialogActive()&&addressVersion===version) addressStatus.textContent=error.message;}
          finally {node.disabled=false;}
          return;
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
            estimate={version:result.estimate_version,total:result.total_cents,key};
            quote.innerHTML=(input.mode==='delivery'?'<p>Produtos '+money(result.subtotal_cents)+
              ' · Taxa '+money(result.delivery_fee_cents)+'</p>':'')+'<p><strong>Total</strong> '+money(result.total_cents)+'</p>'+
              (result.eta_minutes?'<p>Prazo estimado para o bairro: '+esc(String(result.eta_minutes))+' min.</p>':'');
          } catch(error) {if (latest()) quote.innerHTML=err(error);}
          return;
        }
        if (!['delivery-choose-items','delivery-choose-customer'].includes(action)) return;
        const sequence=++searchVersion;
        const latest = () => dialogActive()&&sequence===searchVersion;
        matches.innerHTML=loading('Buscando…');
        try {
          if (action==='delivery-choose-items') {
            const mode=form.elements.mode.value;
            const result=await call('/catalog?'+query({state:'active',sale_mode:mode,search:form.elements.product_search.value}));
            if (!latest()) return;
            if (mode!==form.elements.mode.value) return;
            products=result.items;
            matches.innerHTML=products.length?'<div class="os-actions">'+products.map(item=>
                '<div class="os-delivery-shop-item-name">'+picture(item,esc)+
                command(button,item.name+' · '+item.variant_name,'delivery-add-item',item.image_url?'':'plus',
                  itemAttr('match',item.variant_id))+'</div>').join('')+'</div>':
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

  async function showOrderDetail({id,call,current,openDetails,ui}) {
    const {esc,money,badge}=ui;
    const containerUi=window.OrionDeliveryOrderContainers;
    const when=value=>orderDateTime(value,ui);
    try {
      const result=await call('/orders/'+encodeURIComponent(id));
      if (!current()) return;
      const address=result.address;
      const addressText=address?'<div class="os-card"><h3>Entrega</h3><p>'+esc([
        address.street+(address.number?', '+address.number:' (sem número)'),address.neighborhood,address.city+' - '+address.state,
        address.postal_code,address.complement,
      ].filter(Boolean).join(' · '))+'</p><p>Taxa '+money(result.delivery_fee_cents)+'</p>'+
      (result.scheduled_at?'<p>Agendado para '+esc(when(result.scheduled_at))+'</p>':'')+
      (result.courier_name?'<p>Entregador: '+esc(result.courier_name)+'</p>':'')+'</div>':'';
      const sourceText=result.storefront?'<p>Pedido pela loja · '+esc(result.storefront.customer_name||'')+' · '+
        esc(result.storefront.contact_e164||'')+'</p>':'<p>Origem: '+esc(salesChannels[result.sales_channel]||'Painel')+'</p>';
      const note=result.storefront?.buyer_note||result.operator_note;
      openDetails('Pedido '+code({id}),'<p>'+statusBadge(result.status,badge)+' · '+money(result.total_cents)+'</p>'+
        sourceText+paymentInstructions(result,money)+(note?'<p>Observação: '+esc(note)+'</p>':'')+addressText+
        '<ul class="os-delivery-order-items">'+result.items.map(item=>
        '<li>'+picture(item,esc)+esc(item.quantity+' × '+item.product_name+' · '+item.variant_name)+' · '+money(item.total_cents)+'</li>').join('')+
        '</ul>'+(containerUi?.summary(result.container_plan,ui)||'')+'<h3>Histórico</h3><ul>'+result.history.map(event=>
          '<li>'+esc((({received:'Recebimento registrado',partially_received:'Recebimento parcial',returned:'Devolução registrada',
            delivery_rescheduled:'Entrega reagendada',delivery_recipient_absent:'Cliente ausente'})[event.action]||
            statusLabels[event.action]||event.action)+' · '+when(event.created_at))+
            (event.details?.tenders?.length?'<ul>'+event.details.tenders.map(t=>'<li>'+esc(payments.find(([key])=>key===t.method)?.[1]||t.method)+' · '+money(t.amount_cents)+'</li>').join('')+'</ul>':'')+
            '</li>').join('')+'</ul>');
    } catch(error) {if (current()) openDetails('Pedido '+code({id}),orderError(error,esc));}
  }

  async function salesReport({host,filters,call,allowed,ui,exportPath}) {
    const {esc,loading,field,selectField,title,metric,money,fmtDate,dateKey,timeLabel,query,button}=ui;
    if (!host||!allowed()) return;
    filters.start ??= '';
    filters.end ??= '';
    filters.mode ??= 'all';
    filters.state ??= 'all';
    filters.receipt ??= 'all';
    filters.channel ??= 'all';
    filters.offset ??= 0;
    const paymentsByStatus={pending:'A receber',paid:'Recebido',refunded:'Estornado',cancelled:'Cancelado'};
    host.innerHTML=title('Relatório de vendas','Pedidos criados no período')+
      '<form id="osDeliverySalesFilters" class="os-filter-row">'+
      field('De','start','date',filters.start)+field('Até','end','date',filters.end)+
      selectField('Atendimento','mode',[['all','Todos'],...modes],filters.mode)+
      selectField('Origem','channel',[['all','Todas'],...Object.entries(salesChannels)],filters.channel)+
      selectField('Status','state',[['all','Todos'],...Object.entries(statusLabels)],filters.state)+
      selectField('Recebimento','receipt',[['all','Todos'],['paid','Recebidos'],['pending','A receber'],['refunded','Estornados'],['none','Sem recebimento']],filters.receipt)+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
      '<div data-delivery-report aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const form=host.querySelector('form'), panel=host.querySelector('[data-delivery-report]');
    let version=0, result;
    function rows(items) {
      const columns=['Pedido','Data','Cliente','Atendimento','Origem','Status','Recebimento','Total'];
      return '<div class="os-table-scroll" tabindex="0" role="region" aria-label="Tabela de dados"><table class="os-table"><thead><tr>'+
        columns.map(column=>'<th>'+column+'</th>').join('')+'</tr></thead><tbody>'+items.map(order=>'<tr>'+
        [code(order),fmtDate(dateKey(new Date(order.created_at)))+' '+timeLabel(order.created_at),
          order.customer_name||'Venda avulsa',modeLabel(order),salesChannels[order.sales_channel]||'Painel',statusLabels[order.status],
          paymentsByStatus[order.receipt_status]||'Sem recebimento',money(order.total_cents)]
          .map((value,i)=>'<td data-label="'+columns[i]+'">'+esc(value)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
    }
    async function read() {
      const attempt=++version;
      const current=()=>allowed()&&host.isConnected&&attempt===version;
      panel.innerHTML=loading('Carregando vendas…');
      try {
        const data=await call('/reports/sales?'+query(filters));
        if (!current()) return;
        result=data;
        form.elements.start.value=filters.start=data.period.start_date;
        form.elements.end.value=filters.end=data.period.end_date;
        const {offset,limit,summary}=data;
        const csv=exportPath+'?'+query({start:filters.start,end:filters.end,mode:filters.mode,state:filters.state,receipt:filters.receipt,channel:filters.channel});
        panel.innerHTML='<div class="os-metrics">'+metric('Pedidos',summary.orders,'deliveryReceipt')+
          metric('Cancelados',summary.cancelled,'close')+metric('Recebido',money(summary.paid_cents),'wallet')+
          metric('A receber',money(summary.pending_cents),'clock')+'</div>'+
          '<section class="os-card os-section-gap"><div class="os-card-header"><h2>Vendas</h2>'+
          '<a class="os-button os-outline os-small" href="'+esc(csv)+'" download>Exportar CSV</a></div>'+
          (data.items.length?rows(data.items):'<p class="os-help">Nenhuma venda encontrada para esses filtros.</p>')+
          '</section>'+
          (offset||data.has_more?'<div class="os-pagination"><span>Página '+(offset/limit+1)+'</span><div class="os-actions">'+
            command(button,'Anterior','delivery-report-prev','left',offset?'':'disabled')+
            command(button,'Próxima','delivery-report-next','chevron',data.has_more?'':'disabled')+'</div></div>':'');
      } catch (error) {
        if (current()) panel.innerHTML='<div class="os-notice os-error" role="alert"><p>'+esc(error.message)+'</p></div>';
      }
    }
    form.addEventListener('submit',event=>{
      event.preventDefault();
      Object.assign(filters,Object.fromEntries(new FormData(form)),{offset:0});
      void read();
    });
    panel.addEventListener('click',event=>{
      const action=event.target.closest('button')?.dataset.osAction;
      if (!['delivery-report-prev','delivery-report-next'].includes(action)||!allowed()) return;
      event.stopPropagation();
      filters.offset=Math.max(0,result.offset+(action==='delivery-report-next'?result.limit:-result.limit));
      void read();
    });
    await read();
  }

  async function mount({host,filters,call,allowed,canWrite,canManagePolicy=()=>false,ui,openDetails,openEditor,deliveryOnly=false,creditOnly=false}) {
    if (!host || !allowed()) return;
    const {esc,loading,empty,field,moneyField,selectField,textField,checkField,button,title,money,badge,fmtDate,dateKey,query,metric,routeButton,localISO}=ui;
    const containerUi=window.OrionDeliveryOrderContainers;
    filters.search ??= '';
    filters.state ??= 'all';
    filters.start ??= '';
    filters.end ??= '';
    filters.offset ??= 0;
    let data, stamp=0, lanes={};
    const boardEnabled=!creditOnly&&!deliveryOnly;
    filters.orderView??='list';
    const offsets=filters.boardOffsets??={};
    const current = () => allowed() && host.isConnected;
    const when=value=>orderDateTime(value,ui);
    const itemAttr=(name,id)=>orderAttribute(name,id,esc);
    const err=error=>orderError(error,esc);

    host.innerHTML=title(creditOnly?'Vendas a prazo':deliveryOnly?'Entregas':'Pedidos',
      creditOnly?'Vencimentos e recebimentos':deliveryOnly?'Despacho e acompanhamento':'Balcão, retirada e entrega',
      creditOnly?routeButton('Ver pedidos','pedidos-delivery','os-outline os-small','deliveryReceipt'):
      canWrite()?button('Novo pedido','delivery-new-order','','plus'):'')+
      '<form id="osDeliveryOrderFilters" class="os-filter-row">'+
      (boardEnabled?selectField('Visualização','orderView',[['list','Lista'],['board','Kanban']],filters.orderView):'')+
      field('Buscar','search','search',filters.search,'maxlength="120" placeholder="Número ou cliente"')+
      selectField('Status','state',creditOnly?creditStatuses:[['all','Todos'],...Object.entries(statusLabels)],filters.state)+
      (creditOnly?'':field(deliveryOnly?'Entrega de':'De','start','date',filters.start)+
        field(deliveryOnly?'Entrega até':'Até','end','date',filters.end))+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form><div id="osDeliveryOrderList" aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const list=host.querySelector('#osDeliveryOrderList');

    function orderCard(order) {
      const actions = itemAttr('order',order.id);
      let controls=command(button,'Detalhes','delivery-order-detail','list',actions);
      if (canWrite()&&['confirmed','dispatched'].includes(order.status)) {
        controls+=order.mode==='delivery'&&order.status==='confirmed'
          ?(order.assignment_status==='offered'?command(button,'Retirar solicitação','delivery-order-withdraw','close',actions):
            command(button,'Despachar','delivery-order-dispatch','deliveryTruck',actions))
          :order.pix_state==='pending'?'':command(button,'Concluir','delivery-order-fulfill','check',actions);
        if(order.pix_state==='pending'&&canManagePolicy())controls+=command(button,'Conferir Pix','delivery-order-pix','wallet',actions);
        if(order.mode==='delivery')controls+=order.status==='dispatched'
          ?command(button,'Cliente ausente','delivery-order-absent','users',actions)
          :command(button,'Reagendar','delivery-order-reschedule','calendar',actions);
        controls+=button('Cancelar pedido','delivery-order-cancel','os-ghost os-small','close',actions);
      }
      const pending=order.receipt_status==='pending';
      const late=pending&&order.payment_due_date<dateKey();
      if (canWrite()&&order.payment_due_date&&pending) {
        controls+=command(button,'Registrar recebimento','delivery-order-settle','wallet',actions);
      }
      if (canWrite()&&order.status==='fulfilled'&&!creditOnly) {
        controls+=button('Registrar devolução','delivery-order-return','os-ghost os-small','left',actions);
      }
      if (canWrite()&&!creditOnly) controls+=button('Repetir pedido','delivery-order-repeat','os-ghost os-small','plus',actions);
      const resolution=order.receipt_status==='refunded'?'Estornado':order.receipt_status==='cancelled'?'Recebimento cancelado':'';
      const payment=(order.payment_due_date?'<p class="os-help">Vencimento '+fmtDate(order.payment_due_date)+'</p>':'')+
        (resolution?badge('cancelled',resolution):order.payment_due_date?
          badge(pending?(late?'expired':'pending'):'paid',pending?(late?'Em atraso':'A receber'):'Recebido'):'');
      return '<article class="os-card os-item-card"><h3>Pedido '+esc(code(order))+'</h3><p class="os-help">'+
        esc([order.customer_name||'Venda avulsa',modeLabel(order),when(order.created_at)].join(' · '))+'</p>'+
        (order.pix_state&&order.status!=='cancelled'?'<p>'+ (order.pix_state==='confirmed'?'Pix conferido pelo proprietário':'Pix aguardando conferência')+'</p>':'')+
        (order.courier_name?'<p class="os-help">Entregador: '+esc(order.courier_name)+'</p>':'')+
        (['offered','arrived','refused','expired'].includes(order.assignment_status)?'<p class="os-help">'+
          esc(({offered:'Aguardando aceite',arrived:'Entregador no endereço',refused:'Solicitação recusada',expired:'Prazo de aceite encerrado'})[order.assignment_status])+'</p>':'')+
        (order.scheduled_at?'<p class="os-help">Agendado para '+esc(when(order.scheduled_at))+'</p>':'')+
        '<div class="os-actions">'+statusBadge(order.status,badge)+' <strong>'+money(order.total_cents)+'</strong></div>'+
        payment+(pending?'<p class="os-help">Saldo a receber '+money(order.outstanding_cents??order.total_cents)+'</p>':'')+
        '<div class="os-actions">'+controls+'</div></article>';
    }

    function boardColumn(state,result) {
      return '<section class="os-delivery-board-lane" aria-labelledby="deliveryLane-'+state+'">'+
        '<h2 id="deliveryLane-'+state+'">'+esc(statusLabels[state])+'</h2>'+
        (result.items.length?result.items.map(orderCard).join(''):'<p class="os-help">Nenhum pedido.</p>')+
        (result.offset||result.has_more?'<div class="os-actions">'+
          button('Anterior','delivery-board-prev','os-outline os-small','left','data-order-lane="'+state+'" '+(result.offset?'':'disabled'))+
          button('Próxima','delivery-board-next','os-outline os-small','chevron','data-order-lane="'+state+'" '+(result.has_more?'':'disabled'))+
          '</div>':'')+'</section>';
    }

    async function load() {
      const sequence=++stamp;
      if (!list.firstChild) list.innerHTML=loading('Carregando pedidos…');
      list.setAttribute('aria-busy','true');
      try {
        const {search,state,start,end,offset}=filters;
        if(boardEnabled&&filters.orderView==='board'){
          const states=state==='all'?Object.keys(statusLabels):[state];
          const pages=await Promise.all(states.map(lane=>call('/orders?'+query({search,state:lane,start,end,mode:'all',offset:offsets[lane]||0}))));
          if(!current()||sequence!==stamp)return;
          lanes=Object.fromEntries(states.map((lane,i)=>[lane,pages[i]]));
          data={items:pages.flatMap(page=>page.items)};
          list.innerHTML='<div class="os-delivery-board">'+states.map(lane=>boardColumn(lane,lanes[lane])).join('')+'</div>';
          return;
        }
        const suffix=creditOnly?'/credit?'+query({search:filters.search,status:filters.state,offset:filters.offset}):
          '/orders?'+query({search,state,start,end,offset,mode:deliveryOnly?'delivery':'all',by_delivery_date:deliveryOnly||null});
        const response=await call(suffix);
        if (!current()||sequence!==stamp) return;
        data=response;
        let html=data.items.length?'<div class="os-cards-grid">'+data.items.map(orderCard).join('')+'</div>':
          '<div class="os-card">'+empty(creditOnly?'deliveryDeferred':deliveryOnly?'deliveryTruck':'deliveryReceipt',creditOnly?'Nenhuma venda a prazo encontrada':deliveryOnly?'Nenhuma entrega encontrada':'Nenhum pedido encontrado',
            creditOnly?'Confira os filtros de cliente e vencimento.':'Confira o filtro ou registre um novo pedido.')+'</div>';
        if(creditOnly) html='<div class="os-metrics">'+metric('A receber',money(data.summary.open_cents),'wallet')+
          metric('Em atraso',money(data.summary.overdue_cents),'clock')+'</div>'+html;
        if (data.offset>0||data.has_more) html+='<div class="os-pagination"><span>Página '+(data.offset/data.limit+1)+'</span><div class="os-actions">'+
          command(button,'Anterior','delivery-order-prev','left',data.offset?'':'disabled')+
          command(button,'Próxima','delivery-order-next','chevron',data.has_more?'':'disabled')+'</div></div>';
        list.innerHTML=html;
      } catch (error) {
        if (current()&&sequence===stamp) list.innerHTML=err(error)+command(button,'Tentar novamente','delivery-order-retry','refresh');
      } finally {
        if(current()&&sequence===stamp)list.removeAttribute('aria-busy');
      }
    }

    async function move(order, action) {
      const complete=action==='fulfill';
      const settle=action==='settle';
      const dispatch=action==='dispatch';
      const absent=action==='recipient_absent';
      const reschedule=action==='reschedule';
      const changeDate=absent||reschedule;
      const fullReturn=action==='return';
      const paidCancellation=action==='cancel'&&order.pix_state==='confirmed';
      const refunded=(fullReturn||paidCancellation)&&(order.collected_cents??(order.receipt_status==='paid'?order.total_cents:0))>0;
      const returning=(action==='cancel'||changeDate)&&order.status==='dispatched';
      const hasAmount=order.total_cents>0&&order.pix_state!=='confirmed';
      const methods=payments.filter(([key])=>key!=='pix'||canManagePolicy());
      if (complete && order.total_cents>=2) methods.push(['split','Pagamento misto']);
      if (complete && order.customer_id) methods.push(['on_account','A prazo']);
      const splitFields=complete&&order.total_cents>=2?'<fieldset hidden disabled data-delivery-split><legend>Valor recebido por forma de pagamento</legend>'+
        '<div class="os-form-grid">'+methods.filter(([key])=>key&&!['split','on_account'].includes(key)).map(([key,name])=>moneyField(name,'split_'+key,'text','')).join('')+
        '</div></fieldset>':'';
      let couriers=[];
      if(dispatch) {
        try {couriers=(await call('/logistics/couriers?active_only=true')).items||[];}
        catch {if(!current())return;}
        if(!current())return;
      }
      const courierFields=(couriers.length?selectField('Tipo de despacho','dispatch_kind',[['manual','Despacho manual'],['offer','Solicitar aceite pelo aplicativo']],'manual'):'')+(couriers.length?
        selectField('Entregador cadastrado','courier_id',[['','Digitar o nome'],...couriers.map(person=>[person.id,person.name])],''):'')+
        field('Entregador','courier_name','text','','required maxlength="120"');
      const deferred=complete&&order.customer_id?
        '<fieldset class="hidden" data-delivery-due hidden disabled>'+
        field('Vencimento','payment_due_date','date',dateKey(),'required min="'+dateKey()+'" max="2100-12-31"')+
        checkField('Confirmo que o valor ficará a receber','confirmed_credit')+'</fieldset>':'';
      const content='<p>Pedido '+esc(code(order))+' · '+money(order.total_cents)+'</p>'+
        ((dispatch||complete)?paymentInstructions(order,money):'')+
        ((complete&&hasAmount)||settle?selectField('Forma de pagamento','payment_method',methods,'','required')+
          (settle?moneyField('Valor recebido','received_cents','text',window.OrionFields.moneyDisplay(order.outstanding_cents??order.total_cents)):'')+
          splitFields+'<fieldset data-delivery-received>'+checkField('Confirmo que recebi o pagamento','received')+'</fieldset>'+deferred:
          complete?'<p>Confirme que o cliente recebeu os itens.</p>':
          dispatch?courierFields:
          changeDate?field('Nova data e hora','reschedule_at','datetime-local','',reschedule?'required':'')+
            '<p class="os-help">Horário da empresa</p>'+textField('Observação','note','','maxlength="300"')+
            (returning?checkField('Confirmo que os itens voltaram ao estoque','returned_to_stock'):''):
          fullReturn?checkField('Confirmo que todos os itens voltaram ao estoque','returned_to_stock')+
            (refunded?checkField('Confirmo que já devolvi o valor ao cliente','refunded'):''):
          returning?checkField('Confirmo que os itens voltaram ao estoque','returned_to_stock'):
          '<p>O estoque reservado será liberado.</p>')+(paidCancellation?checkField('Confirmo que já devolvi o Pix ao cliente','refunded'):'')+
          (containerUi?.confirmation(order.has_container_plan,action,ui)||'');
      const label=complete?'Concluir pedido':settle?'Registrar recebimento':dispatch?'Despachar pedido':
        fullReturn?'Registrar devolução':absent?'Registrar cliente ausente':reschedule?'Reagendar entrega':'Cancelar pedido';
      openEditor(label,content,async(form,values,key)=>{
        const credit=values.payment_method==='on_account';
        if ((complete&&hasAmount)||settle) {
          const checked=credit?form.elements.confirmed_credit.checked:form.elements.received.checked;
          if (!checked) throw Error(credit?'Confirme a venda a prazo.':'Confirme que recebeu o pagamento.');
        }
        if(settle){
          const amount_cents=window.OrionFields.parseMoney(values.received_cents,{required:true,min:1,max:order.outstanding_cents??order.total_cents});
          const receipt={amount_cents,payment_method:values.payment_method,expected_revision:order.revision,received:true};
          const saved=await call('/orders/'+encodeURIComponent(order.id)+'/payment-receipts','POST',{...receipt,idempotency_key:key(receipt)});
          if(saved?.status!=='fulfilled')throw Error('Confira o saldo antes de repetir.');
          return;
        }
        const payload={action,expected_revision:order.revision,payment_method:values.payment_method||null};
        if (values.payment_method==='split') {
          payload.payment_method=null;
          payload.tenders=payments.filter(([method])=>method).map(([method])=>({method,
            amount_cents:window.OrionFields.parseMoney(values['split_'+method],{required:false,min:0,max:order.total_cents})||0
          })).filter(t=>t.amount_cents>0);
          if(payload.tenders.length<2)throw Error('Informe os valores de pelo menos duas formas de pagamento.');
          if(payload.tenders.reduce((sum,t)=>sum+t.amount_cents,0)!==order.total_cents)throw Error('A soma dos pagamentos deve corresponder ao total do pedido.');
        }
        if(containerUi?.confirmed(form))payload.containers_confirmed=true;
        if (credit) payload.payment_due_date=values.payment_due_date;
        if (dispatch) {
          if(values.dispatch_kind==='offer'){
            if(!values.courier_id)throw Error('Selecione o entregador que receberá a solicitação.');
            payload.action='offer';
          }
          if(values.courier_id)payload.courier_id=values.courier_id;
          else payload.courier_name=values.courier_name;
        }
        if(changeDate){
          const [day,time]=(values.reschedule_at||'').split('T');
          payload.scheduled_at=day?localISO(day,time):null;
          payload.note=values.note?.trim()||null;
        }
        if (returning||fullReturn) {
          if (!form.elements.returned_to_stock.checked) throw Error('Confirme a devolução antes de liberar o estoque.');
          payload.returned_to_stock=true;
        }
        if (refunded) {
          if (!form.elements.refunded.checked) throw Error('Confirme que devolveu o valor antes de registrar o estorno.');
          payload.refunded=true;
        }
        const saved=await call('/orders/'+encodeURIComponent(order.id)+'/transitions','POST',{...payload,idempotency_key:key(payload)});
        if (saved?.status!==((complete||settle)?'fulfilled':payload.action==='offer'?'confirmed':dispatch?'dispatched':changeDate?'confirmed':'cancelled')) throw Error('Confira o estado do pedido antes de repetir.');
      },complete?'Pedido concluído.':settle?'Recebimento registrado.':dispatch?'Despacho atualizado.':
        fullReturn?'Devolução registrada.':absent?'A entrega voltou para a fila.':reschedule?'Entrega reagendada.':'Pedido cancelado.',{label,bind:dialog=>{
        const form=dialog.querySelector('form');
        form.elements.courier_id?.addEventListener('change',()=>{
          form.elements.courier_name.disabled=Boolean(form.elements.courier_id.value);
        });
        const due=dialog.querySelector('[data-delivery-due]');
        const split=dialog.querySelector('[data-delivery-split]');
        if (!due&&!split) return;
        const received=dialog.querySelector('[data-delivery-received]');
        form.elements.payment_method.addEventListener('change',()=>{
          const credit=form.elements.payment_method.value==='on_account';
          if(due){due.hidden=due.disabled=!credit;due.classList.toggle('hidden',!credit);}
          if(split)split.hidden=split.disabled=form.elements.payment_method.value!=='split';
          received.hidden=received.disabled=credit;
          received.classList.toggle('hidden',credit);
          form.elements.received.checked=false;
          if(form.elements.confirmed_credit)form.elements.confirmed_credit.checked=false;
        });
      }});
    }

    host.addEventListener('change',event=>{
      if(boardEnabled&&event.target.name==='orderView'&&current()){
        filters.orderView=event.target.value;
        void load();
      }
    });
    host.addEventListener('submit',event=>{
      if (event.target.id!=='osDeliveryOrderFilters') return;
      event.preventDefault();
      for(const lane of Object.keys(offsets))delete offsets[lane];
      Object.assign(filters,Object.fromEntries(new FormData(event.target)),{offset:0});
      void load();
    });
    host.addEventListener('click',async event=>{
      const el=event.target.closest('button'), action=el?.dataset.osAction;
      if (!action?.startsWith('delivery-')||!current()) return;
      event.stopPropagation();
      if (action==='delivery-order-retry') {await load();return;}
      if(action==='delivery-board-prev'||action==='delivery-board-next'){
        const key=el.dataset.orderLane,lane=lanes[key];
        if(!lane||list.getAttribute('aria-busy')==='true')return;
        offsets[key]=Math.max(0,lane.offset+(action==='delivery-board-next'?lane.limit:-lane.limit));
        await load();return;
      }
      if (action==='delivery-order-prev'||action==='delivery-order-next') {
        filters.offset=Math.max(0,data.offset+(action==='delivery-order-next'?data.limit:-data.limit));
        await load();return;
      }
      if (action==='delivery-new-order'&&canWrite()) {
        openOrderEditor({call,current,ui,openEditor,deliveryOnly,onCreated:()=>{filters.offset=0;}});
        return;
      }
      const order=data?.items?.find(item=>item.id===el.dataset.deliveryOrder);
      if (!order) return;
      if (action==='delivery-order-detail') {
        await showOrderDetail({id:order.id,call,current,openDetails,ui});
      } else if(action==='delivery-order-pix'&&canManagePolicy()&&order.pix_state==='pending'){
        openEditor('Conferir Pix','<p>Pedido '+esc(code(order))+' · '+money(order.total_cents)+'</p>'+
          checkField('Conferi no extrato que o valor integral entrou na conta da distribuidora','pix_received'),
          async (form,values,key)=>{
            if(!form.elements.pix_received.checked)throw Error('Confira o extrato antes de registrar o Pix.');
            const payload={expected_revision:order.revision,received:true};
            await call('/orders/'+encodeURIComponent(order.id)+'/pix/confirm','POST',{...payload,idempotency_key:key(payload)});
          },'Pix registrado no Financeiro.',{label:'Confirmar recebimento'});
      } else if(action==='delivery-order-withdraw'&&canWrite()){
        openEditor('Retirar solicitação','<p>O entregador deixará de poder aceitar este pedido.</p>',async(form,values,key)=>{
          const payload={action:'withdraw_offer',expected_revision:order.revision};
          await call('/orders/'+encodeURIComponent(order.id)+'/transitions','POST',{...payload,idempotency_key:key(payload)});
        },'Solicitação retirada.',{label:'Retirar solicitação'});
      } else if(action==='delivery-order-repeat'&&canWrite()){
        el.disabled=true;
        try {
          const original=await call('/orders/'+encodeURIComponent(order.id));
          if(current()&&el.isConnected)openOrderEditor({call,current,ui,openEditor,deliveryOnly,repeat:original,onCreated:()=>{filters.offset=0;}});
        } catch(error) {if(current())openDetails('Repetir pedido',err(error));}
        finally {el.disabled=false;}
      } else if (action==='delivery-order-return'&&canWrite()&&order.status==='fulfilled') {
        move(order,'return');
      } else if (action==='delivery-order-settle'&&canWrite()&&order.receipt_status==='pending') {
        move(order,'settle');
      } else if (canWrite()&&['confirmed','dispatched'].includes(order.status)) {
        const transition={'delivery-order-fulfill':'fulfill','delivery-order-dispatch':'dispatch','delivery-order-cancel':'cancel',
          'delivery-order-reschedule':'reschedule','delivery-order-absent':'recipient_absent'}[action];
        if (transition) move(order,transition);
      }
    });
    await load();
  }
  window.OrionDeliveryOrders=Object.freeze({mount,salesReport,openOrderEditor,showOrderDetail,code,modeLabel,statusBadge});
})();
