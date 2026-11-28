/* Warehouse empties and packaging with customers. */
(function () {
  'use strict';

  const kinds=[['cylinder','Botijão'],['jug','Garrafão']];
  const changes=[['in','Entrada'],['out','Saída'],['count','Contagem'],['lend','Empréstimo']];
  const labels=Object.fromEntries([...kinds,...changes,['return','Devolução'],['initial','Saldo inicial']]);
  const cap=10000000;

  async function mount({host,filters,call,allowed,canWrite,ui,openEditor}) {
    const {esc,loading,button,title,field,selectField,textField,empty,fmtDate,dateKey,timeLabel,query,icon}=ui;
    if (!host||!allowed()) return;
    filters.kind ??= 'all';
    filters.search ??= '';
    filters.offset ??= 0;
    let view='list', selected=null, detailOffset=0, result, readVersion=0;
    const current=()=>allowed()&&host.isConnected;
    const cmd=(label,action,icon,extra='')=>button(label,action,'os-outline os-small',icon,extra);
    const itemId=id=>'data-os-container-id="'+esc(id)+'"';
    const error=message=>'<div class="os-notice os-error" role="alert"><p>'+esc(message)+'</p></div>';
    const quantity=(value,min=0)=>window.OrionFields.parseDecimal(value,{required:true,scale:0,min,max:cap});
    const when=value=>fmtDate(dateKey(new Date(value)))+' '+timeLabel(value);
    host.innerHTML=title('Vasilhames','Controle de vasilhames e empréstimos',
      canWrite()?button('Novo vasilhame','container-add','','plus'):'')+
      '<form id="osContainerFilters" class="os-filter-row">'+
      field('Buscar','container_search','search',filters.search,'maxlength="120" placeholder="Nome do vasilhame"')+
      selectField('Tipo','container_filter',[['all','Todos'],...kinds],filters.kind)+
      '<button type="submit" class="os-button os-outline">Filtrar</button></form>'+
      '<div id="osContainerResults" aria-live="polite"></div>';
    window.OrionFields.refresh(host);
    const output=host.querySelector('#osContainerResults'), filterForm=host.querySelector('#osContainerFilters');
    const heading=(name,controls)=>'<div class="os-card-header"><h2>'+esc(name)+'</h2>'+controls+'</div>';

    function card(item) {
      const id=itemId(item.id);
      return '<article class="os-card os-item-card od-vessel-card"><div class="od-order-identity"><span class="os-square-icon">'+icon('deliveryVessel')+'</span><div><h3>'+esc(item.name)+'</h3>'+
        '<p class="os-help">'+esc(labels[item.kind])+'</p></div></div>'+
        '<div class="os-delivery-stock-grid od-vessel-stock"><div><small>Vazios em estoque</small><strong>'+esc(item.empty_quantity)+'</strong></div>'+
        '<div><small>Com clientes</small><strong>'+esc(item.loaned)+'</strong></div></div>'+
        '<div class="os-actions">'+(canWrite()?cmd('Movimentar','container-move','plus',id):'')+
        cmd('Com clientes','container-loans','users',id)+cmd('Histórico','container-history','list',id)+'</div></article>';
    }

    function detailRows(items) {
      if (view==='loans') return items.map(row=>'<div class="os-summary-row"><span>'+esc(row.customer_name)+
        '</span><div class="os-actions"><strong>'+row.quantity+' un</strong>'+
        (canWrite()?cmd('Devolver','container-return','check',itemId(row.customer_id)):'')+'</div></div>').join('');
      const columns=['Data','Movimento','Quantidade','Saldo anterior','Saldo final','Cliente','Observação'];
      return '<div class="os-table-scroll" tabindex="0" role="region" aria-label="Tabela de dados"><table class="os-table"><thead><tr>'+columns.map(c=>'<th>'+c+'</th>').join('')+
        '</tr></thead><tbody>'+items.map(row=>'<tr>'+[
          when(row.occurred_at),labels[row.kind],row.quantity,row.balance_before,row.balance_after,row.customer_name||'—',row.note||'—',
        ].map((v,i)=>'<td data-label="'+columns[i]+'">'+esc(v)+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
    }

    async function read() {
      const ticket=++readVersion;
      output.innerHTML=loading('Carregando vasilhames…');
      filterForm.hidden=view!=='list';
      filterForm.classList.toggle('hidden',view!=='list');
      const offset=view==='list'?filters.offset:detailOffset;
      try {
        const response=await call(view==='list'?'/containers?'+query(filters):
          '/containers/'+encodeURIComponent(selected.id)+'/'+(view==='history'?'movements':'loans')+'?'+query({offset}));
        if (!current()||ticket!==readVersion) return;
        result=response;
        let html;
        if (view==='list') {
          html=result.items.length?'<div class="os-cards-grid">'+result.items.map(card).join('')+'</div>':
            '<div class="os-card">'+empty('deliveryVessel','Nenhum vasilhame encontrado','Confira os filtros ou cadastre um tipo de vasilhame.')+'</div>';
        } else {
          const label=view==='history'?'Histórico':'Com clientes';
          html='<section class="os-card">'+heading(selected.name+' · '+label,cmd('Voltar','container-back','left'))+
            (result.items.length?detailRows(result.items):'<p class="os-help">Nenhum registro encontrado.</p>')+'</section>';
        }
        if (offset>0||result.has_more) html+='<div class="os-pagination"><span>Página '+(offset/result.limit+1)+'</span><div class="os-actions">'+
          cmd('Anterior','container-prev','left',offset?'':'disabled')+
          cmd('Próxima','container-next','chevron',result.has_more?'':'disabled')+'</div></div>';
        output.innerHTML=html;
      } catch (ex) {
        if (current()&&ticket===readVersion) output.innerHTML=error(ex.message)+
          cmd('Tentar novamente','container-retry','refresh')+(selected?cmd('Voltar','container-back','left'):'');
      }
    }

    function create() {
      const content=selectField('Tipo','kind',kinds,'cylinder')+
        field('Nome do vasilhame','name','text','','maxlength="120" required')+
        field('Saldo inicial de vazios','quantity','number',0,'required min="0" max="'+cap+'" step="1"');
      openEditor('Novo vasilhame',content,async(form,values,key)=>{
        const data={kind:values.kind,name:values.name,quantity:quantity(values.quantity)};
        const saved=await call('/containers','POST',{...data,idempotency_key:key(data)});
        if (!saved?.id) throw Error('Confira o cadastro antes de repetir.');
        Object.assign(filters,{kind:'all',search:'',offset:0});
      },'Vasilhame cadastrado.');
    }

    function adjust(item, loan=null) {
      let customer=loan?{id:loan.customer_id,name:loan.customer_name}:null;
      const customerSearch='<div data-container-customer-search hidden class="hidden">'+
        field('Buscar cliente','container_customer','search','','maxlength="120" placeholder="Nome ou telefone"')+
        cmd('Buscar clientes','container-find','users')+'<div class="os-actions" data-container-candidates aria-live="polite"></div>'+
        '<p data-container-customer-selected class="os-help" aria-live="polite"></p></div>';
      const content='<p class="os-help">'+esc(item.name)+(loan?' · '+esc(loan.customer_name):'')+'</p>'+
        (loan?'':selectField('Movimento','kind',changes,'in')+customerSearch)+
        field('Quantidade','quantity','number',loan?.quantity??'','required min="0" max="'+(loan?.quantity??cap)+'" step="1"')+
        textField('Observação','note','','maxlength="300"');
      openEditor(loan?'Devolver vasilhames':'Movimentar vasilhame',content,async(form,values,key)=>{
        const kind=loan?'return':values.kind;
        if (kind==='lend'&&!customer) throw Error('Selecione um cliente para registrar o empréstimo.');
        const data={kind,quantity:quantity(values.quantity,kind==='count'?0:1),
          customer_id:['lend','return'].includes(kind)?customer?.id:null,note:values.note.trim()||null,expected_revision:item.revision};
        const saved=await call('/containers/'+encodeURIComponent(item.id)+'/movements','POST',{...data,idempotency_key:key(data)});
        if (saved?.id!==item.id) throw Error('Confira a movimentação antes de repetir.');
      },'Movimentação registrada.',{bind:dialog=>{
        if (loan) return;
        const form=dialog.querySelector('form'), searchBox=form.querySelector('[data-container-customer-search]');
        const options=form.querySelector('[data-container-candidates]'), info=form.querySelector('[data-container-customer-selected]');
        let candidates=[],version=0;
        form.addEventListener('change',event=>{
          if (event.target.name!=='kind') return;
          searchBox.hidden=form.elements.kind.value!=='lend';
          searchBox.classList.toggle('hidden',searchBox.hidden);
          form.elements.quantity.min=form.elements.kind.value==='count'?'0':'1';
        });
        form.addEventListener('click',async event=>{
          const node=event.target.closest('button'), action=node?.dataset.osAction;
          if (!['container-pick','container-find'].includes(action)||!current()) return;
          event.stopPropagation();
          if (action==='container-pick') {
            customer=candidates.find(item=>item.id===node.dataset.osContainerId)||null;
            info.textContent=customer?'Cliente: '+customer.name:'';
            options.replaceChildren();
            return;
          }
          const term=form.elements.container_customer.value.trim();
          const ticket=++version;
          if (term.length<2) {options.innerHTML=error('Digite ao menos dois caracteres.');return;}
          options.innerHTML=loading('Buscando…');
          const latest=()=>current()&&dialog.open&&form.isConnected&&ticket===version;
          try {
            const response=await call('/order-customers?'+query({search:term}));
            if (!latest()) return;
            candidates=response.items;
            options.innerHTML=candidates.length?candidates.map(c=>cmd(
              [c.name,c.whatsapp_e164].filter(Boolean).join(' · '),'container-pick','user',itemId(c.id))).join(''):
              '<p class="os-help">Nenhum cliente encontrado.</p>';
          } catch (ex) {if (latest()) options.innerHTML=error(ex.message);}
        });
      }});
    }

    filterForm.addEventListener('submit',event=>{
      event.preventDefault();
      Object.assign(filters,{search:filterForm.elements.container_search.value,kind:filterForm.elements.container_filter.value,offset:0});
      void read();
    });
    host.addEventListener('click',event=>{
      const node=event.target.closest('button'), action=node?.dataset.osAction;
      if (!action?.startsWith('container-')||!current()) return;
      event.stopPropagation();
      if (action==='container-retry') {void read();return;}
      if (action==='container-back') {view='list';selected=null;void read();return;}
      if (action==='container-add'&&canWrite()) {create();return;}
      if (action==='container-next'||action==='container-prev') {
        const offset=Math.max(0,result.offset+(action==='container-next'?result.limit:-result.limit));
        if (view==='list') filters.offset=offset;
        else detailOffset=offset;
        void read();return;
      }
      const item=result?.items.find(row=>(row.id||row.customer_id)===node.dataset.osContainerId);
      if (!item) return;
      if (action==='container-return'&&selected&&canWrite()) {adjust(selected,item);return;}
      if (action==='container-move'&&canWrite()) {adjust(item);return;}
      if (action==='container-history'||action==='container-loans') {
        selected=item;
        view=action==='container-history'?'history':'loans';
        detailOffset=0;
        void read();
      }
    });
    await read();
  }
  window.OrionDeliveryContainers=Object.freeze({mount});
})();
