/* Order only items, quantities and prices validated by the store. */
(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const slug = document.body.dataset.storeSlug;
  const api = '/api/v1/delivery-store/' + encodeURIComponent(slug);
  const products = byId('store-products');
  const cartNode = byId('store-cart');
  const checkout = byId('store-checkout');
  const checkoutForm = byId('store-checkout-form');
  const checkoutButton = byId('store-checkout-submit');
  const checkoutFeedback = byId('store-checkout-feedback');
  const summary = byId('store-order-summary');
  const tracking = byId('store-tracking');
  const trackingForm = byId('store-tracking-form');
  const trackingFeedback = byId('store-tracking-feedback');
  const cart = new Map();
  const kinds = { water: 'Água', prepared_food: 'Comida pronta', grocery: 'Mercado', household: 'Utilidades' };
  const methods = { cash: 'Dinheiro', debit_card: 'Cartão de débito', credit_card: 'Cartão de crédito', pix: 'Pix' };
  const days = ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo'];
  const flow = { confirmed: 'Recebido', dispatched: 'Em rota', fulfilled: 'Concluído', cancelled: 'Cancelado' };
  const paymentStatus = { received: 'Recebido', not_verified: 'A confirmar pela loja', refunded: 'Estornado' };
  const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const money = cents => brl.format(cents / 100);
  const units = () => [...cart.values()].reduce((sum, qty) => sum + qty, 0);
  const accepting = () => store.open && (store.allow_pickup || store.allow_delivery);
  const normalized = value => value.normalize('NFD').replace(/\p{Mark}/gu, '').toLowerCase();
  let store;
  let category = '';
  let quote;
  let attempt;
  let ticket = 0;
  let busy = false;
  let receipt;

  function element(tag, className, value) {
    const child = document.createElement(tag);
    if (className) child.className = className;
    if (value != null) child.textContent = value;
    return child;
  }

  function button(label, className, click) {
    const control = element('button', className, label);
    control.type = 'button';
    control.addEventListener('click', click);
    return control;
  }

  function message(target, text, error = false) {
    target.textContent = text;
    target.dataset.error = error ? 'true' : 'false';
  }

  async function request(path = '', data) {
    const response = await fetch(api + path, data === undefined ? {cache: 'no-store'} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify(data),
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = value.detail;
      throw Object.assign(Error(typeof detail === 'string' ? detail : detail?.message ||
        'Não foi possível concluir. Confira os dados e tente novamente.'), {status: response.status});
    }
    return value;
  }

  function renderCategories() {
    const tags = ['', ...new Set(store.items.map(item => item.kind))];
    byId('store-categories').replaceChildren(...tags.map(key => {
      const filter = button(kinds[key] || 'Todos', 'category', () => {
        category = key;
        for (const tag of byId('store-categories').children) {
          tag.setAttribute('aria-pressed', String(tag.dataset.storeKind === category));
        }
        renderProducts();
      });
      filter.dataset.storeKind = key;
      filter.setAttribute('aria-pressed', String(category === key));
      return filter;
    }));
  }

  function renderProducts() {
    const query = normalized(byId('store-search').value.trim());
    const visible = store.items.filter(item => (!category || item.kind === category) &&
      normalized(item.label + ' ' + (item.description || '')).includes(query));
    const cards = visible.map(item => {
      const result = element('article', 'service-card store-product');
      const info = element('div', 'service-detail');
      info.append(element('h3', '', item.label));
      if (item.description) info.append(element('p', '', item.description));
      info.append(element('span', 'price', money(item.unit_price_cents)));
      const add = button(item.available ? 'Adicionar' : 'Indisponível', 'outline', () => change(item.variant_id, 1));
      add.disabled = !item.available || !accepting() || units() >= 100;
      add.dataset.storeAvailable = String(item.available);
      add.setAttribute('aria-label', (item.available ? 'Adicionar ' : 'Indisponível: ') + item.label);
      result.append(info, add);
      return result;
    });
    products.replaceChildren(...(cards.length ? cards : [element('p', 'empty', 'Nenhum produto encontrado.')]));
  }

  function updateButtons() {
    [...products.querySelectorAll('button')].forEach(control => {
      control.disabled = !accepting() || control.dataset.storeAvailable === 'false' || units() >= 100;
    });
    byId('store-checkout-menu').disabled = cart.size === 0 || !accepting();
  }

  function change(id, delta) {
    if (delta > 0 && units() >= 100) return;
    const quantity = (cart.get(id) || 0) + delta;
    if (quantity > 0) cart.set(id, quantity);
    else cart.delete(id);
    invalidate();
    renderCart();
  }

  function renderCart() {
    let total = 0;
    const contents = [...cart].map(([id, qty]) => {
      const item = store.items.find(candidate => candidate.variant_id === id);
      total += item.unit_price_cents * qty;
      const row = element('div', 'store-line');
      const controls = element('div', 'store-quantity');
      function adjust(delta) {
        change(id, delta);
        const next = [...cartNode.querySelectorAll('button')].find(node =>
          node.dataset.cartVariant === id && node.dataset.cartDelta === String(delta) && !node.disabled);
        (next || (cart.size ? byId('store-checkout-menu') : byId('store-search'))).focus();
      }
      const less = button('−', 'outline', () => adjust(-1));
      less.setAttribute('aria-label', 'Remover uma unidade de ' + item.label);
      const more = button('+', 'outline', () => adjust(1));
      more.setAttribute('aria-label', 'Adicionar uma unidade de ' + item.label);
      less.dataset.cartVariant = more.dataset.cartVariant = id;
      less.dataset.cartDelta = '-1';
      more.dataset.cartDelta = '1';
      more.disabled = units() >= 100;
      controls.append(less, element('span', '', qty), more);
      row.append(element('span', '', item.label), controls);
      return row;
    });
    cartNode.replaceChildren(...(contents.length ? contents : [element('p', 'muted', 'Adicione produtos para montar seu pedido.')]));
    byId('store-subtotal').replaceChildren(...(contents.length ? [
      element('span', '', 'Subtotal'), element('strong', '', money(total)),
    ] : []));
    const link = byId('store-mobile-cart');
    link.hidden = !contents.length;
    link.textContent = 'Ver pedido · ' + money(total);
    updateButtons();
  }

  function invalidate() {
    ticket++;
    quote = null;
    attempt = null;
    summary.replaceChildren();
    summary.hidden = true;
    checkoutButton.textContent = 'Conferir valor';
  }

  function updateDeliveryFields() {
    const form = checkoutForm.elements;
    const isDelivery = form.mode.value === 'delivery';
    const fieldset = byId('store-address');
    fieldset.hidden = fieldset.disabled = !isDelivery;
    form.number.disabled = form.no_number.checked;
    const pickup = byId('store-pickup');
    pickup.hidden = isDelivery || !store.pickup_details;
    pickup.textContent = store.pickup_details ? 'Retirada em ' + store.pickup_details : '';
  }

  function draft() {
    const form = checkoutForm.elements;
    let address = null;
    if (form.mode.value === 'delivery') {
      const area = store.delivery_areas[form.coverage_area.value];
      if (!area) throw Error('Selecione um bairro atendido.');
      address = {
        postal_code: window.OrionFields.postalCodeValue(form.postal_code.value),
        street: form.street.value.trim(), number: form.no_number.checked ? null : form.number.value.trim(),
        no_number: form.no_number.checked, complement: form.complement.value.trim() || null,
        neighborhood: area.neighborhood, city: area.city, state: area.state,
      };
    }
    return {
      mode: form.mode.value, address, payment_preference: form.payment_preference.value,
      items: [...cart].map(([variant_id, quantity]) => ({variant_id, quantity})),
    };
  }

  function renderQuote(value) {
    const rows = value.items.map(item => {
      const row = element('div', 'store-line');
      row.append(element('span', '', item.quantity + ' × ' + item.product_name), element('strong', '', money(item.total_cents)));
      return row;
    });
    if (checkoutForm.elements.mode.value === 'delivery') {
      const fee = element('div', 'store-line');
      fee.append(element('span', '', 'Taxa de entrega'), element('strong', '', money(value.delivery_fee_cents)));
      rows.push(fee);
    }
    const total = element('div', 'store-total');
    total.append(element('span', '', 'Total no recebimento'), element('strong', '', money(value.total_cents)));
    summary.replaceChildren(...rows, total);
    summary.hidden = false;
    checkoutButton.textContent = 'Confirmar pedido';
  }

  function confirmed(result) {
    receipt = result;
    checkoutForm.reset();
    cart.clear();
    invalidate();
    renderCart();
    checkoutForm.hidden = true;
    byId('store-confirmed').hidden = false;
    checkout.setAttribute('aria-labelledby', 'store-confirmed-title');
    byId('store-confirmed-details').textContent = 'Pedido ' + result.order_code + ' · ' + money(result.total_cents);
    message(byId('store-copy-feedback'), '');
  }

  checkoutForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !cart.size) return;
    let content;
    let phone;
    try {
      content = draft();
      phone = window.OrionFields.phoneValue(checkoutForm.elements.customer_phone.value, {required: true});
    } catch (error) {
      message(checkoutFeedback, error.message, true);
      return;
    }
    const fields = [...checkoutForm.elements].filter(control => !control.disabled);
    const current = ticket;
    if (quote && !attempt) {
      attempt = {...content, quote: quote.quote, confirmed: true, idempotency_key: crypto.randomUUID(),
        customer_phone: phone, customer_name: checkoutForm.elements.customer_name.value.trim()};
    }
    busy = true;
    for (const control of fields) control.disabled = true;
    message(checkoutFeedback, quote ? 'Enviando pedido…' : 'Conferindo o total…');
    try {
      if (attempt) {
        confirmed(await request('/orders', attempt));
      } else {
        const next = await request('/quote', content);
        if (ticket === current) {
          quote = next;
          renderQuote(next);
          message(checkoutFeedback, 'Confira os itens e o total antes de confirmar.');
        }
      }
    } catch (error) {
      if (error.status === 409 || error.status === 422) invalidate();
      message(checkoutFeedback, error.message, true);
    } finally {
      busy = false;
      for (const control of fields) control.disabled = false;
      updateDeliveryFields();
    }
  });

  checkoutForm.addEventListener('input', () => {
    invalidate();
    message(checkoutFeedback, '');
    updateDeliveryFields();
  });
  checkout.addEventListener('cancel', event => {
    if (busy) event.preventDefault();
  });

  function openCheckout() {
    if (!cart.size || !accepting() || busy) return;
    checkoutForm.hidden = false;
    byId('store-confirmed').hidden = true;
    checkout.setAttribute('aria-labelledby', 'store-checkout-title');
    message(checkoutFeedback, '');
    updateDeliveryFields();
    checkout.showModal();
    window.OrionFields.refresh(checkoutForm);
  }

  function openTracking(code = '') {
    trackingForm.elements.tracking_code.value = code;
    message(trackingFeedback, '');
    if (!tracking.open) tracking.showModal();
    if (code) trackingForm.requestSubmit();
  }

  trackingForm.addEventListener('submit', async event => {
    event.preventDefault();
    const submit = trackingForm.querySelector('button[type=submit]');
    if (submit.disabled) return;
    submit.disabled = true;
    message(trackingFeedback, 'Consultando pedido…');
    try {
      const result = await request('/status', {tracking_code: trackingForm.elements.tracking_code.value.trim()});
      message(trackingFeedback, 'Pedido ' + result.order_code + ' · ' + flow[result.status] +
        '\nTotal ' + money(result.total_cents) + '\nPagamento: ' + paymentStatus[result.payment_status]);
    } catch (error) {
      message(trackingFeedback, error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  function options(target, entries) {
    target.replaceChildren(...entries.map(([value, label]) => new Option(label, value)));
  }

  byId('store-search').addEventListener('input', renderProducts);
  byId('store-checkout-menu').addEventListener('click', openCheckout);
  byId('store-track-menu').addEventListener('click', () => openTracking(receipt?.tracking_code || ''));
  byId('store-confirmed-track').addEventListener('click', () => {
    checkout.close();
    openTracking(receipt.tracking_code);
  });
  byId('store-copy-link').addEventListener('click', async () => {
    const link = new URL(location.pathname, location.origin);
    link.hash = 'pedido=' + receipt.tracking_code;
    try {
      await navigator.clipboard.writeText(link.href);
      message(byId('store-copy-feedback'), 'Link copiado.');
    } catch {
      const copy = element('input', 'store-field');
      copy.type = 'url';
      copy.readOnly = true;
      copy.value = link.href;
      copy.setAttribute('aria-label', 'Link de acompanhamento');
      byId('store-copy-feedback').replaceChildren(copy);
      copy.focus();
      copy.select();
    }
  });
  for (const control of document.querySelectorAll('[data-store-close]')) {
    control.addEventListener('click', () => {
      if (control.closest('dialog') !== checkout || !busy) control.closest('dialog').close();
    });
  }

  request().then(result => {
    store = result;
    const description = byId('store-description');
    description.hidden = !store.description;
    description.textContent = store.description || '';
    const hours = byId('store-hours');
    hours.hidden = store.opening_hours.length === 0;
    hours.textContent = store.opening_hours.map(slot => days[slot.weekday] + ' ' +
      slot.opens.slice(0, 5) + '–' + slot.closes.slice(0, 5)).join('\n');
    options(checkoutForm.elements.mode, [
      ...(store.allow_pickup ? [['pickup', 'Retirada']] : []),
      ...(store.allow_delivery ? [['delivery', 'Entrega']] : []),
    ]);
    options(checkoutForm.elements.payment_preference, store.payment_methods.map(method => [method, methods[method]]));
    options(checkoutForm.elements.coverage_area, [['', 'Selecione'], ...store.delivery_areas.map((area, i) =>
      [String(i), area.neighborhood + ' · ' + area.city + '/' + area.state + ' · ' + money(area.fee_cents)])]);
    renderCategories();
    renderProducts();
    renderCart();
    updateDeliveryFields();
    byId('store-content').hidden = false;
    message(byId('store-feedback'), !store.open ? 'Loja fechada. Confira o catálogo e os horários.' :
      accepting() ? '' : 'Pedidos temporariamente indisponíveis.');
    const code = new URLSearchParams(location.hash.slice(1)).get('pedido');
    if (code && /^[a-zA-Z0-9_-]{43}$/.test(code)) {
      history.replaceState(null, '', location.pathname + location.search);
      openTracking(code);
    }
  }).catch(error => message(byId('store-feedback'), error.message, true));
})();
