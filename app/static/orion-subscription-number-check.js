/* Number verification uses one existing Orion WhatsApp session; it never sends a message. */
(function(){
  'use strict';
  let active=null;
  const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const E164=/^\+[1-9][0-9]{7,14}$/;
  const escape=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const stateLabels={registered:'Cadastrado',not_registered:'Não cadastrado',unknown:'Não confirmado'};
  const stateClasses={registered:'os-success',not_registered:'os-neutral',unknown:'os-warning'};
  const deviceStatuses={connected:'Conectado',disconnected:'Desconectado',stopped:'Parado',needs_attention:'Precisa de atenção',connecting:'Conectando',error:'Indisponível',disabled:'Desativado'};
  function unmount(){active?.revoke();active=null;}
  function mount(bridge){
    unmount();
    if(!bridge?.host||!UUID.test(bridge.tenantId)||typeof bridge.request!=='function')throw Error('invalid_number_check_context');
    const host=bridge.host;
    const state={active:true,busy:false,options:null,devices:[],deviceId:'',input:'',confirmed:false,
      results:null,error:'',revision:0};
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/number-check';
    const alive=()=>state.active&&bridge.canAccess?.()!==false;
    const editable=()=>alive()&&bridge.canWrite?.()!==false;
    function revoke(){state.active=false;state.revision++;}
    active={revoke};
    function devicesValid(value){
      return value&&Array.isArray(value.devices)&&value.devices.length<=50&&
        value.devices.every(row=>row&&UUID.test(row.id)&&typeof row.name==='string'&&
          typeof row.available==='boolean'&&typeof row.status==='string')&&
        value.limits?.per_request===5&&value.limits?.per_hour===30;
    }
    function resultsValid(value,phones){
      return value&&UUID.test(value.device_id)&&value.device_id===state.deviceId&&
        value.total===phones.length&&Array.isArray(value.items)&&value.items.length===phones.length&&
        value.items.every((row,index)=>row&&row.phone===phones[index]&&
          Object.hasOwn(stateLabels,row.status));
    }
    function parsePhones(value){
      const numbers=String(value).split(/[\s,;]+/).filter(Boolean).map(row=>row.startsWith('+')?row:'+'+row);
      if(!numbers.length||numbers.length>5||numbers.some(row=>!E164.test(row))||
          new Set(numbers).size!==numbers.length)
        throw Error('Informe de 1 a 5 números diferentes com DDI e DDD. Exemplo: +5567999999999.');
      return numbers;
    }
    function render(){
      if(!alive())return;
      const focus=host.contains(document.activeElement)&&document.activeElement?.matches('[data-nc-device]')?'[data-nc-device]':null;
      host.setAttribute('aria-busy',String(state.busy));
      const blocked=state.busy||!editable(),hasConnection=state.devices.some(row=>row.id===state.deviceId&&row.available);
      const options=state.devices.map(row=>'<option value="'+escape(row.id)+'" '+(row.id===state.deviceId?'selected':'')+
        (!row.available?' disabled':'')+'>'+escape(row.name)+' · '+escape(deviceStatuses[row.status]||'Indisponível')+'</option>').join('');
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Verificador de números WhatsApp</h1>'+
        '<p>Consulte números autorizados, sem enviar mensagens nem salvar novos contatos.</p></div></div><div class="os-section-stack">'+
        (state.error?'<div class="os-notice os-error" role="alert"><p>'+escape(state.error)+'</p></div>':'')+
        '<form class="os-form os-section-stack" data-nc-form><section class="os-card os-form">'+
        '<div class="os-form-grid"><div class="os-field"><label for="osNcDevice">Dispositivo conectado</label>'+
        '<select id="osNcDevice" data-nc-device data-orion-field="native" '+(blocked?'disabled':'')+'>'+
        '<option value="">Selecione um dispositivo</option>'+options+'</select></div></div>'+
        '<div class="os-field"><label for="osNcPhones">Números para verificar</label>'+
        '<textarea id="osNcPhones" data-nc-phones rows="5" maxlength="200" placeholder="+5567999999999&#10;+595991234567" '+
        (blocked?'disabled':'')+'>'+escape(state.input)+'</textarea>'+
        '<span class="os-help">Até 5 números por consulta, um por linha, com DDI. Limite de 30 verificações por hora neste dispositivo.</span></div>'+
        '<label class="os-check"><input type="checkbox" data-nc-confirm '+(state.confirmed?'checked':'')+' '+(blocked?'disabled':'')+'>'+
        '<span>Tenho autorização para verificar esses números.</span></label>'+
        '<div class="os-form-actions"><button type="submit" class="os-button" data-nc-check '+
        (blocked||!hasConnection||!state.confirmed?'disabled':'')+'>'+
        (state.busy?'Verificando…':'Verificar números')+'</button></div>'+
        '<p class="os-help">“Não confirmado” significa que o WhatsApp não forneceu prova suficiente. A consulta não garante que a pessoa aceitará mensagens.</p>'+
        '</section></form>'+
        (state.results?'<section class="os-card" data-nc-results><header class="os-card-header"><div><h2>Resultado da verificação</h2>'+
        '<p>'+state.results.length+' números consultados no dispositivo escolhido.</p></div></header>'+
        '<div class="os-table-scroll"><table class="os-table"><thead><tr><th scope="col">WhatsApp</th><th scope="col">Status</th></tr></thead><tbody>'+
        state.results.map(row=>'<tr><td>'+escape(row.phone)+'</td><td><span class="os-badge '+
          stateClasses[row.status]+'">'+stateLabels[row.status]+'</span></td></tr>').join('')+
        '</tbody></table></div></section>':'')+'</div>';
      window.OrionFields?.refresh(host);
      if(focus){const next=host.querySelector(focus);if(next&&!next.disabled)next.focus({preventScroll:true});}
    }
    async function load(){
      const revision=++state.revision;
      state.busy=true;state.error='';render();
      try{
        const data=await bridge.request(base+'/options','GET');
        if(!alive()||revision!==state.revision)return;
        if(!devicesValid(data))throw Error('Resposta de dispositivos inválida.');
        state.options=data;state.devices=data.devices;
        if(!state.devices.some(row=>row.id===state.deviceId&&row.available))
          state.deviceId=state.devices.filter(row=>row.available).length===1?
            state.devices.find(row=>row.available).id:'';
      }catch{
        if(alive()&&revision===state.revision)state.error='Não foi possível consultar seus dispositivos. Atualize o painel.';
      }finally{
        if(alive()&&revision===state.revision){state.busy=false;render();}
      }
    }
    async function verify(event){
      event.preventDefault();
      if(!editable()||state.busy)return;
      let phones;
      try{
        phones=parsePhones(state.input);
        if(!state.confirmed||!state.devices.some(row=>row.id===state.deviceId&&row.available))
          throw Error('Selecione um dispositivo conectado e confirme a autorização.');
      }catch(error){state.error=error.message;state.results=null;render();return;}
      const revision=++state.revision;
      state.busy=true;state.error='';state.results=null;render();
      try{
        const result=await bridge.request(base+'/check','POST',{
          device_id:state.deviceId,phones,confirmed_authorization:true
        });
        if(!alive()||revision!==state.revision)return;
        if(!resultsValid(result,phones))throw Error('invalid_number_check_response');
        state.results=result.items;
      }catch{
        if(alive()&&revision===state.revision)
          state.error='A verificação não foi concluída. Confira sua conexão e o limite de consultas.';
      }finally{
        if(alive()&&revision===state.revision){state.busy=false;render();}
      }
    }
    host.addEventListener('submit',event=>{
      if(!event.target.matches('[data-nc-form]'))return;
      void verify(event);
    });
    host.addEventListener('input',event=>{
      if(!alive()||state.busy)return;
      if(event.target.matches('[data-nc-phones]')){
        state.input=event.target.value;
        if(state.results){state.results=null;host.querySelector('[data-nc-results]')?.remove();}
      }
    });
    host.addEventListener('change',event=>{
      if(!alive()||state.busy)return;
      if(event.target.matches('[data-nc-device]')){state.deviceId=event.target.value;state.confirmed=false;state.results=null;render();}
      if(event.target.matches('[data-nc-confirm]')){
        state.confirmed=event.target.checked;
        const button=host.querySelector('[data-nc-check]');
        if(button)button.disabled=!state.confirmed||!state.devices.some(row=>row.id===state.deviceId&&row.available);
      }
    });
    render();
    return load();
  }
  window.OrionSubscriptionNumberCheck={mount,unmount,clearContext:unmount};
})();
