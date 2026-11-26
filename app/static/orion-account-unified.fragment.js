  // Personal preferences are shared across business niches, not a business setting.
  const accountSections=[
    ['profile','user','Dados pessoais','Nome e e-mail da conta'],
    ['business','building','Dados da empresa','Nome e WhatsApp comercial'],
    ['security','shield','Segurança','Senha e proteção do acesso'],
    ['appearance','sun','Aparência','Tema, layout e atalhos']
  ];
  const personalAccount=()=>state.bridge.user?.role==='member';
  const personalSettingsTab=tab=>['appearance','account'].includes(tab);
  const businessSettingsAvailable=()=>!subscriptionWorkspace()&&!affiliateWorkspace()&&settingsSections.some(([key])=>!personalSettingsTab(key)&&settingsTabAllowed(key));
  const appearanceView=()=>state.route==='member'&&personalAccount()&&state.filters.accountTab==='appearance'||state.route==='configuracoes'&&state.filters.settingsTab==='appearance';
  const appearanceRoot=()=>state.route==='member'&&personalAccount()?$('osMemberAppearance'):$('osPageBody');
  function refreshAppearanceView(){
    if(state.route==='member'&&personalAccount())renderMemberAccount();
    else renderSettings();
  }
  function accountSection(tab){
    if(!personalAccount()||!accountSections.some(([key])=>key===tab))return;
    state.filters.accountTab=tab;
    if(state.route==='member'){renderMemberAccount();document.querySelector('#osMemberAccount [data-os-account-tab="'+tab+'"]')?.focus({preventScroll:true});}
    else void open('member');
  }
  function renderMemberAccount(){
    if(!personalAccount()||state.route!=='member')return;
    const root=$('page-member'),overview=root?.querySelector('.member-overview'),settings=root?.querySelector('.member-settings');
    if(!root||!overview||!settings)return;
    const business=settings.querySelector('#memberBusinessForm')?.closest('.member-settings-card');
    const security=settings.querySelector('#memberPasswordForm')?.closest('.member-settings-card');
    if(!business||!security)return;
    let layout=$('osMemberAccount');
    if(!layout){
      layout=document.createElement('div');layout.id='osMemberAccount';layout.className='os-account-layout os-settings-layout';
      layout.innerHTML='<nav class="os-settings-menu os-account-menu" aria-label="Seções de Minha conta"></nav><section class="os-settings-panel os-account-panel" aria-labelledby="osAccountSectionTitle"><div class="os-account-section-heading"><h2 id="osAccountSectionTitle"></h2><p id="osAccountSectionDescription"></p></div><div id="osAccountSections"></div><div class="os-account-footer">'+button('Sair da conta','logout','os-outline','logout')+'</div></section>';
      overview.before(layout);
      const content=layout.querySelector('#osAccountSections');
      content.append(overview,settings);
      const appearance=document.createElement('div');appearance.id='osMemberAppearance';content.append(appearance);
      overview.classList.add('os-card','os-account-overview');business.classList.add('os-card');security.classList.add('os-card');
      settings.setAttribute('aria-label','Dados da conta e empresa');
      business.querySelector('.member-settings-actions button')?.classList.add('os-button');
      security.querySelector('.member-settings-actions button')?.classList.add('os-button');
      const logo=overview.querySelector('.member-overview-top');if(logo)logo.hidden=true;
    }
    root.classList.add('os-member-account');
    const heading=root.querySelector('#memberWelcome');if(heading)heading.textContent='Minha conta';
    const breadcrumb=root.querySelector('.header .eyebrow');if(breadcrumb)breadcrumb.textContent='Orion / Minha conta';
    const subtitle=root.querySelector('.header .subhead');if(subtitle)subtitle.textContent='Gerencie seus dados, segurança e preferências do painel.';
    const profileHeading=$('memberProfileHeading');if(profileHeading)profileHeading.textContent='Informações pessoais';
    const tab=accountSections.some(([key])=>key===state.filters.accountTab)?state.filters.accountTab:'profile';
    state.filters.accountTab=tab;
    const definition=accountSections.find(([key])=>key===tab);
    $('osAccountSectionTitle').textContent=definition[2];
    $('osAccountSectionDescription').textContent=definition[3];
    const menu=layout.querySelector('.os-account-menu');
    if(!menu.firstElementChild)menu.innerHTML=accountSections.map(([key,symbol,label,description])=>'<button type="button" data-os-account-tab="'+key+'" aria-current="false"><span class="os-settings-icon">'+icon(symbol)+'</span><span><strong>'+label+'</strong><small>'+description+'</small></span></button>').join('');
    menu.querySelectorAll('[data-os-account-tab]').forEach(el=>el.setAttribute('aria-current',String(el.dataset.osAccountTab===tab)));
    overview.hidden=tab!=='profile';
    settings.hidden=tab!=='business'&&tab!=='security';
    business.hidden=tab!=='business';security.hidden=tab!=='security';
    const appearance=$('osMemberAppearance');appearance.hidden=tab!=='appearance';
    if(tab==='appearance')appearance.innerHTML=appearancePanel();else appearance.replaceChildren();
    window.OrionFields?.refresh(layout);
  }
