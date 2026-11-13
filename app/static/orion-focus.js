/* Orion input-modality tracking.
 * Keep keyboard/screen-reader focus visible; avoid persistent touch focus
 * rings on action buttons and programmatically focused dialog headings.
 * No timers, storage, network, or focus changes.
 */
(() => {
  'use strict';
  const root = document.documentElement;
  if (root.hasAttribute('data-orion-focus-initialized')) return;
  root.setAttribute('data-orion-focus-initialized', 'true');

  const pointer = () => root.setAttribute('data-orion-input', 'pointer');
  const keyboard = event => {
    if (event.isComposing || ['Shift', 'Control', 'Alt', 'Meta', 'Dead', 'Process'].includes(event.key)) return;
    root.setAttribute('data-orion-input', 'keyboard');
  };

  window.addEventListener('pointerdown', pointer, true);
  // Fallback for older mobile WebKit and legacy synthetic mouse interaction.
  window.addEventListener('touchstart', pointer, {capture: true, passive: true});
  window.addEventListener('mousedown', pointer, true);
  window.addEventListener('keydown', keyboard, true);
})();
