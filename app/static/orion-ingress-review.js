/* Restricted operator diagnostics. No token, payload, replay or business action. */
(()=>{
 "use strict";
 let context=null,generation=0,snapshot=null,operation=null,frozen=null,busy=false;
 const primaryEndpoint="/api/v1/admin/whatsapp/ingress-review";
 const listEndpoint="/api/v1/admin/whatsapp/ingress-devices";
 const deviceEndpoint=id=>"/api/v1/admin/whatsapp/devices/"+id+"/ingress-review";
 const validDeviceId=value=>/^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);
 const selectedEndpoint=()=>context?.deviceSelect?.value&&validDeviceId(context.deviceSelect.value)?
   deviceEndpoint(context.deviceSelect.value):primaryEndpoint;
 const node=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el};
 const valid=()=>context&&context.role==="admin"&&context.isCurrent();
 const current=epoch=>valid()&&epoch===generation;
 const button=(text,action)=>{const el=node("button",text);el.type="button";el.className="btn secondary";el.addEventListener("click",action);return el};
 const field=(parent,label,value="")=>{const row=node("label"),input=node("input");row.className="field";row.append(node("span",label));input.type="text";input.value=value;input.autocomplete="off";row.append(input);parent.append(row);return input};
 const hex=(value,length)=>new RegExp("^[a-f0-9]{"+length+"}$").test(value);
 const status=text=>{if(valid())context.status.textContent=text};
 const phaseNames={preparing:"Preparação órfã: atendimento possivelmente iniciado",consuming:"Atendimento órfão: execução anterior incerta",
   preparation_failed:"Falha de preparação registrada",operator_retired:"Revisada sem reprocessamento"};
 const blockedReasons={expired:"Prazo esgotado",scope_changed:"Configuração ou sessão mudou",
   policy_denied:"Bloqueio preventivo",identity_unresolved:"Identidade não confirmada",
   broker_refused:"Admissão recusada",authorization_revoked:"Permissão alterada",
   invalid_message:"Mensagem inválida",business_started:"Execução possivelmente iniciada"};

 function reset(){
   ++generation;
   if(context){context.root.replaceChildren();context.root.classList.add("hidden")}
   context=null;snapshot=null;operation=null;frozen=null;busy=false;
 }
 function mount(options){
   reset();if(options.role!=="admin")return;
   context={...options};context.root.classList.remove("hidden");
   const title=node("h3","Revisão de mensagens retidas");title.id="ingressReviewTitle";
   context.root.append(title,node("p","Consulta restrita ao administrador principal. Confirme o atendimento no WhatsApp oficial. A revisão exige comprovante, conserva os registros criptografados e nunca reenvia nem executa mensagens antigas."));
   const selection=node("label"),selector=node("select");selection.className="field";
   selection.append(node("span","Conexão para revisão"));
   selector.id="ingressReviewDevice";
   selector.append(new Option("WhatsApp principal (legado)",""));
   selector.addEventListener("change",()=>{if(busy)return;++generation;snapshot=null;operation=null;frozen=null;context.target=null;context.results.replaceChildren();status("Conexão alterada. Consulte novamente.");});
   selection.append(selector);context.root.append(selection);context.deviceSelect=selector;
   context.root.append(button("Buscar dispositivos com mensagens retidas",loadCandidates));
   context.filter=field(context.root,"Identificadores do diagnóstico (opcional, até dois)");
   context.filter.maxLength=41;context.filter.spellcheck=false;
   context.filter.addEventListener("input",()=>{snapshot=null;operation=null;frozen=null;context.results.replaceChildren()});
   context.query=button("Consultar entradas",query);context.root.append(context.query);
   context.status=node("p");context.status.setAttribute("role","status");context.status.setAttribute("aria-live","polite");
   context.results=node("div");context.root.append(context.status,context.results);
 }
 async function loadCandidates(){
   if(!valid()||busy)return;
   const epoch=generation,previous=context.deviceSelect.value;
   try{
     const data=await context.request(listEndpoint,"GET");
     if(!current(epoch))return;
     const items=Array.isArray(data?.items)?data.items:[];
     context.deviceSelect.replaceChildren(new Option("WhatsApp principal (legado)",""));
     for(const item of items){
       if(!validDeviceId(item?.id)||!Number.isSafeInteger(item.blocked)||item.blocked<1)continue;
       context.deviceSelect.append(new Option(String(item.company||"Empresa")+" · "+String(item.name||"Dispositivo")+
         " — "+item.blocked+" retida(s)"+(item.stale?" · estado antigo":""),item.id));
     }
     if([...context.deviceSelect.options].some(option=>option.value===previous))context.deviceSelect.value=previous;
     status(items.length?"Escolha o dispositivo e consulte as entradas retidas.":"Nenhum dispositivo informou mensagens retidas. Confira o estado da conexão.");
   }catch(error){if(current(epoch))status("Não foi possível listar os dispositivos: "+error.message);}
 }
 async function query(){
   if(!valid()||busy)return;
   const traces=context.filter.value.trim().split(/\s+/).filter(Boolean);
   if(traces.length>2||new Set(traces).size!==traces.length||traces.some(value=>!hex(value,20))){status("Informe até dois identificadores de 20 caracteres do diagnóstico.");return}
   const epoch=++generation;busy=true;snapshot=null;frozen=null;operation=crypto.randomUUID();context.target=selectedEndpoint();
   context.query.disabled=true;context.filter.disabled=true;context.deviceSelect.disabled=true;context.results.replaceChildren();status("Consultando, sem alterar a fila…");
   try{
     const data=await context.request(context.target,"POST",{version:1,mode:"dry_run",operation_id:operation,traces});
     if(!current(epoch))return;
     snapshot=data;renderEntries();status(data.entries.length?"Consulta pronta. Selecione somente as entradas com comprovante de revisão.":"Nenhuma entrada encontrada com estes identificadores.");
   }catch(error){if(current(epoch))status(error.message)}
   finally{if(current(epoch)){busy=false;context.query.disabled=false;context.filter.disabled=false;context.deviceSelect.disabled=false}}
 }
 function renderEntries(){
   context.results.replaceChildren();context.inputs=[];
   context.results.append(node("p",snapshot.total_entries+" registro(s) na fila. "+(snapshot.truncated?"Há outros registros; use os identificadores para restringir a consulta.":"")));
   for(const entry of snapshot.entries){
     const card=node("fieldset"),legend=node("legend",new Date(entry.at).toLocaleString("pt-BR"));card.append(legend);
     card.append(node("p",(phaseNames[entry.phase]|| (entry.state==="pending"?"Recebida sem início de atendimento":
       blockedReasons[entry.blocked_reason]||"Registro antigo que exige comprovante"))+" · "+entry.trace));
     if(!entry.eligible_as.length){card.append(node("p","Esta entrada não permite liberação por esta ferramenta."));context.results.append(card);continue}
     const selection=node("input");selection.type="checkbox";
     const label=node("label");label.append(selection,document.createTextNode(" Selecionar para liberar sem reprocessar"));card.append(label);
     const kind=entry.eligible_as[0];
     card.append(node("p",kind==="operator_attested_prepare_failed"?"Exige comprovante de falha anterior ao início do atendimento. O preenchimento é um atestado do operador, não uma verificação automática do fato.":
       kind==="operator_attested_uncertain"?"O atendimento pode ter começado. Confira o histórico no WhatsApp oficial antes de registrar a revisão; esta ferramenta não repete operações nem mensagens.":
       kind==="operator_attested_no_replay"?"Confirme a situação no WhatsApp oficial. A entrada será arquivada com backup, sem reprocessar pedidos antigos.":
       "Somente entrada cujo atendimento não começou. O registro original será mantido."));
     const receipt=field(card,"SHA-256 do comprovante revisado"),build=field(card,"Versão do sistema no comprovante (40 caracteres)");
     receipt.maxLength=64;build.maxLength=40;
     const event=field(card,"Data e hora do evento comprovado (ISO com fuso)",context.target===primaryEndpoint?new Date(entry.at).toISOString():new Date().toISOString());event.maxLength=40;
     context.inputs.push({entry,selection,receipt,build,event,kind});context.results.append(card);
   }
   context.review=button("Revisar liberação",prepare);context.results.append(context.review);
   context.confirmation=node("div");context.results.append(context.confirmation);
   for(const row of context.inputs)for(const input of [row.selection,row.receipt,row.build,row.event])
     input.addEventListener("input",()=>{if(!busy){frozen=null;context.confirmation.replaceChildren()}});
 }
 function prepare(){
   if(!valid()||busy||!snapshot||frozen)return;
   const selected=context.inputs.filter(row=>row.selection.checked);
   const safeBulk=selected.length<=2||selected.every(row=>row.kind==="not_started");
   if(!selected.length||selected.length>32||!safeBulk){status("Selecione até 32 entradas não iniciadas; casos incertos continuam limitados a duas por revisão.");return}
   const entries=[];
   for(const row of selected){
     const receipt=row.receipt.value.trim(),build=row.build.value.trim(),event=row.event.value.trim(),at=Date.parse(event);
     if(!hex(receipt,64)||!hex(build,40)||!/(Z|[+-]\d{2}:\d{2})$/.test(event)||!Number.isSafeInteger(at)||at<1){status("Confira o comprovante, a versão e a data com fuso de cada entrada selecionada.");return}
     entries.push({trace:row.entry.trace,fingerprint:row.entry.fingerprint,disposition:"do_not_replay",
       evidence:{kind:row.kind,receipt_sha256:receipt,build_sha:build,event_at:at}});
   }
   frozen={version:1,mode:"apply",operation_id:operation,expected:{scope:snapshot.scope,snapshot_sha256:snapshot.snapshot_sha256},entries};
   context.confirmation.replaceChildren(node("p","Liberar "+entries.length+" entrada(s), mantendo os registros e sem executar os pedidos antigos? A cópia protegida será exigida antes da alteração."));
   context.confirmation.append(node("p","Operação: "+operation));
   context.apply=button("Liberar sem reprocessar",apply);context.confirmation.append(context.apply);
   status("Confira a seleção antes de liberar.");
 }
 function lockFields(locked){
   context.query.disabled=locked;context.filter.disabled=locked;context.deviceSelect.disabled=locked;context.review.disabled=locked;
   for(const row of context.inputs)for(const input of [row.selection,row.receipt,row.build,row.event])input.disabled=locked;
 }
 async function apply(){
   if(!valid()||busy||!frozen)return;
   const epoch=generation,payload=frozen;busy=true;lockFields(true);context.apply.disabled=true;status("Preservando a cópia e registrando a revisão…");
   try{
     if(context.target!==selectedEndpoint()){status("Conexão mudou. Consulte novamente.");return;}
     const result=await context.request(context.target,"POST",payload);
     if(!current(epoch))return;
     context.confirmation.replaceChildren(node("p",result.replayed?"Esta operação já estava registrada. Nenhuma nova alteração foi feita.":"Revisão registrada sem reprocessar as mensagens."),
       node("p","Operação: "+result.operation_id),node("p","Cópia protegida: "+result.backup.reference),node("p","SHA-256 da cópia: "+result.backup.sha256));
     status("O próximo atendimento continua exigindo um resumo novo antes da confirmação.");
     snapshot=null;frozen=null;context.query.disabled=false;context.filter.disabled=false;context.deviceSelect.disabled=false;
   }catch(error){
     if(!current(epoch))return;
     // Keep this exact operation/body after an uncertain transport result. A new
     // operation is never silently generated, and inputs stay locked until query.
     status(error.message+" Para conferir ou repetir, use a mesma operação abaixo. Uma nova consulta descarta esta seleção.");
     context.apply.textContent="Conferir ou repetir a mesma operação";context.apply.disabled=false;
     context.query.disabled=false;
   }finally{if(current(epoch))busy=false}
 }
 window.OrionIngressReview=Object.freeze({mount,reset});
})();
