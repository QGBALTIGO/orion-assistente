/* Administrative dentistry screens. All identity and scheduling writes go to the verified reception API. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => (
    {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]
  ));
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const birth = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value.split('-').reverse().join('/') : '';
  const button = (action, label, style = 'os-outline') =>
    '<button type="button" class="os-button ' + style + '" data-dental-action="' + action + '">' + label + '</button>';
  const field = (name, label, type, value, attrs = '') => '<label class="os-dental-field"><span>' + label +
    '</span><input name="' + name + '" type="' + type + '" value="' + esc(value) + '" ' + attrs + '></label>';
  const select = (name, label, values, current = '', attrs = '') => '<label class="os-dental-field"><span>' + label +
    '</span><select name="' + name + '" ' + attrs + '>' + values.map(([id, text]) => option(id, text, id === current)).join('') +
    '</select></label>';
  const option = (id, label, selected = false) => '<option value="' + esc(id) + '"' + (selected ? ' selected' : '') + '>' +
    esc(label) + '</option>';
  const textarea = (name, label, value) => '<label class="os-dental-field"><span>' + label +
    '</span><textarea name="' + name + '" rows="2" maxlength="500">' + esc(value) + '</textarea></label>';
  const val = (data, name) => String(data.get(name) || '').trim() || null;
  const pageSize = 25;
  const regionStates = {hold:'Reserva temporária', confirmed:'Agendada', completed:'Concluída', cancelled:'Cancelada', no_show:'Falta'};
  const states = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(' ');
  const visitTypes = [['evaluation','Avaliação'], ['first_visit','Primeira consulta'], ['return','Retorno'],
    ['session','Sessão de tratamento'], ['emergency','Urgência']];
  const visitLabel = type => visitTypes.find(([id]) => id === type)?.[1] || '';
  const patientInputs = (p = {}) => '<div class="os-directory-fields">' +
    field('name', 'Nome do paciente', 'text', p.name, 'required minlength="2" maxlength="120" autocomplete="off"') +
    field('birth_date', 'Data de nascimento', 'date', p.birth_date, 'min="1900-01-01"') +
    field('contact_phone', 'Celular do paciente', 'tel', p.contact_phone, 'maxlength="30" autocomplete="off"') + '</div>';
  const guardianInputs = (p = {}) => '<div class="os-directory-fields">' +
    field('guardian_name', 'Nome do responsável legal', 'text', p.guardian_name, 'minlength="2" maxlength="120"') +
    field('guardian_phone', 'Celular do responsável', 'tel', p.guardian_phone, 'maxlength="30"') + '</div>';
  const patientValues = data => ({
    name: val(data, 'name'), birth_date: val(data, 'birth_date'), contact_phone: val(data, 'contact_phone'),
    guardian_name: val(data, 'guardian_name'), guardian_phone: val(data, 'guardian_phone')
  });
  const identifyPatient = person => [
    birth(person.birth_date),
    person.guardian_name ? 'Responsável · ' + person.guardian_name : '',
    person.contact_phone || person.guardian_phone || ''
  ].filter(Boolean).join(' · ');
  const patientResult = (person, action = 'patient') => '<li><button type="button" class="os-dental-patient" data-dental-action="' +
    action + '" data-id="' + esc(person.id) + '"><span><strong>' + esc(person.name) + '</strong>' +
    (identifyPatient(person) ? '<small>' + esc(identifyPatient(person)) + '</small>' : '') + '</span></button></li>';
  const moment = (value, timeZone) => new Intl.DateTimeFormat('pt-BR',
    {dateStyle:'short', timeStyle:'short', timeZone}).format(new Date(value));

  let directoryInstance;
  function mountDirectory({host, call, clinicalAccess, canAccess, canWrite, openAccess, openVisits, openRecords,
                          openClinical, openAppointment, newAppointment, startNew = false, initialPatient}) {
    const instance = {};
    directoryInstance = instance;
    const current = () => directoryInstance === instance && host.isConnected && canAccess();
    const find = selector => host.querySelector(selector);
    let view = 'list', query = '', offset = 0, hasMore = false, generation = 0, saving = false;
    let patient, registration, permission, visitsOffset = 0, plansPromise;
    function status(message, error = false) {
      const node = find('[data-dental-status]');
      if (node) {
        node.setAttribute('role', error ? 'alert' : 'status');
        node.textContent = message;
      }
    }
    function frame(body) {
      host.innerHTML = '<div class="os-dental os-directory"><header class="os-title-row os-directory-heading"><h1>Pacientes</h1>' +
        (view === 'list' && canWrite() ? button('new', 'Novo paciente', '') : '') +
        '</header><div class="os-dental-card os-dental-space">' + body +
        '<p data-dental-status class="os-reception-notice" role="status"></p></div></div>';
    }
    async function list({focus = false} = {}) {
      const ticket = ++generation;
      view = 'list';
      patient = null;
      frame('<form data-dental-patient-search class="os-directory-search">' +
        field('query', 'Buscar pacientes', 'search', query, 'placeholder="Nome, CPF ou telefone" maxlength="120" autocomplete="off"') +
        '<button type="submit" class="os-button os-outline">Buscar</button></form><div data-dental-results aria-busy="true"></div>');
      if (focus) find('[name=query]').focus();
      status('Buscando pacientes…');
      try {
        const response = await call('/patients/search', 'POST', {query, offset, limit:pageSize});
        if (!current() || ticket !== generation) return;
        const items = response.items;
        hasMore = response.has_more;
        const region = find('[data-dental-results]');
        region.setAttribute('aria-busy', 'false');
        region.innerHTML = (items.length ? '<ul class="os-directory-list">' + items.map(person => patientResult(person)).join('') + '</ul>' :
          '<p class="os-dental-empty">' + (query ? 'Nenhum paciente encontrado.' : 'Nenhum paciente cadastrado.') + '</p>' +
          (!query && canWrite() ? button('new', 'Cadastrar primeiro paciente') : '')) +
          (offset || hasMore ? '<nav class="os-dental-controls" aria-label="Páginas de pacientes">' +
            (offset ? button('previous', 'Anterior') : '') + '<span>Página ' + (Math.floor(offset / pageSize) + 1) + '</span>' +
            (hasMore ? button('next', 'Próxima') : '') + '</nav>' : '');
        status('');
      } catch (error) {
        if (!current() || ticket !== generation) return;
        const region = find('[data-dental-results]');
        region.setAttribute('aria-busy', 'false');
        region.innerHTML = button('retry', 'Tentar novamente');
        status(error.message || 'Não foi possível consultar os pacientes.', true);
      }
    }
    function profileHeader(active) {
      return button('back', 'Voltar aos pacientes', 'os-ghost') +
        '<header class="os-dental-toolbar"><div><h2 class="os-dental-title" tabindex="-1">' + esc(patient.name) + '</h2>' +
        (identifyPatient(patient) ? '<p>' + esc(identifyPatient(patient)) + '</p>' : '') +
        '</div><div class="os-dental-controls">' + (canWrite() ? button('edit', 'Editar cadastro') : '') +
        (newAppointment && canWrite() ? button('appointment', 'Nova consulta', '') : '') + '</div></header>' +
        '<div class="os-pill-tabs" aria-label="Seções da ficha">' +
        [['profile','Cadastro'],['visits','Consultas'],['clinical','Clínica']].map(([id, label]) =>
          '<button type="button" class="os-pill-tab" aria-pressed="' + (active === id) + '" data-dental-action="' + id + '">' +
          label + '</button>').join('') + '</div>';
    }
    function profile(message = '') {
      view = 'detail';
      const info = patient.administrative_profile || {}, address = info.address || {};
      const facts = [['Nascimento', birth(patient.birth_date)], ['Celular do paciente', patient.contact_phone],
        ['E-mail', info.email], ['CPF', info.cpf], ['Documento', info.identity_document], ['Prontuário', info.record_number],
        ['Responsável legal', [patient.guardian_name, info.guardian_relationship, patient.guardian_phone].filter(Boolean).join(' · ')],
        ['Responsável financeiro', [info.financial_name, info.financial_phone].filter(Boolean).join(' · ')],
        ['Carteirinha do convênio', info.insurance_number], ['Origem', info.origin], ['Profissão', info.occupation],
        ['Endereço', [address.street, address.number, address.complement, address.neighborhood, address.city, address.state,
          address.postal_code].filter(Boolean).join(' · ')], ['Observação administrativa', info.administrative_note]];
      frame(profileHeader('profile') + '<dl class="os-directory-facts">' + facts.filter(([,value]) => value).map(([label,value]) =>
        '<div><dt>' + label + '</dt><dd>' + esc(value) + '</dd></div>').join('') +
        '</dl>' + (facts.some(([,value]) => value) ? '' : '<p class="os-dental-empty">O cadastro contém apenas o nome.</p>'));
      status(message);
      find('h2').focus({preventScroll:true});
      if (info.insurance_plan_id) {
        const selected = patient.id;
        readPlans().then(items => {
          if (!current() || patient?.id !== selected || view !== 'detail') return;
          const plan = items.find(item => item.id === info.insurance_plan_id);
          if (!plan) return;
          find('dl').insertAdjacentHTML('beforeend', '<div><dt>Convênio</dt><dd>' + esc(plan.insurer + ' · ' + plan.plan) + '</dd></div>');
        }).catch(() => {});
      }
    }
    async function loadPatient(id) {
      if (!uuid.test(id)) return;
      const ticket = ++generation;
      view = 'detail-loading';
      frame(button('back', 'Voltar aos pacientes', 'os-ghost') + '<div role="status">Consultando cadastro…</div>');
      try {
        const response = await call('/patients/' + id);
        if (!current() || ticket !== generation) return;
        if (response?.id !== id) throw Error('A resposta não identificou o cadastro.');
        patient = response;
        profile();
      } catch (error) {
        if (!current() || ticket !== generation) return;
        frame(button('back', 'Voltar aos pacientes', 'os-ghost'));
        status(error.message, true);
      }
    }
    function readPlans() {
      plansPromise ||= call('/insurance-options').then(response => response.items).catch(error => {
        plansPromise = null;
        throw error;
      });
      return plansPromise;
    }
    function editor() {
      ++generation;
      const editing = Boolean(patient), p = patient || {}, details = p.administrative_profile || {}, address = details.address || {};
      view = editing ? 'edit' : 'new';
      registration = editing ? null : crypto.randomUUID();
      frame('<h2 class="os-dental-title">' + (editing ? 'Editar cadastro' : 'Novo paciente') + '</h2>' +
        '<form data-dental-patient-editor class="os-dental-space" autocomplete="off">' + patientInputs(p) +
        '<details class="os-more-fields"' + (p.guardian_name ? ' open' : '') + '><summary>Responsável legal</summary>' +
        '<div class="os-disclosure-body">' + guardianInputs(p) +
        field('guardian_relationship','Vínculo com o paciente','text',details.guardian_relationship,'maxlength="80"') + '</div></details>' +
        '<details class="os-more-fields"><summary>Identificação e contato</summary><div class="os-disclosure-body os-directory-fields">' +
        field('cpf','CPF','text',details.cpf,'inputmode="numeric" maxlength="18"') +
        field('identity_document','Documento de identidade','text',details.identity_document,'maxlength="35"') +
        field('email','E-mail','email',details.email,'maxlength="254"') +
        field('secondary_phone','Telefone adicional','tel',details.secondary_phone,'maxlength="30"') +
        field('record_number','Número de prontuário','text',details.record_number,'maxlength="40"') +
        field('occupation','Profissão','text',details.occupation,'maxlength="120"') +
        field('origin','Como conheceu a clínica?','text',details.origin,'maxlength="120"') +
        '</div></details><details class="os-more-fields"><summary>Endereço</summary><div class="os-disclosure-body os-directory-fields">' +
        field('postal_code','CEP','text',address.postal_code,'inputmode="numeric" maxlength="9"') +
        field('street','Logradouro','text',address.street,'maxlength="160"') +
        field('number','Número','text',address.number,'maxlength="20"') +
        field('complement','Complemento','text',address.complement,'maxlength="120"') +
        field('neighborhood','Bairro','text',address.neighborhood,'maxlength="100"') +
        field('city','Cidade','text',address.city,'maxlength="100"') +
        select('state','UF', [['','Selecionar'], ...states.map(state => [state,state])], address.state) +
        '</div></details><details class="os-more-fields" data-dental-insurance><summary>Convênio e responsável financeiro</summary>' +
        '<div class="os-disclosure-body os-directory-fields">' +
        select('insurance_plan_id','Convênio', [['','Particular'], ...(details.insurance_plan_id ? [[details.insurance_plan_id,'Convênio do cadastro']] : [])],
          details.insurance_plan_id) +
        field('insurance_number','Número da carteirinha','text',details.insurance_number,'maxlength="70"') +
        field('financial_name','Nome do responsável financeiro','text',details.financial_name,'maxlength="120"') +
        field('financial_phone','Celular do responsável financeiro','tel',details.financial_phone,'maxlength="30"') +
        '<div data-dental-plans-status></div></div></details>' +
        '<details class="os-more-fields"><summary>Observações administrativas</summary><div class="os-disclosure-body">' +
        textarea('administrative_note','Informações para a recepção',details.administrative_note) +
        '</div></details><div class="os-dental-controls">' + button('cancel','Cancelar') +
        '<button type="submit" class="os-button">' + (editing ? 'Salvar alterações' : 'Cadastrar paciente') + '</button></div></form>');
      const insurer = find('[data-dental-insurance]');
      const editorForm = find('[data-dental-patient-editor]');
      editorForm.addEventListener('invalid', event => {
        const section = event.target.closest('details');
        if (section) section.open = true;
      }, true);
      insurer.addEventListener('toggle', async () => {
        if (!insurer.open || !insurer.isConnected) return;
        const plan = insurer.querySelector('[name=insurance_plan_id]'), notice = insurer.querySelector('[data-dental-plans-status]');
        notice.textContent = 'Consultando convênios…';
        try {
          const items = await readPlans(), selected = plan.value;
          if (!current() || !insurer.isConnected) return;
          plan.replaceChildren(new Option('Particular',''), ...items.filter(item => item.active || item.id === selected)
            .map(item => new Option(item.insurer + ' · ' + item.plan + (item.active ? '' : ' · Inativo'), item.id)));
          if (selected) {
            if (!Array.from(plan.options).some(item => item.value === selected)) plan.add(new Option('Convênio do cadastro', selected));
            plan.value = selected;
          }
          notice.textContent = items.some(item => item.active) ? '' : 'Cadastre um convênio em Gestão › Convênios.';
        } catch {
          notice.textContent = 'Convênios indisponíveis. Feche e abra esta seção para tentar novamente.';
        }
      });
      find('[name=name]').focus();
    }
    async function save(form) {
      if (saving || !canWrite()) return;
      const data = new FormData(form);
      const props = ['cpf','identity_document','email','secondary_phone','record_number','occupation','origin','guardian_relationship',
        'financial_name','financial_phone','insurance_plan_id','insurance_number','administrative_note'];
      const address = Object.fromEntries(['postal_code','street','number','complement','neighborhood','city','state'].map(name => [name,val(data,name)]));
      const administrative_profile = {...Object.fromEntries(props.map(name => [name,val(data,name)])),
        address: Object.values(address).some(Boolean) ? address : null};
      const payload = {...patientValues(data), administrative_profile};
      const previous = patient;
      saving = true;
      form.setAttribute('aria-busy','true');
      form.querySelector('[type=submit]').disabled = true;
      status('Salvando cadastro…');
      try {
        const result = previous ? await call('/patients/' + previous.id,'PATCH',{...payload,expected_version:previous.version}) :
          await call('/patients','POST',{...payload,request_id:registration});
        if (!current()) return;
        if (!uuid.test(result?.id)) throw Error('Não foi possível confirmar o cadastro.');
        patient = result;
        profile(previous ? 'Cadastro atualizado.' : 'Paciente cadastrado.');
      } catch (error) {
        if (current()) status(error.message || 'Não foi possível salvar o paciente.', true);
      } finally {
        saving = false;
        form.removeAttribute('aria-busy');
        form.querySelector('[type=submit]').disabled = false;
      }
    }
    async function visits() {
      const ticket = ++generation;
      view = 'visits';
      frame(profileHeader('visits') + '<div data-dental-visit-list aria-busy="true">Consultando as visitas…</div>');
      try {
        const result = await call('/patients/' + patient.id + '/visits?offset=' + visitsOffset);
        if (!current() || ticket !== generation) return;
        const node = find('[data-dental-visit-list]');
        node.setAttribute('aria-busy','false');
        node.innerHTML = (result.items.length ? '<ul class="os-directory-list">' + result.items.map(visit =>
          '<li><button type="button" class="os-dental-patient" data-dental-action="open-visit" data-id="' + esc(visit.id) + '"><span><strong>' +
          esc(moment(visit.start_at, visit.timezone)) + '</strong><small>' + esc(visit.professional + ' · ' + (regionStates[visit.status] || visit.status)) +
          '</small></span></button></li>').join('') + '</ul>' : '<p class="os-dental-empty">Nenhuma consulta vinculada.</p>') +
          '<div class="os-dental-controls">' + (visitsOffset ? button('visits-prev','Anterior') : '') +
          (result.has_more ? button('visits-next','Próxima') : '') +
          (permission?.clinical_access ? button('link-visit','Vincular consulta anterior') : '') + '</div>';
      } catch (error) {
        if (!current() || ticket !== generation) return;
        find('[data-dental-visit-list]').setAttribute('aria-busy','false');
        find('[data-dental-visit-list]').innerHTML = button('visits','Tentar novamente');
        status(error.message, true);
      }
    }
    async function clinical() {
      const ticket = ++generation;
      view = 'clinical';
      frame(profileHeader('clinical') + '<div data-dental-clinical-links aria-busy="true">Verificando o acesso profissional…</div>');
      try {
        permission = await clinicalAccess();
        if (!current() || ticket !== generation) return;
        const node = find('[data-dental-clinical-links]');
        node.setAttribute('aria-busy','false');
        node.innerHTML = permission.can_read_records ? '<div class="os-dental-controls">' + button('records','Prontuário') +
          [['anamnesis','Anamnese'],['evolution','Evolução'],['odontogram','Odontograma'],['treatment','Planos de tratamento'],
           ['budget','Orçamentos'],['exams','Exames e imagens']].map(([key,label]) => button('topic-' + key,label)).join('') +
          '</div>' : '<p>Os dados clínicos exigem acesso de cirurgião-dentista.</p>' +
          (permission.can_manage ? button('access','Gerenciar acesso clínico') : '');
      } catch (error) {
        if (!current() || ticket !== generation) return;
        const node = find('[data-dental-clinical-links]');
        node.setAttribute('aria-busy','false');
        node.innerHTML = button('clinical','Tentar novamente');
        status(error.message, true);
      }
    }
    host.onclick = async event => {
      if (!current() || saving) return;
      const target = event.target.closest('[data-dental-action]');
      if (!target || !host.contains(target)) return;
      const action = target.dataset.dentalAction;
      if (action === 'patient') return loadPatient(target.dataset.id);
      if (action === 'new' || action === 'edit') return editor();
      if ((action === 'cancel' && patient) || action === 'profile') return profile();
      if (action === 'back' || action === 'cancel' || action === 'retry') {
        visitsOffset = 0;
        return list({focus:true});
      }
      if (action === 'previous' || action === 'next') {
        offset = Math.max(0, offset + (action === 'next' ? pageSize : -pageSize));
        return list({focus:true});
      }
      if (action === 'visits' || action === 'visits-next' || action === 'visits-prev') {
        visitsOffset = Math.max(0, visitsOffset + (action === 'visits-next' ? 15 : action === 'visits-prev' ? -15 : 0));
        return visits();
      }
      if (action === 'clinical') return clinical();
      if (action === 'access') return openAccess();
      if (action === 'open-visit' && uuid.test(target.dataset.id)) return openAppointment?.(target.dataset.id);
      if (action === 'appointment' && patient) return newAppointment?.(patient);
      if (action === 'link-visit' && patient && permission?.clinical_access) return openVisits(patient.id);
      if (action === 'records' && patient && permission?.can_read_records) return openRecords(patient.id);
      if (action.startsWith('topic-') && patient && permission?.can_read_records) return openClinical(action.slice(6), patient.id);
    };
    host.onsubmit = event => {
      event.preventDefault();
      if (!current() || saving) return;
      if (event.target.matches('[data-dental-patient-editor]')) return save(event.target);
      if (event.target.matches('[data-dental-patient-search]')) {
        query = event.target.elements.query.value.trim();
        offset = 0;
        return list({focus:true});
      }
    };
    if (initialPatient) return loadPatient(initialPatient);
    if (startNew && canWrite()) return editor();
    return list();
  }

  async function mountBooking({host, call, canAccess, preset, day, clockLabel, localTimestamp, today,
                               close, setupActions, setBusy, onBooked}) {
    const find = selector => host.querySelector(selector);
    const current = () => host.isConnected && canAccess();
    let catalogue, patient = preset.patient || null, searchRows = [], searchTimer, searchVersion = 0, slotVersion = 0;
    let quickKey, previewKey, previewFingerprint, busy = false, slotLoading = false;
    const showStatus = (message, error = false) => {
      const node = find('[data-dental-booking-status]');
      if (!node) return;
      node.setAttribute('role', error ? 'alert' : 'status');
      node.textContent = message;
    };
    const actions = available => '<div class="os-dental-controls">' + available
      .filter(key => setupActions[key]).map(key => button('setup-' + key,
        {professional:'Cadastrar profissional', procedure:'Cadastrar procedimento', hours:'Configurar horários',
          links:'Vincular procedimentos', room:'Cadastrar consultório'}[key])).join('') + '</div>';
    const draw = () => {
      const disabled = !catalogue.professionals.length || !catalogue.procedures.length;
      const professional = catalogue.professionals.find(item => item.id === preset.professionalId);
      host.innerHTML = '<form data-dental-booking class="os-form"><fieldset class="os-form"><div class="os-dental-booking-patient">' +
        '<label class="os-dental-field"><span>Paciente</span><input name="patient_search" type="search" ' +
        'placeholder="Buscar por nome, CPF ou telefone" maxlength="120" autocomplete="off" aria-controls="osDentalBookingPatients"></label>' +
        '<ul id="osDentalBookingPatients" class="os-directory-list" aria-label="Resultados da busca"></ul>' +
        '<div data-dental-selected-patient></div>' + button('quick', 'Cadastrar paciente aqui', 'os-ghost') +
        '</div><div class="os-directory-fields">' +
        select('visit_type','Tipo de consulta',visitTypes,'evaluation','required') +
        select('professional_id','Profissional',[['','Selecionar profissional'], ...catalogue.professionals.map(item => [item.id,item.name])],
          professional?.id || (catalogue.professionals.length === 1 ? catalogue.professionals[0].id : ''),'required') +
        select('procedure_id','Procedimento',[['','Selecionar procedimento']],'','required disabled') +
        field('duration_min','Duração (minutos)','number','','min="5" max="720" required') +
        field('day','Data da consulta','date',day,'min="' + esc(today) + '" required') +
        (catalogue.rooms.length ? select('room_id','Consultório ou cadeira',[['','Sem consultório reservado'],
          ...catalogue.rooms.map(room => [room.id,room.name])]) : '') +
        select('mode','Horário',[['standard','Disponível no expediente'],['exceptional','Encaixe fora da grade']],
          preset.bookingMode === 'exceptional' ? 'exceptional' : 'standard') +
        '<div data-dental-slot-group>' + select('start_at','Horário disponível',[['','Escolha o profissional e o procedimento']],'','required disabled') + '</div>' +
        '<div data-dental-manual-hour hidden>' + field('clock','Hora do encaixe','time',preset.time || '','disabled') + '</div></div>' +
        '<div data-dental-setup></div><p data-dental-slot-status class="os-help" role="status"></p>' +
        '<div data-dental-contact></div>' + textarea('administrative_note','Observação para a recepção','') +
        '<div class="os-form-actions">' + button('close','Cancelar') +
        '<button type="submit" class="os-button" data-dental-booking-submit ' + (disabled ? 'disabled ' : '') + '>Marcar consulta</button></div>' +
        '</fieldset></form><div data-dental-quick></div><p data-dental-booking-status class="os-reception-notice" role="status"></p>';
      renderPatient();
      updateProfessional();
      if (preset.patient) find('[name=professional_id]').focus();
      else find('[name=patient_search]').focus();
    };
    function renderPatient() {
      const holder = find('[data-dental-selected-patient]');
      holder.innerHTML = patient ? '<div class="os-dental-selected"><div><strong>' + esc(patient.name) + '</strong><small>' +
        esc(identifyPatient(patient)) + '</small></div>' + button('clear-patient','Trocar paciente','os-ghost os-small') + '</div>' : '';
      find('[name=patient_search]').hidden = Boolean(patient);
      const contacts = [];
      if (patient?.contact_phone) contacts.push(['patient','Usar celular do paciente']);
      if (patient?.guardian_phone) contacts.push(['guardian','Usar celular do responsável']);
      contacts.push(['none','Sem telefone']);
      find('[data-dental-contact]').innerHTML = patient && contacts.length > 1 ?
        select('contact','Contato vinculado à consulta',contacts,contacts[0][0]) : '';
    }
    async function search() {
      const version = ++searchVersion, query = find('[name=patient_search]').value.trim();
      const results = find('#osDentalBookingPatients');
      results.replaceChildren();
      if (query.length < 2) return;
      showStatus('Buscando pacientes…');
      try {
        const data = await call('/patients/search','POST',{query,limit:10,offset:0});
        if (!current() || version !== searchVersion || patient) return;
        searchRows = data.items;
        results.innerHTML = searchRows.map(person => patientResult(person,'choose-patient')).join('');
        showStatus(searchRows.length ? '' : 'Nenhum paciente encontrado. Use Cadastrar paciente aqui.');
      } catch (error) {
        if (current() && version === searchVersion) showStatus(error.message, true);
      }
    }
    function updateProfessional() {
      const form = find('[data-dental-booking]'), control = form.elements.procedure_id;
      const professional = catalogue.professionals.find(item => item.id === form.elements.professional_id.value);
      const previous = control.value;
      const procedures = catalogue.procedures.filter(item => professional?.procedure_ids.includes(item.id));
      control.replaceChildren(new Option('Selecionar procedimento',''), ...procedures.map(item => new Option(item.name,item.id)));
      control.disabled = !procedures.length;
      if (procedures.some(item => item.id === previous)) control.value = previous;
      else if (procedures.length === 1) control.value = procedures[0].id;
      if (form.elements.room_id) form.elements.room_id.value = catalogue.rooms.some(room => room.id === professional?.default_room_id) ?
        professional.default_room_id : '';
      const needs = [];
      if (!catalogue.professionals.length) needs.push('professional');
      if (!catalogue.procedures.length) needs.push('procedure');
      if (professional && !professional.has_hours) needs.push('hours');
      if (professional && catalogue.procedures.length && !procedures.length) needs.push('links');
      find('[data-dental-setup]').innerHTML = needs.length ? '<p class="os-help">' +
        (needs.includes('professional') ? 'Cadastre um profissional que receba agendamentos. ' : '') +
        (needs.includes('procedure') ? 'Cadastre um procedimento. ' : '') +
        (needs.includes('hours') ? 'Este profissional ainda não tem expediente. ' : '') +
        (needs.includes('links') ? 'Este profissional ainda não tem procedimentos vinculados.' : '') + '</p>' + actions(needs) : '';
      const procedure = procedures.find(item => item.id === control.value);
      if (previous !== control.value || !form.elements.duration_min.value) form.elements.duration_min.value = procedure?.duration_min || '';
      void loadSlots();
    }
    async function loadSlots() {
      const form = find('[data-dental-booking]'), values = new FormData(form), ticket = ++slotVersion;
      const slot = form.elements.start_at, manual = values.get('mode') === 'exceptional';
      const hint = find('[data-dental-slot-status]');
      find('[data-dental-slot-group]').hidden = manual;
      find('[data-dental-manual-hour]').hidden = !manual;
      form.elements.clock.disabled = !manual;
      form.elements.clock.required = manual;
      slot.disabled = true;
      slot.required = !manual;
      slot.replaceChildren(new Option('Escolha o profissional e o procedimento',''));
      hint.textContent = manual ? 'O encaixe pode ficar fora do expediente. Horários ocupados e bloqueios continuam protegidos.' : '';
      slotLoading = false;
      const duration = Number(values.get('duration_min'));
      if (manual || !values.get('professional_id') || !values.get('procedure_id') || !values.get('day') ||
          !Number.isInteger(duration) || duration < 5 || duration > 720) return;
      const params = new URLSearchParams();
      for (const key of ['professional_id','procedure_id','day','duration_min','room_id']) {
        if (values.get(key)) params.set(key,values.get(key));
      }
      hint.textContent = 'Consultando disponibilidade…';
      slotLoading = true;
      try {
        const response = await call('/availability?' + params);
        if (!current() || ticket !== slotVersion) return;
        slot.replaceChildren(new Option(response.slots.length ? 'Selecionar horário' : 'Sem horários disponíveis',''),
          ...response.slots.map(value => new Option(clockLabel(value),value)));
        slot.disabled = !response.slots.length;
        const desired = response.slots.find(value => clockLabel(value) === preset.time);
        if (desired) slot.value = desired;
        hint.textContent = response.slots.length ? '' : ({no_working_hours:'O profissional não atende nesse dia.',
          room_occupied:'O consultório está ocupado nesses horários.', outside_horizon:'A data está fora do período permitido.'}
          [response.reason] || 'Experimente outra data, profissional ou duração.');
      } catch (error) {
        if (current() && ticket === slotVersion) hint.textContent = error.message;
      } finally {
        if (current() && ticket === slotVersion) slotLoading = false;
      }
    }
    function quick() {
      quickKey = crypto.randomUUID();
      const text = find('[name=patient_search]').value.trim();
      find('[data-dental-booking]').hidden = true;
      showStatus('');
      find('[data-dental-quick]').innerHTML = '<form class="os-form" data-dental-booking-quick><h3>Cadastro rápido de paciente</h3>' +
        patientInputs({name:text}) + '<details class="os-more-fields"><summary>Responsável legal</summary>' +
        '<div class="os-disclosure-body">' + guardianInputs() + '</div></details><div class="os-form-actions">' +
        button('close-quick','Voltar à consulta') + '<button class="os-button" type="submit">Cadastrar e selecionar</button></div></form>';
      find('[data-dental-quick] [name=name]').focus();
    }
    function closeQuick() {
      find('[data-dental-quick]').replaceChildren();
      find('[data-dental-booking]').hidden = false;
      find('[name=professional_id]').focus();
    }
    async function submit(form) {
      if (busy) return;
      const values = new FormData(form), quickForm = form.matches('[data-dental-booking-quick]');
      if (!quickForm && !patient) { showStatus('Selecione ou cadastre um paciente antes de marcar.',true); find('[name=patient_search]').focus(); return; }
      const mode = values.get('mode');
      if (!quickForm && (slotLoading || !values.get('procedure_id') || !values.get('professional_id') || mode === 'standard' && !values.get('start_at'))) {
        showStatus('Confira profissional, procedimento e horário antes de marcar.', true);
        return;
      }
      busy = true;
      setBusy(true);
      form.setAttribute('aria-busy','true');
      form.querySelector('[type=submit]').disabled = true;
      try {
        if (quickForm) {
          const saved = await call('/patients','POST',{...patientValues(values),request_id:quickKey});
          if (!current()) return;
          if (!uuid.test(saved?.id)) throw Error('Não foi possível confirmar a identificação do paciente.');
          patient = saved;
          closeQuick();
          renderPatient();
          showStatus('Paciente cadastrado e selecionado.');
          return;
        }
        const payload = {patient_id:patient.id, professional_id:val(values,'professional_id'), procedure_id:val(values,'procedure_id'),
          room_id:val(values,'room_id'), duration_min:Number(values.get('duration_min')), visit_type:values.get('visit_type'),
          administrative_note:val(values,'administrative_note'), contact:val(values,'contact') || 'none', mode,
          start_at:mode === 'exceptional' ? localTimestamp(values.get('day'),values.get('clock')) : values.get('start_at')};
        const fingerprint = JSON.stringify(payload);
        if (fingerprint !== previewFingerprint) {
          previewKey = crypto.randomUUID();
          previewFingerprint = fingerprint;
        }
        const result = await call('/appointments','POST',{...payload,request_id:previewKey});
        if (!current()) return;
        if (!uuid.test(result?.id) || result.status !== 'confirmed') throw Error('Não foi possível confirmar a consulta salva.');
        setBusy(false);
        await onBooked(result, values.get('day'));
      } catch (error) {
        if (current()) {
          showStatus(error.message || 'Não foi possível salvar.', true);
          if (!quickForm && error.status === 409) void loadSlots();
        }
      } finally {
        busy = false;
        setBusy(false);
        form.removeAttribute('aria-busy');
        form.querySelector('[type=submit]').disabled = false;
      }
    }
    host.onclick = event => {
      if (!current() || busy) return;
      const action = event.target.closest('[data-dental-action]')?.dataset.dentalAction;
      if (action === 'close') return close();
      if (action === 'retry-options') return loadOptions();
      if (action === 'quick') return quick();
      if (action === 'close-quick') return closeQuick();
      if (action === 'choose-patient') {
        patient = searchRows.find(item => item.id === event.target.closest('[data-id]').dataset.id);
        ++searchVersion;
        find('#osDentalBookingPatients').replaceChildren();
        renderPatient();
        return showStatus('');
      }
      if (action === 'clear-patient') {
        patient = null;
        renderPatient();
        find('[name=patient_search]').focus();
      }
      if (action?.startsWith('setup-')) return setupActions[action.slice(6)]?.(find('[name=professional_id]').value);
    };
    host.oninput = event => {
      if (!event.target.matches('[name=patient_search]')) return;
      clearTimeout(searchTimer);
      ++searchVersion;
      searchTimer = setTimeout(() => { if (current()) void search(); }, 230);
    };
    host.onchange = event => {
      if (!current() || !event.target.closest('[data-dental-booking]')) return;
      const name = event.target.name;
      if (name === 'professional_id') return updateProfessional();
      if (name === 'procedure_id') {
        const chosen = catalogue.procedures.find(procedure => procedure.id === event.target.value);
        find('[name=duration_min]').value = chosen?.duration_min || '';
      }
      if (['procedure_id','day','duration_min','room_id','mode'].includes(name)) void loadSlots();
    };
    host.onsubmit = event => {
      event.preventDefault();
      if (current()) void submit(event.target);
    };
    async function loadOptions() {
      host.innerHTML = '<p role="status">Preparando a consulta…</p>';
      try {
        catalogue = await call('/booking-options');
        if (current()) draw();
      } catch (error) {
        if (current()) host.innerHTML = '<p role="alert">' + esc(error.message) + '</p>' + button('retry-options','Tentar novamente');
      }
    }
    await loadOptions();
  }

  window.OrionDentalReception = Object.freeze({
    mountDirectory, mountBooking, visitLabel,
    unmountDirectory() { directoryInstance = null; }
  });
})();
