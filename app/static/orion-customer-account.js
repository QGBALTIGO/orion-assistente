/* Orion public-site customer account. Native components, no framework, no
   storage of phones or private appointment details in localStorage or URLs. */
(() => {
  "use strict";
  const slug = location.pathname.match(/^\/s\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/)?.[1];
  const preview = /^\/app\/site-preview\//.test(location.pathname);
  const trigger = document.getElementById("customer-account-trigger");
  const dialog = document.getElementById("customer-account-dialog");
  const root = document.getElementById("customer-account-body");
  const tabs = document.getElementById("customer-account-tabs");
  const feedback = document.getElementById("customer-account-feedback");
  const closeButton = document.getElementById("customer-account-close");
  if (!trigger || !dialog || !root || preview || !slug) {
    if (trigger) trigger.hidden = true;
    return;
  }
  const prefix = "/api/v1/public/sites/" + encodeURIComponent(slug) + "/account";
  const fields = window.OrionFields;
  const state = {
    available: false, authenticated: false, profile: null, loading: false,
    stage: "phone", loginPhone: "", tab: "upcoming", selected: null,
    lists: {upcoming:null,history:null}, zone: "America/Sao_Paulo",
    editingPhone: false, requestNumber: 0
  };

  function el(tag, className="", text="") {
    const element=document.createElement(tag);
    if(className)element.className=className;
    if(text)element.textContent=text;
    return element;
  }
  function button(label, className, onClick) {
    const b=el("button",className,label);b.type="button";
    if(onClick)b.addEventListener("click",onClick);
    return b;
  }
  function notice(message="",isError=false) {
    feedback.textContent=message;
    if(message)feedback.dataset.kind=isError?"error":"info";
    else delete feedback.dataset.kind;
  }
  function body(...items) { root.replaceChildren(...items); }
  function labelField(label,name,type="text",value="",required=false) {
    const wrapper=el("label","customer-account-field");
    const span=el("span","",label);const input=el("input");
    input.name=name;input.type=type;input.value=value??"";
    input.required=required;input.autocomplete="off";
    if(type==="tel"){input.inputMode="tel";input.placeholder="(00) 00000-0000";input.dataset.orionField="phone";input.autocomplete="tel-national";}
    if(type==="date"){input.min="1900-01-01";input.max=new Date().toISOString().slice(0,10);}
    wrapper.append(span,input);return {wrapper,input};
  }
  function fieldSet(form){fields?.refresh?.(form);}
  function sourceError(error) {
    const detail=error?.message||"Não foi possível concluir. Tente novamente.";
    return detail.length<190?detail:"Não foi possível concluir. Tente novamente.";
  }
  async function api(path="",method="GET",data) {
    const init={method,credentials:"same-origin",cache:"no-store",referrerPolicy:"no-referrer",
      headers:{"Accept":"application/json"}};
    if(data!==undefined){init.headers["Content-Type"]="application/json";init.body=JSON.stringify(data);}
    const res=await fetch(prefix+path,init);
    const value=await res.json().catch(()=>({}));
    if(!res.ok){
      const detail=value.detail;
      const error=new Error(typeof detail==="string"?detail:
        "Não foi possível concluir agora. Tente novamente.");
      error.status=res.status;
      throw error;
    }
    return value;
  }
  function clearPrivateState(){
    state.authenticated=false;state.profile=null;state.lists={upcoming:null,history:null};
    state.selected=null;state.loginPhone="";state.stage="phone";state.editingPhone=false;
    state.requestNumber++;
    tabs.hidden=true;root.replaceChildren();notice();
  }
  async function withBusy(control,task){
    if(state.loading)return;
    state.loading=true;if(control)control.disabled=true;
    notice();
    try{await task();}
    catch(error){
      if(error.status===401){clearPrivateState();await loadState();return;}
      notice(sourceError(error),true);
    }finally{
      state.loading=false;if(control&&control.isConnected)control.disabled=false;
    }
  }
  function mark(elm,status){
    elm.textContent=statusLabel(status);elm.dataset.status=status;
  }
  function statusLabel(s){
    return ({confirmed:"Confirmado",completed:"Concluído",cancelled:"Cancelado",
      no_show:"Não compareceu",expired_hold:"Reserva expirada"})[s]||"Atualizado";
  }
  function localDate(value,style="medium"){
    try{return new Intl.DateTimeFormat("pt-BR",
      {timeZone:state.zone,dateStyle:style}).format(new Date(value));}
    catch{return "Data não disponível";}
  }
  function localTime(value){
    try{return new Intl.DateTimeFormat("pt-BR",{timeZone:state.zone,
      hour:"2-digit",minute:"2-digit"}).format(new Date(value));}
    catch{return "—";}
  }
  function money(cents,mode){
    const label=new Intl.NumberFormat("pt-BR",{style:"currency",currency:"BRL"}).format(Number(cents||0)/100);
    return (mode==="from"?"A partir de ":"")+label;
  }
  function icon(symbol){
    const span=el("span","customer-account-auth-mark");
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox","0 0 24 24");svg.setAttribute("fill","none");
    svg.setAttribute("stroke","currentColor");svg.setAttribute("stroke-width","1.8");
    svg.setAttribute("stroke-linecap","round");svg.setAttribute("stroke-linejoin","round");
    const path=document.createElementNS("http://www.w3.org/2000/svg","path");
    path.setAttribute("d",symbol==="calendar"?
      "M7 2v4m10-4v4M3 9h18M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z":
      "M12 21a9 9 0 1 0-9-9m9 9a9 9 0 0 0 9-9M9 9a3 3 0 0 1 6 0v2H9V9Zm-2 9v-3a5 5 0 0 1 10 0v3");
    svg.append(path);span.append(svg);return span;
  }

  function renderLogin(){
    tabs.hidden=true;state.selected=null;
    const wrapper=el("section","customer-account-auth");
    wrapper.append(icon("account"));
    if(!state.available){
      wrapper.append(el("h3","","Sua conta em preparação"),
        el("p","customer-account-description",
          "A área do cliente ainda não foi ativada neste estabelecimento. O agendamento pelo site continua funcionando."),
        button("Voltar ao site","outline",()=>dialog.close()));
      body(wrapper);return;
    }
    const needsLink=state.stage==="link";
    wrapper.append(
      el("h3","",needsLink?"Acesse pelo seu agendamento":"Seus horários, em um só lugar"),
      el("p","customer-account-description",needsLink?
        "Neste aparelho ainda não há uma conta reconhecida. Para não mostrar os dados de outra pessoa, cole uma única vez o link exclusivo de um agendamento que você já recebeu da empresa.":
        "Informe o mesmo telefone usado no agendamento. Se já acessou sua conta neste aparelho, a entrada é imediata, sem SMS ou senha.")
    );
    const form=el("form","customer-account-auth-form");
    if(!needsLink){
      const phone=labelField("Telefone com DDD","phone","tel",state.loginPhone,true);
      form.append(phone.wrapper);
      const actions=el("div","customer-account-actions");
      const submit=el("button","primary","Entrar");submit.type="submit";
      actions.append(submit);form.append(actions);
      form.addEventListener("submit",event=>{
        event.preventDefault();
        if(!form.reportValidity())return;
        void withBusy(submit,async()=>{
          const normalized=fields.phoneValue(phone.input.value);
          const result=await api("/enter","POST",{phone:normalized});
          state.loginPhone=normalized;
          if(result.authenticated){
            state.authenticated=true;state.profile=result.profile;
            state.tab="upcoming";state.lists={upcoming:null,history:null};
            await openTab("upcoming");return;
          }
          state.stage="link";renderLogin();
        });
      });
    }else{
      const input=labelField("Link do seu agendamento","booking_link","text","",true);
      input.input.placeholder="https://orionagenda.pro/a#...";
      input.input.autocomplete="off";
      input.input.maxLength=650;input.input.spellcheck=false;
      form.append(input.wrapper);
      const actions=el("div","customer-account-actions");
      const back=button("Voltar","outline",()=>{state.stage="phone";renderLogin();notice();});
      const submit=el("button","primary","Confirmar acesso");submit.type="submit";
      actions.append(back,submit);form.append(actions);
      form.addEventListener("submit",event=>{
        event.preventDefault();
        if(!form.reportValidity())return;
        void withBusy(submit,async()=>{
          const result=await api("/link-login","POST",{
            phone:state.loginPhone,booking_link:input.input.value.trim()
          });
          state.authenticated=true;state.profile=result.profile;
          state.tab="upcoming";state.lists={upcoming:null,history:null};
          await openTab("upcoming");
          notice("Acesso confirmado. Da próxima vez, neste aparelho, basta entrar com o telefone.");
        });
      });
    }
    wrapper.append(form,el("p","customer-account-auth-note",needsLink?
      "O link é o mesmo que abre o gerenciamento do seu horário. Não recebeu um link? Peça à empresa. Não precisamos de código por SMS.":
      "Por privacidade, apenas conhecer o telefone de alguém não dá acesso ao histórico dessa pessoa."));
    body(wrapper);fieldSet(form);
    form.querySelector("input")?.focus({preventScroll:true});
  }

  function renderTabs(){
    tabs.hidden=false;
    for(const item of tabs.querySelectorAll("[data-customer-tab]")){
      if(item.dataset.customerTab===state.tab)item.setAttribute("aria-current","page");
      else item.removeAttribute("aria-current");
    }
  }
  function welcome(){
    const wrap=el("div","customer-account-intro");
    const name=state.profile?.name||"Cliente";
    const avatar=el("span","customer-account-avatar",name.trim().charAt(0).toUpperCase()||"C");
    const content=el("div");
    content.append(el("strong","","Olá, "+name.trim().split(/\s+/)[0]+"!"),
      el("p","","Confira seus horários e mantenha seus dados atualizados."));
    wrap.append(avatar,content);return wrap;
  }
  function empty(text,description){
    const section=el("div","customer-account-empty");
    section.append(el("strong","",text),el("p","",description));return section;
  }
  function renderBooking(item){
    const card=el("article","customer-account-booking");
    const top=el("div","customer-account-booking-top");
    const name=el("strong","",item.services?.join(" + ")||"Atendimento");
    const status=el("span","customer-account-status");mark(status,item.status);
    top.append(name,status);
    const meta=el("div","customer-account-booking-meta");
    meta.append(el("span","customer-account-date",localDate(item.start_at)+" às "+localTime(item.start_at)));
    meta.append(el("span","","Com "+item.professional));
    meta.append(el("span","","Protocolo: "+item.protocol));
    const footer=el("div","customer-account-booking-footer");
    footer.append(el("strong","",money(item.price_cents,item.price_mode)));
    const actions=el("div","customer-account-actions");
    if(item.status==="confirmed"&&item.can_change){
      actions.append(button("Gerenciar","outline",()=>renderManage(item)));
    }else if(item.status==="confirmed"){
      actions.append(el("small","muted","Prazo de alterações encerrado"));
    }
    if(state.tab==="history")actions.append(button("Agendar novamente","outline",()=>{
      dialog.close();document.getElementById("hero-book")?.click();
    }));
    footer.append(actions);
    card.append(top,meta,footer);return card;
  }
  function renderBookingTab(){
    const record=state.lists[state.tab],section=el("section","customer-account-section");
    const upcoming=state.tab==="upcoming";
    section.append(welcome(),el("h3","",upcoming?"Meus agendamentos":"Histórico de atendimentos"),
      el("p","customer-account-description",upcoming?
        "Consulte os próximos horários e cancele ou remarque dentro do prazo do estabelecimento.":
        "Seus atendimentos anteriores neste estabelecimento, inclusive remarcações e cancelamentos."));
    const cards=el("div","customer-account-bookings");
    const items=record?.items||[];
    if(items.length)cards.replaceChildren(...items.map(renderBooking));
    else cards.append(empty(upcoming?"Nenhum horário futuro":"Nenhum atendimento anterior",
      upcoming?"Quando você agendar novamente, seu horário aparecerá aqui.":
        "Seus atendimentos concluídos e cancelados aparecerão aqui."));
    section.append(cards);
    if(record?.has_more){
      const more=button("Mostrar mais atendimentos","outline",()=>{
        withBusy(more,async()=>{
          const batch=await api("/appointments?kind="+state.tab+"&offset="+record.next_offset);
          state.zone=batch.timezone||state.zone;
          state.lists[state.tab]={items:[...record.items,...batch.items],
            has_more:batch.has_more,next_offset:batch.next_offset};
          renderBookingTab();
        });
      });
      more.style.marginTop="16px";section.append(more);
    }
    body(section);
  }
  async function openTab(tab){
    if(!state.authenticated)return renderLogin();
    state.tab=tab;state.selected=null;
    renderTabs();notice();
    if(tab==="profile"){renderProfile();return;}
    const version=++state.requestNumber;
    if(!state.lists[tab]){
      body(el("div","customer-account-empty","Carregando seus atendimentos…"));
      try{
        const result=await api("/appointments?kind="+tab+"&offset=0");
        if(version!==state.requestNumber||!dialog.open)return;
        state.zone=result.timezone||state.zone;
        state.lists[tab]=result;
      }catch(error){if(version===state.requestNumber){body(empty("Não foi possível carregar","Atualize o Orion e tente novamente."));notice(sourceError(error),true);}return;}
    }
    renderBookingTab();
  }

  function renderManage(item){
    state.selected=item;
    tabs.hidden=true;notice();
    const section=el("section","customer-account-section");
    const back=button("← Voltar aos agendamentos","customer-account-secondary",()=>openTab("upcoming"));
    section.append(back,el("h3","","Gerenciar atendimento"));
    const list=el("div","customer-account-bookings");
    const summary=renderBooking(item);
    summary.querySelector(".customer-account-actions")?.replaceChildren();
    list.append(summary);section.append(list);
    if(!item.can_change){
      section.append(el("p","customer-account-privacy",
        "O prazo para cancelar ou remarcar pelo site terminou. Entre em contato com o estabelecimento."));
      body(section);return;
    }
    const actions=el("div","customer-account-actions");
    actions.append(button("Remarcar","primary",()=>renderReschedule(item)),
      button("Cancelar agendamento","outline",()=>renderCancel(item)));
    section.append(actions);body(section);
  }
  function renderCancel(item){
    const section=el("section","customer-account-section");
    section.append(button("← Voltar","customer-account-secondary",()=>renderManage(item)));
    const panel=el("div","customer-account-danger");
    panel.append(el("h4","","Cancelar este agendamento?"),
      el("p","","O horário será liberado após a confirmação. Essa ação não pode ser desfeita."));
    const actions=el("div","customer-account-actions");
    const confirm=button("Sim, cancelar agendamento","primary",()=>{
      withBusy(confirm,async()=>{
        await api("/appointments/"+encodeURIComponent(item.id)+"/cancel","POST",
          {expected_updated_at:item.updated_at,idempotency_key:crypto.randomUUID()});
        state.lists={upcoming:null,history:null};
        await openTab("upcoming");notice("Agendamento cancelado com sucesso.");
      });
    });
    actions.append(button("Manter meu horário","outline",()=>renderManage(item)),confirm);
    panel.append(actions);section.append(panel);body(section);
  }
  function renderReschedule(item){
    const section=el("section","customer-account-section customer-account-reschedule");
    section.append(button("← Voltar","customer-account-secondary",()=>renderManage(item)),
      el("h3","","Escolha um novo horário"),
      el("p","customer-account-description",
        "Manteremos seu atendimento com o mesmo profissional e os mesmos serviços."));
    const form=el("div","customer-account-field");
    const day=labelField("Nova data","day","date","",true);
    const localNow=new Intl.DateTimeFormat("sv-SE",{timeZone:state.zone,
      year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
    day.input.min=localNow;form.append(day.wrapper);
    const slots=el("div","customer-account-slots");
    const info=el("p","customer-account-description",
      "Selecione uma data para conferir os horários disponíveis.");
    let chosen=null;let sequence=0;
    const controls=el("div","customer-account-actions");
    const save=button("Confirmar novo horário","primary",()=>{
      if(!chosen){notice("Selecione um dos horários disponíveis.",true);return;}
      withBusy(save,async()=>{
        await api("/appointments/"+encodeURIComponent(item.id)+"/reschedule","POST",
          {expected_updated_at:item.updated_at,idempotency_key:crypto.randomUUID(),start_at:chosen});
        state.lists={upcoming:null,history:null};
        await openTab("upcoming");notice("Horário remarcado com sucesso.");
      });
    });
    save.disabled=true;controls.append(save);
    day.input.addEventListener("change",async()=>{
      if(!day.input.value)return;
      const current=++sequence;chosen=null;save.disabled=true;
      slots.replaceChildren();info.textContent="Consultando horários…";notice();
      try{
        const result=await api("/appointments/"+encodeURIComponent(item.id)+
          "/slots?day="+encodeURIComponent(day.input.value));
        if(current!==sequence||!section.isConnected)return;
        if(!result.slots?.length){
          info.textContent="Não há horários disponíveis nesta data. Tente outro dia.";return;
        }
        info.textContent="Selecione um dos horários disponíveis:";
        slots.replaceChildren(...result.slots.map(start=>{
          const b=button(localTime(start),"customer-account-slot",()=>{
            chosen=start;save.disabled=false;
            for(const other of slots.children)other.setAttribute("aria-pressed",String(other===b));
          });
          b.setAttribute("aria-pressed","false");return b;
        }));
      }catch(error){if(current===sequence)info.textContent=sourceError(error);}
    });
    section.append(form,info,slots,controls);body(section);
  }

  function renderProfile(){
    tabs.hidden=false;renderTabs();
    if(state.editingPhone)return renderPhoneChange();
    const section=el("section","customer-account-section");
    section.append(welcome(),el("h3","","Meus dados"),
      el("p","customer-account-description",
        "Essas informações são compartilhadas apenas com este estabelecimento. Você pode atualizá-las quando precisar."));
    const form=el("form","customer-account-form");
    const user=state.profile;
    const specs=[
      ["Nome completo","name","text",user.name,true],
      ["Instagram","instagram","text",user.instagram||"",false],
      ["E-mail","email","email",user.email||"",false],
      ["Nascimento","birth_date","date",user.birth_date||"",false],
      ["Bairro","neighborhood","text",user.neighborhood||"",false],
      ["Cidade","city","text",user.city||"",false]
    ];
    for(const [title,name,type,value,required] of specs){
      const {wrapper,input}=labelField(title,name,type,value,required);
      if(name==="name")input.maxLength=120;
      if(name==="instagram"){input.maxLength=35;input.placeholder="@seuinstagram";}
      if(name==="email")input.maxLength=320;
      form.append(wrapper);
    }
    section.append(form);
    const phone=el("div","customer-account-phone-row");
    const phoneInfo=el("div");
    phoneInfo.append(el("span","customer-account-description","WhatsApp cadastrado"),
      el("strong","",fields?.phoneDisplay?.(user.whatsapp_e164)||user.whatsapp_e164));
    phone.append(phoneInfo,button("Corrigir telefone","outline",()=>{
      state.editingPhone=true;renderPhoneChange();
    }));
    section.append(phone);
    const actions=el("div","customer-account-profile-footer");
    actions.append(button("Sair da minha conta","customer-account-secondary",()=>{
      withBusy(null,async()=>{await api("/logout","POST");clearPrivateState();renderLogin();notice("Sessão encerrada.");});
    }));
    const submit=el("button","primary","Salvar alterações");
    actions.append(submit);section.append(actions,
      el("p","customer-account-privacy",
        "Informações administrativas, observações da equipe e registros financeiros internos não ficam disponíveis aqui."));
    form.addEventListener("submit",event=>{event.preventDefault();submit.click();});
    submit.addEventListener("click",()=>{
      withBusy(submit,async()=>{
        const data={expected_version:user.version};
        for(const field of specs){
          const name=field[1],value=form.elements[name].value.trim();
          const old=user[name]??"";
          if(value!==String(old))data[name]=value||null;
        }
        if(Object.keys(data).length===1){notice("Nenhuma alteração para salvar.");return;}
        const result=await api("/profile","PATCH",data);
        state.profile=result.profile;renderProfile();
        notice("Seus dados foram atualizados.");
      });
    });
    body(section);fieldSet(form);
  }
  function renderPhoneChange(){
    tabs.hidden=false;renderTabs();notice();
    const section=el("section","customer-account-section");
    section.append(button("← Voltar aos meus dados","customer-account-secondary",()=>{
      state.editingPhone=false;renderProfile();
    }),el("h3","","Corrigir telefone"),
      el("p","customer-account-description",
        "Para proteger o histórico e não associar seus horários ao número de outra pessoa, a mudança do WhatsApp deve ser conferida pela equipe do estabelecimento."),
      el("p","customer-account-privacy",
        "Informe à empresa seu número correto e, se tiver, o protocolo de um agendamento. Seus outros dados podem ser atualizados aqui normalmente."));
    body(section);
  }

  async function loadState(){
    const requestId=++state.requestNumber;
    state.lists={upcoming:null,history:null};
    body(empty("Carregando sua conta","Conferindo a sessão deste estabelecimento…"));
    try{
      const data=await api();
      // The overlay may have been closed or reopened under another session.
      // Never resurrect personal data from an obsolete in-flight response.
      if(requestId!==state.requestNumber||!dialog.open)return;
      state.available=data.available===true;
      state.authenticated=data.authenticated===true;
      state.profile=state.authenticated?data.profile:null;
      document.getElementById("customer-account-subtitle").textContent=
        document.getElementById("site-title")?.textContent||"Seu espaço neste estabelecimento";
      if(state.authenticated)await openTab("upcoming");
      else renderLogin();
    }catch(error){
      if(requestId!==state.requestNumber||!dialog.open)return;
      state.available=false;renderLogin();notice(sourceError(error),true);
    }
  }
  function open(){
    if(dialog.open)return;
    dialog.showModal();
    document.body.classList.add("customer-account-open");
    state.stage="phone";state.tab="upcoming";notice();
    void loadState();
  }
  trigger.addEventListener("click",open);
  closeButton.addEventListener("click",()=>dialog.close());
  dialog.addEventListener("close",()=>{
    document.body.classList.remove("customer-account-open");
    state.requestNumber++;
    state.lists={upcoming:null,history:null};
    state.selected=null;state.editingPhone=false;
    root.replaceChildren();notice();
    // Closing an overlay is not signing out. Keep only the verified profile
    // in page memory to prefill the next booking; signing out clears it.
    if(!state.authenticated)clearPrivateState();
    trigger.focus({preventScroll:true});
  });
  dialog.addEventListener("click",event=>{
    if(event.target===dialog)dialog.close();
  });
  tabs.addEventListener("click",event=>{
    const tab=event.target.closest("[data-customer-tab]")?.dataset.customerTab;
    if(!state.authenticated||!["upcoming","history","profile"].includes(tab))return;
    state.editingPhone=false;state.requestNumber++;
    void openTab(tab);
  });
  window.OrionCustomerAccount=Object.freeze({
    prefill:()=>state.authenticated&&state.profile?
      {name:state.profile.name,whatsapp_e164:state.profile.whatsapp_e164,
       birth_date:state.profile.birth_date}:null,
    refresh:()=>{state.lists={upcoming:null,history:null};}
  });
})();
