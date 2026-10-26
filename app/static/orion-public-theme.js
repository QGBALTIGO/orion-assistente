/* Theme preference shared with the Orion panel; no inline script (strict public CSP). */
(function () {
  'use strict';
  const root = document.documentElement;
  const storageKey = 'orion-theme';
  const allowed = ['light', 'dark', 'auto'];
  const media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  const labels = { light: 'Claro', dark: 'Escuro', auto: 'Sistema' };
  function stored() {
    try {
      const value = window.localStorage.getItem(storageKey);
      return allowed.includes(value) ? value : 'auto';
    } catch (_) { return 'auto'; }
  }
  function apply(value, persist) {
    const preference = allowed.includes(value) ? value : 'auto';
    const effective = preference === 'auto' ? (media && media.matches ? 'dark' : 'light') : preference;
    root.dataset.themePreference = preference;
    root.dataset.theme = effective;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.content = effective === 'dark' ? '#080d18' : '#f5f8fc';
    const toggle = document.getElementById('public-theme-toggle');
    if (toggle) toggle.setAttribute('aria-label', 'Aparência: ' + labels[preference] + '. Escolher tema');
    document.querySelectorAll('[data-public-theme]').forEach(option => {
      option.setAttribute('aria-pressed', String(option.dataset.publicTheme === preference));
    });
    if (persist) {
      try { window.localStorage.setItem(storageKey, preference); } catch (_) { /* Private browsing */ }
    }
  }
  function closeMenu(restoreFocus) {
    const menu = document.getElementById('public-theme-menu');
    const toggle = document.getElementById('public-theme-toggle');
    if (!menu || !toggle || menu.hidden) return;
    menu.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) toggle.focus();
  }
  function mount() {
    apply(stored(), false);
    const wrapper = document.getElementById('public-theme-control');
    const toggle = document.getElementById('public-theme-toggle');
    const menu = document.getElementById('public-theme-menu');
    if (!wrapper || !toggle || !menu) return;
    toggle.addEventListener('click', () => {
      menu.hidden = !menu.hidden;
      toggle.setAttribute('aria-expanded', String(!menu.hidden));
      if (!menu.hidden) menu.querySelector('[aria-pressed="true"]')?.focus();
    });
    menu.querySelectorAll('[data-public-theme]').forEach(option => {
      option.addEventListener('click', () => {
        apply(option.dataset.publicTheme, true);
        closeMenu(true);
      });
    });
    document.addEventListener('click', event => {
      if (!wrapper.contains(event.target)) closeMenu(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !menu.hidden) {
        event.preventDefault();
        closeMenu(true);
      }
    });
  }
  apply(stored(), false);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else mount();
  if (media) {
    const update = () => { if (root.dataset.themePreference === 'auto') apply('auto', false); };
    if (typeof media.addEventListener === 'function') media.addEventListener('change', update);
    else if (typeof media.addListener === 'function') media.addListener(update);
  }
  window.addEventListener('storage', event => {
    if (event.key === storageKey || event.key === null) apply(stored(), false);
  });
})();
