/* Delivery areas, appointment windows and courier registration. */
(function () {
  'use strict';

  const modes=[['manual','Taxa manual'],['areas','Taxa por bairro']];
  const kinds=[['included','Atendido'],['excluded','Excluído']];
  const days=['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo'];
  const weekdays=days.map((name,index)=>[index,name]);
  const currencyLimit=1000000000;

  async function mount({host,filters,call,allowed,canWrite,canManagePolicy=canWrite,ui,openEditor}) {
    if (!host||!allowed()) return;
    const {esc,loading,button,title,field,moneyField,selectField,checkField,empty,money,badge,query,brazilianStates}=ui;
    const current=()=>allowed()&&host.isConnected;
    const command=(label,action,icon,attrs='')=>button(label,action,'os-outline os-small',icon,attrs);
    const error=message=>'<div class="os-notice os-error" role="alert"><p>'+esc(message)+'</p></div>';
    const states=brazilianStates.map(state=>[state,state]);
    const nameAttr=(kind,id)=>'data-os-logistics-'+kind+'="'+esc(id)+'"';
    const number=(value,min,max)=>window.OrionFields.parseDecimal(value,{required:true,scale:0,min,max});
    let policy,couriers,version=0;
    filters.search ??= '';
    filters.offset ??= 0;
    filters.active_only ??= false;
    host.innerHTML=title('Logística','Configure cobertura, agendamentos e entregadores.')+
      '<div id="osDeliveryLogisticsSections" aria-live="polite"></div>';
    const output=host.querySelector('#osDeliveryLogisticsSections');

    function table(columns,rows) {
      return '<div class="os-table-scroll"><table class="os-table"><thead><tr>'+
        columns.map(label=>'<th scope="col">'+esc(label)+'</th>').join('')+'</tr></thead><tbody>'+
        rows.map(cells=>'<tr>'+cells.map((cell,index)=>'<td data-label="'+esc(columns[index])+'">'+cell+'</td>').join('')+'</tr>').join('')+
        '</tbody></table></div>';
    }

    function writableActions(options, permitted=canWrite) {
      return permitted()?'<div class="os-actions">'+options.map(args=>command(...args)).join('')+'</div>':'';
    }

    function coverage() {
      const rows=policy.areas.map((area,index)=>{
        const key=nameAttr('area',index);
        const included=area.kind==='included';
        const values=[esc(area.neighborhood),esc(area.city+' / '+area.state),badge(included?'active':'inactive',included?'Atendido':'Excluído'),
          included?esc(money(area.fee_cents)):'—'];
        if (canManagePolicy()) values.push(writableActions([['Editar','logistics-edit-area','edit',key],['Remover','logistics-remove-area','trash',key]],canManagePolicy));
        return values;
      });
      const columns=['Bairro','Cidade / UF','Cobertura','Taxa de entrega'];
      if (canManagePolicy()) columns.push('Ações');
      return '<section class="os-card os-section-gap"><div class="os-card-header"><div><h2>Cobertura e taxas</h2>'+
        '<p>'+esc(policy.coverage_mode==='areas'?'Taxa calculada pelo bairro.':'Taxa informada pelo operador.')+'</p></div>'+
        writableActions([['Configurar cobertura','logistics-coverage','settings'],['Novo bairro','logistics-new-area','plus']],canManagePolicy)+
        '</div>'+(rows.length?table(columns,rows):empty('map','Nenhum bairro cadastrado','Cadastre os bairros que a empresa atende.'))+'</section>';
    }

    function scheduling() {
      const rows=policy.windows.map((window,index)=>{
        const key=nameAttr('window',index);
        const values=[esc(days[window.weekday]),esc(window.start),esc(window.end)];
        if (canManagePolicy()) values.push(writableActions([['Editar','logistics-edit-window','edit',key],['Remover','logistics-remove-window','trash',key]],canManagePolicy));
        return values;
      });
      const columns=['Dia','Início','Fim'];
      if (canManagePolicy()) columns.push('Ações');
      const description=(policy.scheduling_enabled?'Agendamentos ativos. ':'Agendamentos desativados. ')+
        'Antecedência de '+policy.lead_minutes+' min, até '+policy.horizon_days+
        ' dia(s). Fuso '+policy.timezone+'.';
      return '<section class="os-card os-section-gap"><div class="os-card-header"><div><h2>Agendamento de entrega</h2>'+
        '<p>'+esc(description)+'</p></div>'+
        writableActions([['Configurar agendamento','logistics-schedule','settings'],['Nova janela','logistics-new-window','plus']],canManagePolicy)+
        '</div>'+(rows.length?table(columns,rows):empty('calendar','Nenhum horário cadastrado','Adicione horários para receber entregas agendadas.'))+'</section>';
    }

    function courierList() {
      const rows=couriers.items.map(courier=>{
        const values=[esc(courier.name),courier.phone?esc(window.OrionFields.phoneDisplay(courier.phone)):'—',
          badge(courier.active?'active':'inactive',courier.active?'Ativo':'Inativo')];
        if (canWrite()) values.push(writableActions([['Editar','logistics-edit-courier','edit',nameAttr('courier',courier.id)]]));
        return values;
      });
      const columns=['Nome','Telefone','Situação'];
      if (canWrite()) columns.push('Ações');
      return '<section class="os-card os-section-gap"><div class="os-card-header"><h2>Entregadores</h2>'+
        writableActions([['Novo entregador','logistics-new-courier','plus']])+'</div>'+
        '<form id="osDeliveryCouriersFilter" class="os-filter-row">'+field('Buscar','logistics_courier_search','search',filters.search,'maxlength="120"')+
        selectField('Situação','logistics_courier_status',[['all','Todos'],['active','Ativos']],filters.active_only?'active':'all')+
        '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
        (rows.length?table(columns,rows):empty('users','Nenhum entregador encontrado','Confira os filtros ou cadastre um entregador.'))+
        ((couriers.offset||couriers.has_more)?'<div class="os-pagination"><span>Página '+(couriers.offset/couriers.limit+1)+'</span>'+
          '<div class="os-actions">'+command('Anterior','logistics-courier-prev','left',couriers.offset?'':'disabled')+
          command('Próxima','logistics-courier-next','chevron',couriers.has_more?'':'disabled')+'</div></div>':'')+'</section>';
    }

    async function read() {
      const ticket=++version;
      output.innerHTML=loading('Carregando logística…');
      try {
        const responses=await Promise.all([call('/logistics'),call('/logistics/couriers?'+query(filters))]);
        if (!current()||ticket!==version) return;
        [policy,couriers]=responses;
        output.innerHTML=coverage()+scheduling()+courierList();
        window.OrionFields.refresh(output);
      } catch (ex) {
        if (current()&&ticket===version) {
          output.innerHTML=error(ex.message)+command('Tentar novamente','logistics-retry','refresh');
        }
      }
    }

    async function savePolicy(changes) {
      const {revision,timezone,...settings}=policy;
      const saved=await call('/logistics','PUT',{...settings,...changes,expected_revision:revision});
      if (!saved?.revision) throw Error('Confira a configuração antes de repetir.');
    }

    function editCoverage() {
      const content=selectField('Cálculo da taxa','logistics_mode',modes,policy.coverage_mode)+
        '<p class="os-help">Com a taxa por bairro, pedidos com entrega só podem ser confirmados para bairros atendidos.</p>';
      openEditor('Configurar cobertura',content,async(form,values)=>{
        await savePolicy({coverage_mode:values.logistics_mode});
      },'Cobertura salva.');
    }

    function editArea(index) {
      const area=policy.areas[index];
      const content='<div class="os-form-grid">'+
        field('Cidade','logistics_city','text',area?.city||'','required maxlength="120"')+
        selectField('UF','logistics_state',[['','Selecione'],...states],area?.state||'','required')+
        field('Bairro','logistics_neighborhood','text',area?.neighborhood||'','required maxlength="120"')+
        selectField('Cobertura','logistics_kind',kinds,area?.kind||'included')+
        moneyField('Taxa de entrega','logistics_fee','text',window.OrionFields.moneyDisplay(area?.fee_cents??0))+'</div>';
      openEditor(area?'Editar bairro':'Novo bairro',content,async(form,values)=>{
        const kind=values.logistics_kind;
        const data={city:values.logistics_city, state:values.logistics_state, neighborhood:values.logistics_neighborhood,
          kind, fee_cents:kind==='excluded'?0:window.OrionFields.parseMoney(values.logistics_fee,{required:true,min:0,max:currencyLimit})};
        const areas=[...policy.areas];
        if (area) areas[index]=data;
        else areas.push(data);
        await savePolicy({areas});
      },'Bairro salvo.',{bind:dialog=>{
        const form=dialog.querySelector('form'), toggle=()=>{
          const hidden=form.elements.logistics_kind.value==='excluded';
          const fee=form.elements.logistics_fee;
          fee.closest('[data-field-wrapper]').hidden=hidden;
          fee.disabled=hidden;
        };
        form.elements.logistics_kind.addEventListener('change',toggle);
        toggle();
      }});
    }

    function editSchedule() {
      const content=checkField('Aceitar agendamentos','logistics_schedule_enabled',policy.scheduling_enabled)+
        '<div class="os-form-grid">'+
        field('Antecedência mínima em minutos','logistics_lead','number',policy.lead_minutes,'required min="0" max="1440" step="1"')+
        field('Limite em dias','logistics_horizon','number',policy.horizon_days,'required min="1" max="90" step="1"')+'</div>';
      openEditor('Configurar agendamento',content,async(form,values)=>{
        await savePolicy({scheduling_enabled:form.elements.logistics_schedule_enabled.checked,
          lead_minutes:number(values.logistics_lead,0,1440),horizon_days:number(values.logistics_horizon,1,90)});
      },'Agendamento salvo.');
    }

    function editWindow(index) {
      const existing=policy.windows[index];
      const content='<div class="os-form-grid">'+selectField('Dia','logistics_weekday',weekdays,existing?.weekday??0)+
        field('Início','logistics_start','time',existing?.start||'09:00','required step="60"')+
        field('Fim','logistics_end','text',existing?.end||'18:00','required pattern="(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00" maxlength="5" placeholder="HH:MM"')+
        '</div><p class="os-help">Para encerrar à meia-noite, use 24:00 no fim da janela.</p>';
      openEditor(existing?'Editar janela':'Nova janela',content,async(form,values)=>{
        const data={weekday:Number(values.logistics_weekday),start:values.logistics_start,end:values.logistics_end};
        const windows=[...policy.windows];
        if (existing) windows[index]=data;
        else windows.push(data);
        await savePolicy({windows});
      },'Janela salva.');
    }

    function remove(index,kind,label) {
      openEditor('Remover '+label,'<p>Confirme a remoção de '+esc(kind==='areas'?policy.areas[index].neighborhood:
        days[policy.windows[index].weekday]+' '+policy.windows[index].start+'–'+policy.windows[index].end)+'.</p>',async()=>{
        await savePolicy({[kind]:policy[kind].filter((_,position)=>position!==index)});
      },'Remoção concluída.',{label:'Remover'});
    }

    function editCourier(courier) {
      const content='<div class="os-form-grid">'+field('Nome','logistics_courier_name','text',courier?.name||'','required maxlength="120"')+
        field('Telefone','logistics_courier_phone','tel',courier?.phone||'','autocomplete="tel" maxlength="40"')+'</div>'+
        checkField('Entregador ativo','logistics_courier_active',courier?.active??true);
      openEditor(courier?'Editar entregador':'Novo entregador',content,async(form,values,key)=>{
        const data={name:values.logistics_courier_name,phone:values.logistics_courier_phone||null,
          active:form.elements.logistics_courier_active.checked};
        const saved=courier?await call('/logistics/couriers/'+encodeURIComponent(courier.id),'PUT',
          {...data,expected_revision:courier.revision}):await call('/logistics/couriers','POST',{...data,idempotency_key:key(data)});
        if (!saved?.id) throw Error('Confira o cadastro antes de repetir.');
        if (!courier) Object.assign(filters,{search:'',offset:0,active_only:false});
      },'Entregador salvo.');
    }

    host.addEventListener('submit',event=>{
      if (event.target.id!=='osDeliveryCouriersFilter'||!current()) return;
      event.preventDefault();
      const names=event.target.elements;
      Object.assign(filters,{search:names.logistics_courier_search.value.trim(),offset:0,active_only:names.logistics_courier_status.value==='active'});
      void read();
    });
    host.addEventListener('click',event=>{
      const node=event.target.closest('button'),action=node?.dataset.osAction;
      if (!action?.startsWith('logistics-')||!current()) return;
      event.stopPropagation();
      if (action==='logistics-retry') {void read();return;}
      if (action==='logistics-courier-prev'||action==='logistics-courier-next') {
        filters.offset=Math.max(0,couriers.offset+(action==='logistics-courier-next'?couriers.limit:-couriers.limit));
        void read();return;
      }
      const courierAction=action==='logistics-new-courier'||action==='logistics-edit-courier';
      if (!(courierAction?canWrite():canManagePolicy())) return;
      const area=Number(node.dataset.osLogisticsArea),windowIndex=Number(node.dataset.osLogisticsWindow);
      switch (action) {
        case 'logistics-coverage':editCoverage();break;
        case 'logistics-new-area':editArea();break;
        case 'logistics-edit-area':editArea(area);break;
        case 'logistics-remove-area':remove(area,'areas','bairro');break;
        case 'logistics-schedule':editSchedule();break;
        case 'logistics-new-window':editWindow();break;
        case 'logistics-edit-window':editWindow(windowIndex);break;
        case 'logistics-remove-window':remove(windowIndex,'windows','janela');break;
        case 'logistics-new-courier':editCourier();break;
        case 'logistics-edit-courier':editCourier(couriers.items.find(row=>row.id===node.dataset.osLogisticsCourier));break;
      }
    });
    await read();
  }

  window.OrionDeliveryLogistics=Object.freeze({mount});
})();
