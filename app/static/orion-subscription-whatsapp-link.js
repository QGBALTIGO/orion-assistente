/* Subscription click-to-chat link and local QR, without any WhatsApp send. */
(function(){
  'use strict';
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const PHONE=/^\+[1-9][0-9]{7,14}$/;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  let active=null;
  const unmount=()=>{active?.revoke();active=null;};
  function mount(bridge){
    unmount();
    if(!bridge?.host||!UUID.test(bridge.tenantId)||typeof bridge.request!=='function')
      throw Error('invalid_whatsapp_link_context');
    const host=bridge.host,base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+
      '/subscription-whatsapp/whatsapp-link';
    const state={alive:true,version:0,busy:false,phone:'',message:'',result:null,error:'',notice:''};
    const current=()=>state.alive&&bridge.canAccess?.()!==false;
    const canWrite=()=>current()&&bridge.canWrite?.()!==false;
    function revoke(){state.alive=false;state.version++;}
    active={revoke};
    function cleanResult(value,phone,message){
      if(!value||value.mime_type!=='image/png'||value.format!=='png'||
          typeof value.url!=='string'||typeof value.image_base64!=='string'||value.image_base64.length>200000||
          !/^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(value.image_base64))return false;
      let link;
      try{link=new URL(value.url);}catch{return false;}
      return link.protocol==='https:'&&link.origin==='https://wa.me'&&
        link.pathname==='/'+phone.slice(1)&&
        link.hash===''&&
        [...link.searchParams.keys()].every(key=>key==='text')&&
        ((message&&link.searchParams.getAll('text').length===1&&
            link.searchParams.get('text')===message.normalize('NFC'))||
          (!message&&!link.search))&&value.url.length<=1800;
    }
    function reset(){state.version++;state.result=null;state.error='';state.notice='';
      host.querySelector('[data-wl-result]')?.remove();host.querySelector('[data-wl-error]')?.remove();
      host.querySelector('[data-wl-notice]')?.remove();}
    function render(){
      if(!current())return;
      host.setAttribute('aria-busy',String(state.busy));
      const lock=state.busy||!canWrite();
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Link e QR do WhatsApp</h1>'+
        '<p>Crie um link direto para conversar com seu número, com mensagem inicial opcional.</p></div></div>'+
        '<div class="os-section-stack">'+
        (state.error?'<div class="os-notice os-error" role="alert" data-wl-error><p>'+esc(state.error)+'</p></div>':'')+
        (state.notice?'<div class="os-notice" role="status" data-wl-notice><p>'+esc(state.notice)+'</p></div>':'')+
        '<form class="os-card os-form" data-wl-form>'+
        '<div class="os-field"><label for="osWlPhone">Número do WhatsApp</label>'+
        '<input type="tel" id="osWlPhone" data-wl-phone autocomplete="tel" inputmode="tel" maxlength="16" '+
        'placeholder="+5567999999999" value="'+esc(state.phone)+'" '+(lock?'disabled':'')+'>'+
        '<span class="os-help">Inclua + e DDI. Exemplo: +5567999999999.</span></div>'+
        '<div class="os-field"><label for="osWlMessage">Mensagem inicial (opcional)</label>'+
        '<textarea id="osWlMessage" data-wl-message rows="4" maxlength="360" '+
        'placeholder="Olá! Gostaria de saber mais sobre os serviços." '+(lock?'disabled':'')+'>'+
        esc(state.message)+'</textarea><span class="os-help">A mensagem será preenchida no WhatsApp, mas nunca enviada automaticamente.</span></div>'+
        '<div class="os-form-actions"><button type="submit" class="os-button" data-wl-generate '+(lock?'disabled':'')+'>'+
        (state.busy?'Gerando…':'Gerar link e QR')+'</button></div></form>'+
        (state.result?'<section class="os-card os-form" data-wl-result>'+
          '<header class="os-card-header"><div><h2>Pronto para compartilhar</h2><p>O QR abre o mesmo link. A pessoa decide se envia a mensagem.</p></div></header>'+
          '<div class="os-field"><label for="osWlResult">Seu link</label>'+
          '<input id="osWlResult" data-wl-output readonly value="'+esc(state.result.url)+'"></div>'+
          '<div class="os-actions"><button type="button" class="os-button os-outline" data-wl-copy>Copiar link</button>'+
          '<a class="os-button os-outline" href="'+esc(state.result.url)+'" target="_blank" rel="noopener noreferrer">Abrir WhatsApp</a>'+
          '<button type="button" class="os-button os-outline" data-wl-download>Baixar QR em PNG</button></div>'+
          '<div><img src="data:image/png;base64,'+state.result.image_base64+'" width="280" height="280" '+
          'alt="QR code do seu link de WhatsApp" data-wl-qr></div></section>':'')+'</div>';
      window.OrionFields?.refresh(host);
    }
    async function generate(event){
      event.preventDefault();if(!canWrite()||state.busy)return;
      if(!PHONE.test(state.phone)){state.error='Informe um número válido com +, DDI e DDD.';state.result=null;render();return;}
      if(state.message.length>360||new TextEncoder().encode(state.message).length>1200){
        state.error='Reduza a mensagem antes de gerar o link.';state.result=null;render();return;}
      const version=++state.version;
      state.busy=true;state.result=null;state.error='';state.notice='';render();
      try{
        const data=await bridge.request(base+'/generate','POST',{phone:state.phone,message:state.message});
        if(!current()||version!==state.version)return;
        if(!cleanResult(data,state.phone,state.message))throw Error('invalid_whatsapp_link_response');
        state.result=data;
      }catch{
        if(current()&&version===state.version)state.error='Não foi possível gerar o link e o QR agora.';
      }finally{if(current()&&version===state.version){state.busy=false;render();}}
    }
    host.addEventListener('submit',event=>{
      if(event.target.matches('[data-wl-form]'))void generate(event);
    });
    host.addEventListener('input',event=>{
      if(!current()||state.busy)return;
      if(event.target.matches('[data-wl-phone]')){state.phone=event.target.value;reset();}
      if(event.target.matches('[data-wl-message]')){state.message=event.target.value;reset();}
    });
    host.addEventListener('click',async event=>{
      const action=event.target.closest('[data-wl-copy],[data-wl-download]');
      if(!action||!current()||!state.result||state.busy)return;
      if(action.hasAttribute('data-wl-copy')){
        try{
          if(!window.navigator.clipboard?.writeText)throw Error('clipboard_unavailable');
          await window.navigator.clipboard.writeText(state.result.url);
          if(!current())return;
          state.notice='Link copiado para a área de transferência.';
        }catch{state.notice='Selecione o link acima e copie manualmente.';}
        render();
      }else{
        const link=host.ownerDocument.createElement('a');
        link.href='data:image/png;base64,'+state.result.image_base64;
        link.download='orion-whatsapp-qr.png';link.rel='noopener';
        link.click();
      }
    });
    render();
    return Promise.resolve();
  }
  window.OrionSubscriptionWhatsAppLink={mount,unmount,clearContext:unmount};
})();
