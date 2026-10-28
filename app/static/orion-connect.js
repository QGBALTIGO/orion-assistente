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
      ['Abra o WhatsApp','Abra o WhatsApp normal ou o WhatsApp Business no celular.'],
      ['Vá às configurações','No iPhone, abra Configurações. No Android, toque no menu ⋮.'],
      ['Entre em Dispositivos conectados','Veja os dispositivos vinculados à sua conta.'],
      ['Confira conexões anteriores','Se houver uma sessão antiga do Orion, revise antes de adicionar outra.'],
      ['Toque em Conectar dispositivo','Seu WhatsApp abrirá a tela para escanear um QR Code.'],
      ['Deixe o celular pronto','Mantenha o WhatsApp aberto, com a câmera preparada.'],
      ['Pronto para escanear','Continue abaixo para mostrar o QR Code temporário.'],
    ],
    code:[
      ['Abra o WhatsApp','Abra o WhatsApp normal ou o WhatsApp Business no celular.'],
      ['Vá às configurações','No iPhone, abra Configurações. No Android, toque no menu ⋮.'],
      ['Entre em Dispositivos conectados','Veja os dispositivos já vinculados à sua conta.'],
      ['Confira conexões anteriores','Se houver uma sessão antiga do Orion, confira antes de adicionar outra.'],
      ['Toque em Conectar dispositivo','Na tela de dispositivos conectados, escolha Conectar dispositivo.'],
      ['Escolha Conectar com número','Toque na opção Conectar com número de telefone.'],
      ['Pronto para gerar o código','Continue para informar seu número e gerar o código que será digitado no WhatsApp.'],
    ],
  };
  function instructions(method) {
    if (!Object.hasOwn(steps,method)) throw Error('Método de conexão inválido.');
    const phone='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/></svg>';
    const check='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.7" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
    return '<div class="wizard-timeline" role="list" aria-label="Todos os passos para conectar o WhatsApp">'+steps[method].map(([title,description],index)=>{
      const edge=index===0||index===6;
      return '<div class="wizard-timeline-step" role="listitem"><span class="wizard-timeline-dot'+(edge?' edge':'')+'">'+(edge?phone:check)+'</span><div class="wizard-timeline-copy"><small>PASSO '+(index+1)+'</small><strong>'+escape(title)+'</strong><p>'+escape(description)+'</p></div></div>';
    }).join('')+'</div>';
  }
  function methods(method,attribute='data-method',disabled=false) {
    if (!['data-method','data-qc-method'].includes(attribute)) throw Error('Seletor de método inválido.');
    return ['qr','code'].map(value=>'<button type="button" class="wizard-method'+(method===value?' active':'')+'" '+attribute+'="'+value+
      '" aria-pressed="'+String(method===value)+'"'+(disabled?' disabled':'')+
      '><span class="wizard-method-icon"><svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+icons[value]+
      '</svg></span><strong>'+(value==='code'?'Código':'QR Code')+'</strong><span>'+(value==='code'?'Digite no seu telefone':'Escaneie com seu telefone')+'</span></button>').join('');
  }
  function heading(){return '<div class="wizard-heading"><h2>Escolha como conectar</h2><p>No próprio celular, use o código. Se estiver em outro aparelho, escaneie o QR Code.</p></div>';}
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
