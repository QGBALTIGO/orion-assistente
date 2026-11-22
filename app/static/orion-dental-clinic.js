/* Orion Dental — authenticated module. No health data in browser storage, URLs or analytics. */
(function () {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const idRe=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const fmtDate=v=>v?new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—';
  const row=(title,value)=>'<div class="os-dental-row"><strong>'+esc(title)+'</strong><span>'+esc(value??'Não informado')+'</span></div>';
  const button=(label,action,variant='os-outline',attrs='')=>'<button type="button" class="os-button '+variant+' os-dental-button" data-dental-action="'+action+'" '+attrs+'>'+esc(label)+'</button>';
  const field=(label,name,type='text',value='',attrs='')=>
    '<div class="os-dental-field"><label for="dental-'+esc(name)+'">'+esc(label)+'</label>'+
    (type==='textarea'?'<textarea id="dental-'+esc(name)+'" name="'+esc(name)+'" '+attrs+'>'+esc(value)+'</textarea>':
      '<input id="dental-'+esc(name)+'" type="'+type+'" name="'+esc(name)+'" value="'+esc(value)+'" '+attrs+'>')+'</div>';
  const choices=(label,name,options,selected='',attrs='')=>'<div class="os-dental-field"><label for="dental-'+esc(name)+'">'+esc(label)+'</label>'+
    '<select id="dental-'+esc(name)+'" name="'+esc(name)+'" '+attrs+'>'+
    options.map(([v,l])=>'<option value="'+esc(v)+'"'+(v===selected?' selected':'')+'>'+esc(l)+'</option>').join('')+'</select></div>';
  function localToday(){
    const d=new Date(),n=x=>String(x).padStart(2,'0');
    return d.getFullYear()+'-'+n(d.getMonth()+1)+'-'+n(d.getDate());
  }
  const state={host:null,request:null,tenantId:'',canAccess:null,epoch:0,access:null,team:[],professionals:[],
    patients:[],patient:null,records:[],exams:[],intakeTemplates:[],rooms:[],appointments:[],roomDay:localToday(),
    roomZone:'',supplies:[],supplyHistory:null,visitBoard:null,prostheses:null,laboratories:null,labEditing:null,q:'',selectedTooth:11,
    dentition:'permanent',tab:'anamnesis',section:'patients',findings:[],periodontal:[],stages:[],
    error:'',success:'',loading:false,submitting:false,patientRead:0,searchRead:0};
  const endpoint=path=>'/api/v1/tenants/'+state.tenantId+'/dental'+path;
  const valid=epoch=>state.host&&state.host.isConnected&&state.epoch===epoch&&state.canAccess?.()===true;
  async function api(path,method='GET',data){return await state.request(endpoint(path),method,data);}
  const formValues=form=>Object.fromEntries(new FormData(form).entries());
  function info(message,error=false){return '<div class="os-dental-notice'+(error?' os-dental-error':'')+'" role="'+(error?'alert':'status')+'">'+esc(message)+'</div>';}
  const pending=()=>'<div class="os-dental-muted" role="status">Carregando informações protegidas…</div>';
  const icons='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5C7 2 3 4 3 9c0 4 2 8 3.2 10.2.8 1.7 2.3 1.7 3 .1l2.1-5c.4-.8 1.1-.8 1.5 0l2 5c.7 1.6 2.2 1.6 3-.1C19 17 21 13 21 9c0-5-4-7-9-4Z"/></svg>';
  const adult=[18,17,16,15,14,13,12,11,21,22,23,24,25,26,27,28,
               48,47,46,45,44,43,42,41,31,32,33,34,35,36,37,38];
  const child=[55,54,53,52,51,61,62,63,64,65,85,84,83,82,81,71,72,73,74,75];
  const toothOptions=[['','Selecione'],...adult.concat(child).map(n=>[String(n),String(n)])];
  const topics=[
    ['anamnesis','Anamnese'],['odontogram','Odontograma'],['periodontal','Periodontia'],
    ['progress','Evolução'],['treatment','Plano de tratamento'],['exams','Exames'],
    ['budget','Orçamentos'],['consent','Termos e consentimentos'],['prosthesis','Próteses'],
    ['insurance','Convênios'],['exam_note','Registros de exames'],['certificate','Atestados'],
    ['referral','Encaminhamentos']
  ];
  const nameFor=kind=>topics.find(x=>x[0]===kind)?.[1]||kind;
  const safeItem=item=>(item&&typeof item==='object'?item:{});
  const dateInput=v=>v?String(v).slice(0,10):'';
  const clinicTime=value=>new Intl.DateTimeFormat('pt-BR',{
    timeZone:state.roomZone||undefined,hour:'2-digit',minute:'2-digit'
  }).format(new Date(value));
  function roomManager(){
    return '<div class="os-dental-space"><div class="os-dental-card">'+
       '<h2 class="os-dental-title">Consultórios</h2><p class="os-dental-muted">As reservas usam a agenda real do Orion e impedem sobreposição no banco.</p>'+
       '<form data-dental-form="room" class="os-dental-space">'+field('Nome do consultório','name','text','','required maxlength="80" placeholder="Ex.: Consultório 1"')+
       '<div class="os-dental-controls"><button class="os-button" type="submit">Cadastrar sala</button></div></form></div>'+
       '<div class="os-dental-card"><h3>Consultórios cadastrados</h3>'+
       (state.rooms.length?state.rooms.map(r=>'<div class="os-dental-entry">'+esc(r.name)+' <small>'+(r.active?'Ativo':'Inativo')+'</small></div>').join(''):
       '<div class="os-dental-empty">Nenhum consultório cadastrado.</div>')+'</div>'+
       '<div class="os-dental-card"><h3>Reservar sala para uma consulta</h3>'+
       '<p class="os-dental-muted">Selecione uma consulta da agenda real. O Orion verifica colisões no banco ao reservar ou reagendar.</p>'+
       '<form data-dental-form="room-day" class="os-dental-row">'+
       field('Dia das consultas','day','date',state.roomDay,'required')+
       '<div class="os-dental-field"><label>Consultar agenda</label><button type="submit" class="os-button">Buscar consultas</button></div></form>'+
       (state.appointments.length?'<form data-dental-form="room-reservation" class="os-dental-space">'+
       choices('Consulta confirmada','appointment_id',state.appointments.map(x=>[
         x.id,clinicTime(x.start_at)+' · '+x.customer_name+' · '+x.professional_name+
         (x.room_name?' · '+x.room_name:'')
       ]),'','required')+
       choices('Consultório','room_id',state.rooms.filter(x=>x.active).map(x=>[x.id,x.name]),'','required')+
       '<div class="os-dental-controls"><button class="os-button" type="submit"'+
       (state.rooms.some(x=>x.active)?'':' disabled')+'>Vincular consultório</button></div></form>':
       '<p class="os-dental-empty">Não há consultas confirmadas para o dia selecionado.</p>')+
       '</div></div>';
  }
  const supplyUnits=[['unit','Unidade'],['box','Caixa'],['ml','mL'],['g','Grama'],['kit','Kit']];
  const supplyQuantity=(quantity,unit)=>new Intl.NumberFormat('pt-BR').format(quantity)+' '+
    ({unit:'un.',box:'cx.',ml:'mL',g:'g',kit:quantity===1?'kit':'kits'}[unit]||unit);

  function supplyHistoryPanel(history){
    const rows=history.items.map(item=>'<tr><td>'+esc(fmtDate(item.occurred_at))+'</td><td>'+
      (item.movement==='in'?'Entrada':'Saída')+'</td><td>'+
      esc(supplyQuantity(item.quantity,history.supply.unit))+'</td></tr>').join('');
    return '<section class="os-dental-card os-dental-space" aria-labelledby="dental-stock-history">'+
      '<div class="os-dental-actions">'+button('Voltar ao estoque','supply-history-close','os-ghost')+'</div>'+
      '<header><h2 class="os-dental-title" id="dental-stock-history" tabindex="-1">Histórico de movimentações</h2>'+
      '<p class="os-dental-muted">'+esc(history.supply.name)+'</p></header>'+
      choices('Tipo de movimentação','supply_history_movement',[
        ['','Todas'],['in','Entradas'],['out','Saídas']],history.movement)+
      (rows?'<div class="os-dental-table-wrap"><table class="os-dental-table"><thead><tr>'+
        '<th scope="col">Data e hora</th><th scope="col">Tipo</th><th scope="col">Quantidade</th></tr></thead>'+
        '<tbody>'+rows+'</tbody></table></div>':'')+
      (history.loading?pending():'')+
      (history.error?info(history.error,true)+'<div class="os-dental-actions">'+button('Tentar novamente','supply-history-more')+'</div>':'')+
      (!history.loading&&!history.error&&!rows?'<p class="os-dental-empty">Nenhuma movimentação neste filtro.</p>':'')+
      (!history.loading&&!history.error&&history.nextCursor?
        '<div class="os-dental-actions">'+button('Carregar mais','supply-history-more')+'</div>':'')+
      '</section>';
  }

  function supplyManager(){
    if(state.supplyHistory)return supplyHistoryPanel(state.supplyHistory);
    if(state.supplyMove){
      const {item,movement,quantity}=state.supplyMove;
      const label=movement==='in'?'Entrada de material':'Saída de material';
      return '<section class="os-dental-card os-dental-space" aria-labelledby="dental-stock-move">'+
        '<header><h2 class="os-dental-title" id="dental-stock-move">'+label+'</h2><p>'+esc(item.name)+'</p></header>'+
        '<p class="os-dental-muted">Saldo atual · '+esc(supplyQuantity(item.quantity,item.unit))+'</p>'+
        '<form data-dental-form="stock-move" class="os-dental-space">'+
        field('Quantidade','stock_quantity','number',quantity,'required min="1" max="1000000" step="1" inputmode="numeric"')+
        '<div class="os-dental-actions"><button class="os-button" type="submit">Registrar '+(movement==='in'?'entrada':'saída')+'</button>'+
        button('Voltar','stock-back','os-ghost')+'</div></form></section>';
    }
    const materialForm='<details class="os-dental-card os-stock-create"'+(state.stockCreateOpen?' open':'')+'><summary>Cadastrar material</summary>'+
      '<form data-dental-form="supply" class="os-dental-row">'+field('Nome do material','name','text',state.stockDraft?.name||'','required minlength="2" maxlength="120"')+
      choices('Unidade','unit',supplyUnits,state.stockDraft?.unit||'unit')+'<div class="os-dental-controls"><button class="os-button" type="submit">Cadastrar</button></div></form></details>';
    const filters='<form data-dental-form="stock-search" class="os-stock-filter">'+
      field('Buscar material','stock_query','search',state.stockQuery,'maxlength="120"')+
      choices('Ordenação','stock_order',[['asc','Nome (A a Z)'],['desc','Nome (Z a A)']],state.stockOrder)+
      '<button class="os-button os-outline" type="submit">Buscar</button></form>';
    const items=state.supplies.map(item=>{
      const id='data-id="'+esc(item.id)+'"';
      return '<li><div><strong>'+esc(item.name)+'</strong><small>'+esc(supplyQuantity(item.quantity,item.unit))+'</small></div>'+
        '<div class="os-dental-actions">'+button('Entrada','supply-in','os-outline',id)+button('Saída','supply-out','os-outline',id)+
        button('Ver histórico','supply-history','os-ghost',id)+'</div></li>';
    }).join('');
    const more=state.stockCursor?button('Carregar mais','stock-more','os-outline',state.stockLoading?'disabled':''):'';
    return '<div class="os-dental-space"><section class="os-dental-card os-dental-space" aria-labelledby="dental-stock-title">'+
      '<h2 class="os-dental-title" id="dental-stock-title">Materiais</h2>'+filters+
      (items?'<ul class="os-stock-list">'+items+'</ul>':'')+
      (state.stockLoading?pending():'')+
      (state.stockError?info(state.stockError,true)+button('Tentar novamente','stock-retry','os-outline'):'')+
      (!items&&!state.stockError&&!state.stockLoading?'<p class="os-dental-empty">'+
        (state.stockQuery?'Nenhum material corresponde à busca.':'Nenhum insumo cadastrado.')+'</p>':'')+
      (more&&!state.stockError?'<div class="os-dental-actions">'+more+'</div>':'')+'</section>'+materialForm+'</div>';
  }
  const prosthesisStatuses=[
    ['requested','Preparação'],['ready_to_send','Pronta para envio'],['working','No laboratório'],
    ['delivered','Recebida'],['completed','Concluída'],['cancelled','Cancelada']
  ];
  const prosthesisStatus=Object.fromEntries(prosthesisStatuses);
  const prosthesisAction={
    requested:'Voltar para preparação',ready_to_send:'Marcar pronta para envio',working:'Registrar envio ao laboratório',
    delivered:'Registrar recebimento',completed:'Concluir acompanhamento',cancelled:'Cancelar acompanhamento'
  };
  const formatDueDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value||'')?value.split('-').reverse().join('/'):'Sem prazo';

  function prosthesisManager(){
    const board=state.prostheses;
    const cards=(board?.items||[]).map(item=>{
      const content=item.content;
      const id=esc(item.id);
      const pendingStep=board.change?.id===item.id?board.change.status:'';
      const steps=(item.actions||[]).map(status=>[
        status,status==='working'&&content.status==='delivered'?'Devolver ao laboratório':prosthesisAction[status]
      ]);
      const deadline=item.deadline_state==='overdue'?'<span class="os-badge os-danger">Em atraso</span>':
        item.deadline_state==='today'?'<span class="os-badge">Vence hoje</span>':'';
      return '<article class="os-dental-entry os-dental-prosthesis-card">'+
        '<div class="os-dental-row"><div><h3>'+esc(item.patient_name)+'</h3>'+
        '<p>'+esc(content.description)+'</p></div><div><span class="os-dental-label">Etapa atual</span>'+
        '<p><strong>'+esc(prosthesisStatus[content.status])+'</strong></p></div></div>'+
        '<div class="os-dental-row"><div><span class="os-dental-label">Laboratório</span><p>'+esc(content.laboratory)+'</p></div>'+
        '<div><span class="os-dental-label">Prazo estimado</span><p class="os-dental-deadline">'+esc(formatDueDate(content.due_date))+deadline+'</p></div></div>'+
        (steps.length?'<form class="os-dental-row" data-dental-form="prosthesis-step" data-id="'+id+'">'+
          choices('Próxima ação','prosthesis_step_'+item.id,[['','Selecione uma ação'],...steps],pendingStep,'required')+
          '<div class="os-dental-controls"><button type="submit" class="os-button">Atualizar etapa</button></div></form>':'')+
        '<div class="os-dental-actions">'+button('Abrir prontuário','open-prosthesis-patient','os-ghost','data-id="'+id+'"')+'</div>'+
        '</article>';
    }).join('');
    return '<section class="os-dental-card os-dental-space os-dental-work"><header class="os-dental-work-head">'+
      '<h2 class="os-dental-title">'+(state.dedicatedProstheses?'Trabalhos protéticos':'Próteses')+'</h2>'+button('Laboratórios','section-laboratories')+'</header>'+
      '<div class="os-dental-work-filters">'+
      choices('Etapa','prosthesis_filter',[['','Todas as etapas'],...prosthesisStatuses],board?.status||'')+
      choices('Prazo','prosthesis_deadline',[['','Todos os prazos'],['overdue','Somente em atraso']],board?.overdue?'overdue':'')+'</div>'+
      cards+(board?.loading?pending():'')+
      (board?.error?info(board.error,true)+'<div class="os-dental-actions">'+button('Tentar novamente','prosthesis-more')+'</div>':'')+
      (!cards&&!board?.loading&&!board?.error?'<p class="os-dental-empty">'+
        (board?.nextCursor?'Há mais trabalhos para consultar.':board?.overdue?'Nenhum trabalho em atraso para este filtro.':'Nenhuma prótese encontrada.')+'</p>':'')+
      (!board?.loading&&board?.nextCursor&&!board?.error?
        '<div class="os-dental-actions">'+button('Carregar mais','prosthesis-more')+'</div>':'')+'</section>';
  }
  function laboratoryOptions(){
    return (state.laboratories||[]).filter(lab=>lab.active)
      .map(lab=>'<option value="'+esc(lab.name)+'"></option>').join('');
  }
  function laboratoryManager(){
    const item=state.labEditing;
    const editing=!!item?.id;
    const cards=(state.laboratories||[]).map(lab=>'<div class="os-dental-entry">'+
      '<h3>'+esc(lab.name)+'</h3><small>'+esc(lab.phone||'Telefone não informado')+
      ' · '+(lab.active?'Ativo':'Inativo')+'</small><div class="os-dental-actions">'+
      button('Editar','edit-laboratory','os-outline','data-id="'+esc(lab.id)+'"')+'</div></div>').join('');
    return '<section class="os-dental-card os-dental-space"><div class="os-dental-actions">'+
      button('Voltar às próteses','section-prostheses','os-ghost')+'</div>'+
      '<h2 class="os-dental-title">Laboratórios</h2>'+
      '<form class="os-dental-space" data-dental-form="laboratory"><h3>'+
      (editing?'Editar laboratório':'Cadastrar laboratório')+'</h3><div class="os-dental-row">'+
      field('Nome','laboratory_name','text',item?.name||'','required maxlength="120"')+
      field('Telefone (opcional)','laboratory_phone','tel',item?.phone||'','maxlength="30"')+
      (editing?choices('Situação','laboratory_active',[['true','Ativo'],['false','Inativo']],String(item.active)):'')+
      '</div><div class="os-dental-controls">'+
      (editing?button('Cancelar edição','cancel-laboratory-edit','os-ghost'):'')+
      '<button class="os-button" type="submit">Salvar laboratório</button></div></form>'+
      (cards||'<p class="os-dental-empty">Nenhum laboratório cadastrado.</p>')+'</section>';
  }
  function accessManager(){
    const canManage=state.access?.can_manage;
    const grants=state.access?.grants||[];
    return '<div class="os-dental-space"><div class="os-dental-card">'+
     '<h2 class="os-dental-title">Equipe clínica e permissões</h2>'+
     info('Apenas profissionais autorizados podem abrir dados de saúde. A validação do CRO é declarada pelo responsável da clínica; não substitui consulta ao Conselho.')+
     (!canManage?'<p class="os-dental-muted">Somente o proprietário pode conceder ou revogar acessos.</p>':
     '<form data-dental-form="grant" class="os-dental-space">'+
     choices('Conta da equipe','account_id',
       (state.team.some(x=>x.active!==false&&['owner','editor','professional'].includes(x.role))?state.team.filter(x=>x.active!==false&&['owner','editor','professional'].includes(x.role)).map(x=>[x.account_id,x.name||x.email||x.account_id]):
         [[state.access?.account_id||'','Minha conta']]),state.access?.account_id||'','required')+
     choices('Profissional cadastrado','professional_id',state.professionals.filter(x=>x.active!==false).map(x=>[x.id,x.name]),'','required')+
     choices('Acesso','level',[['dentist','Cirurgião-dentista'],['assistant','Assistente administrativo']],'dentist')+
     '<div class="os-dental-row">'+field('CRO do dentista','cro_number','text','','inputmode="numeric" maxlength="14" placeholder="Somente números"')+
     field('UF do CRO','cro_state','text','','maxlength="2" placeholder="MS"')+'</div>'+
     '<label class="os-check"><input type="checkbox" name="professional_license_checked" value="true"> Confirmo ter verificado manualmente o CRO/UF do profissional</label>'+
     '<div class="os-dental-controls"><button class="os-button" type="submit">Autorizar profissional</button></div></form>')+'</div>'+
     '<div class="os-dental-card"><h3>Acessos cadastrados</h3>'+
     (grants.length?grants.map(g=>'<div class="os-dental-entry"><strong>'+esc(g.account_id)+'</strong>'+
       '<small>'+esc(g.level==='dentist'?'Dentista':'Assistente')+' · '+esc(g.cro_state||'')+' '+esc(g.cro_number||'')+' · '+(g.enabled?'Ativo':'Revogado')+'</small>'+
       (canManage&&g.enabled?button('Revogar','revoke','os-danger','data-account="'+esc(g.account_id)+'"'):'')+'</div>').join(''):
       '<div class="os-dental-empty">Nenhum acesso clínico liberado.</div>')+'</div></div>';
  }
  function patientList(){
    return '<div class="os-dental-card os-dental-space"><div class="os-dental-toolbar"><div><h2 class="os-dental-title">Pacientes</h2>'+
      '<p class="os-dental-muted">Cadastros independentes. Um responsável pode ter vários pacientes.</p></div>'+
      button('Novo','new-patient','os-outline')+'</div>'+
      '<form data-dental-form="search" class="os-dental-row">'+field('Buscar nome','q','search',state.q,'maxlength="120" placeholder="Buscar paciente"')+
      '<div class="os-dental-field"><label>Buscar</label><button class="os-button" type="submit">Filtrar</button></div></form>'+
      '<div class="os-dental-list">'+(state.patients.length?state.patients.map(x=>
       '<button type="button" class="os-dental-patient" data-dental-action="select-patient" data-id="'+esc(x.id)+
       '" aria-current="'+String(state.patient?.id===x.id)+'"><span>'+esc(x.name)+'</span><span aria-hidden="true">›</span></button>').join(''):
       '<p class="os-dental-empty">Nenhum paciente encontrado.</p>')+'</div></div>';
  }
  function patientCreate(){
    const editing=state.editingPatient&&state.patient,record=editing?state.patient:{};
    return '<div class="os-dental-card os-dental-space"><h2 class="os-dental-title">'+
      (editing?'Editar paciente':'Novo paciente')+'</h2>'+
      info('Cadastre a pessoa atendida, não apenas o número de WhatsApp. O vínculo do responsável não une prontuários.')+
      '<form data-dental-form="patient" class="os-dental-space">'+
      field('Nome completo do paciente','name','text',record.name||'','required maxlength="120"')+
      field('Data de nascimento','birth_date','date',dateInput(record.birth_date))+
      field('Telefone de contato (opcional)','contact_phone','tel',record.contact_phone||'','maxlength="30"')+
      field('Nome do responsável (opcional)','guardian_name','text',record.guardian_name||'','maxlength="120"')+
      field('Telefone do responsável (opcional)','guardian_phone','tel',record.guardian_phone||'','maxlength="30"')+
      '<div class="os-dental-controls">'+button('Cancelar','cancel-patient')+
      '<button type="submit" class="os-button">'+(editing?'Salvar alterações':'Cadastrar paciente')+'</button></div></form></div>';
  }
  function toothGrid(){
    const values=state.dentition==='permanent'?adult:state.dentition==='deciduous'?child:adult.concat(child);
    return '<div class="os-dental-odontogram" role="group" aria-label="Selecionar dente FDI">'+
      values.map(n=>'<button type="button" class="os-dental-tooth" data-dental-tooth="'+n+
      '" aria-pressed="'+String(state.selectedTooth===n)+'" title="Dente '+n+'">'+icons+'<span>'+n+'</span></button>').join('')+'</div>';
  }
  function findingList(){
    return state.findings.length?'<div class="os-dental-table-wrap"><table class="os-dental-table"><thead><tr><th>Dente</th><th>Face</th><th>Registro</th><th></th></tr></thead><tbody>'+
      state.findings.map((x,i)=>'<tr><td>'+esc(x.tooth)+'</td><td>'+esc(x.face)+'</td><td>'+esc(x.finding)+'</td><td>'+
      button('Retirar','remove-finding','os-ghost','data-index="'+i+'"')+'</td></tr>').join('')+'</tbody></table></div>':'<p class="os-dental-muted">Nenhuma face registrada. Não há dentes classificados automaticamente como saudáveis.</p>';
  }
  function periodList(){
    return state.periodontal.length?'<div class="os-dental-entry"><strong>'+state.periodontal.length+' dente(s) medido(s)</strong>'+
      state.periodontal.map(t=>'<small>FDI '+esc(t.tooth)+' · '+t.sites.map(s=>s.depth_mm==null?'—':s.depth_mm+' mm').join(' / ')+'</small>').join('')+'</div>':
      '<p class="os-dental-muted">Nenhuma medição preenchida; espaços vazios não equivalem a 2 mm ou a saúde periodontal.</p>';
  }
  function stageList(){
    return state.stages.length?'<div class="os-dental-space">'+state.stages.map((x,i)=>
      '<div class="os-dental-step"><strong>'+esc(x.description)+'</strong><small>'+
      (x.tooth?'Dente FDI '+esc(x.tooth)+' · ':'')+(x.estimate_cents==null?'Sem valor':new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(x.estimate_cents/100))+'</small>'+
      button('Retirar etapa','remove-stage','os-ghost','data-index="'+i+'"')+'</div>').join('')+'</div>':
      '<p class="os-dental-muted">Adicione pelo menos uma etapa. Nenhum procedimento será marcado como concluído automaticamente.</p>';
  }
  function intakeTemplateFields(){
    if(!state.intakeTemplates.length)return '';
    return choices('Modelo de anamnese','intake_template',[
      ['','Anamnese padrão'],...state.intakeTemplates.map(template=>[template.id,template.name])
    ])+'<div class="os-dental-space" data-dental-intake-answers></div>';
  }
  function editor(kind){
    const header='<div class="os-dental-space"><div class="os-dental-card os-dental-space"><h3>Registrar '+esc(nameFor(kind))+'</h3>'+
      info('O Orion registra autoria e horário e impede alteração silenciosa. Registro armazenado não equivale a assinatura digital certificada.');
    if(kind==='exams')return '<div class="os-dental-space"><div class="os-dental-card"><h3>Exames e arquivos privados</h3>'+
      '<p class="os-dental-muted">JPG, PNG ou PDF de até 8 MB. Arquivos são cifrados antes de serem armazenados e só abrem com permissão clínica.</p>'+
      '<form data-dental-form="exam" class="os-dental-space">'+
      '<div class="os-dental-field"><label for="dental-file">Escolher arquivo</label><input id="dental-file" type="file" name="file" accept="application/pdf,image/png,image/jpeg" required></div>'+
      '<div class="os-dental-controls"><button type="submit" class="os-button">Enviar exame</button></div></form></div>'+
      '<div class="os-dental-card"><h3>Arquivos anexados</h3>'+
      (state.exams.length?state.exams.map(x=>'<div class="os-dental-entry"><strong>'+esc(x.filename)+'</strong>'+
      '<small>'+esc(x.byte_count)+' bytes · '+esc(fmtDate(x.created_at))+'</small>'+
      button('Baixar','download-exam','os-outline','data-id="'+esc(x.id)+'"')+'</div>').join(''):
      '<div class="os-dental-empty">Nenhum exame anexado.</div>')+'</div></div>';
    let fields='';
    if(kind==='anamnesis')fields=intakeTemplateFields()+field('Queixa principal','complaint','textarea','','required maxlength="2500"')+
      field('Histórico médico declarado','medical_history','textarea','','maxlength="4000"')+
      field('Alergias declaradas','declared_allergies','textarea','','maxlength="2000"')+
      field('Medicamentos declarados','declared_medications','textarea','','maxlength="2000"')+
      field('Observações adicionais','observations','textarea','','maxlength="2500"')+
      choices('Quem respondeu','answered_by',[['patient','Paciente'],['guardian','Responsável'],['clinician','Profissional']]);
    else if(kind==='odontogram')fields=choices('Dentição','dentition',[['permanent','Permanente'],['deciduous','Decídua'],['mixed','Mista']],state.dentition)+
      toothGrid()+'<p class="os-dental-muted">Dente selecionado: <strong>'+state.selectedTooth+'</strong></p>'+
      '<div class="os-dental-row">'+choices('Face','face',[['M','Mesial'],['D','Distal'],['V','Vestibular'],['L','Lingual'],['O','Oclusal'],['I','Incisal'],['G','Geral']])+
      choices('Achado informado pelo profissional','finding',[['caries','Cárie'],['restoration','Restauração'],['crown','Coroa'],['fracture','Fratura'],['absent','Ausente'],['implant','Implante'],['healthy','Sem alteração observada'],['watch','Acompanhar']])+'</div>'+
      field('Nota da face','finding_note','text','','maxlength="300"')+
      button('Adicionar registro da face','add-finding')+findingList()+
      field('Observação do odontograma','narrative','textarea','','maxlength="3000"');
    else if(kind==='periodontal')fields=choices('Dente FDI','period_tooth',toothOptions)+
      '<p class="os-dental-muted">Seis sítios, por ordem: MV, V, DV, ML, L, DL. Campos em branco permanecem não registrados.</p>'+
      '<div class="os-dental-face-grid">'+['MV','V','DV','ML','L','DL'].map((x,i)=>'<div class="os-dental-step">'+
        field('Sondagem '+x+' (mm)','depth_'+i,'number','','min="0" max="15"')+
        field('Recessão (mm)','recession_'+i,'number','','min="-15" max="15"')+
        choices('Sangramento '+x,'bleeding_'+i,[['','Não registrado'],['true','Sim'],['false','Não']])+'</div>').join('')+'</div>'+
      button('Adicionar dente medido','add-period')+periodList()+
      field('Observações clínicas','note','textarea','','maxlength="3000"');
    else if(kind==='treatment'||kind==='budget')fields=field('Descrição','description','textarea','','required maxlength="3000"')+
      '<div class="os-dental-row">'+field('Etapa / procedimento','stage_description','text','','maxlength="400"')+
      choices('Dente (opcional)','stage_tooth',toothOptions)+
      field('Estimativa em reais (opcional)','stage_price','number','','min="0" step="0.01"')+'</div>'+
      button('Adicionar etapa','add-stage')+stageList()+
      (kind==='treatment'?field('Observações adicionais','note','textarea','','maxlength="2500"'):
       '<p class="os-dental-muted">Orçamento registrado como rascunho. Não constitui aprovação ou aceite do paciente.</p>');
    else if(kind==='prosthesis')fields=field('Laboratório','laboratory','text','','required maxlength="120" list="dental-laboratory-options"')+
      '<datalist id="dental-laboratory-options">'+laboratoryOptions()+'</datalist>'+
      field('Serviço ou peça','description','textarea','','required maxlength="3000"')+
      field('Prazo estimado','due_date','date')+
      choices('Etapa inicial','status',prosthesisStatuses);
    else if(kind==='insurance')fields=field('Convênio / operadora','insurer','text','','required maxlength="120"')+
      field('Plano / contrato','plan','text','','maxlength="160"')+
      choices('Elegibilidade','eligibility',[['not_verified','Não verificada'],['verified_manually','Conferida manualmente'],['rejected','Negada']])+
      field('Observações','notes','textarea','','maxlength="2000"');
    else if(kind==='progress')fields=field('Evolução / intercorrência','description','textarea','','required maxlength="8000"')+
      field('Identificador da consulta vinculada (opcional)','appointment_id','text','','maxlength="36"');
    else fields=field('Conteúdo do documento','text','textarea','','required maxlength="8000"')+
      field('Referência (opcional)','reference','text','','maxlength="400"')+
      '<p class="os-dental-muted">Documento em rascunho. Não é receituário, atestado ou consentimento assinado digitalmente.</p>';
    return header+'<form data-dental-form="record" data-kind="'+esc(kind)+'" class="os-dental-space">'+fields+
      '<div class="os-dental-controls"><button type="submit" class="os-button">Salvar registro clínico</button></div></form></div>'+
      '<div class="os-dental-card"><h3>Histórico de '+esc(nameFor(kind))+'</h3>'+recordHistory(kind)+'</div></div>';
  }
  function recordValue(kind,data){
    if(kind==='odontogram')return '<div class="os-dental-table-wrap"><table class="os-dental-table"><thead><tr><th>Dente</th><th>Face</th><th>Registro</th></tr></thead><tbody>'+
      (data.findings||[]).map(f=>'<tr><td>'+esc(f.tooth)+'</td><td>'+esc(f.face)+'</td><td>'+esc(f.finding)+(f.note?' · '+esc(f.note):'')+'</td></tr>').join('')+'</tbody></table></div>';
    if(kind==='periodontal')return '<div class="os-dental-space">'+(data.teeth||[]).map(t=>
      '<div class="os-dental-entry"><strong>FDI '+esc(t.tooth)+'</strong><small>'+
      (t.sites||[]).map((s,i)=>['MV','V','DV','ML','L','DL'][i]+': '+(s.depth_mm==null?'—':s.depth_mm+' mm')+
      (s.bleeding===null||s.bleeding===undefined?'':s.bleeding?' · Sangramento':' · Sem sangramento')).join(' | ')+'</small></div>').join('')+'</div>';
    if(kind==='treatment'||kind==='budget')return '<p>'+esc(data.description||'')+'</p>'+
      '<div class="os-dental-space">'+(data.stages||data.items||[]).map(s=>'<div class="os-dental-entry">'+esc(s.description)+' · '+esc(s.tooth||'')+'</div>').join('')+'</div>';
    if(kind==='prosthesis')return row('Etapa',prosthesisStatus[data.status])+row('Laboratório',data.laboratory)+
      row('Serviço ou peça',data.description)+row('Prazo estimado',formatDueDate(data.due_date));
    const snapshot=kind==='anamnesis'?data.intake_template:null;
    return (snapshot?'<div class="os-dental-space"><h4>'+esc(snapshot.name)+'</h4>'+
      (snapshot.responses||[]).map(answer=>row(answer.prompt,answer.answer||'Sem resposta')).join('')+'</div>':'')+
      Object.entries(safeItem(data)).filter(([key])=>key!=='intake_template').map(([k,v])=>
      '<div class="os-dental-row"><strong>'+esc(k.replace(/_/g,' '))+'</strong><span>'+
       esc(typeof v==='object'?JSON.stringify(v):v)+'</span></div>').join('');
  }
  function recordHistory(kind){
    const entries=state.records.filter(x=>x.kind===kind);
    return entries.length?'<div class="os-dental-space">'+entries.map(x=>
      '<details class="os-dental-entry"><summary><strong>'+esc(fmtDate(x.created_at))+'</strong>'+
      '<small> · Profissional '+esc(String(x.professional_id).slice(0,8))+'</small></summary>'+
      recordValue(kind,x.content)+'</details>').join('')+'</div>':
      '<div class="os-dental-empty">Ainda não existe registro deste tipo.</div>';
  }
  const visitStatus={confirmed:'Confirmada',completed:'Concluída',cancelled:'Cancelada',no_show:'Falta'};
  function visitLabel(visit,zone){
    const when=new Intl.DateTimeFormat('pt-BR',{timeZone:zone,dateStyle:'short',timeStyle:'short'}).format(new Date(visit.start_at));
    return when+' · '+visit.professional+' · '+visit.protocol;
  }
  function visitsPanel(board){
    const patient=state.patient;
    let content='<div class="os-dental-actions">'+button('Voltar à ficha','visits-close','os-ghost')+'</div>'+
      '<header><h2 class="os-dental-title" id="dental-visits-heading" tabindex="-1">Consultas de '+esc(patient.name)+'</h2></header>';
    if(board.confirmation){
      const {visit,target}=board.confirmation;
      const title=target?'Transferir consulta':'Desvincular consulta';
      const message=target?'A consulta '+visit.protocol+' passará de '+visit.patient_name+' para '+patient.name+'.':
        'A consulta '+visit.protocol+' continuará na agenda após a remoção do vínculo com '+patient.name+'.';
      return '<section class="os-dental-card os-dental-space" aria-labelledby="dental-confirm-visit">'+
        '<h2 class="os-dental-title" id="dental-confirm-visit" tabindex="-1">'+esc(title)+'</h2><p>'+esc(message)+'</p>'+
        '<div class="os-dental-actions">'+button('Voltar','visit-confirm-cancel','os-outline')+
        button('Confirmar '+(target?'transferência':'desvínculo'),'visit-confirm-save','')+'</div></section>';
    }
    if(board.error)content+=info(board.error,true)+button('Tentar novamente','visits-refresh');
    if(board.loading)return '<section class="os-dental-card os-dental-space">'+content+pending()+'</section>';
    if(!board.ready)return '<section class="os-dental-card os-dental-space">'+content+'</section>';
    content+=board.items.length?'<ul class="os-dental-linked-visits">'+board.items.map(v=>
      '<li><div><strong>'+esc(visitLabel(v,v.timezone))+'</strong><small>'+esc(visitStatus[v.status]||v.status)+'</small></div>'+
      button('Desvincular','visit-unlink','os-ghost','data-id="'+esc(v.id)+'"')+'</li>').join('')+'</ul>':
      '<p class="os-dental-empty">Nenhuma consulta vinculada a este paciente.</p>';
    if(board.more)content+=button('Mais consultas vinculadas','visits-more');
    content+='<h3>Vincular uma consulta</h3>'+
      '<form data-dental-form="visit-day" class="os-dental-visit-search">'+field('Dia da consulta','visit_day','date',board.day,'required')+
      '<button type="submit" class="os-button os-outline">Buscar consultas</button></form>';
    if(board.options.length){
      content+='<form data-dental-form="visit-link" class="os-dental-space">'+
        choices('Consulta na agenda','visit_id',[['','Selecione a consulta'],...board.options.map(v=>[v.id,
          visitLabel(v,board.zone)+(v.booking_contact?' · Contato: '+v.booking_contact:'')+
          (v.patient_name?' · Paciente: '+v.patient_name:'')])],'','required')+
        '<div><button class="os-button" type="submit">Vincular ao paciente</button></div></form>';
    }else content+='<p class="os-dental-muted">Nenhuma consulta disponível nesta data.</p>';
    if(board.moreOptions)content+=button('Mais consultas desta data','visits-options-more');
    return '<section class="os-dental-card os-dental-space" aria-labelledby="dental-visits-heading">'+content+'</section>';
  }
  async function fetchVisits(board, mode='all'){
    if(board.loading)return;
    const epoch=state.epoch,patientId=board.patient;
    const active=()=>valid(epoch)&&state.visitBoard===board&&state.patient?.id===patientId;
    board.loading=true;board.error='';render();
    try{
      const [history,options]=await Promise.all([
        mode==='options'?null:api('/patients/'+patientId+'/visits?offset='+(mode==='history'?board.items.length:0)),
        mode==='history'?null:api('/visit-options?'+new URLSearchParams({
          ...(board.day?{day:board.day}:{}),offset:String(mode==='options'?board.options.length:0)}))
      ]);
      if(!active())return;
      if(history){board.items=mode==='history'?[...board.items,...history.items]:history.items;board.more=history.has_more;}
      if(options){board.options=mode==='options'?[...board.options,...options.items]:options.items;
        board.moreOptions=options.has_more;board.day=options.day;board.zone=options.timezone;}
      board.ready=true;
    }catch(e){if(active()){board.ready=false;board.error=e.message||'Não foi possível consultar os vínculos.';}}
    finally{if(active()){board.loading=false;render();}}
  }
  async function changeVisitPatient(board,visit,target,confirmed=false){
    if(state.submitting||board.loading)return;
    if(!confirmed&&(!target||(visit.patient_id&&visit.patient_id!==target))){
      board.confirmation={visit,target};render();state.host?.querySelector('#dental-confirm-visit')?.focus();return;
    }
    board.confirmation=null;
    const epoch=state.epoch;state.submitting=true;state.error='';state.success='';
    try{
      await api('/appointments/'+visit.id+'/patient','PUT',{patient_id:target,expected_version:visit.version});
      if(valid(epoch)&&state.visitBoard===board)state.success=target?'Consulta vinculada ao paciente.':'Vínculo removido.';
    }catch(e){if(valid(epoch)&&state.visitBoard===board)state.error=e.message||'Não foi possível salvar o vínculo.';}
    finally{
      state.submitting=false;
      if(valid(epoch)&&state.visitBoard===board)await fetchVisits(board);
    }
  }
  function patientPanel(){
    if(!state.patient)return '<div class="os-dental-card os-dental-empty">Selecione um paciente para abrir a área clínica. Nenhum diagnóstico ou medição é preenchido automaticamente.</div>';
    if(state.visitBoard)return visitsPanel(state.visitBoard);
    if(state.access?.can_read_records!==true)
      return '<div class="os-dental-card os-dental-space"><h2 class="os-dental-title">'+esc(state.patient.name)+'</h2>'+
        button('Consultas do paciente','visits-open')+info('Este acesso administrativo não inclui consulta ou edição de prontuários, odontogramas, anamnese, periodontia ou exames.')+'</div>';
    return '<div class="os-dental-space"><div class="os-dental-card os-dental-space"><div class="os-dental-toolbar">'+
       '<div><p class="os-dental-kicker">Paciente da clínica</p><h2 class="os-dental-title">'+esc(state.patient.name)+'</h2>'+
       '<p class="os-dental-muted">Nascimento: '+esc(dateInput(state.patient.birth_date)||'Não informado')+
       ' · Responsável: '+esc(state.patient.guardian_name||'Não informado')+'</p></div>'+
       '<div class="os-dental-actions">'+button('Consultas do paciente','visits-open')+button('Editar cadastro','edit-patient')+
       button('Atualizar','refresh-patient')+'</div></div>'+
       info('As anotações são protegidas e possuem autoria, mas não estão homologadas como documentos assinados digitalmente.')+'</div>'+
       '<div class="os-dental-card"><nav class="os-dental-tabs" aria-label="Áreas odontológicas">'+
       topics.map(([kind,label])=>'<button type="button" class="os-pill-tab" data-dental-tab="'+kind+'" role="tab" aria-selected="'+String(state.tab===kind)+'">'+esc(label)+'</button>').join('')+'</nav>'+
       '</div>'+editor(state.tab)+'</div>';
  }
  function render(){
    const host=state.host;if(!host)return;
    const focusedFilter=host.contains(document.activeElement)&&['prosthesis_filter','prosthesis_deadline'].includes(document.activeElement?.name)?
      document.activeElement.name:null;
    const allowed=!!state.access?.clinical_access;
    let html='<div class="os-dental os-dental-space">';
    if(!state.dedicatedStock&&!state.dedicatedProstheses&&!state.visitsOnly)html+='<div class="os-dental-toolbar"><div><p class="os-dental-kicker">Orion • Odontologia</p>'+
      '<h2 class="os-dental-title">Gestão odontológica</h2></div>'+
      '<div class="os-dental-actions">'+
        button('Pacientes','section-patients',state.section==='patients'?'':'os-outline')+
        button('Consultórios','section-rooms',state.section==='rooms'?'':'os-outline')+
        (!state.prosthesesInMenu&&state.access?.can_read_records?button('Próteses','section-prostheses',['prostheses','laboratories'].includes(state.section)?'':'os-outline'):'')+
        (state.stockInMenu?'':button('Estoque','section-supplies',state.section==='supplies'?'':'os-outline'))+
        button('Equipe clínica','section-access',state.section==='access'?'':'os-outline')+
      '</div></div>';
    if(state.error)html+=info(state.error,true);
    if(state.success)html+=info(state.success);
    if(state.loading)html+=pending();
    else if(!state.access)html+=info('Não foi possível confirmar as permissões clínicas.',true);
    else if(state.section==='access')html+=accessManager();
    else if(state.dedicatedProstheses&&!state.access.can_read_records)html+=info('O controle de próteses requer acesso clínico de dentista.');
    else if(!allowed&&state.dedicatedStock)html+=info('Solicite ao proprietário da clínica acesso ao estoque.');
    else if(!allowed)html+=info('O acesso ao prontuário ainda não foi concedido a esta conta. Abra Equipe clínica se você for o proprietário.');
    else if(state.visitsOnly)html+=state.visitBoard?visitsPanel(state.visitBoard):button('Voltar aos pacientes','visits-close');
    else if(state.section==='rooms')html+=roomManager();
    else if(state.section==='supplies')html+=supplyManager();
    else if(state.section==='prostheses'&&state.access?.can_read_records)html+=prosthesisManager();
    else if(state.section==='laboratories'&&state.access?.can_read_records)html+=laboratoryManager();
    else if(state.section==='patients')html+=
       '<div class="os-dental-grid">'+patientList()+'<div class="os-dental-space">'+
       (state.creatingPatient||state.editingPatient?patientCreate():patientPanel())+'</div></div>';
    html+='</div>';
    host.innerHTML=html;
    if(focusedFilter)host.querySelector('[name="'+focusedFilter+'"]')?.focus({preventScroll:true});
  }
  async function invoke(fn,success){
    if(state.submitting)return;
    const epoch=state.epoch;state.submitting=true;state.error='';state.success='';
    try{await fn();if(valid(epoch)){state.success=success||'Registro salvo com segurança.';render();}}
    catch(error){if(valid(epoch)){state.error=error?.message||'Não foi possível salvar.';render();}}
    finally{state.submitting=false;}
  }
  async function loadPatient(patient,tab='anamnesis'){
    const epoch=state.epoch,read=++state.patientRead;state.visitBoard=null;
    if(state.patient?.id!==patient){
      state.patient=null;state.records=[];state.exams=[];state.intakeTemplates=[];
    }
    state.loading=true;state.error='';render();
    try{
      const authorized=state.access?.can_read_records===true;
      const [detail,records,exams,templates,labs]=await Promise.all([
        api('/patients/'+patient),
        authorized?api('/patients/'+patient+'/records?limit=100'):Promise.resolve({items:[]}),
        authorized?api('/patients/'+patient+'/exams'):Promise.resolve({items:[]}),
        authorized?api('/intake-templates/clinical').catch(()=>({items:[]})):Promise.resolve({items:[]}),
        authorized&&tab==='prosthesis'&&state.laboratories===null?api('/laboratories').catch(()=>null):null]);
      if(!valid(epoch)||read!==state.patientRead)return;
      if(detail?.id!==patient)throw Error('Resposta não corresponde ao paciente selecionado.');
      state.patient=detail;state.records=records.items||[];state.exams=exams.items||[];
      state.intakeTemplates=templates.items||[];
      if(labs)state.laboratories=labs.items;
      state.tab=tab;state.findings=[];state.periodontal=[];state.stages=[];
    }catch(e){if(valid(epoch)&&read===state.patientRead)state.error=e?.message||'Falha ao consultar paciente.';}
    finally{if(valid(epoch)&&read===state.patientRead){state.loading=false;render();}}
  }
  async function refreshPatients(){
    const epoch=state.epoch,read=++state.searchRead;state.loading=true;render();
    try{const result=await api('/patients?q='+encodeURIComponent(state.q)+'&limit=100');
      if(valid(epoch)&&read===state.searchRead)state.patients=result.items||[];
    }catch(e){if(valid(epoch)&&read===state.searchRead)state.error=e?.message||'Não foi possível buscar pacientes.';}
    finally{if(valid(epoch)&&read===state.searchRead){state.loading=false;render();}}
  }
  async function readStock(append=false){
    const epoch=state.epoch,read=++state.stockRead;
    const cursor=append?state.stockCursor:null;
    const params=new URLSearchParams({q:state.stockQuery,order:state.stockOrder,limit:'30'});
    if(cursor)params.set('cursor',cursor);
    state.stockLoading=true;state.stockError='';state.stockFailedPage=append;
    if(!append){state.supplies=[];state.stockCursor=null;}
    render();
    const active=()=>valid(epoch)&&state.section==='supplies'&&read===state.stockRead;
    try{
      const result=await api('/supplies?'+params);
      if(active()){
        const page=Array.isArray(result.items)?result.items:[];
        state.supplies=append?state.supplies.concat(page):page;
        state.stockCursor=result.next_cursor||null;
      }
    }catch(error){
      if(active())state.stockError=error?.message||'Não foi possível carregar os materiais.';
    }finally{
      if(active()){state.stockLoading=false;render();}
    }
  }
  async function loadManagers(section){
    if(section==='supplies')return readStock();
    const epoch=state.epoch;state.loading=true;state.error='';render();
    try{
      if(section==='rooms'){
        const [rooms,appointments]=await Promise.all([
          api('/rooms'),api('/appointments?day='+encodeURIComponent(state.roomDay))]);
        if(valid(epoch)){
          state.rooms=rooms.items||[];
          state.appointments=appointments.items||[];
          state.roomZone=appointments.timezone||'';
        }
      }else if(section==='laboratories'){
        const res=await api('/laboratories');
        if(valid(epoch))state.laboratories=res.items||[];
      }
    }catch(e){if(valid(epoch))state.error=e?.message||'Não foi possível carregar os cadastros.';}
    finally{if(valid(epoch)){state.loading=false;render();}}
  }
  async function loadSupplyHistory(history){
    if(history.loading)return;
    const epoch=state.epoch;
    history.loading=true;
    history.error='';
    render();
    const query=new URLSearchParams();
    if(history.movement)query.set('movement',history.movement);
    if(history.nextCursor)query.set('cursor',history.nextCursor);
    try{
      const suffix=query.toString();
      const res=await api('/supplies/'+history.supply.id+'/movements'+(suffix?'?'+suffix:''));
      if(valid(epoch)&&history===state.supplyHistory){
        history.items.push(...res.items);
        history.nextCursor=res.next_cursor;
      }
    }catch(e){
      if(valid(epoch)&&history===state.supplyHistory)
        history.error=e?.message||'Não foi possível carregar o histórico.';
    }finally{
      if(valid(epoch)&&history===state.supplyHistory){history.loading=false;render();}
    }
  }
  function selectSupplyHistory(supply,movement=''){
    state.supplyHistory={supply,movement,items:[],nextCursor:null,loading:false,error:''};
    return loadSupplyHistory(state.supplyHistory);
  }
  async function loadProstheses(board){
    if(board.loading)return;
    const epoch=state.epoch;
    const current=()=>valid(epoch)&&state.prostheses===board;
    board.loading=true;
    board.error='';
    render();
    const query=new URLSearchParams();
    if(board.status)query.set('status',board.status);
    if(board.overdue)query.set('overdue_only','true');
    if(board.nextCursor)query.set('cursor',board.nextCursor);
    try{
      const params=query.toString();
      const result=await api('/prostheses'+(params?'?'+params:''));
      if(current()){
        if(!Array.isArray(result?.items))throw Error('Não foi possível ler os trabalhos protéticos.');
        const listed=new Set(board.items.map(item=>item.id));
        board.items.push(...result.items.filter(item=>!listed.has(item.id)));
        board.nextCursor=result.next_cursor;
      }
    }catch(error){
      if(current())board.error=error?.message||'Não foi possível consultar as próteses.';
    }finally{
      if(current()){board.loading=false;render();}
    }
  }
  function selectProstheses(status='',overdue=false){
    state.prostheses={status,overdue,items:[],nextCursor:null,loading:false,error:'',change:null};
    return loadProstheses(state.prostheses);
  }
  function addFinding(){
    const form=state.host?.querySelector('[data-dental-form=record]');if(!form)return;
    const v=formValues(form),tooth=Number(state.selectedTooth),face=v.face;
    if(state.findings.some(x=>x.tooth===tooth&&x.face===face)){state.error='Esta face já está na lista.';render();return;}
    if(state.findings.length>=160){state.error='Limite de faces atingido.';render();return;}
    state.findings.push({tooth,face,finding:v.finding,...(v.finding_note?{note:v.finding_note}:{})});
    state.error='';render();
  }
  function addPeriod(){
    const form=state.host?.querySelector('[data-dental-form=record]');if(!form)return;
    const v=formValues(form),tooth=Number(v.period_tooth);
    if(!tooth){state.error='Escolha o dente FDI.';render();return;}
    if(state.periodontal.some(x=>x.tooth===tooth)){state.error='Este dente já foi medido.';render();return;}
    const sites=Array.from({length:6},(_,i)=>{
      const depth=v['depth_'+i],rec=v['recession_'+i],bleeding=v['bleeding_'+i];
      return {depth_mm:depth===''?null:Number(depth),recession_mm:rec===''?null:Number(rec),
              bleeding:bleeding===''?null:bleeding==='true'};});
    if(!sites.some(x=>x.depth_mm!==null||x.recession_mm!==null||x.bleeding!==null)){
      state.error='Preencha ao menos uma medição do dente.';render();return;
    }
    state.periodontal.push({tooth,sites});state.error='';render();
  }
  function addStage(){
    const form=state.host?.querySelector('[data-dental-form=record]');if(!form)return;
    const v=formValues(form);if(!String(v.stage_description||'').trim()){state.error='Descreva a etapa.';render();return;}
    const cents=v.stage_price===''?null:Math.round(Number(v.stage_price)*100);
    if(cents!==null&&(!Number.isSafeInteger(cents)||cents<0||cents>100000000)){
      state.error='Informe um valor válido.';render();return;
    }
    state.stages.push({description:v.stage_description,tooth:v.stage_tooth?Number(v.stage_tooth):null,
      estimate_cents:cents,state:'planned'});
    state.error='';render();
  }
  function recordPayload(form){
    const v=formValues(form),kind=form.dataset.kind;
    if(kind==='anamnesis'){
      const template=state.intakeTemplates.find(item=>item.id===v.intake_template);
      return {complaint:v.complaint,medical_history:v.medical_history||null,
        declared_allergies:v.declared_allergies||null,declared_medications:v.declared_medications||null,
        observations:v.observations||null,answered_by:v.answered_by,
        ...(template?{intake_template:{template_id:template.id,version:template.version,
          answers:[...form.querySelectorAll('[data-dental-intake-answers] textarea')].map(el=>el.value.trim())}}:{})};
    }
    if(kind==='odontogram')return {dentition:state.dentition,findings:state.findings,narrative:v.narrative||null};
    if(kind==='periodontal')return {teeth:state.periodontal,note:v.note||null};
    if(kind==='progress')return {description:v.description,...(v.appointment_id?{appointment_id:v.appointment_id}:{})};
    if(kind==='treatment')return {description:v.description,stages:state.stages,note:v.note||null};
    if(kind==='budget')return {description:v.description,items:state.stages,
      total_cents:state.stages.reduce((sum,x)=>sum+(x.estimate_cents||0),0),status:'draft'};
    if(kind==='prosthesis')return {laboratory:v.laboratory,description:v.description,
      due_date:v.due_date||null,status:v.status};
    if(kind==='insurance')return {insurer:v.insurer,plan:v.plan||null,eligibility:v.eligibility,notes:v.notes||null};
    return {text:v.text,reference:v.reference||null,state:'draft'};
  }
  async function downloadExam(id){
    if(!idRe.test(id)||!state.patient)return;
    const epoch=state.epoch;
    const url=endpoint('/patients/'+state.patient.id+'/exams/'+id);
    const res=await fetch(url,{credentials:'same-origin',cache:'no-store',redirect:'error'});
    if(!res.ok)throw Error('Exame indisponível ou acesso expirado.');
    const blob=await res.blob();if(!valid(epoch))return;
    const objectUrl=URL.createObjectURL(blob);
    const link=document.createElement('a');link.href=objectUrl;link.download='exame-'+id;
    link.style.display='none';document.body.appendChild(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
  }
  async function onClick(event){
    const btn=event.target.closest('[data-dental-action],[data-dental-tab],[data-dental-tooth]');
    if(!btn||!state.host?.contains(btn))return;
    if(btn.dataset.dentalTooth){state.selectedTooth=Number(btn.dataset.dentalTooth);render();return;}
    if(btn.dataset.dentalTab){
      state.tab=btn.dataset.dentalTab;state.findings=[];state.periodontal=[];state.stages=[];state.error='';render();
      if(state.tab==='prosthesis'&&state.laboratories===null&&state.access?.can_read_records){
        const epoch=state.epoch;
        const labs=await api('/laboratories').catch(()=>null);
        if(valid(epoch)&&labs&&state.laboratories===null){
          state.laboratories=labs.items;
          const options=state.host.querySelector('#dental-laboratory-options');
          if(options)options.innerHTML=laboratoryOptions();
        }
      }
      return;
    }
    const act=btn.dataset.dentalAction;
    if(act?.startsWith('section-')){
      const section=act.slice(8);
      if(state.dedicatedStock||(state.dedicatedProstheses&&!['prostheses','laboratories'].includes(section)))return;
      state.supplyMove=null;state.visitBoard=null;state.section=section;state.error='';state.success='';
      state.supplyHistory=null;
      state.prostheses=null;
      if(section==='prostheses'&&state.access?.can_read_records){
        await selectProstheses();return;
      }
      render();
      if(section==='laboratories'&&state.access?.can_read_records){
        state.labEditing=null;
        await loadManagers(section);return;
      }
      if(section==='rooms'||section==='supplies')await loadManagers(section);
      return;
    }
    if(act==='visits-open'&&state.patient){
      const board=state.visitBoard={patient:state.patient.id,items:[],options:[],day:'',loading:false,ready:false};
      await fetchVisits(board);
      if(state.visitBoard===board)state.host?.querySelector('#dental-visits-heading')?.focus();
      return;
    }
    if(act==='visits-close'&&state.visitsOnly){state.returnToDirectory?.();return;}
    if(act==='visits-close'){state.visitBoard=null;state.success='';state.error='';render();
      state.host?.querySelector('[data-dental-action="visits-open"]')?.focus();return;}
    if(state.visitBoard){
      if(act==='visits-more'||act==='visits-options-more'||act==='visits-refresh'){
        await fetchVisits(state.visitBoard,act==='visits-more'?'history':act==='visits-options-more'?'options':'all');return;
      }
      if(act==='visit-confirm-cancel'){
        const choice=state.visitBoard.confirmation;state.visitBoard.confirmation=null;render();
        const selector=choice?.target?'select[name="visit_id"]':'[data-dental-action="visit-unlink"][data-id="'+(choice?.visit.id||'')+'"]';
        state.host?.querySelector(selector)?.focus();return;
      }
      if(act==='visit-confirm-save'&&state.visitBoard.confirmation){
        const {visit,target}=state.visitBoard.confirmation;
        await changeVisitPatient(state.visitBoard,visit,target,true);return;
      }
      if(act==='visit-unlink'){
        const visit=state.visitBoard.items.find(v=>v.id===btn.dataset.id);
        if(visit)await changeVisitPatient(state.visitBoard,visit,null);
        return;
      }
    }
    if(act==='new-patient'){state.creatingPatient=true;state.editingPatient=false;render();return;}
    if(act==='edit-patient'&&state.patient){state.editingPatient=true;state.creatingPatient=false;render();return;}
    if(act==='cancel-patient'){state.creatingPatient=false;state.editingPatient=false;render();return;}
    if(act==='select-patient'&&idRe.test(btn.dataset.id||'')){
      state.creatingPatient=false;state.editingPatient=false;await loadPatient(btn.dataset.id);return;}
    if(act==='refresh-patient'&&state.patient){await loadPatient(state.patient.id);return;}
    if(act==='add-finding'){addFinding();return;}
    if(act==='add-period'){addPeriod();return;}
    if(act==='add-stage'){addStage();return;}
    if(act==='remove-finding'){state.findings.splice(Number(btn.dataset.index),1);render();return;}
    if(act==='remove-stage'){state.stages.splice(Number(btn.dataset.index),1);render();return;}
    if(act==='revoke'&&idRe.test(btn.dataset.account||'')&&confirm('Revogar o acesso clínico desta conta?')){
      await invoke(async()=>{await api('/clinicians/'+btn.dataset.account,'DELETE');await fetchAccess();},'Acesso revogado.');return;
    }
    if(act==='download-exam'&&idRe.test(btn.dataset.id||'')){
      await invoke(()=>downloadExam(btn.dataset.id),'Exame disponibilizado para download.');return;
    }
    if(act==='supply-history'){
      const supply=state.supplies.find(item=>item.id===btn.dataset.id);
      if(supply){
        await selectSupplyHistory(supply);
        if(state.supplyHistory?.supply===supply)
          state.host?.querySelector('#dental-stock-history')?.focus();
      }
      return;
    }
    if(act==='supply-history-close'){
      const supply=state.supplyHistory?.supply;
      state.supplyHistory=null;
      render();
      if(idRe.test(supply?.id||''))state.host?.querySelector('[data-dental-action="supply-history"][data-id="'+supply.id+'"]')?.focus();
      return;
    }
    if(act==='supply-history-more'&&state.supplyHistory){
      await loadSupplyHistory(state.supplyHistory);return;
    }
    if(act==='prosthesis-more'&&state.prostheses){
      await loadProstheses(state.prostheses);return;
    }
    if(act==='edit-laboratory'&&state.section==='laboratories'){
      const item=state.laboratories?.find(lab=>lab.id===btn.dataset.id);
      if(item){state.labEditing={...item};state.error='';state.success='';render();}
      return;
    }
    if(act==='cancel-laboratory-edit'){
      state.labEditing=null;state.error='';render();return;
    }
    if(act==='open-prosthesis-patient'&&state.prostheses){
      const patient=state.prostheses.items.find(item=>item.id===btn.dataset.id)?.patient_id;
      if(idRe.test(patient||'')){
        if(state.dedicatedProstheses){state.openProsthesisPatient?.(patient);return;}
        state.section='patients';state.prostheses=null;state.creatingPatient=false;state.editingPatient=false;
        await loadPatient(patient,'prosthesis');
      }
      return;
    }
    if(act==='stock-more'||act==='stock-retry'){
      if(!state.stockLoading)await readStock(act==='stock-more'||state.stockFailedPage);
      return;
    }
    if(act==='stock-back'&&state.supplyMove){
      const {item,movement}=state.supplyMove;
      state.supplyMove=null;state.error='';render();
      state.host.querySelector('[data-dental-action="supply-'+movement+'"][data-id="'+item.id+'"]')?.focus();
      return;
    }
    if(act==='supply-in'||act==='supply-out'){
      const item=state.supplies.find(entry=>entry.id===btn.dataset.id);
      if(!item||!idRe.test(item.id))return;
      state.supplyMove={item,movement:act==='supply-in'?'in':'out',key:crypto.randomUUID(),quantity:''};
      state.success='';state.error='';render();state.host.querySelector('[name=stock_quantity]')?.focus();
    }
  }
  async function onSubmit(event){
    const form=event.target.closest('form[data-dental-form]');
    if(!form||!state.host?.contains(form))return;
    event.preventDefault();
    const kind=form.dataset.dentalForm,v=formValues(form),patient=state.patient;
    if(kind==='stock-search'){
      state.stockQuery=(v.stock_query||'').trim();state.stockOrder=v.stock_order==='desc'?'desc':'asc';
      state.success='';await readStock();return;
    }
    if(kind==='stock-move'&&state.supplyMove){
      const move=state.supplyMove;move.quantity=v.stock_quantity;
      const quantity=Number(move.quantity);
      if(!Number.isSafeInteger(quantity)||quantity<1||quantity>1000000){state.error='Informe uma quantidade entre 1 e 1.000.000.';render();return;}
      const epoch=state.epoch;
      return invoke(async()=>{
        await api('/supplies/'+move.item.id+'/movements','POST',{movement:move.movement,quantity,entry_key:move.key});
        if(valid(epoch)&&state.supplyMove===move){state.supplyMove=null;await readStock();}
      },'Movimentação registrada.');
    }
    if(kind==='visit-day'&&state.visitBoard&&v.visit_day){
      state.visitBoard.day=v.visit_day;await fetchVisits(state.visitBoard);return;
    }
    if(kind==='visit-link'&&state.visitBoard&&patient){
      const visit=state.visitBoard.options.find(item=>item.id===v.visit_id);
      if(visit)await changeVisitPatient(state.visitBoard,visit,patient.id);
      return;
    }
    if(kind==='laboratory'&&state.section==='laboratories'&&state.access?.can_read_records){
      if(state.submitting)return;
      const item=state.labEditing;
      const editing=!!item?.id;
      const values={name:v.laboratory_name.trim(),phone:v.laboratory_phone.trim()||null};
      const draft={...item,...values,active:editing?v.laboratory_active==='true':true};
      state.labEditing=draft;
      const epoch=state.epoch;
      return invoke(async()=>{
        const saved=editing?await api('/laboratories/'+draft.id,'PATCH',{
          ...values,active:draft.active,expected_version:draft.version
        }):await api('/laboratories','POST',values);
        if(!valid(epoch))return;
        state.laboratories=[...(state.laboratories||[]).filter(lab=>lab.id!==saved.id),saved]
          .sort((a,b)=>a.name.localeCompare(b.name,'pt-BR'));
        if(state.labEditing===draft)state.labEditing=null;
      },editing?'Laboratório atualizado.':'Laboratório cadastrado.');
    }
    if(kind==='prosthesis-step'&&state.prostheses){
      const board=state.prostheses,id=form.dataset.id;
      const item=board.items.find(item=>item.id===id);
      const status=v['prosthesis_step_'+id];
      if(!item?.actions?.includes(status)||state.submitting)return;
      if(status==='cancelled'&&!confirm('Cancelar o acompanhamento desta prótese?'))return;
      if(board.change?.id!==id||board.change.status!==status)
        board.change={id,status,key:crypto.randomUUID()};
      const epoch=state.epoch;
      return invoke(async()=>{
        await api('/prostheses/'+id+'/status','PATCH',{status,entry_key:board.change.key});
        if(valid(epoch)&&state.prostheses===board)await selectProstheses(board.status,board.overdue);
      },'Etapa da prótese atualizada.');
    }
    if(kind==='search'){state.q=v.q||'';await refreshPatients();return;}
    if(kind==='patient')return invoke(async()=>{
      const payload={name:v.name,birth_date:v.birth_date||null,contact_phone:v.contact_phone||null,
        guardian_name:v.guardian_name||null,guardian_phone:v.guardian_phone||null};
      const editing=state.editingPatient&&state.patient;
      if(editing){
        await api('/patients/'+state.patient.id,'PATCH',{
          ...payload,customer_id:state.patient.customer_id||null,
          expected_version:state.patient.version
        });
      }else{
        const created=await api('/patients','POST',payload);
        state.patient={...created};
      }
      const updatedId=state.patient?.id;
      state.creatingPatient=false;state.editingPatient=false;state.q='';
      const res=await api('/patients?limit=100');state.patients=res.items||[];
      if(updatedId)await loadPatient(updatedId);
    },state.editingPatient?'Cadastro atualizado.':'Paciente cadastrado.');
    if(kind==='record'){
      if(!patient)return;
      const payload={kind:form.dataset.kind,content:recordPayload(form),entry_key:crypto.randomUUID()};
      return invoke(async()=>{
        await api('/patients/'+patient.id+'/records','POST',payload);
        const r=await api('/patients/'+patient.id+'/records?limit=100');
        if(state.patient?.id!==patient.id)return;
        state.records=r.items||[];state.findings=[];state.periodontal=[];state.stages=[];
      },'Registro clínico gravado e auditado.');
    }
    if(kind==='exam'&&patient)return invoke(async()=>{
      const file=form.querySelector('[name=file]').files?.[0];
      if(!file||file.size>8*1024*1024)throw Error('Escolha arquivo de até 8 MB.');
      const b64=await new Promise((resolve,reject)=>{
        const reader=new FileReader();
        reader.onload=()=>resolve(String(reader.result||'').split(',').at(-1));
        reader.onerror=()=>reject(Error('Não foi possível ler o arquivo.'));
        reader.readAsDataURL(file);
      });
      const type=file.type||({'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg'}[file.name.split('.').pop().toLowerCase()]);
      await api('/patients/'+patient.id+'/exams','POST',{filename:file.name,media_type:type,data_base64:b64});
      const r=await api('/patients/'+patient.id+'/exams');
      if(state.patient?.id===patient.id)state.exams=r.items||[];
    },'Exame armazenado com criptografia.');
    if(kind==='room')return invoke(async()=>{
      await api('/rooms','POST',{name:v.name});
      const r=await api('/rooms');state.rooms=r.items||[];
    },'Consultório cadastrado.');
    if(kind==='room-day')return invoke(async()=>{
      if(!/^\d{4}-\d{2}-\d{2}$/.test(v.day||''))throw Error('Selecione uma data válida.');
      const next=await api('/appointments?day='+encodeURIComponent(v.day));
      state.roomDay=v.day;state.appointments=next.items||[];state.roomZone=next.timezone||'';
    },'Consultas atualizadas.');
    if(kind==='room-reservation')return invoke(async()=>{
      if(!idRe.test(v.appointment_id||''))throw Error('Selecione uma consulta da agenda.');
      if(!state.appointments.some(x=>x.id===v.appointment_id))
        throw Error('Consulta não encontrada na data selecionada.');
      await api('/appointments/'+v.appointment_id+'/room','PUT',{room_id:v.room_id});
      const next=await api('/appointments?day='+encodeURIComponent(state.roomDay));
      state.appointments=next.items||[];
    },'Consultório vinculado à consulta.');
    if(kind==='supply')return invoke(async()=>{
      const epoch=state.epoch;state.stockCreateOpen=true;state.stockDraft={name:v.name,unit:v.unit};
      await api('/supplies','POST',{name:v.name.trim(),unit:v.unit});
      if(valid(epoch)&&state.section==='supplies'){state.stockCreateOpen=false;state.stockDraft=null;await readStock();}
    },'Insumo cadastrado.');
    if(kind==='grant')return invoke(async()=>{
      const payload={account_id:v.account_id,professional_id:v.professional_id,level:v.level,
        cro_number:v.level==='dentist'?(v.cro_number||null):null,
        cro_state:v.level==='dentist'?(v.cro_state||'').toUpperCase():null,
        professional_license_checked:v.professional_license_checked==='true'};
      await api('/clinicians','PUT',payload);await fetchAccess();
    },'Permissão clínica atualizada.');
  }
  function onChange(event){
    const el=event.target;
    if(!state.host?.contains(el))return;
    if(['prosthesis_filter','prosthesis_deadline'].includes(el.name)&&state.prostheses){
      state.success='';
      state.error='';
      const board=state.prostheses;
      void selectProstheses(el.name==='prosthesis_filter'?el.value:board.status,
        el.name==='prosthesis_deadline'?el.value==='overdue':board.overdue);
      return;
    }
    if(el.name==='supply_history_movement'&&state.supplyHistory){
      const history=state.supplyHistory;
      void selectSupplyHistory(history.supply,el.value);
      return;
    }
    if(el.name==='intake_template'){
      const template=state.intakeTemplates.find(item=>item.id===el.value);
      const answers=el.form?.querySelector('[data-dental-intake-answers]');
      if(answers)answers.innerHTML=(template?.questions||[]).map((prompt,i)=>
        field(prompt,'intake_answer_'+i,'textarea','','maxlength="1000"')).join('');
      return;
    }
    if(el.name==='dentition'){
      state.dentition=el.value;state.selectedTooth=state.dentition==='deciduous'?55:11;
      state.findings=[];render();
    }
  }
  async function fetchAccess(){
    const epoch=state.epoch;
    const data=await api('/access');if(!valid(epoch))return;
    state.access=data;
    if(data.can_manage&&!state.dedicatedStock&&!state.dedicatedProstheses&&!state.visitsOnly){
      const base='/api/v1/tenants/'+state.tenantId;
      const res=await Promise.allSettled([
        state.request(base+'/professionals'),state.request(base+'/management/team')]);
      if(!valid(epoch))return;
      state.professionals=res[0].status==='fulfilled'?(res[0].value.items||res[0].value||[]):[];
      state.team=res[1].status==='fulfilled'?(res[1].value.members||[]):[];
    }
  }
  async function mount(options){
    unmount();
    if(!options?.host||!options.request||!options.tenantId)return;
    state.host=options.host;state.request=options.request;state.tenantId=options.tenantId;
    state.canAccess=options.canAccess;state.epoch++;
    state.host.addEventListener('click',onClick);
    state.host.addEventListener('submit',onSubmit);
    state.host.addEventListener('change',onChange);
    state.dedicatedStock=options.dedicatedStock===true;state.stockInMenu=options.stockInMenu===true;
    state.dedicatedProstheses=options.dedicatedProstheses===true;state.prosthesesInMenu=options.prosthesesInMenu===true;
    state.openProsthesisPatient=options.openProsthesisPatient;
    state.visitsOnly=options.visitsOnly===true&&idRe.test(options.initialPatient||'');
    state.returnToDirectory=options.returnToDirectory;
    state.loading=true;state.section=state.dedicatedStock?'supplies':state.dedicatedProstheses?'prostheses':options.team?'access':'patients';state.error='';render();
    const epoch=state.epoch;
    try{
      await fetchAccess();
      if(valid(epoch)&&state.access?.clinical_access){
        if(state.dedicatedStock)await readStock();
        else if(state.dedicatedProstheses){if(state.access.can_read_records)await selectProstheses();}
        else if(state.visitsOnly){
          const patient=await api('/patients/'+options.initialPatient);
          if(!valid(epoch))return;
          if(patient?.id!==options.initialPatient)throw Error('A consulta não corresponde ao paciente selecionado.');
          state.patient=patient;
          const board=state.visitBoard={patient:patient.id,items:[],options:[],day:'',loading:false,ready:false};
          await fetchVisits(board);
        }else if(state.section!=='access'){
          const r=await api('/patients?limit=100');if(!valid(epoch))return;
          state.patients=r.items||[];
          if(idRe.test(options.initialPatient||''))await loadPatient(options.initialPatient,options.initialTab==='prosthesis'?'prosthesis':'anamnesis');
        }
      }
    }catch(e){if(valid(epoch))state.error=e?.message||'Não foi possível confirmar o acesso clínico.';}
    finally{if(valid(epoch)){state.loading=false;render();}}
  }
  function unmount(){
    state.epoch++;
    const host=state.host;
    if(host){host.removeEventListener('click',onClick);host.removeEventListener('submit',onSubmit);
      host.removeEventListener('change',onChange);host.replaceChildren();}
    Object.assign(state,{host:null,request:null,tenantId:'',canAccess:null,access:null,team:[],
      dedicatedProstheses:false,prosthesesInMenu:false,openProsthesisPatient:null,
      dedicatedStock:false,visitsOnly:false,returnToDirectory:null,stockInMenu:false,stockQuery:'',stockOrder:'asc',stockCursor:null,stockFailedPage:false,
      stockLoading:false,stockRead:0,stockError:'',stockCreateOpen:false,stockDraft:null,supplyMove:null,
      professionals:[],patients:[],patient:null,records:[],exams:[],intakeTemplates:[],rooms:[],appointments:[],
      roomDay:localToday(),roomZone:'',supplies:[],supplyHistory:null,visitBoard:null,prostheses:null,laboratories:null,labEditing:null,q:'',
      selectedTooth:11,dentition:'permanent',tab:'anamnesis',section:'patients',findings:[],
      periodontal:[],stages:[],creatingPatient:false,editingPatient:false,
      error:'',success:'',loading:false,submitting:false,patientRead:0,searchRead:0});
  }
  window.OrionDentalClinic=Object.freeze({mount,unmount});
})();

