/* Public, illustrative interactions. Only the explicitly submitted contact form calls an API. */
(() => {
  'use strict';
  const fields = window.OrionFields;
  fields.bind(document);
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const reducedMotion = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  const sectors = {
    barbearia: {
      word: 'sua barbearia.', photo: 'negocio-barbearia.webp',
      alt: 'Profissional concentrado no corte de cabelo de um cliente',
      activity: 'Corte com Rafael, às 14h', label: 'NA BARBEARIA',
      kicker: 'MAIS PRESENÇA EM CADA ATENDIMENTO',
      title: ['Você cuida do corte.', 'O dia ganha organização.'],
      description: 'Cortes, barba, combinações e horários de cada profissional. Organize os detalhes que fazem a barbearia funcionar.',
      cases: ['Serviços e profissionais na mesma agenda', 'Pedidos de agendamento pelo WhatsApp', 'Clientes, retorno e comissões da equipe']
    },
    estetica: {
      word: 'sua clínica.', photo: 'negocio-estetica.webp',
      alt: 'Profissional realizando um atendimento de estética facial',
      activity: 'Avaliação com Marina, às 15h', label: 'NA ESTÉTICA',
      kicker: 'CUIDADO TAMBÉM É ORGANIZAÇÃO',
      title: ['Cada atendimento,', 'com a atenção que merece.'],
      description: 'Deixe claras as informações dos serviços, organize a duração de cada atendimento e acompanhe o relacionamento com seus clientes.',
      cases: ['Serviços, duração e agenda dos profissionais', 'Informações do negócio no atendimento', 'Cadastro de clientes e regras de fidelização']
    },
    odontologia: {
      word: 'seu consultório.', photo: 'negocio-odontologia.webp',
      alt: 'Profissional de odontologia durante um atendimento no consultório',
      activity: 'Consulta agendada para 10h30', label: 'NO CONSULTÓRIO',
      kicker: 'O ATENDIMENTO COMEÇA ANTES DA CONSULTA',
      title: ['Mais clareza para', 'a rotina do consultório.'],
      description: 'Organize o contato inicial, os horários e as informações administrativas. A equipe avalia a configuração para a rotina do seu consultório.',
      cases: ['Solicitações administrativas pelo WhatsApp', 'Horários, profissionais e bloqueios de agenda', 'Nomes e contatos em um cadastro organizado']
    },
    restaurante: {
      word: 'seu restaurante.', photo: 'negocio-restaurante.webp',
      alt: 'Equipe de cozinha em um restaurante.',
      activity: 'Solicitação recebida pelo WhatsApp', label: 'NO RESTAURANTE',
      kicker: 'SUA OPERAÇÃO TEM MUITO ACONTECENDO',
      title: ['A conversa chega.', 'O pedido ganha contexto.'],
      description: 'Apresente informações do negócio e organize produtos e solicitações. A configuração do catálogo e dos pedidos é alinhada com a equipe.',
      cases: ['Informações e dúvidas no WhatsApp', 'Catálogo de produtos, quando habilitado', 'Propostas e pedidos para acompanhar']
    },
    fitness: {
      word: 'seu estúdio.', photo: 'negocio-fitness.webp',
      alt: 'Personal trainer orientando uma aluna durante um exercício',
      activity: 'Atendimento individual, às 8h', label: 'COM O PERSONAL',
      kicker: 'FOCO EM QUEM ESTÁ COM VOCÊ',
      title: ['Seu acompanhamento.', 'Uma rotina bem preparada.'],
      description: 'Organize atendimentos individuais, disponibilidade e contatos. Adapte o Orion ao seu jeito de trabalhar com orientação na configuração.',
      cases: ['Agenda para atendimentos individuais', 'Horários e informações pelo WhatsApp', 'Contatos e registros para acompanhar a rotina']
    }
  };

  function initializeForm() {
    const form = $('#sales-request-form');
    if (!form) return;
    const submit = $('.form-submit', form);
    const error = $('#lead-error');
    const success = $('#lead-success');
    let busy = false;
    let pendingPayload = '';
    let pendingKey = '';
    submit.disabled = false;

    function uuid() {
      if (typeof window.crypto?.randomUUID === 'function') return window.crypto.randomUUID();
      const bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = [...bytes].map(byte => byte.toString(16).padStart(2, '0')).join('');
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function showError(message) {
      error.textContent = message;
      error.hidden = false;
      error.setAttribute('tabindex', '-1');
      error.focus({ preventScroll: true });
    }

    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !fields.validateForm(form) || !form.reportValidity()) return;
      error.hidden = true;
      error.textContent = '';
      const data = new FormData(form);
      const payload = JSON.stringify({
        name: String(data.get('name') || '').trim(),
        email: String(data.get('email') || '').trim(),
        phone: fields.phoneValue(data.get('phone')),
        business_name: '',
        segment: String(data.get('segment') || ''),
        intent: String(data.get('intent') || 'trial'),
        consent: data.get('consent') === 'on',
        website: String(data.get('website') || '')
      });
      if (payload !== pendingPayload || !pendingKey) {
        try { pendingKey = uuid(); }
        catch { showError('Não foi possível preparar o envio. Atualize o navegador e tente novamente.'); return; }
        pendingPayload = payload;
      }
      busy = true;
      submit.disabled = true;
      form.setAttribute('aria-busy', 'true');
      const label = $('span', submit);
      label.textContent = 'Enviando solicitação...';
      const controls = $$('input, select', form);
      controls.forEach(control => { control.disabled = true; });
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 16000);
      try {
        const response = await fetch('/api/public/sales-requests', {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pendingKey },
          body: payload, credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal
        });
        if (response.status !== 201) {
          if (response.status === 422) throw new Error('Confira seu nome, e-mail, WhatsApp e segmento. A autorização para contato também é necessária.');
          if (response.status === 429) throw new Error('Recebemos várias tentativas em pouco tempo. Aguarde alguns minutos e envie novamente. Seus dados continuam preenchidos.');
          if (response.status === 409) {
            pendingKey = '';
            throw new Error('Precisamos renovar a identificação deste envio. Confira os dados e tente novamente.');
          }
          throw new Error('Não foi possível registrar sua solicitação agora. Tente novamente em alguns instantes. Seus dados continuam preenchidos.');
        }
        let result;
        try { result = await response.json(); } catch { result = null; }
        if (result?.ok !== true || typeof result.request_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(result.request_id)) {
          throw new Error('Não conseguimos confirmar o registro. Tente enviar novamente para verificar sua solicitação.');
        }
        form.reset();
        form.hidden = true;
        success.hidden = false;
        success.focus({ preventScroll: false });
        pendingKey = '';
        pendingPayload = '';
      } catch (failure) {
        const message = failure.name === 'AbortError' || failure instanceof TypeError
          ? 'A conexão não confirmou o envio. Seus dados continuam preenchidos. Verifique sua conexão e tente novamente.'
          : failure.message;
        showError(message);
      } finally {
        window.clearTimeout(timeout);
        busy = false;
        controls.forEach(control => { control.disabled = false; });
        submit.disabled = false;
        label.textContent = 'Enviar solicitação';
        form.removeAttribute('aria-busy');
      }
    });

    $$('[data-intent]').forEach(link => link.addEventListener('click', () => {
      if (form.hidden || busy) return;
      const intent = link.dataset.intent;
      if ([...$('#lead-intent').options].some(option => option.value === intent)) $('#lead-intent').value = intent;
      if (link.id === 'sector-cta') $('#lead-segment').value = $('#sector-panel').dataset.segment || 'barbearia';
    }));
  }
  initializeForm();

  const menuButton = $('.menu-toggle');
  const mobileNav = $('#mobile-nav');
  function setMenu(open, returnFocus = false) {
    if (!menuButton || !mobileNav) return;
    mobileNav.hidden = !open;
    menuButton.setAttribute('aria-expanded', String(open));
    menuButton.setAttribute('aria-label', open ? 'Fechar menu' : 'Abrir menu');
    $('use', menuButton).setAttribute('href', open ? '#i-close' : '#i-menu');
    document.body.classList.toggle('menu-open', open);
    if (returnFocus) menuButton.focus({ preventScroll: true });
  }
  menuButton?.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
  $$('a', mobileNav || document.createElement('div')).forEach(link => link.addEventListener('click', () => setMenu(false)));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') setMenu(false, true);
  });
  window.addEventListener('resize', () => { if (window.innerWidth >= 960) setMenu(false); }, { passive: true });
  document.addEventListener('focusin', event => {
    if (menuButton?.getAttribute('aria-expanded') === 'true' && !event.target.closest('.site-header')) setMenu(false);
  });
  document.addEventListener('click', event => {
    if (menuButton?.getAttribute('aria-expanded') === 'true' && !event.target.closest('.site-header')) setMenu(false);
  });

  function bindTabs(selector, activate) {
    const tabs = $$(selector);
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => activate(tab));
      tab.addEventListener('keydown', event => {
        let next;
        if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
        else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = tabs.length - 1;
        else return;
        event.preventDefault();
        activate(tabs[next]);
        tabs[next].focus({ preventScroll: true });
      });
    });
  }
  function selectDemo(tab) {
    const name = tab.dataset.demoTab;
    $$('[data-demo-tab]').forEach(button => {
      const active = button === tab;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    $$('.demo-scene').forEach(scene => {
      scene.hidden = scene.id !== `demo-${name}`;
      scene.classList.toggle('scene-enter', !scene.hidden && !reducedMotion.matches);
    });
    $('.product-demo').dataset.demo = name;
  }
  bindTabs('[data-demo-tab]', selectDemo);
  $$('[data-show-demo]').forEach(button => button.addEventListener('click', () => {
    const tab = $(`[data-demo-tab="${button.dataset.showDemo}"]`);
    selectDemo(tab);
    tab.focus({ preventScroll: true });
  }));

  function setSector(tab) {
    const key = tab.dataset.sector;
    const data = sectors[key];
    if (!data) return;
    $$('[data-sector]').forEach(button => {
      const active = button === tab;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    const panel = $('#sector-panel');
    panel.setAttribute('aria-labelledby', tab.id);
    panel.dataset.segment = key;
    const photo = $('#sector-photo');
    photo.src = `/landing-assets/${data.photo}`;
    photo.alt = data.alt;
    $('#sector-photo-label').textContent = data.label;
    $('#sector-kicker').textContent = data.kicker;
    const title = $('#sector-title');
    title.replaceChildren(document.createTextNode(data.title[0]), document.createElement('br'), document.createTextNode(data.title[1]));
    $('#sector-description').textContent = data.description;
    const list = $('#sector-use-cases');
    list.replaceChildren(...data.cases.map((text, index) => {
      const item = document.createElement('li');
      const number = document.createElement('span');
      number.textContent = `0${index + 1}`;
      item.append(number, document.createTextNode(text));
      return item;
    }));
  }
  bindTabs('[data-sector]', setSector);

  const heroKeys = ['barbearia', 'estetica', 'odontologia', 'restaurante', 'fitness'];
  let heroIndex = 0;
  let paused = false;
  let heroVisible = true;
  let heroTimer;
  const motionButton = $('.motion-toggle');
  const heroPhoto = $('#hero-photo');
  function updateHero(key) {
    const data = sectors[key];
    if (!data || !heroPhoto) return;
    heroIndex = heroKeys.indexOf(key);
    $('#hero-word').textContent = data.word;
    $('#hero-word').classList.remove('word-enter');
    if (!reducedMotion.matches) window.requestAnimationFrame(() => $('#hero-word').classList.add('word-enter'));
    heroPhoto.src = `/landing-assets/${data.photo}`;
    heroPhoto.alt = data.alt;
    $('#hero-activity').textContent = data.activity;
    $('.receipt-label').textContent = key === 'restaurante' ? 'ATENDIMENTO COM CONTEXTO' : 'DA CONVERSA PARA A AGENDA';
    $('.hero-image-index').textContent = `0${heroIndex + 1} / 05`;
    $$('[data-hero-sector]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.heroSector === key)));
    $('.hero-visual').dataset.sector = key;
  }
  function scheduleHero() {
    window.clearInterval(heroTimer);
    if (!paused && !reducedMotion.matches && !document.hidden && heroVisible && !navigator.connection?.saveData) {
      heroTimer = window.setInterval(() => updateHero(heroKeys[(heroIndex + 1) % heroKeys.length]), 5600);
    }
  }
  function updateMotionControl() {
    if (!motionButton) return;
    motionButton.disabled = reducedMotion.matches;
    motionButton.setAttribute('aria-pressed', String(paused || reducedMotion.matches));
    motionButton.setAttribute('aria-label', reducedMotion.matches ? 'Troca automática desativada pela preferência de movimento' : paused ? 'Retomar troca de negócios' : 'Pausar troca de negócios');
    $('use', motionButton).setAttribute('href', paused || reducedMotion.matches ? '#i-play' : '#i-pause');
    scheduleHero();
  }
  motionButton?.addEventListener('click', () => { paused = !paused; updateMotionControl(); });
  $$('[data-hero-sector]').forEach(button => button.addEventListener('click', () => {
    paused = true;
    updateHero(button.dataset.heroSector);
    updateMotionControl();
  }));
  reducedMotion.addEventListener?.('change', updateMotionControl);
  document.addEventListener('visibilitychange', scheduleHero);
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      heroVisible = entries.some(entry => entry.isIntersecting);
      scheduleHero();
    }, { threshold: 0.05 });
    observer.observe($('.hero'));
  }
  updateMotionControl();
  window.addEventListener('pagehide', () => window.clearInterval(heroTimer));
  window.addEventListener('pageshow', scheduleHero);

  const video = $('#routine-video');
  const videoButton = $('.video-control');
  function syncVideoControl() {
    const playing = !video.paused && !video.ended;
    videoButton.setAttribute('aria-pressed', String(playing));
    videoButton.setAttribute('aria-label', playing ? 'Pausar vídeo da rotina de trabalho' : 'Reproduzir vídeo da rotina de trabalho');
    $('use', videoButton).setAttribute('href', playing ? '#i-pause' : '#i-play');
  }
  videoButton?.addEventListener('click', async () => {
    try {
      if (video.paused) await video.play();
      else video.pause();
      syncVideoControl();
    } catch {
      $('strong', videoButton).textContent = 'Não foi possível reproduzir. Tente novamente.';
      syncVideoControl();
    }
  });
  video?.addEventListener('play', syncVideoControl);
  video?.addEventListener('pause', syncVideoControl);
  document.addEventListener('visibilitychange', () => { if (document.hidden && video && !video.paused) video.pause(); });
  if ('IntersectionObserver' in window && video) {
    new IntersectionObserver(entries => {
      if (!entries[0].isIntersecting && !video.paused) video.pause();
    }, { threshold: 0.05 }).observe(video);
  }

  $$('.faq-list details').forEach(detail => detail.addEventListener('toggle', () => {
    if (detail.open) $$('.faq-list details').forEach(other => { if (other !== detail) other.open = false; });
  }));
  const privacy = $('#privacy-dialog');
  let privacyTrigger;
  function closePrivacy() {
    if (typeof privacy.close === 'function') privacy.close();
    else privacy.removeAttribute('open');
    privacyTrigger?.focus({ preventScroll: true });
  }
  $$('.privacy-trigger').forEach(button => button.addEventListener('click', () => {
    privacyTrigger = button;
    if (typeof privacy.showModal === 'function') privacy.showModal();
    else privacy.setAttribute('open', '');
  }));
  $$('.dialog-close, .dialog-close-button').forEach(button => button.addEventListener('click', closePrivacy));
  privacy?.addEventListener('click', event => { if (event.target === privacy) closePrivacy(); });
  if ($('#copyright-year')) $('#copyright-year').textContent = String(new Date().getFullYear());
})();
