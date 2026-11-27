/* Publication controls for verified Delivery and Distribution workspaces. */
(() => {
  'use strict';

  const paymentOptions = [
    ['cash', 'Dinheiro'], ['debit_card', 'Cartão de débito'],
    ['credit_card', 'Cartão de crédito'], ['pix', 'Pix'],
  ];
  const allKinds = [
    ['water', 'Água'], ['prepared_food', 'Comida pronta'],
    ['grocery', 'Mercado'], ['household', 'Utilidades'],
  ];
  const weekdays = ['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado','Domingo'];

  async function mount({host, call, allowed, canWrite, ui, openEditor}) {
    const {esc, loading, button, title, field, selectField, textField, checkField, empty, money, query} = ui;
    if (!host || !allowed()) return;
    let state;
    let results;
    let offset = 0;
    let requestSequence = 0;
    const root = '/storefront';
    const current = () => allowed() && host.isConnected;
    const error = message => '<p class="os-notice os-error" role="alert">' + esc(message) + '</p>';
    const smallButton = (text, action, icon, extra = '') => button(text, action, 'os-outline os-small', icon, extra);
    const controls = () => canWrite() && state;
    const targetAttr = id => 'data-store-variant="' + esc(id) + '"';
    const kinds = () => state.offering === 'gas_agua' ? allKinds.slice(0, 1) : allKinds;

    host.innerHTML = title('Loja rápida', 'Catálogo público de Delivery', '') + loading('Carregando loja…');
    try {
      state = await call(root);
    } catch (ex) {
      if (current()) host.innerHTML = title('Loja rápida', '', '') + error(ex.message);
      return;
    }
    if (!current()) return;
    host.innerHTML = title('Loja rápida', 'Catálogo público de Delivery',
      controls() ? button('Configurar loja', 'store-settings', '', 'settings') : '') +
      '<section class="os-card"><div class="os-card-header"><h2>' + esc(state.title) + '</h2>' +
      '<span class="os-badge">' + (state.public_url ? 'Publicada' : 'Despublicada') + '</span></div>' +
      '<div class="os-actions">' +
      (state.public_url ? '<a class="os-button os-outline" target="_blank" rel="noopener" href="' +
        esc(state.public_url) + '">Abrir loja</a>' : '') +
      (state.enabled && controls() ? smallButton('Despublicar', 'store-close', 'x') : '') + '</div>' +
      (!state.publication_available ? '<p class="os-help">Publicação pendente de liberação para esta empresa.</p>' : '') +
      '</section><section class="os-card os-section-gap"><div class="os-card-header"><h2>Produtos na vitrine</h2></div>' +
      (state.items.length ? '<div class="os-summary-list">' + state.items.map(item =>
        '<div class="os-summary-row"><span>' + esc(item.label) + '</span>' +
        (controls() ? smallButton('Retirar', 'store-remove', 'x', targetAttr(item.variant_id)) : '') +
        '</div>').join('') + '</div>' : '<p class="os-help">Selecione os produtos que deseja exibir na loja.</p>') +
      '</section><section class="os-card os-section-gap"><div class="os-card-header"><h2>Selecionar produtos</h2></div>' +
      '<form id="osStoreFilters" class="os-filter-row">' +
      field('Buscar produto', 'store_search', 'search', '', 'maxlength="120"') +
      '<button type="submit" class="os-button os-outline">Filtrar</button></form><div id="osStoreItems" aria-live="polite"></div></section>';
    window.OrionFields.refresh(host);

    const searchForm = host.querySelector('#osStoreFilters');
    const output = host.querySelector('#osStoreItems');

    function payload(patch) {
      const {revision, slug, title, description, enabled, allow_pickup, allow_delivery, pickup_details,
        opening_hours, payment_methods, max_open_orders, items} = state;
      return {revision, slug, title, description, enabled, allow_pickup, allow_delivery, pickup_details,
        opening_hours, payment_methods, max_open_orders, items, ...patch};
    }

    async function submit(patch) {
      const result = await call(root, 'PUT', payload(patch));
      if (!result || typeof result.revision !== 'number') throw Error('Confira a situação da loja antes de tentar novamente.');
    }

    const confirmation = form => !!form.elements.store_confirm?.checked;
    function confirmField() {
      return checkField('Confirmo a publicação e a seleção de produtos permitidos', 'store_confirm', false,
        'A loja pública mostra apenas os produtos selecionados. O recebimento é confirmado pelo operador.');
    }

    function hour(index, row = {}) {
      return '<fieldset data-store-hour class="os-action-panel"><div class="os-form-grid">' +
        selectField('Dia da semana', 'weekday_' + index, weekdays.map((day, n) => [String(n), day]), String(row.weekday ?? 0)) +
        field('Abre', 'opens_' + index, 'time', (row.opens || '').slice(0, 5), 'required') +
        field('Fecha', 'closes_' + index, 'time', (row.closes || '').slice(0, 5), 'required') +
        '</div><button type="button" class="os-button os-outline os-small" data-store-remove-hour>Remover horário</button></fieldset>';
    }

    function settings() {
      const content = '<div class="os-form-grid">' +
        field('Nome público', 'store_title', 'text', state.title, 'required maxlength="160"') +
        field('Endereço na web', 'store_slug', 'text', state.slug, 'required minlength="3" maxlength="80" pattern="[a-z0-9]+(?:-[a-z0-9]+)*"') +
        field('Pedidos públicos simultâneos', 'max_open_orders', 'number', state.max_open_orders, 'required min="1" max="300" step="1"') +
        textField('Descrição pública', 'store_description', state.description || '', 'maxlength="600"') +
        textField('Endereço público para retirada', 'pickup_details', state.pickup_details || '', 'maxlength="300"') +
        '</div><div class="os-form-grid">' +
        checkField('Aceitar retirada', 'allow_pickup', state.allow_pickup) +
        checkField('Aceitar entrega pela área cadastrada', 'allow_delivery', state.allow_delivery) + '</div>' +
        '<section><h3>Pagamento no recebimento</h3><div class="os-form-grid">' +
        paymentOptions.map(([key, label]) => checkField(label, 'method_' + key, state.payment_methods.includes(key))).join('') +
        '</div></section><section><h3>Horários</h3><p class="os-help">Sem horários cadastrados, a loja aceita pedidos a qualquer hora.</p>' +
        '<div data-store-hours>' + state.opening_hours.map((row, i) => hour(i, row)).join('') + '</div>' +
        '<button type="button" class="os-button os-outline os-small" data-store-add-hour>Adicionar horário</button></section>' +
        checkField('Publicar loja', 'store_enabled', state.enabled) + confirmField();
      openEditor('Configurar loja', content, async (form, values) => {
        const enabled = form.elements.store_enabled.checked;
        if (enabled && !state.publication_available) throw Error('A publicação ainda não foi liberada para esta empresa.');
        const list = [...form.querySelectorAll('[data-store-hour]')].map(node => ({
          weekday: Number(node.querySelector('[name^="weekday_"]').value),
          opens: node.querySelector('[name^="opens_"]').value,
          closes: node.querySelector('[name^="closes_"]').value,
        }));
        await submit({
          slug: values.store_slug.trim(), title: values.store_title.trim(), description: values.store_description.trim() || null,
          enabled, allow_pickup: form.elements.allow_pickup.checked, allow_delivery: form.elements.allow_delivery.checked,
          pickup_details: values.pickup_details.trim() || null, max_open_orders: Number(values.max_open_orders),
          opening_hours: list, confirm_catalogue: confirmation(form),
          payment_methods: paymentOptions.filter(([key]) => form.elements['method_' + key].checked).map(([key]) => key),
        });
      }, 'Configuração da loja salva.', {bind: dialog => {
        let rowIndex = state.opening_hours.length;
        dialog.addEventListener('click', event => {
          if (event.target.closest('[data-store-add-hour]')) {
            const rows = dialog.querySelector('[data-store-hours]');
            if (rows.children.length < 28) rows.insertAdjacentHTML('beforeend', hour(rowIndex++));
            window.OrionFields.refresh(rows);
          } else {
            event.target.closest('[data-store-remove-hour]')?.closest('[data-store-hour]')?.remove();
          }
        });
      }});
    }

    function editItem(source) {
      const entry = state.items.find(item => item.variant_id === source.variant_id);
      const kindOptions = kinds();
      const content = field('Nome na loja', 'store_label', 'text',
        entry?.label || (source.name + ' · ' + source.variant_name).slice(0, 160), 'required maxlength="160"') +
        textField('Descrição na loja', 'store_item_description', entry?.description || '', 'maxlength="600"') +
        selectField('Categoria pública', 'store_item_kind', kindOptions, entry?.kind || kindOptions[0][0]) +
        (state.enabled ? confirmField() : '');
      openEditor(entry ? 'Atualizar publicação do produto' : 'Selecionar produto para a loja', content,
        async (form, values) => {
          const {variant_id, product_revision, variant_revision} = source;
          const item = {variant_id, product_revision, variant_revision, label: values.store_label.trim(),
            kind: values.store_item_kind, description: values.store_item_description.trim() || null};
          await submit({items: [...state.items.filter(row => row.variant_id !== variant_id), item],
            confirm_catalogue: confirmation(form)});
        }, 'Produto da loja salvo.');
    }

    function removeItem(item) {
      const remaining = state.items.filter(row => row.variant_id !== item.variant_id);
      const enabled = state.enabled && remaining.length > 0;
      const description = '<p class="os-help">Retirar ' + esc(item.label) + ' da loja.' +
        (state.enabled && !remaining.length ? ' A loja será despublicada ao retirar o último produto.' : '') + '</p>';
      openEditor('Retirar produto da loja', description + (enabled ? confirmField() : ''),
        async form => { await submit({items: remaining, enabled, confirm_catalogue: confirmation(form)}); },
        'Produto retirado da loja.', {label: 'Retirar da loja'});
    }

    function closeStore() {
      openEditor('Despublicar loja', '<p class="os-help">O link ficará indisponível para novos pedidos.</p>',
        async () => {
          const result = await call(root + '/unpublish', 'POST', {revision: state.revision});
          if (result?.enabled !== false) throw Error('Confira a situação da loja antes de tentar novamente.');
        }, 'Loja despublicada.', {label:'Despublicar'});
    }

    async function readItems() {
      const sequence = ++requestSequence;
      output.innerHTML = loading('Buscando produtos…');
      try {
        const response = await call(root + '/items?' + query({search:searchForm.elements.store_search.value.trim(), offset}));
        if (!current() || sequence !== requestSequence) return;
        results = response.items;
        output.innerHTML = (results.length ? '<div class="os-cards-grid">' + results.map(item => {
          const entry = state.items.find(row => row.variant_id === item.variant_id);
          const changed = entry && (entry.product_revision !== item.product_revision || entry.variant_revision !== item.variant_revision);
          return '<article class="os-card os-item-card"><h3>' + esc(item.name) + '</h3><p class="os-help">' +
            esc(item.variant_name + ' · ' + money(item.unit_price_cents)) + '</p>' +
            (changed ? '<p class="os-help">Atualize a publicação para exibir a variação após a mudança.</p>' : '') +
            (controls() ? '<div class="os-actions">' + smallButton(entry ? 'Editar publicação' : 'Selecionar', 'store-add',
              entry ? 'edit' : 'plus', targetAttr(item.variant_id)) + '</div>' : '') + '</article>';
        }).join('') + '</div>' : empty('box', 'Nenhuma variação encontrada', 'Cadastre ou procure produtos ativos com preço fixo.')) +
          (offset > 0 || response.has_more ? '<div class="os-pagination"><div class="os-actions">' +
            smallButton('Anterior', 'store-prev', 'left', offset ? '' : 'disabled') +
            smallButton('Próxima', 'store-next', 'chevron', response.has_more ? '' : 'disabled') + '</div></div>' : '');
      } catch (ex) {
        if (current() && sequence === requestSequence) output.innerHTML = error(ex.message);
      }
    }

    searchForm.addEventListener('submit', event => {
      event.preventDefault();
      offset = 0;
      void readItems();
    });
    host.addEventListener('click', event => {
      const control = event.target.closest('[data-os-action^="store-"]');
      if (!control || !current()) return;
      event.stopPropagation();
      const action = control.dataset.osAction;
      if (action === 'store-prev' || action === 'store-next') {
        offset += action === 'store-next' ? 25 : -25;
        void readItems();
      } else if (controls()) {
        const id = control.dataset.storeVariant;
        if (action === 'store-settings') settings();
        if (action === 'store-close') closeStore();
        if (action === 'store-add' && results) editItem(results.find(item => item.variant_id === id));
        if (action === 'store-remove') removeItem(state.items.find(item => item.variant_id === id));
      }
    });
    await readItems();
  }

  window.OrionDeliveryStorefront = Object.freeze({mount});
})();
