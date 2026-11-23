Warning: truncated output (original token count: 186494)
Total output lines: 5698

/* Orion management suite — framework-free, authenticated, tenant-scoped.
 * Integration: OrionSuite.mount({request(path, method='GET', data), user:{role,name,email},
 * tenants:[{id,name,slug,timezone,role}], tenantId, openLegacy(page), onRouteChange(page),
 * businessContextReady?, businessContextError?, logout()?}); sync after the account's business is verified.
 * Existing panel sections and IDs remain in the DOM. request must handle 204 as null.
 * Data lives on the server. No demonstration records, stored tokens, or third-party analytics.
 */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const paths = {
    menu:'M4 6h16M4 12h16M4 18h16', close:'m6 6 12 12M6 18 18 6',
    chevron:'m9 5 7 7-7 7', left:'m15 5-7 7 7 7', down:'m6 9 6 6 6-6',
    arrow:'M5 12h14m-5-5 5 5-5 5', external:'M15 3h6v6m0-6-9 9M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4',
    dashboard:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
    home:'m3 10 9-7 9 7v10H3V10Zm6 10v-7h6v7',user:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21v-2a8 8 0 0 1 16 0v2',
    calendar:'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z',
    calendarPlus:'M8 2v4m8-4v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm7 9v6m-3-3h6',
    repeat:'M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3',
    users:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
    userPlus:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M20 8v6m-3-3h6',
    scissors:'m14 14 7 7M21 3l-8 8M7.5 7.5l9 9',
    prosthesis:'M5 9 3.5 6.6a2 2 0 0 1 2.8-2.7l2.3 1.4 2.2-2a1.8 1.8 0 0 1 2.4 0l2.2 2 2.3-1.4a2 2 0 0 1 2.8 2.7L19 9l-.8 5H5.8L5 9Zm3 5v4.5L12 21l4-2.5V14M8 17h8',
    tooth:'M12 5C7 2 3 4 3 9c0 4 2 8 3.2 10.2.8 1.7 2.3 1.7 3 .1l2.1-5c.4-.8 1.1-.8 1.5 0l2 5c.7 1.6 2.2 1.6 3-.1C19 17 21 13 21 9c0-5-4-7-9-4Z',
    wallet:'M20 8V5a2 2 0 0 0-2-2H6a3 3 0 0 0 0 6h14v12H6a3 3 0 0 1-3-3V6m17 7h-4a2 2 0 0 0 0 4h4',
    chart:'M3 3v18h18M7 14l4-4 4 3 6-8',
    sparkles:'m12 3 2.8 6.2L21 12l-6.2 2.8L12 21l-2.8-6.2L3 12l6.2-2.8ZM20 2v4m-2-2h4',
    globe:'M2 12h20M12 2c3 3.5 4 7 4 10s-1 6.5-4 10c-3-3.5-4-7-4-10s1-6.5 4-10',
    gift:'M20 12v9H4v-9M2 7h20v5H2zM12 7v14m0-14H7.5A2.5 2.5 0 1 1 10 4.5L12 7Zm0 0h4.5A2.5 2.5 0 1 0 14 4.5L12 7Z',
    layers:'m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 17l9 5 9-5',
    bot:'M12 3v3.5M8 7h8a5 5 0 0 1 5 5v4a5 5 0 0 1-5 5H9l-4 2v-4.1A5 5 0 0 1 3 15V12a5 5 0 0 1 5-5ZM9 13h.01M15 13h.01M9 17c1.7 1.1 4.3 1.1 6 0',
    credit:'M2 8h20M4 3h16a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2ZM6 16h3',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm-2-6h4l1 3 3 1 3-1 2 4-2 2v3l2 2-2 4-3-1-3 1-1 3h-4l-1-3-3-1-3 1-2-4 2-2v-3l-2-2 2-4 3 1 3-1 1-3Z',
    phone:'M6 2h12v20H6zM10 5h4M11 19h2',
    chat:'M6 3h12a3 3 0 0 1 3 3v9a3 3 0 0 1-3 3H8l-5 3V6a3 3 0 0 1 3-3ZM8 8h8M8 12h5',
    box:'m3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10M7.5 5l9 4',
    rocket:'M4 15 2 22l7-2M14 3c3-1 6-1 8-1 0 2 0 5-1 8l-9 9-7-7 9-9ZM5 12H2l3-7h7m0 14v3l7-3v-7',
    check:'m5 12 4 4L19 6', clock:'M12 6v6l4 2', search:'m21 21-4.3-4.3',
    bell:'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4',
    sun:'M12 2v2m0 16v2M2 12h2m16 0h2M4.93 4.93l1.42 1.42m11.3 11.3 1.42 1.42M4.93 19.07l1.42-1.42m11.3-11.3 1.42-1.42',
    moon:'M20.5 14.3A9 9 0 0 1 9.7 3.5 9 9 0 1 0 20.5 14.3Z',
    monitor:'M3 3h18v14H3zM8 21h8M12 17v4',
    list:'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
    plus:'M12 5v14M5 12h14', minus:'M5 12h14', edit:'m16 3 5 5L8 21H3v-5L16 3Zm-2 2 5 5',
    eye:'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
    trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
    block:'m5 5 14 14', info:'M12 11v6M12 7h.01',
    download:'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6', upload:'M12 17V3m-5 5 5-5 5 5M4 15v6h16v-6',
    copy:'M9 9h12v12H9zM15 9V3H3v12h6',
    book:'M12 5c-3-2-6-2-10-2v16c4 0 7 0 10 2 3-2 6-2 10-2V3c-4 0-7 0-10 2Zm0 0v16',
    help:'M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3m.1 4h.01',
    target:'M12 12 21 3M16 3h5v5', lock:'M6 10h12v12H6zM8 10V6a4 4 0 0 1 8 0v4',
    link:'M10 13a5 5 0 0 0 7 .5l4-4a5 5 0 0 0-7-7l-2 2m2 6a5 5 0 0 0-7-.5l-4 4a5 5 0 0 0 7 7l2-2',
    refresh:'M20 11a8.1 8.1 0 0 0-15.5-2M4 4v5h5M4 13a8.1 8.1 0 0 0 15.5 2M20 20v-5h-5',
    flask:'M9 3h6M10 3v7l-5.4 8.5A2 2 0 0 0 6.3 22h11.4a2 2 0 0 0 1.7-3.5L14 10V3M8 15h8',
    fileCheck:'M5 3h9l5 5v13H5V3Zm9 0v5h5M8 15l3 3 5-5',
    activity:'M2 12h4l3-8 5 16 3-8h5',
    megaphone:'M3 10v4h4l13 5V5L7 10H3Zm4 4 1.8 7H12l-2.1-6',
    calendarClock:'M8 3v3m8-3v3M3 9h18M5 5h14a2 2 0 0 1 2 2v6M5 5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h7M18 15a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0 2v2l1.5 1',
    broadcast:'M12 12v9M6.7 6.7a7.5 7.5 0 0 0 0 10.6M17.3 6.7a7.5 7.5 0 0 1 0 10.6M3.5 3.5a12 12 0 0 0 0 17M20.5 3.5a12 12 0 0 1 0 17M12 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4',
    flame:'M12 22c4.6 0 8-3.4 8-7.7 0-3.2-1.7-5.3-4-7.8-.2 2.4-1.2 3.8-2.5 4.7C13.2 8 11.5 5 8 2c.1 4.1-1.8 6-3.3 8.5-.5 1-.7 2.3-.7 3.8C4 18.6 7.4 22 12 22ZM10 19c-1.4-1.3-1.4-3.1-.2-4.7l2.2-2.3c.2 1.3 1.2 2.1 1.9 3 1 1.5.2 3.6-1.9 4',
    shieldCheck:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4ZM8 12l3 3 5-6',
    shieldAlert:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4ZM12 8v5m0 3h.01',
    usersSearch:'M3 20v-1a4 4 0 0 1 4-4h4M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM17 12a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm3 7 2 2',
    badgeUser:'M4 4h16v16H4zM8 9a2 2 0 1 0 4 0 2 2 0 0 0-4 0ZM6.5 16a3.5 3.5 0 0 1 7 0M15 9h3M15 12h3',
    brain:'M12 6a4 4 0 0 0-7 2 3.5 3.5 0 0 0-.5 6A4 4 0 0 0 8 20h1.5A3 3 0 0 0 12 22M12 6a4 4 0 0 1 7 2 3.5 3.5 0 0 1 .5 6A4 4 0 0 1 16 20h-1.5A3 3 0 0 1 12 22V6M8 10v3m8-3v3',
    messages:'M4 4h12a2 2 0 0 1 2 2v9H8l-4 3V6a2 2 0 0 1 2-2ZM10 19h7l3 2V10a2 2 0 0 0-2-2M8 8h6m-6 4h4',
    building:'M4 21V4l8-2 8 2v17M2 21h20M8 7h1m6 0h1M8 11h1m6 0h1M8 15h1m6 0h1M10 21v-4h4v4',
    send:'m22 2-7 20-4-9-9-4 20-7ZM11 13 22 2', mail:'M3 5h18v14H3zM3 5l9 7 9-7', shield:'m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6l9-4Z',
    more:'M5 12h.01M12 12h.01M19 12h.01', arrowDown:'M12 4v16m-5-5 5 5 5-5', arrowUp:'M12 20V4m-5 5 5-5 5 5',
    instagram:'M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5Zm10 5h.01',
    logout:'M9 21H4V3h5m7 14 5-5-5-5M9 12h12',
  };
  const circles={users:'<circle cx="9" cy="7" r="4"/>',userPlus:'<circle cx="9" cy="7" r="4"/>',scissors:'<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>',clock:'<circle cx="12" cy="12" r="10"/>',search:'<circle cx="10.5" cy="10.5" r="7.5"/>',sun:'<circle cx="12" cy="12" r="4"/>',info:'<circle cx="12" cy="12" r="10"/>',help:'<circle cx="12" cy="12" r="10"/>',globe:'<circle cx="12" cy="12" r="10"/>',block:'<circle cx="12" cy="12" r="10"/>',target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/>',instagram:'<circle cx="12" cy="12" r="4"/>'};
  const icon = name => '<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+(circles[name]||'')+'<path d="'+(paths[name]||paths.info)+'"/></svg>';
  const compactDisclosureSummary = label => '<summary><span class="os-detail-toggle-icon" aria-hidden="true">'+icon('plus')+icon('minus')+'</span><span>'+esc(label)+'</span></summary>';
  const routeNames={dispositivos:'Dispositivos',chatbot:'Chatbot','testar-chatbot':'Testar Chatbot',templates:'Templates','mensagens-enviadas':'Mensagens Enviadas',diagnosticos:'Diagnósticos',campanhas:'Campanhas',programadas:'Mensagens Programadas',massa:'Mensagem em Massa','enviar-mensagem':'Enviar Mensagem',aquecimento:'Aquecimento','evitar-banimento':'Evitar Banimento','resolver-banimento':'Resolver Banimento','extrator-contatos':'Extrator de contatos de grupos','adicionar-ao-grupo':'Adicionar contatos ao grupo',inbox:'Central de atendimento',dashboard:'Dashboard',perfil:'Perfil',agenda:'Agenda',clientes:'Clientes',clinica:'Clínica odontológica','pacientes-odonto':'Pacientes','proteses-odonto':'Controle de próteses','estoque-odonto':'Estoque','vendas-odonto':'Vendas','ajustes-odonto':'Ajustes da clínica',servicos:'Serviços',funcionarios:'Funcionários',financeiro:'Financeiro',desempenho:'Meu desempenho',marketing:'Marketing IA',site:'Meu site',fidelizacao:'Fidelização',consultor:'Consultor IA',plano:'Plano',configuracoes:'Configurações',novidades:'Novidades',ajuda:'Base de conhecimento',dispositivo:'Conectar WhatsApp',conversas:'Conversas',ai:'Testar atendimento',empresa:'Configurar atendimento',catalogo:'Catálogo e pedidos',cadastros:'Administração do atendimento',member:'Minha conta'};
  const routeAliases=new Map([['acelerar','dashboard']]);
  const mainNav=[['dispositivos','phone'],['chatbot','bot'],['testar-chatbot','flask'],['templates','layers'],['mensagens-enviadas','fileCheck'],['diagnosticos','activity'],['campanhas','megaphone'],['programadas','calendarClock'],['massa','broadcast'],['enviar-mensagem','send'],['aquecimento','flame'],['evitar-banimento','shieldCheck'],['resolver-banimento','shieldAlert'],['extrator-contatos','usersSearch'],['adicionar-ao-grupo','userPlus'],['dashboard','dashboard'],['agenda','calendar'],['clientes','users'],['clinica','tooth'],['pacientes-odonto','users'],['proteses-odonto','prosthesis'],['estoque-odonto','box'],['vendas-odonto','layers'],['ajustes-odonto','settings'],['inbox','chat'],['servicos','scissors'],['funcionarios','badgeUser'],['financeiro','wallet'],['desempenho','chart'],['marketing','sparkles'],['site','globe'],['fidelizacao','gift'],['consultor','brain'],['plano','credit'],['configuracoes','settings']];
  const toolNavRoutes=['extrator-contatos','adicionar-ao-grupo','aquecimento','evitar-banimento','resolver-banimento'];
  const appShortcutChoices=[
    ['dashboard','home','Início'],['agenda','calendar','Agenda'],['servicos','scissors','Serviços'],
    ['hours','clock','Horários'],['clientes','users','Clientes'],['financeiro','wallet','Financeiro'],
    ['inbox','chat','Conversas'],['funcionarios','badgeUser','Equipe'],['desempenho','chart','Desempenho'],
    ['site','globe','Meu site'],['fidelizacao','gift','Fidelização'],
    ['marketing','sparkles','Marketing'],['consultor','brain','Consultor IA'],
    ['chatbot','bot','Chatbot'],['templates','layers','Templates'],
    ['enviar-mensagem','send','Enviar mensagens'],['diagnosticos','activity','Diagnóstico'],
    ['dispositivos','phone','Dispositivos'],['testar-chatbot','flask','Testar chatbot'],
    ['mensagens-enviadas','fileCheck','Mensagens enviadas'],['campanhas','megaphone','Campanhas'],
    ['programadas','calendarClock','Programadas'],['massa','broadcast','Mensagem em massa'],
    ['extrator-contatos','usersSearch','Extrator de contatos'],['adicionar-ao-grupo','userPlus','Adicionar ao grupo']
  ];
  const appDefaultShortcuts=()=>subscriptionWorkspace()?
    ['chatbot','templates','enviar-mensagem','diagnosticos']:['dashboard','agenda','servicos','hours'];
  const appProfileShortcut=['perfil','user','Perfil'];
  const legacyPages=new Set(['dispositivo','conversas','ai','empresa','catalogo','cadastros','member']);
  // The original Orion operator console is restricted to the authenticated
  // platform administrator. Business owners use the regular workspace.
  const platformAdminOnlyPages=new Set(['conversas','ai','empresa','catalogo','cadastros']);
  const appPages=new Set([...Object.keys(routeNames),...routeAliases.keys()]);
  const routeFeatures={dispositivos:'devices.manage',chatbot:'account.manage','testar-chatbot':'chatbot.manage',templates:'templates.manage','mensagens-enviadas':'sent_messages.manage',diagnosticos:'diagnostics.manage',campanhas:'campaigns.manage',programadas:'scheduled_messages.manage',massa:'bulk_messages.manage','enviar-mensagem':'direct_messages.manage',aquecimento:'whatsapp_guidance.view','evitar-banimento':'whatsapp_guidance.view','resolver-banimento':'whatsapp_guidance.view','extrator-contatos':'group_contacts.manage','adicionar-ao-grupo':'group_add.manage',dashboard:'dashboard.basic',agenda:'agenda.manage',clientes:'clients.manage',clinica:'clients.manage','pacientes-odonto':'clients.manage','proteses-odonto':'clients.manage','estoque-odonto':'clients.manage',inbox:'conversations.manual',servicos:'services.manage',funcionarios:'professionals.manage',financeiro:'finance.basic',desempenho:'performance.advanced',marketing:'marketing.ai',site:'site.manage',fidelizacao:'loyalty.manage',consultor:'consultant.ai',configuracoes:'settings.manage'};
  const platformWorkspace=()=>state.bridge.user?.role==='admin'&&!state.bridge.user?.maintenance?.active;
  const subscriptionRoutes=new Set(['dashboard','novidades','ajuda','member']);
  const workspaceProfile=()=>platformWorkspace()?'standard':state.workspaceProfile||tenant()?.workspace_profile||null;
  const subscriptionWorkspace=()=>workspaceProfile()==='subscription_services';
  // A niche comes from the server's verified entitlement, never from a URL or storage.
  const dentalWorkspace=()=>!platformWorkspace()&&!subscriptionWorkspace()&&state.entitlements?.business_niche==='odontologia';
  function barbershopHomeEligible(){
    const plan=state.entitlements;
    if(!plan||plan.workspace_profile!=='standard'||subscriptionWorkspace())return false;
    // The current entitlement advertises App layout for barbershops and subscriptions only.
    return plan.business_niche==null?plan.layout_modes?.includes('app')===true:plan.business_niche==='barbearia';
  }
  const homePreviewEnabled=()=>platformWorkspace()||barbershopHomeEligible();
  const dedicatedPatients=()=>dentalWorkspace()&&state.entitlements?.dental_clinical_enabled===true;
  const routeLabel=route=>route==='dashboard'&&homePreviewEnabled()?'Início':dentalWorkspace()?({dashboard:'Recepção',agenda:'Agenda',clientes:dedicatedPatients()?'Contatos da agenda':'Pacientes',clinica:'Prontuários',servicos:'Procedimentos',funcionarios:'Equipe clínica',desempenho:'Indicadores',inbox:'Conversas'})[route]||routeNames[route]:subscriptionWorkspace()&&route==='dashboard'?'Início':routeNames[route];
  const fallbackRoute=()=>subscriptionWorkspace()?'dashboard':'plano';
  const permitted=feature=>!feature||platformWorkspace()||(!subscriptionWorkspace()||['account.manage','business.setup','devices.manage','chatbot.manage','templates.manage','sent_messages.manage','diagnostics.manage','campaigns.manage','scheduled_messages.manage','bulk_messages.manage','direct_messages.manage','whatsapp_guidance.view','group_contacts.manage','group_add.manage'].includes(feature))&&state.entitlements?.permissions?.[feature]===true;
  const routePermitted=route=>route==='vendas-odonto'?dentalSettingsAllowed():route==='ajustes-odonto'?dentalWorkspace():['clinica','estoque-odonto','pacientes-odonto','proteses-odonto'].includes(route)?(dentalWorkspace()&&state.entitlements?.dental_clinical_enabled===true&&['owner','editor'].includes(tenant()?.role)&&permitted('clients.manage')):route==='perfil'?appLayoutEnabled():['aquecimento','evitar-banimento','resolver-banimento','extrator-contatos','adicionar-ao-grupo'].includes(route)?(subscriptionWorkspace()&&tenant()?.role==='owner'&&tenant()?.status==='active'&&permitted(routeFeatures[route])):['dispositivos','chatbot','testar-chatbot','templates','mensagens-enviadas','diagnosticos','campanhas','programadas','massa','enviar-mensagem'].includes(route)?(subscriptionWorkspace()&&permitted(routeFeatures[route])):subscriptionWorkspace()?(route==='configuracoes'?permitted('account.manage'):subscriptionRoutes.has(route)):route==='configuracoes'?(permitted('settings.manage')||permitted('business.setup')||permitted('account.manage')):permitted(routeFeatures[route]);
  const dentalSettingsAllowed=()=>dentalWorkspace()&&state.entitlements?.dental_clinical_enabled===true&&
    state.bridge.user?.role==='member'&&!state.bridge.user?.maintenance?.active&&tenant()?.role==='owner'&&permitted('settings.manage');
  const settingsTabAllowed=tab=>['dental-chairs','dental-intake','dental-sales-stages'].includes(tab)?dentalSettingsAllowed():subscriptionWorkspace()?['appearance','account'].includes(tab):tab==='finance'?permitted('finance.basic'):['appearance','account'].includes(tab)||tab==='general'?(tab!=='general'||permitted('business.setup')||permitted('settings.manage')):tab==='ai'?(permitted('marketing.ai')||permitted('consultant.ai')):tab==='whatsapp'?permitted('conversations.manual'):tab==='hours'?permitted('agenda.manage'):tab==='calendar'?permitted('calendar.manage'):tab==='notifications'?permitted('notifications.basic'):tab==='site-requests'?canManageSiteRequests():platformWorkspace();
  const atProfessionalLimit=()=>!platformWorkspace()&&Number.isInteger(state.entitlements?.limits?.max_professionals)&&Number(state.entitlements.limits.active_professionals)>=state.entitlements.limits.max_professionals;
  function featureForPath(path,method='GET',data){
    const suffix=path.split('/management/')[1]||'',resource=path.split('/api/v1/tenants/')[1]?.split('/')[1];
    if(/^plan-|^permissions(?:$|\?)/.test(suffix))return null;
    if(/^(performance)/.test(suffix))return 'performance.advanced';
    if(/^goals/.test(suffix))return 'goals.manage';
    if(/^finance\/(commissions|advances|settlements)/.test(suffix)||/^professionals\/commission-rules/.test(suffix)||/entry_type=commission_payment/.test(suffix))return 'commissions.advanced';
    if(/^finance\/dre/.test(suffix))return 'finance.advanced';
    if(/^finance/.test(suffix))return 'finance.basic';
    if(/^marketing\/(execution|consent|contact-pause|jobs|automations)(?:[/?]|$)/.test(suffix))return 'notifications.basic';
    if(/^marketing/.test(suffix))return 'marketing.campaigns';
    if(/^loyalty/.test(suffix))return 'loyalty.manage';
    if(/^coupons/.test(suffix))return 'coupons.manage';
    if(/^ai\/(consult|consultant)/.test(suffix))return 'consultant.ai';
    if(/^ai\/(campaign|marketing)/.test(suffix))return 'marketing.ai';
    if(/^ai\//.test(suffix))return permitted('marketing.ai')?'marketing.ai':'consultant.ai';
    if(resource==='dental')return 'clients.manage';
    if(/^customers/.test(suffix))return 'clients.manage';
    if(/^dashboard/.test(suffix))return 'dashboard.basic';
    if(/^professionals/.test(suffix)||resource==='professionals')return 'professionals.manage';
    if(/^service/.test(suffix)||['services','professional_services'].includes(resource))return 'services.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/devices(?:[/?]|$)/.test(path))return 'devices.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/group-add(?:[/?]|$)/.test(path))return 'group_add.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/group-contacts(?:[/?]|$)/.test(path))return 'group_contacts.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/direct-messages(?:[/?]|$)/.test(path))return 'direct_messages.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/bulk-messages(?:[/?]|$)/.test(path))return 'bulk_messages.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/scheduled-messages(?:[/?]|$)/.test(path))return 'scheduled_messages.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/campaigns(?:[/?]|$)/.test(path))return 'campaigns.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/diagnostics(?:[/?]|$)/.test(path))return 'diagnostics.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/sent-messages(?:[/?]|$)/.test(path))return 'sent_messages.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/templates(?:[/?]|$)/.test(path))return 'templates.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/chatbot-test(?:[/?]|$)/.test(path))return 'chatbot.manage';
    if(resource==='subscription-whatsapp'&&/\/subscription-whatsapp\/chatbot(?:[/?]|$)/.test(path))return method==='GET'||method==='POST'&&/\/chatbot\/exports$/.test(path)?'account.manage':'chatbot.manage';
    if(resource==='qr-channels')return data?.assistant_enabled===true?'reception.ai':'conversations.manual';
    if(/^inbox/.test(suffix))return 'conversations.manual';
    return null;
  }
  function dialogFeature(type){
    if(state.route==='dashboard'&&homePreviewEnabled()){
      if(type==='customer')return 'clients.manage';
      if(type.startsWith('appointment')||type==='fixed-client')return 'agenda.manage';
    }
    if(type.startsWith('dental-chair')||type==='dental-intake'||type==='dental-sales-stage'||type==='dental-sales-opportunity')return 'settings.manage';
    return /receipt/.test(type)?'finance.basic':type==='business-preferences'?'business.setup':/goal/.test(type)?'goals.manage':/campaign|marketing/.test(type)?'marketing.campaigns':/loyalty/.test(type)?'loyalty.manage':/coupon/.test(type)?'coupons.manage':/settlement|advance|commission/.test(type)?'commissions.advanced':/professional|hours/.test(type)?'professionals.manage':routeFeatures[state.route];
  }
  function applyPermissionDOM(){
    if(!state.mounted)return;
    document.querySelectorAll('[data-os-feature]').forEach(node=>{const allowed=permitted(node.dataset.osFeature);if(!allowed){if(node.dataset.osPlanHidden===undefined)node.dataset.osPlanHidden=String(node.hidden);node.hidden=true;}else if(node.dataset.osPlanHidden!==undefined){node.hidden=node.dataset.osPlanHidden==='true';delete node.dataset.osPlanHidden;}node.querySelectorAll('input,select,textarea,button').forEach(input=>{if(!allowed){if(input.dataset.osPlanDisabled===undefined)input.dataset.osPlanDisabled=String(input.disabled);input.disabled=true;}else if(input.dataset.osPlanDisabled!==undefined){input.disabled=input.dataset.osPlanDisabled==='true';delete input.dataset.osPlanDisabled;}});});
    document.querySelectorAll('[data-os-route]').forEach(node=>{if(!node.closest('#osNav'))node.hidden=!routePermitted(node.dataset.osRoute);});
    const fab=$('osConsultantFab');if(fab)fab.classList.toggle('hidden',!permitted('consultant.ai')||['agenda','inbox'].includes(state.route));
    document.querySelectorAll('[data-qc-assistant]').forEach(node=>{node.hidden=!permitted('reception.ai');});
    document.querySelectorAll('[data-os-action=notifications]').forEach(node=>{node.hidden=!permitted('notifications.basic');});
    const notifications=$('osNotificationsToggle')?.closest('.os-notifications');if(notifications)notifications.hidden=subscriptionWorkspace();
    document.querySelectorAll('#osDialog .os-commission-default,#osDialog .os-commission-service-controls,#osDialog [name=commission_mode]').forEach(node=>{
      const area=node.name==='commission_mode'?node.closest('.os-field'):node;if(!area)return;area.hidden=!permitted('commissions.advanced');
      area.querySelectorAll('input,select').forEach(input=>{if(!permitted('commissions.advanced')){if(!input.dataset.osPlanDisabled)input.dataset.osPlanDisabled=String(input.disabled);input.disabled=true;}else if(input.dataset.osPlanDisabled){input.disabled=input.dataset.osPlanDisabled==='true';delete input.dataset.osPlanDisabled;}});
    });
  }
  function armPermissionExpiry(value,epoch,id){
    clearTimeout(state.permissionExpiryTimer);state.permissionExpiryTimer=null;
    const expires=Date.parse(value.valid_until),key=value.revision+'|'+value.valid_until;
    if(value.active!==true||!Number.isFinite(expires)||state.permissionExpiryConsumed===key)return;
    state.permissionExpiryTimer=setTimeout(()=>{
      if(!state.mounted||state.epoch!==epoch||state.tenantId!==id)return;
      if(expires<=Date.now()+50)state.permissionExpiryConsumed=key;
      const refresh=()=>{if(state.mounted&&state.epoch===epoch&&state.tenantId===id)void refreshPermissions({reconcile:true});};
      if(state.permissionPending)void state.permissionPending.then(refresh);else refresh();
    },Math.min(2147483647,Math.max(0,expires-Date.now()+50)));
  }
  async function refreshPermissions({reconcile=false}={}){
    if(platformWorkspace())return true;
    if(!tenant())return false;
    if(state.permissionPending)return state.permissionPending;
    const epoch=state.epoch,id=state.tenantId,read=state.permissionRead=(state.permissionRead||0)+1;
    const pending=(async()=>{
      try{
        const result=await Promise.resolve().then(()=>state.bridge.request(mgmt('/permissions'))),value=result?.effective_plan;
        if(!state.mounted||epoch!==state.epoch||id!==state.tenantId||read!==state.permissionRead)return false;
        if(!value||!value.permissions||typeof value.permissions!=='object'||Array.isArray(value.permissions)||typeof value.revision!=='string')throw Error('Não foi possível confirmar as permissões desta empresa.');
        const previousProfile=workspaceProfile(),nextProfile=['standard','subscription_services'].includes(value.workspace_profile)?value.workspace_profile:tenant()?.workspace_profile||previousProfile;
        const profileChanged=previousProfile!==nextProfile,changed=state.entitlements?.revision!==value.revision||profileChanged;
        state.workspaceProfile=nextProfile;state.entitlements=value;state.permissionError='';armPermissionExpiry(value,epoch,id);applyAppLayout();
        if(profileChanged){state.load++;state.data={};state.busy=false;clearTimeout(state.searchTimer);clearTimeout(state.agendaSearchTimer);clearSiteLookups();closeNotifications();closeDialog(true);}
        state.bridge.onWorkspaceProfile?.();
        if(changed){
          state.appShortcutDraft=null;renderSidebar();applyPermissionDOM();
          if(state.dialog&&!permitted(state.dialog.requiredFeature)){state.busy=false;closeDialog(true);toast('Seu plano mudou. Esta ação não está mais disponível.',true);}
          if(reconcile){
            if(!routePermitted(state.route))await open(fallbackRoute(),{force:true,permissionsChecked:true});
            else if(state.route==='configuracoes'&&!settingsTabAllowed(state.filters.settingsTab)){state.filters.settingsTab='account';await open('configuracoes',{force:true,permissionsChecked:true});}
            else if(state.route==='financeiro'&&!permitted(financeFeature(state.filters.financeTab))){state.filters.financeTab='entries';await open('financeiro',{force:true,permissionsChecked:true});}
            else if(!state.dialog&&!document.activeElement?.matches?.('input,select,textarea,[contenteditable=true]'))await open(state.route,{force:true,keepScroll:true,permissionsChecked:true});
            else state.permissionRenderPending=true;
          }
        }
        return true;
      }catch(error){if(epoch===state.epoch&&id===state.tenantId){clearTimeout(state.permissionExpiryTimer);state.permissionExpiryTimer=null;state.entitlements=null;state.permissionError=error.message||'Não foi possível confirmar as permissões.';applyAppLayout();renderSidebar();applyPermissionDOM();if(reconcile){state.busy=false;closeDialog(true);await open(fallbackRoute(),{force:true,permissionsChecked:true});}}return false;}
      finally{if(state.permissionPending===pending)state.permissionPending=null;}
    })();state.permissionPending=pending;return pending;
  }
  const defaultFilters=()=>({dentalSalesQuery:'',dentalSalesStart:'',dentalSalesEnd:'',customerQuery:'',customerStatus:'active',customerRisk:30,customerOffset:0,agendaMode:'list',agendaPeriod:'day',agendaRangeList:false,agendaFocusDay:'',agendaFocusOffset:0,agendaDay:'',agendaTablet:false,agendaProfessional:'',agendaStatus:'all',agendaQuery:'',agendaOffset:0,financeKind:'',financeStatus:'',financeFiltersOpen:false,financeReceivables:false,financeTab:'entries',performancePeriod:'month',performanceDay:'',performanceGroup:'day',performanceTab:'overview',performanceMetric:'revenue_cents',performanceProfessional:'',performancePoint:'',performanceRange:null,staffTab:'professionals',staffStatus:'active',marketingTab:'overview',marketingQueueStatus:'all',loyaltyTab:'ranking',loyaltyOffset:0,couponStatus:'all',periodStart:'',periodEnd:'',settingsTab:'general',appHoursView:'hours',siteRequestStatus:'all',siteRequestIntent:'all',siteRequestQuery:'',siteRequestOffset:0,siteTab:'dashboard',helpQuery:'',helpCategory:'all',helpArticle:'',newsFilter:'all',notificationsUnread:false});
  const state={bridge:{},mounted:false,epoch:0,load:0,route:'dashboard',tenantId:'',layoutMode:'classic',appShortcutDraft:null,data:{},filters:defaultFilters(),dialog:null,busy:false,imageUploading:false,searchTimer:null,drawerTrigger:null,observer:null};
  const rows = value => Array.isArray(value)?value:Array.isArray(value?.items)?value.items:[];
  const number = value => Number.isFinite(Number(value))?Number(value):0;
  const money = value => new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(number(value)/100);
  const priceLabel = (value,mode) => (mode==='from'?'A partir de ':'')+money(value);
  const variableItems = value => rows(value).some(item=>item.price_mode_snapshot==='from'||item.price_mode==='from');
  const initials = value => String(value||'O').trim().split(/\s+/).slice(0,2).map(x=>Array.from(x)[0]).join('').toLocaleUpperCase('pt-BR');
  const customerNumber = value => typeof value==='string'&&/\d/.test(value)&&/^[\d\s()+.-]+$/.test(value);
  const customerPlaceholders=new Set(['cliente whatsapp','cliente do whatsapp','contato whatsapp','contato do whatsapp','whatsapp','cliente','contato','sem nome','nome nao informado','desconhecido','unknown','whatsapp customer','whatsapp contact']);
  function customerName(value){
    if(typeof value!=='string'||value.length>1024)return '';
    const name=value.normalize('NFKC').trim().replace(/\s+/gu,' '),pictogram=/[\p{So}\p{Sk}]/u.test(name),controls=pictogram?name.replace(/\u200d/g,''):name;
    if(!name||Array.from(name).length>120||!/[\p{L}\p{So}\p{Sk}]/u.test(name)||/\p{C}/u.test(controls)||/@(?:lid|s\.whatsapp\.net|g\.us)\b/i.test(name))return '';
    return customerPlaceholders.has(name.normalize('NFKD').replace(/\p{M}/gu,'').toLocaleLowerCase('pt-BR'))?'':name;
  }
  function customerDisplayName(customer){
    // A useful manual name always wins; metadata changes only the presentation.
    return customerName(customer.name)||customerName(customer.display_name)||customerName(customer.whatsapp_name)||[customer.phone,customer.display_name,customer.name].map(value=>typeof value==='string'?value.trim():'').find(customerNumber)||'Cliente sem nome';
  }
  function customerAvatar(customer){return customerName(customer.name)||customerName(customer.display_name)||customerName(customer.whatsapp_name)?esc(initials(customerDisplayName(customer))):icon('users');}
  function customerMobileName(customer){
    const name=customerDisplayName(customer);
    return customerNumber(name)?'Cliente sem nome':name;
  }
  function customerWhatsappLink(value){
    if(typeof value!=='string'||!/^[+\d\s().-]+$/.test(value.trim()))return '';
    let normalized;try{normalized=phone(value);}catch{return '';}
    return /^\+55[1-9][0-9]{9,10}$/.test(normalized)?'https://wa.me/'+normalized.slice(1):'';
  }
  const whatsappMark='<svg class="os-whatsapp-mark" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" focusable="false">'+
    '<path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.966-.273-.099-.471-.149-.67.149-.197.297-.767.966-.94 1.164-.173.198-.347.223-.644.075-.297-.149-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.52.149-.174.198-.298.298-.496.099-.198.05-.372-.025-.52-.075-.149-.67-1.612-.917-2.207-.242-.579-.487-.5-.67-.51-.173-.009-.372-.01-.57-.01s-.52.074-.792.372c-.272.297-1.04 1.016-1.04 2.48s1.065 2.876 1.213 3.074c.149.198 2.096 3.2 5.077 4.487.709.306 1.262.49 1.693.627.711.226 1.36.194 1.872.118.571-.085 1.758-.719 2.005-1.413.248-.694.248-1.289.174-1.413-.074-.124-.272-.198-.57-.347zM12.004 2C6.488 2 2 6.486 2 12c0 1.763.46 3.486 1.335 5.007L2 22l5.127-1.344A9.94 9.94 0 0 0 12.005 22C17.52 22 22 17.514 22 12S17.52 2 12.004 2zm0 18.31c-1.55 0-3.07-.416-4.396-1.203l-.315-.187-3.035.796.81-2.958-.205-.332A8.282 8.282 0 0 1 3.69 12c0-4.585 3.73-8.316 8.316-8.316 4.586 0 8.316 3.731 8.316 8.316 0 4.586-3.73 8.31-8.316 8.31z"/></svg>';

  const businessAccessError = () => state.bridge.businessContextError||((state.bridge.tenants||[]).length>1?'Sua conta está vinculada a mais de uma empresa. Entre em contato com o suporte para regularizar o vínculo.':'');
  const tenant = () => {const list=state.bridge.tenants||[];return state.bridge.businessContextReady!==false&&!businessAccessError()&&list.length===1&&String(list[0].id)===state.tenantId?list[0]:null;};
  const accountTenantId = () => {const list=state.bridge.tenants||[];return state.bridge.businessContextReady!==false&&!businessAccessError()&&list.length===1?String(list[0].id):'';};
  const zone = () => tenant()?.timezone||'America/Campo_Grande';
  function dateKey(value=new Date()) { try {return new Intl.DateTimeFormat('en-CA',{timeZone:zone(),year:'numeric',month:'2-digit',day:'2-digit'}).format(value);}catch{return value.toISOString().slice(0,10);} }
  const dayDate = day => new Date((day||dateKey())+'T12:00:00Z');
  const shiftDay = (day,count) => {const d=dayDate(day);d.setUTCDate(d.getUTCDate()+count);return d.toISOString().slice(0,10);};
  const dateLabel = (value, opts={}) => new Intl.DateTimeFormat('pt-BR',{timeZone:zone(),...opts}).format(value instanceof Date?value:new Date(value));
  const timeLabel = value => value?dateLabel(value,{hour:'2-digit',minute:'2-digit'}):'—';
  const longDay = day => new Intl.DateTimeFormat('pt-BR',{timeZone:'UTC',weekday:'long',day:'numeric',month:'long'}).format(dayDate(day));
  const fmtDate = value => value?new Intl.DateTimeFormat('pt-BR',{timeZone:'UTC',day:'2-digit',month:'2-digit',year:'numeric'}).format(dayDate(/^\d{4}-\d{2}-\d{2}$/.test(String(value))?String(value):dateKey(new Date(value)))):'—';
  function localISO(day,time) {
    const wall=Date.parse(day+'T'+(time||'00:00')+':00Z');
    let utc=wall;
    for(let i=0;i<3;i++){
      const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:zone(),year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(utc)).map(x=>[x.type,x.value]));
      const asUTC=Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute,+p.second);
      const correction=wall-asUTC;utc+=correction;if(!correction)break;
    }
    return new Date(utc).toISOString();
  }
  function phone(value){return sharedFields().phoneValue(value)||'';}
  function customerPhoneValue(value){
    const canonical=sharedFields().phoneValue(value,{allowInternational:true});
    if(canonical&&!/^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$/.test(canonical))
      throw Error('Informe telefone brasileiro com DDD ou celular paraguaio +595.');
    return canonical;
  }
  const sharedFields=()=>{if(!window.OrionFields)throw Error('Atualize a página para carregar os campos do formulário.');return window.OrionFields;};
  const professionalPhoneDisplay=value=>sharedFields().phoneDisplay(value);
  const professionalPhoneValue=value=>sharedFields().phoneValue(value);
  const cents=(value,options={})=>sharedFields().parseMoney(value,{required:true,min:0,max:1000000000,...options});
  const currencyInput=value=>sharedFields().moneyDisplay(value==null?0:Number(value));
  const currencyInputGrouped=currencyInput;
  function syncServiceDurationPresets(form){
    if(form?.dataset?.osForm!=='service')return;
    const value=String(form.elements.duration_min?.value||'');
    form.querySelectorAll('[data-os-duration]').forEach(button=>{
      button.setAttribute('aria-pressed',String(button.dataset.osDuration===value));
    });
  }
  function writable(ownerOnly=false){if(!tenant())return false;const role=state.bridge.user?.role;if(role==='support'||role==='admin')return true;const access=tenant()?.role;return ownerOnly?access==='owner':['owner','editor'].includes(access);}
  const canManageSiteRequests=()=>['support','admin'].includes(state.bridge.user?.role);
  function apiPath(suffix){if(!tenant())throw Error(businessAccessError()||'Não foi possível confirmar a empresa desta conta.');return '/api/v1/tenants/'+encodeURIComponent(state.tenantId)+suffix;}
  function mgmt(suffix){return apiPath('/management'+suffix);}
  function request(path,method='GET',data,options){if(typeof state.bridge.request!=='function')return Promise.reject(Error('A conexão com sua conta ainda não está pronta.'));if(subscriptionWorkspace()&&path.startsWith('/api/v1/tenants/')&&!path.endsWith('/management/permissions')&&!(path.startsWith('/api/v1/tenants/'+encodeURIComponent(state.tenantId)+'/subscription-whatsapp/')&&['devices.manage','chatbot.manage','templates.manage','sent_messages.manage','diagnostics.manage','campaigns.manage','scheduled_messages.manage','bulk_messages.manage','direct_messages.manage','group_contacts.manage','group_add.manage','account.manage'].includes(featureForPath(path,method,data))))return Promise.reject(Object.assign(Error('Esta função ainda não está disponível para este tipo de negócio.'),{status:403}));if(!permitted(featureForPath(path,method,data)))return Promise.reject(Object.assign(Error('Seu plano não inclui este recurso.'),{status:403}));return state.bridge.request(path,method,data,options).catch(error=>{if(error.status===403&&!platformWorkspace())void refreshPermissions({reconcile:true});throw error;});}
  function context(){return {epoch:state.epoch,tenantId:state.tenantId,load:state.load,route:state.route,accessRevision:state.entitlements?.revision};}
  function current(c){return state.mounted&&c.epoch===state.epoch&&c.tenantId===state.tenantId&&c.load===state.load&&c.route===state.route&&c.accessRevision===state.entitlements?.revision;}
  function stillAccount(c){return state.mounted&&c.epoch===state.epoch&&c.tenantId===state.tenantId&&c.accessRevision===state.entitlements?.revision;}
  const query = obj => {const p=new URLSearchParams();Object.entries(obj).forEach(([k,v])=>{if(v!==''&&v!=null)p.set(k,String(v));});return p.toString();};
  const periodQuery = () => query({start_date:state.filters.periodStart,end_date:state.filters.periodEnd});
  const statusLabels={archived:'Arquivada',paused:'Pausada',approved:'Autorizada',hold:'Reserva',confirmed:'Confirmado',completed:'Atendido',cancelled:'Cancelado',no_show:'Não compareceu',pending:'Pendente',paid:'Pago',available:'Disponível',redeemed:'Usado',expired:'Expirado',draft:'Rascunho',active:'Ativo',inactive:'Inativo'};
  function badge(status,label){const cls=['completed','paid','active','available','redeemed'].includes(status)?'os-success':['cancelled','no_show','expired'].includes(status)?'os-danger':['hold','pending'].includes(status)?'os-warning':'os-neutral';return '<span class="os-badge '+cls+'">'+esc(label||statusLabels[status]||status)+'</span>';}
  const button=(text,action,kind='',symbol='',extra='')=>'<button type="button" class="os-button '+kind+'" data-os-action="'+action+'" '+extra+'>'+(symbol?icon(symbol):'')+esc(text)+'</button>';
  const routeButton=(text,route,kind='',symbol='')=>subscriptionWorkspace()&&!routePermitted(route)?'':'<button type="button" class="os-button '+kind+'" data-os-route="'+route+'">'+(symbol?icon(symbol):'')+esc(text)+'</button>';
  function typedField(type,value,extra,name){
    if(type==='tel'){
      const internationalCustomer=name==='phone'||name==='customer_phone';
      value=sharedFields().phoneDisplay(value,{allowInternational:internationalCustomer});
      extra=extra.replace(/\s*(?:maxlength|placeholder|inputmode)="[^"]*"/g,'')+
        ' data-orion-field="phone"'+(internationalCustomer?' data-orion-international="true" data-orion-phone-scope="br-py"':'')+
        ' inputmode="tel" placeholder="(00) 00000-0000"';
      if(!/autocomplete=/.test(extra))extra+=' autocomplete="tel-national"';
    }else if(type==='email'){
      extra=extra.replace(/\s*placeholder="[^"]*"/g,'')+' data-orion-field="email" inputmode="email" autocapitalize="none" spellcheck="false" placeholder="nome@email.com"';
      if(!/autocomplete=/.test(extra))extra+=' autocomplete="email"';
    }else if(type==='date')extra+=' data-orion-field="date" lang="pt-BR"';
    else if(type==='time'||type==='month'||type==='datetime-local')extra+=' lang="pt-BR"';
    else if(type==='number'&&!/inputmode=/.test(extra))extra+=' inputmode="'+(/step="(?:any|0\.)/.test(extra)?'decimal':'numeric')+'"';
    if(name!=='slug'&&!/data-orion-field=/.test(extra)&&['text','number','url','time','month','datetime-local'].includes(type))extra+=' data-orion-field="native"';
    return {value,extra};
  }
  const field=(label,name,type='text',value='',extra='')=>{
    ({value,extra}=typedField(type,value,extra,name));
    return '<div class="os-field" data-field-wrapper><label for="osField-'+name+'">'+esc(label)+'</label><input id="osField-'+name+'" name="'+name+'" type="'+type+'" value="'+esc(value)+'" '+extra+'></div>';
  };
  const moneyField=(label,name,type='text',value='',extra='')=>field(label,name,'text',value,extra+' data-orion-field="money"'+(/data-field-min=/.test(extra)?'':' data-field-min="0"')+(/data-field-max=/.test(extra)?'':' data-field-max="1000000000"'));

  const brazilianStates=['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'];
  const documentDrafts=new WeakMap(),siteAddressRevisions=new WeakMap();
  function taxDocumentField(b){
    const kind=b.tax_document_kind||'none',enabled=kind!=='none';
    return field(enabled?kind.toUpperCase():'CPF / CNPJ','tax_document','text',b.tax_document||'',
      'data-orion-field="'+(enabled?kind:'cpf')+'" data-document-kind="'+kind+'" data-orion-original-kind="'+esc(kind)+'" data-orion-original="'+esc(b.tax_document||'')+'" '+(enabled?'':'disabled'));
  }
  function setTaxDocumentKind(input,kind){
    const previous=input.dataset.documentKind||'none';
    if(previous!==kind){const drafts=documentDrafts.get(input)||{};drafts[previous]=input.value;input.value=drafts[kind]??'';documentDrafts.set(input,drafts);}
    sharedFields().clearError(input);input.dataset.documentKind=kind;
    input.dataset.orionField=kind==='none'?'cpf':kind;input.disabled=kind==='none';input.required=false;
    input.closest('[data-field-wrapper]').querySelector('label').textContent=kind==='none'?'CPF / CNPJ':kind.toUpperCase();
    sharedFields().refresh(input);
  }
  function markSiteAddressEdit(input){
    const form=input.closest?.('#osSiteForm');if(!form||!['address','neighborhood','city','state'].includes(input.name))return;
    const revisions=siteAddressRevisions.get(form)||{};revisions[input.name]=(revisions[input.name]||0)+1;siteAddressRevisions.set(form,revisions);
  }
  const numericDrafts=new WeakMap();
  function numericAttributes(mode,{min=0,max,scale=2}={}){
    const monetary=['fixed','revenue_cents'].includes(mode);
    return 'data-orion-mode="'+mode+'" data-orion-field="'+(monetary?'money':'decimal')+'" data-field-min="'+min+'" data-field-max="'+(max??(monetary?1000000000:100))+'" data-field-scale="'+scale+'" inputmode="'+(scale===0&&!monetary?'numeric':'decimal')+'"';
  }
  function setNumericMode(input,mode,options={}){
    if(!input)return;
    const previous=input.dataset.orionMode;
    if(previous&&previous!==mode){
      const drafts=numericDrafts.get(input)||{};drafts[previous]=input.value;
      input.value=drafts[mode]??'';numericDrafts.set(input,drafts);
    }
    sharedFields().clearError(input);
    const attrs=numericAttributes(mode,options);
    for(const match of attrs.matchAll(/([\w-]+)="([^"]*)"/g))input.setAttribute(match[1],match[2]);
    input.placeholder=['fixed','revenue_cents'].includes(mode)?'0,00':options.scale===0?'Ex.: 100':'Ex.: 10,5';
    sharedFields().refresh(input);
  }

  /* Keep a real native input for iOS Safari's accessible color sheet. */
  const colorField=(label,name,value,compact=false)=>{
    const hex=cleanColor(value,name==='theme_color'?'#2e7bf6':'#8b5cf6').toUpperCase();
    return '<div class="os-field os-color-field'+(compact?' os-color-compact':'')+'">'+
      '<label class="'+(compact?'os-color-label-hidden':'')+'" for="osField-'+name+'">'+esc(label)+'</label>'+
      '<div class="os-color-control">'+
        '<span class="os-color-wheel" style="--os-selected-color:'+hex+'">'+
          '<span class="os-color-selected" aria-hidden="true"></span>'+
          '<input class="os-color-native" id="osField-'+name+'" type="color" name="'+name+'" value="'+hex+'" aria-label="'+esc(label)+'">'+
        '</span>'+
        (compact?'<span class="os-color-description os-color-custom-caption"><strong>Personalizar</strong><small>Escolher outra cor</small></span>':'<span class="os-color-description"><strong class="os-color-value">'+hex+'</strong><small>Toque para escolher</small></span>')+
      '</div></div>';
  };
  function syncColorPicker(input){
    if(!input?.matches?.('input.os-color-native'))return;
    const box=input.closest('.os-color-field'),hex=cleanColor(input.value).toUpperCase();
    const wheel=box?.querySelector('.os-color-wheel');
    if(wheel)wheel.style.setProperty('--os-selected-color',hex);
    const value=box?.querySelector('.os-color-value');
    if(value)value.textContent=hex;
    const palette=box?.closest('.os-color-presets');
    palette?.querySelectorAll('[data-os-color]').forEach(button=>{
      button.setAttribute('aria-pressed',String(button.dataset.osColor.toUpperCase()===hex));
    });
  }
  const selectField=(label,name,options,value='',extra='')=>'<div class="os-field" data-field-wrapper><label for="osField-'+name+'">'+esc(label)+'</label><select data-orion-field="native" id="osField-'+name+'" name="'+name+'" '+extra+'>'+options.map(([v,t])=>'<option value="'+esc(v)+'"'+(String(v)===String(value)?' selected':'')+'>'+esc(t)+'</option>').join('')+'</select></div>';
  const textField=(label,name,value='',extra='')=>'<div class="os-field" data-field-wrapper><label for="osField-'+name+'">'+esc(label)+'</label><textarea data-orion-field="native" id="osField-'+name+'" name="'+name+'" '+extra+'>'+esc(value)+'</textarea></div>';
  const checkField=(label,name,checked=false,help='')=>'<label class="os-check os-toggle"><span>'+esc(label)+(help?'<br><small>'+esc(help)+'</small>':'')+'</span><input type="checkbox" name="'+name+'" '+(checked?'checked':'')+'></label>';
  const empty=(symbol,title,text,action='')=>'<div class="os-empty"><div class="os-square-icon">'+icon(symbol)+'</div><h3>'+esc(title)+'</h3><p>'+esc(text)+'</p>'+action+'</div>';
  const loading=(text='Carregando…')=>'<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>'+esc(text)+'</div>';
  function errorHTML(error){return '<div class="os-notice os-error" role="alert">'+icon('info')+'<p>'+esc(error?.message||'Não foi possível consultar os dados. Tente novamente.')+'</p>'+button('Tentar novamente','reload','os-ghost os-small','refresh')+'</div>';}
  function toast(message,error=false){const r=$('osToastRegion');if(!r)return;const n=document.createElement('div');n.className='os-toast'+(error?' os-error':'');n.innerHTML=icon(error?'info':'check')+'<span>'+esc(message)+'</span>';r.appendChild(n);setTimeout(()=>n.remove(),6000);}
  function title(titleText,subtitle,actions=''){return '<div class="os-title-row"><div><h1>'+esc(titleText)+'</h1>'+(subtitle?'<p>'+esc(subtitle)+'</p>':'')+'</div>'+(actions?'<div class="os-actions">'+actions+'</div>':'')+'</div>';}
  function bodyHTML(html){clearSiteLookups();window.OrionSubscriptionGroupAdd?.unmount();window.OrionSubscriptionGroupContacts?.unmount();window.OrionSubscriptionDirectMessages?.unmount();window.OrionSubscriptionBulkMessages?.unmount();window.OrionSubscriptionScheduledMessages?.unmount();window.OrionSubscriptionCampaigns?.unmount();window.OrionSubscriptionDiagnostics?.unmount();window.OrionSubscriptionSentMessages?.unmount();window.OrionSubscriptionTemplates?.unmount();window.OrionSubscriptionChatbotTest?.unmount();window.OrionSubscriptionChatbot?.unmount();window.OrionSubscriptionDevices?.unmount();window.OrionSubscriptionDashboard?.unmount();window.OrionChannels?.unmount();window.OrionAutomations?.unmount();window.OrionCommunication?.unmount();window.OrionInbox?.unmount();window.OrionDentalDirectory?.unmount();window.OrionDentalClinic?.unmount?.();const body=$('osPageBody');if(body){body.innerHTML=html;window.OrionFields?.refresh(body);}}
  function pageHTML(html){$('page-suite').innerHTML=html;window.OrionFields?.refresh($('page-suite'));}
  function selectedNav(){
    let active=state.route==='cadastros'?'configuracoes':state.route;
    const channels=active==='configuracoes'&&state.filters.settingsTab==='whatsapp'&&!!document.querySelector('#osNav [data-os-action="managed-whatsapp"]');
    if(dentalWorkspace()&&dentalSetupRoutes().has(active))active='ajustes-odonto';
    document.querySelectorAll('.os-nav-link').forEach(link=>{
      if(channels?link.dataset.osAction==='managed-whatsapp':link.dataset.osRoute===active)link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    });
    updateAppDock();
  }

  function buildShell(){
    const app=$('appView'),sidebar=app.querySelector('.sidebar'),content=app.querySelector('.content');
    app.classList.add('orion-suite-enabled');sidebar.id='osSidebar';
    if(!$('osCollapse'))sidebar.querySelector('.brand').insertAdjacentHTML('beforeend','<button id="osCollapse" class="os-icon-button" type="button" data-os-action="collapse" title="Recolher menu" aria-label="Recolher menu">'+icon('left')+'</button>');
    if(!$('osSidebarContext'))sidebar.querySelector('.brand').insertAdjacentHTML('afterend','<div id="osSidebarContext" class="os-sidebar-context"></div><nav id="osNav" class="os-nav" aria-label="Navegação do Orion"></nav>');
    if(!$('osHeader'))content.insertAdjacentHTML('afterbegin','<header id="osHeader" class="os-header"><button id="osMenu" type="button" class="os-icon-button os-menu-button" data-os-action="menu" aria-label="Abrir menu" aria-controls="osSidebar" aria-expanded="false">'+icon('menu')+'</button><div class="os-mobile-brand"><img src="/brand/orion-brand-'+(document.documentElement.dataset.theme==='dark'?'dark':'light')+'.svg" data-orion-logo alt="Orion" width="104" height="32"></div><span class="os-header-spacer"></span><span id="osHeaderNotice" class="os-header-notice"></span><div class="os-theme"><button id="osThemeToggle" class="os-icon-button" type="button" data-os-action="theme-menu" aria-label="Escolher aparência" aria-expanded="false">'+icon('sun')+'</button><div id="osThemeMenu" class="os-theme-menu hidden" aria-label="Aparência"><button type="button" data-os-theme="light">'+icon('sun')+'Claro</button><button type="button" data-os-theme="dark">'+icon('moon')+'Escuro</button><button type="button" data-os-theme="auto">'+icon('monitor')+'Sistema</button></div></div><div class="os-notifications"><button id="osNotificationsToggle" class="os-icon-button" type="button" data-os-action="notifications" aria-label="Notificações" aria-expanded="false" aria-controls="osNotificationsPanel">'+icon('bell')+'<span id="osNotificationDot" class="os-notification-dot hidden"></span></button><section id="osNotificationsPanel" class="os-notifications-panel hidden" aria-label="Notificações" tabindex="-1"></section></div></header>');
    if(!$('page-suite'))content.insertAdjacentHTML('beforeend','<section id="page-suite" class="page os-main" aria-label="Gestão Orion"></section>');
    if(!$('osScrim'))app.insertAdjacentHTML('beforeend','<button id="osScrim" type="button" class="os-scrim" data-os-action="close-menu" aria-label="Fechar menu"></button><button id="osConsultantFab" class="os-fab" type="button" data-os-route="consultor" aria-label="Abrir Consultor IA"><span>'+icon('bot')+'</span><span class="os-fab-label">Consultor IA</span></button>');
    if(!$('osAppDock'))app.insertAdjacentHTML('beforeend','<nav id="osAppDock" class="os-app-dock" aria-label="Navegação rápida" hidden></nav>');
    if(!$('osDialog'))document.body.insertAdjacentHTML('beforeend','<dialog id="osDialog" class="os-dialog" aria-labelledby="osDialogTitle"></dialog><div id="osToastRegion" class="os-toast-region" aria-live="polite" aria-atomic="false"></div>');
    renderSidebar();applyThemeState();
  }
  function closeNotifications(restoreFocus=false){state.notificationsOpen=false;state.notificationsRead=(state.notificationsRead||0)+1;$('osNotificationsPanel')?.classList.add('hidden');$('osNotificationsToggle')?.setAttribute('aria-expanded','false');if(restoreFocus)$('osNotificationsToggle')?.focus();}
  async function loadNotifications(){
    if(!tenant()){$('osNotificationsPanel').innerHTML=empty('bell','Empresa indisponível','Confirme o acesso à sua empresa para consultar os avisos de agendamento.');return;}
    const c=context(),read=(state.notificationsRead||0)+1;state.notificationsRead=read;$('osNotificationsPanel').innerHTML=loading('Carregando notificações…');
    try{const data=await request(mgmt('/notifications?'+query({unread_only:state.filters.notificationsUnread})));if(!stillAccount(c)||read!==state.notificationsRead||!state.notificationsOpen)return;state.data.notifications=data;renderNotifications();}
    catch(error){if(stillAccount(c)&&read===state.notificationsRead&&state.notificationsOpen)$('osNotificationsPanel').innerHTML='<div class="os-notification-head"><h2>Notificações</h2>'+button('','close-notifications','os-ghost os-small','close','aria-label="Fechar notificações"')+'</div><div class="os-notification-error"><p role="alert">'+esc(error.message)+'</p>'+button('Tentar novamente','refresh-notifications','os-outline os-small','refresh')+'</div>';}
  }
  function renderNotifications(){
    const data=state.data.notifications||{},items=rows(data),unread=items.filter(item=>!item.read_at),labels={confirmed:'Agendamento confirmado',rescheduled:'Agendamento alterado',cancelled:'Agendamento cancelado',completed:'Atendimento concluído',no_show:'Ausência registrada'};
    $('osNotificationsPanel').innerHTML='<div class="os-notification-head"><div><h2>Notificações</h2><p>'+ (unread.length?unread.length+' não lida'+(unread.length>1?'s':''):'Tudo em dia')+'</p></div>'+button('Ler todas','read-notifications','os-ghost os-small','check',data.can_mark_read&&unread.length?'':'disabled')+button('','close-notifications','os-ghost os-small','close','aria-label="Fechar notificações"')+'</div><div class="os-notification-tabs" role="tablist" aria-label="Filtro de notificações">'+[[false,'Todas'],[true,'Não lidas']].map(([value,label])=>'<button type="button" role="tab" aria-selected="'+(state.filters.notificationsUnread===value)+'" data-os-notification-filter="'+value+'">'+label+'</button>').join('')+'</div><div class="os-notification-list">'+(items.length?items.map(item=>'<button type="button" class="os-notification-item '+(!item.read_at?'os-unread':'')+'" data-os-notification="'+esc(item.id)+'"><span class="os-square-icon">'+icon(['completed','confirmed'].includes(item.event)?'check':item.event==='rescheduled'?'refresh':item.event==='cancelled'?'close':'calendar')+'</span><span><strong>'+esc(labels[item.event]||item.event)+'</strong><span>'+esc(item.customer_name)+' · '+esc(item.professional_name)+'</span><small>'+fmtDate(dateKey(new Date(item.happened_at)))+' às '+timeLabel(item.happened_at)+'</small></span></button>').join(''):empty('bell','Sem notificações','Confirmações e alterações de agendamentos aparecerão aqui.'))+'</div>';
    $('osNotificationDot')?.classList.toggle('hidden',!unread.length);
  }
  async function markNotificationsRead(ids){
    if(!state.data.notifications?.can_mark_read||!ids.length)return;const c=context();await request(mgmt('/notifications/read'),'POST',{ids:ids.slice(0,100)});if(!stillAccount(c))return;const now=new Date().toISOString();rows(state.data.notifications).forEach(item=>{if(ids.includes(item.id))item.read_at=now;});if(state.notificationsOpen)await loadNotifications();
  }
  function dentalSetupGroups(){
    const groups=[
      ['Atendimento',[
        ['servicos','tooth','Procedimentos'],
        ['funcionarios','badgeUser','Profissionais e equipe'],
        ['configuracoes','calendar','Horários de atendimento','hours'],
        ['configuracoes','building','Consultórios e cadeiras','dental-chairs'],
        ['configuracoes','fileCheck','Modelos de anamnese','dental-intake'],
        ...(dentalSettingsAllowed()?[['clinica','shield','Acessos ao prontuário','dental-access']]:[])
      ]],
      ['Clínica e conta',[
        ...(dedicatedPatients()&&routePermitted('pacientes-odonto')?[['clientes','users','Contatos da agenda']]:[]),
        ['site','globe','Site da clínica'],
        ['configuracoes','settings','Preferências da clínica'],
        ['configuracoes','layers','Etapas de vendas','dental-sales-stages'],
        ['plano','credit','Plano da clínica']
      ]]
    ];
    return groups.map(([heading,entries])=>[heading,entries.filter(([route,,,tab])=>
      routePermitted(route)&&(!tab||tab==='dental-access'||settingsTabAllowed(tab)))]).filter(([,entries])=>entries.length);
  }
  function dentalSetupRoutes(){
    return new Set(dentalSetupGroups().flatMap(([,entries])=>entries.map(([route])=>route).filter(route=>route!=='clinica')));
  }
  function dentalSetupPage(){
    const groups=dentalSetupGroups().map(([heading,entries])=>{
      const items=entries.map(([route,symbol,label,tab])=>
        '<button type="button" class="os-app-profile-item" data-os-route="'+route+'"'+(tab?' data-os-dental-setting="'+tab+'"':'')+'>'+
        '<span class="os-app-profile-item-icon" aria-hidden="true">'+icon(symbol)+'</span><span>'+esc(label)+'</span>'+icon('chevron')+'</button>').join('');
      return '<section class="os-app-profile-group"><h2>'+esc(heading)+'</h2><div class="os-app-profile-items">'+items+'</div></section>';
    });
    return title('Ajustes da clínica','')+'<div id="osPageBody" class="os-app-profile-groups">'+groups.join('')+'</div>';
  }
  function renderSidebar(){
    const focused=document.activeElement,previousFocus=focused?.closest?.('#osNav')?{route:focused.dataset.osRoute,action:focused.dataset.osAction}:null;
    const t=tenant(),isAdmin=['support','admin'].includes(state.bridge.user?.role);
    $('appView')?.classList.toggle('os-dental-workspace',dentalWorkspace());
    $('osSidebarContext').classList.toggle('os-sidebar-context--subscription',subscriptionWorkspace());
    $('osSidebarContext').innerHTML='<div class="os-overline">Meu negócio</div><div class="os-tenant-name">'+esc(t?.name||(businessAccessError()?'Acesso pendente':state.bridge.businessContextReady===false?'Conferindo empresa…':'Sua empresa'))+'</div><div class="os-sidebar-badges"><span class="os-badge">'+esc(isAdmin?'Administração':t?.role==='viewer'?'Consulta':t?.role==='editor'?'Equipe':'Minha empresa')+'</span></div>'+(t&&!subscriptionWorkspace()&&!dentalWorkspace()?'<button type="button" class="os-side-site" data-os-route="site">meu site '+icon('external')+'</button>':'');
    const navItem=([route,symbol],extra='')=>'<button type="button" class="os-nav-link'+extra+'" data-os-route="'+route+'" title="'+esc(routeLabel(route))+'">'+icon(symbol)+'<span>'+esc(routeLabel(route))+'</span></button>';
    let nav=subscriptionWorkspace()?'':isAdmin?navItem(['dispositivo','phone'],' os-nav-connection'):t&&permitted('conversations.manual')?'<button type="button" class="os-nav-link os-nav-connection" data-os-action="managed-whatsapp" title="Conectar WhatsApp">'+icon('phone')+'<span>Conectar WhatsApp</span></button>':'';
    const navAvailable=mainNav.filter(([route])=>routePermitted(route)&&!(subscriptionWorkspace()&&route==='configuracoes'));
    const permittedNav=subscriptionWorkspace()?[...navAvailable.filter(([route])=>route==='dashboard'),...navAvailable.filter(([route])=>route!=='dashboard')]:navAvailable;
    if(dentalWorkspace()){
      const groups=[
        ['Atendimento',['dashboard','agenda',dedicatedPatients()&&routePermitted('pacientes-odonto')?'pacientes-odonto':'clientes','clinica']],
        ['Operação da clínica',['proteses-odonto','estoque-odonto','vendas-odonto','financeiro','desempenho']],
        ['Relacionamento',['inbox','marketing','fidelizacao','consultor']],
        ['Administração',['ajustes-odonto']]
      ];
      for(const [heading,routes] of groups){
        const items=routes.map(route=>permittedNav.find(item=>item[0]===route)).filter(Boolean);
        if(items.length)nav+='<div class="os-nav-section">'+heading+'</div>'+items.map(item=>navItem(item)).join('');
      }
    }else{
      nav+='<div class="os-nav-section">Gestão</div>'+permittedNav.filter(([route])=>!toolNavRoutes.includes(route)).map(item=>navItem(item)).join('');
    }
    if(state.bridge.user?.role==='admin'&&!subscriptionWorkspace()){
      nav+='<div class="os-nav-section">Atendimento Orion</div>';
      const attendance=[['conversas','messages'],['ai','flask'],['empresa','building'],['catalogo','box']];
      attendance.forEach(item=>{nav+=navItem(item);});
    }
    const availableTools=toolNavRoutes.map(route=>permittedNav.find(([name])=>name===route)).filter(Boolean);
    if(availableTools.length)nav+='<div class="os-nav-section">Ferramentas</div>'+availableTools.map(item=>navItem(item)).join('');
    nav+='<div class="os-nav-section">Ajuda e conta</div>'+[['novidades','bell'],['ajuda','book'],['member','shield']].map(([route,symbol])=>{
      const label=route==='member'&&state.bridge.user?.role==='admin'&&!subscriptionWorkspace()?'Administração de clientes':routeNames[route];
      return '<button type="button" class="os-nav-link os-nav-utility" data-os-route="'+route+'" title="'+esc(label)+'">'+icon(symbol)+'<span>'+esc(label)+'</span></button>';
    }).join('');
    if(subscriptionWorkspace()&&routePermitted('configuracoes'))nav+='<button type="button" class="os-nav-link os-nav-utility" data-os-app-settings="appearance" title="Aparência">'+icon('sun')+'<span>Aparência</span></button>';
    $('osNav').innerHTML=nav;selectedNav();applyPermissionDOM();
    if(previousFocus){const target=previousFocus.route?$('osNav').querySelector('[data-os-route="'+previousFocus.route+'"]'):$('osNav').querySelector('[data-os-action="'+previousFocus.action+'"]');(target||$('osNav').querySelector('[data-os-route="'+fallbackRoute()+'"]'))?.focus({preventScroll:true});}
  }
  function applyThemeState(){const pref=document.documentElement.dataset.themePreference||'auto',effective=document.documentElement.dataset.theme||'light';document.querySelectorAll('[data-os-theme]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.osTheme===pref)));const toggle=$('osThemeToggle');if(toggle)toggle.innerHTML=icon(effective==='dark'?'moon':'sun');document.querySelectorAll('.os-mobile-brand [data-orion-logo]').forEach(i=>i.src='/brand/orion-brand-'+effective+'.svg');}
  function setTheme(preference){const original=document.querySelector('.theme-choice[data-theme-choice="'+preference+'"]');if(original)original.click();else{document.documentElement.dataset.themePreference=preference;document.documentElement.dataset.theme=preference==='auto'?(matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light'):preference;try{localStorage.setItem('orion-theme',preference);}catch{}}applyThemeState();$('osThemeMenu').classList.add('hidden');$('osThemeToggle').setAttribute('aria-expanded','false');}

  // Navigation is a presentation layer. All booking, finance and access logic stays shared.
  // Authorization is server-derived from the verified business niche.
  const appLayoutEligible=()=>!!tenant()&&!platformWorkspace()&&state.entitlements?.layout_modes?.includes('app')===true;
  const appLayoutEnabled=()=>state.layoutMode==='app'&&appLayoutEligible();
  const appLayoutKey=()=>state.tenantId&&(state.bridge.user?.id||state.bridge.user?.email)?
    'orion-navigation-v1:'+state.tenantId+':'+String(state.bridge.user.id||state.bridge.user.email).toLowerCase():null;
  function savedAppLayout(){
    try{const key=appLayoutKey();return key&&localStorage.getItem(key)==='app'?'app':'classic';}
    catch{return 'classic';}
  }
  // Stored locally, scoped to the authenticated account and single company.
  // Only server-authorized routes can ever become interactive shortcuts.
  const appShortcutKey=()=>{
    const key=appLayoutKey();return key?'orion-quick-tabs-v1:'+key.slice('orion-navigation-v1:'.length)+(subscriptionWorkspace()?':subscription_services':''):null;
  };
  const appTabAllowed=tab=>tab==='perfil'?appLayoutEnabled():
    tab==='hours'?routePermitted('configuracoes')&&settingsTabAllowed('hours'):
    appShortcutChoices.some(([route])=>route===tab)&&routePermitted(tab);
  function savedAppShortcuts(){
    try{
      const key=appShortcutKey(),raw=key?localStorage.getItem(key):null;
      if(raw===null||raw.length>1024)return null;
      const chosen=JSON.parse(raw);
      if(!Array.isArray(chosen)||chosen.length>4||new Set(chosen).size!==chosen.length||
        chosen.some(route=>typeof route!=='string'||!appShortcutChoices.some(([name])=>name===route)))return null;
      return chosen;
    }catch{return null;}
  }
  function appShortcutSelection(){
    const saved=savedAppShortcuts(),selected=saved===null?appDefaultShortcuts():saved;
    return selected.filter((name,index)=>selected.indexOf(name)===index&&appTabAllowed(name)).slice(0,4);
  }
  function appLayoutTabs(){
    return [...appShortcutSelection().map(route=>appShortcutChoices.find(item=>item[0]===route)),appProfileShortcut];
  }
  function appActiveTab(){
    const route=state.route==='configuracoes'&&state.filters.settingsTab==='hours'?'hours':state.route;
    return appLayoutTabs().some(([tab])=>tab===route)?route:'perfil';
  }
  function updateAppDock(){
    const dock=$('osAppDock');if(!dock||dock.hidden)return;
    const tabs=appLayoutTabs(),signature=tabs.map(([route])=>route).join('|');
    if(dock.dataset.tabSignature!==signature){renderAppDock();return;}
    const active=appActiveTab(),index=tabs.findIndex(([name])=>name===active);
    dock.style.setProperty('--os-app-active',String(Math.max(0,index)));
    dock.style.setProperty('--os-app-offset',(Math.max(0,index)*100)+'%');
    dock.style.setProperty('--os-app-y',(Math.max(0,index)*69)+'px');
    dock.querySelectorAll('[data-os-app-tab]').forEach(button=>{
      if(button.dataset.osAppTab===active)button.setAttribute('aria-current','page');
      else button.removeAttribute('aria-current');
      button.disabled=!appTabAllowed(button.dataset.osAppTab);
    });
  }
  function renderAppDock(){
    const dock=$('osAppDock');if(!dock)return;
    if(!appLayoutEnabled()){dock.hidden=true;dock.replaceChildren();delete dock.dataset.tabSignature;return;}
    const tabs=appLayoutTabs();
    dock.hidden=false;
    dock.dataset.tabSignature=tabs.map(([name])=>name).join('|');
    dock.style.setProperty('--os-app-count',String(tabs.length));
    dock.style.setProperty('--os-app-width',(100/tabs.length)+'%');
    dock.innerHTML='<div class="os-app-dock-rail"><span class="os-app-dock-indicator" aria-hidden="true"></span>'+
      tabs.map(([name,symbol,label])=>'<button type="button" class="os-app-dock-item" data-os-app-tab="'+name+'" aria-label="'+esc(label)+'" title="'+esc(label)+'"'+
        (appTabAllowed(name)?'':' disabled aria-disabled="true"')+'><span class="os-app-dock-symbol">'+icon(symbol)+'</span><span class="os-app-dock-label">'+esc(name==='enviar-mensagem'?'Enviar':label)+'</span></button>').join('')+'</div>';
    updateAppDock();
  }
  function appShortcutDraft(){
    if(!Array.isArray(state.appShortcutDraft)){
      const saved=savedAppShortcuts(),selected=saved===null?appDefaultShortcuts():saved;
      state.appShortcutDraft=selected.filter(name=>appTabAllowed(name)).slice(0,4);
      while(state.appShortcutDraft.length<4)state.appShortcutDraft.push('');
    }
    return state.appShortcutDraft;
  }
  function appShortcutEditor(){
    const draft=appShortcutDraft(),allowed=appShortcutChoices.filter(([name])=>appTabAllowed(name));
    const preview=[...draft.filter(Boolean).map(name=>appShortcutChoices.find(item=>item[0]===name)),appProfileShortcut];
    const fields=Array.from({length:4},(_,index)=>{
      const selected=draft[index]||'',item=appShortcutChoices.find(entry=>entry[0]===selected);
      return '<div class="os-app-shortcut-row"><span class="os-app-shortcut-number">'+(index+1)+'</span>'+
        '<span class="os-app-shortcut-icon" aria-hidden="true">'+icon(item?.[1]||'plus')+'</span>'+
        '<div class="os-app-shortcut-field"><label for="osAppShortcut'+index+'">Atalho '+(index+1)+'</label>'+
        '<select id="osAppShortcut'+index+'" data-os-app-shortcut="'+index+'" aria-label="Escolher atalho '+(index+1)+'">'+
        '<option value=""'+(selected?'':' selected')+'>Nenhum atalho</option>'+
        allowed.map(([name,,label])=>'<option value="'+name+'"'+(name===selected?' selected':'')+'>'+esc(label)+'</option>').join('')+
        '</select></div></div>';
    }).join('');
    return '<section class="os-card os-section-gap os-app-shortcut-settings" aria-labelledby="osAppShortcutsTitle">'+
      '<div class="os-card-header"><div><h2 id="osAppShortcutsTitle">Atalhos do modo Aplicativo</h2>'+
      '<p>Escolha até quatro atalhos. Perfil fica sempre na última posição.</p></div></div>'+
      '<div class="os-app-shortcuts-list">'+fields+
      '<div class="os-app-shortcut-row os-app-shortcut-locked"><span class="os-app-shortcut-number">5</span>'+
      '<span class="os-app-shortcut-icon" aria-hidden="true">'+icon('user')+'</span>'+
      '<div class="os-app-shortcut-fixed-copy"><strong>Perfil</strong><small>Atalho fixo</small></div>'+
      '<span class="os-app-shortcut-lock" aria-label="Não pode ser removido">'+icon('lock')+'</span></div></div>'+
      '<div class="os-app-shortcut-preview"><strong>Prévia da navegação</strong>'+
      '<div class="os-app-shortcut-preview-items" style="--os-preview-count:'+preview.length+'">'+preview.map(([,symbol,label])=>
        '<span class="os-app-shortcut-preview-item">'+icon(symbol)+'<small>'+esc(label==='Enviar mensagens'?'Enviar':label)+'</small></span>').join('')+'</div></div>'+
      '<p class="os-help">Personalização salva somente para esta conta neste dispositivo. As outras funções continuam em Perfil.</p>'+
      '<div class="os-form-actions os-app-shortcut-actions">'+
      button('Restaurar padrão','app-shortcuts-default','os-outline','refresh')+
      button('Salvar atalhos','app-shortcuts-save','','check')+'</div></section>';
  }
  function changeAppShortcut(index,route){
    if(!appLayoutEligible()||!Number.isInteger(index)||index<0||index>=4||
      (route!==''&&!appTabAllowed(route)))return;
    const draft=appShortcutDraft(),previous=draft[index],collision=route?draft.indexOf(route):-1;
    if(previous===route)return;
    if(collision>=0&&collision!==index)draft[collision]=previous;
    draft[index]=route;
    if(state.route==='configuracoes'&&state.filters.settingsTab==='appearance'){
      renderSettings();$('osAppShortcut'+index)?.focus({preventScroll:true});
    }
  }
  function saveAppShortcuts(){
    if(!appLayoutEligible()||state.route!=='configuracoes'||state.filters.settingsTab!=='appearance')return;
    const selected=appShortcutDraft().filter(Boolean);
    if(selected.length>4||new Set(selected).size!==selected.length||selected.some(name=>!appTabAllowed(name))){
      toast('Confira os atalhos antes de salvar.',true);return;
    }
    const key=appShortcutKey();
    if(!key){toast('Não foi possível identificar a conta para salvar os atalhos.',true);return;}
    try{localStorage.setItem(key,JSON.stringify(selected));}
    catch{toast('Não foi possível salvar os atalhos neste dispositivo.',true);return;}
    state.appShortcutDraft=null;
    renderAppDock();renderSettings();
    $('osPageBody')?.querySelector('[data-os-action=app-shortcuts-save]')?.focus({preventScroll:true});
    toast('Atalhos salvos. A navegação foi atualizada.');
  }
  function applyAppLayout(preference=null){
    const previous=state.layoutMode;
    state.layoutMode=appLayoutEligible()&&(preference||savedAppLayout())==='app'?'app':'classic';
    $('appView')?.classList.toggle('os-layout-app',state.layoutMode==='app');
    const dock=$('osAppDock');
    if(dock){
      if(state.layoutMode!=='app'){dock.hidden=true;dock.replaceChildren();}
      else if(previous!==state.layoutMode||!dock.firstElementChild)renderAppDock();
      else updateAppDock();
    }
    selectedNav();
  }
  function chooseAppLayout(mode){
    if(!appLayoutEligible()||!['classic','app'].includes(mode))return;
    let stored=false;
    try{const key=appLayoutKey();if(key){localStorage.setItem(key,mode);stored=true;}}catch{}
    applyAppLayout(mode);
    if(state.route==='configuracoes'&&state.filters.settingsTab==='appearance'){
      renderSettings();
      $('osPageBody')?.querySelector('[data-os-layout="'+mode+'"]')?.focus({preventScroll:true});
    }
    toast('Layout '+(mode==='app'?'Aplicativo':'Clássico')+' ativado.'+(stored?'':' Esta escolha não pôde ser salva neste navegador.'));
  }
  function appProfileView(){
    const groups=subscriptionWorkspace()?[
      ['Visão geral',[['dashboard','home']]],
      ['Atendimento e chatbot',[['dispositivos','phone'],['chatbot','bot'],['testar-chatbot','flask'],['templates','layers'],['mensagens-enviadas','fileCheck'],['diagnosticos','activity']]],
      ['Envios e campanhas',[['enviar-mensagem','send'],['programadas','calendarClock'],['campanhas','megaphone'],['massa','broadcast']]],
      ['Ferramentas',[['aquecimento','flame'],['evitar-banimento','shieldCheck'],['resolver-banimento','shieldAlert'],['extrator-contatos','usersSearch'],['adicionar-ao-grupo','userPlus']]],
      ['Configurações e conta',[['configuracoes','settings'],['novidades','bell'],['ajuda','book'],['member','user']]]
    ]:[
      ['Agenda e operação',[['dashboard','home'],['agenda','calendar'],['servicos','scissors'],['hours','clock'],['clientes','users'],['funcionarios','badgeUser']]],
      ['Gestão do negócio',[['financeiro','wallet'],['desempenho','chart']]],
      ['Atendimento e relacionamento',[['inbox','chat'],['fidelizacao','gift'],['marketing','sparkles'],['consultor','bot']]],
      ['Site e configurações',[['site','globe'],['configuracoes','settings'],['plano','credit']]],
      ['Ajuda e conta',[['novidades','bell'],['ajuda','book'],['member','user']]]
    ];
    const sections=groups.map(([heading,items],i)=>{
      const allowed=items.filter(([route])=>route==='hours'?appTabAllowed('hours'):routePermitted(route));if(!allowed.length)return '';
      const rows=allowed.map(([route,symbol])=>
        '<button type="button" class="os-app-profile-item" '+(route==='hours'?'data-os-app-tab="hours"':'data-os-route="'+route+'"')+'><span class="os-app-profile-item-icon">'+icon(symbol)+'</span><span>'+esc(route==='hours'?'Horários':routeNames[route])+'</span>'+icon('chevron')+'</button>').join('');
      const appearance=['Site e configurações','Configurações e conta'].includes(heading)&&settingsTabAllowed('appearance')?
        '<button type="button" class="os-app-profile-item" data-os-app-settings="appearance"><span class="os-app-profile-item-icon">'+icon('sun')+'</span><span>Aparência</span>'+icon('chevron')+'</button>':'';
      return '<section class="os-app-profile-group" aria-labelledby="osAppProfileGroup'+i+'"><h2 id="osAppProfileGroup'+i+'">'+esc(heading)+'</h2><div class="os-app-profile-items">'+rows+appearance+'</div></section>';
    }).join('');
    return '<div class="os-app-profile-view"><header class="os-app-profile-title"><h1>Perfil</h1><p>Seu negócio e todas as ferramentas do Orion.</p></header>'+
      '<div class="os-app-profile-hero"><span class="os-avatar" aria-hidden="true">'+esc(initials(tenant()?.name||'Orion'))+'</span><div><strong>'+esc(tenant()?.name||'Minha empresa')+'</strong><small>'+esc(state.bridge.user?.name||'Minha conta')+'</small></div></div>'+
      '<div class="os-app-profile-groups">'+sections+'</div></div>';
  }
  function appBlocksView(){
    const professionals=rows(state.data.professionals);
    const blocks=[...rows(state.data.scheduleBlocks).map(item=>({...item,_resource:'schedule_blocks'})),
      ...rows(state.data.timeOff).map(item=>({...item,_resource:'time_off'}))]
      .filter(item=>item.id&&item.starts_at&&item.ends_at)
      .sort((a,b)=>String(a.starts_at).localeCompare(String(b.starts_at))).slice(0,150);
    const header='<div class="os-app-blocks-head"><p class="os-help">Os bloqueios também aparecem na Agenda.</p>'+
      (writable()?button('Novo bloqueio','new-block','','plus'):'')+'</div>';
    if(!blocks.length)return '<section class="os-card">'+header+empty('block','Nenhum bloqueio registrado','Bloqueie um intervalo ou dia inteiro quando necessário.')+'</section>';
    return '<section class="os-card">'+header+'<div class="os-app-blocks-list">'+blocks.map(item=>{
      const professional=professionals.find(person=>person.id===item.professional_id);
      return '<button type="button" class="os-app-profile-item os-app-block-item" data-os-block="'+esc(item.id)+'" data-resource="'+esc(item._resource)+'"><span class="os-app-profile-item-icon">'+icon('block')+'</span><span><strong>'+esc(fmtDate(dateKey(new Date(item.starts_at))))+' · '+esc(timeLabel(item.starts_at))+'–'+esc(timeLabel(item.ends_at))+'</strong><small>'+esc(item.reason||'Horário bloqueado')+(professional?' · '+esc(professional.name):'')+'</small></span>'+icon('chevron')+'</button>';
    }).join('')+'</div></section>';
  }
  function appHoursView(){
    const mode=state.filters.appHoursView==='blocks'?'blocks':'hours';
    return '<section class="os-app-hours-view"><div class="os-app-hours-tabs" role="tablist" aria-label="Horários">'+
      [['hours','Dias de atendimento'],['blocks','Bloqueios']].map(([value,label])=>'<button type="button" role="tab" data-os-app-hours="'+value+'" aria-selected="'+(mode===value)+'">'+label+'</button>').join('')+
      '</div><div class="os-app-hours-panel" role="tabpanel">'+(mode==='blocks'?appBlocksView():settingsHours())+'</div></section>';
  }

  function drawer(open){const app=$('appView'),side=$('osSidebar');if(!app||!side)return;app.classList.toggle('os-menu-open',open);$('osMenu').setAttribute('aria-expanded',String(open));if(open){state.drawerTrigger=document.activeElement;side.querySelector('button')?.focus();}else state.drawerTrigger?.focus?.();}
  function resetPeriod(){const today=dateKey(),d=dayDate(today);d.setUTCMonth(d.getUTCMonth()+1,0);state.filters.periodStart=today.slice(0,8)+'01';state.filters.periodEnd=d.toISOString().slice(0,10);state.filters.performancePeriod='month';state.filters.performanceDay=today;}
  function preservePerformancePeriod(nextRoute){
    const f=state.filters;
    if(state.route==='desempenho'&&nextRoute!=='desempenho')f.performanceRange={periodStart:f.periodStart,periodEnd:f.periodEnd,performancePeriod:f.performancePeriod,performanceDay:f.performanceDay};
    if(nextRoute==='desempenho'&&state.route!=='desempenho'){
      if(f.performanceRange)Object.assign(f,f.performanceRange);
      else setPerformancePeriod(f.performancePeriod||'month');
    }
  }
  function legacyOpened(route){stopHomeLive();if(state.mounted&&subscriptionWorkspace()&&!routePermitted(route)){void open(fallbackRoute(),{force:true});return;}if(route==='cadastros'){void open(subscriptionWorkspace()?'dashboard':'configuracoes',{force:true});return;}if(!state.mounted||!legacyPages.has(route))return;if(platformAdminOnlyPages.has(route)&&state.bridge.user?.role!=='admin'){void open('dashboard',{force:true});return;}clearSiteLookups();preservePerformancePeriod(route);state.route=route;state.load++;state.filters.agendaTablet=false;clearInterval(state.clockTimer);$('appView')?.classList.remove('os-tablet-mode');closeNotifications();closeDialog();selectedNav();$('page-suite').classList.add('hidden');$('osConsultantFab')?.classList.add('hidden');drawer(false);document.title=routeLabel(route)+' · Orion';}
  async function open(route,options={}){
    if(route!=='dashboard')stopHomeLive();
    route=routeAliases.get(route)||route;
    const navigationRead=state.navigationRead=(state.navigationRead||0)+1;state.pendingRoute=route;
    if(route==='cadastros')route=subscriptionWorkspace()?'dashboard':'configuracoes';
    if(!appPages.has(route))route='dashboard';
    // Never open the legacy operator console from a member/support bookmark,
    // stale URL, programmatic link or previous account's navigation state.
    if(platformAdminOnlyPages.has(route)&&state.bridge.user?.role!=='admin')route='dashboard';
    if(state.busy&&!options.force)return;
    // The platform-admin customer directory is account-wide; it must remain
    // reachable even when the operator has no selected business context.
    if(route==='member'&&state.bridge.user?.role==='admin'){
      clearSiteLookups();
      legacyOpened(route);
      state.bridge.openLegacy?.(route);
      return;
    }
    clearSiteLookups();
    window.OrionSubscriptionGroupAdd?.unmount();window.OrionSubscriptionGroupContacts?.unmount();window.OrionSubscriptionDirectMessages?.unmount();window.OrionSubscriptionBulkMessages?.unmount();window.OrionSubscriptionScheduledMessages?.unmount();window.OrionSubscriptionCampaigns?.unmount();window.OrionSubscriptionDiagnostics?.unmount();window.OrionSubscriptionSentMessages?.unmount();window.OrionSubscriptionTemplates?.unmount();window.OrionSubscriptionChatbotTest?.unmount();window.OrionSubscriptionChatbot?.unmount();window.OrionSubscriptionDevices?.unmount();window.OrionSubscriptionDashboard?.unmount();window.OrionChannels?.unmount();window.OrionAutomations?.unmount();window.OrionCommunication?.unmount();window.OrionInbox?.unmount();window.OrionDentalDirectory?.unmount();window.OrionDentalClinic?.unmount?.();
    if(!tenant()){
      preservePerformancePeriod(route);state.route=route;state.load++;closeNotifications();closeDialog(true);drawer(false);
      document.querySelectorAll('#appView .content>.page').forEach(p=>p.classList.toggle('hidden',p.id!=='page-suite'));$('osConsultantFab')?.classList.add('hidden');selectedNav();
      const error=businessAccessError();
      pageHTML(error?title('Acesso à sua empresa','Confira o vínculo da sua conta.')+'<div class="os-card"><p class="os-notice os-error" role="alert">'+esc(error)+'</p>'+button('Verificar novamente','business-context-retry','os-outline','refresh')+'</div>':state.bridge.businessContextReady===false?loading('Conferindo o acesso à sua empresa…'):title('Sua empresa','Conclua o cadastro do negócio para continuar.')+'<div class="os-card">'+empty('users','Nenhuma empresa vinculada','Esta conta ainda não possui uma empresa disponível.')+'</div>');
      try{const url=new URL(location.href);url.searchParams.delete('empresa');history.replaceState({...window.OrionSubscriptionSentMessages?.navigationState?.(),...window.OrionSubscriptionDiagnostics?.navigationState?.(),...window.OrionSubscriptionGroupAdd?.navigationState?.(),...window.OrionSubscriptionGroupContacts?.navigationState?.(),...window.OrionSubscriptionDirectMessages?.navigationState?.(),...window.OrionSubscriptionBulkMessages?.navigationState?.(),...window.OrionSubscriptionScheduledMessages?.navigationState?.(),...window.OrionSubscriptionCampaigns?.navigationState?.()},'',url.pathname+url.search);}catch{}
      return;
    }
    const permissionEpoch=state.epoch,permissionTenant=state.tenantId;
    if(!options.permissionsChecked&&!platformWorkspace()){
      if(!state.entitlements){
         const pendingHome=subscriptionWorkspace()&&route==='dashboard';
         const placeholder=pendingHome&&window.OrionSubscriptionDashboard?.placeholder?.();
         pageHTML(placeholder?'<div id="osPageBody">'+placeholder+'</div>':
           loading(subscriptionWorkspace()?'Conferindo o acesso à sua empresa…':'Conferindo seu plano e permissões…'));
         $('osConsultantFab')?.classList.add('hidden');
       }
      await refreshPermissions();
      if(!state.mounted||permissionEpoch!==state.epoch||permissionTenant!==state.tenantId||navigationRead!==state.navigationRead)return;
    }
    if(!routePermitted(route))route=fallbackRoute();
    if(route!=='enviar-mensagem'){
      try{const url=new URL(location.href);if(state.route==='enviar-mensagem'||url.searchParams.get('aba')==='enviar-mensagem'){url.searchParams.delete('message_id');history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(route!=='massa'){
      try{const url=new URL(location.href);if(state.route==='massa'||url.searchParams.get('aba')==='massa'){for(const key of ['batch_id','view'])url.searchParams.delete(key);history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(route!=='programadas'){
      try{const url=new URL(location.href);if(state.route==='programadas'||url.searchParams.get('aba')==='programadas'){for(const key of ['schedule_id','run_id','view'])url.searchParams.delete(key);history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(route!=='campanhas'){
      try{const url=new URL(location.href);if(state.route==='campanhas'||url.searchParams.get('aba')==='campanhas'){for(const key of ['campaign_id','group_id','view'])url.searchParams.delete(key);history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(route!=='diagnosticos'){
      try{const url=new URL(location.href);if(state.route==='diagnosticos'||url.searchParams.get('aba')==='diagnosticos'){url.searchParams.delete('device_id');history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(route!=='testar-chatbot'){
      try{const url=new URL(location.href);if(state.route==='testar-chatbot'||url.searchParams.get('aba')==='testar-chatbot'){url.searchParams.delete('device_id');url.searchParams.delete('reply_id');history.replaceState(history.state,'',url.pathname+url.search);}}catch{}
    }
    if(legacyPages.has(route)){
      if(route==='dispositivo'&&!['support','admin'].includes(state.bridge.user?.role)){toast('Seu acesso não inclui a operação do WhatsApp.',true);return;}
      if(route==='member'&&['support','admin'].includes(state.bridge.user?.role)){route='configuracoes';}else{legacyOpened(route);state.bridge.openLegacy?.(route);return;}
    }
    state.pendingRoute=null;clearTimeout(state.agendaSearchTimer);preservePerformancePeriod(route);state.route=route;state.load++;if(route!=='agenda')state.filters.agendaTablet=false;closeNotifications();closeDialog();drawer(false);state.bridge.onRouteChange?.(route);$('page-suite').dataset.osCurrentRoute=route;$('osConsultantFab')?.classList.toggle('hidden',!permitted('consultant.ai')||['agenda','inbox'].includes(route));
    document.querySelectorAll('#appView .content>.page').forEach(p=>p.classList.toggle('hidden',p.id!=='page-suite'));
    selectedNav();document.title=routeLabel(route)+' · Orion';
    try{const url=new URL(location.href);url.searchParams.set('aba',route);url.searchParams.delete('empresa');history.replaceState({...window.OrionSubscriptionSentMessages?.navigationState?.(),...window.OrionSubscriptionDiagnostics?.navigationState?.(),...window.OrionSubscriptionGroupAdd?.navigationState?.(),...window.OrionSubscriptionGroupContacts?.navigationState?.(),...window.OrionSubscriptionDirectMessages?.navigationState?.(),...window.OrionSubscriptionBulkMessages?.navigationState?.(),...window.OrionSubscriptionScheduledMessages?.navigationState?.(),...window.OrionSubscriptionCampaigns?.navigationState?.(),orionSuite:route},'',url.pathname+url.search);}catch{}
    if(!options.keepScroll)window.scrollTo({top:0,behavior:'instant'});
    renderRoute(options);await loadRoute(options);
    if(route==='agenda'&&tenant()){
      const target=new URLSearchParams(location.search).get('agendamento');
      if(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(target||'')){
        const url=new URL(location.href);url.searchParams.delete('agendamento');history.replaceState({},'',url.pathname+url.search);
        await showAppointmentDetail(target);
      }
    }
  }
  function renderRoute(options={}){
    const f=state.filters,can=writable();$('appView').classList.toggle('os-tablet-mode',state.route==='agenda'&&f.agendaTablet);clearInterval(state.clockTimer);
    const readOnly=tenant()&&!can?'<div class="os-notice os-readonly">'+icon('lock')+'<p>Seu acesso a esta empresa é somente para consulta.</p></div>':'';
    let html='';
    if(state.route==='dispositivos')html='<div id="osPageBody"><section id="osSubscriptionDevices"></section></div>';
    if(state.route==='adicionar-ao-grupo')html='<div id="osPageBody"><section id="osSubscriptionGroupAdd"></section></div>';
    if(state.route==='extrator-contatos')html='<div id="osPageBody"><section id="osSubscriptionGroupContacts"></section></div>';
    if(state.route==='enviar-mensagem')html='<div id="osPageBody"><section id="osSubscriptionDirectMessages"></section></div>';
    if(state.route==='massa')html='<div id="osPageBody"><section id="osSubscriptionBulkMessages"></section></div>';
    if(state.route==='programadas')html='<div id="osPageBody"><section id="osSubscriptionScheduledMessages"></section></div>';
    else if(state.route==='campanhas')html='<div id="osPageBody"><section id="osSubscriptionCampaigns"></section></div>';
    if(state.route==='diagnosticos')html='<div id="osPageBody"><section id="osSubscriptionDiagnostics"></section></div>';
    if(state.route==='mensagens-enviadas')html='<div id="osPageBody"><section id="osSubscriptionSentMessages"></section></div>';
    if(state.route==='templates')html='<div id="osPageBody"><section id="osSubscriptionTemplates"></section></div>';
    if(state.route==='chatbot')html='<div id="osPageBody"><section id="osSubscriptionChatbot"></section></div>';
    if(state.route==='testar-chatbot')html='<div id="osPageBody"><section id="osSubscriptionChatbotTest"></section></div>';
    if(state.route==='ajustes-odonto')html=dentalSetupPage();
    if(state.route==='clinica')html=title(options.dentalTeam?'Acessos clínicos':'Prontuários','')+'<div id="osPageBody"><section id="osDentalClinic"></section></div>';
    if(state.route==='pacientes-odonto')html='<div id="osPageBody"><section id="osDentalDirectory"></section></div>';
    if(state.route==='proteses-odonto')html='<div class="os-title-row"><h1>Controle de próteses</h1></div><div id="osPageBody"><section id="osDentalClinic"></section></div>';
    if(state.route==='vendas-odonto')html=title('Vendas','',writable()?button('Nova oportunidade','dental-opportunity-new','','plus'):'')+
      dentalSalesSearchForm()+dentalSalesPeriodForm()+'<div id="osPageBody">'+loading('Carregando oportunidades…')+'</div>';
    if(state.route==='estoque-odonto')html=title('Estoque odontológico','Materiais, saldos e movimentações da clínica.')+'<div id="osPageBody"><section id="osDentalClinic"></section></div>';
    if(state.route==='inbox')html=title('Central de atendimento','Acompanhe suas conversas e atendimentos.')+'<div id="osPageBody"><div id="osInboxHost"></div></div>';
    if(state.route==='dashboard'){html=subscriptionWorkspace()?'<div id="osPageBody"><section id="osSubscriptionDashboard">'+(window.OrionSubscriptionDashboard?.placeholder?.()||loading('Preparando seu painel…'))+'</section></div>':'<div id="osPageBody">'+loading('Carregando o resumo do seu negócio…')+'</div>';}
    if(state.route==='perfil'&&appLayoutEnabled())html='<div id="osPageBody">'+appProfileView()+'</div>';
    if(state.route==='agenda')html=agendaFrame(readOnly);
    if(state.route==='clientes')html='<div class="os-title-row os-client-heading"><div><h1>'+esc(routeLabel('clientes'))+'</h1><p id="osCustomersCount">'+(dedicatedPatients()?'Carregando contatos…':dentalWorkspace()?'Carregando pacientes…':'Carregando clientes…')+'</p></div><div class="os-actions">'+button(dedicatedPatients()?'Novo contato':dentalWorkspace()?'Novo paciente':f.customerStatus==='fixed'?'Novo fixo':'Novo cliente',!dentalWorkspace()&&f.customerStatus==='fixed'?'new-fixed-client':'new-customer','','plus',can?'':'disabled')+'</div></div>'+readOnly+'<div class="os-search os-client-search">'+icon('search')+'<input id="osCustomerSearch" type="search" placeholder="Buscar por nome ou telefone..." aria-label="Buscar clientes por nome, telefone ou e-mail" value="'+esc(f.customerQuery)+'"></div><div class="os-client-filters"><div class="os-pill-tabs" role="tablist" aria-label="Situação dos clientes">'+(dentalWorkspace()?[['active','Ativos'],['inactive','Inativos']]:[['active','Ativos'],['fixed','Fixos'],['at_risk','Em risco']]).map(([v,label])=>'<button type="button" class="os-pill-tab" role="tab" aria-selected="'+(f.customerStatus===v)+'" data-os-customer-status="'+v+'">'+label+'</button>').join('')+'</div>'+(dentalWorkspace()?'':button('Aniversariantes','customer-birthdays',f.customerStatus==='birthday'?'':'os-outline','gift','aria-pressed="'+(f.customerStatus==='birthday')+'"'))+'<button class="os-icon-button" type="button" data-os-action="export-customers" title="Exportar clientes em CSV" aria-label="Exportar clientes em CSV">'+icon('download')+'</button></div>'+(f.customerStatus==='at_risk'?'<div class="os-client-risk"><span>Sem retornar há mais de</span>'+[30,60,90].map(n=>'<button class="os-button os-small '+(f.customerRisk===n?'':'os-outline')+'" type="button" data-os-customer-risk="'+n+'">'+n+' dias</button>').join('')+'</div>':f.customerStatus==='birthday'?'<p class="os-help" style="margin-bottom:16px">Aniversariantes desta semana, de domingo a sábado.</p>':'')+'<div id="osPageBody">'+loading('Carregando clientes…')+'</div>';
    if(state.route==='servicos')html=title(routeLabel('servicos'),dentalWorkspace()?'Cadastre os procedimentos oferecidos pela clínica, com duração e valores.':'Cadastre, ative e organize os serviços oferecidos pela sua empresa.')+readOnly+'<div id="osPageBody">'+loading('Carregando serviços…')+'</div>';
    if(state.route==='funcionarios')html=title(routeLabel('funcionarios'),'Gerencie os profissionais, a equipe e as permissões de acesso ao painel.')+readOnly+'<div class="os-pill-tabs os-staff-tabs" role="tablist" aria-label="Funcionários">'+[['professionals','Profissionais'],['permissions','Equipe & Permissões']].map(([v,l])=>'<button type="button" class="os-pill-tab" role="tab" aria-selected="'+(f.staffTab===v)+'" data-os-staff-tab="'+v+'">'+l+'</button>').join('')+'</div><div id="osPageBody">'+loading('Carregando profissionais…')+'</div>';
    if(state.route==='financeiro')html='<div class="os-title-row os-finance-heading">'+
      '<div class="os-finance-title-line"><h1>Financeiro</h1></div>'+
      '<div class="os-actions os-finance-toolbar os-finance-period-row">'+financeMonthPicker()+button('Hoje','finance-today','os-outline os-small')+button('Novo lançamento','new-expense','os-finance-add','plus',can?'':'disabled')+'</div></div>'+
      readOnly+'<div id="osPageBody">'+loading('Carregando movimentações…')+'</div>';
    if(state.route==='desempenho')html='<div class="os-title-row os-finance-heading os-performance-heading"><div class="os-finance-title-line"><h1>Meu desempenho</h1></div><div class="os-actions">'+button('Atualizar','refresh-performance','os-outline os-small','refresh')+'</div></div>'+performancePeriodFilters()+'<div id="osPageBody">'+loading('Calculando o desempenho…')+'</div>';
    if(state.route==='marketing')html=title('Marketing IA','Mensagens automáticas, recuperação de clientes e campanhas em um só lugar')+readOnly+'<div id="osPageBody">'+loading('Carregando campanhas…')+'</div>';
    if(state.route==='site')html=title('Meu Site','Acompanhe os acessos e agendamentos da sua página.')+readOnly+'<div id="osPageBody">'+loading('Carregando sua página…')+'</div>';
    if(state.route==='fidelizacao')html=title('Fidelização','Programa de pontos, cupons automáticos e ações de retenção para trazer o cliente de volta.')+readOnly+'<div id="osPageBody">'+loading('Carregando o programa de fidelidade…')+'</div>';
    if(state.route==='consultor')html=title('Consultor IA','Encontre oportunidades a partir dos dados do seu negócio.')+'<div id="osPageBody">'+loading('Analisando os dados disponíveis…')+'</div>';
    if(state.route==='plano')html='<div id="osPageBody">'+loading('Consultando os planos do Orion…')+'</div>';
    if(state.route==='configuracoes'){
      const hours=appLayoutEnabled()&&state.filters.settingsTab==='hours';
      const heading=hours?title('Horários','Expediente, pausas e bloqueios dos profissionais.'):
        dentalWorkspace()?title('Ajustes da clínica',''):title('Configurações','Gerencie sua empresa, horários e preferências.');
      html=heading+'<div id="osPageBody">'+loading(hours?'Carregando horários…':'Carregando configurações…')+'</div>';
    }
    if(['ajuda','aquecimento','evitar-banimento','resolver-banimento'].includes(state.route))html='<div id="osPageBody"></div>';
    if(state.route==='novidades')html=title('Novidades','O que você já pode fazer no seu painel Orion.')+'<div id="osPageBody"></div>';
    pageHTML(html);
  }
  function periodFilters(){return '<form id="osPeriodForm" class="os-filter-row"><div class="os-field"><label for="osPeriodStart">De</label><input id="osPeriodStart" name="start_date" type="date" value="'+esc(state.filters.periodStart)+'" required></div><div class="os-field"><label for="osPeriodEnd">Até</label><input id="osPeriodEnd" name="end_date" type="date" value="'+esc(state.filters.periodEnd)+'" required></div>'+button('Este mês','period-month','os-outline','calendar')+'<button type="submit" class="os-button">Aplicar</button></form>';}
  function financeMonthPicker(){const day=state.filters.periodStart||dateKey(),label=new Intl.DateTimeFormat('pt-BR',{timeZone:'UTC',month:'long',year:'numeric'}).format(dayDate(day));return '<div class="os-month-picker">'+button('','finance-prev','os-ghost os-small','left','aria-label="Mês anterior"')+'<label class="os-month-label"><span>'+esc(label)+'</span><input id="osFinanceMonth" type="month" value="'+esc(day.slice(0,7))+'" aria-label="Escolher mês do financeiro"></label>'+button('','finance-next','os-ghost os-small','chevron','aria-label="Próximo mês"')+'</div>';}
  function setMonth(value){const d=dayDate(value+'-01');d.setUTCMonth(d.getUTCMonth()+1,0);state.filters.periodStart=value+'-01';state.filters.periodEnd=d.toISOString().slice(0,10);state.filters.financeOffset=0;}
  function setPerformancePeriod(value){if(!['today','week','month','30','90'].includes(value))return;const today=dateKey();state.filters.performancePoint='';state.filters.performancePeriod=value;state.filters.performanceDay=today;if(value==='month'){resetPeriod();return;}state.filters.periodStart=shiftDay(today,value==='today'?0:value==='week'?-6:value==='30'?-29:-89);state.filters.periodEnd=today;}
  function performanceProfessionalOptions(people=rows(state.data.performance)){
    return '<option value="">Toda a equipe</option>'+people.map(p=>'<option value="'+esc(p.id)+'"'+(state.filters.performanceProfessional===p.id?' selected':'')+'>'+esc(p.name)+(!p.active?' · Inativo':'')+'</option>').join('');
  }
  function performancePeriodFilters(){const f=state.filters;return '<div class="os-filter-row os-performance-periods"><select id="osPerformancePeriod" aria-label="Período do relatório">'+[['today','Hoje'],['week','Últimos 7 dias'],['month','Este mês'],['30','Últimos 30 dias'],['90','Últimos 90 dias'],['custom','Personalizado']].map(([v,l])=>'<option value="'+v+'"'+(f…136494 tokens truncated…tos.');const payload={name:f.name.trim(),description:f.description?.trim()||null,service_ids:ids,duration_override_min:minutes,price_cents:cents(f.price,{max:100000000}),active:form.elements.active.checked,featured:form.elements.featured.checked,sort_order:number(r?.sort_order),color:r?.color||'#8b5cf6',image_url:f.image_url?.trim()||null};await saveForm(form,()=>request(mgmt('/service-packages'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.version}:{idempotency_key:d.key})}),r?'Combo atualizado.':'Combo criado.');}
      if(kind==='professional'){
        const p=d.record,selected=new FormData(form).getAll('service_ids'),
          advanced=permitted('commissions.advanced'),mode=advanced?form.elements.commission_mode.value:null,
          baseRate=advanced?sharedFields().parseDecimal(form.elements.commission_percent.value,{required:true,min:0,max:100,scale:2}):null;
        let professionalPhone;
        try{professionalPhone=professionalPhoneValue(f.phone_e164);}
        catch(error){form.elements.phone_e164.setAttribute('aria-invalid','true');form.elements.phone_e164.focus();throw error;}
        if(advanced&&(!Number.isFinite(baseRate)||baseRate<0||baseRate>100||Math.abs(Math.round(baseRate*100)-baseRate*100)>1e-7))
          throw Error('Informe um percentual padrão válido de 0 a 100, com até duas casas decimais.');
        const payload={
          name:f.name.trim(),active:form.elements.active.checked,
          specialty:f.specialty?.trim()||null,phone_e164:professionalPhone,color:f.color||'#31ad52',
          image_url:f.image_url?.trim()||null,
          ...(advanced?{commission_mode:mode,commission_percent:baseRate}:{}),
          accepts_appointments:form.elements.accepts_appointments.checked,
          service_ids:selected,commission_rules:[]
        };
        if(mode==='per_service'){
          payload.commission_rules=selected.map(serviceId=>{
            const kind=form.elements['commission_kind_'+serviceId].value,
              value=String(form.elements['commission_value_'+serviceId].value).trim();
            if(kind==='fixed'){
              const fixed=cents(value,{max:100000000});
              if(fixed>100000000)throw Error('A comissão fixa excede o valor máximo por serviço.');
              return {service_id:serviceId,commission_kind:'fixed',commission_fixed_cents:fixed};
            }
            const percent=sharedFields().parseDecimal(value,{required:true,min:0,max:100,scale:2});
            return {service_id:serviceId,commission_kind:'percent',commission_percent:percent};
          });
        }else{
          payload.commission_rules=(d.commissionRules||[]).filter(rule=>selected.includes(rule.service_id)).map(rule=>
            rule.commission_kind==='fixed'
              ?{service_id:rule.service_id,commission_kind:'fixed',commission_fixed_cents:number(rule.commission_fixed_cents)}
              :{service_id:rule.service_id,commission_kind:'percent',commission_percent:number(rule.commission_percent)});
        }
        if(!advanced)delete payload.commission_rules;
        if(payload.active&&(!p||p.active===false)&&atProfessionalLimit())throw Error('Você atingiu o limite de profissionais do seu plano.');
        if(p?.id)payload.expected_version=number(p.commission_config_version)||1;
        else payload.idempotency_key=d.key;
        let invitationResult=null;
        const inviteEmail=(f.access_email||'').trim();
        const sendInvitation=form.elements.send_invitation?.checked===true;
        if(sendInvitation&&!inviteEmail)throw Error('Informe o e-mail para enviar o convite.');
        await saveForm(form,async c=>{
          const id=p?.id||d.savedId;
          const result=await request(mgmt('/professionals/'+(id?encodeURIComponent(id)+'/':'')+'configure'),
            id?'PUT':'POST',payload);
          if(!stillAccount(c))return;
          d.savedId=result?.id||id;
          if(!d.savedId)throw Error('Não foi possível confirmar o cadastro do profissional.');
          if(state.route==='funcionarios')state.filters.staffStatus=payload.active?'active':'inactive';
          state.data.commissionRules=null;
          state.data.professionalServices=null;
          if(stillAccount(c)){
            try{
              if(sendInvitation){
                invitationResult=await request(mgmt('/team/professionals/'+encodeURIComponent(d.savedId)+'/invite'),'POST',
                   {email:inviteEmail,idempotency_key:d.inviteKey||(d.inviteKey=crypto.randomUUID())});
              }else if(form.elements.access_email&&!form.elements.access_email.disabled&&(inviteEmail||null)!==(access?.email||null)){
                await request(mgmt('/team/professionals/'+encodeURIComponent(d.savedId)+'/email'),'PUT',
                   {email:inviteEmail||null});
              }
            }catch(error){throw Error('Profissional salvo, mas o acesso não foi atualizado: '+error.message);}
          }
        },p?'Profissional atualizado.':'Profissional cadastrado.');
        if(invitationResult&&state.mounted)showInvitationResult(invitationResult);
      }
      if(kind==='hours-start-policy'){await submitHoursStartPolicy(form,d);return;}
      if(['hours','hours-edit','pause','pause-edit'].includes(kind)){await submitHoursEditor(form,kind,f,d);return;}
      if(kind==='fixed-client'){
        const payload=fixedDraft(d,f),signature=JSON.stringify(payload);
        if(!d.preview||d.previewKey!==signature){
          const c=context();markBusy(form,true);
          try{
            const endpoint=d.editSeriesId?'/appointments/series/'+encodeURIComponent(d.editSeriesId)+'/preview':'/appointments/series/preview';
            const preview=await request(apiPath(endpoint),'POST',payload);
            if(stillAccount(c)&&state.dialog===d){d.preview=preview;d.previewKey=signature;renderFixedPreview(d,preview);}
          }catch(error){if(stillAccount(c)&&state.dialog===d)dialogError(error);}
          finally{if(stillAccount(c)&&state.dialog===d)markBusy(form,false);}
          return;
        }
        if(d.preview.conflict_count)throw Error('Existem conflitos nas datas. Ajuste os horários e verifique novamente.');
        await saveForm(form,async c=>{
          const endpoint=d.editSeriesId?'/appointments/series/'+encodeURIComponent(d.editSeriesId)+'/replace':'/appointments/series';
          const result=await request(apiPath(endpoint),'POST',{...payload,idempotency_key:requestKey(form,payload)});
          if(current(c)&&!d.editSeriesId){
            state.filters.agendaMode='list';
            state.filters.agendaDay=payload.first_date;
            state.filters.agendaOffset=0;
          }
          return result;
        },d.editSeriesId?'Cliente fixo atualizado. Os novos horários estão na Agenda.':'Cliente fixo cadastrado. Todas as visitas foram reservadas na Agenda.');
        return;
      }
      if(kind==='appointment'){
        if(d.manualOperation||form.elements.booking_mode.value!=='regular'){await submitManualAppointment(form,d,f);return;}
        if(d.selectionPending)throw Error('Aguarde a conferência dos serviços e horários.');
        if(d.selectionError)throw Error(d.selectionError);
        const values=d.holdForm||f,customerPhone=customerPhoneValue(values.customer_phone);if(!/^(?:\+55[1-9][0-9]{9,10}|\+5959[0-9]{8})$/.test(customerPhone))throw Error('Para agendar, informe WhatsApp com DDD brasileiro ou celular paraguaio +595.');if(!d.serviceIds.length)throw Error('Selecione ao menos um serviço.');if(!d.holdForm&&!d.slots.includes(values.start_at))throw Error('Selecione um horário disponível.');
        await saveForm(form,async c=>{
          let hold=d.holdId?d.holdRecord:null;
          if(!hold){
            const selected=d.customers.find(customer=>customer.id===d.customerId);
            if(selected&&customerPhoneValue(selected.phone)!==customerPhone){
              const path=mgmt('/customers/'+encodeURIComponent(selected.id));let updated;
              try{updated=await request(path,'PATCH',{phone:customerPhone,expected_version:selected.version});}
              catch(error){
                if(error.status>=400&&error.status<500&&error.status!==409)throw error;
                try{updated=await request(path);}catch{throw error;}
                if(updated?.id!==selected.id||customerPhoneValue(updated.phone)!==customerPhone)throw error;
              }
              if(!stillAccount(c))return;Object.assign(selected,updated);
            }
            if(!d.holdForm){d.holdForm={...values};d.holdPayload={professional_id:values.professional_id,service_ids:[...(d.engineServiceIds||d.serviceIds)],customer_name:values.customer_name.trim(),customer_phone:customerPhone,start_at:values.start_at,idempotency_key:d.key};}
            try{hold=await request(apiPath('/appointments/hold'),'POST',d.holdPayload);d.holdId=hold.id;d.holdRecord=hold;}
            catch(error){if(error.status>=400&&error.status<500){d.holdForm=null;d.holdPayload=null;d.key=crypto.randomUUID();}throw error;}
          }
          if(!stillAccount(c))return;if(typeof hold.updated_at!=='string')throw Error('Não foi possível confirmar a versão da reserva. Consulte os detalhes na Agenda antes de continuar.');if(!d.confirmPayload)d.confirmPayload={expected_updated_at:hold.updated_at,idempotency_key:crypto.randomUUID()};const confirmed=await request(apiPath('/appointments/'+encodeURIComponent(hold.id)+'/confirm'),'POST',d.confirmPayload);if(confirmed.status!=='confirmed')throw Error('A reserva ainda não foi confirmada. Confira a agenda antes de tentar novamente.');state.filters.agendaDay=values.day;
        },'Agendamento confirmado. O protocolo está disponível na agenda.');
        if(state.dialog===d&&d.holdForm)freezePendingBooking(form,d);else if(state.dialog===d&&$('osPendingBooking')){form.querySelectorAll('fieldset').forEach(fieldset=>fieldset.disabled=false);$('osPendingBooking').remove();syncBookingMode();}
      }
      if(kind==='block'){
        const dayOff=f.block_type==='days',repeating=form.elements.repeat.checked&&!dayOff,start=dayOff?f.from_day:f.day,end=dayOff?f.to_day:repeating?f.until_date:f.day;
        if(!start||!end||end<start)throw Error('Confira as datas do bloqueio.');if((dayDate(end)-dayDate(start))/86400000>365)throw Error('O bloqueio pode abranger até 366 dias.');if(!dayOff&&f.start_time>=f.end_time)throw Error('O horário final deve ser posterior ao inicial.');
        const weekdays=new FormData(form).getAll('weekdays').map(Number),frequency=repeating?f.frequency:'daily';if(frequency==='weekdays'&&!weekdays.length)throw Error('Escolha pelo menos um dia da semana.');
        const payload={professional_id:f.professional_id,start_date:start,until_date:end,frequency,weekdays:frequency==='weekdays'?weekdays:[],kind:dayOff?'day_off':'hours',start_time:dayOff?null:f.start_time,end_time:dayOff?null:f.end_time,reason:f.reason?.trim()||null,idempotency_key:d.key};
        await saveForm(form,()=>request(mgmt('/block-series'),'POST',payload),repeating?'Bloqueios recorrentes registrados.':'Período bloqueado.');
      }
      if(kind==='settlement'){const preview=d.preview;if(!preview)return;await saveForm(form,()=>request(mgmt('/finance/settlements'),'POST',{professional_id:d.professionalId,expected_snapshot:preview.snapshot_hash,payment_method:f.payment_method,occurred_at:f.day?localISO(f.day,'12:00'):null,notes:f.notes?.trim()||null,idempotency_key:d.key}),'Quitação registrada no financeiro.');return;}
      if(kind==='staff-finance'){const r=d.record,payload={kind:'expense',entry_type:d.entryType,description:f.description.trim(),amount_cents:cents(f.amount,{min:1}),category:d.entryType==='advance'?'Vale':'Comissão',payment_method:f.payment_method,status:r?.status||'paid',professional_id:f.professional_id,occurred_at:localISO(f.day,'12:00'),notes:f.notes?.trim()||null};if(!payload.amount_cents)throw Error('Informe um valor maior que zero.');await saveForm(form,()=>request(mgmt('/finance'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.version}:{idempotency_key:d.key})}),r?'Lançamento atualizado.':d.entryType==='advance'?'Vale registrado.':'Pagamento registrado.');}
      if(kind==='finance'){
        const r=d.record,description=f.description.trim(),payload={kind:f.kind,description:r&&description===financeEntryTitle(r)?r.description:description,amount_cents:cents(f.amount,{min:1}),category:f.category?.trim()||'Geral',payment_method:f.payment_method,status:f.status,occurred_at:financeOccurredAt(r,f.day),notes:f.notes?.trim()||null};
        if(!payload.amount_cents)throw Error('O valor do lançamento deve ser maior que zero.');await saveForm(form,()=>request(mgmt('/finance'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.version}:{idempotency_key:d.key})}),r?'Lançamento atualizado.':'Lançamento registrado.');
      }
      if(kind==='delete-series')await saveForm(form,()=>request(mgmt('/block-series/'+encodeURIComponent(d.series.id)+'?'+query({scope:f.scope,occurrence_date:d.occurrenceDate})),'DELETE'),'Bloqueio removido.');
      if(kind==='campaign'){const r=d.record,payload={name:f.name.trim(),message:f.message.trim(),audience:f.audience,scheduled_at:f.scheduled_day?localISO(f.scheduled_day,f.scheduled_time||'09:00'):null};await saveForm(form,()=>request(mgmt('/marketing'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.version}:{idempotency_key:d.key})}),'Rascunho salvo.');}
      if(kind==='loyalty-inline'){const r=rows(state.data.loyalty?.rules).find(r=>r.id===form.dataset.ruleId),type=f.reward_type,rewardValue=type==='fixed'?cents(f.reward_value,{min:1}):type==='percent'?sharedFields().parseDecimal(f.reward_value,{required:true,min:0.01,max:100,scale:2}):null,service=(state.data.services||[]).find(s=>s.id===f.reward_service_id),description=type==='percent'?rewardValue+'% de desconto':type==='fixed'?money(rewardValue)+' de desconto':service?.name+' grátis';const payload={name:r?.name||('Fidelidade '+(tenant()?.name||'Orion')).slice(0,120),earning_mode:'both',points_per_visit:Number(f.points_per_visit),points_per_step:Number(f.points_per_step),spend_step_cents:cents(f.spend_step,{min:1}),reward_points:Number(f.reward_points),reward_type:type,reward_percent:type==='percent'?rewardValue:null,reward_value_cents:type==='fixed'?rewardValue:null,reward_service_id:type==='free_service'?f.reward_service_id:null,reward_valid_days:Number(f.reward_valid_days),reward_description:description,active:form.elements.active.checked};await saveForm(form,()=>request(mgmt('/loyalty/rules'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:number(form.dataset.version)}:{idempotency_key:form.dataset.key})}),'Programa de fidelidade salvo.');}
      if(kind==='loyalty'){const r=d.record,payload={name:f.name.trim(),earning_mode:f.earning_mode,points_per_visit:Number(f.points_per_visit),spend_step_cents:cents(f.spend_step,{min:1}),points_per_step:Number(f.points_per_step),reward_points:Number(f.reward_points),reward_description:f.reward_description.trim(),active:form.elements.active.checked};await saveForm(form,()=>request(mgmt('/loyalty/rules'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.version}:{idempotency_key:d.key})}),'Regra de fidelidade salva.');}
      if(kind==='loyalty-points'){const payload={customer_id:f.customer_id,points_delta:Number(f.points)*(f.operation==='redeem'?-1:1),reason:f.reason.trim(),rule_id:f.rule_id||null,idempotency_key:d.key};await saveForm(form,()=>request(mgmt('/loyalty/transactions'),'POST',payload),'Pontos registrados.');}
      if(kind==='goal'){
        const r=d.record;if(r?.can_edit===false)return;
        if(!d.monthly&&f.start_date>f.end_date)throw Error('A data final deve ser igual ou posterior à inicial.');
        const payload={scope:f.scope,professional_id:f.scope==='company'?null:f.professional_id,metric:f.metric,target_value:f.metric==='revenue_cents'?cents(f.target,{min:1}):sharedFields().parseDecimal(f.target,{required:true,min:1,max:1000000000,scale:0}),active:form.elements.active.checked,recurrence:d.monthly?'monthly':'none',...(!d.monthly?{start_date:f.start_date,end_date:f.end_date}:{})};
        if(payload.scope==='professional'&&!payload.professional_id)throw Error('Selecione um profissional.');
        if(!Number.isSafeInteger(payload.target_value)||payload.target_value<=0||payload.target_value>1000000000)throw Error('Informe uma meta válida maior que zero.');
        await saveForm(form,()=>request(mgmt('/goals'+(r?'/'+encodeURIComponent(r.id):'')),r?'PATCH':'POST',{...payload,...(r?{expected_version:r.template_version||r.version}:{idempotency_key:requestKey(form,payload)})}),'Meta salva.');
      }
      if(kind==='site'){if(!siteCanEdit())return;const payload={title:f.title.trim(),description:f.description?.trim()||'',slug:normalizeSiteSlug(f.slug)||null,theme_color:f.theme_color,logo_url:f.logo_url?.trim()||null,cover_url:f.cover_url?.trim()||null,address:f.address?.trim()||null,address_number:form.elements.address_no_number.checked?null:f.address_number?.trim()||null,address_no_number:form.elements.address_no_number.checked,address_complement:f.address_complement?.trim()||null,neighborhood:f.neighborhood?.trim()||null,city:f.city?.trim()||null,state:f.state?.trim().toUpperCase()||null,postal_code:f.postal_code?.trim()||null,contact_phone:phone(f.contact_phone)||null,whatsapp_phone:phone(f.whatsapp_phone)||null,instagram_url:socialURL(f.instagram_url,'Instagram'),facebook_url:socialURL(f.facebook_url,'Facebook'),tiktok_url:socialURL(f.tiktok_url,'TikTok'),maps_url:f.maps_url?.trim()||null,external_url:f.external_url?.trim()||null,show_prices:form.elements.show_prices.checked,amenity_wifi:form.elements.amenity_wifi.checked,amenity_parking:form.elements.amenity_parking.checked,amenity_accessibility:form.elements.amenity_accessibility.checked,amenity_children:form.elements.amenity_children.checked,expected_version:number(form.dataset.version)};await saveForm(form,async c=>{try{return await request(mgmt('/site'),'PUT',payload);}catch(error){if(current(c)&&error.status===409&&form.isConnected)queueSiteSlugCheck(form.elements.slug,0);throw error;}},'Configurações do site salvas no rascunho.');}
      if(kind==='site-publication')await saveSitePublication(form,d);
      if(kind==='confirm')await saveForm(form,d.operation,d.success);
    }catch(ex){const target=form.querySelector('.os-form-message');if(target)target.textContent=ex.message;else toast(ex.message,true);}
  }
  async function action(name,element){
    if(name==='permissions-retry'){state.entitlements=null;await open(state.route,{force:true});return;}
    if(name==='managed-whatsapp'&&!permitted('conversations.manual'))return;
    if(name==='managed-whatsapp'){if(state.busy)return;state.filters.settingsTab='whatsapp';await open('configuracoes');return;}
    if(['image-pick','image-remove'].includes(name)&&element.closest('#osSiteForm')&&!siteCanEdit())return;
    if(name==='image-pick'){element.closest('.os-image-upload')?.querySelector('.os-image-file')?.click();return;}
    if(name==='image-remove'){
      const box=element.closest('.os-image-upload'),field=box?.querySelector('input[type=hidden]'),thumb=box?.querySelector('.os-image-thumb');
      if(!box||!field||state.imageUploading||!writable())return;
      field.value='';thumb.innerHTML=icon('upload');element.disabled=true;
      box.querySelector('.os-image-status').textContent="Imagem retirada. Salve o formulário para confirmar.";
      if(box.closest('#osSiteForm'))box.closest('form').dataset.dirty='true';
      return;
    }
    if(name==='app-shortcuts-default'){
      if(!appLayoutEligible()||state.route!=='configuracoes'||state.filters.settingsTab!=='appearance')return;
      state.appShortcutDraft=appDefaultShortcuts().filter(tab=>appTabAllowed(tab));
      while(state.appShortcutDraft.length<4)state.appShortcutDraft.push('');
      renderSettings();$('osAppShortcut0')?.focus({preventScroll:true});return;
    }
    if(name==='app-shortcuts-save'){saveAppShortcuts();return;}
    if(name==='menu'){drawer(true);return;}if(name==='close-menu'){drawer(false);return;}if(name==='collapse'){if(matchMedia('(max-width:767px)').matches){drawer(false);return;}const app=$('appView');app.classList.toggle('os-rail-collapsed');element.innerHTML=icon(app.classList.contains('os-rail-collapsed')?'chevron':'left');element.setAttribute('aria-label',app.classList.contains('os-rail-collapsed')?'Expandir menu':'Recolher menu');return;}
    if(name==='notifications'){if(state.busy)return;const show=!state.notificationsOpen;closeNotifications();if(show){state.notificationsOpen=true;$('osNotificationsPanel').classList.remove('hidden');$('osNotificationsToggle').setAttribute('aria-expanded','true');await loadNotifications();$('osNotificationsPanel').focus();}return;}
    if(name==='close-notifications'){closeNotifications(true);return;}
    if(name==='refresh-notifications'){await loadNotifications();return;}
    if(name==='read-notifications'){await markNotificationsRead(rows(state.data.notifications).filter(item=>!item.read_at).map(item=>item.id));return;}
    if(name==='theme-menu'){$('osThemeMenu').classList.toggle('hidden');$('osThemeToggle').setAttribute('aria-expanded',String(!$('osThemeMenu').classList.contains('hidden')));return;}
    if(name==='close-dialog'){closeDialog();return;}if(name==='logout'){state.bridge.logout?state.bridge.logout():$('logout').click();return;}
    if(state.busy)return;
    const d=state.dialog,f=state.filters;
    switch(name){
      case 'finance-receivables':f.financeReceivables=!f.financeReceivables;f.financeOffset=0;await open('financeiro',{keepScroll:true});break;
      case 'receipt-settings':f.settingsTab='finance';await open('configuracoes');break;
      case 'receipt-retry':if(d?.receiptId)await showServiceReceipt(d.receiptId,{appointmentId:d.returnAppointment});break;
      case 'receipt-hold':showReceiptDecision('hold');break;
      case 'receipt-resume':showReceiptDecision('resume');break;
      case 'receipt-reopen':showReceiptDecision('reopen');break;
      case 'appointment-receipts-retry':if(d?.record)await loadAppointmentReceipts(d);break;
      case 'appointment-receipt':await showServiceReceipt(element.dataset.receiptId,{appointmentId:d?.record?.id});break;
      case 'business-setup':state.bridge.openSetup?.({force:true});break;
      case 'business-context-retry':state.bridge.retryBusinessContext?.();break;
      case 'reload':await open(state.route,{keepScroll:true});break;
      case 'site-requests-refresh':if(canManageSiteRequests()&&state.filters.settingsTab==='site-requests')await open('configuracoes',{keepScroll:true});break;
      case 'site-requests-prev':case 'site-requests-next':{if(!canManageSiteRequests()||state.filters.settingsTab!=='site-requests'||!state.data.siteRequests)return;const data=state.data.siteRequests,limit=number(data.limit)||25,offset=number(data.offset),next=Math.max(0,offset+(name==='site-requests-prev'?-limit:limit));if(name==='site-requests-next'&&next>=number(data.total))return;f.siteRequestOffset=next;await open('configuracoes',{keepScroll:true});break;}
      case 'site-request-status':await updateSiteRequestStatus(element);break;
      case 'new-dental-patient':if(writable()&&routePermitted('pacientes-odonto'))await open('pacientes-odonto',{newDentalPatient:true});break;
      case 'new-customer':if(state.route!=='dashboard'||!homePreviewEnabled()||writable()&&routePermitted('clientes'))showCustomer();break;
      case 'customer-birth-clear':{if(d?.type!=='customer'||!writable())return;const input=$('osDialog')?.querySelector('[name=birth_date]');if(input){input.value='';markCustomerBirthChanged(input);input.focus();}break;}
      case 'new-service':await showService();break;
      case 'toggle-service-models':{const box=$('osServiceModels');box.classList.toggle('hidden');element.setAttribute('aria-expanded',String(!box.classList.contains('hidden')));break;}
      case 'new-package':await showPackage();break;
      case 'delete-package':{if(!d?.record||!writable())return;const r=d.record,path=mgmt('/service-packages/'+encodeURIComponent(r.id));confirmMutation('Excluir combo','O combo '+r.name+' deixará de aparecer no catálogo. Os atendimentos anteriores serão preservados.',()=>request(path,'DELETE',{expected_version:r.version}),'Combo excluído.');break;}
      case 'finance-filters':f.financeFiltersOpen=!f.financeFiltersOpen;$('osFinanceFilters')?.classList.toggle('hidden',!f.financeFiltersOpen);element.setAttribute('aria-expanded',String(f.financeFiltersOpen));break;
      case 'finance-clear-filters':f.financeKind='';f.financeStatus='';f.financeOffset=0;f.financeFiltersOpen=true;await open('financeiro',{keepScroll:true});break;
      case 'finance-prev':case 'finance-next':{const month=dayDate(f.periodStart);month.setUTCMonth(month.getUTCMonth()+(name==='finance-prev'?-1:1));setMonth(month.toISOString().slice(0,7));await open('financeiro',{keepScroll:true});break;}
      case 'finance-today':resetPeriod();f.financeOffset=0;await open('financeiro',{keepScroll:true});break;
      case 'staff-finance-prev':case 'staff-finance-next':{const key=element.dataset.entryType==='advance'?'advanceOffset':'paymentOffset';f[key]=Math.max(0,(f[key]||0)+(name==='staff-finance-prev'?-100:100));await open('financeiro',{keepScroll:true});break;}
      case 'finance-page-prev':f.financeOffset=Math.max(0,(f.financeOffset||0)-100);await open('financeiro',{keepScroll:true});break;
      case 'finance-page-next':f.financeOffset=(f.financeOffset||0)+100;await open('financeiro',{keepScroll:true});break;
      case 'settlement-preview':await showSettlement(element.dataset.professional);break;
      case 'refresh-settlement':if(d?.professionalId)await showSettlement(d.professionalId);break;
      case 'settlement-prev':case 'settlement-next':f.settlementOffset=Math.max(0,(f.settlementOffset||0)+(name==='settlement-prev'?-50:50));await open('financeiro',{keepScroll:true});break;
      case 'new-advance':await showStaffFinance('advance',element.dataset.professional);break;
      case 'new-commission-payment':await showStaffFinance('commission_payment',element.dataset.professional);break;
      case 'print-finance':window.print();break;
      case 'refresh-performance':await open('desempenho',{keepScroll:true});break;
      case 'custom-period':showDialog('period','Escolher período',periodFilters()+button('Fechar','close-dialog','os-ghost'));break;
      case 'new-professional':await showProfessional();break;
      case 'new-invitation':showInvitation();break;
      case 'pro-access-suspend':case 'pro-access-reactivate':case 'pro-access-revoke':{
        if(!state.data.team?.can_manage)return;
        const prof=rows(state.data.professionals).find(p=>p.id===element.dataset.professional);
        if(!prof)return;
        const link=proAccessState(prof).link;
        if(!link?.account_id)return;
        const action=name==='pro-access-suspend'?'suspend':name==='pro-access-reactivate'?'reactivate':'revoke';
        confirmMutation(action==='revoke'?'Revogar acesso de '+prof.name:action==='suspend'?'Suspender acesso de '+prof.name:'Reativar acesso de '+prof.name,
          action==='revoke'?'Esta conta deixará de estar vinculada ao profissional. O histórico será preservado.':'A alteração vale imediatamente para a área profissional.',
          ()=>request(mgmt('/team/professionals/'+encodeURIComponent(prof.id)+'/access'),'PATCH',
            {action,expected_updated_at:link.updated_at}),'Acesso profissional atualizado.');
        break;
      }
      case 'pro-block-permission':{
        if(!state.data.team?.can_manage)return;
        const prof=rows(state.data.professionals).find(p=>p.id===element.dataset.professional);
        if(!prof)return;
        const link=proAccessState(prof).link;
        if(!link?.account_id)return;
        const grant=!link.can_manage_owner_blocks;
        confirmMutation(grant?'Permitir remover bloqueios da empresa':'Proteger bloqueios da empresa',
          grant?'O profissional poderá remover bloqueios criados pela empresa em sua própria agenda. Não terá acesso aos horários de colegas.':
            'O profissional ficará limitado aos bloqueios que ele mesmo criou.',
          ()=>request(mgmt('/team/professionals/'+encodeURIComponent(prof.id)+'/block-permissions'),
            'PATCH',{can_manage_owner_blocks:grant,expected_updated_at:link.updated_at}),
            'Permissão de bloqueios atualizada.');
        break;
      }
      case 'copy-invitation':if(d?.link){await navigator.clipboard.writeText(d.link);toast('Link do convite copiado.');}break;
      case 'approve-invitation':case 'revoke-invitation':{if(!state.data.team?.can_manage)return;const invitation=rows(state.data.team.invitations).find(i=>i.id===element.dataset.invitation);if(!invitation)return;const approval=name==='approve-invitation',path=mgmt('/team/invitations/'+encodeURIComponent(invitation.id));confirmMutation(approval?'Aprovar acesso':'Revogar convite',approval?'Autorizar o acesso de '+(invitation.claimed_by_name||invitation.email||'quem aceitou o convite')+' como '+(teamRoles[invitation.role]||invitation.role)+'.':'Este convite não poderá mais ser usado para entrar na equipe.',()=>request(path,'PATCH',{action:approval?'approve':'revoke',expected_version:invitation.version}),approval?'Acesso aprovado.':'Convite revogado.');break;}
      case 'new-appointment':await showAppointment();break;
      case 'new-fixed-client':await showAppointment({bookingMode:'fixed'});break;
      case 'new-block':await showBlock();break;
      case 'home-chart-point':selectHomeChartPoint(element);break;
      case 'home-period':{
        if(homePreviewEnabled()&&state.route==='dashboard'&&['today','yesterday','week'].includes(element.dataset.osPeriod)){state.filters.homePeriod=element.dataset.osPeriod;await loadHomePreview(context());document.querySelector('[data-os-action=home-period][data-os-period="'+state.filters.homePeriod+'"]')?.focus({preventScroll:true});}break;
      }
      case 'dashboard-finance-refresh':if(state.route==='dashboard')await loadDashboardFinance(context(),dateKey());break;
      case 'dashboard-finance-full':f.financeTab='entries';await open('financeiro');break;
      case 'dashboard-goals-full':state.filters.performanceTab='goals';await open('desempenho');break;
      case 'dashboard-agenda-refresh':if(state.route==='dashboard')await open('dashboard',{keepScroll:true});break;
      case 'dashboard-agenda-full':{
        f.agendaDay=dateKey();f.agendaMode='list';f.agendaPeriod='day';f.agendaOffset=0;
        f.agendaQuery='';f.agendaProfessional='';f.agendaStatus='all';
        await open('agenda');break;
      }
      case 'agenda-open':await open('agenda');break;
      case 'agenda-reload':await loadAgenda(context());if(agendaPeriod()==='day')void loadGoogle();break;
      case 'agenda-range-list':
        f.agendaMode=f.agendaMode==='list'?'calendar':'list';
        f.agendaRangeList=f.agendaMode!=='calendar';
        f.agendaFocusOffset=0;
        renderAgenda();
        if(!f.agendaRangeList)await loadAgendaFocus(context());
        break;
      case 'agenda-open-selected':
        f.agendaDay=agendaSelectedDay();f.agendaPeriod='day';f.agendaMode='list';f.agendaOffset=0;
        await open('agenda',{keepScroll:true});break;
      case 'agenda-focus-retry':await loadAgendaFocus(context());break;
      case 'agenda-focus-page-prev':case 'agenda-focus-page-next':{
        const r=state.data.agendaFocusResult;if(!r)return;
        const amount=number(r.limit)||80;
        f.agendaFocusOffset=Math.max(0,number(r.offset)+(name==='agenda-focus-page-next'?amount:-amount));
        if(f.agendaFocusOffset>=number(r.total))return;
        await loadAgendaFocus(context());break;
      }
      case 'agenda-clear':f.agendaProfessional='';f.agendaStatus='all';f.agendaQuery='';f.agendaOffset=0;f.agendaFocusOffset=0;await open('agenda',{keepScroll:true});break;
      case 'agenda-page-prev':case 'agenda-page-next':{const result=state.data.agendaResult;if(!result||state.data.agendaError)return;const next=Math.max(0,f.agendaOffset+(name==='agenda-page-prev'?-1:1)*(number(result.limit)||50));if(next>=number(result.total))return;f.agendaOffset=next;await loadAgenda(context(),{catalogue:false});$('osAgendaCount')?.focus();break;}
      case 'agenda-google':await showGoogle();break;
      case 'dental-opportunity-new':{
        if(state.route!=='vendas-odonto'||!dentalSettingsAllowed()||!writable())break;
        const content=field('Título','title','text','','required maxlength="40"')+
          '<div class="os-field"><label for="osSaleDescription">Descrição (opcional)</label>'+
          '<textarea id="osSaleDescription" name="description" maxlength="300" rows="3"></textarea></div>'+
          '<section class="os-sales-patient-picker" aria-labelledby="osSalesPatientLabel">'+
          '<h3 id="osSalesPatientLabel">Paciente (opcional)</h3><p class="os-help">Busque por nome ou telefone e selecione a pessoa correta. Um telefone pode pertencer a vários pacientes.</p>'+
          '<div class="os-search">'+icon('search')+'<input id="osSalesPatientQuery" type="search" minlength="2" maxlength="120" autocomplete="off" placeholder="Nome ou telefone">'+
          button('Buscar','dental-sales-patient-search','os-outline os-small')+'</div>'+
          '<div id="osSalesPatientResults" class="os-sales-patient-results" aria-live="polite"></div></section>';
        showDialog('dental-sales-opportunity','Nova oportunidade',dialogForm('dental-sales-opportunity',content,'Criar oportunidade'));
        break;
      }
      case 'dental-sales-patient-search':{
        if(d?.type!=='dental-sales-opportunity'||state.route!=='vendas-odonto')break;
        const input=$('osSalesPatientQuery'),host=$('osSalesPatientResults'),query=input?.value.trim();
        if(!input||!host)break;
        if(!query||[...query].length<2){host.innerHTML='<p class="os-form-message">Digite pelo menos 2 caracteres.</p>';input.focus();break;}
        element.disabled=true;host.innerHTML='<p class="os-help">Buscando pacientes…</p>';
        try{
          const data=await request(apiPath('/dental/sales/patients/search'),'POST',{query,limit:20,offset:0});
          if(state.dialog!==d||!host.isConnected)return;
          d.patientOptions=data.items;
          host.innerHTML=data.items.length?'<div role="list" aria-label="Pacientes encontrados">'+data.items.map(patient=>
            '<button type="button" class="os-sales-patient-option" data-os-action="dental-sales-patient-select" data-id="'+esc(patient.id)+'" role="listitem">'+
            '<strong>'+esc(patient.name)+'</strong><span>'+(patient.birth_date?'Nascimento: '+esc(fmtDate(patient.birth_date)):'Nascimento não informado')+
            (patient.guardian_name?' · Responsável: '+esc(patient.guardian_name):'')+'</span></button>').join('')+'</div>'+
            (data.has_more?'<p class="os-help">Refine a busca para ver outros resultados.</p>':''):
            '<p class="os-help">Nenhum paciente encontrado.</p>';
        }catch(error){if(state.dialog===d&&host.isConnected)host.innerHTML='<p class="os-form-message">'+esc(error.message)+'</p>';}
        finally{if(element.isConnected)element.disabled=false;}
        break;
      }
      case 'dental-sales-patient-select':{
        if(d?.type!=='dental-sales-opportunity')break;
        const patient=d.patientOptions?.find(item=>item.id===element.dataset.id);if(!patient)break;
        d.patient=patient;
        const host=$('osSalesPatientResults');if(host)host.innerHTML='<div class="os-sales-patient-selected">'+icon('check')+'<span><strong>'+esc(patient.name)+'</strong><small>'+(patient.birth_date?'Nascimento: '+esc(fmtDate(patient.birth_date)):'Nascimento não informado')+(patient.guardian_name?' · Responsável: '+esc(patient.guardian_name):'')+'</small></span>'+button('Trocar','dental-sales-patient-clear','os-ghost os-small')+'</div>';
        break;
      }
      case 'dental-sales-patient-clear':{
        if(d?.type!=='dental-sales-opportunity')break;d.patient=null;d.patientOptions=[];
        const input=$('osSalesPatientQuery'),host=$('osSalesPatientResults');if(input)input.value='';if(host)host.replaceChildren();input?.focus();break;
      }
      case 'dental-sales-clear':{
        const form=$('osDentalSalesPeriod');if(form)await applyDentalSalesPeriod(form,true);
        break;
      }
      case 'dental-sales-search-clear':{
        const form=$('osDentalSalesSearch');if(form)await applyDentalSalesSearch(form,true);
        break;
      }
      case 'dental-opportunity-more':
        if(state.route==='vendas-odonto'&&dentalSettingsAllowed())await loadDentalOpportunities(context(),element);
        break;
      case 'dental-sales-stage-new':
        if(dentalSettingsAllowed()&&state.filters.settingsTab==='dental-sales-stages'){
          const content=field('Nome da etapa','name','text','','required maxlength="16"');
          showDialog('dental-sales-stage','Nova etapa de vendas',dialogForm('dental-sales-stage',content,'Criar etapa'));
        }
        break;
      case 'dental-intake-new':showDentalIntakeEditor();break;
      case 'dental-intake-edit':{
        const template=state.data.dentalIntakes?.find(item=>item.id===element.dataset.id);
        if(template)showDentalIntakeEditor(template);
        break;
      }
      case 'dental-intake-add-question':{
        if(!dentalSettingsAllowed()||d?.type!=='dental-intake')break;
        const host=$('osIntakeQuestions');
        if(!host||host.children.length>=50)break;
        host.insertAdjacentHTML('beforeend',intakeQuestionField());
        host.lastElementChild.querySelector('input').focus();
        if(host.children.length>=50)element.disabled=true;
        break;
      }
      case 'dental-intake-remove-question':{
        if(!dentalSettingsAllowed()||d?.type!=='dental-intake')break;
        const host=$('osIntakeQuestions'),question=element.closest('[data-os-intake-question]');
        if(!host||host.children.length<=1||!host.contains(question))break;
        question.remove();
        const add=$('osDialog').querySelector('[data-os-action=dental-intake-add-question]');
        add.disabled=false;
        add.focus();
        break;
      }
      case 'dental-chair-new':showDentalChairEditor();break;
      case 'dental-chair-edit':{
        const chair=state.data.dentalChairs?.find(item=>item.id===element.dataset.id);
        if(chair)showDentalChairEditor(chair);
        break;
      }
      case 'dental-chair-availability':{
        if(!dentalSettingsAllowed()||state.filters.settingsTab!=='dental-chairs')break;
        const chair=state.data.dentalChairs?.find(item=>item.id===element.dataset.id);
        if(!chair)break;
        const label=chair.active?'Inativar cadeira':'Reativar cadeira';
        const content='<p><strong>'+esc(chair.name)+'</strong></p><p>'+
          (chair.active?'A cadeira sairá das opções de reserva. O histórico permanece disponível.':
            'A cadeira ficará disponível para novas reservas.')+'</p>';
        showDialog('dental-chair-availability',label,dialogForm('dental-chair-availability',content,label),{record:chair});
        break;
      }
      case 'google-refresh':await loadGoogle({handleReturn:false});break;
      case 'google-sign-in':window.location.assign('/painel?aba=agenda');break;
      case 'google-calendars':await showGoogleCalendars();break;
      case 'google-retry':showGoogleAction('retry');break;
      case 'appointment-more':setAppointmentOptions(element.getAttribute('aria-expanded')!=='true');break;
      case 'appointment-extras':await showAppointmentExtras();break;
      case 'appointment-back':if(d?.appointmentId||d?.record?.id)await showAppointmentDetail(d.appointmentId||d.record.id);break;
      case 'reschedule-reload':await loadRescheduleAvailability();break;
      case 'appointment-copy-phone':case 'appointment-copy-protocol':{if(d?.type!=='appointment-detail'||!d.record)return;const text=name==='appointment-copy-phone'?d.record.customer_phone:d.record.protocol;if(typeof text==='string'&&text){const c=context();await navigator.clipboard.writeText(text);if(current(c)&&state.dialog===d)toast(name==='appointment-copy-phone'?'WhatsApp copiado.':'Protocolo copiado.');}break;}
      case 'agenda-tablet':f.agendaTablet=true;f.agendaMode='calendar';await open('agenda',{keepScroll:true});break;
      case 'agenda-tablet-exit':f.agendaTablet=false;await open('agenda',{keepScroll:true});break;
      case 'delete-block':{if(!d?.record||!writable())return;if(d.series){const series=d.series,occurrenceDate=d.occurrenceDate;showDialog('delete-series','Remover bloqueio',dialogForm('delete-series',selectField('Quais bloqueios remover?','scope',[['one','Somente este dia'],['following','Este dia e os próximos'],['all','Toda a série']],'one')+'<p class="os-help">Os períodos selecionados voltarão a aceitar novos agendamentos, conforme o expediente.</p>','Remover bloqueio'),{series,occurrenceDate});}else{const path=apiPath('/'+d.resource+'/'+encodeURIComponent(d.record.id));confirmMutation('Remover bloqueio','Este período voltará a aceitar novos agendamentos, conforme o expediente do profissional.',()=>request(path,'DELETE'),'Bloqueio removido.');}break;}
      case 'agenda-today':f.agendaOffset=0;f.agendaDay=dateKey();f.agendaFocusDay=dateKey();f.agendaFocusOffset=0;await open('agenda',{keepScroll:true});break;
      case 'agenda-prev':case 'agenda-next':case 'week-prev':case 'week-next':{
        f.agendaOffset=0;f.agendaFocusDay='';f.agendaFocusOffset=0;
        const delta=name.endsWith('prev')?-1:1,period=agendaPeriod();
        const restoreWeekNav=period==='week'&&document.activeElement===element;
        const day=name.startsWith('week-')?shiftDay(f.agendaDay,delta*7):
          period==='month'?agendaShiftMonth(f.agendaDay,delta):
          shiftDay(f.agendaDay,delta*(period==='week'?7:1));
        f.agendaDay=day;
        await open('agenda',{keepScroll:true});
        if(restoreWeekNav&&state.route==='agenda'&&agendaPeriod()==='week'&&state.filters.agendaDay===day)
          document.querySelector('.os-agenda-week-overview-wrapper [data-os-action="'+name+'"]')?.focus({preventScroll:true});
        break;
      }
      case 'customer-history-more':if(d?.type==='customer')await loadCustomerServiceHistory(d,{append:true});break;
      case 'customer-history-retry':if(d?.type==='customer')await loadCustomerServiceHistory(d,{append:!!d.historyRetryAppend});break;
      case 'customers-prev':f.customerOffset=Math.max(0,f.customerOffset-50);await open('clientes',{keepScroll:true});break;
      case 'customers-next':f.customerOffset+=50;await open('clientes',{keepScroll:true});break;
      case 'customer-birthdays':f.customerStatus=f.customerStatus==='birthday'?'active':'birthday';f.customerOffset=0;await open('clientes',{keepScroll:true});break;
      case 'export-customers':{const url=mgmt('/customers/export.csv?'+query({q:f.customerQuery,status:f.customerStatus,risk_days:f.customerRisk}));if(state.bridge.exportRequest){await state.bridge.exportRequest(url,'clientes.csv');}else{const a=document.createElement('a');a.href=url;a.download='clientes.csv';a.rel='noopener';document.body.appendChild(a);a.click();a.remove();}break;}
      case 'period-month':resetPeriod();await open(state.route,{keepScroll:true});break;
      case 'new-income':showFinance('income');break;
      case 'new-expense':showFinance('expense');break;
      case 'new-campaign':showCampaign();break;
      case 'marketing-generator':f.marketingTab='campaigns';renderMarketing(state.data.marketing);$('page-suite').querySelector('[name=brief]')?.focus();break;
      case 'marketing-automations':f.marketingTab='automations';await open('marketing',{keepScroll:true});break;
      case 'marketing-prev':f.marketingOffset=Math.max(0,(f.marketingOffset||0)-100);await open('marketing',{keepScroll:true});break;
      case 'marketing-next':f.marketingOffset=(f.marketingOffset||0)+100;await open('marketing',{keepScroll:true});break;
      case 'save-ai-campaign':{const output=state.data.aiCampaignResult?.output;if(!output)return;showCampaign();const form=$('osDialog').querySelector('form');form.elements.name.value=String(output.name||'Campanha').slice(0,120);form.elements.message.value=String(output.whatsapp||output.offer||'').slice(0,5000);break;}
      case 'consultor-reset':state.data.consultorMessages=[];renderConsultor();break;
      case 'new-loyalty':f.loyaltyTab='settings';await open('fidelizacao',{keepScroll:true});break;
      case 'loyalty-prev':f.loyaltyOffset=Math.max(0,f.loyaltyOffset-50);await open('fidelizacao',{keepScroll:true});break;
      case 'loyalty-next':f.loyaltyOffset+=50;await open('fidelizacao',{keepScroll:true});break;
      case 'copy-loyalty-message':if(state.data.loyaltyShareText){await navigator.clipboard.writeText(state.data.loyaltyShareText);toast('Mensagem copiada.');}break;
      case 'issue-coupon':{const customer=rows(state.data.loyaltyRanking).find(c=>c.id===element.dataset.customer),rule=rows(state.data.loyalty?.rules).find(r=>r.active);if(!customer||!rule||!writable())return;const path=mgmt('/loyalty/coupons'),key=crypto.randomUUID();confirmMutation('Emitir cupom','Usar '+number(rule.reward_points)+' pontos de '+customer.name+' para gerar '+rule.reward_description+'.',async()=>{await request(path,'POST',{customer_id:customer.id,idempotency_key:key});f.loyaltyTab='coupons';f.loyaltyOffset=0;},'Cupom emitido.');break;}
      case 'loyalty-points':await showLoyaltyPoints();break;
      case 'new-goal':await showGoal();break;
      case 'professional-history-retry':await loadProfessionalHistory(true);break;
      case 'professional-history-prev':case 'professional-history-next':if(d?.type==='professional-history'){if(name==='professional-history-next'&&!d.result?.has_more)return;d.offset=Math.max(0,d.offset+(name==='professional-history-prev'?-25:25));await loadProfessionalHistory(true);}break;
      case 'help-back':state.filters.helpArticle='';renderHelp();window.scrollTo({top:0,behavior:'instant'});break;
      case 'site-information':showDialog('site-information','Sobre o seu site','<div class="os-form"><p>O cliente pode consultar seus serviços, escolher um profissional e reservar um horário disponível na página pública.</p><p>As configurações salvas formam um rascunho. A prévia mostra esse rascunho e não registra visitas no painel de métricas.</p><p>O proprietário publica uma versão revisada. Alterações posteriores de apresentação aguardam nova publicação. A disponibilidade dos horários continua sendo verificada na agenda real.</p><div class="os-form-actions">'+button('Entendi','close-dialog','','check')+'</div></div>');break;
      case 'publish-site':case 'unpublish-site':case 'restore-site':showSitePublication(name);break;
      case 'site-refresh':if($('osSiteForm')?.dataset.dirty==='true'){toast('Salve as configurações antes de atualizar o estado.',true);return;}closeDialog();await open('site',{keepScroll:true});break;
      case 'site-reauthenticate':if(siteNeedsReauthentication()){if($('osSiteForm')?.dataset.dirty==='true'){toast('Salve as configurações antes de atualizar seu acesso de administrador.',true);return;}if(typeof state.bridge.reauthenticate==='function')await state.bridge.reauthenticate();else toast('Atualize a página para confirmar seu acesso de administrador.',true);}break;
      case 'site-cep-retry':{const input=$('osSiteForm')?.elements.postal_code;if(input&&siteCanEdit())queueSiteCepLookup(input,0);break;}
      case 'copy-site':{const link=sitePublicLink();if(!link)throw Error('Sua página ainda não foi publicada.');await navigator.clipboard.writeText(link);toast('Link copiado.');break;}
      case 'google-connect':showGoogleAction('connect');break;
      case 'google-email':showGoogleEmailVerification();break;
      case 'google-revoke':showGoogleAction('revoke');break;
      case 'delete-customer':deleteResource('customer',d?.record);break;
      case 'delete-finance':deleteResource('finance',d?.record);break;
      case 'delete-campaign':deleteResource('campaign',d?.record);break;
      case 'delete-loyalty':deleteResource('loyalty',d?.record);break;
      case 'delete-goal':{
        const g=d?.record;if(!g||!writable()||g.can_edit===false)return;
        const path=mgmt('/goals/'+encodeURIComponent(g.id));
        confirmMutation('Excluir meta','A meta de '+goalOwner(g)+(g.recurrence==='monthly'?' deixará de valer no mês atual e nos próximos. Os resultados dos meses anteriores serão preservados.':' será encerrada. O histórico do período anterior à exclusão será preservado.'),()=>request(path,'DELETE',{expected_version:g.template_version||g.version}),'Meta excluída.');break;
      }
      case 'delete-service':{if(!d?.record||!writable())return;const r=d.record,path=apiPath('/services/'+encodeURIComponent(r.id));confirmMutation('Excluir serviço','O serviço '+r.name+' deixará de aparecer no catálogo. Agendamentos antigos continuarão no histórico.',()=>request(path,'DELETE'),'Serviço excluído.');break;}
      case 'confirm-appointment':await showAppointmentAction('confirm');break;
      case 'cancel-appointment':await showAppointmentAction('cancel');break;
      case 'complete-appointment':await showAppointmentAction('complete');break;
      case 'no-show-appointment':await showAppointmentAction('no_show');break;
      case 'reschedule-appointment':await showAppointmentAction('reschedule');break;
      case 'fixed-open-from-appointment':if(d?.record?.series_id)await showFixedSeriesDetail(d.record.series_id);break;
      case 'fixed-edit':if(d?.type==='fixed-detail'&&d.record&&writable())await showFixedClient({editSeries:d.record});break;
      case 'fixed-stop':if(d?.type==='fixed-detail'&&d.record?.next_start_at)confirmMutation('Encerrar cliente fixo','Cancelar todos os horários futuros dessa recorrência? Atendimentos anteriores, histórico e financeiro serão preservados. Esta operação não envia cancelamentos em massa pelo WhatsApp.',()=>request(apiPath('/appointments/series/'+encodeURIComponent(d.record.id)+'/stop'),'POST',{}),'Horários futuros cancelados.');break;
    }
  }
  async function handleClick(event){
    if(!state.mounted)return;
    if(event.target.closest('#osAppointmentOptions[hidden]'))return;
    if($('osAppointmentMore')?.getAttribute('aria-expanded')==='true'&&!event.target.closest('.os-appointment-options'))setAppointmentOptions(false);
    if(state.notificationsOpen&&!event.target.closest('.os-notifications')&&!event.target.closest('[data-os-action=notifications]'))closeNotifications();let el=event.target.closest('button,[role=button],a[data-os-route]');if(el&&!Object.keys(el.dataset).some(key=>key.startsWith('os')))el=null;
    if(!el){if(!$('osThemeMenu')?.classList.contains('hidden')&&!event.target.closest('.os-theme')){$('osThemeMenu').classList.add('hidden');$('osThemeToggle').setAttribute('aria-expanded','false');}return;}if(el.disabled)return;
    event.preventDefault();try{
      if(el.dataset.osLayout){chooseAppLayout(el.dataset.osLayout);return;}
      if(el.dataset.osTheme){setTheme(el.dataset.osTheme);return;}
      if(el.dataset.osAppTab){
        if(!appLayoutEnabled()||!appTabAllowed(el.dataset.osAppTab))return;
        if(el.dataset.osAppTab==='hours'){state.filters.settingsTab='hours';await open('configuracoes');}
        else await open(el.dataset.osAppTab);
        return;
      }
      if(el.dataset.osAction){await action(el.dataset.osAction,el);return;}
      if(state.busy)return;
      if(el.dataset.osHelpArticle){openHelpArticle(el.dataset.osHelpArticle);return;}
      if(el.dataset.osHelpCategory){state.filters.helpCategory=el.dataset.osHelpCategory;state.filters.helpArticle='';renderHelp();return;}
      if(el.dataset.osNewsFilter){state.filters.newsFilter=el.dataset.osNewsFilter;renderNews();return;}
      if(el.dataset.osSiteSlugSuggestion){if(!siteCanEdit())return;const form=$('osSiteForm'),input=form?.elements.slug;if(!input||!el.closest('#osSiteSlugSuggestions'))return;input.value=el.dataset.osSiteSlugSuggestion;form.dataset.dirty='true';queueSiteSlugCheck(input,0);input.focus({preventScroll:true});return;}
      if(el.dataset.osSiteTab){if($('osSiteForm')?.dataset.dirty==='true'){toast('Salve as configurações antes de mudar de aba.',true);return;}state.filters.siteTab=el.dataset.osSiteTab;renderSite(state.data.site);return;}
      if(el.dataset.osSiteFix){if($('osSiteForm')?.dataset.dirty==='true'){toast('Salve as configurações antes de revisar outra área.',true);return;}const target=el.dataset.osSiteFix;if(target==='site'){state.filters.siteTab='settings';renderSite(state.data.site);}else if(target==='hours'){state.filters.settingsTab='hours';await open('configuracoes');}else if(target==='business')await action('business-setup',el);else await open(target==='services'?'servicos':'funcionarios');return;}
      if(el.dataset.osAutomationTab){if(el.dataset.osAutomationTab==='vacancy'&&!permitted('reception.automation'))return;state.filters.automationTab=el.dataset.osAutomationTab;renderMarketing(state.data.marketing);return;}
      if(el.dataset.osQueueStatus){state.filters.marketingQueueStatus=el.dataset.osQueueStatus;await open('marketing',{keepScroll:true});return;}
      if(el.dataset.osNotificationFilter){state.filters.notificationsUnread=el.dataset.osNotificationFilter==='true';await loadNotifications();return;}
      if(el.dataset.osNotification){const item=rows(state.data.notifications).find(item=>item.id===el.dataset.osNotification);if(!item)return;const c=context();if(!item.read_at&&state.data.notifications?.can_mark_read)await markNotificationsRead([item.id]);if(!stillAccount(c))return;closeNotifications();await showAppointmentDetail(item.appointment_id);return;}
      if(el.dataset.osAppHours){if(!appLayoutEnabled()||!settingsTabAllowed('hours')||!['hours','blocks'].includes(el.dataset.osAppHours))return;state.filters.appHoursView=el.dataset.osAppHours;await open('configuracoes',{keepScroll:true});return;}
      if(el.dataset.osAppSettings){if(!(appLayoutEnabled()||subscriptionWorkspace())||!settingsTabAllowed(el.dataset.osAppSettings))return;state.filters.settingsTab=el.dataset.osAppSettings;await open('configuracoes');return;}
      if(el.dataset.osDentalSetting){
        const tab=el.dataset.osDentalSetting;
        if(tab==='dental-access'){
          if(state.route==='ajustes-odonto'&&dentalSettingsAllowed()&&routePermitted('clinica'))await open('clinica',{dentalTeam:true});
          return;
        }
        if(state.route!=='ajustes-odonto'||!dentalWorkspace()||!routePermitted('configuracoes')||!settingsTabAllowed(tab))return;
        state.filters.settingsTab=tab;await open('configuracoes');return;
      }
      if(el.dataset.osRoute){if(dentalWorkspace()&&state.route==='ajustes-odonto'&&el.dataset.osRoute==='configuracoes')state.filters.settingsTab=settingsTabAllowed('general')?'general':'account';if(appLayoutEnabled()&&state.route==='perfil'&&el.dataset.osRoute==='configuracoes')state.filters.settingsTab='general';await open(el.dataset.osRoute);return;}
      if(el.dataset.osOpenDay){
        const day=el.dataset.osOpenDay,{start,end}=agendaBounds();
        if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||day<start||day>end)return;
        state.filters.agendaDay=day;state.filters.agendaFocusDay=day;
        state.filters.agendaFocusOffset=0;state.filters.agendaMode='calendar';state.filters.agendaRangeList=false;
        renderAgenda();
        document.querySelector('[data-os-open-day="'+day+'"]')?.focus({preventScroll:true});
        await loadAgendaFocus(context());
        return;
      }
      if(el.dataset.osAgendaPeriod){const period=el.dataset.osAgendaPeriod;if(!['day','week','month'].includes(period)||period===agendaPeriod())return;state.filters.agendaPeriod=period;state.filters.agendaMode=period==='month'?'calendar':'list';state.filters.agendaOffset=0;state.filters.agendaFocusDay='';state.filters.agendaFocusOffset=0;state.filters.agendaRangeList=period!=='month';await open('agenda',{keepScroll:true});return;}
      if(el.dataset.osDay){state.filters.agendaDay=el.dataset.osDay;state.filters.agendaOffset=0;await open('agenda',{keepScroll:true});$('osAgendaDay-'+state.filters.agendaDay)?.focus();return;}
      if(el.dataset.osAgendaMode){const mode=el.dataset.osAgendaMode;if(!['list','calendar','professional'].includes(mode))return;state.filters.agendaMode=mode;state.filters.agendaOffset=0;state.filters.agendaFocusDay='';state.filters.agendaFocusOffset=0;if(agendaPeriod()!=='day')state.filters.agendaRangeList=mode!=='calendar';clearTimeout(state.agendaSearchTimer);await open('agenda',{keepScroll:true});return;}
      if(el.dataset.osSettings){state.filters.settingsTab=el.dataset.osSettings;await open('configuracoes',{keepScroll:true});return;}
      if(el.dataset.osSiteRequestCopy){if(!canManageSiteRequests()||state.filters.settingsTab!=='site-requests')return;const item=rows(state.data.siteRequests).find(item=>item.id===el.dataset.requestId),key=el.dataset.osSiteRequestCopy;if(!item||!['email','phone'].includes(key)||typeof item[key]!=='string')return;const c=context();await navigator.clipboard.writeText(item[key]);if(current(c))toast(key==='email'?'E-mail copiado.':'WhatsApp copiado.');return;}
      if(el.dataset.osCustomerStatus){state.filters.customerStatus=el.dataset.osCustomerStatus;state.filters.customerOffset=0;await open('clientes',{keepScroll:true});return;}
      if(el.dataset.osCustomerRisk){state.filters.customerRisk=Number(el.dataset.osCustomerRisk);state.filters.customerOffset=0;await open('clientes',{keepScroll:true});return;}
      if(el.dataset.osCustomerToggle){const c=rows(state.data.customers).find(c=>c.id===el.dataset.osCustomerToggle);if(c)await runInline(()=>request(mgmt('/customers/'+encodeURIComponent(c.id)),'PATCH',{active:!c.active,expected_version:c.version}),c.active?'Cliente inativado.':'Cliente ativado.');return;}
      if(el.dataset.osCustomerDelete){deleteResource('customer',rows(state.data.customers).find(c=>c.id===el.dataset.osCustomerDelete));return;}
      if(el.dataset.osStaffTab){state.filters.staffTab=el.dataset.osStaffTab;document.querySelectorAll('[data-os-staff-tab]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.osStaffTab===state.filters.staffTab)));renderProfessionals();return;}
       if(el.dataset.osStaffStatusFilter){
         if(state.route!=='funcionarios'||state.filters.staffTab!=='professionals'||!['active','inactive'].includes(el.dataset.osStaffStatusFilter))return;
         state.filters.staffStatus=el.dataset.osStaffStatusFilter;renderProfessionals();return;
       }
       if(el.dataset.osProfessionalStatus){
         if(state.route!=='funcionarios'||state.filters.staffTab!=='professionals'||!writable())return;
         const p=(state.data.professionals||[]).find(item=>item.id===el.dataset.osProfessionalStatus);
         if(!p)return;
         const activate=p.active===false,version=Number(p.commission_config_version);
         if(activate&&atProfessionalLimit()){toast('Você atingiu o limite de profissionais do seu plano.',true);return;}
         if(!Number.isInteger(version)||version<1){toast('Atualize a lista antes de alterar este funcionário.',true);return;}
         confirmMutation(activate?'Reativar funcionário':'Remover funcionário',
           activate?'Reativar '+p.name+'? O profissional voltará para a lista de ativos. A disponibilidade continuará respeitando as configurações dos horários e agendamentos.':
             'Remover '+p.name+' da equipe ativa? Ele irá para Inativos e deixará de aparecer nas opções de novos agendamentos. Os atendimentos, valores e comissões anteriores serão preservados. Se ele tiver acesso ao painel, desative-o também em Equipe & Permissões.',
           ()=>request(mgmt('/professionals/'+encodeURIComponent(p.id)+'/status'),'PATCH',{active:activate,expected_version:version}),
           activate?'Profissional reativado.':'Funcionário movido para Inativos.');
         return;
       }
      if(el.dataset.osReceipt){await showServiceReceipt(el.dataset.osReceipt);return;}
      if(el.dataset.osFinanceTab){if(!permitted(financeFeature(el.dataset.osFinanceTab)))return;state.filters.financeTab=el.dataset.osFinanceTab;state.filters.financeOffset=0;await open('financeiro',{keepScroll:true});return;}
      if(el.dataset.osPerformancePeriod){setPerformancePeriod(el.dataset.osPerformancePeriod);await open('desempenho',{keepScroll:true});return;}
      if(el.dataset.osPerformanceTab){selectPerformanceTab(el.dataset.osPerformanceTab);return;}
      if(el.dataset.osPerformanceGroup){if(!['day','week'].includes(el.dataset.osPerformanceGroup))return;state.filters.performanceGroup=el.dataset.osPerformanceGroup;state.filters.performancePoint='';refreshPerformanceView();document.querySelector('[data-os-performance-group="'+state.filters.performanceGroup+'"]')?.focus({preventScroll:true});return;}
      if(el.hasAttribute('data-os-performance-point')){selectPerformancePoint(Number(el.dataset.osPerformancePoint));return;}
      if(el.dataset.osPerformanceHistory){await showProfessionalHistory(el.dataset.osPerformanceHistory,el);return;}
      if(el.dataset.osServiceModel!==undefined){const model=serviceModelOptions()[Number(el.dataset.osServiceModel)];if(model)await showService(null,{name:model[1],category:model[0]});return;}
      if(el.dataset.osServiceOrder){await moveService(el.dataset.osServiceOrder,Number(el.dataset.direction));return;}
      if(el.dataset.osServiceDelete){deleteService((state.data.services||[]).find(s=>s.id===el.dataset.osServiceDelete));return;}
      if(el.dataset.osPackage){const record=rows(state.data.packages).find(p=>p.id===el.dataset.osPackage);if(record)await showPackage(record);return;}
      if(el.dataset.osPackageDiscount){applyPackageDiscount(Number(el.dataset.osPackageDiscount));return;}
      if(el.dataset.osColor){const input=$('osDialog')?.querySelector('input.os-color-native[name=color]');if(input){input.value=el.dataset.osColor;syncColorPicker(input);}return;}
      if(el.dataset.osCustomer){const c=rows(state.data.customers).find(c=>c.id===el.dataset.osCustomer);if(c)showCustomer(c);return;}
      if(el.dataset.osService){const s=(state.data.services||[]).find(s=>s.id===el.dataset.osService);if(s)await showService(s);return;}
      if(el.dataset.osTeamMember){const member=rows(state.data.team?.members).find(m=>m.account_id===el.dataset.osTeamMember);if(member)showTeamMember(member);return;}
      if(el.dataset.osProfessional){const p=(state.data.professionals||[]).find(p=>p.id===el.dataset.osProfessional);if(p)await showProfessional(p);return;}
      if(el.dataset.osHours){await showHours(el.dataset.osHours);return;}
      if(el.dataset.osBlock){showBlockDetail(el.dataset.osBlock,el.dataset.resource);return;}
      if(el.dataset.osCalendarSlot){await showAppointment({professionalId:el.dataset.osCalendarSlot,time:el.dataset.time});return;}
      if(el.dataset.osAppointment){await showAppointmentDetail(el.dataset.osAppointment,{trigger:el});return;}
      if(el.dataset.osFixedSeries){await showFixedSeriesDetail(el.dataset.osFixedSeries);return;}
      if(el.dataset.osFixedAppointment){await showAppointmentDetail(el.dataset.osFixedAppointment,{trigger:el});return;}
      if(el.dataset.osFixedService){const d=state.dialog;if(d?.type!=='fixed-client')return;const id=el.dataset.osFixedService;if(!d.serviceIds.includes(id)&&d.serviceIds.length>=6){toast('Selecione até 6 serviços.',true);return;}d.serviceIds=d.serviceIds.includes(id)?d.serviceIds.filter(x=>x!==id):[...d.serviceIds,id];el.setAttribute('aria-pressed',String(d.serviceIds.includes(id)));resetFixedPreview();return;}
      if(el.dataset.osFixedCustomer){const d=state.dialog;if(d?.type!=='fixed-client')return;const c=d.customers.find(item=>item.id===el.dataset.osFixedCustomer);if(!c)return;const form=$('osDialog').querySelector('form');form.elements.customer_name.value=c.name;form.elements.customer_phone.value=sharedFields().phoneDisplay(c.phone||'');$('osFixedCustomers')?.replaceChildren();resetFixedPreview();return;}
      if(el.dataset.osSettlement){await showSettlementDetail(el.dataset.osSettlement);return;}
      if(el.dataset.osFinance){const r=[...rows(state.data.finance),...rows(state.data.financeAdvances),...rows(state.data.financePayments)].find(r=>r.id===el.dataset.osFinance);if(r){if(number(r.allocated_cents)>0||rows(state.data.settlements).some(item=>item.payment_entry_id===r.id))showLockedFinance(r);else showFinance(r.kind,r);}return;}
      if(el.dataset.osMarketingTab){state.filters.marketingTab=el.dataset.osMarketingTab;if(['automations','queue'].includes(el.dataset.osMarketingTab))await open('marketing',{keepScroll:true});else renderMarketing(state.data.marketing);return;}
      if(el.dataset.osCampaignExample!==undefined){const example=campaignExamples[Number(el.dataset.osCampaignExample)],input=$('page-suite').querySelector('[name=brief]');if(input&&example){input.value=example;input.focus();}return;}
      if(el.dataset.osCopyAi){if(['whatsapp','instagram','stories','notification'].includes(el.dataset.osCopyAi)){const text=state.data.aiCampaignResult?.output?.[el.dataset.osCopyAi];if(text){await navigator.clipboard.writeText(text);toast('Texto copiado.');}}return;}
      if(el.dataset.osAiHistory){const item=rows(state.data.aiHistory).find(item=>item.id===el.dataset.osAiHistory);if(item){state.data.aiCampaignResult=item;renderMarketing(state.data.marketing);}return;}
      if(el.dataset.osConsultorPrompt!==undefined){const question=consultantPrompts[Number(el.dataset.osConsultorPrompt)],input=$('osConsultorQuestion');if(question&&input){input.value=question;input.focus();}return;}
      if(el.dataset.osCampaign){const r=rows(state.data.marketing).find(r=>r.id===el.dataset.osCampaign);if(r)showCampaign(r);return;}
      if(el.dataset.osCampaignAudience){showCampaign(null,el.dataset.osCampaignAudience);return;}
      if(el.dataset.osLoyaltyTab){state.filters.loyaltyTab=el.dataset.osLoyaltyTab;state.filters.loyaltyOffset=0;await open('fidelizacao',{keepScroll:true});return;}
      if(el.dataset.osCouponCopy){const coupon=rows(state.data.loyaltyCoupons).find(c=>c.id===el.dataset.osCouponCopy);if(coupon){await navigator.clipboard.writeText(coupon.code);toast('Código copiado.');}return;}
      if(el.dataset.osCopyLoyaltyIdea!==undefined){const idea=rows(state.data.loyaltyIdeasResult?.output?.ideas)[Number(el.dataset.osCopyLoyaltyIdea)];if(idea?.message){await navigator.clipboard.writeText(idea.message);toast('Mensagem copiada.');}return;}
      if(el.dataset.osLoyalty){const r=rows(state.data.loyalty?.rules).find(r=>r.id===el.dataset.osLoyalty);if(r)showLoyalty(r);return;}
      if(el.dataset.osGoal){const key=el.dataset.osGoal,r=key.startsWith('template:')?rows(state.data.goalTemplates).find(g=>'template:'+g.id===key):rows(state.data.goals).find(g=>goalKey(g)===key);if(r)await showGoal(r);return;}
      if(el.dataset.osDuration){const form=$('osDialog')?.querySelector('[data-os-form=service]');const input=form?.elements?.duration_min;if(input){input.value=el.dataset.osDuration;syncServiceDurationPresets(form);}return;}
      if(el.dataset.osBookService){const d=state.dialog;if(d?.type!=='appointment'||d.holdForm||d.manualOperation)return;const id=el.dataset.osBookService;if(d.serviceIds.includes(id))d.serviceIds=d.serviceIds.filter(s=>s!==id);else if(d.serviceIds.length<6)d.serviceIds.push(id);else{toast('Escolha até 6 serviços por agendamento.',true);return;}el.setAttribute('aria-pressed',String(d.serviceIds.includes(id)));await updateAvailability();return;}
      if(el.dataset.osBookCustomer){const d=state.dialog;if(d?.type!=='appointment'||d.holdForm||d.manualOperation)return;const c=d.customers.find(c=>c.id===el.dataset.osBookCustomer);if(!c)return;const form=$('osDialog').querySelector('form');form.elements.customer_name.value=c.name;form.elements.customer_phone.value=sharedFields().phoneDisplay(c.phone||'');d.customerId=c.id;syncBookingMode();$('osBookingCustomers').replaceChildren();$('osBookingCustomerHelp').textContent=c.phone?'':d.bookingMode!=='regular'?'Cliente selecionado sem WhatsApp. Informe o contato ou marque Cliente avulso.':'Este cliente ainda não tem WhatsApp. Ao confirmar, o número informado será salvo neste cadastro.';return;}

      if(el.dataset.osStartPolicyToggle!==undefined){
        const d=state.dialog,weekday=Number(el.dataset.osStartPolicyToggle);
        if(d?.type!=='hours'||!d.startPolicies?.some(p=>p.weekday===weekday))return;
        const expanded=d.hoursEditor?.mode==='start-policy'&&d.hoursEditor.weekday===weekday;
        d.hoursEditor=expanded?null:{mode:'start-policy',weekday};
        if(!expanded&&writable())hoursStartDraft(d,weekday);
        renderHoursDialog();
        const target=expanded?'[data-os-start-policy-toggle="'+weekday+'"]':'#osHoursStartPolicy-'+weekday+' input:checked';
        $('osDialog').querySelector(target)?.focus({preventScroll:true});return;
      }
      if(el.dataset.osStartPolicyCancel!==undefined){
        const d=state.dialog,weekday=Number(el.dataset.osStartPolicyCancel);
        if(d?.type!=='hours'||!writable())return;
        delete d.startPolicyDrafts?.[weekday];d.hoursEditor=null;renderHoursDialog();
        $('osDialog').querySelector('[data-os-start-policy-toggle="'+weekday+'"]')?.focus({preventScroll:true});return;
      }
      if(el.dataset.osStartPolicyRetry!==undefined||el.dataset.osStartPolicyReload!==undefined){
        await reloadHoursStartPolicies(state.dialog,Number(el.dataset.osStartPolicyRetry??el.dataset.osStartPolicyReload),el.dataset.osStartPolicyReload!==undefined);return;
      }
      if(el.dataset.osStartTimeAdd!==undefined){addHoursStartTime(el.closest('form'));return;}
      if(el.dataset.osStartTimeRemove!==undefined){
        const d=state.dialog,form=el.closest('[data-os-form="hours-start-policy"]'),weekday=Number(form?.dataset.weekday);
        if(d?.type!=='hours'||!writable()||!form)return;
        const draft=hoursStartDraft(d,weekday);if(!draft||draft.mode!=='manual')return;
        draft.startTimes=draft.startTimes.filter(time=>time!==el.dataset.osStartTimeRemove);draft.message='';
        draft.pendingTime=form.elements.start_time?.value||'';renderHoursDialog();$('osHoursStartTime-'+weekday)?.focus({preventScroll:true});return;
      }
      if(el.dataset.osHoursCancel!==undefined){
        if(state.dialog?.type==='hours'){state.dialog.hoursEditor=null;renderHoursDialog();}return;
      }
      if(el.dataset.osHoursAdd!==undefined||el.dataset.osEditHour||el.dataset.osPause||el.dataset.osEditBreak){
        const d=state.dialog;if(d?.type!=='hours'||!writable())return;
        let next;
        if(el.dataset.osHoursAdd!==undefined){
          const weekday=Number(el.dataset.osHoursAdd);
          if(!Number.isInteger(weekday)||weekday<0||weekday>6)return;
          next={mode:'add-hour',weekday};
        }else if(el.dataset.osEditHour){
          if(!(d.hours||[]).some(h=>h.id===el.dataset.osEditHour))return;
          next={mode:'edit-hour',id:el.dataset.osEditHour};
        }else if(el.dataset.osPause){
          if(!(d.hours||[]).some(h=>h.id===el.dataset.osPause))return;
          next={mode:'add-break',id:el.dataset.osPause};
        }else{
          if(!(d.breaks||[]).some(b=>b.id===el.dataset.osEditBreak))return;
          next={mode:'edit-break',id:el.dataset.osEditBreak};
        }
        const prev=d.hoursEditor;
        d.hoursEditor=prev&&prev.mode===next.mode&&prev.id===next.id&&prev.weekday===next.weekday?null:next;
        renderHoursDialog();
        const form=$('osDialog')?.querySelector('.os-hours-editor');
        if(form){form.scrollIntoView({block:'nearest'});form.querySelector('select,input:not([type=hidden])')?.focus({preventScroll:true});}
        return;
      }
      if(el.dataset.osDeleteHour||el.dataset.osDeleteBreak){if(!writable())return;const isBreak=!!el.dataset.osDeleteBreak,id=el.dataset.osDeleteBreak||el.dataset.osDeleteHour,path=apiPath('/'+(isBreak?'working_breaks':'working_hours')+'/'+encodeURIComponent(id));confirmMutation(isBreak?'Remover pausa':'Remover horário','Este período deixará de fazer parte da disponibilidade do profissional.',()=>request(path,'DELETE'),isBreak?'Pausa removida.':'Horário removido.');}
    }catch(ex){toast(ex.message||'Não foi possível concluir a ação.',true);}
  }
  function markCustomerBirthChanged(input){
    if(state.dialog?.type!=='customer'||input?.name!=='birth_date'||!input.closest('[data-os-form=customer]'))return;
    input.dataset.edited='true';
    const legacy=$('osCustomerLegacyBirthday');if(legacy){legacy.hidden=true;input.removeAttribute('aria-describedby');}
  }
  async function handleChange(event){if(!state.mounted)return;const el=event.target;markCustomerBirthChanged(el);markSiteAddressEdit(el);if(el.matches?.('[data-os-image-file]')){await sendImageFile(el);return;}if(state.busy)return;if(el.matches?.('input.os-color-native'))syncColorPicker(el);if(el.closest('#osSiteForm'))el.closest('form').dataset.dirty='true';
    if(el.closest('#osSiteForm')&&siteCanEdit()){if(el.name==='slug'){finalizeSiteSlug(el);return;}if(el.name==='postal_code'){const form=el.closest('form');if(form._siteCepQuery!==el.value.replace(/\D/g,''))queueSiteCepLookup(el);return;}if(el.name==='address_no_number'){syncSiteNumber(el.closest('form'));return;}if(el.name==='state'){el.value=el.value.toUpperCase();return;}}
    if(el.id==='osAgendaDatePicker'){if(/^\d{4}-\d{2}-\d{2}$/.test(el.value)){state.filters.agendaDay=el.value;state.filters.agendaFocusDay=el.value;state.filters.agendaFocusOffset=0;state.filters.agendaOffset=0;await open('agenda',{keepScroll:true});$('osAgendaDatePicker')?.focus();}return;}
    if(el.id==='osAgendaProfessional'||el.id==='osAgendaStatus'){state.filters[el.id==='osAgendaProfessional'?'agendaProfessional':'agendaStatus']=el.value;await applyAgendaFilters();return;}
    if(state.dialog?.action==='reschedule'&&el.name==='day'){await loadRescheduleAvailability();return;}
    if(el.dataset.osAppShortcut!==undefined){
      if(appLayoutEligible()&&state.route==='configuracoes'&&state.filters.settingsTab==='appearance')changeAppShortcut(Number(el.dataset.osAppShortcut),el.value);
      return;
    }
    if(el.id==='osDentalChairFilter'&&dentalSettingsAllowed()){
      if(!['all','active','inactive'].includes(el.value))return;
      state.filters.dentalChairFilter=el.value;
      renderSettings();
      $('osDentalChairFilter')?.focus({preventScroll:true});
      return;
    }
    if(el.id==='osSettingsSection'){state.filters.settingsTab=el.value;await open('configuracoes',{keepScroll:true});return;}
    if(['osSiteRequestStatus','osSiteRequestIntent'].includes(el.id)){await applySiteRequestFilters(el.closest('form'));return;}
    if(el.id==='osHoursProfessional'){
      const c=context(),id=el.value;state.filters.hoursProfessional=id;
      const pending=loadSettingsStartPolicies(id,c);renderSettings();await pending;
      if(current(c))renderSettings();return;
    }
    if(el.matches?.('[data-os-start-policy-mode]')){
      const d=state.dialog,form=el.closest('[data-os-form="hours-start-policy"]'),weekday=Number(form?.dataset.weekday);
      if(d?.type!=='hours'||!writable()||!form||!['automatic','manual'].includes(el.value))return;
      const draft=hoursStartDraft(d,weekday);if(!draft)return;
      draft.pendingTime=form.elements.start_time?.value||draft.pendingTime;draft.mode=el.value;draft.message='';
      renderHoursDialog();$('osDialog').querySelector('[data-os-start-policy-mode]:checked')?.focus({preventScroll:true});return;
    }
    if(el.name==='tax_document_kind'&&el.closest('[data-os-form=business-preferences]')){const input=el.closest('form').elements.tax_document;setTaxDocumentKind(input,el.value);return;}
    if(el.id==='osFinanceMonth'){if(/^\d{4}-\d{2}$/.test(el.value)){setMonth(el.value);await open('financeiro',{keepScroll:true});}return;}
    if(el.id==='osPerformanceMetric'){if(!['revenue_cents','completed',...(!essentialPerformance()?['commission_cents']:[])].includes(el.value))return;state.filters.performanceMetric=el.value;refreshPerformanceView();$('osPerformanceMetric')?.focus({preventScroll:true});return;}
    if(el.id==='osPerformanceProfessional'){if(el.value&&!rows(state.data.performance).some(p=>p.id===el.value))return;state.filters.performanceProfessional=el.value;state.filters.performancePoint='';refreshPerformanceView();$('osPerformanceProfessional')?.focus({preventScroll:true});return;}
    if(el.id==='osPerformancePeriod'){if(el.value==='custom'){el.value=state.filters.performancePeriod;await action('custom-period',el);return;}setPerformancePeriod(el.value);await open('desempenho',{keepScroll:true});return;}
    if(el.id==='osProfessionalHistoryStatus'&&state.dialog?.type==='professional-history'){state.dialog.status=el.value;state.dialog.offset=0;await loadProfessionalHistory();return;}
    if(el.closest('[data-os-form=goal]')&&el.name==='scope'){const form=el.closest('form'),company=el.value==='company';form.querySelector('[data-os-goal-professional]').hidden=company;form.elements.professional_id.disabled=company;form.elements.professional_id.required=!company;return;}
    if(el.closest('[data-os-form=goal]')&&el.name==='metric'){const form=el.closest('form'),moneyGoal=el.value==='revenue_cents';form.querySelector('label[for=osField-target]').textContent='Objetivo'+(state.dialog?.monthly?' mensal':'')+(moneyGoal?' (R$)':'');setNumericMode(form.elements.target,el.value,{min:1,max:1000000000,scale:moneyGoal?2:0});return;}
    if(el.dataset.osServiceToggle){const s=(state.data.services||[]).find(s=>s.id===el.dataset.osServiceToggle);if(s)await runInline(()=>request(apiPath('/services/'+encodeURIComponent(s.id)),'PUT',{...serviceRecordPayload(s),active:el.checked}),el.checked?'Serviço ativado.':'Serviço inativado.');return;}
    if(state.dialog?.type==='service'&&el.name==='addon_ids'){const form=el.closest('form'),checked=form.querySelectorAll('[name=addon_ids]:checked');if(checked.length>5){el.checked=false;toast('Selecione até 5 adicionais.',true);}const duration=form.elements['addon_duration_'+el.value];if(duration)duration.disabled=!el.checked;return;}
    if(state.dialog?.type==='package'&&el.name==='service_ids'){updatePackageSummary();return;}
    if(state.dialog?.type==='package'&&el.name==='custom_duration'){syncPackageDurationMode();return;}
    if(el.id==='osCustomerStatus'){state.filters.customerStatus=el.value;state.filters.customerOffset=0;await open('clientes',{keepScroll:true});return;}
    if(el.id==='osCouponStatus'){state.filters.couponStatus=el.value;state.filters.loyaltyOffset=0;await open('fidelizacao',{keepScroll:true});return;}
    if(el.name==='reward_type'&&el.closest('[data-os-form=loyalty-inline]')){const free=el.value==='free_service',value=$('osLoyaltyRewardValue'),service=$('osLoyaltyRewardService'),input=value.querySelector('input'),select=service.querySelector('select');value.classList.toggle('hidden',free);service.classList.toggle('hidden',!free);input.required=!free;input.disabled=free;select.required=free;select.disabled=!free;setNumericMode(input,el.value,{min:el.value==='fixed'?1:0.01,max:el.value==='fixed'?1000000000:100});value.querySelector('label').textContent=el.value==='fixed'?'Valor do cupom (R$)':'Valor do cupom (%)';return;}
    if(state.dialog?.type==='complete-appointment'&&['coupon_id','final_total'].includes(el.name)){updateCompletionSummary();return;}
    if(state.dialog?.type==='professional'&&
       (el.name==='commission_mode'||el.name==='service_ids'||el.name.startsWith('commission_kind_'))){
      syncProfessionalCommissionControls(el.closest('form'));return;
    }
    if(state.dialog?.type==='service'&&el.name==='price_mode'){updateServicePriceMode(el.closest('form'));return;}
    if(el.id==='osAdvanceStatus'){state.filters.advanceStatus=el.value;state.filters.advanceOffset=0;await open('financeiro',{keepScroll:true});return;}
    if(el.id==='osFinanceKind'){state.filters.financeKind=el.value;state.filters.financeOffset=0;state.filters.financeFiltersOpen=true;await open('financeiro',{keepScroll:true});return;}
    if(el.id==='osFinanceStatus'){state.filters.financeStatus=el.value;state.filters.financeOffset=0;state.filters.financeFiltersOpen=true;await open('financeiro',{keepScroll:true});return;}
    if(state.dialog?.type==='appointment-extras'&&el.dataset.osExtraService){const d=state.dialog;if(d.pendingExtras)return;const selected=el.form.querySelectorAll('[name=extra_service_ids]:checked');if(selected.length>d.extraLimit){el.checked=false;toast('Este atendimento permite mais '+d.extraLimit+' serviços.',true);return;}resetExtrasPreview(d);return;}
    if(state.dialog?.type==='appointment-extras'&&el.name==='actual_duration_min'){resetExtrasPreview();return;}
    if(el.dataset.osBookAddon){const d=state.dialog;if(d?.type!=='appointment'||d.holdForm||d.manualOperation)return;const id=el.dataset.osBookAddon;if(!d.addons?.some(item=>item.addon_service_id===id))return;d.addonIds=el.checked?[...new Set([...d.addonIds,id])]:d.addonIds.filter(value=>value!==id);await updateAvailability();return;}
    if(state.dialog?.type==='appointment'&&el.name==='booking_mode'&&el.value==='fixed'){
      const d=state.dialog,form=el.form;
      await showFixedClient({customers:d.customers,services:d.services,professionals:d.professionals,
        customerName:form.elements.customer_name.value,customerPhone:form.elements.customer_phone.value,
        serviceIds:[...d.serviceIds],professionalId:form.elements.professional_id.value,day:form.elements.day.value});return;
    }
    if(state.dialog?.type==='fixed-client'&&el.name==='booking_mode'){
      if(el.value==='fixed')return;
      const d=state.dialog,form=el.form;
      await showAppointment({bookingMode:el.value,customerName:form.elements.customer_name.value,
        customerPhone:form.elements.customer_phone.value,serviceIds:[...d.serviceIds],
        professionalId:form.elements.professional_id.value,day:form.elements.first_date.value});return;
    }
    if(state.dialog?.type==='appointment'&&['booking_mode','walk_in'].includes(el.name)){syncBookingMode();await updateAvailability();return;}
    if(state.dialog?.type==='fixed-client'&&el.closest('[data-os-form=fixed-client]')){
      const form=el.closest('form');
      if(el.name==='weekday'){
        const first=form.elements.first_date.value||dateKey(),base=state.dialog.editSeriesId?dateKey():first;
        const current=(dayDate(base).getUTCDay()+6)%7;
        form.elements.first_date.value=shiftDay(base,(Number(el.value)-current+7)%7);
      }else if(el.name==='first_date'&&el.value){
        form.elements.weekday.value=(dayDate(el.value).getUTCDay()+6)%7;
      }
      resetFixedPreview();return;
    }
    if(state.dialog?.type==='appointment'&&['professional_id','day'].includes(el.name)){await updateAvailability();return;}
    if(state.dialog?.type==='block'&&el.name==='repeat'){$('osBlockRepeat').classList.toggle('hidden',!el.checked);$('osField-until_date').required=el.checked;return;}
    if(state.dialog?.type==='block'&&el.name==='frequency'){$('osBlockWeekdays').classList.toggle('hidden',el.value!=='weekdays');return;}
    if(state.dialog?.type==='block'&&el.name==='block_type'){$('osBlockHours').classList.toggle('hidden',el.value==='days');$('osBlockDays').classList.toggle('hidden',el.value!=='days');const form=el.closest('form');['day','start_time','end_time'].forEach(name=>form.elements[name].required=el.value==='hours');form.elements.until_date.required=el.value==='hours'&&form.elements.repeat.checked;$('osBlockDays').querySelectorAll('input').forEach(i=>i.required=el.value==='days');}
  }
  function handleInput(event){if(!state.mounted)return;const el=event.target;markCustomerBirthChanged(el);markSiteAddressEdit(el);if(state.dialog?.type==='service'&&el.name==='duration_min')syncServiceDurationPresets(el.closest('form'));if(el.matches?.('input.os-color-native'))syncColorPicker(el);if(el.id==='osAgendaSearch'&&!state.busy){state.filters.agendaQuery=el.value;state.filters.agendaOffset=0;state.agendaRead=(state.agendaRead||0)+1;state.agendaFocusRead=(state.agendaFocusRead||0)+1;clearTimeout(state.agendaSearchTimer);state.agendaSearchTimer=setTimeout(()=>void applyAgendaFilters(),250);return;}if(el.closest('#osSiteForm'))el.closest('form').dataset.dirty='true';if(el.closest('#osSiteForm')&&!state.busy&&siteCanEdit()&&!event.isComposing){if(el.name==='slug')queueSiteSlugCheck(el);if(el.name==='postal_code')queueSiteCepLookup(el);if(el.name==='state')el.value=el.value.toUpperCase();}if(el.id==='osHelpSearch'){state.filters.helpQuery=el.value;renderHelpResults();}if(el.id==='osCustomerSearch'){state.filters.customerQuery=el.value;state.filters.customerOffset=0;state.customerRead=(state.customerRead||0)+1;clearTimeout(state.searchTimer);clearTimeout(state.agendaSearchTimer);const c=context();state.searchTimer=setTimeout(()=>loadCustomers(c).catch(ex=>{if(current(c))bodyHTML(errorHTML(ex));}),250);}if(el.id==='osResourceSearch')renderServices(el.value);if(state.dialog?.type==='appointment-extras'&&el.name==='actual_duration_min')resetExtrasPreview();if(state.dialog?.type==='appointment'&&el.name==='final_total')state.dialog.finalTotalTouched=true;if(el.id==='osBookingCustomer')filterBookingCustomers(el.value);if(el.id==='osFixedCustomer')filterFixedCustomers(el.value);if(state.dialog?.type==='fixed-client'&&el.closest('[data-os-form=fixed-client]'))resetFixedPreview();if(state.dialog?.type==='appointment'&&el.name==='customer_phone'&&state.dialog.customerId){$('osBookingCustomerHelp').textContent='Ao confirmar, o WhatsApp informado será salvo no cadastro deste cliente.';}if(state.dialog?.type==='package'&&el.name==='price')state.dialog.priceTouched=true;
    if(el.matches?.('[data-os-start-time-input]')&&state.dialog?.type==='hours'&&writable()&&!state.busy){
      const form=el.closest('[data-os-form="hours-start-policy"]'),draft=hoursStartDraft(state.dialog,Number(form?.dataset.weekday));
      if(draft)draft.pendingTime=el.value;
    }
    if(state.dialog?.type==='package'&&el.name==='duration_override_min')updatePackageSummary();
    if(state.dialog?.type==='complete-appointment'&&el.name==='final_total')updateCompletionSummary();}
  function handleKeydown(event){if(!state.mounted)return;
    const homePoint=event.target.closest?.('#osHomeTrend [data-os-chart-key]');
    if(homePoint&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      const columns=[...$('osHomeTrend').querySelectorAll('[data-os-chart-key]')];
      const index=columns.indexOf(homePoint),last=columns.length-1;
      const next=event.key==='Home'?0:event.key==='End'?last:Math.max(0,Math.min(last,index+(event.key==='ArrowRight'?1:-1)));
      event.preventDefault();selectHomeChartPoint(columns[next],true);return;
    }
    const performanceTab=event.target.closest?.('[data-os-performance-tab]');
    if(performanceTab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)&&!state.busy){const tabs=[...document.querySelectorAll('[data-os-performance-tab]')],index=tabs.indexOf(performanceTab),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;event.preventDefault();selectPerformanceTab(tabs[next].dataset.osPerformanceTab,true);return;}
    const point=event.target.closest?.('[data-os-performance-point]');
    if(point&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)&&!state.busy){
      const index=Number(point.dataset.osPerformancePoint),last=performanceSeries().length-1,next=event.key==='Home'?0:event.key==='End'?last:Math.max(0,Math.min(last,index+(event.key==='ArrowRight'?1:-1)));
      event.preventDefault();selectPerformancePoint(next,true);return;
    }
    const appTab=event.target.closest?.('[data-os-app-tab]');
    if(appTab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      const tabs=[...$('osAppDock').querySelectorAll('[data-os-app-tab]:not(:disabled)')],index=tabs.indexOf(appTab),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      if(index>=0&&tabs[next]){event.preventDefault();tabs[next].focus();tabs[next].click();}return;
    }
    const serviceGrip=event.target.closest?.('[data-os-service-drag]');if(serviceGrip&&['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();void moveService(serviceGrip.dataset.osServiceDrag,event.key==='ArrowUp'?-1:1);return;}if(event.key==='Escape'){if(state.notificationsOpen){event.preventDefault();closeNotifications(true);}if($('appView').classList.contains('os-menu-open')){event.preventDefault();drawer(false);}if(!$('osThemeMenu').classList.contains('hidden')){$('osThemeMenu').classList.add('hidden');$('osThemeToggle').setAttribute('aria-expanded','false');$('osThemeToggle').focus();}}
    if(event.key==='Enter'&&!event.isComposing&&event.target.matches?.('[data-os-start-time-input]')){event.preventDefault();addHoursStartTime(event.target.closest('form'));return;}
    const agendaTab=event.target.closest('[data-os-day],[data-os-agenda-mode],[data-os-agenda-period]');
    if(agendaTab&&['ArrowLeft','ArrowRight','Home','End'].includes(event.key)&&!state.busy){const tabs=[...agendaTab.closest('[role=tablist]').querySelectorAll('[role=tab]')],index=tabs.indexOf(agendaTab),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;event.preventDefault();tabs[next].focus();tabs[next].click();return;}
    const row=event.target.closest('[data-os-appointment][role=button]');if(row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();void showAppointmentDetail(row.dataset.osAppointment,{trigger:row});}
    if(event.key==='Tab'&&$('appView').classList.contains('os-menu-open')){const focusable=[...$('osSidebar').querySelectorAll('button:not([disabled]),select:not([disabled]),a[href]')].filter(el=>el.getClientRects().length);const first=focusable[0],last=focusable[focusable.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
  }
  function onDialogCancel(event){
    event.preventDefault();
    if(state.busy)return;
    if($('osAppointmentMore')?.getAttribute('aria-expanded')==='true'){setAppointmentOptions(false,true);return;}
    closeDialog();
  }
  function sync(bridge={}){
    const oldIdentity=state.bridge.user?.email+'|'+state.bridge.user?.role,oldTenant=state.tenantId,oldReady=state.bridge.businessContextReady,oldError=businessAccessError(),oldProfile=tenant()?.workspace_profile;state.bridge={...state.bridge,...bridge};
    state.tenantId=accountTenantId();
    const nextProfile=tenant()?.workspace_profile,profileChanged=!!nextProfile&&oldProfile!==nextProfile;
    const identity=state.bridge.user?.email+'|'+state.bridge.user?.role;if(oldIdentity!==identity||(oldTenant&&oldTenant!==state.tenantId)||(oldProfile&&profileChanged)){window.OrionSubscriptionSentMessages?.clearContext();window.OrionSubscriptionGroupAdd?.clearContext();window.OrionSubscriptionGroupContacts?.clearContext();window.OrionSubscriptionDirectMessages?.clearContext();window.OrionSubscriptionBulkMessages?.clearContext();window.OrionSubscriptionScheduledMessages?.clearContext();window.OrionSubscriptionCampaigns?.clearContext();window.OrionSubscriptionDiagnostics?.clearContext();}if(oldIdentity!==identity||oldTenant!==state.tenantId||profileChanged){stopHomeLive();window.OrionSubscriptionDashboard?.unmount();state.workspaceProfile=nextProfile||null;clearTimeout(state.permissionExpiryTimer);state.permissionExpiryTimer=null;state.permissionExpiryConsumed=null;state.entitlements=null;state.permissionPending=null;state.permissionRead=(state.permissionRead||0)+1;state.epoch++;state.imageUploading=false;clearSiteLookups();clearTimeout(state.searchTimer);clearTimeout(state.agendaSearchTimer);state.data={};state.filters=defaultFilters();state.appShortcutDraft=null;applyAppLayout();closeNotifications();$('osNotificationsPanel')?.replaceChildren();$('osNotificationDot')?.classList.add('hidden');state.filters.agendaDay=dateKey();resetPeriod();closeDialog(true);}
    if(!state.mounted)return;renderSidebar();state.bridge.onWorkspaceProfile?.();if(oldIdentity!==identity||oldTenant!==state.tenantId||profileChanged||oldReady!==state.bridge.businessContextReady||oldError!==businessAccessError())void open(state.pendingRoute||state.route,{replace:true,force:true});
  }
  async function refreshLiveRoute(){
    if(state.mounted&&tenant()&&document.visibilityState!=='hidden')await refreshPermissions({reconcile:true});
    if(state.mounted&&homePreviewEnabled()&&state.route==='dashboard'){if(document.visibilityState!=='hidden'&&!state.busy)await loadHomePreview(context(),dateKey(),{background:true});return;}
    if(!state.mounted||!tenant()||state.busy||state.dialog||document.visibilityState==='hidden')return;
    if(document.activeElement?.matches?.('input,select,textarea,[contenteditable=true]'))return;
    const c=context();
    if(state.route==='agenda'){
      await loadAgenda(c,{catalogue:false});
      if(current(c))void loadGoogle({handleReturn:false});
    }else if(['dashboard','financeiro','desempenho'].includes(state.route)){
      await loadRoute();
    }
  }
  function refreshAccess(event){if(state.mounted&&homePreviewEnabled()&&state.route==='dashboard'&&document.visibilityState!=='hidden')void loadHomePreview(context(),dateKey(),{background:true,force:true}).catch(()=>{});if(state.mounted&&tenant()&&document.visibilityState!=='hidden')void refreshPermissions({reconcile:event?.detail?.preservePage!==true});}
  function mount(bridge){
    if(state.mounted){sync(bridge);return;}
    state.bridge=bridge||{};state.mounted=true;state.epoch++;state.load++;state.tenantId=accountTenantId();state.workspaceProfile=tenant()?.workspace_profile||null;state.filters.agendaDay=dateKey();resetPeriod();
    buildShell();if(typeof window.BroadcastChannel==='function'){try{state.permissionChannel=new BroadcastChannel('orion-plan-access');state.permissionChannel.onmessage=event=>{if(event.data?.type!=='changed'||event.data.tenantId!==state.tenantId||!tenant())return;const epoch=state.epoch,id=state.tenantId,refresh=()=>{if(state.mounted&&state.epoch===epoch&&state.tenantId===id)void refreshPermissions({reconcile:true});};if(state.permissionPending)void state.permissionPending.then(refresh);else refresh();};}catch{state.permissionChannel=null;}}state.permissionObserver=new MutationObserver(()=>applyPermissionDOM());state.permissionObserver.observe($('page-suite'),{childList:true,subtree:true});window.addEventListener('focus',refreshAccess);document.addEventListener('visibilitychange',refreshAccess);window.addEventListener('orion:plan-changed',refreshAccess);clearInterval(state.liveRefreshTimer);state.liveRefreshTimer=setInterval(()=>{void refreshLiveRoute().catch(()=>{});},30000);document.addEventListener('click',handleClick);document.addEventListener('change',handleChange);document.addEventListener('input',handleInput);document.addEventListener('focusout',handleSiteFocusOut);document.addEventListener('compositionend',handleSiteCompositionEnd);document.addEventListener('submit',submitForm);document.addEventListener('keydown',handleKeydown);document.addEventListener('pointerdown',handleServicePointerDown);document.addEventListener('pointermove',handleServicePointerMove);document.addEventListener('pointerup',handleServicePointerUp);document.addEventListener('pointercancel',clearServiceDrag);$('osDialog').addEventListener('cancel',onDialogCancel);
    state.observer=new MutationObserver(()=>applyThemeState());state.observer.observe(document.documentElement,{attributes:true,attributeFilter:['data-theme','data-theme-preference']});
    const desired=bridge?.initialRoute||'dashboard';void open(appPages.has(desired)?desired:'dashboard');
  }
  function destroy(){stopHomeLive();$('appView')?.classList.remove('os-dental-workspace');window.OrionDentalDirectory?.unmount();window.OrionDentalClinic?.unmount?.();window.OrionSubscriptionGroupAdd?.clearContext();window.OrionSubscriptionGroupContacts?.clearContext();window.OrionSubscriptionDirectMessages?.clearContext();window.OrionSubscriptionBulkMessages?.clearContext();window.OrionSubscriptionScheduledMessages?.clearContext();window.OrionSubscriptionCampaigns?.clearContext();window.OrionSubscriptionDiagnostics?.clearContext();window.OrionSubscriptionSentMessages?.clearContext();window.OrionSubscriptionGroupAdd?.unmount();window.OrionSubscriptionGroupContacts?.unmount();window.OrionSubscriptionDirectMessages?.unmount();window.OrionSubscriptionBulkMessages?.unmount();window.OrionSubscriptionScheduledMessages?.unmount();window.OrionSubscriptionCampaigns?.unmount();window.OrionSubscriptionDiagnostics?.unmount();window.OrionSubscriptionSentMessages?.unmount();window.OrionSubscriptionTemplates?.unmount();window.OrionSubscriptionChatbotTest?.unmount();window.OrionSubscriptionChatbot?.unmount();window.OrionSubscriptionDevices?.unmount();window.OrionSubscriptionDashboard?.unmount();window.OrionChannels?.unmount();window.OrionAutomations?.unmount();window.OrionCommunication?.unmount();window.OrionInbox?.unmount();state.mounted=false;clearInterval(state.clockTimer);clearInterval(state.liveRefreshTimer);state.epoch++;state.load++;state.busy=false;state.imageUploading=false;clearTimeout(state.searchTimer);clearTimeout(state.agendaSearchTimer);clearSiteLookups();closeDialog(true);state.data={};state.bridge={};state.tenantId='';state.workspaceProfile=null;state.filters=defaultFilters();state.appShortcutDraft=null;applyAppLayout();closeNotifications();$('osNotificationsPanel')?.replaceChildren();$('osNotificationDot')?.classList.add('hidden');clearTimeout(state.permissionExpiryTimer);state.permissionExpiryTimer=null;if(state.permissionChannel){state.permissionChannel.onmessage=null;state.permissionChannel.close();state.permissionChannel=null;}state.permissionObserver?.disconnect();state.permissionObserver=null;window.removeEventListener('focus',refreshAccess);document.removeEventListener('visibilitychange',refreshAccess);window.removeEventListener('orion:plan-changed',refreshAccess);state.entitlements=null;state.permissionPending=null;state.observer?.disconnect();state.observer=null;document.removeEventListener('click',handleClick);document.removeEventListener('change',handleChange);document.removeEventListener('input',handleInput);document.removeEventListener('focusout',handleSiteFocusOut);document.removeEventListener('compositionend',handleSiteCompositionEnd);document.removeEventListener('submit',submitForm);document.removeEventListener('keydown',handleKeydown);document.removeEventListener('pointerdown',handleServicePointerDown);document.removeEventListener('pointermove',handleServicePointerMove);document.removeEventListener('pointerup',handleServicePointerUp);document.removeEventListener('pointercancel',clearServiceDrag);clearServiceDrag();$('osDialog')?.removeEventListener('cancel',onDialogCancel);$('page-suite')?.replaceChildren();$('osNav')?.replaceChildren();$('osSidebarContext')?.replaceChildren();$('osToastRegion')?.replaceChildren();$('appView')?.classList.remove('os-menu-open','os-tablet-mode');}
  window.OrionSuite=Object.freeze({mount,sync,destroy,open,legacyOpened,workspaceProfile});
})();
