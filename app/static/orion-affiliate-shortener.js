// Native Affiliate workspace: links are owned by the connected Spoo.me account.
export function mount({host,tenantId,request,icon,showNotice}) {
  const controller=new AbortController(), signal=controller.signal;
  const root="/api/v1/afiliados/"+encodeURIComponent(tenantId)+"/api/links/";
  const $=s=>host.querySelector(s);
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const btn=(label,glyph,attrs="",primary=false)=>'<button type="button" class="os-button'+(primary?'':' os-outline')+'" '+attrs+'>'+icon(glyph)+esc(label)+'</button>';
  const notice=(text,error=false)=>'<div class="os-notice'+(error?' os-error':'')+'" role="'+(error?'alert':'status')+'">'+esc(text)+'</div>';
  let key="", connected=false, available=null, busy=false, page=1, query="", history=null, historyError="", result=null, stats=null;
  const fmt=n=>Number.isInteger(n)&&n>=0?n.toLocaleString("pt-BR"):"—";
  function validShort(value) {
    try{const u=new URL(value);return u.origin==="https://spoo.me"&&/^\/[A-Za-z0-9_-]{3,16}$/.test(u.pathname)&&!u.search&&!u.hash&&!u.username&&!u.password;}catch{return false;}
  }
  function call(action,data={}){return request(root+action,"POST",{...data,api_key:key},{signal});}
  function notify(text,error=false){if(!signal.aborted)showNotice(text,error);}
  function message(text,error=false){
    const node=$("[data-shortener-message]");if(!node)return;
    node.textContent=text;node.classList.toggle("os-error",error);node.hidden=!text;node.setAttribute("role",error?"alert":"status");
  }
  function setBusy(value){
    busy=value;
    for(const el of host.querySelectorAll("button,input"))if(!el.closest("dialog"))el.disabled=value;
    host.querySelector(".af-shortener")?.setAttribute("aria-busy",String(value));
  }
  function header(){return '<header class="os-title-row"><div><h1>Encurtador de links</h1><p>Crie links curtos personalizados para redes sociais, sites, grupos, checkouts e outros destinos permitidos.</p></div></header>';}
  function cardHead(glyph,title,subtitle){return '<div class="af-card-head"><span class="os-square-icon">'+icon(glyph)+'</span><div><h3>'+esc(title)+'</h3><p>'+esc(subtitle)+'</p></div></div>';}
  function shell(){
    if(signal.aborted)return;
    host.innerHTML='<div class="af-native-content af-page af-shortener">'+header()+
      '<section class="os-card af-card">'+cardHead("link","Spoo.me","Domínio genérico compartilhado, sem redirecionamento pela Orion.")+
      (available===null?'<div class="os-loading" role="status"><span class="os-spinner"></span>Conferindo disponibilidade…</div>':
        available===false?notice("O encurtamento dentro da Orion aguarda liberação do provedor. Por enquanto, você pode criar e gerenciar seus links diretamente no Spoo.me.")+'<div class="af-row">'+btn("Verificar novamente","refresh",'data-shortener="check"')+'<a class="os-button os-outline" href="https://spoo.me/" target="_blank" rel="noopener noreferrer">Abrir Spoo.me '+icon("external")+'</a></div>':
        connected?'<div class="af-row"><span class="os-badge os-success">Conectado nesta sessão</span>'+btn("Desconectar","close",'data-shortener="disconnect"')+'</div>':
        '<form class="os-form af-form" data-shortener-form="connect"><label for="shortener-key">Chave da sua conta Spoo.me<input id="shortener-key" type="password" name="key" required minlength="16" maxlength="256" autocomplete="off" spellcheck="false"></label>'+
        '<p class="af-muted">Use a chave da sua própria conta, com permissões para links e estatísticas. A chave é utilizada apenas nesta tela e não é gravada no banco ou armazenamento do navegador pela Orion. O histórico permanece na sua conta Spoo.me.</p>'+
        '<div class="af-row"><button class="os-button" type="submit">'+icon("check")+'Conectar</button><a class="os-button os-outline" href="https://spoo.me/" target="_blank" rel="noopener noreferrer">Abrir Spoo.me '+icon("external")+'</a></div></form>')+
      '<p class="os-notice" data-shortener-message role="status" hidden></p></section>'+
      (connected?'<div class="af-shortener-grid"><section class="os-card af-card">'+cardHead("plus","Criar link","Personalize o final ou deixe o serviço gerar um código.")+
      '<form class="os-form af-form" data-shortener-form="create"><label for="shortener-url">URL de destino<input id="shortener-url" name="url" type="url" required maxlength="4096" placeholder="https://exemplo.com/pagina"></label>'+
      '<label for="shortener-alias">Endereço personalizado (opcional)<span class="af-shortener-prefix"><span>spoo.me/</span><input id="shortener-alias" name="alias" maxlength="16" pattern="[A-Za-z0-9_\\-]{3,16}" placeholder="minhas-ofertas" autocomplete="off"></span><small>3 a 16 caracteres: letras, números, hífen ou sublinhado.</small></label>'+
      '<label class="os-check af-shortener-consent"><input type="checkbox" name="accepted" required><span>Tenho autorização para compartilhar o destino e vou respeitar as regras do serviço e do marketplace, quando aplicável.</span></label>'+
      '<button class="os-button" type="submit">'+icon("link")+'Encurtar link</button></form><div data-shortener-result></div></section>'+
      '<section class="os-card af-card">'+cardHead("info","Sobre seus links","Você mantém o controle na sua conta externa.")+'<p>Os endereços pertencem ao domínio Spoo.me e dependem de disponibilidade. A Orion não cobra por domínio, não altera o destino e não contorna regras de afiliados. Use a URL original, não uma cadeia de encurtadores.</p><p class="af-muted">O provedor recebe o endereço de destino. Não publique informações confidenciais.</p></section></div>'+
      '<section class="os-card af-card">'+cardHead("list","Meus links","Histórico da conta Spoo.me conectada.")+
      '<form class="af-shortener-search" data-shortener-form="search"><label for="shortener-search" class="af-muted">Buscar links</label><div class="af-row"><input id="shortener-search" type="search" name="search" maxlength="100" placeholder="Buscar por endereço ou destino" value="'+esc(query)+'"><button type="submit" class="os-button os-outline">'+icon("search")+'Buscar</button>'+btn("Atualizar","refresh",'data-shortener="refresh"')+'</div></form>'+
      '<div data-shortener-history></div><div data-shortener-stats></div></section>':'')+
      '<dialog class="os-dialog" data-shortener-dialog aria-labelledby="shortener-dialog-title"><h2 id="shortener-dialog-title"></h2><p data-shortener-dialog-text></p><form method="dialog" class="os-form-actions"><button type="submit" class="os-button os-outline" value="cancel" autofocus>Voltar</button><button type="submit" class="os-button os-danger" value="confirm">Confirmar</button></form></dialog></div>';
    window.OrionFields?.refresh(host);renderResult();renderHistory();renderStats();
  }
  function renderResult(){
    const target=$("[data-shortener-result]");if(!target)return;
    target.innerHTML=result&&validShort(result.short_url)?'<div class="af-shortener-result"><small>Link criado</small><strong>'+esc(result.short_url)+'</strong><div class="af-row">'+btn("Copiar","copy",'data-shortener="copy-result"')+'</div></div>':"";
  }
  function renderHistory(){
    const target=$("[data-shortener-history]");if(!target)return;
    if(historyError){target.innerHTML=notice(historyError,true);return;}
    if(!history){target.innerHTML='<div class="os-loading" role="status"><span class="os-spinner"></span>Carregando histórico…</div>';return;}
    const rows=history.items||[];
    target.innerHTML='<p class="af-muted">'+fmt(history.total)+' links na conta'+(history.omitted?' · '+fmt(history.omitted)+' links de outros formatos não exibidos nesta página':'')+'</p>'+
      (rows.length?'<div class="af-list">'+rows.map(row=>{
        const id=esc(row.id), active=row.status==="ACTIVE", inactive=row.status==="INACTIVE";
        return '<article class="af-shortener-item"><div class="af-shortener-item-main"><strong>'+esc(row.short_url)+'</strong><small title="'+esc(row.long_url)+'">'+esc(row.long_url)+'</small><div class="af-row"><span class="os-badge '+(active?'os-success':'os-neutral')+'">'+(active?'Ativo':inactive?'Pausado':'Estado indisponível')+'</span><span class="af-muted">'+fmt(row.total_clicks)+' cliques</span></div></div><div class="af-row af-shortener-item-actions">'+
        (validShort(row.short_url)?btn("Copiar","copy",'data-shortener="copy" data-id="'+id+'"'):"")+btn("Estatísticas","chart",'data-shortener="stats" data-id="'+id+'"')+
        ((active||inactive)?btn(active?"Pausar":"Ativar",active?"close":"check",'data-shortener="toggle" data-id="'+id+'"'):"")+btn("Excluir","trash",'data-shortener="delete" data-id="'+id+'"')+'</div></article>';
      }).join("")+'</div>':'<div class="af-empty"><p>Nenhum link encontrado nesta página. Crie um link ou altere a busca.</p></div>')+
      '<div class="af-row af-shortener-pagination">'+(page>1?btn("Anterior","left",'data-shortener="prev"'):"")+'<span class="af-muted">Página '+page+'</span>'+(history.has_next?btn("Próxima","chevron",'data-shortener="next"'):"")+'</div>';
  }
  function renderStats(){
    const target=$("[data-shortener-stats]");if(!target)return;
    target.innerHTML=stats?'<div class="af-shortener-result"><strong>Estatísticas · últimos 7 dias</strong><p>'+esc(stats.url)+'</p><div class="af-row"><span>Cliques: <strong>'+fmt(stats.total_clicks)+'</strong></span><span>Únicos estimados: <strong>'+fmt(stats.unique_clicks)+'</strong></span>'+btn("Fechar","close",'data-shortener="close-stats"')+'</div></div>':"";
  }
  async function check(){
    setBusy(true);message("");
    try{const response=await request(root+"status","GET",undefined,{signal});if(signal.aborted)return;available=response?.enabled===true;shell();}
    catch(error){if(!signal.aborted){available=false;shell();message(error.message||"Não foi possível conferir a disponibilidade.",true);}}
    finally{if(!signal.aborted)setBusy(false);}
  }
  async function loadHistory(targetPage=page,targetQuery=query){
    historyError="";
    try{
      const data=await call("list",{page:targetPage,search:targetQuery});
      if(signal.aborted)return false;
      if(!Array.isArray(data?.items)||data.page!==targetPage)throw Error("Resposta inesperada do histórico.");
      history=data;page=targetPage;query=targetQuery;renderHistory();return true;
    }catch(error){if(!signal.aborted){historyError=error.message||"Não foi possível atualizar o histórico.";renderHistory();}return false;}
  }
  function clear(){key="";connected=false;history=null;historyError="";stats=null;result=null;page=1;query="";shell();}
  async function confirmAction(title,text){
    const dialog=$("[data-shortener-dialog]");if(!dialog?.showModal)return false;
    $("#shortener-dialog-title").textContent=title;$("[data-shortener-dialog-text]").textContent=text;
    dialog.returnValue="cancel";dialog.showModal();
    return new Promise(resolve=>dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true}));
  }
  async function copy(value){
    if(!validShort(value))throw Error("Endereço curto inválido.");
    if(!navigator.clipboard?.writeText)throw Error("Cópia automática indisponível. Selecione o endereço para copiar.");
    await navigator.clipboard.writeText(value);notify("Link copiado.");
  }
  host.addEventListener("submit",async event=>{
    const form=event.target.closest("[data-shortener-form]");if(!form)return;
    event.preventDefault();if(busy||signal.aborted)return;
    const values=new FormData(form),action=form.dataset.shortenerForm;setBusy(true);message("");
    try{
      if(action==="connect"){
        key=String(values.get("key")||"").trim();form.elements.key.value="";
        const data=await call("list",{page:1});
        if(signal.aborted)return;
        if(!Array.isArray(data?.items)||data.page!==1)throw Error("O provedor não confirmou a conexão.");
        connected=true;history=data;page=1;shell();notify("Conta Spoo.me conectada nesta sessão.");
      }else if(action==="create"&&connected){
        result=null;renderResult();
        const created=await call("create",{url:String(values.get("url")||"").trim(),alias:String(values.get("alias")||"").trim(),accepted:values.get("accepted")==="on"});
        if(signal.aborted)return;
        if(!validShort(created?.short_url))throw Error("O provedor não confirmou o link.");
        result=created;form.reset();renderResult();notify("Link criado.");await loadHistory(1,"");
        if($("#shortener-search"))$("#shortener-search").value="";
      }else if(action==="search"&&connected){await loadHistory(1,String(values.get("search")||"").trim());}
    }catch(error){if(action==="connect"){key="";connected=false;}if(!signal.aborted)message(error.message||"Não foi possível concluir a operação.",true);}
    finally{if(!signal.aborted)setBusy(false);}
  },{signal});
  host.addEventListener("click",async event=>{
    const button=event.target.closest("[data-shortener]");if(!button||busy||signal.aborted)return;
    const action=button.dataset.shortener;
    if(action==="check"){await check();return;}
    if(action==="disconnect"){clear();return;}
    if(!connected)return;
    if(action==="close-stats"){stats=null;renderStats();return;}
    const row=history?.items?.find(item=>item.id===button.dataset.id);
    if(action==="copy"||action==="copy-result"){
      try{await copy(action==="copy-result"?result?.short_url:row?.short_url);}catch(error){notify(error.message,true);}return;
    }
    setBusy(true);message("");
    try{
      if(action==="refresh")await loadHistory();
      else if(action==="prev"&&page>1)await loadHistory(page-1);
      else if(action==="next"&&history?.has_next)await loadHistory(page+1);
      else if(action==="stats"&&row){
        const data=await call("stats",{id:row.id});if(signal.aborted)return;
        stats={...data,url:row.short_url};renderStats();
      }else if(action==="toggle"&&row){
        const next=row.status==="ACTIVE"?"INACTIVE":"ACTIVE";
        await call("status",{id:row.id,status:next});if(signal.aborted)return;
        notify(next==="ACTIVE"?"Link ativado.":"Link pausado.");await loadHistory();
      }else if(action==="delete"&&row){
        if(!await confirmAction("Excluir link?","O endereço poderá parar de funcionar para todas as pessoas que o receberam."))return;
        if(signal.aborted)return;
        await call("delete",{id:row.id});if(signal.aborted)return;
        stats=null;if(result?.short_url===row.short_url){result=null;renderResult();}renderStats();notify("Link excluído.");await loadHistory();
      }
    }catch(error){if(!signal.aborted)message(error.message||"Não foi possível concluir a operação.",true);}
    finally{if(!signal.aborted)setBusy(false);}
  },{signal});
  shell();void check();
  return {setView(){},unmount(){key="";const dialog=$("[data-shortener-dialog]");if(dialog?.open)dialog.close("cancel");controller.abort();host.replaceChildren();}};
}
