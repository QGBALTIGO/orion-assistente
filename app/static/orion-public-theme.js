/* Independent public-site theme: follow the device unless the visitor switches. */
(function () {
  'use strict';
  const root = document.documentElement;
  const key = 'orion-public-theme';
  const allowed = ['auto', 'light', 'dark'];
  const media = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const systemTheme = () => media && media.matches ? 'dark' : 'light';

  function read() {
    try {
      const preference = window.localStorage.getItem(key);
      return allowed.includes(preference) ? preference : 'auto';
    } catch (_) { return 'auto'; }
  }

  function apply(value, persist) {
    const preference = allowed.includes(value) ? value : 'auto';
    const current = preference === 'auto' ? systemTheme() : preference;
    root.dataset.themePreference = preference;
    root.dataset.theme = current;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = current === 'dark' ? '#080d18' : '#f5f8fc';
    const button = document.getElementById('public-theme-toggle');
    if (button) {
      const nextLabel = current === 'dark' ? 'claro' : 'escuro';
      const currentLabel = current === 'dark' ? 'escuro' : 'claro';
      button.setAttribute('aria-label', 'Tema ' + currentLabel +
        (preference === 'auto' ? ' (automático)' : ' (manual)') + '. Ativar modo ' + nextLabel);
      button.title = 'Ativar modo ' + nextLabel;
    }
    if (persist) {
      try { window.localStorage.setItem(key, preference); } catch (_) { /* Storage unavailable */ }
    }
  }

  function toggle() {
    const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
    // When toggling back to the device's appearance, restore automatic mode.
    apply(next === systemTheme() ? 'auto' : next, true);
  }

  function mount() {
    apply(read(), false);
    document.getElementById('public-theme-toggle')?.addEventListener('click', toggle);
  }

  // Apply the device theme before CSS and body paint.
  apply(read(), false);
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();

  if (media) {
    const refresh = () => { if (root.dataset.themePreference === 'auto') apply('auto', false); };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', refresh);
    else if (typeof media.addListener === 'function') media.addListener(refresh);
  }
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(read(), false);
  });
})();
