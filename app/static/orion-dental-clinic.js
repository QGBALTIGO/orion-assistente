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
    patients:[],patient:null,records:[],exams:[],rooms:[],appointments:[],roomDay:localToday(),
    roomZone:'',supplies:[],q:'',selectedTooth:11,
    dentition:'permanent',tab:'anamnesis',section:'patients',findings:[],periodontal:[],stages:[],
    error:'',success:'',loading:false,submitting:false};
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
  function supplyManager(){
    return '<div class="os-dental-space"><div class="os-dental-card"><h2 class="os-dental-title">Estoque e insumos</h2>'+
      '<p class="os-dental-muted">Entradas e saídas são transações registradas; estoque nunca pode ficar negativo.</p>'+
      '<form data-dental-form="supply" class="os-dental-row">'+field('Insumo','name','text','','required maxlength="120"')+
      choices('Unidade','unit',[['unit','Unidade'],['box','Caixa'],['ml','mL'],['g','Grama'],['kit','Kit']])+
      '<div class="os-dental-field"><label>Cadastro</label><button class="os-button" type="submit">Adicionar</button></div></form></div>'+
      '<div class="os-dental-card">'+(state.supplies.length?'<div class="os-dental-table-wrap"><table class="os-dental-table"><thead><tr><th>Insumo</th><th>Quantidade</th><th>Movimentar</th></tr></thead><tbody>'+
       state.supplies.map(x=>'<tr><td>'+esc(x.name)+'</td><td>'+esc(x.quantity)+' '+esc(x.unit)+'</td><td>'+button('Entrada','supply-in','os-outline','data-id="'+esc(x.id)+'"')+' '+button('Saída','supply-out','os-outline','data-id="'+esc(x.id)+'"')+'</td></tr>').join('')+'</tbody></table></div>':
       '<div class="os-dental-empty">Nenhum insumo cadastrado.</div>')+'</div></div>';
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
       (state.team.length?state.team.map(x=>[x.account_id,x.name||x.email||x.account_id]):
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
    if(kind==='anamnesis')fields=field('Queixa principal','complaint','textarea','','required maxlength="2500"')+
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
    else if(kind==='prosthesis')fields=field('Laboratório','laboratory','text','','required maxlength="120"')+
      field('Serviço ou peça','description','textarea','','required maxlength="3000"')+
      field('Prazo estimado','due_date','date')+
      choices('Situação','status',[['requested','Solicitado'],['working','Em produção'],['delivered','Entregue'],['cancelled','Cancelado']]);
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
    return Object.entries(safeItem(data)).map(([k,v])=>
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
  function patientPanel(){
    if(!state.patient)return '<div class="os-dental-card os-dental-empty">Selecione um paciente para abrir a área clínica. Nenhum diagnóstico ou medição é preenchido automaticamente.</div>';
    if(state.access?.can_read_records!==true)
      return '<div class="os-dental-card os-dental-space"><h2 class="os-dental-title">'+esc(state.patient.name)+'</h2>'+
        info('Este acesso administrativo não inclui consulta ou edição de prontuários, odontogramas, anamnese, periodontia ou exames.')+'</div>';
    return '<div class="os-dental-space"><div class="os-dental-card os-dental-space"><div class="os-dental-toolbar">'+
       '<div><p class="os-dental-kicker">Paciente da clínica</p><h2 class="os-dental-title">'+esc(state.patient.name)+'</h2>'+
       '<p class="os-dental-muted">Nascimento: '+esc(dateInput(state.patient.birth_date)||'Não informado')+
       ' · Responsável: '+esc(state.patient.guardian_name||'Não informado')+'</p></div>'+
       '<div class="os-dental-actions">'+button('Editar cadastro','edit-patient')+
       button('Atualizar','refresh-patient')+'</div></div>'+
       info('As anotações são protegidas e possuem autoria, mas não estão homologadas como documentos assinados digitalmente.')+'</div>'+
       '<div class="os-dental-card"><nav class="os-dental-tabs" aria-label="Áreas odontológicas">'+
       topics.map(([kind,label])=>'<button type="button" class="os-pill-tab" data-dental-tab="'+kind+'" role="tab" aria-selected="'+String(state.tab===kind)+'">'+esc(label)+'</button>').join('')+'</nav>'+
       '</div>'+editor(state.tab)+'</div>';
  }
  function render(){
    const host=state.host;if(!host)return;
    const allowed=!!state.access?.clinical_access;
    let html='<div class="os-dental os-dental-space">'+
      '<div class="os-dental-toolbar"><div><p class="os-dental-kicker">Orion • Odontologia</p>'+
      '<h2 class="os-dental-title">Gestão odontológica</h2></div>'+
      '<div class="os-dental-actions">'+
        button('Pacientes','section-patients',state.section==='patients'?'':'os-outline')+
        button('Consultórios','section-rooms',state.section==='rooms'?'':'os-outline')+
        button('Estoque','section-supplies',state.section==='supplies'?'':'os-outline')+
        button('Equipe clínica','section-access',state.section==='access'?'':'os-outline')+
      '</div></div>';
    if(state.error)html+=info(state.error,true);
    if(state.success)html+=info(state.success);
    if(state.loading)html+=pending();
    else if(!state.access)html+=info('Não foi possível confirmar as permissões clínicas.',true);
    else if(state.section==='access')html+=accessManager();
    else if(!allowed)html+=info('O acesso ao prontuário ainda não foi concedido a esta conta. Abra Equipe clínica se você for o proprietário.');
    else if(state.section==='rooms')html+=roomManager();
    else if(state.section==='supplies')html+=supplyManager();
    else if(state.section==='patients')html+=
       '<div class="os-dental-grid">'+patientList()+'<div class="os-dental-space">'+
       (state.creatingPatient||state.editingPatient?patientCreate():patientPanel())+'</div></div>';
    html+='</div>';
    host.innerHTML=html;
  }
  async function invoke(fn,success){
    if(state.submitting)return;
    const epoch=state.epoch;state.submitting=true;state.error='';state.success='';
    try{await fn();if(valid(epoch)){state.success=success||'Registro salvo com segurança.';render();}}
    catch(error){if(valid(epoch)){state.error=error?.message||'Não foi possível salvar.';render();}}
    finally{state.submitting=false;}
  }
  async function loadPatient(patient){
    const epoch=state.epoch;
    state.loading=true;state.error='';render();
    try{
      const authorized=state.access?.can_read_records===true;
      const [detail,records,exams]=await Promise.all([
        api('/patients/'+patient),
        authorized?api('/patients/'+patient+'/records?limit=100'):Promise.resolve({items:[]}),
        authorized?api('/patients/'+patient+'/exams'):Promise.resolve({items:[]})]);
      if(!valid(epoch))return;
      state.patient=detail;state.records=records.items||[];state.exams=exams.items||[];
      state.tab='anamnesis';state.findings=[];state.periodontal=[];state.stages=[];
    }catch(e){if(valid(epoch))state.error=e?.message||'Falha ao consultar paciente.';}
    finally{if(valid(epoch)){state.loading=false;render();}}
  }
  async function refreshPatients(){
    const epoch=state.epoch;state.loading=true;render();
    try{const result=await api('/patients?q='+encodeURIComponent(state.q)+'&limit=100');
      if(valid(epoch))state.patients=result.items||[];
    }catch(e){if(valid(epoch))state.error=e?.message||'Não foi possível buscar pacientes.';}
    finally{if(valid(epoch)){state.loading=false;render();}}
  }
  async function loadManagers(section){
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
      }else{
        const res=await api('/supplies');
        if(valid(epoch))state.supplies=res.items||[];
      }
    }catch(e){if(valid(epoch))state.error=e?.message||'Não foi possível carregar os cadastros.';}
    finally{if(valid(epoch)){state.loading=false;render();}}
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
    if(kind==='anamnesis')return {complaint:v.complaint,medical_history:v.medical_history||null,
      declared_allergies:v.declared_allergies||null,declared_medications:v.declared_medications||null,
      observations:v.observations||null,answered_by:v.answered_by};
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
    if(btn.dataset.dentalTab){state.tab=btn.dataset.dentalTab;state.findings=[];state.periodontal=[];state.stages=[];state.error='';render();return;}
    const act=btn.dataset.dentalAction;
    if(act?.startsWith('section-')){
      const section=act.slice(8);state.section=section;state.error='';state.success='';
      render();
      if(section==='rooms'||section==='supplies')await loadManagers(section);
      return;
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
    if((act==='supply-in'||act==='supply-out')&&idRe.test(btn.dataset.id||'')){
      const value=prompt('Quantidade de '+(act==='supply-in'?'entrada':'saída')+' (inteiro positivo):');
      if(value===null)return;
      if(!/^[1-9][0-9]{0,6}$/.test(value)){state.error='Informe um inteiro positivo até 1.000.000.';render();return;}
      await invoke(async()=>{await api('/supplies/'+btn.dataset.id+'/movements','POST',{
        movement:act==='supply-in'?'in':'out',quantity:Number(value),entry_key:crypto.randomUUID()});
        const r=await api('/supplies');state.supplies=r.items||[];},'Estoque atualizado.');
    }
  }
  async function onSubmit(event){
    const form=event.target.closest('form[data-dental-form]');
    if(!form||!state.host?.contains(form))return;
    event.preventDefault();
    const kind=form.dataset.dentalForm,v=formValues(form),patient=state.patient;
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
      const r=await api('/patients/'+patient.id+'/exams');state.exams=r.items||[];
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
      await api('/supplies','POST',{name:v.name,unit:v.unit});
      const r=await api('/supplies');state.supplies=r.items||[];
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
    if(el.name==='dentition'){
      state.dentition=el.value;state.selectedTooth=state.dentition==='deciduous'?55:11;
      state.findings=[];render();
    }
  }
  async function fetchAccess(){
    const epoch=state.epoch;
    const data=await api('/access');if(!valid(epoch))return;
    state.access=data;
    if(data.can_manage){
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
    state.loading=true;state.section='patients';state.error='';render();
    const epoch=state.epoch;
    try{
      await fetchAccess();
      if(valid(epoch)&&state.access?.clinical_access){
        const r=await api('/patients?limit=100');if(valid(epoch))state.patients=r.items||[];
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
      professionals:[],patients:[],patient:null,records:[],exams:[],rooms:[],appointments:[],
      roomDay:localToday(),roomZone:'',supplies:[],q:'',
      selectedTooth:11,dentition:'permanent',tab:'anamnesis',section:'patients',findings:[],
      periodontal:[],stages:[],creatingPatient:false,editingPatient:false,
      error:'',success:'',loading:false,submitting:false});
  }
  window.OrionDentalClinic=Object.freeze({mount,unmount});
})();
