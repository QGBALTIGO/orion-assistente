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
  const steps = {
    qr:[
      'Abra o WhatsApp ou WhatsApp Business no celular que deseja conectar.',
      'No iPhone, abra Configurações. No Android, toque no menu ⋮.',
      'Entre em Dispositivos conectados.',
      'Toque em Conectar dispositivo.',
      'Mantenha o WhatsApp aberto, com a câmera pronta para escanear.',
      'No Orion, toque em Mostrar QR. Deixe esta tela aberta em outro aparelho.',
      'Aponte a câmera do WhatsApp para o QR Code e aguarde o status Conectado no Orion.',
    ],
    code:[
      'Abra o WhatsApp ou WhatsApp Business no celular que deseja conectar.',
      'No iPhone, abra Configurações. No Android, toque no menu ⋮.',
      'Entre em Dispositivos conectados.',
      'Toque em Conectar dispositivo.',
      'Escolha Conectar com número de telefone.',
      'No Orion, informe esse número com DDD e toque em Gerar código.',
      'Digite no WhatsApp o código gerado aqui e aguarde o status Conectado no Orion.',
    ],
  };
  function instructions(method) {
    if (!Object.hasOwn(steps,method)) throw Error('Método de conexão inválido.');
    return '<ol class="os-connect-guide os-help">'+steps[method].map(text=>'<li>'+escape(text)+'</li>').join('')+'</ol>';
  }
  function methods(method,attribute='data-method',disabled=false) {
    if (!['data-method','data-qc-method'].includes(attribute)) throw Error('Seletor de método inválido.');
    return ['code','qr'].map(value=>'<button type="button" class="os-service-option" '+attribute+'="'+value+
      '" aria-pressed="'+String(method===value)+'"'+(disabled?' disabled':'')+
      '><span class="os-actions"><svg class="os-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[value]+
      '</svg><strong>'+(value==='code'?'Código':'QR Code')+'</strong></span></button>').join('');
  }
  function normalizeCode(value) {
    if(typeof value!=='string'||!/^[A-Z0-9]{4}-?[A-Z0-9]{4}$/i.test(value)) throw Error('O WhatsApp não retornou um código válido.');
    const raw=value.replace('-','').toUpperCase();
    return raw.slice(0,4)+'-'+raw.slice(4);
  }
  function seconds(milliseconds) {
    const n=Math.max(0,Math.ceil(Number(milliseconds)/1000));
    return Number.isFinite(n)&&n>0?n+' s':'expirado';
  }
  window.OrionConnectView=Object.freeze({instructions,methods,normalizeCode,seconds});
})();
