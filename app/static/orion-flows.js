/* Local draft editing and explicit server simulation. No publish/send action. */
(function(){'use strict';let active=null;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const kinds={tag:'Etiqueta',handoff:'Atendimento humano',reminder_draft:'Lembrete em rascunho'};
  function unmount(){if(active){active.alive=false;active.abort.abort();active.host.inert=false;active=null;}}
  async function mount({host,request,tenantId,canWrite=false}){
    unmount();const s={host,alive:true,abort:new AbortController(),items:[],draft:null,busy:false,trace:null};active=s;
    const current=()=>active===s&&s.alive&&host.isConnected,path=v=>'/api/v1/tenants/'+encodeURIComponent(tenantId)+'/management/workflows'+v;
    const button=(label,action,disabled=false,extra='')=>'<button type="button" class="os-button os-outline" data-flow-action="'+action+'" '+extra+' '+(disabled?'disabled':'')+'>'+label+'</button>';
    function render(){if(!current())return;const d=s.draft;
      host.innerHTML='<section class="os-card"><h2>Fluxos em rascunho</h2><p>Organize até dez etapas e simule a ordem das ações. A simulação não aplica etiquetas, transfere conversas ou envia lembretes.</p><p role="status" data-flow-notice aria-live="polite"></p>'+button('Novo rascunho','new',!canWrite||s.busy)+'<div class="os-inbox-list">'+s.items.map(x=>button(esc(x.name)+' · versão '+esc(x.version),'select',s.busy,'data-id="'+esc(x.id)+'"')).join('')+'</div>'+(d?'<form data-flow-form><label>Nome do fluxo<input name="name" maxlength="120" required value="'+esc(d.name)+'" '+(!canWrite?'readonly':'')+'></label><ol>'+d.steps.map((step,i)=>'<li><label>Etapa '+(i+1)+'<select data-flow-kind="'+i+'" '+(!canWrite?'disabled':'')+'>'+Object.entries(kinds).map(([id,name])=>'<option value="'+id+'" '+(step.kind===id?'selected':'')+'>'+name+'</option>').join('')+'</select></label><label>Descrição<input data-flow-label="'+i+'" maxlength="120" required value="'+esc(step.label)+'" '+(!canWrite?'readonly':'')+'></label><div class="os-actions">'+button('Subir','up',!canWrite||s.busy||i===0,'data-index="'+i+'"')+button('Descer','down',!canWrite||s.busy||i===d.steps.length-1,'data-index="'+i+'"')+button('Remover','remove',!canWrite||s.busy||d.steps.length===1,'data-index="'+i+'"')+'</div></li>').join('')+'</ol><div class="os-actions">'+button('Adicionar etapa','add',!canWrite||s.busy||d.steps.length>=10)+'<button class="os-button" '+(!canWrite||s.busy?'disabled':'')+'>Salvar rascunho</button>'+button('Simular versão salva','simulate',s.busy||!d.id||s.dirty)+'</div>'+(s.dirty?'<p>Salve as alterações antes de simular.</p>':'')+'</form>':'')+(s.trace?'<h3>Prévia das ações</h3><ol>'+s.trace.map(x=>'<li>'+esc(x.description)+'</li>').join('')+'</ol><p>Nenhuma ação foi executada.</p>':'')+'</section>';
    }
    function notice(e){if(current()){const n=host.querySelector('[data-flow-notice]');n.textContent=e.message||String(e);n.setAttribute('role',e instanceof Error?'alert':'status');}}
    async function perform(work){if(s.busy)return;s.busy=true;host.inert=true;let error;try{await work();}catch(e){error=e;}finally{s.busy=false;if(current()){host.inert=false;render();if(error)notice(error);}}}
    function changed(){s.dirty=true;s.trace=null;const b=host.querySelector('[data-flow-action=simulate]');if(b)b.disabled=true;}
    host.addEventListener('input',e=>{if(!canWrite||!s.draft||s.busy)return;if(e.target.name==='name')s.draft.name=e.target.value;else if(e.target.hasAttribute('data-flow-label'))s.draft.steps[Number(e.target.dataset.flowLabel)].label=e.target.value;changed();},{signal:s.abort.signal});
    host.addEventListener('change',e=>{if(canWrite&&s.draft&&!s.busy&&e.target.hasAttribute('data-flow-kind')){s.draft.steps[Number(e.target.dataset.flowKind)].kind=e.target.value;changed();}},{signal:s.abort.signal});
    host.addEventListener('submit',e=>{e.preventDefault();if(!canWrite||!s.draft)return;void perform(async()=>{const d=s.draft;const saved=await request(path(d.id?'/'+d.id:''),d.id?'PUT':'POST',{name:d.name,steps:d.steps,expected_version:d.id?d.version:0});if(!current())return;s.draft=saved;s.dirty=false;s.items=(await request(path(''))).items;});},{signal:s.abort.signal});
    host.addEventListener('click',e=>{const b=e.target.closest('[data-flow-action]');if(!b||b.disabled||s.busy)return;const a=b.dataset.flowAction,i=Number(b.dataset.index),d=s.draft;
      if(a==='select'){
        if(s.dirty&&!window.confirm('Há alterações não salvas neste fluxo. Descartar e abrir outro?'))return;
        const item=s.items.find(x=>x.id===b.dataset.id);if(!item)return;
        s.draft=JSON.parse(JSON.stringify(item));s.dirty=false;s.trace=null;render();return;
      }
      if(a==='simulate'){void perform(async()=>{const result=await request(path('/'+d.id+'/simulate'),'POST',{expected_version:d.version});if(current())s.trace=result.trace;});return;}
      if(!canWrite)return;
      if(a==='new'){
        if(s.dirty&&!window.confirm('Há alterações não salvas neste fluxo. Descartar e criar outro?'))return;
        s.draft={name:'',steps:[{kind:'handoff',label:''}]};s.dirty=true;s.trace=null;
      }
      if(a==='add')d.steps.push({kind:'tag',label:''});if(a==='remove')d.steps.splice(i,1);
      if(a==='up'||a==='down'){const target=i+(a==='up'?-1:1);[d.steps[i],d.steps[target]]=[d.steps[target],d.steps[i]];}
      if(a!=='new')changed();render();
    },{signal:s.abort.signal});
    host.innerHTML='<p role="status">Carregando rascunhos…</p>';try{s.items=(await request(path(''))).items;render();}catch(e){if(current())host.textContent=e.message;}
  }
  const hasUnsavedDraft=()=>!!active&&active.alive&&active.dirty===true;
  window.OrionFlows={mount,unmount,hasUnsavedDraft};
})();
