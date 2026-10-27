/* Conversation workspace using the panel's authenticated tenant bridge. */
(function(){
  'use strict';
  let active=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  function unmount(){
    if(!active)return;
    const state=active;active=null;state.alive=false;state.controller.abort();
    window.OrionConversations?.unmount();
    state.host.inert=false;state.host.removeAttribute('aria-busy');
  }
  async function mount({host,request,tenantId,canWrite=false}){
    unmount();if(!host)return;
    const state={host,alive:true,controller:new AbortController(),loading:false};
    active=state;
    const current=()=>active===state&&state.alive&&host.isConnected;
    const shell=html=>{host.innerHTML='<div class="os-inbox os-inbox-conversations-only">'+html+'</div>';};
    async function load(){
      if(!current()||state.loading)return;
      state.loading=true;host.setAttribute('aria-busy','true');
      shell('<p class="os-inbox-notice" role="status">Carregando conversas…</p>');
      try{
        const capability=await request('/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/inbox/capabilities');
        if(!current())return;
        if(capability?.central_enabled!==true){
          shell('<section class="os-card os-inbox-empty"><strong>Conversas indisponíveis</strong><p>A central de conversas ainda não está disponível para esta empresa.</p></section>');
          return;
        }
        if(typeof window.OrionConversations?.mount!=='function')throw Error('Não foi possível abrir as conversas. Atualize a página e tente novamente.');
        shell('<div data-inbox-central></div>');
        await window.OrionConversations.mount({
          host:host.querySelector('[data-inbox-central]'),request,tenantId,canWrite,toolsEnabled:false
        });
      }catch(error){
        if(current())shell('<section class="os-card os-inbox-empty"><p role="alert">'+
          esc(error?.message||'Não foi possível carregar as conversas.')+'</p>'+
          '<button type="button" class="os-button os-outline" data-inbox-action="retry">Tentar novamente</button></section>');
      }finally{
        state.loading=false;
        if(current())host.removeAttribute('aria-busy');
      }
    }
    host.addEventListener('click',event=>{
      if(event.target.closest('[data-inbox-action="retry"]'))void load();
    },{signal:state.controller.signal});
    await load();
  }
  window.OrionInbox={mount,unmount};
})();
