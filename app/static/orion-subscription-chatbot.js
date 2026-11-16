/* Subscription Chatbot. Tenant-scoped state and explicit commands only.
 * Opening a card or preview never executes a rule, URL or WhatsApp send.
 */
(function () {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const copy=value=>JSON.parse(JSON.stringify(value));
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
  const stable=value=>JSON.stringify(value,(_key,current)=>object(current)?Object.fromEntries(Object.entries(current).sort(([a],[b])=>a.localeCompare(b))):current);
  const count=value=>Number.isSafeInteger(value)&&value>=0?new Intl.NumberFormat('pt-BR').format(value):'—';
  const dateTime=(value,timezone)=>{try{const date=new Date(value);return value&&Number.isFinite(date.getTime())?new Intl.DateTimeFormat('pt-BR',{dateStyle:'short',timeStyle:'short',...(timezone?{timeZone:timezone}:{})}).format(date):'—';}catch{return '—';}};
  const bytes=value=>!Number.isSafeInteger(value)||value<0?'—':value<1024?value+' B':value<1048576?new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(value/1024)+' KB':new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(value/1048576)+' MB';
  const paths={bot:'M12 3v3.5M8 7h8a5 5 0 0 1 5 5v4a5 5 0 0 1-5 5H9l-4 2v-4.1A5 5 0 0 1 3 15V12a5 5 0 0 1 5-5ZM9 13h.01M15 13h.01M9 17c1.7 1.1 4.3 1.1 6 0',plus:'M12 5v14M5 12h14',refresh:'M20 11a8 8 0 1 0-2.4 6M20 4v7h-7',search:'m21 21-4.3-4.3M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',edit:'m16 3 5 5M4 20l4-1L21 6l-4-4L4 15z',copy:'M9 9h12v12H9zM5 15H3V3h12v2',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',close:'m6 6 12 12M6 18 18 6',check:'m5 12 4 4L19 6',chevron:'m6 9 6 6 6-6',up:'m6 15 6-6 6 6',left:'m15 18-6-6 6-6',right:'m9 18 6-6-6-6',download:'M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6',upload:'M12 17V3m-5 5 5-5 5 5M4 15v6h16v-6',history:'M3 11a9 9 0 1 1 2.6 7.4M3 4v7h7M12 7v5l3 2',send:'m22 2-7 20-4-9-9-4 20-7M22 2 11 13',info:'M12 8v.01M12 11v6',tag:'M3 3h8l10 10-8 8L3 11zM7 7h.01',file:'M14 2H6v20h12V6zM14 2v6h6M8 13h8M8 17h6',link:'M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2'};
  const icon=name=>'<svg class="os-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'+(name==='info'?'<circle cx="12" cy="12" r="10"/>':'')+'<path d="'+(paths[name]||paths.bot)+'"/></svg>';
  const button=(label,action,style='os-outline',symbol='',extra='')=>'<button type="button" class="os-button '+style+'" data-sc-action="'+action+'" '+extra+'>'+(symbol?icon(symbol):'')+'<span>'+esc(label)+'</span></button>';
  const loading=label=>'<div class="os-loading" role="status"><span class="os-spinner" aria-hidden="true"></span>'+esc(label)+'</div>';
  const fieldId=path=>'sc-'+path.replace(/[^a-z0-9]+/gi,'-');
  const input=(label,path,value,type='text',extra='',help='')=>'<div class="os-field"><label for="'+fieldId(path)+'">'+esc(label)+'</label><input id="'+fieldId(path)+'" data-sc-field="'+path+'" type="'+type+'" value="'+esc(value)+'" '+extra+(help?' aria-describedby="'+fieldId(path)+'-help"':'')+'>'+(help?'<small class="os-help" id="'+fieldId(path)+'-help">'+esc(help)+'</small>':'')+'</div>';
  const textarea=(label,path,value,limit,help='')=>'<div class="os-field"><label for="'+fieldId(path)+'">'+esc(label)+'</label><textarea id="'+fieldId(path)+'" data-sc-field="'+path+'" maxlength="'+limit+'"'+(help?' aria-describedby="'+fieldId(path)+'-help"':'')+'>'+esc(value)+'</textarea>'+(help?'<small class="os-help" id="'+fieldId(path)+'-help">'+esc(help)+'</small>':'')+'</div>';
  const select=(label,path,value,options,extra='',help='')=>'<div class="os-field"><label for="'+fieldId(path)+'">'+esc(label)+'</label><select id="'+fieldId(path)+'" data-sc-field="'+path+'" '+extra+'>'+options.map(([key,text,disabled])=>'<option value="'+esc(key)+'"'+(String(value??'')===String(key)?' selected':'')+(disabled?' disabled':'')+'>'+esc(text)+'</option>').join('')+'</select>'+(help?'<small class="os-help">'+esc(help)+'</small>':'')+'</div>';
  const checkbox=(label,path,value,help='',disabled=false)=>'<div><label class="os-check"><input type="checkbox" data-sc-field="'+path+'"'+(value?' checked':'')+(disabled?' disabled':'')+'><span>'+esc(label)+'</span></label>'+(help?'<small class="os-help">'+esc(help)+'</small>':'')+'</div>';
  const contactTags=[['{name}','Nome do contato que enviou a mensagem.'],['{phone_number}','Número de WhatsApp do contato.'],['{my_name}','Nome do seu negócio.'],['{my_email}','Seu e-mail no cadastro do Orion.'],['{my_contact_number}','Número de contato comercial do negócio.']];
  const matchLabels={equal:'Frase Exata',like:'Contém a Palavra',array:'Lista'};
  const actionLabels={text:'Texto',template:'Template',sticker:'Figurinha',shortvideo:'Recado de Vídeo',carousel:'Carrossel',url:'URL, Servidor Externo ou Webhook',url_button:'Botão com link',nested_reply:'Enviar outra resposta',nested_list:'Enviar uma lista de opções'};
  const restoreLabels={add_missing:'Adicionar ausentes',merge:'Mesclar e atualizar',exact:'Snapshot exato'};
  const uncertain=error=>!Number(error?.status)||Number(error.status)>=500;
  const errorMessage=error=>[400,404,409,410,413,415,422,423,429].includes(Number(error?.status))&&typeof error?.message==='string'&&error.message.trim()&&!/^O servidor retornou HTTP /.test(error.message)?error.message.slice(0,1000):({401:'Sua sessão expirou. Entre novamente no Orion.',403:'Seu acesso não permite gerenciar o Chatbot desta empresa.',404:'Este registro não está mais disponível. Atualize a lista.',409:'Os dados mudaram. Confira a versão atual antes de continuar.',413:'O arquivo excede o tamanho permitido.',422:'Confira os campos informados antes de continuar.',429:'Aguarde um pouco antes de tentar novamente.',503:'Não foi possível confirmar a operação. Confira o estado antes de repetir.'}[Number(error?.status)]||'Não foi possível confirmar a resposta do servidor. Seus dados foram preservados.');
  // Declarative JSON paths; kept in parity with subscription_chatbot_tags.py.
  function validUrlTag(path){
    if(typeof path!=='string'||!path||path.length>512)return false;
    let position=0,segments=0;
    const name=()=>{const match=/^[a-zA-Z_][a-zA-Z0-9_]{0,79}/.exec(path.slice(position));if(!match)return null;position+=match[0].length;return match[0];};
    while(position<path.length){
      if(++segments>16)return false;let value;
      if(segments>1&&path[position]==='.'){position++;value=name();if(value===null)return false;}
      else if(path[position]==='['){
        position++;
        if(path[position]==='"'){
          const start=position++;let escaped=false,closed=false;
          while(position<path.length){const char=path[position++];if(escaped){escaped=false;continue;}if(char==='\\'){escaped=true;continue;}if(char==='"'){closed=true;break;}}
          if(!closed)return false;try{value=JSON.parse(path.slice(start,position));}catch{return false;}
        }else{
          const match=/^(?:0|[1-9][0-9]{0,3})/.exec(path.slice(position));if(!match)return false;position+=match[0].length;value=Number(match[0]);if(value>=8192)return false;
        }
        if(path[position++]!==']')return false;
      }else if(segments===1){value=name();if(value===null)return false;}
      else return false;
      if(typeof value==='string'&&(!value||[...value].length>2048||['__proto__','prototype','constructor'].includes(value)||/[\u0000-\u001f\u007f{}\uD800-\uDFFF]/u.test(value)))return false;
    }
    return true;
  }
  function validUrlTemplate(value){
    const pattern=/\{\{([^{}]*)\}\}/g;
    const contacts=/(?<!\{)\{(?:name|phone_number|my_name|my_email|my_contact_number)\}(?!\})/g;
    for(const match of String(value).matchAll(pattern))if(!validUrlTag(match[1].trim()))return false;
    return !/[{}]/.test(String(value).replace(pattern,'').replace(contacts,''));
  }
  const validUrlTags=tags=>Array.isArray(tags)&&tags.length<=128&&new Set(tags).size===tags.length&&tags.every(validUrlTag);
  function formatText(value){
    return String(value??'').split(/(```[\s\S]*?```|`[^`\n]+`)/g).map(part=>{
      if(part.startsWith('```')&&part.endsWith('```'))return '<code>'+esc(part.slice(3,-3))+'</code>';
      if(part.startsWith('`')&&part.endsWith('`'))return '<code>'+esc(part.slice(1,-1))+'</code>';
      return esc(part).replace(/\*([^*\n]+)\*/g,'<strong>$1</strong>').replace(/_([^_\n]+)_/g,'<em>$1</em>').replace(/~([^~\n]+)~/g,'<s>$1</s>');
    }).join('');
  }
  function fileEnvelope(file){
    if(!file||!Number.isSafeInteger(file.size)||file.size<1||file.size>10*1024*1024||!/^.+\.(?:json|zip)$/i.test(file.name))return Promise.reject(Error('Selecione um arquivo JSON ou ZIP de até 10 MB.'));
    return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onerror=()=>reject(Error('Não foi possível ler o arquivo selecionado.'));reader.onload=()=>{const value=String(reader.result||''),comma=value.indexOf(',');if(comma<0||!value.slice(0,comma).endsWith(';base64'))return reject(Error('Não foi possível ler o arquivo selecionado.'));resolve({filename:file.name,content_base64:value.slice(comma+1)});};reader.readAsDataURL(file);});
  }
  let active=null;

  function mount(bridge){
    unmount();
    const host=typeof bridge?.host==='string'?document.querySelector(bridge.host):bridge?.host;
    if(!host||typeof bridge?.request!=='function'||!uuid(bridge.tenantId))throw Error('Não foi possível confirmar a empresa deste Chatbot.');
    if(!window.OrionFields)throw Error('Atualize o painel para abrir o Chatbot.');
    const state={mounted:true,epoch:1,read:0,busy:false,loading:false,loaded:false,data:null,catalog:null,feedback:'',error:false,dialogs:[],editor:null,pending:null,expanded:new Set(),details:new Map(),detailReads:new Map(),previewMode:new Map(),filters:{q:'',search_in:'both',device_id:'',keyword:'',status:'',match_mode:'',response_type:'',created_from:'',created_to:'',page:1,per_page:20},advanced:false};
    const base='/api/v1/tenants/'+encodeURIComponent(bridge.tenantId)+'/subscription-whatsapp/chatbot';
    const request=(suffix='',method='GET',payload)=>bridge.request(base+suffix,method,payload);
    const current=(epoch,read)=>state.mounted&&state.epoch===epoch&&(read===undefined||state.read===read);
    const fields=window.OrionFields,releaseFields=fields.bind(host);
    let operationTimer=null;const downloads=new Set();
    const deviceName=id=>id===null?'Todos os Dispositivos':state.catalog?.devices.find(item=>item.id===id)?.name||'Dispositivo indisponível';
    const canManage=()=>state.loaded&&state.catalog?.capabilities?.manage===true;
    const canWrite=()=>canManage()&&!state.busy&&!state.pending&&!state.stale;
    const allowed=key=>canWrite()&&state.catalog?.capabilities?.[key]===true;
    const canRead=key=>state.loaded&&!state.busy&&state.catalog?.capabilities?.[key]===true;
    const actionAllowed=type=>state.catalog?.capabilities?.actions?.[type]===true;
    const pathId=id=>'/rules/'+encodeURIComponent(id);
    function feedback(message,error=false){state.feedback=String(message||'');state.error=error;const node=host.querySelector('[data-sc-feedback]');if(node){node.hidden=!state.feedback;node.className='os-notice os-chatbot-feedback'+(error?' os-error':'');node.setAttribute('role',error?'alert':'status');node.textContent=state.feedback;}}
    const feedbackHTML=()=>'<div class="os-notice os-chatbot-feedback'+(state.error?' os-error':'')+'" data-sc-feedback role="'+(state.error?'alert':'status')+'"'+(!state.feedback?' hidden':'')+'>'+esc(state.feedback)+'</div>';
    function setBusy(value){
      state.busy=value;host.setAttribute('aria-busy',String(value||state.loading));
      for(const root of [host,...state.dialogs.map(dialog=>dialog.element)])for(const node of root.querySelectorAll('button,input,select,textarea')){
        if(value){if(node.dataset.scWasDisabled===undefined)node.dataset.scWasDisabled=String(node.disabled);node.disabled=true;}
        else if(node.dataset.scWasDisabled!==undefined){node.disabled=node.dataset.scWasDisabled==='true';delete node.dataset.scWasDisabled;}
      }
    }
    function query(filters=state.filters){return new URLSearchParams(Object.entries(filters).filter(([,value])=>value!==''&&value!==null&&value!==undefined).map(([key,value])=>[key,String(value)])).toString();}
    function catalogDTO(value){
      if(!object(value)||!Array.isArray(value.devices)||!Array.isArray(value.templates)||!Array.isArray(value.rules)||!object(value.capabilities)||!object(value.capabilities.actions)||!object(value.limits)||!Number.isSafeInteger(value.chatbot_revision))throw Error('invalid_catalog_response');
      for(const key of ['devices','templates','rules'])if(value[key].some(item=>!object(item)||!uuid(item.id)||typeof (key==='rules'?item.keyword:item.name)!=='string'))throw Error('invalid_catalog_response');
      return copy(value);
    }
    function listDTO(value){
      if(!object(value)||!Array.isArray(value.items)||!object(value.counts)||!Number.isSafeInteger(value.chatbot_revision)||value.items.some(item=>!uuid(item.id)||!Number.isSafeInteger(item.revision)||typeof item.keyword!=='string'||!['sequence','list'].includes(item.response_kind)))throw Error('invalid_list_response');
      return copy(value);
    }
    function actionDTO(action,menu=false){
      if(!object(action)||!uuid(action.id)||!Object.hasOwn(actionLabels,action.type)||!Number.isInteger(action.delay_seconds)||action.delay_seconds<1||action.delay_seconds>(menu?5:300))throw Error('invalid_action_response');
      if(action.type==='text'&&typeof action.text!=='string'||['template','sticker','shortvideo'].includes(action.type)&&!uuid(action.template_id)||['nested_reply','nested_list'].includes(action.type)&&!uuid(action.rule_id))throw Error('invalid_action_response');
      if(action.type==='url'&&(typeof action.url!=='string'||typeof action.custom_template!=='string'||!['GET','POST'].includes(action.method)||typeof action.as_file!=='boolean'))throw Error('invalid_action_response');
      if(action.type==='url_button'&&(['text','footer','button_text','url'].some(key=>typeof action[key]!=='string')||!fixedPublicLink(action.url)))throw Error('invalid_action_response');
      if(action.type==='carousel'&&(typeof action.body!=='string'||!Array.isArray(action.cards)||action.cards.length!==3||action.cards.some(card=>!object(card)||!uuid(card.id)||['title','body','image_url','button_text'].some(key=>typeof card[key]!=='string'))))throw Error('invalid_action_response');
    }
    function ruleDTO(value,id){
      const rule=value?.rule;
      if(!object(value)||!object(rule)||!uuid(rule.id)||id&&rule.id!==id||!Number.isSafeInteger(rule.revision)||typeof rule.keyword!=='string'||typeof rule.enabled!=='boolean'||rule.device_id!==null&&!uuid(rule.device_id)||!Object.hasOwn(matchLabels,rule.match_mode)||!object(rule.response)||!['sequence','list'].includes(rule.response.kind)||!Number.isSafeInteger(value.chatbot_revision))throw Error('invalid_rule_response');
      const response=rule.response;
      if(response.kind==='sequence'){
        if(!Array.isArray(response.actions)||!response.actions.length||response.actions.length>30)throw Error('invalid_sequence_response');response.actions.forEach(action=>actionDTO(action));
      }else{
        if(['title','description','footer','button_text'].some(key=>typeof response[key]!=='string')||!Array.isArray(response.sections)||!response.sections.length||response.sections.length>10)throw Error('invalid_list_response');let rows=0;
        for(const section of response.sections){if(!object(section)||!uuid(section.id)||typeof section.title!=='string'||!Array.isArray(section.rows)||!section.rows.length)throw Error('invalid_list_response');for(const row of section.rows){if(!object(row)||!uuid(row.id)||typeof row.title!=='string'||typeof row.description!=='string')throw Error('invalid_list_response');actionDTO(row.action,true);rows++;}}
        if(rows>10)throw Error('invalid_list_response');
      }
      return copy(value);
    }
    async function load(withCatalog=true){
      if(!state.mounted||state.busy)return;
      const epoch=state.epoch,read=++state.read;state.loading=true;render();
      const results=await Promise.allSettled([request('?'+query()),...(withCatalog?[request('/catalog')]:[])]);
      if(!current(epoch,read))return;
      try{
        if(results[0].status!=='fulfilled')throw results[0].reason;
        const data=listDTO(results[0].value);
        if(withCatalog){if(results[1].status!=='fulfilled')throw results[1].reason;state.catalog=catalogDTO(results[1].value);}
        for(const item of data.items){const detail=state.details.get(item.id);if(detail&&detail.rule.revision!==item.revision)state.details.delete(item.id);}
        state.data=data;state.loaded=true;state.stale=false;
        if(data.pending_job&&!state.job){const pending=jobDTO({job:data.pending_job});if(pending.kind!=='restore'||!['queued','running'].includes(pending.status))throw Error('invalid_pending_job');state.job=pending;state.jobContext={};state.pending={kind:'job',id:pending.id};scheduleJob();}
      }catch(error){state.stale=true;feedback(errorMessage(error),true);}
      finally{state.loading=false;render();}
    }
    async function readRule(id,{openEditor=false}={}){
      if(!uuid(id)||state.busy||state.detailReads.has(id))return;
      const epoch=state.epoch,token=crypto.randomUUID();state.detailReads.set(id,token);
      try{
        const value=ruleDTO(await request(pathId(id)),id);if(!current(epoch)||state.detailReads.get(id)!==token)return;
        state.details.set(id,value);
        if(openEditor)openRuleEditor(value);else render();
      }catch(error){if(current(epoch)){feedback(errorMessage(error),true);render();}}
      finally{if(current(epoch)&&state.detailReads.get(id)===token)state.detailReads.delete(id);}
    }
    function filtersHTML(){
      const f=state.filters,devices=[['','Todos os escopos'],['all','Todos os Dispositivos'],...(state.catalog?.devices||[]).map(item=>[item.id,item.name])];
      const keywords=[['','Todas as palavras-chave'],...[...new Set((state.catalog?.rules||[]).map(item=>item.keyword))].sort((a,b)=>a.localeCompare(b,'pt-BR')).map(value=>[value,value])];
      return '<section class="os-card os-chatbot-filters"><form data-sc-form="filters" class="os-chatbot-filter-form"><div class="os-chatbot-filter-primary">'+input('Buscar respostas','filter.q',f.q,'search','maxlength="255" placeholder="Digite uma palavra ou trecho da resposta"')+select('Buscar em','filter.search_in',f.search_in,[['both','Palavra-chave e resposta'],['keyword','Palavra-chave'],['response','Resposta']])+select('Dispositivo','filter.device_id',f.device_id,devices)+'</div><details class="os-chatbot-filter-advanced" data-sc-advanced'+(state.advanced?' open':'')+'><summary>'+icon('chevron')+'Pesquisa avançada</summary><div class="os-chatbot-filter-grid">'+select('Palavra-chave','filter.keyword',f.keyword,keywords)+select('Estado','filter.status',f.status,[['','Todos'],['active','Ativas'],['inactive','Inativas']])+select('Correspondência','filter.match_mode',f.match_mode,[['','Todas'],...Object.entries(matchLabels)])+select('Tipo de resposta','filter.response_type',f.response_type,[['','Todos'],['text','Texto'],['url','URL'],['template','Template'],['list','Lista de Opções'],['buttons','Botões de Resposta']])+input('Criadas a partir de','filter.created_from',f.created_from,'date')+input('Criadas até','filter.created_to',f.created_to,'date')+'</div></details><div class="os-chatbot-filter-actions"><button type="submit" class="os-button"'+(state.loading?' disabled':'')+'>'+icon('search')+'Pesquisar</button>'+button('Limpar filtros','clear-filters','os-outline')+'</div></form></section>';
    }
    function actionPreview(action){
      const label=actionLabels[action.type]||'Tipo indisponível';
      if(action.type==='text')return formatText(action.text);
      if(action.type==='url_button')return formatText(action.text)+(action.footer?'\n\n'+esc(action.footer):'')+'\n'+esc(action.button_text)+' · '+esc(action.url);
      if(action.type==='url')return '<strong>'+label+'</strong>\n'+esc(action.method+' '+action.url)+(action.custom_template?'\n'+formatText(action.custom_template):'')+(action.as_file?'\nEnviar como arquivo .txt':'');
      if(['template','sticker','shortvideo'].includes(action.type)){const template=state.catalog?.templates.find(item=>item.id===action.template_id);return '<strong>'+label+'</strong>\n'+esc(template?.name||'Template indisponível');}
      if(['nested_reply','nested_list'].includes(action.type)){const rule=state.catalog?.rules.find(item=>item.id===action.rule_id);return '<strong>'+label+'</strong>\n'+esc(rule?.keyword||'Resposta indisponível')+(rule?.enabled===false?' · Inativa':'');}
      if(action.type==='carousel')return '<strong>Carrossel</strong>\n'+formatText(action.body)+'\n'+(action.cards||[]).map((card,index)=>'<strong>'+esc('Card '+(index+1)+(card.title?' · '+card.title:''))+'</strong>\n'+formatText(card.body)+(card.button_text?'\n'+esc(card.button_text):'')).join('\n\n');
      return esc(label);
    }
    function responsePreview(response){
      if(response.kind==='list')return '<strong>'+esc(response.title)+'</strong>\n'+formatText(response.description)+'\n\n'+response.sections.map(section=>(section.title?'<strong>'+esc(section.title)+'</strong>\n':'')+section.rows.map(row=>'• '+esc(row.title)+(row.description?' — '+esc(row.description):'')).join('\n')).join('\n\n')+(response.footer?'\n\n'+esc(response.footer):'')+'\n\n'+esc(response.button_text);
      return response.actions.map((action,index)=>'<strong>'+esc((index+1)+'. '+actionLabels[action.type]+' · após '+action.delay_seconds+' s')+'</strong>\n'+actionPreview(action)).join('\n\n');
    }
    function responseRaw(response){
      if(response.kind==='list')return [response.title,response.description,...response.sections.map(section=>[section.title,...section.rows.map(row=>'• '+row.title+(row.description?' — '+row.description:''))].filter(Boolean).join('\n')),response.footer,response.button_text].filter(Boolean).join('\n\n');
      return response.actions.map((action,index)=>{
        let value='';
        if(action.type==='text')value=action.text;
        else if(action.type==='url_button')value=[[action.text,action.footer].filter(Boolean).join('\n\n'),action.button_text+' · '+action.url].filter(Boolean).join('\n');
        else if(action.type==='url')value=action.method+' '+action.url+(action.custom_template?'\n'+action.custom_template:'')+(action.as_file?'\nEnviar como arquivo .txt':'');
        else if(['template','sticker','shortvideo'].includes(action.type))value=state.catalog?.templates.find(item=>item.id===action.template_id)?.name||'Template indisponível';
        else if(['nested_reply','nested_list'].includes(action.type))value=state.catalog?.rules.find(item=>item.id===action.rule_id)?.keyword||'Resposta indisponível';
        else if(action.type==='carousel')value=[action.body,...action.cards.map(card=>[card.title,card.body,card.button_text].filter(Boolean).join('\n'))].join('\n\n');
        return (index+1)+'. '+actionLabels[action.type]+' · após '+action.delay_seconds+' s\n'+value;
      }).join('\n\n');
    }
    function card(item){
      const detail=state.details.get(item.id),rule=detail?.rule,expanded=state.expanded.has(item.id),raw=state.previewMode.get(item.id)==='raw',enabled=item.enabled===true,attr='data-sc-id="'+item.id+'"';
      const tester=typeof bridge.openTester==='function'?button('Testar Chatbot','simulate','os-outline','bot',attr+(!canManage()?' disabled':'')):'';
      const content=rule?'<dl class="os-chatbot-details-grid"><div><dt>Dispositivo</dt><dd>'+esc(deviceName(rule.device_id))+'</dd></div><div><dt>Correspondência</dt><dd>'+esc(matchLabels[rule.match_mode])+'</dd></div><div><dt>Criada em</dt><dd>'+esc(dateTime(rule.created_at))+'</dd></div><div><dt>Sequência</dt><dd>'+count(item.action_count)+' resposta(s) · '+count(item.total_delay_seconds)+' s de atraso configurado</dd></div></dl><div class="os-chatbot-preview-switch" role="group" aria-label="Apresentação da resposta">'+button('Formatado','preview-formatted',raw?'os-outline':'','',attr+' aria-pressed="'+!raw+'"')+button('Texto bruto','preview-raw',raw?'':'os-outline','',attr+' aria-pressed="'+raw+'"')+'</div>'+(raw?'<pre class="os-chatbot-raw">'+esc(responseRaw(rule.response))+'</pre>':'<div class="os-chatbot-preview">'+responsePreview(rule.response)+'</div>')+'<div class="os-actions">'+tester+button('Testar Resposta','test','os-outline','send',attr+(!allowed('test_response')?' disabled':''))+'</div><p class="os-help">'+(tester?'Testar Chatbot abre o simulador no painel. Testar Resposta pede um telefone e envia pelo WhatsApp.':'O teste exige dispositivo e telefone e envia uma mensagem real. Esta prévia não executa a resposta.')+'</p>':loading('Carregando os detalhes…');
      const statusAction=enabled?'Desativar':'Ativar',writeDisabled=!canWrite();
      const switchControl='<button type="button" class="os-chatbot-switch" role="switch" aria-checked="'+enabled+'" aria-label="'+esc(statusAction+' resposta: '+item.keyword)+'" title="'+esc(statusAction+' resposta')+'" data-sc-action="toggle" '+attr+(writeDisabled?' disabled':'')+'><span class="os-chatbot-switch-track" aria-hidden="true"></span></button>';
      const iconAction=(label,action,symbol,danger=false)=>'<button type="button" class="os-button os-outline os-chatbot-icon-action'+(danger?' os-chatbot-icon-danger':'')+'" data-sc-action="'+action+'" '+attr+' title="'+esc(label)+'" aria-label="'+esc(label+' resposta: '+item.keyword)+'"'+(writeDisabled?' disabled':'')+'>'+icon(symbol)+'</button>';
      return '<article class="os-card os-chatbot-card" data-sc-card="'+item.id+'"><div class="os-chatbot-card-header"><div class="os-chatbot-card-title"><h2>'+esc(item.keyword)+'</h2><div class="os-chatbot-card-meta"><span class="os-badge '+(enabled?'os-success':'os-neutral')+'">'+(enabled?'Ativa':'Inativa')+'</span><span>'+esc(matchLabels[item.match_mode]||'')+'</span><span>·</span><span>'+esc(item.device_name||deviceName(item.device_id))+'</span></div><p class="os-chatbot-card-subtitle">'+esc(item.preview||'')+'</p><div class="os-chatbot-card-actions" role="group" aria-label="'+esc('Ações da resposta: '+item.keyword)+'">'+iconAction('Editar','edit','edit')+iconAction('Copiar','copy','copy')+'<span class="os-chatbot-action-divider" aria-hidden="true"></span>'+iconAction('Excluir','delete','trash',true)+'</div></div>'+switchControl+'</div><details class="os-chatbot-card-details" data-sc-details="'+item.id+'"'+(expanded?' open':'')+'><summary><span>Detalhes e prévia</span>'+icon('chevron')+'</summary><div class="os-chatbot-card-content">'+(expanded?content:'')+'</div></details></article>';
    }
    function render(){
      if(!state.mounted)return;
      const actionAttrs=key=>!allowed(key)?'disabled':'',data=state.data;
      const heading='<header class="os-title-row os-chatbot-heading"><div><h1>Chatbot</h1><p>Organize as respostas automáticas do WhatsApp.</p></div><div class="os-actions">'+button('Exportar','export','os-outline','download',!canRead('export')?'disabled':'')+button('Backups','backups','os-outline','history',!canRead('backups')?'disabled':'')+button('Importar','import','os-outline','upload',actionAttrs('import'))+button('Criar Resposta','create','','plus',!canWrite()?'disabled':'')+'</div></header>';
      if(!state.loaded){host.innerHTML=heading+feedbackHTML()+(state.stale&&!state.loading?button('Tentar novamente','refresh','os-outline','refresh'):loading('Conferindo as respostas do Chatbot…'));return;}
      const metrics='<div class="os-chatbot-metrics">'+[['total','Respostas totais'],['active','Ativas'],['inactive','Inativas']].map(([key,label])=>'<section class="os-card os-chatbot-metric"><span>'+label+'</span><strong>'+count(data.counts[key])+'</strong></section>').join('')+'</div>';
      const results='<div class="os-chatbot-results-heading"><p>'+count(data.filtered_total)+' resposta(s) encontradas</p><div class="os-actions">'+button('Atualizar','refresh','os-outline os-small','refresh',state.loading?'disabled':'')+button('Excluir Todos','delete-all','os-outline os-small','trash',!canWrite()||!data.counts.total?'disabled':'')+'</div></div>';
      const list=data.items.length?'<div class="os-chatbot-list">'+data.items.map(card).join('')+'</div>':'<section class="os-card os-empty"><div class="os-square-icon">'+icon('bot')+'</div><h2>Nenhuma resposta encontrada</h2><p>Revise os filtros ou crie uma resposta para o seu WhatsApp.</p></section>';
      const pagination='<div class="os-chatbot-pagination"><span>Página '+count(data.page)+' de '+count(Math.max(1,data.total_pages||1))+'</span><div class="os-actions">'+button('Anterior','previous','os-outline','left',state.loading||data.page<=1?'disabled':'')+button('Próxima','next','os-outline','right',state.loading||data.page>=data.total_pages?'disabled':'')+'</div></div>';
      host.innerHTML=heading+metrics+feedbackHTML()+(!canManage()?'<div class="os-notice os-chatbot-feedback"><p>Seu acesso ao Chatbot é somente para consulta. As alterações e os testes estão indisponíveis no momento.</p></div>':'')+filtersHTML()+results+list+pagination;fields.refresh(host);host.setAttribute('aria-busy',String(state.busy||state.loading));refreshPending();
    }
    function dialog(kind,title,body,{confirm='',danger=false,wide=false,...meta}={}){
      const element=document.createElement('dialog'),id='sc-dialog-'+crypto.randomUUID();element.className='os-dialog os-chatbot-dialog'+(wide?' os-dialog-wide':'');element.setAttribute('aria-labelledby',id);
      element.innerHTML='<button type="button" class="os-icon-button os-close" data-sc-action="close-dialog" aria-label="Fechar">'+icon('close')+'</button><h2 id="'+id+'">'+esc(title)+'</h2><form class="os-form" data-sc-form="'+kind+'">'+body+'<div class="os-form-message" data-sc-dialog-error role="alert"></div><div class="os-form-actions">'+button('Cancelar','close-dialog','os-outline')+(confirm?'<button type="submit" class="os-button'+(danger?' os-danger':'')+'">'+esc(confirm)+'</button>':'')+'</div></form>';
      const entry={kind,element,titleId:id,trigger:document.activeElement,releaseFields:fields.bind(element),...meta};state.dialogs.push(entry);element.addEventListener('click',click);element.addEventListener('input',inputChanged);element.addEventListener('change',change);element.addEventListener('submit',submit);element.addEventListener('cancel',cancelDialog);document.body.append(element);element.showModal();fields.refresh(element);element.querySelector('input:not([disabled]),select:not([disabled]),button:not(.os-close)')?.focus({preventScroll:true});return entry;
    }
    function dialogFor(node){return state.dialogs.find(entry=>entry.element.contains(node));}
    function closeDialog(entry,force=false){
      if(!entry||state.busy&&!force)return;
      if(entry.kind==='editor'&&!force&&state.editor?.dirty){dialog('discard','Fechar sem salvar?','<p>As alterações desta resposta ainda não foram salvas.</p>',{confirm:'Fechar sem salvar',danger:true,editor:entry});return;}
      clearTimeout(entry.expiryTimer);entry.releaseFields();entry.element.removeEventListener('click',click);entry.element.removeEventListener('input',inputChanged);entry.element.removeEventListener('change',change);entry.element.removeEventListener('submit',submit);entry.element.removeEventListener('cancel',cancelDialog);if(entry.element.open)entry.element.close();entry.element.remove();state.dialogs=state.dialogs.filter(value=>value!==entry);if(entry.kind==='editor')state.editor=null;entry.trigger?.isConnected&&entry.trigger.focus({preventScroll:true});
    }
    function cancelDialog(event){event.preventDefault();closeDialog(dialogFor(event.target));}
    function dialogError(entry,message){const node=entry?.element?.querySelector('[data-sc-dialog-error]');if(node?.isConnected){node.textContent=message;node.tabIndex=-1;node.focus();node.scrollIntoView({block:'nearest'});}else feedback(message,true);}
    async function responseBytes(response,maximum){
      if(!response.body?.getReader)throw Error('invalid_download_body');
      const reader=response.body.getReader(),chunks=[];let size=0;
      try{while(true){const {done,value}=await reader.read();if(done)break;if(size+value.byteLength>maximum)throw Object.assign(Error('O arquivo excede o tamanho permitido.'),{status:413});size+=value.byteLength;chunks.push(value);}}
      catch(error){void reader.cancel().catch(()=>{});throw error;}
      finally{reader.releaseLock();}
      const content=new Uint8Array(size);let offset=0;for(const chunk of chunks){content.set(chunk,offset);offset+=chunk.byteLength;}return content;
    }
    async function download(url,filename,format,entry){
      if(!state.mounted||state.busy)return false;
      const epoch=state.epoch,controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),180000),maximum=10*1024*1024;
      downloads.add(controller);setBusy(true);feedback('');entry.element.querySelector('[data-sc-dialog-error]').textContent='';
      try{
        const target=new URL(url,location.origin),relative=target.pathname,mime=format==='json'?'application/json':format==='zip'?'application/zip':null;
        if(!mime||target.origin!==location.origin||target.username||target.password||target.search||target.hash||!relative.startsWith(base+'/')||!/^\/(?:exports\/[0-9a-f-]{36}|backups\/[0-9a-f-]{36}\/download)$/i.test(relative.slice(base.length)))throw Object.assign(Error('Não foi possível confirmar o endereço do arquivo. Atualize a lista.'),{status:422});
        const response=await fetch(relative,{method:'GET',headers:{Accept:mime},credentials:'same-origin',mode:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
        if(!current(epoch)||!entry.element.isConnected)return false;
        const type=(response.headers.get('Content-Type')||'').split(';')[0].trim().toLowerCase(),length=response.headers.get('Content-Length');
        if(!response.ok){
          let message='O servidor retornou HTTP '+response.status;
          if(type==='application/json'&&(!length||/^\d{1,9}$/.test(length)&&Number(length)<=16384))try{const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await responseBytes(response,16384))),detail=data?.detail;const text=typeof detail==='string'?detail:detail?.message;if(typeof text==='string'&&text.trim())message=text.slice(0,1000);}catch{}
          throw Object.assign(Error(message),{status:response.status});
        }
        if(response.status!==200||response.redirected||type!==mime)throw Object.assign(Error('O servidor não retornou o arquivo no formato esperado. Tente baixar novamente.'),{status:415});
        if(length!==null&&(!/^[1-9]\d{0,8}$/.test(length)||Number(length)>maximum))throw Object.assign(Error('O arquivo excede o tamanho permitido ou está incompleto.'),{status:413});
        const content=await responseBytes(response,maximum),encoding=response.headers.get('Content-Encoding');
        if(!current(epoch)||!entry.element.isConnected)return false;
        if(!content.length||length!==null&&(!encoding||encoding==='identity')&&content.length!==Number(length))throw Object.assign(Error('O arquivo recebido está incompleto. Tente baixar novamente.'),{status:422});
        let valid;
        if(format==='zip')valid=content.length>=4&&content[0]===80&&content[1]===75&&content[2]===3&&content[3]===4;
        else try{const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(content));valid=object(data)&&data.format==='orion.subscription-chatbot'&&data.version===1&&['rules','templates','assets'].every(key=>Array.isArray(data[key]));}catch{}
        if(!valid)throw Object.assign(Error('O conteúdo recebido não é um arquivo do Chatbot Orion. Tente baixar novamente.'),{status:422});
        const objectURL=URL.createObjectURL(new Blob([content],{type:mime})),link=document.createElement('a');
        try{link.href=objectURL;link.download=filename;link.rel='noopener';document.body.append(link);link.click();}
        finally{link.remove();setTimeout(()=>URL.revokeObjectURL(objectURL),1000);}
        return true;
      }catch(error){if(current(epoch)&&entry.element.isConnected)dialogError(entry,Number(error?.status)?errorMessage(error):'Não foi possível concluir o download. Tente baixar novamente.');return false;}
      finally{controller.abort();downloads.delete(controller);clearTimeout(timeout);if(current(epoch))setBusy(false);}
    }
    async function downloadBackup(entry,backup){
      if(!canRead('backups')||state.pending||backup.status!=='ready')return;
      if(await download(backup.download_url,'orion-chatbot-backup.zip','zip',entry))feedback('Download do backup iniciado.');
    }
    function exportFiltersHTML(entry){
      const f=entry.filters,devices=[['','Todos os escopos'],['all','Todos os Dispositivos'],...state.catalog.devices.map(item=>[item.id,item.name])];
      const keywords=[['','Todas as palavras-chave'],...[...new Set((state.catalog?.rules||[]).map(item=>item.keyword))].sort((a,b)=>a.localeCompare(b,'pt-BR')).map(value=>[value,value])];
      return '<div class="os-form-grid">'+input('Buscar','export.q',f.q,'search','maxlength="255"')+select('Buscar em','export.search_in',f.search_in,[['both','Palavra-chave e resposta'],['keyword','Palavra-chave'],['response','Resposta']])+select('Dispositivo','export.device_id',f.device_id,devices)+select('Estado','export.status',f.status,[['','Todos'],['active','Ativas'],['inactive','Inativas']])+'</div><details class="os-chatbot-filter-advanced"'+([f.keyword,f.match_mode,f.response_type,f.created_from,f.created_to].some(Boolean)?' open':'')+'><summary>'+icon('chevron')+'Pesquisa avançada</summary><div class="os-form-grid">'+select('Palavra-chave','export.keyword',f.keyword,keywords)+select('Correspondência','export.match_mode',f.match_mode,[['','Todas'],...Object.entries(matchLabels)])+select('Tipo de resposta','export.response_type',f.response_type,[['','Todos'],['text','Texto'],['url','URL'],['template','Template'],['list','Lista de Opções'],['buttons','Botões de Resposta']])+input('Criadas a partir de','export.created_from',f.created_from,'date')+input('Criadas até','export.created_to',f.created_to,'date')+'</div></details><div class="os-actions">'+button('Pesquisar','export-search','os-outline os-small','search')+'</div>';
    }
    function renderExport(entry){
      if(!entry.element.isConnected)return;const data=entry.data,all=data?.items.length&&data.items.every(item=>entry.selected.has(item.id));
      entry.element.querySelector('form').innerHTML='<p>Selecione as respostas que deseja incluir no arquivo do Orion.</p>'+exportFiltersHTML(entry)+(data?'<label class="os-check"><input type="checkbox" data-sc-export-all'+(all?' checked':'')+'><span>Selecionar esta página</span></label><div class="os-chatbot-transfer-list">'+(data.items.length?data.items.map(item=>'<label class="os-chatbot-transfer-item"><input type="checkbox" data-sc-select-id="'+item.id+'"'+(entry.selected.has(item.id)?' checked':'')+'><span><strong>'+esc(item.keyword)+'</strong><small>'+esc(item.preview||'')+'</small><small>'+esc(item.device_name||deviceName(item.device_id))+' · '+esc(matchLabels[item.match_mode])+' · '+(item.enabled?'Ativa':'Inativa')+'</small></span></label>').join(''):'<p class="os-help">Nenhuma resposta encontrada para estes filtros.</p>')+'</div><div class="os-chatbot-pagination"><span>Página '+count(data.page)+' de '+count(Math.max(1,data.total_pages||1))+'</span><div class="os-actions">'+button('Anterior','export-previous','os-outline os-small','left',data.page<=1?'disabled':'')+button('Próxima','export-next','os-outline os-small','right',data.page>=data.total_pages?'disabled':'')+'</div></div>':loading('Carregando respostas…'))+select('Formato do arquivo','export_format',entry.format,[['zip','ZIP'],['json','JSON']])+'<p class="os-help" data-sc-selected-count>'+entry.selected.size+' resposta(s) selecionadas</p><div class="os-form-message" data-sc-dialog-error role="alert"></div><div class="os-form-actions">'+button('Cancelar','close-dialog','os-outline')+'<button type="submit" class="os-button"'+(!entry.selected.size||!canRead('export')?' disabled':'')+'>'+icon('download')+'Exportar Selecionados</button></div>';fields.refresh(entry.element);
    }
    async function loadExport(entry){
      if(!entry.element.isConnected)return;const epoch=state.epoch,read=entry.read=(entry.read||0)+1;
      try{const result=listDTO(await request('?'+query(entry.filters)));if(!current(epoch)||!entry.element.isConnected||entry.read!==read)return;entry.data=result;renderExport(entry);}catch(error){if(current(epoch)&&entry.element.isConnected)dialogError(entry,errorMessage(error));}
    }
    function showExport(){
      if(!canRead('export')||state.pending)return;const entry=dialog('export','Exportar Respostas','',{wide:true,selected:new Set(),filters:{...state.filters,page:1},format:'zip',data:null});renderExport(entry);void loadExport(entry);
    }
    async function submitExport(entry){
      if(!canRead('export')||!entry.selected.size)return;
      const format=entry.format;
      await execute({kind:'export',entry,suffix:'/exports',method:'POST',payload:{ids:[...entry.selected],format},accept:async result=>{if(!object(result?.export)||!uuid(result.export.id)||typeof result.export.url!=='string')throw Error('invalid_export_response');if(await download(result.export.url,'orion-chatbot.'+format,format,entry)){closeDialog(entry,true);feedback('Download do arquivo do Orion iniciado.');}}});
    }
    function backupDTO(value){if(!object(value)||!Array.isArray(value.items)||value.items.some(item=>!uuid(item.id)||typeof item.status!=='string'))throw Error('invalid_backups_response');return copy(value);}
    function renderBackups(entry){
      if(!entry.element.isConnected)return;const data=entry.data;
      const list=data?.items.length?'<div class="os-chatbot-backups">'+data.items.map(item=>'<article class="os-chatbot-backup"><div><strong>'+esc(dateTime(item.created_at))+'</strong><small>'+esc(item.source==='scheduled'?'Agendado':item.source==='safety'?'Segurança':'Backup')+' · '+count(item.rules_count)+' resposta(s) · '+count(item.list_rows_count)+' opção(ões) de lista</small><small>'+esc(bytes(item.size))+' · '+esc(item.status==='ready'?'Pronto':item.status==='failed'?'Falhou':'Preparando')+'</small></div><div class="os-actions">'+button('Baixar','backup-download','os-outline os-small','download','data-sc-id="'+item.id+'"'+(item.status!=='ready'?' disabled':''))+button('Restaurar','backup-restore','os-outline os-small','history','data-sc-id="'+item.id+'"'+(!allowed('restore')||item.status!=='ready'?' disabled':''))+'</div></article>').join('')+'</div>':data?'<p class="os-help">Nenhum backup disponível. Os snapshots agendados aparecerão neste histórico.</p>':loading('Carregando backups…');
      entry.element.querySelector('form').innerHTML='<p>Histórico dos snapshots diários das respostas do WhatsApp. A consulta e o download permanecem disponíveis mesmo sem acesso às alterações.</p>'+list+(data?'<div class="os-chatbot-pagination"><span>Página '+count(data.page)+' de '+count(Math.max(1,data.total_pages||1))+'</span><div class="os-actions">'+button('Anterior','backups-previous','os-outline os-small','left',data.page<=1?'disabled':'')+button('Próxima','backups-next','os-outline os-small','right',data.page>=data.total_pages?'disabled':'')+'</div></div>':'')+'<div class="os-form-message" data-sc-dialog-error role="alert"></div><div class="os-form-actions">'+button('Fechar','close-dialog','os-outline')+'</div>';
    }
    async function loadBackups(entry){
      if(!entry.element.isConnected)return;const epoch=state.epoch,read=entry.read=(entry.read||0)+1;
      try{const data=backupDTO(await request('/backups?page='+entry.page+'&per_page=20'));if(!current(epoch)||entry.read!==read||!entry.element.isConnected)return;entry.data=data;renderBackups(entry);}catch(error){if(current(epoch))dialogError(entry,errorMessage(error));}
    }
    function showBackups(){if(!canRead('backups')||state.pending)return;const entry=dialog('backups','Backups de Respostas','',{wide:true,page:1,data:null});renderBackups(entry);void loadBackups(entry);}
    function transferDeviceOptions(){return [['','Manter atribuições originais'],...(state.catalog?.devices||[]).map(item=>[item.id,item.name])];}
    function showImport(){
      if(!allowed('import'))return;
      const entry=dialog('import','Importar Respostas','<p>Selecione um arquivo para conferir as respostas.</p><div class="os-field"><label for="sc-import-file">Arquivo de respostas</label><input id="sc-import-file" type="file" data-sc-import-file accept=".json,.zip,application/json,application/zip" required><small class="os-help">JSON ou ZIP • até 10 MB</small></div>'+select('Atribuir a dispositivo (opcional)','transfer_device','',transferDeviceOptions(),'','Se escolhido, todas as respostas serão atribuídas a esse dispositivo.')+select('Como importar','transfer_mode','add_missing',[['add_missing','Adicionar ausentes'],['merge','Mesclar e atualizar']])+'<div data-sc-transfer-preview></div>',{confirm:'Ver prévia',wide:true,preview:null,file:null,mode:'add_missing',applyKey:null});entry.element.querySelector('button[type=submit]').dataset.scTransferSubmit='';
    }
    function showRestore(backup){
      if(!allowed('restore')||!backup||backup.status!=='ready')return;
      const descriptions={add_missing:'Preserva as respostas atuais e adiciona somente as ausentes.',merge:'Atualiza as correspondentes, adiciona as ausentes e preserva as demais.',exact:'Substitui as respostas atuais. Um backup de segurança será criado antes da substituição.'};
      const entry=dialog('restore','Restaurar Respostas','<p>Backup de <strong>'+esc(dateTime(backup.created_at))+'</strong>. Confira a prévia antes de aplicar qualquer alteração.</p><div class="os-chatbot-restore-modes">'+Object.entries(restoreLabels).map(([mode,label])=>'<label class="os-chatbot-restore-mode"><input type="radio" name="sc_restore_mode" data-sc-field="transfer_mode" value="'+mode+'"'+(mode==='add_missing'?' checked':'')+'><span><strong>'+label+'</strong><small>'+descriptions[mode]+'</small></span></label>').join('')+'</div>'+select('Atribuir a dispositivo (opcional)','transfer_device','',transferDeviceOptions(),'','Em branco, mantém as atribuições originais do backup.')+'<div data-sc-transfer-preview></div>',{confirm:'Visualizar restauração',wide:true,backup:copy(backup),preview:null,mode:'add_missing',applyKey:null});entry.element.querySelector('button[type=submit]').dataset.scTransferSubmit='';
    }
    function previewDTO(result,source,mode){
      const preview=result?.preview;if(!object(preview)||!uuid(preview.id)||preview.source!==source||preview.mode!==mode||!Object.hasOwn(restoreLabels,preview.mode)||!Number.isSafeInteger(preview.chatbot_revision)||!object(preview.counts)||typeof preview.can_apply!=='boolean'||!Number.isFinite(Date.parse(preview.expires_at))||!Array.isArray(preview.warnings))throw Error('invalid_preview_response');
      for(const key of ['create','update','delete','skip','template_clones','media_copies'])if(!Number.isSafeInteger(preview.counts[key])||preview.counts[key]<0)throw Error('invalid_preview_counts');return copy(preview);
    }
    function transferMode(entry){return entry.element.querySelector('[data-sc-field="transfer_mode"]:checked')?.value||entry.element.querySelector('[data-sc-field="transfer_mode"]')?.value||'add_missing';}
    function refreshTransferPreview(entry){
      if(!entry.element.isConnected)return;clearTimeout(entry.expiryTimer);const preview=entry.preview,container=entry.element.querySelector('[data-sc-transfer-preview]'),submit=entry.element.querySelector('[data-sc-transfer-submit]');
      if(!preview){container.replaceChildren();submit.textContent=entry.kind==='import'?'Ver prévia':'Visualizar restauração';submit.disabled=!allowed(entry.kind==='import'?'import':'restore');return;}
      const expired=Date.parse(preview.expires_at)<=Date.now(),exact=preview.mode==='exact';
      container.innerHTML='<h3>Prévia · '+esc(restoreLabels[preview.mode])+'</h3><p class="os-help">Nenhuma alteração foi aplicada. A prévia expira em '+esc(dateTime(preview.expires_at))+'.</p><div class="os-chatbot-preview-counts">'+[['create','Criar'],['update','Atualizar'],['delete','Excluir'],['skip','Pular'],['template_clones','Templates a copiar'],['media_copies','Mídias a copiar']].map(([key,label])=>'<div><strong>'+count(preview.counts[key])+'</strong>'+label+'</div>').join('')+'</div>'+preview.warnings.map(warning=>'<p class="os-notice">'+esc(typeof warning==='string'?warning:warning?.message||'Revise os dados do arquivo antes de aplicar.')+'</p>').join('')+(exact?'<div class="os-notice"><p>O snapshot exato pode excluir respostas criadas depois deste backup. Um backup de segurança será criado antes da substituição.</p></div>'+input('Digite RESTORE para confirmar','restore_confirmation','','text','autocomplete="off" maxlength="20" spellcheck="false"'):'')+(expired?'<p class="os-notice os-error" role="alert">Esta prévia expirou. Gere uma nova prévia antes de aplicar.</p>':'')+'<div class="os-actions">'+button('Gerar nova prévia','new-preview','os-outline os-small','refresh')+'</div>';
      submit.textContent=entry.kind==='import'?'Aplicar importação':'Colocar restauração na fila';submit.classList.toggle('os-danger',exact);submit.disabled=expired||!preview.can_apply||exact||!allowed(entry.kind==='import'?'import':'restore');
      if(!expired)entry.expiryTimer=setTimeout(()=>refreshTransferPreview(entry),Math.min(2147483647,Date.parse(preview.expires_at)-Date.now()+1));fields.refresh(container);
    }
    async function generatePreview(entry){
      if(!allowed(entry.kind==='import'?'import':'restore'))return;
      const mode=transferMode(entry),selected=entry.element.querySelector('[data-sc-field="transfer_device"]').value,device_id=selected||null;if(device_id&&!uuid(device_id))return;
      let payload,suffix;const epoch=state.epoch,previewRead=entry.previewRead=(entry.previewRead||0)+1;
      if(entry.kind==='import'){
        const file=entry.element.querySelector('[data-sc-import-file]').files?.[0]||entry.file;if(!file){dialogError(entry,'Selecione um arquivo JSON ou ZIP.');return;}
        try{payload={...await fileEnvelope(file),device_id,mode};}catch(error){dialogError(entry,error.message);return;}
        if(!current(epoch)||!entry.element.isConnected||entry.previewRead!==previewRead)return;entry.file=file;suffix='/imports/preview';
      }else{payload={mode,device_id};suffix='/backups/'+encodeURIComponent(entry.backup.id)+'/preview';}
      await execute({kind:'preview',entry,suffix,method:'POST',payload,accept:result=>{entry.preview=previewDTO(result,entry.kind==='import'?'import':'backup',mode);entry.applyKey=crypto.randomUUID();refreshTransferPreview(entry);}});
    }
    async function applyPreview(entry){
      const preview=entry.preview;if(!preview||!preview.can_apply||Date.parse(preview.expires_at)<=Date.now()||!allowed(entry.kind==='import'?'import':'restore'))return;
      const exact=preview.mode==='exact',confirmation=exact?entry.element.querySelector('[data-sc-field="restore_confirmation"]')?.value:null;if(exact&&confirmation!=='RESTORE')return;
      await execute({kind:'apply-preview',entry,suffix:'/previews/'+encodeURIComponent(preview.id)+'/apply',method:'POST',payload:{expected_chatbot_revision:preview.chatbot_revision,idempotency_key:entry.applyKey,confirmation},accept:result=>acceptJob(result,{entry,exact,kind:'restore',source:preview.source})});
    }
    function pendingHTML(){
      if(!state.pending)return '';
      const job=state.job,known=!!job;
      return '<section class="os-notice os-chatbot-feedback" data-sc-pending><div><strong>'+esc(known?(job.status==='queued'?'Solicitação na fila':'Solicitação em andamento'):'Resultado ainda não confirmado')+'</strong><p>'+esc(known?'Acompanhe a conclusão antes de iniciar outra alteração.':'A solicitação anterior pode ter sido recebida. Nenhum comando será repetido automaticamente.')+'</p><div class="os-actions">'+button('Conferir estado','reconcile','os-outline os-small')+(!known?button('Retomar a mesma solicitação','retry-pending','os-ghost os-small'):'')+'</div></div></section>';
    }
    function refreshPending(){
      for(const root of [host,...state.dialogs.map(entry=>entry.element)]){
        root.querySelector('[data-sc-pending]')?.remove();
        if(state.pending){const html=pendingHTML();if(root===host)host.querySelector('[data-sc-feedback]')?.insertAdjacentHTML('afterend',html);else root.querySelector('[data-sc-dialog-error]')?.insertAdjacentHTML('beforebegin',html);}
        for(const node of root.querySelectorAll('button,input,select,textarea')){
          const exempt=node.matches('[data-sc-action="reconcile"],[data-sc-action="retry-pending"],[data-sc-action="close-dialog"]');
          if(state.pending&&!exempt){if(node.dataset.scPendingDisabled===undefined)node.dataset.scPendingDisabled=String(node.disabled);node.disabled=true;}
          else if(node.dataset.scPendingDisabled!==undefined){node.disabled=node.dataset.scPendingDisabled==='true';delete node.dataset.scPendingDisabled;}
        }
      }
    }
    async function execute(command,retry=false){
      if(!state.mounted||state.busy||state.pending&&!retry)return;
      const epoch=state.epoch;setBusy(true);
      try{
        const result=await request(command.suffix,command.method,copy(command.payload));if(!current(epoch))return;
        state.pending=null;setBusy(false);refreshPending();await command.accept(result);
      }catch(error){
        if(!current(epoch))return;setBusy(false);
        if(uncertain(error)){state.pending=command;dialogError(command.entry,'Não foi possível confirmar a solicitação. Confira o estado antes de tentar novamente.');}
        else{state.pending=null;if(state.editor&&Number(error.status)===409){state.editor.stale=true;renderEditor();}if(['import','restore'].includes(command.entry?.kind)&&[409,410].includes(Number(error.status))){command.entry.preview=null;refreshTransferPreview(command.entry);}dialogError(command.entry,errorMessage(error));}
      }finally{if(current(epoch)){setBusy(false);refreshPending();if(state.job)scheduleJob();}}
    }
    async function saveEditor(form){
      const editor=state.editor;if(!editor||!canWrite()||editor.stale||!form.reportValidity())return;
      let definition;try{definition=validateDefinition();}catch(error){dialogError(editor.entry,error.message);return;}
      const payload={expected_chatbot_revision:editor.chatbotRevision,idempotency_key:editor.key,rule:definition,...(editor.id?{expected_revision:editor.revision}:{})};
      await execute({kind:'save',entry:editor.entry,suffix:editor.id?pathId(editor.id):'/rules',method:editor.id?'PUT':'POST',payload,accept:async result=>{
        const value=ruleDTO(result,editor.id);state.details.set(value.rule.id,value);closeDialog(editor.entry,true);feedback(editor.id?'Resposta atualizada.':'Resposta criada.');await load();
      }});
    }
    function mutationEntry(kind,item){
      if(!canWrite()||!item)return;
      if(kind==='toggle')return void applyMutation({kind:'toggle',item:copy(item),key:crypto.randomUUID(),revision:state.data.chatbot_revision});
      const source='<p><strong>'+esc(item.keyword)+'</strong><br>'+esc(item.device_name||deviceName(item.device_id))+'</p>';
      if(kind==='delete')return dialog('delete','Excluir resposta?',source+'<p>Esta resposta será removida do Chatbot. As demais respostas serão preservadas.</p>',{confirm:'Excluir resposta',danger:true,item:copy(item),key:crypto.randomUUID(),revision:state.data.chatbot_revision});
      if(kind==='copy')return dialog('copy','Copiar resposta?',source+'<p>A cópia terá uma nova ordem de cadastro e ficará inativa para você revisar antes de ativar.</p>',{confirm:'Criar cópia',item:copy(item),key:crypto.randomUUID(),revision:state.data.chatbot_revision});

    }
    async function applyMutation(entry){
      if(!canWrite())return;
      const item=entry.item,payload={expected_revision:item.revision,expected_chatbot_revision:entry.revision,idempotency_key:entry.key,...(entry.kind==='toggle'?{enabled:!item.enabled}:{})};
      const suffix=pathId(item.id)+(entry.kind==='copy'?'/duplicate':entry.kind==='toggle'?'/enabled':''),method=entry.kind==='delete'?'DELETE':entry.kind==='toggle'?'PATCH':'POST';
      await execute({kind:entry.kind,entry,suffix,method,payload,accept:async result=>{
        if(entry.kind==='delete'){if(result?.deleted!==1||!Number.isSafeInteger(result.chatbot_revision))throw Error('invalid_delete_response');state.details.delete(item.id);state.expanded.delete(item.id);}
        else{const value=ruleDTO(result,entry.kind==='copy'?undefined:item.id);if(entry.kind==='copy'&&value.rule.enabled)throw Error('invalid_duplicate_state');state.details.set(value.rule.id,value);}
        if(entry.element)closeDialog(entry,true);feedback(entry.kind==='copy'?'Cópia criada e mantida inativa para revisão.':entry.kind==='delete'?'Resposta excluída.':item.enabled?'Resposta desativada.':'Resposta ativada.');await load();
      }});
    }
    function showDeleteAll(){
      if(!canWrite())return;
      const entry=dialog('delete-all','Excluir todas as respostas?','<p>Esta ação exclui todas as respostas do WhatsApp desta empresa, inclusive as de Todos os Dispositivos. Os filtros da lista não limitam esta exclusão.</p><p>Revise seus backups antes de continuar. A exclusão não pode ser desfeita por esta ação.</p>'+select('Deseja continuar?','delete_all_confirm','no',[['no','Não'],['yes','Sim, excluir todas as respostas']]),{confirm:'Excluir todas as respostas',danger:true,key:crypto.randomUUID(),revision:state.data.chatbot_revision});entry.element.querySelector('button[type=submit]').disabled=true;
    }
    async function deleteAll(entry){
      if(!canWrite()||entry.element.querySelector('[data-sc-field="delete_all_confirm"]').value!=='yes')return;
      await execute({kind:'delete-all',entry,suffix:'/delete-all',method:'POST',payload:{expected_chatbot_revision:entry.revision,idempotency_key:entry.key,confirm:true},accept:async result=>{if(!Number.isSafeInteger(result?.deleted)||!Number.isSafeInteger(result.chatbot_revision))throw Error('invalid_delete_all_response');state.details.clear();state.expanded.clear();closeDialog(entry,true);feedback(count(result.deleted)+' resposta(s) excluídas.');await load();}});
    }
    function showTest(id){
      if(!allowed('test_response'))return;const value=state.details.get(id);if(!value)return;
      const eligible=state.catalog.devices.filter(item=>item.status==='connected'&&(value.rule.device_id===null||value.rule.device_id===item.id));
      const options=[['','Selecione o dispositivo'],...eligible.map(item=>[item.id,item.name])];
      const entry=dialog('test','Testar Resposta','<p><strong>'+esc(value.rule.keyword)+'</strong></p><div class="os-notice"><p>O teste envia esta resposta para um telefone real pelo WhatsApp selecionado. Confira os dois campos antes de enviar.</p></div>'+select('Dispositivo','test_device',eligible.length===1?eligible[0].id:'',options,'required')+input('Telefone com código do país','test_phone','','tel','required data-orion-field="phone" data-orion-international="true" inputmode="tel" autocomplete="off"','Inclua + e o código do país, ou um número brasileiro completo com DDD.'),{confirm:'Enviar resposta de teste',rule:copy(value.rule),revision:value.chatbot_revision,key:crypto.randomUUID()});
      if(!eligible.length){entry.element.querySelector('button[type=submit]').disabled=true;dialogError(entry,'Conecte um dispositivo compatível com o escopo da resposta antes de testar.');}
    }
    function showUrlTags(path){
      if(!allowed('read_url_tags')||!state.editor)return;const action=getPath(state.editor.definition,path);if(!action||action.type!=='url')return;
      try{validateAction(action);}catch(error){dialogError(state.editor.entry,error.message);return;}
      dialog('url-tags','Ler Tags da URL','<p>Esta ação consulta a URL abaixo com dados de exemplo. A consulta funciona sem dispositivo conectado e não envia mensagens ao WhatsApp.</p><p><strong>'+esc(action.method)+'</strong> '+esc(action.url)+'</p>',{confirm:'Consultar URL',action:copy(action),actionPath:path,editor:state.editor,revision:state.editor.chatbotRevision,key:crypto.randomUUID()});
    }
    function jobDTO(value,id){const job=value?.job;if(!object(job)||!uuid(job.id)||id&&job.id!==id||!['queued','running','succeeded','failed'].includes(job.status)||!['restore','test_response','url_tags'].includes(job.kind)||job.kind==='restore'&&!['import','backup'].includes(job.source)||job.kind!=='restore'&&job.source!=null)throw Error('invalid_job_response');return copy(job);}
    async function acceptJob(value,context={}){
      const job=jobDTO(value,state.job?.id);if(context.kind&&job.kind!==context.kind||context.source&&job.source!==context.source||state.job&&(job.kind!==state.job.kind||job.source!==state.job.source))throw Error('invalid_job_scope');state.job=job;state.jobContext=context;state.pending={kind:'job',id:job.id};
      if(job.status==='queued'||job.status==='running'){refreshPending();scheduleJob();return;}
      if(job.status==='succeeded'){
        if(job.kind==='url_tags'&&(!validUrlTags(job.result?.tags)))throw Error('invalid_url_tags_result');
        if(job.kind==='test_response'&&(!Number.isSafeInteger(job.result?.accepted_messages)||job.result.accepted_messages<0))throw Error('invalid_test_result');
        if(context.exact&&!uuid(job.safety_backup_id))throw Error('missing_safety_backup');
      }
      clearTimeout(operationTimer);state.pending=null;state.job=null;refreshPending();
      if(job.status==='failed'){
        if(['import','restore'].includes(context.entry?.kind))invalidatePreview(context.entry);
        const accepted=job.result?.accepted_messages,partial=job.kind==='test_response'&&Number.isSafeInteger(accepted)&&accepted>0;
        const message=typeof job.error==='string'?job.error:job.error?.message||'A solicitação não foi concluída. Confira o estado antes de tentar novamente.';
        dialogError(context.entry,message+(partial?' '+count(accepted)+' mensagem(ns) já foram aceitas pelo servidor. A entrega não está confirmada. Confira o WhatsApp antes de repetir o teste.':''));await load();return;
      }
      if(job.kind==='url_tags'){
        const tags=job.result?.tags;if(!validUrlTags(tags))throw Error('invalid_url_tags_result');
        if(state.editor===context.editor){state.editor.urlTags.set(context.actionId,tags);renderEditor();}
        closeDialog(context.entry,true);feedback(tags.length?'Tags consultadas. Confira os campos disponíveis na resposta de URL.':'A URL não retornou campos de texto, número ou booleano preenchidos.');
      }else if(job.kind==='test_response'){
        const accepted=job.result?.accepted_messages;if(!Number.isSafeInteger(accepted)||accepted<0)throw Error('invalid_test_result');
        closeDialog(context.entry,true);feedback(count(accepted)+' mensagem(ns) aceita(s) pelo servidor para envio. A entrega depende do WhatsApp.');
      }else{
        if(context.exact&&!uuid(job.safety_backup_id))throw Error('missing_safety_backup');
        closeDialog(context.entry,true);state.details.clear();feedback((job.source==='import'?'Importação concluída.':'Restauração concluída.')+(job.safety_backup_id?' O backup de segurança está disponível em Backups.':''));await load();
      }
    }
    function scheduleJob(){clearTimeout(operationTimer);operationTimer=null;if(!state.mounted||!state.job||document.hidden||state.busy)return;operationTimer=setTimeout(()=>{operationTimer=null;void pollJob();},2000);}
    async function pollJob(){
      if(!state.mounted||!state.job||state.busy||document.hidden)return;const epoch=state.epoch,job=state.job;
      try{const result=await request('/jobs/'+encodeURIComponent(job.id));if(!current(epoch)||state.job?.id!==job.id)return;await acceptJob(result,state.jobContext||{});}catch(error){if(current(epoch)){feedback(errorMessage(error),true);scheduleJob();}}
    }
    async function submitTest(entry){
      if(!allowed('test_response'))return;let phone;try{phone=fields.phoneValue(entry.element.querySelector('[data-sc-field="test_phone"]').value,{required:true,allowInternational:true}).replace(/^\+/,'');}catch(error){dialogError(entry,error.message);return;}
      const deviceId=entry.element.querySelector('[data-sc-field="test_device"]').value;if(!uuid(deviceId))return;
      await execute({kind:'test',entry,suffix:pathId(entry.rule.id)+'/test',method:'POST',payload:{expected_revision:entry.rule.revision,expected_chatbot_revision:entry.revision,idempotency_key:entry.key,device_id:deviceId,phone},accept:result=>acceptJob(result,{entry,kind:'test_response'})});
    }
    async function submitUrlTags(entry){
      if(!allowed('read_url_tags')||state.editor!==entry.editor)return;
      await execute({kind:'url-tags',entry,suffix:'/url-tags',method:'POST',payload:{expected_chatbot_revision:entry.revision,idempotency_key:entry.key,device_id:null,url:entry.action.url,method:entry.action.method},accept:result=>acceptJob(result,{entry,editor:entry.editor,actionId:entry.action.id,kind:'url_tags'})});
    }
    function defaultAction(type='text',delay=1){
      const common={id:crypto.randomUUID(),type,delay_seconds:delay};
      if(type==='text')return {...common,text:''};
      if(type==='url_button')return {...common,text:'',footer:'',button_text:'Acessar',url:''};
      if(['template','sticker','shortvideo'].includes(type))return {...common,template_id:''};
      if(['nested_reply','nested_list'].includes(type))return {...common,rule_id:''};
      if(type==='url')return {...common,url:'',method:'POST',custom_template:'',as_file:false};
      return {...common,body:'',cards:Array.from({length:3},()=>({id:crypto.randomUUID(),title:'',body:'',image_url:'',button_text:''}))};
    }
    const defaultRow=()=>({id:crypto.randomUUID(),title:'',description:'',action:defaultAction()});
    const defaultSection=(length=2)=>({id:crypto.randomUUID(),title:'',rows:Array.from({length},defaultRow)});
    const defaultResponse=kind=>kind==='list'?{kind,title:'',description:'',footer:'',button_text:'Ver opções',sections:[defaultSection()]}:{kind:'sequence',actions:[defaultAction()]};
    const definitionOf=rule=>({device_id:rule.device_id??null,enabled:rule.enabled===true,match_mode:rule.match_mode,keyword:rule.keyword,response:copy(rule.response)});
    const getPath=(value,path)=>path.split('.').reduce((current,key)=>current?.[key],value);
    function setPath(value,path,next){
      if(!/^[a-z_]+(?:\.(?:[a-z_]+|\d+))*$/.test(path)||path.split('.').some(key=>['__proto__','prototype','constructor'].includes(key)))throw Error('invalid_field_path');
      const keys=path.split('.'),last=keys.pop();let target=value;for(const key of keys)target=target[key];target[last]=next;
    }
    function editorDirty(){
      const editor=state.editor;if(!editor)return;
      editor.dirty=editor.isNew||stable(editor.definition)!==editor.original;
      const node=editor.entry.element.querySelector('[data-sc-dirty]');if(node)node.textContent=editor.dirty?'Você tem alterações não salvas.':'As alterações só entram em vigor depois de salvar.';
    }
    function openRuleEditor(value=null){
      if(!canWrite()||state.editor)return;
      const rule=value?.rule,definition=rule?definitionOf(rule):{device_id:null,enabled:true,match_mode:'equal',keyword:'',response:defaultResponse('sequence')};
      const entry=dialog('editor',rule?'Editar Resposta':'Criar Resposta','');entry.element.className='os-dialog os-sheet os-chatbot-dialog os-chatbot-editor';
      state.editor={entry,id:rule?.id||null,revision:rule?.revision||null,chatbotRevision:value?.chatbot_revision||state.catalog.chatbot_revision,isNew:!rule,definition,original:stable(definition),dirty:!rule,key:crypto.randomUUID(),variants:{},actionVariants:new Map(),urlTags:new Map(),stale:false};
      entry.element.innerHTML='<header class="os-chatbot-editor-header"><div><h2 id="'+entry.titleId+'">'+(rule?'Editar Resposta':'Criar Resposta')+'</h2><p>Defina quando e como o WhatsApp responde.</p></div><button type="button" class="os-icon-button os-close" data-sc-action="close-dialog" aria-label="Fechar editor">'+icon('close')+'</button></header><form class="os-chatbot-editor-form" data-sc-form="editor"><div class="os-chatbot-editor-body" data-sc-editor-body></div><footer class="os-chatbot-editor-footer"><div class="os-form-message" data-sc-dialog-error role="alert"></div><p class="os-help" data-sc-dirty></p>'+button('Cancelar','close-dialog','os-outline')+'<button type="submit" class="os-button">'+icon('check')+(rule?'Salvar alterações':'Criar Resposta')+'</button></footer></form>';
      renderEditor();entry.element.querySelector('#sc-keyword')?.focus({preventScroll:true});
    }
    function templateOptions(type,selected){
      const list=(state.catalog?.templates||[]).filter(item=>type==='sticker'?item.kind==='sticker':type==='shortvideo'?item.kind==='ptv':true);
      const options=[['',list.length?'Selecione o template':'Nenhum template disponível'],...list.map(item=>[item.id,item.name+(item.available?'':' · Indisponível'),item.available!==true])];
      if(selected&&!list.some(item=>item.id===selected))options.push([selected,'Template indisponível',true]);
      return {options,list};
    }
    function referenceOptions(type,selected){
      const list=(state.catalog?.rules||[]).filter(item=>item.id!==state.editor?.id&&item.response_kind===(type==='nested_list'?'list':'sequence'));
      const options=[['',list.length?'Selecione a resposta':'Nenhuma resposta disponível'],...list.map(item=>[item.id,item.keyword+' · '+deviceName(item.device_id)+(item.enabled?'':' · Inativa')])];
      if(selected&&!list.some(item=>item.id===selected))options.push([selected,'Resposta indisponível',true]);
      return {options,list};
    }
    function actionEditor(action,path,{menu=false}={}){
      const typeOptions=Object.entries(actionLabels).map(([key,label])=>[key,label+(actionAllowed(key)?'':' · Indisponível'),!actionAllowed(key)]);
      const types=select('Tipo de resposta',path+'.type',action.type,typeOptions,'data-sc-action-type="'+path+'"');
      const delay=input('Atraso antes de enviar (segundos)',path+'.delay_seconds',action.delay_seconds,'number','required min="1" max="'+(menu?5:300)+'" step="1"',menu?'De 1 a 5 segundos para esta opção.':'Até 300 segundos de atraso total por sequência.');
      let body='';
      if(action.type==='text')body=textarea('Resposta',path+'.text',action.text,32000)+button('Tags disponíveis','tags','os-ghost os-small','tag');
      else if(action.type==='url_button')body=textarea('Mensagem',path+'.text',action.text,1024)+input('Texto do botão',path+'.button_text',action.button_text,'text','required maxlength="20"')+input('Link do botão',path+'.url',action.url,'url','required maxlength="2048" placeholder="https://…"')+input('Rodapé (opcional)',path+'.footer',action.footer,'text','maxlength="60"')+button('Tags disponíveis','tags','os-ghost os-small','tag')+'<p class="os-help">O botão abre um site externo. Use um link HTTPS fixo, sem tags. Nenhuma URL é consultada ao editar ou simular esta mensagem. A aparência depende da versão do WhatsApp.</p>';
      else if(['template','sticker','shortvideo'].includes(action.type)){
        const templates=templateOptions(action.type,action.template_id);
        body=select('Template',path+'.template_id',action.template_id,templates.options,'required'+(!templates.list.length?' disabled':''))+'<p class="os-help">'+(templates.list.length?'Somente templates disponíveis da sua empresa podem ser usados.':'Nenhum template compatível está cadastrado. Escolha outro tipo de resposta para continuar.')+'</p>';
      }else if(['nested_reply','nested_list'].includes(action.type)){
        const references=referenceOptions(action.type,action.rule_id),target=references.list.find(item=>item.id===action.rule_id);
        body=select(action.type==='nested_list'?'Lista de Opções':'Resposta existente',path+'.rule_id',action.rule_id,references.options,'required'+(!references.list.length?' disabled':''))+(target?.enabled===false?'<p class="os-notice">A resposta selecionada está inativa e não será executada enquanto permanecer assim.</p>':'')+'<p class="os-help">Referências removidas, ciclos e sequências que ultrapassem o atraso máximo são recusados ao salvar.</p>';
      }else if(action.type==='url'){
        const tags=state.editor.urlTags.get(action.id)||[];
        body=input('URL',path+'.url',action.url,'url','required maxlength="2048" placeholder="https://…"')+select('Método',path+'.method',action.method,[['POST','POST'],['GET','GET']])+textarea('Resposta customizada (opcional)',path+'.custom_template',action.custom_template,32000,'Use {name} para o contato e {{username}}, {{password}} ou {{reply}} para os dados da API. Toque nas tags encontradas para inserir. Em branco, utiliza reply na raiz.')+checkbox('Enviar como arquivo .txt',path+'.as_file',action.as_file)+'<div class="os-actions">'+button('Ler Tags da URL','read-url-tags','os-outline os-small','link','data-sc-path="'+path+'"'+(!allowed('read_url_tags')?' disabled':''))+'</div><p class="os-help">Ler as tags acessa a URL informada com dados de exemplo. Abrir este formulário não consulta a URL.</p>'+(tags.length?'<div class="os-chatbot-keywords" data-sc-url-tags="'+esc(action.id)+'" role="group" aria-label="Tags encontradas">'+tags.map(tag=>'<button type="button" class="os-chatbot-keyword-tag" data-sc-action="insert-url-tag" data-sc-path="'+esc(path)+'" data-sc-tag="'+esc(tag)+'" title="Inserir tag na resposta">'+esc('{{'+tag+'}}')+'</button>').join('')+'</div>':'');
      }else if(action.type==='carousel'){
        body=textarea('Corpo principal',path+'.body',action.body,1024)+'<div class="os-chatbot-step-list">'+action.cards.map((card,index)=>{const p=path+'.cards.'+index;return '<section class="os-chatbot-menu-section"><h3>Card '+(index+1)+'</h3>'+input('Título',p+'.title',card.title,'text','required maxlength="60"')+textarea('Corpo'+(index===0?' (obrigatório)':' (opcional)'),p+'.body',card.body,1024)+input('URL da imagem',p+'.image_url',card.image_url,'url','required maxlength="2048" placeholder="https://…"')+input('Texto do botão',p+'.button_text',card.button_text,'text','required maxlength="20"')+'</section>';}).join('')+'</div>';
      }
      return '<div class="os-chatbot-step-body"><div class="os-form-grid">'+types+delay+'</div>'+(!actionAllowed(action.type)?'<p class="os-notice">Este tipo de resposta não está disponível para ativação nesta conexão.</p>':'')+body+'</div>';
    }
    function sequenceEditor(response){
      const total=response.actions.reduce((sum,action)=>sum+Number(action.delay_seconds||0),0);
      return '<p class="os-help" data-sc-sequence-summary>'+response.actions.length+' resposta(s) · '+total+' de 300 segundos de atraso configurado</p><div class="os-chatbot-step-list">'+response.actions.map((action,index)=>'<section class="os-chatbot-step" data-sc-step="'+action.id+'"><header class="os-chatbot-step-header"><strong>Resposta '+(index+1)+'</strong><div class="os-actions">'+button('Subir','step-up','os-outline','up','data-sc-index="'+index+'"'+(index===0?' disabled':''))+button('Descer','step-down','os-outline','chevron','data-sc-index="'+index+'"'+(index===response.actions.length-1?' disabled':''))+button('Remover','step-remove','os-ghost','trash','data-sc-index="'+index+'"'+(response.actions.length===1?' disabled':''))+'</div></header>'+actionEditor(action,'response.actions.'+index)+'</section>').join('')+'</div><div class="os-actions">'+button('Adicionar Resposta','step-add','os-outline','plus',response.actions.length>=30||total>=30?'disabled':'')+'</div>';
    }
    function listEditor(response){
      const rows=response.sections.reduce((sum,section)=>sum+section.rows.length,0);
      return '<div class="os-form-grid">'+input('Título','response.title',response.title,'text','required maxlength="60"')+input('Texto do botão','response.button_text',response.button_text,'text','required maxlength="20"')+'</div>'+textarea('Descrição','response.description',response.description,1000)+input('Rodapé (opcional)','response.footer',response.footer,'text','maxlength="60"')+'<p class="os-help">'+response.sections.length+' de 10 seções · '+rows+' de 10 opções no total</p><div class="os-chatbot-menu-sections">'+response.sections.map((section,index)=>'<section class="os-chatbot-menu-section" data-sc-menu-section="'+section.id+'"><header class="os-chatbot-menu-header"><h3>Seção '+(index+1)+'</h3>'+button('Remover seção','section-remove','os-ghost','trash','data-sc-index="'+index+'"'+(response.sections.length===1?' disabled':''))+'</header>'+input('Título da seção (opcional)','response.sections.'+index+'.title',section.title,'text','maxlength="24"')+'<div class="os-chatbot-menu-rows">'+section.rows.map((row,rowIndex)=>{const path='response.sections.'+index+'.rows.'+rowIndex;return '<section class="os-chatbot-menu-row" data-sc-menu-row="'+row.id+'"><div class="os-chatbot-menu-row-fields"><h3>Opção '+(rowIndex+1)+'</h3>'+input('Título da opção',path+'.title',row.title,'text','required maxlength="24"')+input('Descrição da opção (opcional)',path+'.description',row.description,'text','maxlength="72"')+actionEditor(row.action,path+'.action',{menu:true})+'</div>'+button('Remover','row-remove','os-ghost os-small','trash','data-sc-section="'+index+'" data-sc-index="'+rowIndex+'"'+(section.rows.length===1?' disabled':''))+'</section>';}).join('')+'</div>'+button('Adicionar opção','row-add','os-outline os-small','plus','data-sc-index="'+index+'"'+(rows>=10?' disabled':''))+'</section>').join('')+'</div><div class="os-actions">'+button('Adicionar seção','section-add','os-outline','plus',response.sections.length>=10||rows>=10?'disabled':'')+'</div>';
    }
    function renderEditor(){
      const editor=state.editor;if(!editor)return;const definition=editor.definition,body=editor.entry.element.querySelector('[data-sc-editor-body]'),scroll=body.scrollTop,focus=document.activeElement?.dataset.scField;
      const devices=[['','Todos os Dispositivos'],...(state.catalog?.devices||[]).map(item=>[item.id,item.name])];if(definition.device_id&&!devices.some(([id])=>id===definition.device_id))devices.push([definition.device_id,'Dispositivo indisponível',true]);
      body.innerHTML=(editor.stale?'<div class="os-notice os-error"><p>Esta resposta mudou em outra sessão. Confira a versão atual antes de salvar.</p>'+button(editor.id?'Carregar versão atual':'Atualizar catálogo','reload-editor','os-outline os-small','refresh')+'</div>':'')+'<section class="os-chatbot-editor-section"><h3>Quando responder</h3><div class="os-form-grid">'+select('Dispositivo','device_id',definition.device_id,devices)+select('Correspondência','match_mode',definition.match_mode,Object.entries(matchLabels))+'</div>'+input('Palavra-chave','keyword',definition.keyword,'text','required maxlength="255"',definition.match_mode==='array'?'Separe os termos por vírgula. Qualquer termo pode acionar esta resposta.':'Acentos são normalizados. Emojis também podem ser usados.')+'<div class="os-actions">'+button('Palavras especiais','keywords-help','os-ghost os-small','info')+'</div>'+checkbox('Resposta ativa','enabled',definition.enabled,'Quando ativa, a resposta pode ser enviada automaticamente ao encontrar a palavra-chave.')+'<p class="os-help">Se houver mais de uma correspondência, a resposta cadastrada mais recentemente tem preferência. Editar uma resposta não muda a ordem de cadastro.</p></section><section class="os-chatbot-editor-section"><h3>O que enviar</h3><div class="os-tabs" role="group" aria-label="Formato da resposta">'+button('Texto e Modelos','response-sequence',definition.response.kind==='sequence'?'':'os-ghost','','aria-pressed="'+(definition.response.kind==='sequence')+'"')+button('Lista de Opções','response-list',definition.response.kind==='list'?'':'os-ghost','','aria-pressed="'+(definition.response.kind==='list')+'"'+(!actionAllowed('nested_list')&&definition.response.kind!=='list'?' disabled':''))+'</div>'+(definition.response.kind==='sequence'?sequenceEditor(definition.response):listEditor(definition.response))+'</section>';
      fields.refresh(body);body.scrollTop=scroll;editorDirty();if(focus)body.querySelector('#'+fieldId(focus))?.focus({preventScroll:true});
      editor.entry.element.querySelector('button[type=submit]').disabled=!canWrite()||editor.stale;
    }
    function validateContactText(value,label){
      if(!String(value||'').trim())throw Error('Preencha '+label+'.');
      if(/[{}]/.test(String(value).replace(/\{(?:name|phone_number|my_name|my_email|my_contact_number)\}/g,'')))throw Error('Use somente as cinco tags disponíveis no texto da resposta.');
    }
    function fixedPublicLink(value){
      if(typeof value!=='string'||!value||value.length>2048||/[{}\\\u0000-\u0020\u007f]|%7[bd]|%0[0-9a-f]|%1[0-9a-f]|%7f/i.test(value))return false;
      try{
        const url=new URL(value),host=url.hostname.toLowerCase().replace(/^\[|\]$/g,'');
        if(url.protocol!=='https:'||url.username||url.password||url.hash||url.port==='0')return false;
        if(/^\d+\.\d+\.\d+\.\d+$/.test(host)){
          const [a,b,c]=host.split('.').map(Number);
          return !(a===0||a===10||a===127||a>=224||a===100&&b>=64&&b<=127||a===169&&b===254||a===172&&b>=16&&b<=31||a===192&&(b===0&&(c===0||c===2)||b===88&&c===99||b===168)||a===198&&(b===18||b===19||b===51&&c===100)||a===203&&b===0&&c===113);
        }
        if(host.includes(':')){
          if(host.includes('.'))return false;
          const number=source=>{const sides=source.split('::'),left=sides[0]?sides[0].split(':'):[],right=sides[1]?sides[1].split(':'):[],groups=sides.length===2?[...left,...Array(8-left.length-right.length).fill('0'),...right]:left;return groups.reduce((n,v)=>(n<<16n)+BigInt('0x'+v),0n);};
          const n=number(host),inside=(base,bits)=>(n>>BigInt(128-bits))===(number(base)>>BigInt(128-bits));
          return inside('2000::',3)&&!inside('2001::',23)&&!inside('2001:db8::',32)&&!inside('2002::',16)&&!inside('3fff::',20);
        }
        return host.length<=253&&host.includes('.')&&!host.endsWith('.')&&!/(?:^|\.)(?:localhost|local|internal|test|invalid|onion)$/.test(host)&&host.split('.').every(part=>/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(part));
      }catch{return false;}
    }
    function validateAction(action,menu=false){
      if(!uuid(action.id)||!Object.hasOwn(actionLabels,action.type))throw Error('Confira o tipo da resposta.');
      if(!Number.isInteger(action.delay_seconds)||action.delay_seconds<1||action.delay_seconds>(menu?5:300))throw Error('O atraso deve ser de 1 a '+(menu?5:300)+' segundos.');
      if(state.editor.definition.enabled&&!actionAllowed(action.type))throw Error('O tipo '+actionLabels[action.type]+' não está disponível para ativação.');
      if(action.type==='text')validateContactText(action.text,'o texto da resposta');
      if(action.type==='url_button'){
        for(const [key,maximum,label]of [['text',1024,'a mensagem'],['button_text',20,'o texto do botão'],['footer',60,'o rodapé']]){
          if(typeof action[key]!=='string'||action[key].length>maximum||action[key].includes('\0')||/[\uD800-\uDFFF]/u.test([...action[key]].filter(char=>char.length===1).join('')))throw Error('Confira '+label+' e seu limite de '+maximum+' caracteres.');
          if(key!=='footer'||action[key])validateContactText(action[key],label);
        }
        if(/[\u0000-\u001f\u007f]/.test(action.button_text))throw Error('O texto do botão deve ocupar uma linha, sem caracteres de controle.');
        if(!fixedPublicLink(action.url))throw Error('Use um link HTTPS público e fixo, sem usuário, senha, fragmento ou tags.');
      }
      if(['template','sticker','shortvideo'].includes(action.type)&&!templateOptions(action.type,action.template_id).list.some(item=>item.id===action.template_id&&item.available===true))throw Error('Selecione um template disponível da sua empresa.');
      if(['nested_reply','nested_list'].includes(action.type)&&!referenceOptions(action.type,action.rule_id).list.some(item=>item.id===action.rule_id))throw Error('Selecione uma resposta existente e diferente da própria regra.');
      if(action.type==='url'){
        try{const url=new URL(action.url);if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error();}catch{throw Error('Informe uma URL HTTP ou HTTPS sem usuário ou senha.');}
        if(!validUrlTemplate(action.custom_template))throw Error('Use somente tags válidas: {name} para o contato e {{campo}} para a API, sem expressões.');
      }
      if(action.type==='carousel'&&(!action.body.trim()||!action.cards[0]?.body.trim()||action.cards.length!==3))throw Error('Preencha o corpo principal e o corpo do primeiro card do carrossel.');
    }
    function validateDefinition(){
      const definition=copy(state.editor.definition);definition.keyword=definition.keyword.trim();
      if(!definition.keyword||definition.keyword.length>255||/[\u0000-\u001f]/.test(definition.keyword))throw Error('Informe uma palavra-chave de até 255 caracteres, em uma linha.');
      if(definition.match_mode==='array'&&!definition.keyword.split(',').some(value=>value.trim()))throw Error('Informe ao menos um termo para a lista de palavras-chave.');
      if(definition.device_id&&!state.catalog.devices.some(item=>item.id===definition.device_id))throw Error('Selecione um dispositivo disponível ou Todos os Dispositivos.');
      const response=definition.response;
      if(response.kind==='sequence'){
        if(!response.actions.length||response.actions.length>30)throw Error('A sequência deve ter de 1 a 30 respostas.');
        response.actions.forEach(action=>validateAction(action));
        if(response.actions.reduce((sum,action)=>sum+action.delay_seconds,0)>300)throw Error('A soma dos atrasos da sequência não pode ultrapassar 300 segundos.');
      }else{
        const rows=response.sections.flatMap(section=>section.rows);
        if(!response.title.trim()||!response.description.trim()||!response.button_text.trim())throw Error('Preencha o título, a descrição e o texto do botão da lista.');
        if(!response.sections.length||response.sections.length>10||!rows.length||rows.length>10)throw Error('Use até 10 seções e até 10 opções no total.');
        for(const row of rows){if(!row.title.trim())throw Error('Preencha o título de cada opção.');validateAction(row.action,true);}
      }
      return definition;
    }
    function showTags(){dialog('tags','Tags disponíveis','<p>Use estas tags no texto. O Orion preenche os valores ao enviar a resposta.</p><dl class="os-chatbot-details-grid">'+contactTags.map(([tag,label])=>'<div><dt><code>'+esc(tag)+'</code></dt><dd>'+esc(label)+'</dd></div>').join('')+'</dl>');}
    function showKeywords(){dialog('keywords','Palavras especiais','<p>Respostas específicas têm preferência sobre palavras especiais. As janelas abaixo são contadas por contato e dispositivo.</p><dl class="os-chatbot-details-grid">'+[['*','Uma vez a cada 24 horas.'],['**','Uma vez a cada 14 dias.'],['***','Uma vez por dia, no fuso do dispositivo.'],['****','Em toda mensagem recebida.'],['*****','Uma vez a cada hora.']].map(([key,text])=>'<div><dt><code>'+key+'</code></dt><dd>'+text+'</dd></div>').join('')+'</dl><p class="os-help">O comando !palavra-chave pode ser usado pelo próprio WhatsApp conectado para acionar a resposta. Esta tela apenas explica a configuração e não envia mensagens.</p>');}
    function resetFilters(){state.filters={q:'',search_in:'both',device_id:'',keyword:'',status:'',match_mode:'',response_type:'',created_from:'',created_to:'',page:1,per_page:20};void load(false);}
    function filterValues(root,prefix,target){for(const node of root.querySelectorAll('[data-sc-field]')){const name=node.dataset.scField;if(name.startsWith(prefix+'.')&&Object.hasOwn(target,name.slice(prefix.length+1)))target[name.slice(prefix.length+1)]=node.value;}}
    function validFilterDates(filters,entry){if(filters.created_from&&filters.created_to&&filters.created_from>filters.created_to){if(entry)dialogError(entry,'A data inicial deve ser anterior ou igual à data final.');else feedback('A data inicial deve ser anterior ou igual à data final.',true);return false;}return true;}
    function editorChange(){if(state.editor){editorDirty();state.editor.key=crypto.randomUUID();}}
    function updateDelaySummary(){const response=state.editor?.definition.response;if(response?.kind!=='sequence')return;const total=response.actions.reduce((sum,action)=>sum+Number(action.delay_seconds||0),0),element=state.editor.entry.element;const summary=element.querySelector('[data-sc-sequence-summary]');if(summary)summary.textContent=response.actions.length+' resposta(s) · '+total+' de 300 segundos de atraso configurado';const add=element.querySelector('[data-sc-action="step-add"]');if(add)add.disabled=response.actions.length>=30||total>=300||!canWrite();}
    function editorAction(action,node){
      const editor=state.editor;if(!editor||!canWrite()||editor.stale)return;const response=editor.definition.response,index=Number(node.dataset.scIndex),sectionIndex=Number(node.dataset.scSection);
      if(action==='response-sequence'||action==='response-list'){
        const kind=action==='response-list'?'list':'sequence';if(response.kind===kind)return;editor.variants[response.kind]=copy(response);editor.definition.response=editor.variants[kind]?copy(editor.variants[kind]):defaultResponse(kind);
      }else if(action==='step-add'&&response.kind==='sequence'){
        if(response.actions.length>=30||response.actions.reduce((sum,row)=>sum+Number(row.delay_seconds||0),0)>=300)return;response.actions.push(defaultAction());
      }else if(['step-up','step-down','step-remove'].includes(action)&&response.kind==='sequence'){
        if(!Number.isInteger(index)||!response.actions[index])return;if(action==='step-remove'){if(response.actions.length<2)return;response.actions.splice(index,1);}else{const next=index+(action==='step-up'?-1:1);if(!response.actions[next])return;[response.actions[index],response.actions[next]]=[response.actions[next],response.actions[index]];}
      }else if(response.kind==='list'){
        const rows=response.sections.reduce((sum,section)=>sum+section.rows.length,0);
        if(action==='section-add'){if(rows>=10||response.sections.length>=10)return;response.sections.push(defaultSection(Math.min(2,10-rows)));}
        else if(action==='section-remove'){if(response.sections.length<2||!response.sections[index])return;response.sections.splice(index,1);}
        else if(action==='row-add'){if(rows>=10||!response.sections[index])return;response.sections[index].rows.push(defaultRow());}
        else if(action==='row-remove'){const section=response.sections[sectionIndex];if(!section||section.rows.length<2||!section.rows[index])return;section.rows.splice(index,1);}
        else return;
      }else return;
      editorChange();renderEditor();
    }
    async function reloadEditor(entry){
      const editor=state.editor;if(!editor||state.busy||state.pending)return;const epoch=state.epoch;setBusy(true);
      try{const results=await Promise.all([request('/catalog'),...(editor.id?[request(pathId(editor.id))]:[])]);if(!current(epoch)||state.editor!==editor)return;const catalog=catalogDTO(results[0]),detail=editor.id?ruleDTO(results[1],editor.id):null;state.catalog=catalog;state.stale=false;editor.chatbotRevision=detail?.chatbot_revision||catalog.chatbot_revision;editor.stale=false;editor.key=crypto.randomUUID();if(detail){editor.definition=definitionOf(detail.rule);editor.revision=detail.rule.revision;editor.original=stable(editor.definition);editor.variants={};editor.actionVariants.clear();editor.urlTags.clear();}closeDialog(entry,true);setBusy(false);renderEditor();}
      catch(error){if(current(epoch))dialogError(entry,errorMessage(error));}finally{if(current(epoch))setBusy(false);}
    }
    function syncExportSelection(entry){
      const submit=entry.element.querySelector('button[type=submit]'),label=entry.element.querySelector('[data-sc-selected-count]'),all=entry.element.querySelector('[data-sc-export-all]');
      if(submit)submit.disabled=!entry.selected.size||!canRead('export');if(label)label.textContent=entry.selected.size+' resposta(s) selecionadas';if(all){const selected=(entry.data?.items||[]).filter(item=>entry.selected.has(item.id)).length;all.checked=selected>0&&selected===entry.data.items.length;all.indeterminate=selected>0&&selected<entry.data.items.length;}
    }
    function invalidatePreview(entry){entry.previewRead=(entry.previewRead||0)+1;entry.preview=null;entry.applyKey=null;clearTimeout(entry.expiryTimer);refreshTransferPreview(entry);}
    function click(event){
      const node=event.target.closest('[data-sc-action]');if(!node||node.disabled||!state.mounted)return;const action=node.dataset.scAction,entry=dialogFor(node);event.preventDefault();
      if(action==='close-dialog'){closeDialog(entry);return;}
      if(state.busy)return;
      if(action==='reconcile'){if(state.job)void pollJob();else void load();return;}
      if(action==='retry-pending'){const command=state.pending;if(command&&command.kind!=='job')void execute(command,true);return;}
      if(state.pending)return;
      const id=node.dataset.scId,item=state.data?.items.find(value=>value.id===id);
      if(action==='refresh'){void load();return;}
      if(action==='clear-filters'){resetFilters();return;}
      if(action==='previous'||action==='next'){const page=state.filters.page+(action==='next'?1:-1);if(page>=1&&page<=state.data.total_pages){state.filters.page=page;void load(false);}return;}
      if(action==='create'){openRuleEditor();return;}
      if(action==='edit'){if(canWrite())void readRule(id,{openEditor:true});return;}
      if(['copy','toggle','delete'].includes(action)){mutationEntry(action,item);return;}
      if(action==='delete-all'){showDeleteAll();return;}
      if(action==='preview-formatted'||action==='preview-raw'){state.previewMode.set(id,action==='preview-raw'?'raw':'formatted');render();return;}
      if(action==='simulate'){
        const rule=state.details.get(id)?.rule,devices=state.catalog?.devices||[];
        if(rule&&canManage()&&typeof bridge.openTester==='function')bridge.openTester({deviceId:rule.device_id||(devices.length===1?devices[0].id:null),ruleId:rule.id});
        return;
      }
      if(action==='test'){showTest(id);return;}
      if(action==='tags'){showTags();return;}
      if(action==='keywords-help'){showKeywords();return;}
      if(action==='insert-url-tag'){
        const path=node.dataset.scPath,tag=node.dataset.scTag;
        const editor=state.editor,source=editor?.entry?.element;
        const step=editor?.definition&&getPath(editor.definition,path),allowedTags=step?.id&&editor.urlTags.get(step.id);
        if(!canWrite()||!step||!validUrlTag(tag)||!allowedTags?.includes(tag))return;
        const target=source?.querySelector('[data-sc-field="'+path+'.custom_template"]');
        if(!target||target.disabled)return;
        const snippet='{{'+tag+'}}',start=target.selectionStart,end=target.selectionEnd;
        target.setRangeText(snippet,start,end,'end');
        target.dispatchEvent(new Event('input',{bubbles:true}));
        target.focus({preventScroll:true});
        return;
      }
      if(action==='read-url-tags'){showUrlTags(node.dataset.scPath);return;}
      if(action==='reload-editor'){if(state.editor)dialog('reload-editor',state.editor.id?'Carregar a versão atual?':'Atualizar o catálogo?','<p>'+esc(state.editor.id?'As alterações locais desta resposta serão substituídas pela versão salva no servidor.':'Seu rascunho será preservado e validado com os dados atuais ao salvar.')+'</p>',{confirm:state.editor.id?'Carregar versão atual':'Atualizar catálogo'});return;}
      if(action==='export'){showExport();return;}
      if(action==='backups'){showBackups();return;}
      if(action==='import'){showImport();return;}
      if(action==='export-search'){filterValues(entry.element,'export',entry.filters);if(!validFilterDates(entry.filters,entry))return;entry.filters.page=1;void loadExport(entry);return;}
      if(action==='export-previous'||action==='export-next'){entry.filters.page+=action==='export-next'?1:-1;void loadExport(entry);return;}
      if(action==='backups-previous'||action==='backups-next'){entry.page+=action==='backups-next'?1:-1;void loadBackups(entry);return;}
      if(action==='backup-download'||action==='backup-restore'){const backup=entry.data?.items.find(item=>item.id===id);if(!backup)return;if(action==='backup-restore')showRestore(backup);else void downloadBackup(entry,backup);return;}
      if(action==='new-preview'){invalidatePreview(entry);void generatePreview(entry);return;}
      editorAction(action,node);
    }
    function inputChanged(event){
      const node=event.target,path=node.dataset.scField,entry=dialogFor(node);if(!path||node.disabled||state.busy||state.pending)return;
      if(path.startsWith('filter.')){state.filters[path.slice(7)]=node.value;return;}
      if(path.startsWith('export.')){if(entry?.kind==='export')entry.filters[path.slice(7)]=node.value;return;}
      if(path==='restore_confirmation'){const preview=entry?.preview;if(preview)entry.element.querySelector('[data-sc-transfer-submit]').disabled=node.value!=='RESTORE'||Date.parse(preview.expires_at)<=Date.now()||!preview.can_apply||!allowed('restore');return;}
      if(entry?.kind==='editor'&&state.editor&&!state.editor.stale&&!node.dataset.scActionType){
        const value=node.type==='checkbox'?node.checked:node.type==='number'?Number(node.value):path==='device_id'?(node.value||null):node.value;
        if(getPath(state.editor.definition,path)!==undefined){
          const action=getPath(state.editor.definition,path.slice(0,path.lastIndexOf('.')));
          if(action?.type==='url'&&(/\.(?:url|method)$/.test(path))&&getPath(state.editor.definition,path)!==value){state.editor.urlTags.delete(action.id);state.editor.entry.element.querySelector('[data-sc-url-tags="'+action.id+'"]')?.remove();}
          setPath(state.editor.definition,path,value);editorChange();if(path.endsWith('.delay_seconds'))updateDelaySummary();
        }
      }
    }
    function change(event){
      const node=event.target,entry=dialogFor(node),path=node.dataset.scField;if(node.disabled||state.busy||state.pending)return;
      if(node.dataset.scSelectId&&entry?.kind==='export'){if(node.checked)entry.selected.add(node.dataset.scSelectId);else entry.selected.delete(node.dataset.scSelectId);syncExportSelection(entry);return;}
      if(node.hasAttribute('data-sc-export-all')&&entry?.kind==='export'){for(const item of entry.data.items){if(node.checked)entry.selected.add(item.id);else entry.selected.delete(item.id);}for(const input of entry.element.querySelectorAll('[data-sc-select-id]'))input.checked=entry.selected.has(input.dataset.scSelectId);syncExportSelection(entry);return;}
      if(node.hasAttribute('data-sc-import-file')&&entry?.kind==='import'){entry.file=node.files?.[0]||null;invalidatePreview(entry);return;}
      if(['transfer_mode','transfer_device'].includes(path)&&['import','restore'].includes(entry?.kind)){invalidatePreview(entry);return;}
      if(path==='export_format'&&entry?.kind==='export'){entry.format=node.value;return;}
      if(path==='delete_all_confirm'&&entry?.kind==='delete-all'){entry.element.querySelector('button[type=submit]').disabled=node.value!=='yes'||!canWrite();return;}
      if(node.dataset.scActionType&&entry?.kind==='editor'&&state.editor&&!state.editor.stale){
        const actionPath=node.dataset.scActionType,currentAction=getPath(state.editor.definition,actionPath),nextType=node.value;if(!currentAction||!Object.hasOwn(actionLabels,nextType)||!actionAllowed(nextType)||currentAction.type===nextType)return;
        const variants=state.editor.actionVariants.get(currentAction.id)||{};variants[currentAction.type]=copy(currentAction);state.editor.actionVariants.set(currentAction.id,variants);const next=variants[nextType]?copy(variants[nextType]):defaultAction(nextType,currentAction.delay_seconds);next.id=currentAction.id;next.delay_seconds=currentAction.delay_seconds;setPath(state.editor.definition,actionPath,next);editorChange();renderEditor();return;
      }
      inputChanged(event);if(entry?.kind==='editor'&&(path?.endsWith('.rule_id')||path==='match_mode'))renderEditor();
    }
    function submit(event){
      const form=event.target.closest('[data-sc-form]');if(!form)return;event.preventDefault();if(state.busy||state.pending)return;const kind=form.dataset.scForm,entry=dialogFor(form);
      if(kind==='filters'){filterValues(form,'filter',state.filters);if(!validFilterDates(state.filters))return;state.filters.page=1;void load(false);return;}
      if(kind==='editor'){void saveEditor(form);return;}
      if(kind==='discard'){const editor=entry.editor;closeDialog(entry,true);closeDialog(editor,true);return;}
      if(kind==='reload-editor'){void reloadEditor(entry);return;}
      if(!form.reportValidity())return;
      if(['copy','delete'].includes(kind)){void applyMutation(entry);return;}
      if(kind==='delete-all'){void deleteAll(entry);return;}
      if(kind==='test'){void submitTest(entry);return;}
      if(kind==='url-tags'){void submitUrlTags(entry);return;}
      if(kind==='export'){void submitExport(entry);return;}
      if(kind==='import'||kind==='restore'){if(entry.preview)void applyPreview(entry);else void generatePreview(entry);}
    }
    function visibilityChanged(){if(document.hidden){clearTimeout(operationTimer);operationTimer=null;}else if(state.job)void pollJob();}
    function toggle(event){const target=event.target;if(target.matches('[data-sc-advanced]'))state.advanced=target.open;if(target.dataset.scDetails){const id=target.dataset.scDetails;if(target.open){state.expanded.add(id);if(!state.details.has(id))void readRule(id);}else state.expanded.delete(id);}}
    host.classList.add('os-chatbot');
    host.addEventListener('click',click);host.addEventListener('input',inputChanged);host.addEventListener('change',change);host.addEventListener('submit',submit);host.addEventListener('toggle',toggle,true);document.addEventListener('visibilitychange',visibilityChanged);
    const controller={load:()=>load(),unmount(){if(!state.mounted)return;state.mounted=false;state.epoch++;clearTimeout(operationTimer);for(const download of downloads)download.abort();downloads.clear();releaseFields();for(const entry of [...state.dialogs])closeDialog(entry,true);host.removeEventListener('click',click);host.removeEventListener('input',inputChanged);host.removeEventListener('change',change);host.removeEventListener('submit',submit);host.removeEventListener('toggle',toggle,true);document.removeEventListener('visibilitychange',visibilityChanged);host.replaceChildren();host.classList.remove('os-chatbot');host.removeAttribute('aria-busy');state.details.clear();state.catalog=null;state.data=null;state.pending=null;},getState:()=>({loaded:state.loaded,busy:state.busy,pending:state.pending?.kind||null,editor:!!state.editor,dirty:state.editor?.dirty===true})};
    active=controller;render();void load();return controller;
  }
  function unmount(){active?.unmount();active=null;}
  window.OrionSubscriptionChatbot=Object.freeze({mount,unmount});
})();