/* Administrative patient directory. State lives only while its tenant's page is open. */
(function(){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const identifier=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const pageSize=25;
  const birth=value=>value&&/^\d{4}-\d{2}-\d{2}$/.test(value)?value.split('-').reverse().join('/'):'';
  const button=(action,label,style='os-outline')=>'<button type="button" class="os-button '+style+'" data-patient-action="'+action+'">'+label+'</button>';
  const field=(name,label,type,value,attrs='')=>'<label class="os-dental-field"><span>'+label+'</span><input name="'+name+
    '" type="'+type+'" value="'+esc(value)+'" '+attrs+'></label>';
  let active=null;

  async function mount({host,request,tenantId,canAccess,canWrite,openVisits,openRecords,openAccess,startNew=false}){
    if(!host)return;
    const instance={};
    active=instance;
    const current=()=>active===instance&&host.isConnected&&canAccess();
    const call=(path,method='GET',data)=>request('/api/v1/tenants/'+tenantId+'/dental'+path,method,data);
    let query='',offset=0,hasMore=false,rows=[],patient=null,view='list',read=0,saving=false;
    let registration=null,access=null;
    const find=selector=>host.querySelector(selector);
    const focus=selector=>find(selector)?.focus({preventScroll:true});
    function status(message,error=false){
      const node=find('[data-patient-status]');
      if(node){node.setAttribute('role',error?'alert':'status');node.textContent=message;}
    }
    function frame(contents){
      host.innerHTML='<div class="os-dental os-directory"><header class="os-title-row os-directory-heading"><h1>Pacientes</h1>'+
        (view==='list'&&access?.clinical_access&&canWrite()?button('new','Novo paciente',''):'')+
        '</header><div class="os-dental-card os-dental-space">'+contents+
        '<p class="os-reception-notice" data-patient-status role="status"></p></div></div>';
    }
    function showList(){
      view='list';patient=null;registration=null;
      frame('<form data-patient-search class="os-directory-search">'+field('query','Nome ou telefone','search',query,
        'autocomplete="off" maxlength="120"')+
        '<button type="submit" class="os-button os-outline">Buscar</button></form>'+
        '<div data-patient-results aria-busy="true"></div>');
    }
    function renderRows(){
      const region=find('[data-patient-results]');
      region.setAttribute('aria-busy','false');
      const list=rows.map(person=>{
        const extra=[birth(person.birth_date),person.guardian_name?'Responsável · '+person.guardian_name:''].filter(Boolean);
        return '<li><button type="button" class="os-dental-patient" data-patient-id="'+esc(person.id)+'"><span><strong>'+
          esc(person.name)+'</strong>'+(extra.length?'<small>'+esc(extra.join(' · '))+'</small>':'')+
          '</span><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg></button></li>';
      }).join('');
      region.innerHTML=(list?'<ul class="os-directory-list">'+list+'</ul>':
        '<p class="os-dental-empty">'+(query?'Nenhum paciente corresponde à busca.':'Nenhum paciente cadastrado.')+'</p>')+
        (offset||hasMore?'<nav class="os-dental-controls" aria-label="Páginas de pacientes">'+
           (offset?button('previous','Anterior'):'')+'<span>Página '+(Math.floor(offset/pageSize)+1)+'</span>'+
           (hasMore?button('next','Próxima'):'')+'</nav>':'');
    }
    async function search(){
      const ticket=++read;
      const region=find('[data-patient-results]');
      region.setAttribute('aria-busy','true');region.replaceChildren();status('Buscando pacientes…');
      try{
        const data=await call('/patients/search','POST',{query,offset,limit:pageSize});
        if(!current()||ticket!==read||view!=='list')return;
        if(!Array.isArray(data?.items)||typeof data.has_more!=='boolean'||
          data.items.some(x=>!identifier.test(x?.id)||typeof x.name!=='string'))throw Error();
        rows=data.items;hasMore=data.has_more;renderRows();
        status(rows.length?(rows.length===1?'1 paciente nesta página.':rows.length+' pacientes nesta página.'):'');
      }catch{
        if(!current()||ticket!==read||view!=='list')return;
        rows=[];region.setAttribute('aria-busy','false');region.innerHTML=button('retry-search','Tentar novamente');
        status('Não foi possível consultar os pacientes.',true);
      }
    }
    function detail(message=''){
      view='detail';
      const values=[['Nascimento',birth(patient.birth_date)],['Telefone de contato',patient.contact_phone],
                    ['Responsável',patient.guardian_name],['Telefone do responsável',patient.guardian_phone]];
      frame('<div class="os-dental-actions">'+button('back','Voltar aos pacientes','os-ghost')+'</div>'+
        '<header class="os-dental-toolbar"><h2 class="os-dental-title" tabindex="-1">'+esc(patient.name)+'</h2>'+
        (canWrite()?button('edit','Editar cadastro'):'')+'</header><dl class="os-directory-facts">'+
        values.map(([label,value])=>'<div><dt>'+label+'</dt><dd>'+esc(value||'Não informado')+'</dd></div>').join('')+
        '</dl><div class="os-dental-controls">'+button('visits','Consultas do paciente')+
        (access.can_read_records?button('records','Abrir prontuário'):'')+'</div>');
      status(message);focus('h2');
    }
    async function select(id){
      if(!identifier.test(id))return;
      const ticket=++read;view='detail-loading';patient=null;
      frame(button('back','Voltar aos pacientes','os-ghost')+'<div role="status">Consultando cadastro…</div>');
      try{
        const result=await call('/patients/'+id);
        if(!current()||ticket!==read)return;
        if(result?.id!==id||typeof result.name!=='string')throw Error();
        patient=result;detail();
      }catch{
        if(!current()||ticket!==read)return;
        frame(button('back','Voltar aos pacientes','os-ghost'));status('Não foi possível abrir este cadastro.',true);
      }
    }
    function form(editing=false){
      ++read;view=editing?'edit':'new';
      const p=editing?patient:{};
      registration=editing?null:crypto.randomUUID();
      frame('<h2 class="os-dental-title" tabindex="-1">'+(editing?'Editar cadastro':'Novo paciente')+'</h2>'+
        '<form data-patient-editor class="os-dental-space" autocomplete="off"><div class="os-directory-fields">'+
        field('name','Nome completo','text',p.name,'required minlength="2" maxlength="120"')+
        field('birth_date','Data de nascimento (opcional)','date',p.birth_date)+
        field('contact_phone','Telefone do paciente (opcional)','tel',p.contact_phone,'maxlength="30"')+'</div>'+
        '<fieldset class="os-directory-guardian"><legend>Responsável (opcional)</legend><div class="os-directory-fields">'+
        field('guardian_name','Nome do responsável','text',p.guardian_name,'minlength="2" maxlength="120"')+
        field('guardian_phone','Telefone do responsável','tel',p.guardian_phone,'maxlength="30"')+'</div></fieldset>'+
        '<div class="os-dental-controls">'+button('cancel','Cancelar')+'<button type="submit" class="os-button">'+
        (editing?'Salvar alterações':'Cadastrar paciente')+'</button></div></form>');
      focus('[name=name]');
    }
    async function save(formEl){
      if(saving||!canWrite()||!['edit','new'].includes(view))return;
      if(!formEl.reportValidity())return;
      const data=Object.fromEntries(new FormData(formEl));
      for(const key of Object.keys(data))data[key]=data[key].trim()||null;
      if(!data.name||data.name.length<2){status('Informe o nome do paciente.',true);focus('[name=name]');return;}
      const editing=view==='edit',id=editing?patient.id:null,ticket=++read;
      saving=true;formEl.setAttribute('aria-busy','true');status('Salvando cadastro…');
      const submit=formEl.querySelector('[type=submit]');
      submit.disabled=true;
      try{
        const result=editing?
          await call('/patients/'+id,'PATCH',{...data,customer_id:patient.customer_id||null,expected_version:patient.version}):
          await call('/patients','POST',{...data,request_id:registration});
        if(!current()||ticket!==read)return;
        if(!identifier.test(result?.id)||(editing&&result.id!==id)||typeof result.name!=='string'||
           !Number.isInteger(result.version))throw Error('A resposta não confirmou o cadastro. Tente novamente.');
        patient=result;registration=null;detail(editing?'Cadastro atualizado.':'Paciente cadastrado.');
      }catch(error){
        if(!current()||ticket!==read)return;
        status(error?.message||'Não foi possível confirmar a gravação. Tente novamente.',true);
      }finally{
        if(current()&&ticket===read){saving=false;formEl.removeAttribute('aria-busy');submit.disabled=false;}
      }
    }
    async function listAndFocus(){
      showList();focus('[name=query]');await search();
    }
    host.onclick=async event=>{
      if(!current()||saving)return;
      const el=event.target.closest('button');if(!el||!host.contains(el))return;
      const action=el.dataset.patientAction;
      if(action==='retry-access'){await checkAccess();return;}
      if(action==='access'){openAccess();return;}
      if(!access?.clinical_access)return;
      if(el.dataset.patientId){await select(el.dataset.patientId);return;}
      if(action==='new'&&canWrite()){form();return;}
      if(action==='edit'&&patient&&canWrite()){form(true);return;}
      if(action==='cancel'&&view==='edit'){detail();return;}
      if(action==='back'||action==='cancel'){await listAndFocus();return;}
      if(action==='visits'&&patient){openVisits(patient.id);return;}
      if(action==='records'&&patient&&access.can_read_records){openRecords(patient.id);return;}
      if(view!=='list')return;
      if(action==='previous'&&offset)offset=Math.max(0,offset-pageSize);
      else if(action==='next'&&hasMore)offset+=pageSize;
      else if(action!=='retry-search')return;
      await search();if(current())focus('[name=query]');
    };
    host.onsubmit=async event=>{
      event.preventDefault();if(!current()||!access?.clinical_access)return;
      if(event.target.matches('[data-patient-editor]')){await save(event.target);return;}
      if(!event.target.matches('[data-patient-search]'))return;
      query=event.target.elements.query.value.trim();offset=0;await search();
    };
    async function checkAccess(){
      const ticket=++read;
      frame('<div role="status">Verificando acesso ao cadastro…</div>');
      try{
        const result=await call('/access');
        if(!current()||ticket!==read)return;
        access=result;
        if(!access?.clinical_access){
          frame('<p>A equipe responsável precisa liberar seu acesso ao cadastro de pacientes.</p>'+
            (access?.can_manage?button('access','Abrir equipe clínica'):''));
          return;
        }
        if(startNew&&canWrite()){form();return;}
        showList();await search();
      }catch{
        if(!current()||ticket!==read)return;
        frame(button('retry-access','Verificar acesso'));
        status('Não foi possível verificar a permissão para o cadastro de pacientes.',true);
      }
    }
    await checkAccess();
  }
  window.OrionDentalDirectory=Object.freeze({mount,unmount(){active=null;}});
})();
