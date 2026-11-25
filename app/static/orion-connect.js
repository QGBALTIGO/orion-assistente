/* Presentation shared by the platform and tenant WhatsApp connectors.
 * No network, storage, credentials or connection commands belong here. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icons = {
    code:'<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M10 5h4m-3 14h2"/>',
    qr:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="3" y="15" width="6" height="6" rx="1"/><path d="M15 15h2v2h4v4h-6v-2m6-6v2"/>',
  };
  function instructions(method) {
    if (!['qr','code'].includes(method)) throw Error('Método de conexão inválido.');
    const action = method==='qr'?'escaneie o QR Code.':'escolha Conectar com número de telefone e digite o código.';
    return '<p class="os-help os-connect-steps">No WhatsApp do celular, abra <strong>Aparelhos conectados</strong>, toque em <strong>Conectar um aparelho</strong> e '+action+'</p>';
  }
  function methods(method,attribute='data-method',disabled=false) {
    if (!['data-method','data-qc-method','data-sd-method'].includes(attribute)) throw Error('Seletor de método inválido.');
    return ['qr','code'].map(value=>'<button type="button" class="wizard-method'+(method===value?' active':'')+'" '+attribute+'="'+value+
      '" aria-pressed="'+String(method===value)+'"'+(disabled?' disabled':'')+
      '><span class="wizard-method-icon"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[value]+
      '</svg></span><strong>'+(value==='code'?'Código pelo número':'QR Code')+'</strong><span>'+(value==='code'?'Use o mesmo celular':'Escaneie pelo WhatsApp')+'</span></button>').join('');
  }
  function heading(codeAvailable=true){return codeAvailable?'<div class="wizard-heading"><h2>Escolha como conectar</h2><p>Use o QR Code em outro aparelho ou vincule pelo número neste celular.</p></div>':'<div class="wizard-heading"><h2>Conectar por QR Code</h2><p>Use o WhatsApp em outro aparelho para escanear o QR Code desta tela.</p></div>';}
  function normalizeCode(value) {
    if(typeof value!=='string'||!/^[A-Z0-9]{4}-?[A-Z0-9]{4}$/i.test(value)) throw Error('O WhatsApp não retornou um código válido.');
    const raw=value.replace('-','').toUpperCase();
    return raw.slice(0,4)+'-'+raw.slice(4);
  }
  function seconds(milliseconds) {
    const n=Math.max(0,Math.ceil(Number(milliseconds)/1000));
    return Number.isFinite(n)&&n>0?n+' s':'expirado';
  }
  window.OrionConnectView=Object.freeze({heading,instructions,methods,normalizeCode,seconds});
})();
