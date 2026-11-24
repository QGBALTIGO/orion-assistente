/* Orion click-to-chat links: complete messages and first-party QR generation. */
(()=>{
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const countries=[['55','Brasil (+55)'],['595','Paraguai (+595)'],['351','Portugal (+351)'],['1','Estados Unidos / Canadá (+1)'],['54','Argentina (+54)'],['598','Uruguai (+598)'],['56','Chile (+56)'],['34','Espanha (+34)'],['44','Reino Unido (+44)'],['other','Outro país']];
  const notice=(message,error=false)=>'<div class="os-notice'+(error?' os-error':'')+'" role="'+(error?'alert':'status')+'"><p>'+esc(message)+'</p></div>';
  let active=null;
  function unmount(){const old=active;active=null;old?.destroy();}
  function clearContext(){active?.revoke();}
  function mount(bridge){
    unmount();const host=bridge.host,base='/api/v1/tenants/'+bridge.tenantId+'/subscription-whatsapp/whatsapp-link';
    if(!host||!uuid(bridge.tenantId))throw Error('invalid_whatsapp_link_context');
    const state={alive:true,revoked:false,epoch:0,busy:false,country:'55',ddi:'',phone:'',message:'',result:null,error:'',notice:''};let controller,abort=null;
    const current=()=>state.alive&&!state.revoked&&active===controller&&host.isConnected&&bridge.canAccess?.()!==false;
    function revoke(){state.revoked=true;state.epoch++;abort?.abort();state.result=null;state.phone='';state.message='';host.setAttribute('aria-busy','false');host.innerHTML=notice('O acesso a esta empresa mudou. Abra o painel novamente.');}
    function phone(){const value=state.phone.trim();if(value.startsWith('+')||value.startsWith('00'))return value;const ddi=state.country==='other'?state.ddi.replace(/^\+/,''):state.country;return '+'+ddi+value;}
    function canGenerate(){return current()&&bridge.canWrite?.()!==false&&!state.busy&&!!state.phone.trim()&&[...state.message].length<=1000&&(state.country!=='other'||/^[1-9][0-9]{0,2}$/.test(state.ddi.replace(/^\+/,'')));}
    function validate(value){
      const expected=phone().replace(/[ ().\-]/g,'').replace(/^00/,'+');
      const url=new URL(value?.url||'invalid',location.origin);
      if(!/^\+[1-9]\d{7,14}$/.test(value?.phone||'')||value.phone!==expected||url.origin!=='https://wa.me'||url.pathname!=='/'+value.phone.slice(1)||url.username||url.password||url.hash||(url.searchParams.get('text')||'')!==state.message||[...url.searchParams.keys()].some(key=>key!=='text'))throw Error('Não foi possível confirmar o link gerado.');
      if(value.qr!==null&&(!value.qr||typeof value.qr.svg!=='string'||value.qr.svg.length>2_000_000||!/^<svg\s/.test(value.qr.svg)||typeof value.qr.png_base64!=='string'||value.qr.png_base64.length>300_000||!/^[A-Za-z0-9+/]+={0,2}$/.test(value.qr.png_base64)||!Number.isInteger(value.qr.size)||value.qr.size<100||value.qr.size>1000))throw Error('Não foi possível confirmar o QR Code.');
      if(value.qr===null&&typeof value.qr_notice!=='string')throw Error('Não foi possível confirmar o QR Code.');return value;
    }
    function preview(){
      const value=state.result;
      if(!value)return '<p class="os-help">Preencha o telefone e gere seu link para visualizar e baixar o QR Code.</p>';
      return '<div class="os-field"><label for="osWlResult">Link do WhatsApp</label><input id="osWlResult" type="text" readonly value="'+esc(value.url)+'" autocomplete="off"></div><div class="os-form-actions"><button type="button" class="os-button os-outline" data-wl-action="copy">Copiar</button><a class="os-button os-outline" href="'+esc(value.url)+'" target="_blank" rel="noopener noreferrer" data-wl-open>Abrir link</a></div>'+(value.qr?'<div class="os-whatsapp-qr"><img src="data:image/png;base64,'+value.qr.png_base64+'" width="240" height="240" alt="QR Code para abrir o mesmo link do WhatsApp"><div class="os-form-actions"><button type="button" class="os-button os-outline" data-wl-action="svg">Baixar QR (SVG)</button><button type="button" class="os-button os-outline" data-wl-action="png">Baixar QR (PNG)</button></div></div>':notice(value.qr_notice));
    }
    function render(){
      if(!current())return;host.setAttribute('aria-busy',String(state.busy));
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Link e QR do WhatsApp</h1><p>Crie um link com uma mensagem pronta e compartilhe seu QR Code.</p></div></div><div class="os-section-stack">'+(state.error?notice(state.error,true):'')+(state.notice?notice(state.notice):'')+'<div class="os-whatsapp-link-layout"><form class="os-card os-form" data-wl-form><header class="os-card-header"><div><h2>Crie seu link</h2></div></header><div class="os-form-grid"><div class="os-field"><label for="osWlCountry">País</label><select id="osWlCountry" data-orion-field="native" '+(state.busy?'disabled':'')+'>'+countries.map(row=>'<option value="'+row[0]+'" '+(state.country===row[0]?'selected':'')+'>'+row[1]+'</option>').join('')+'</select></div>'+(state.country==='other'?'<div class="os-field"><label for="osWlDdi">Código do país (DDI)</label><input id="osWlDdi" type="text" inputmode="tel" maxlength="4" placeholder="Ex.: 49" value="'+esc(state.ddi)+'" '+(state.busy?'disabled':'')+'></div>':'')+'<div class="os-field"><label for="osWlPhone">Telefone do WhatsApp</label><input id="osWlPhone" type="tel" inputmode="tel" maxlength="80" placeholder="DDD e número" value="'+esc(state.phone)+'" '+(state.busy?'disabled':'')+' required><small class="os-help">Você também pode colar o número completo com + e DDI.</small></div></div><div class="os-field"><label for="osWlMessage">Mensagem (opcional)</label><textarea id="osWlMessage" rows="5" maxlength="2000" '+(state.busy?'disabled':'')+' placeholder="Olá! Quero saber mais.">'+esc(state.message)+'</textarea><small class="os-help" id="osWlCount">'+[...state.message].length+' / 1.000 caracteres</small></div><div class="os-form-actions"><button type="submit" class="os-button" data-wl-generate '+(!canGenerate()?'disabled':'')+'>'+(state.busy?'Gerando…':'Gerar link e QR')+'</button></div></form><section class="os-card os-form" aria-labelledby="osWlPreview"><header class="os-card-header"><div><h2 id="osWlPreview">Visualização</h2></div></header><div data-wl-preview>'+preview()+'</div></section></div></div>';
      window.OrionFields?.refresh(host);
    }
    async function generate(){
      if(!canGenerate())return;abort?.abort();abort=new AbortController();const epoch=++state.epoch;state.busy=true;state.result=null;state.error='';state.notice='';render();
      try{const value=await bridge.request(base+'/generate','POST',{phone:phone(),message:state.message},{signal:abort.signal});if(!current()||epoch!==state.epoch)return;state.result=validate(value);}
      catch(error){if(current()&&epoch===state.epoch){if([401,403].includes(error.status))revoke();else if(error.name!=='AbortError')state.error=error?.detail?.message||error.message||'Confira o telefone e a mensagem e tente novamente.';}}
      finally{if(current()&&epoch===state.epoch){state.busy=false;render();}}
    }
    function changed(){state.result=null;state.error='';state.notice='';host.querySelectorAll('.os-section-stack > .os-notice').forEach(el=>el.remove());host.querySelector('[data-wl-preview]').innerHTML=preview();host.querySelector('[data-wl-generate]').disabled=!canGenerate();host.querySelector('#osWlCount').textContent=[...state.message].length+' / 1.000 caracteres';}
    function input(event){if(!current()){revoke();return;}if(state.busy)return;const key={osWlPhone:'phone',osWlMessage:'message',osWlDdi:'ddi'}[event.target.id];if(key){state[key]=event.target.value;changed();}}
    function change(event){if(event.target.id!=='osWlCountry'||state.busy)return;if(!current()){revoke();return;}state.country=event.target.value;state.result=null;state.error='';state.notice='';render();}
    function download(kind){const value=state.result?.qr;if(!value)return;let data,type;if(kind==='svg'){data=value.svg;type='image/svg+xml';}else{data=Uint8Array.from(atob(value.png_base64),char=>char.charCodeAt(0));type='image/png';}const url=URL.createObjectURL(new Blob([data],{type})),link=document.createElement('a');link.href=url;link.download='orion-whatsapp-qr.'+kind;host.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
    async function click(event){const el=event.target.closest('[data-wl-action]');if(!el||el.disabled||!host.contains(el))return;if(!current()){revoke();return;}const action=el.dataset.wlAction;if(action==='svg'||action==='png')download(action);else if(action==='copy'&&state.result){try{await navigator.clipboard.writeText(state.result.url);if(current()){state.notice='Link copiado.';render();}}catch{if(current()){state.error='Não foi possível copiar automaticamente. Selecione e copie o link exibido.';render();}}}}
    function submit(event){if(!event.target.matches('[data-wl-form]'))return;event.preventDefault();void generate();}
    function destroy(){state.alive=false;state.epoch++;abort?.abort();state.result=null;state.phone='';state.message='';for(const [name,fn] of [['click',click],['change',change],['input',input],['submit',submit]])host.removeEventListener(name,fn);host.replaceChildren();}
    controller={ready:Promise.resolve(),destroy,revoke,getState:()=>({busy:state.busy,hasResult:!!state.result})};active=controller;for(const [name,fn] of [['click',click],['change',change],['input',input],['submit',submit]])host.addEventListener(name,fn);render();return controller;
  }
  window.OrionSubscriptionWhatsappLink=Object.freeze({mount,unmount,clearContext});
})();
