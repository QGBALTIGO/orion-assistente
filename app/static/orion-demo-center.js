/* Commercial demos use a separate admin API and never create customer tenants. */
(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels={draft:'Em revisão',published:'Disponível',archived:'Arquivada'};
  let state=null,epoch=0;
  const button=(label,action,id='',disabled=false)=>`<button type="button" class="os-button os-outline os-small" data-demo-action="${action}" data-demo-id="${esc(id)}"${disabled?' disabled':''}>${esc(label)}</button>`;
  const safeLink=value=>{try{const u=new URL(value,location.origin);return u.origin===location.origin&&/^\/demo\/[A-Za-z0-9_-]+\/?$/.test(u.pathname)?u.href:null;}catch{return null;}};
  function current(s){return state===s&&s.epoch===epoch&&s.host.isConnected&&!s.host.closest('.hidden,[hidden]');}
  function feedback(s,message,error=false){if(!current(s))return;const node=s.host.querySelector('#demoFeedback');node.hidden=!message;node.className=error?'os-notice os-error':'os-notice';node.textContent=message;node.setAttribute('role',error?'alert':'status');}
  function frame(s){
    s.host.innerHTML=`<div class="os-title-row"><div><h1>Demonstrações</h1><p>Apresente cada empresa na Orion com agendamentos simulados.</p></div><div class="os-actions">${button('Voltar para clientes','back')}${button('Atualizar','refresh')}</div></div>
      <div class="os-card"><div class="os-title-row"><div><h2>Importar demonstrações</h2><p class="os-help">Selecione o arquivo com os cadastros preparados. Revise cada demonstração antes de disponibilizar seu link.</p></div></div>
      <div class="os-field"><label for="demoImportFile">Arquivo de demonstrações</label><input id="demoImportFile" type="file" accept=".json,application/json"></div>
      <div id="demoImportReview" class="os-space" hidden></div></div>
      <div id="demoFeedback" class="os-notice" role="status" hidden></div>
      <section class="os-space" aria-label="Demonstrações cadastradas"><div id="demoResults" class="os-cards-grid"><p role="status">Carregando demonstrações…</p></div></section>`;
    s.host.addEventListener('click',s.click);
    s.host.addEventListener('change',s.change);
  }
  function render(s){
    if(!current(s))return;
    const root=s.host.querySelector('#demoResults');
    if(!s.items.length){root.innerHTML='<p class="os-client-empty">Nenhuma demonstração cadastrada.</p>';return;}
    root.innerHTML=s.items.map(item=>{
      const catalogue=item.catalogue||{},site=catalogue.site||{};
      const expired=item.expires_at&&Date.parse(item.expires_at)<=Date.now();
      const status=expired?'Expirada':labels[item.status]||'Em revisão';
      const url=safeLink(item.url||item.public_url||item.demo_url||(item.token?`/demo/${item.token}`:''));
      const pending=Array.isArray(item.pending_fields)?item.pending_fields:[];
      const sources=Array.isArray(item.source_urls)?item.source_urls:[];
      const ready=item.status==='published'&&!expired;
      const city=[site.city,site.state].filter(Boolean).join(' / ');
      return `<article class="os-card os-item-card" data-demo-card="${esc(item.id)}"><div class="os-item-top"><div><h3>${esc(site.title||item.title||item.source_id)}</h3>${city?`<p class="os-help">${esc(city)}</p>`:''}</div><span class="os-badge ${ready?'os-success':'os-neutral'}">${esc(status)}</span></div>
        ${item.catalogue?`<p class="os-help">${(catalogue.services||[]).length} serviços. ${(catalogue.professionals||[]).length} profissionais identificados.</p>
        <details><summary>Dados da agenda</summary><p>${esc(site.address_display||site.address||'Endereço a confirmar')}</p><p>${esc(site.whatsapp_phone||'WhatsApp a confirmar')}</p><ul>${(catalogue.services||[]).map(service=>`<li>${esc(service.name)} — ${service.price_cents==null?'Valor a confirmar':esc((service.price_cents/100).toLocaleString('pt-BR',{style:'currency',currency:'BRL'}))}${service.duration_min==null?'':` · ${esc(service.duration_min)} min`}</li>`).join('')}</ul><p>Equipe: ${esc((catalogue.professionals||[]).map(p=>p.name).join(', ')||'A confirmar')}.</p></details>`:''}
        ${item.expires_at?`<p class="os-help">Disponível até ${esc(new Date(item.expires_at).toLocaleDateString('pt-BR'))}.</p>`:''}
        ${pending.length?`<details><summary>${pending.length} informações a confirmar</summary><ul>${pending.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details>`:'<p class="os-help">Sem pendências de revisão registradas.</p>'}
        ${sources.length?`<details><summary>Fontes dos dados</summary><ul>${sources.map(value=>{try{const u=new URL(value);return u.protocol==='https:'?`<li><a href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(u.hostname)}</a></li>`:'';}catch{return '';}}).join('')}</ul></details>`:''}
        <div class="os-item-commands">${!item.catalogue?button('Ver cadastro','inspect',item.id,s.busy):''}${ready&&url?`<a class="os-button os-outline os-small" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Abrir demonstração</a>`:''}
          ${item.status==='draft'&&!expired?button('Disponibilizar link','publish',item.id,s.busy):''}${ready?button('Arquivar','archive',item.id,s.busy):''}</div></article>`;
    }).join('');
  }
  async function load(s){
    const read=++s.read;
    try{const data=await s.request('/api/v1/admin/demos');if(!current(s)||read!==s.read)return;s.items=Array.isArray(data)?data:data.items||data.demos||[];render(s);}
    catch(error){if(current(s)&&read===s.read){s.host.querySelector('#demoResults').innerHTML='<p class="os-client-empty">Não foi possível carregar as demonstrações.</p>';feedback(s,error.message||'Falha ao carregar.',true);}}
  }
  function reviewImport(s,raw){
    const rows=Array.isArray(raw)?raw:raw?.demos;
    if(!Array.isArray(rows)||!rows.length||rows.length>100)throw Error('O arquivo deve conter entre 1 e 100 demonstrações.');
    const ids=new Set();
    for(const row of rows){if(!row||typeof row.source_id!=='string'||!row.catalogue?.site||ids.has(row.source_id))throw Error('Confira o cadastro e o identificador único de cada demonstração.');ids.add(row.source_id);}
    s.importRows=rows;
    const root=s.host.querySelector('#demoImportReview');root.hidden=false;
    root.innerHTML=`<h3>${rows.length} demonstrações prontas para importar</h3><ul>${rows.map(row=>`<li>${esc(row.catalogue.site.title||row.source_id)}</li>`).join('')}</ul>${button('Criar demonstrações em revisão','import')}`;
  }
  async function importRows(s){
    if(s.busy||!s.importRows?.length)return;s.busy=true;s.read++;
    s.host.querySelectorAll('button,input').forEach(n=>n.disabled=true);
    const rows=s.importRows.slice();let done=0;const errors=[];
    for(const row of rows){
      if(!current(s))break;
      try{await s.request('/api/v1/admin/demos','POST',{...row,status:'draft'});done++;}
      catch(error){errors.push(`${row.catalogue.site.title||row.source_id}: ${error.message||'Falha ao importar'}`);}
    }
    if(!current(s))return;s.busy=false;
    s.host.querySelectorAll('button,input').forEach(n=>n.disabled=false);
    feedback(s,`${done} de ${rows.length} demonstrações processadas.${errors.length?' '+errors.join(' '):' Revise os dados e disponibilize os links.'}`,errors.length>0);
    if(!errors.length){s.importRows=null;s.host.querySelector('#demoImportReview').hidden=true;s.host.querySelector('#demoImportFile').value='';}
    await load(s);
  }
  async function changeStatus(s,id,status){
    if(s.busy)return;const item=s.items.find(x=>String(x.id)===id);if(!item)return;s.busy=true;s.read++;render(s);
    try{await s.request(`/api/v1/admin/demos/${encodeURIComponent(id)}/status`,'PATCH',{expected_version:item.version,status});if(current(s))feedback(s,status==='published'?'Link de demonstração disponível.':'Demonstração arquivada.');}
    catch(error){feedback(s,error.message||'Não foi possível atualizar a demonstração.',true);}
    finally{if(current(s)){s.busy=false;await load(s);}}
  }
  async function inspect(s,id){
    if(s.busy)return;s.busy=true;s.read++;render(s);
    try{const item=await s.request(`/api/v1/admin/demos/${encodeURIComponent(id)}`);if(current(s)){s.items=s.items.map(x=>String(x.id)===id?item:x);}}
    catch(error){feedback(s,error.message||'Não foi possível abrir o cadastro.',true);}
    finally{if(current(s)){s.busy=false;render(s);}}
  }
  function open({host,request,role,onBack}){
    reset();if(role!=='admin'||!host||typeof request!=='function')return;
    const s={host,request,onBack,epoch,items:[],read:0,fileRead:0,busy:false,importRows:null};state=s;
    s.click=event=>{const b=event.target.closest('[data-demo-action]');if(!b||!current(s)||s.busy)return;event.preventDefault();
      if(b.dataset.demoAction==='back'){const back=s.onBack;reset();back?.();}
      if(b.dataset.demoAction==='refresh')void load(s);
      if(b.dataset.demoAction==='import')void importRows(s);
      if(b.dataset.demoAction==='inspect')void inspect(s,b.dataset.demoId);
      if(b.dataset.demoAction==='publish')void changeStatus(s,b.dataset.demoId,'published');
      if(b.dataset.demoAction==='archive')void changeStatus(s,b.dataset.demoId,'archived');
    };
    s.change=async event=>{if(event.target.id!=='demoImportFile'||!current(s)||s.busy)return;
      const read=++s.fileRead,file=event.target.files?.[0];
      s.importRows=null;s.host.querySelector('#demoImportReview').hidden=true;feedback(s,'');if(!file)return;
      try{if(file.size>2_000_000)throw Error('Use um arquivo de até 2 MB.');const text=await file.text();if(current(s)&&read===s.fileRead)reviewImport(s,JSON.parse(text));}
      catch(error){if(current(s)&&read===s.fileRead){s.importRows=null;s.host.querySelector('#demoImportReview').hidden=true;feedback(s,error instanceof SyntaxError?'O arquivo não contém dados válidos.':error.message,true);}}
    };
    frame(s);void load(s);
  }
  function reset(){epoch++;if(state){state.host.removeEventListener('click',state.click);state.host.removeEventListener('change',state.change);}state=null;}
  window.OrionDemoCenter=Object.freeze({open,reset});
})();
