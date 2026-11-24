/* Orion number checker: bounded read-only queries on the selected device. */
(()=>{
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const labels={registered:'Com WhatsApp',not_registered:'Sem WhatsApp',inconclusive:'Inconclusivo',error:'Erro'};
  const reasons={provider_timeout:'O WhatsApp demorou para responder.',provider_failed:'Não foi possível concluir a consulta.',provider_result_unverified:'O WhatsApp retornou uma resposta não confirmada.',deadline_exceeded:'O tempo desta consulta terminou antes de verificar este número.',invalid_number:'Formato inválido. Informe o número com DDI e DDD.'};
  const split=value=>value.split(/[\r\n,;]+/).map(row=>row.trim()).filter(Boolean);
  const readable=device=>device?.available===true&&device.status==='connected';
  const notice=(value,error=false)=>'<div class="os-notice'+(error?' os-error':'')+'" role="'+(error?'alert':'status')+'"><p>'+esc(value)+'</p></div>';
  let active=null;
  function unmount(){const old=active;active=null;old?.destroy();}
  function clearContext(){active?.revoke();}
  function mount(bridge){
    unmount();
    const host=bridge.host,base='/api/v1/tenants/'+bridge.tenantId+'/subscription-whatsapp/number-check';
    if(!host||!uuid(bridge.tenantId))throw Error('invalid_number_check_context');
    const state={alive:true,revoked:false,epoch:0,busy:false,loading:false,options:null,device:'',group:'',numbers:'',error:'',result:null,filter:'',page:1,message:''};
    let controller,abort=null;
    const current=()=>state.alive&&!state.revoked&&active===controller&&host.isConnected&&bridge.canAccess?.()!==false;
    const allowed=()=>current()&&bridge.canWrite?.()!==false;
    function revoke(){state.revoked=true;state.epoch++;abort?.abort();state.result=null;state.numbers='';state.options=null;host.setAttribute('aria-busy','false');host.innerHTML=notice('O acesso a esta empresa mudou. Abra o painel novamente.');}
    function errorText(error){return error?.detail?.message||error?.message||'Não foi possível concluir a consulta. Tente novamente.';}
    function validateOptions(value){
      if(!value||!Array.isArray(value.devices)||!Array.isArray(value.groups)||value.limits?.numbers_per_check!==1000)throw Error('Não foi possível confirmar os limites da consulta.');
      if(value.devices.some(d=>!uuid(d.id)||typeof d.name!=='string'||typeof d.available!=='boolean')||value.groups.some(g=>!uuid(g.id)||typeof g.name!=='string'||!Number.isSafeInteger(g.member_count)||g.member_count<0))throw Error('Não foi possível confirmar os dispositivos e grupos.');
      return value;
    }
    function validateResult(value,device){
      if(!value||value.device_id!==device||!Array.isArray(value.items)||value.items.length>1000||value.total!==value.items.length||!Number.isSafeInteger(value.duplicates)||value.duplicates<0||!Number.isSafeInteger(value.input_total)||value.input_total!==value.total+value.duplicates)throw Error('A resposta recebida não pôde ser confirmada.');
      for(const item of value.items){
        if(typeof item.number!=='string'||item.number.length>80||!Object.hasOwn(labels,item.status)||item.reason!==null&&!Object.hasOwn(reasons,item.reason))throw Error('A resposta recebida não pôde ser confirmada.');
        if(['registered','not_registered'].includes(item.status)&&(item.reason!==null||!/^\+[1-9]\d{7,14}$/.test(item.number)))throw Error('A resposta recebida não pôde ser confirmada.');
      }
      for(const status of Object.keys(labels))if(value.counts?.[status]!==value.items.filter(row=>row.status===status).length)throw Error('A contagem recebida não pôde ser confirmada.');
      return value;
    }
    function canCheck(){
      const group=state.options?.groups.find(g=>g.id===state.group),count=split(state.numbers).length;
      return allowed()&&!state.busy&&!state.loading&&readable(state.options?.devices.find(d=>d.id===state.device))&&(state.group?!!group&&group.member_count>0&&group.member_count<=1000:count>0&&count<=1000);
    }
    function select(label,id,values,value,disabled=false){return '<div class="os-field"><label for="'+id+'">'+esc(label)+'</label><select id="'+id+'" data-orion-field="native" '+(disabled?'disabled':'')+'>'+values.map(row=>'<option value="'+esc(row[0])+'" '+(row[0]===value?'selected':'')+' '+(row[2]?'disabled':'')+'>'+esc(row[1])+'</option>').join('')+'</select></div>';}
    function resultsHTML(){
      if(!state.result)return '';
      const result=state.result,filtered=result.items.filter(row=>!state.filter||row.status===state.filter),pages=Math.max(1,Math.ceil(filtered.length/30));
      state.page=Math.min(state.page,pages);const start=(state.page-1)*30,items=filtered.slice(start,start+30);
      const pagination=filtered.length>30?'<nav class="os-pagination" aria-label="Páginas de resultados"><span>'+(start+1)+'–'+(start+items.length)+' de '+filtered.length+'</span><div><button type="button" class="os-button os-outline os-small" data-nc-action="previous" '+(state.page===1?'disabled':'')+'>Anterior</button><span>Página '+state.page+' de '+pages+'</span><button type="button" class="os-button os-outline os-small" data-nc-action="next" '+(state.page===pages?'disabled':'')+'>Próxima</button></div></nav>':'';
      return '<section class="os-card os-form" data-nc-results aria-labelledby="osNcResults"><header class="os-card-header"><div><h2 id="osNcResults">Resultado da verificação</h2><p>'+result.total+' resultado(s) · '+result.duplicates+' número(s) repetido(s) ignorado(s).</p></div></header><p class="os-help">'+Object.entries(labels).map(([key,label])=>esc(label)+': <strong>'+result.counts[key]+'</strong>').join(' · ')+'</p>'+select('Mostrar resultados','osNcFilter',[['','Todos'],...Object.entries(labels)],state.filter)+
        '<div class="os-table-scroll"><table class="os-table"><thead><tr><th scope="col">Número</th><th scope="col">Resultado</th><th scope="col">Detalhes</th></tr></thead><tbody>'+items.map(row=>'<tr><td>'+esc(row.number||'Linha vazia')+'</td><td><span class="os-badge '+(row.status==='registered'?'os-success':row.status==='not_registered'?'os-neutral':'os-warning')+'">'+labels[row.status]+'</span></td><td>'+esc(reasons[row.reason]||'Verificado nesta consulta.')+'</td></tr>').join('')+(!items.length?'<tr><td colspan="3">Nenhum resultado neste filtro.</td></tr>':'')+'</tbody></table></div>'+pagination+'<div class="os-form-actions"><button type="button" class="os-button os-outline" data-nc-action="copy" '+(!result.counts.registered?'disabled':'')+'>Copiar números com WhatsApp</button><button type="button" class="os-button os-outline" data-nc-action="export">Exportar resultados CSV</button></div><p class="os-help">Uma consulta inconclusiva não significa que o número não possui WhatsApp.</p></section>';
    }
    function render(){
      if(!current())return;
      host.setAttribute('aria-busy',String(state.busy||state.loading));
      const options=state.options,off=state.busy||state.loading,device=options?.devices.find(d=>d.id===state.device);
      host.innerHTML='<div class="os-title-row"><div><h1 tabindex="-1">Verificador de números WhatsApp</h1><p>Consulte quais números possuem WhatsApp usando um dispositivo conectado.</p></div><button type="button" class="os-button os-outline" data-nc-action="refresh" '+(off?'disabled':'')+'>Atualizar</button></div><div class="os-section-stack">'+(state.error?notice(state.error,true):'')+(state.message?notice(state.message):'')+(options?'<form class="os-card os-form" data-nc-form><div class="os-form-grid">'+select('Dispositivo','osNcDevice',[['','Selecione um dispositivo'],...options.devices.map(d=>[d.id,d.name+' · '+(readable(d)?'Conectado':'Indisponível'),!readable(d)])],state.device,off)+select('Grupo de contatos (opcional)','osNcGroup',[['','Informar números abaixo'],...options.groups.map(g=>[g.id,g.name+' ('+g.member_count+' contatos)'+(g.member_count>1000?' · acima do limite':''),g.member_count>1000||!g.member_count])],state.group,off)+'</div><div class="os-field"><label for="osNcNumbers">Números de WhatsApp</label><textarea id="osNcNumbers" rows="7" maxlength="81000" placeholder="+55 (67) 99999-0000&#10;+595 981 234567" '+(off||state.group?'disabled':'')+'>'+esc(state.numbers)+'</textarea><small class="os-help" id="osNcCount">'+(state.group?'Serão usados os contatos do grupo selecionado.':split(state.numbers).length+' / 1.000 números. Use um por linha, com DDI e DDD.')+'</small></div>'+(device&&!readable(device)?notice('Este dispositivo não está conectado. Confira a conexão em Dispositivos.'):!options.devices.length?notice('Conecte um dispositivo em Dispositivos para iniciar.'):'' )+'<div class="os-form-actions"><button type="submit" class="os-button" data-nc-submit '+(!canCheck()?'disabled':'')+'>'+(state.busy?'Verificando…':'Verificar números')+'</button></div><p class="os-help">A consulta não envia mensagens nem altera os contatos.</p></form>':notice(state.loading?'Carregando dispositivos e grupos…':'Use Atualizar para carregar os dispositivos e grupos.'))+resultsHTML()+'</div>';
      window.OrionFields?.refresh(host);
    }
    async function load(){
      if(!current()||state.busy)return;abort?.abort();abort=new AbortController();const epoch=++state.epoch;state.loading=true;state.error='';state.message='';render();
      try{const value=validateOptions(await bridge.request(base+'/options','GET',undefined,{signal:abort.signal}));if(!current()||epoch!==state.epoch)return;state.options=value;if(!value.devices.some(d=>d.id===state.device&&readable(d)))state.device=value.devices.filter(readable).length===1?value.devices.find(readable).id:'';if(!value.groups.some(g=>g.id===state.group))state.group='';}
      catch(error){if(current()&&epoch===state.epoch){if([401,403].includes(error.status))revoke();else if(error.name!=='AbortError')state.error=errorText(error);}}
      finally{if(current()&&epoch===state.epoch){state.loading=false;render();}}
    }
    async function check(){
      if(!canCheck())return;const device=state.device,payload={device_id:device,...(state.group?{group_id:state.group}:{numbers:split(state.numbers)})};
      abort?.abort();abort=new AbortController();const epoch=++state.epoch;state.busy=true;state.error='';state.result=null;state.message='';render();
      try{const result=validateResult(await bridge.request(base+'/check','POST',payload,{signal:abort.signal}),device);if(!current()||epoch!==state.epoch)return;state.result=result;state.filter='';state.page=1;}
      catch(error){if(current()&&epoch===state.epoch){if([401,403].includes(error.status))revoke();else if(error.name!=='AbortError')state.error=errorText(error);}}
      finally{if(current()&&epoch===state.epoch){state.busy=false;render();}}
    }
    function download(){
      if(!state.result)return;
      const cell=value=>{value=String(value??'');if(/^[=+\-@\t\r]/.test(value))value="'"+value;return '"'+value.replace(/"/g,'""')+'"';};
      const csv='\ufeff'+[['Número','Resultado','Detalhes'],...state.result.items.map(row=>[row.number,labels[row.status],reasons[row.reason]||''])].map(row=>row.map(cell).join(';')).join('\r\n');
      const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='orion-verificacao-whatsapp.csv';host.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    async function click(event){const el=event.target.closest('[data-nc-action]');if(!el||el.disabled||!host.contains(el))return;if(!current()){revoke();return;}
      if(el.dataset.ncAction==='refresh')await load();
      else if(el.dataset.ncAction==='export')download();
      else if(['previous','next'].includes(el.dataset.ncAction)&&state.result){state.page+=el.dataset.ncAction==='next'?1:-1;render();}
      else if(el.dataset.ncAction==='copy'&&state.result){try{await navigator.clipboard.writeText(state.result.items.filter(row=>row.status==='registered').map(row=>row.number).join('\n'));if(current()){state.message='Números com WhatsApp copiados.';render();}}catch{if(current()){state.error='Não foi possível copiar. Use Exportar resultados CSV.';render();}}}
    }
    function change(event){if(!current()){revoke();return;}if(event.target.id==='osNcFilter'){state.filter=event.target.value;state.page=1;render();return;}if(state.busy)return;if(event.target.id==='osNcDevice')state.device=event.target.value;else if(event.target.id==='osNcGroup')state.group=event.target.value;else return;state.result=null;state.error='';state.message='';render();}
    function input(event){if(event.target.id!=='osNcNumbers'||state.busy)return;if(!current()){revoke();return;}state.numbers=event.target.value;state.result=null;state.error='';state.message='';host.querySelector('[data-nc-results]')?.remove();host.querySelectorAll('.os-section-stack > .os-notice').forEach(el=>el.remove());const count=split(state.numbers).length;host.querySelector('#osNcCount').textContent=count+' / 1.000 números. Use um por linha, com DDI e DDD.';host.querySelector('[data-nc-submit]').disabled=!canCheck();}
    function submit(event){if(!event.target.matches('[data-nc-form]'))return;event.preventDefault();void check();}
    function destroy(){state.alive=false;state.epoch++;abort?.abort();state.numbers='';state.result=null;for(const [name,fn] of [['click',click],['change',change],['input',input],['submit',submit]])host.removeEventListener(name,fn);host.replaceChildren();}
    controller={ready:null,destroy,revoke,getState:()=>({busy:state.busy,loading:state.loading,hasResult:!!state.result})};active=controller;
    for(const [name,fn] of [['click',click],['change',change],['input',input],['submit',submit]])host.addEventListener(name,fn);controller.ready=load();return controller;
  }
  window.OrionSubscriptionNumberCheck=Object.freeze({mount,unmount,clearContext});
})();
