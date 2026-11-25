(() => {
  if (window.parent === window) {
    location.replace('/painel');
    return;
  }
  const origin = location.origin;
  const root = document.documentElement;
  const app = document.querySelector('#appView');
  const loading = document.querySelector('#loginView');
  const dialog = document.querySelector('#dialog');
  const send = (kind, payload = {}) => parent.postMessage({from: 'orion-affiliates', kind, ...payload}, origin);
  const readHeight = () => send('size', {height: Math.ceil((app.hidden ? loading : app).getBoundingClientRect().bottom + scrollY)});
  const readRoute = () => send('route', {view: location.hash.slice(1) || 'dashboard'});
  let theme = null;
  const applyTheme = () => {
    if (theme && root.dataset.theme !== theme) root.dataset.theme = theme;
  };
  new MutationObserver(applyTheme).observe(root, {attributes: true, attributeFilter: ['data-theme']});
  addEventListener('message', event => {
    if (event.origin !== origin || event.source !== parent || event.data?.from !== 'orion-panel') return;
    if (['dark', 'light'].includes(event.data.theme)) {
      theme = event.data.theme;
      applyTheme();
    }
    if (typeof event.data.view === 'string' && /^[a-z]+$/.test(event.data.view) && location.hash !== '#' + event.data.view) {
      location.replace('#' + event.data.view);
    }
  });
  const size = new ResizeObserver(readHeight);
  size.observe(app);
  size.observe(loading);
  new MutationObserver(() => {
    if (dialog.open) send('dialog', {offset: dialog.getBoundingClientRect().top + scrollY});
  }).observe(dialog, {attributes: true, attributeFilter: ['open']});
  addEventListener('hashchange', readRoute);
  send('ready');
})();
