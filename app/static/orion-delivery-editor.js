/* Product editor in the Orion workspace, with original catalog packshots. */
(() => {
  'use strict';

  const images = [
    ['agua-20l', 'Garrafão 20 L'], ['agua-10l', 'Garrafão 10 L'], ['gas-p13', 'Gás P13'],
    ['gas-p45', 'Gás P45'], ['fardo-agua', 'Fardo de água'], ['agua-pequena', 'Água pequena'],
    ['garrafa-2l', 'Garrafa 2 L'], ['bebidas', 'Bebidas'], ['gelo', 'Gelo'], ['lata', 'Lata'],
    ['mangueira-gas', 'Mangueira de gás'], ['mercearia', 'Mercearia'], ['registro-gas', 'Registro de gás'],
    ['carvao', 'Carvão'], ['descartaveis', 'Descartáveis'], ['outros', 'Outros'],
  ];
  const imagePath = key => '/delivery-store-assets/products/' + key + '.webp';
  const models = [
    ['agua-20l', 'Água mineral 20 L', 'Água'], ['agua-10l', 'Água mineral 10 L', 'Água'],
    ['gas-p13', 'Gás P13', 'Gás'], ['gas-p45', 'Gás P45', 'Gás'],
    ['fardo-agua', 'Fardo de água', 'Água', 'fardo'], ['gelo', 'Gelo', 'Gelo', 'saco'],
    ['bebidas', 'Bebida', 'Bebidas'], ['carvao', 'Carvão', 'Carvão', 'saco'],
    ['mercearia', 'Produto de mercearia', 'Mercearia'],
    ['mangueira-gas', 'Mangueira de gás', 'Acessórios'], ['descartaveis', 'Descartáveis', 'Descartáveis'],
  ];
  const baseCategories = ['Água','Gás','Bebidas','Gelo','Mercearia','Alimentos preparados',
    'Carvão','Vasilhames','Acessórios','Descartáveis','Outros'];
  const prices = [['fixed','Preço fixo'],['from','A partir de'],['on_request','Sob consulta'],['custom','Personalizado']];
  const moneyInput = cents => cents == null ? '' :
    new Intl.NumberFormat('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2}).format(cents/100);
  const photoPattern = /^\/media\/[0-9a-f-]{36}\/[0-9a-f-]{36}(?:\/thumb)?\.webp$/;

  function safeProductImage(value) {
    return typeof value === 'string' && (photoPattern.test(value) ||
      images.some(([key]) => imagePath(key) === value)) ? value : '';
  }

  function picture(value, esc, css = 'os-product-thumbnail') {
    const path = safeProductImage(value);
    return path ? '<img class="' + css + '" src="' + esc(path) + '" alt="" loading="lazy" decoding="async">' : '';
  }

  function mount({host, item, addVariant, categories, call, allowed, canWrite, ui, onBack, onSaved}) {
    const {esc,title,button,field,moneyField,selectField,textField,checkField,icon,imageUploadField,requestKey} = ui;
    const variant = addVariant ? null : item;
    const heading = addVariant ? 'Nova variação' : item ? 'Editar produto' : 'Novo produto';
    const categoriesAvailable = [...new Set([...baseCategories, ...categories, item?.category].filter(Boolean))]
      .sort((a,b) => a.localeCompare(b, 'pt-BR'));
    const back = button('Voltar para produtos', 'delivery-editor-back', 'os-outline', 'left');
    const save = '<button type="submit" form="osDeliveryProductForm" class="os-button">'+icon('check')+'Salvar produto</button>';
    const section = (label, children) => '<section class="os-card os-form"><h2>' + label + '</h2>' + children + '</section>';
    const grid = children => '<div class="os-form-grid">' + children + '</div>';
    const categorySelect = selectField('Categoria','editor_category',
      [['','Selecione'],...categoriesAvailable.map(value => [value,value]),['__custom__','Nova categoria…']],item?.category||'','required');
    const newCategory = '<div data-delivery-new-category hidden>' +
      field('Nome da categoria','product_category','text','','maxlength="80" disabled') + '</div>';
    const returnable = '<fieldset class="os-form" data-delivery-returnable><legend>Vasilhame</legend>' +
      checkField('Exigir devolução de um vazio por unidade','exchange_enabled',!!variant?.exchange_container_id) +
      '<div data-delivery-container-select' + (variant?.exchange_container_id?'':' hidden') + '>' +
      selectField('Vasilhame para troca','exchange_container',[['','Selecione'], ...(variant?.exchange_container_id ?
        [[variant.exchange_container_id,variant.exchange_container_name||'Vasilhame atual']] : [])],
        variant?.exchange_container_id||'') + '<p class="os-help" data-delivery-container-status role="status"></p></div></fieldset>';
    const advanced = '<details class="os-card os-more-fields"><summary>Campos avançados</summary><div class="os-disclosure-body os-form">' +
      grid(field('Variação','variant_name','text',variant?.variant_name||(addVariant?'':'Unidade'),'maxlength="120" '+(addVariant?'required':''))+
      field('SKU','sku','text',variant?.sku||'','maxlength="80"')+
      field('Código de barras','barcode','text',variant?.barcode||'','pattern="[0-9]{1,32}" maxlength="32" inputmode="numeric"')+
      selectField('Tipo de preço','price_kind',prices,variant?.price_kind||'fixed'))+
      checkField('Variação ativa','variant_active',variant?.variant_active??true)+returnable+
      '<p class="os-help">Para cobrar valores diferentes, cadastre variações como Com troca e Sem troca. Vincule o vasilhame na variação com troca.</p></div></details>';
    const modelEntry = !item ? '<div class="os-delivery-models">' +
      selectField('Preencher com um modelo','product_model',[['','Selecione um modelo'],
        ...models.map(([key,label])=>[key,label])],'')+'</div>' : '';
    const gallery = images.map(([key,label]) => '<button type="button" class="os-service-option" data-delivery-image="' + key +
      '" aria-pressed="false">' + picture(imagePath(key),esc,'os-delivery-preset-image') + '<span>' + esc(label) + '</span></button>').join('');
    const brand = field('Marca','brand','text',item?.brand||'','maxlength="100"');
    const information = addVariant ? grid(categorySelect+newCategory) :
      grid(field('Nome do produto','name','text',item?.name||'','required maxlength="160"')+categorySelect+newCategory+
        (item?'<div class="os-wide">'+brand+'</div>':brand+modelEntry)) +
      textField('Descrição','description',item?.description||'','maxlength="2000" rows="3"');
    const imagePanel = addVariant ? '' : '<aside class="os-delivery-editor-side">' + section('Imagem do produto',
      '<div class="os-delivery-image-preview" data-delivery-image-preview hidden></div>'+
      '<div class="os-delivery-image-grid" role="group" aria-label="Imagens da biblioteca">'+gallery+'</div>'+
      imageUploadField('Foto do produto','photo_url',item?.photo_url||'', 'product')) + '</aside>';

    host.innerHTML = title(heading,addVariant ? item.name : '',back) +
      '<form id="osDeliveryProductForm" class="os-form os-delivery-product-form">' +
      '<div class="os-delivery-editor-layout' + (addVariant?' os-delivery-editor-single':'') +'"><div class="os-form">' +
      section('Dados do produto',information)+
      section('Preços',grid(moneyField('Preço de venda','price','text',moneyInput(variant?.unit_price_cents),'data-field-max="100000000"')+
        moneyField('Custo de compra','cost','text',moneyInput(variant?.purchase_cost_cents),'data-field-max="100000000"')))+
      section('Estoque',grid(field('Unidade','unit','text',item?.unit||'un','required maxlength="24"')+
        field('Estoque mínimo','minimum','number',variant?.minimum_quantity??0,'required min="0" max="10000000" step="1"')+
        (!variant ? field('Estoque inicial','initial','number',0,'required min="0" max="10000000" step="1"') : '')))+
      section('Disponibilidade',(addVariant?'':checkField('Produto ativo','product_active',item?.product_active??true))+
        checkField('Venda interna','sell_internally',variant?.sell_internally??true)+
        checkField('Entrega','allow_delivery',variant?.allow_delivery??true)+
        checkField('Retirada','allow_pickup',variant?.allow_pickup??true)+
        checkField('Disponível para a Loja rápida','allow_storefront',variant?.allow_storefront??false,
          'Inclua a variação na vitrine em Loja rápida.'))+advanced+'</div>'+imagePanel+'</div>'+
      '<div class="os-form-message" role="alert" data-delivery-fields-error></div>'+
      '<div class="os-form-actions">'+button('Cancelar','delivery-editor-back','os-outline')+save+'</div></form>';
    window.OrionFields.refresh(host);
    const form = host.querySelector('#osDeliveryProductForm');
    const elements = form.elements;
    const submit = form.querySelector('button[type=submit]');
    const error = form.querySelector('[data-delivery-fields-error]');
    let picked = item?.preset_image || '';
    let saving = false;
    let loadingContainers = false;
    let choicesLoaded = false;

    function showImage() {
      if (addVariant) return;
      const own = elements.photo_url.value;
      const selected = picture(own || (picked && imagePath(picked)),esc,'os-delivery-current-image');
      const preview=form.querySelector('[data-delivery-image-preview]');
      preview.hidden=!selected;
      preview.innerHTML=selected;
      form.querySelectorAll('[data-delivery-image]').forEach(control => {
        control.setAttribute('aria-pressed',String(!own&&picked===control.dataset.deliveryImage));
      });
    }

    function chooseImage(key) {
      if (form.querySelector('[data-os-uploading=true]')) return;
      picked = key === picked && !elements.photo_url.value ? '' : key;
      if (elements.photo_url.value) form.querySelector('[data-os-action=image-remove]').click();
      showImage();
    }

    function refreshCategory() {
      const custom = elements.editor_category.value === '__custom__';
      form.querySelector('[data-delivery-new-category]').hidden = !custom;
      elements.product_category.disabled = !custom;
      elements.product_category.required = custom;
    }

    async function refreshExchange() {
      const enabled = elements.exchange_enabled.checked;
      const pane = form.querySelector('[data-delivery-container-select]');
      pane.hidden = !enabled;
      const picker = elements.exchange_container;
      picker.disabled = !enabled || loadingContainers;
      picker.required = enabled;
      if (!enabled || choicesLoaded || loadingContainers) return;
      const status = pane.querySelector('[data-delivery-container-status]');
      loadingContainers = true;
      picker.disabled = true;
      status.textContent = 'Carregando vasilhames…';
      try {
        const value = picker.value;
        const choices = [];
        let offset = 0;
        for (;;) {
          const page = await call('/containers?offset='+offset);
          choices.push(...page.items);
          if (!page.has_more) break;
          offset += page.limit;
        }
        if (!allowed() || !form.isConnected) return;
        picker.replaceChildren(new Option('Selecione',''), ...choices.map(row=>new Option(row.name,row.id)));
        picker.value = value;
        choicesLoaded = true;
        status.textContent = choices.length ? '' : 'Cadastre um vasilhame para vincular à variação.';
      } catch (ex) {
        status.textContent = ex.message || 'Não foi possível listar os vasilhames.';
      } finally {
        loadingContainers = false;
        picker.disabled = !elements.exchange_enabled.checked;
      }
    }

    function priceMode() {
      const priced = ['fixed','from'].includes(elements.price_kind.value);
      elements.price.disabled = !priced;
      elements.price.required = priced;
    }

    host.querySelectorAll('[data-os-action=delivery-editor-back]').forEach(control => control.addEventListener('click', () => {
      if (!saving && !form.querySelector('[data-os-uploading=true]')) onBack();
    }));
    form.addEventListener('click', event => {
      const choice = event.target.closest('[data-delivery-image]');
      if (choice && !saving) chooseImage(choice.dataset.deliveryImage);
    });
    form.addEventListener('change', event => {
      const name = event.target.name;
      if (name === 'editor_category') refreshCategory();
      if (name === 'exchange_enabled') void refreshExchange();
      if (name === 'price_kind') priceMode();
      if (name !== 'product_model') return;
      const model = models.find(([key]) => key === event.target.value);
      if (!model) return;
      const [key,label,category,unit='un'] = model;
      elements.name.value = label;
      elements.editor_category.value = category;
      elements.unit.value = unit;
      if (elements.photo_url.value) form.querySelector('[data-os-action=image-remove]').click();
      picked = key;
      refreshCategory();
      showImage();
    });
    if (!addVariant) {
      const thumb = form.querySelector('.os-image-thumb');
      if (thumb) new MutationObserver(showImage).observe(thumb,{childList:true});
    }
    form.addEventListener('invalid', event => {
      const disclosure = event.target.closest('details');
      if (disclosure) disclosure.open = true;
    }, true);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (saving || !allowed() || !canWrite() || form.querySelector('[data-os-uploading=true]')) return;
      error.textContent = '';
      saving = true;
      submit.disabled = true;
      form.setAttribute('aria-busy','true');
      try {
        const parseMoney = (value, required) => window.OrionFields.parseMoney(value,{required,min:0,max:100000000});
        const quantity = value => window.OrionFields.parseDecimal(value,{required:true,scale:0,min:0,max:10000000});
        const exchange = elements.exchange_enabled.checked;
        if (exchange && !elements.exchange_container.value) throw Error('Selecione o vasilhame para troca.');
        const price = elements.price_kind.value;
        const payload = {
          variant: {name:elements.variant_name.value.trim()||'Unidade',sku:elements.sku.value.trim()||null,
            attributes:variant?.attributes||{},price_kind:price,active:elements.variant_active.checked,
            unit_price_cents:['fixed','from'].includes(price)?parseMoney(elements.price.value,true):null},
          details: {category:(elements.editor_category.value==='__custom__'?elements.product_category.value:elements.editor_category.value).trim(),
            unit:elements.unit.value,minimum_quantity:quantity(elements.minimum.value),barcode:elements.barcode.value.trim()||null,
            purchase_cost_cents:parseMoney(elements.cost.value,false),sell_internally:elements.sell_internally.checked,
            allow_delivery:elements.allow_delivery.checked,allow_pickup:elements.allow_pickup.checked,
            allow_storefront:elements.allow_storefront.checked,exchange_container_id:exchange?elements.exchange_container.value:null},
        };
        if (!addVariant) {
          payload.product = {name:elements.name.value,description:elements.description.value.trim()||null,
            active:elements.product_active.checked,synonyms:item?.synonyms||[],faqs:item?.faqs||[],
            official_benefits:item?.official_benefits||[]};
          const photo = elements.photo_url.value || null;
          payload.profile = {brand:elements.brand.value.trim()||null,photo_url:photo,preset_image:photo?null:picked||null};
        }
        let result;
        if (variant) {
          payload.product_revision = item.product_revision;
          payload.variant_revision = item.variant_revision;
          result = await call('/catalog/variants/'+encodeURIComponent(item.variant_id),'PUT',payload);
        } else {
          payload.initial_quantity = quantity(elements.initial.value);
          const path = addVariant ? '/catalog/products/'+encodeURIComponent(item.product_id)+'/variants' : '/catalog';
          result = await call(path,'POST',{...payload,idempotency_key:requestKey(form,payload)});
        }
        if (!form.isConnected || !allowed()) return;
        if (!result?.variant_id) throw Error('Não foi possível confirmar o cadastro.');
        onSaved(addVariant?'Variação salva.':'Produto salvo.');
      } catch (ex) {
        if (!form.isConnected || !allowed()) return;
        error.textContent = ex.message || 'Não foi possível salvar o produto.';
        error.scrollIntoView?.({block:'nearest'});
      } finally {
        saving = false;
        form.removeAttribute('aria-busy');
        submit.disabled = false;
      }
    });
    refreshCategory();
    priceMode();
    showImage();
    if (elements.exchange_enabled.checked) void refreshExchange();
    (form.querySelector('input[name=name]') || elements.editor_category).focus({preventScroll:true});
  }

  window.OrionDeliveryEditor = Object.freeze({mount, picture});
})();
