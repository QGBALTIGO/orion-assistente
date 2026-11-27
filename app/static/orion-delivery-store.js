/* Order only items, quantities and prices validated by the store. */
(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const slug = document.body.dataset.storeSlug;
  const preview = document.body.dataset.storePreview;
  const api = preview || '/api/v1/delivery-store/' + encodeURIComponent(slug);
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
  const methods = { cash: 'Dinheiro', debit_card: 'Cartão de débito', credit_card: 'Cartão de crédito', pix: 'Pix na entrega' };
  const days = ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo'];
  const flow = { confirmed: 'Recebido', dispatched: 'Em rota', fulfilled: 'Concluído', cancelled: 'Cancelado' };
  const paymentStatus = { received: 'Recebido', not_verified: 'A confirmar pela loja', refunded: 'Estornado' };
  const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  const money = cents => brl.format(cents / 100);
  const units = () => [...cart.values()].reduce((sum, qty) => sum + qty, 0);
  const accepting = () => store.open && (store.allow_pickup || store.allow_delivery);
  const cartCand = id => store.items.find(item=>item.variant_id===id);
  const cap = item => item.available_quantity ?? (item.available ? 100 : 0);
  const allowedModes = () => [['pickup','Retirada'],['delivery','Entrega']].filter(([kind]) =>
    store['allow_'+kind] && [...cart.keys()].every(id=>cartCand(id)['allow_'+kind]!==false));
  const subtotal = () => [...cart].reduce((total,[id,count])=>total+cartCand(id).unit_price_cents*count,0);
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
    if (preview && data !== undefined) throw Error('A prévia da loja é apenas para visualização.');
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
      normalized([item.label,item.brand,item.description].filter(Boolean).join(' ')).includes(query));
    const cards = visible.map(item => {
      const result = element('article', 'service-card store-product');
      const info = element('div', 'service-detail');
      info.append(element('h3', '', item.label));
      if (item.brand) info.append(element('p','muted',item.brand));
      if (item.description) info.append(element('p', '', item.description));
      if (item.requires_exchange) info.append(element('p','store-exchange-tag','Com troca de vasilhame'));
      info.append(element('span', 'price', money(item.unit_price_cents)));
      const add = button(item.available ? 'Adicionar' : 'Indisponível', 'outline', () => change(item.variant_id, 1));
      add.disabled = !accepting() || units() >= 100 || (cart.get(item.variant_id)||0) >= cap(item);
      add.dataset.storeVariant = item.variant_id;
      add.setAttribute('aria-label', (item.available ? 'Adicionar ' : 'Indisponível: ') + item.label);
      const photo = image('store-product-photo',item.image_url);
      if (photo) result.append(photo);
      result.append(info, add);
      return result;
    });
    products.replaceChildren(...(cards.length ? cards : [element('p', 'empty', 'Nenhum produto encontrado.')]));
  }

  function updateButtons() {
    products.querySelectorAll('[data-store-variant]').forEach(control => {
      const id=control.dataset.storeVariant;
      control.disabled = !accepting() || units() >= 100 || (cart.get(id)||0) >= cap(cartCand(id));
    });
    const minimum=store.minimum_subtotal_cents||0;
    const feedback = !cart.size ? '' : !allowedModes().length ?
      'Os produtos selecionados precisam ter uma forma de recebimento em comum.' :
      subtotal()<minimum ? 'Pedido mínimo de '+money(minimum)+' em produtos.' : '';
    byId('store-cart-feedback').textContent=feedback;
    byId('store-checkout-menu').disabled = !!preview || cart.size === 0 || !accepting() || !!feedback;
  }

  function change(id, delta) {
    if (delta > 0 && units() >= 100) return;
    const quantity = (cart.get(id) || 0) + delta;
    if (delta > 0 && quantity > cap(cartCand(id))) return;
    if (quantity > 0) cart.set(id, quantity);
    else cart.delete(id);
    invalidate();
    renderCart();
  }

  function renderCart() {
    let total = 0;
    const contents = [...cart].map(([id, qty]) => {
      const item = cartCand(id);
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
      more.disabled = units() >= 100 || qty >= cap(item);
      controls.append(less, element('span', '', qty), more);
      const name = element('span','store-cart-product');
      const previewImage = image('store-cart-photo',item.image_url);
      if (previewImage) name.append(previewImage);
      name.append(element('span','',item.label));
      row.append(name, controls);
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
    const scheduling = isDelivery && !!store.schedule_options?.length;
    byId('store-schedule-field').hidden = form.scheduled_at.disabled = !scheduling;
    const cash = form.payment_preference.value === 'cash';
    byId('store-cash').hidden = byId('store-cash').disabled = !cash;
    const change = cash && form.need_change.checked;
    byId('store-change-field').hidden = form.change_for.disabled = !change;
    form.change_for.required = change;
    const exchange = [...cart.keys()].some(id => cartCand(id).requires_exchange);
    byId('store-exchange').hidden = form.accept_exchange.disabled = !exchange;
    form.accept_exchange.required = exchange;
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
      scheduled_at:form.scheduled_at.disabled?null:form.scheduled_at.value||null,
      accept_exchange:!form.accept_exchange.disabled&&form.accept_exchange.checked,
      change_for_cents:form.change_for.disabled?null:
        window.OrionFields.parseMoney(form.change_for.value,{required:true,min:1,max:1000000000}),
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
    const change = value.change_cents == null ? [] : [element('p','small muted','Troco previsto: '+money(value.change_cents))];
    summary.replaceChildren(...rows, total, ...change);
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
    if (preview || busy || !cart.size) return;
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
        customer_phone: phone, customer_name: checkoutForm.elements.customer_name.value.trim(),
        buyer_note:checkoutForm.elements.buyer_note.value.trim()||null};
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
  byId('store-lookup-cep').addEventListener('click', async event => {
    const button=event.currentTarget;
    const target=byId('store-cep-feedback');
    const form=checkoutForm.elements;
    let cep;
    try {cep=window.OrionFields.postalCodeValue(form.postal_code.value,{required:true});}
    catch(error) {message(target,error.message,true);return;}
    if (!cep) {message(target,'Informe o CEP com oito dígitos.',true);return;}
    const stamp=ticket;
    button.disabled=true;
    message(target,'Consultando CEP…');
    try {
      const address=await request('/cep/'+encodeURIComponent(cep));
      if (!checkout.open || stamp!==ticket || form.mode.value!=='delivery') {
        message(target,'');
        return;
      }
      if (address.street) form.street.value=address.street;
      const area=store.delivery_areas.findIndex(item=>item.state===address.state&&
        normalized(item.city)===normalized(address.city)&&normalized(item.neighborhood)===normalized(address.neighborhood));
      form.coverage_area.value=area<0?'':String(area);
      invalidate();
      message(target,area<0?'Escolha seu bairro entre as áreas atendidas.':'Endereço encontrado. Confira e informe o número.');
    } catch(error) {
      message(target,stamp===ticket?error.message:'',stamp===ticket);
    } finally {button.disabled=false;}
  });
  checkout.addEventListener('cancel', event => {
    if (busy) event.preventDefault();
  });

  function openCheckout() {
    if (preview || byId('store-checkout-menu').disabled || busy) return;
    const mode=checkoutForm.elements.mode;
    const chosen=mode.value;
    const modes=allowedModes();
    options(mode,modes);
    if (modes.some(([value])=>value===chosen)) mode.value=chosen;
    checkoutForm.hidden = false;
    byId('store-confirmed').hidden = true;
    checkout.setAttribute('aria-labelledby', 'store-checkout-title');
    message(checkoutFeedback, '');
    updateDeliveryFields();
    checkout.showModal();
    window.OrionFields.refresh(checkoutForm);
  }

  function openTracking(code = '') {
    if (preview) return;
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

  function safeImage(value) {
    return typeof value==='string' && /^\/(?:media\/[0-9a-f-]{36}\/[0-9a-f-]{36}(?:\/thumb)?|delivery-store-assets\/products\/[a-z0-9-]+)\.webp$/.test(value);
  }

  function image(css,value) {
    if (!safeImage(value)) return null;
    const node=element('img',css);
    node.alt='';node.src=value;node.loading='lazy';node.decoding='async';
    return node;
  }

  function storeIdentity() {
    for (const [id, url] of [['store-cover',store.cover_url],['store-logo',store.logo_url]]) {
      if (!safeImage(url)) continue;
      const node=byId(id);node.src=url;node.hidden=false;
    }
    if (/^#[0-9a-f]{6}$/i.test(store.brand_color||'')) {
      document.body.style.setProperty('--store-brand-accent',store.brand_color);
    }
    const label=byId('store-open-label');
    label.textContent=store.open?'Aberta agora':'Fechada';
    label.dataset.open=String(store.open);
    label.hidden=false;
    if (store.estimate_min_minutes!=null && store.estimate_max_minutes!=null) {
      const eta=byId('store-eta');
      eta.textContent='Previsão de '+store.estimate_min_minutes+' a '+store.estimate_max_minutes+' min';
      eta.hidden=false;
    }
    if (!preview && /^\+?\d{10,15}$/.test(store.public_phone||'')) {
      const contact=byId('store-contact');
      contact.href='https://wa.me/'+store.public_phone.replace(/^\+/,'');
      contact.hidden=false;
    }
    byId('store-preview-label').hidden=!preview;
    byId('store-checkout-menu').hidden=!!preview;
    byId('store-track-menu').hidden=!!preview;
    products.classList.toggle('store-product-list',store.layout==='list');
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
    storeIdentity();
    options(checkoutForm.elements.mode, allowedModes());
    options(checkoutForm.elements.payment_preference, store.payment_methods.map(method => [method, methods[method]]));
    options(checkoutForm.elements.coverage_area, [['', 'Selecione'], ...store.delivery_areas.map((area, i) =>
      [String(i), area.neighborhood + ' · ' + area.city + '/' + area.state + ' · ' + money(area.fee_cents)])]);
    options(checkoutForm.elements.scheduled_at,[['','Assim que possível'],...(store.schedule_options||[]).map(slot=>[slot.when,slot.label])]);
    renderCategories();
    renderProducts();
    renderCart();
    updateDeliveryFields();
    byId('store-content').hidden = false;
    message(byId('store-feedback'), !store.open ? store.closed_message || 'Loja fechada. Confira o catálogo e os horários.' :
      accepting() ? '' : 'Pedidos temporariamente indisponíveis.');
    const code = new URLSearchParams(location.hash.slice(1)).get('pedido');
    if (!preview && code && /^[a-zA-Z0-9_-]{43}$/.test(code)) {
      history.replaceState(null, '', location.pathname + location.search);
      openTracking(code);
    }
  }).catch(error => message(byId('store-feedback'), error.message, true));
})();
