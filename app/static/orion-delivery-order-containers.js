/* Physical packaging in the Delivery order editor. */
(function () {
  'use strict';

  const fields = () => '<details class="os-card hidden" data-container-order hidden>'+
    '<summary>Vasilhames</summary><div class="os-form" data-container-order-lines aria-live="polite"></div></details>';

  function bind(form, {call,ui,current}) {
    const group=form.querySelector('[data-container-order]');
    const lines=group.querySelector('[data-container-order-lines]');
    const draft=new Map();
    let cart=new Map(), choices=null, loading=false;

    function draw() {
      group.hidden=!cart.size;
      group.classList.toggle('hidden',group.hidden);
      if (!choices||!group.open||group.hidden) return;
      if (!choices.length) {
        lines.innerHTML='<p class="os-help">Cadastre os tipos primeiro em Vasilhames.</p>';
        return;
      }
      const options=[['','Nenhum'],...choices.map(row=>[row.id,row.name])];
      lines.innerHTML=[...cart.entries()].map(([id,item])=>{
        if (item.exchange_container_id) {
          return '<p class="os-help">'+ui.esc(item.label+' · Troca obrigatória de um '+(item.exchange_container_name||'vasilhame')+
            ' por unidade. A equipe confirma os vazios na conclusão.')+'</p>';
        }
        const saved=draft.get(id)||{};
        return '<fieldset><legend>'+ui.esc(item.label)+'</legend><div class="os-form-grid">'+
          ui.selectField('Tipo de vasilhame','container-order-id-'+id,options,saved.container_id||'', 'data-container-input="type"')+
          ui.field('Vazios recebidos na troca','container-order-exchange-'+id,'number',saved.exchange_quantity||0,
            'min="0" max="1000" step="1" data-container-input="exchange"')+
          ui.field('Vasilhames em comodato','container-order-loan-'+id,'number',saved.loan_quantity||0,
            'min="0" max="1000" step="1" data-container-input="loan"')+
          '</div></fieldset>';
      }).join('');
      window.OrionFields.refresh(lines);
    }

    async function load() {
      if (choices||loading||!group.open||!cart.size) return;
      loading=true;
      lines.innerHTML=ui.loading('Carregando vasilhames…');
      try {
        const list=[];
        let offset=0;
        for (;;) {
          const result=await call('/containers?offset='+offset);
          if (!current()) return;
          list.push(...result.items);
          if (!result.has_more) break;
          offset+=result.limit;
        }
        choices=list;
        draw();
      } catch (error) {
        if (current()) lines.innerHTML='<p class="os-help" role="alert">'+
          ui.esc(error.message||'Feche e abra Vasilhames para tentar novamente.')+'</p>';
      } finally {loading=false;}
    }

    function readRow(id, kind) {
      return form.elements['container-order-'+kind+'-'+id]?.value||'';
    }

    function keepDraft() {
      for (const id of cart.keys()) {
        if (!form.elements['container-order-id-'+id]) continue;
        draft.set(id,{
          container_id:readRow(id,'id'),
          exchange_quantity:readRow(id,'exchange'),
          loan_quantity:readRow(id,'loan'),
        });
      }
    }

    group.addEventListener('toggle',()=>{
      if (group.open) {
        draw();
        void load();
      }
    });
    lines.addEventListener('input',keepDraft);
    lines.addEventListener('change',keepDraft);

    return {
      render(items) {
        keepDraft();
        cart=items;
        for (const id of draft.keys()) if (!cart.has(id)) draft.delete(id);
        draw();
        void load();
      },
      collect(items, customerId) {
        keepDraft();
        const plan=[];
        for (const item of items) {
          const row=draft.get(item.variant_id);
          if (!row) continue;
          const exchange=window.OrionFields.parseDecimal(row.exchange_quantity,{required:false,scale:0,min:0,max:item.quantity})||0;
          const loan=window.OrionFields.parseDecimal(row.loan_quantity,{required:false,scale:0,min:0,max:item.quantity})||0;
          if (exchange+loan>item.quantity) throw Error('Some a troca e o comodato dentro da quantidade do produto.');
          if (!exchange&&!loan) {
            if (row.container_id) throw Error('Informe quantos vasilhames serão trocados ou emprestados.');
            continue;
          }
          if (!row.container_id) throw Error('Selecione o tipo de vasilhame para a troca ou o comodato.');
          if (loan&&!customerId) throw Error('Selecione um cliente para registrar o comodato.');
          plan.push({variant_id:item.variant_id,container_id:row.container_id,exchange_quantity:exchange,loan_quantity:loan});
        }
        return plan;
      },
    };
  }

  function confirmation(hasPlan, action, ui) {
    if (!hasPlan||!['fulfill','return'].includes(action)) return '';
    const label=action==='return'
      ?'Confirmei a devolução dos vasilhames do pedido'
      :'Confirmei a troca ou o comodato dos vasilhames do pedido';
    return ui.checkField(label,'containers_confirmed');
  }

  function confirmed(form) {
    const control=form.elements.containers_confirmed;
    if (control&&!control.checked) throw Error('Confirme a movimentação física dos vasilhames.');
    return Boolean(control?.checked);
  }

  function summary(plan,ui) {
    if (!plan?.length) return '';
    return '<h3>Vasilhames</h3><ul>'+plan.map(row=>'<li>'+ui.esc(
      [row.container_name,row.exchange_quantity&&row.exchange_quantity+' em troca',
        row.loan_quantity&&row.loan_quantity+' em comodato'].filter(Boolean).join(' · ')
    )+'</li>').join('')+'</ul>';
  }

  window.OrionDeliveryOrderContainers=Object.freeze({fields,bind,confirmation,confirmed,summary});
})();
