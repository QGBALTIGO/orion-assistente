/* Invitation tokens stay in the URL fragment; OTP values are never persisted. */
(()=>{'use strict';
const fields=window.OrionFields;fields.bind(document);
const $=id=>document.getElementById(id),message=$('inviteMessage');
let token='',csrf='',busy=false,account=null,invitation=null;
try{token=new URLSearchParams(location.hash.slice(1)).get('token')||sessionStorage.getItem('orion_pending_invitation')||'';}catch{}
const notice=(text,error=false)=>{message.textContent=text;message.classList.toggle('error',error)};
const verificationNotice=(text,error=false)=>{$('inviteVerifyStatus').textContent=text;$('inviteVerifyStatus').classList.toggle('error',error)};
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...(csrf?{'X-Orion-CSRF':csrf}:{})},...(data?{body:JSON.stringify(data)}:{})});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Não foi possível concluir. Tente novamente.');return d;}
function clearToken(){try{sessionStorage.removeItem('orion_pending_invitation')}catch{}}
function lock(value){busy=value;$('inviteAccept').disabled=value;$('inviteVerifyForm').setAttribute('aria-busy',String(value));$('inviteVerifyForm').querySelectorAll('button,input').forEach(node=>node.disabled=value);}
async function refreshAccount(){
 account=null;$('inviteAccept').hidden=true;$('inviteVerifyForm').hidden=true;
 try{const me=await api('/auth/me');csrf=me.csrf||'';
  if(me.account&&me.email){account=me;$('inviteAccount').textContent='Conta: '+(me.name||me.email);$('inviteLogin').hidden=true;
   const pending=invitation?.status!=='approved';$('inviteAccept').hidden=!pending||me.email_verified!==true;
   $('inviteVerifyForm').hidden=!pending||me.email_verified===true;$('inviteVerifyEmail').textContent=me.email;
  }else{$('inviteAccount').textContent='Entre com sua conta pessoal para aceitar o convite.';$('inviteLogin').hidden=false;}
 }catch{$('inviteLogin').hidden=false;}
}
$('inviteLogin').addEventListener('click',()=>{try{sessionStorage.setItem('orion_pending_invitation',token)}catch{}});
$('inviteResend').addEventListener('click',async()=>{
 if(busy||!account?.email||account.email_verified===true)return;lock(true);verificationNotice('Solicitando código…');
 try{const d=await api('/auth/verify/resend',{email:account.email});$('inviteCode').value='';fields.clearError($('inviteCode'));verificationNotice(d.message||'Solicitação registrada. Aguarde o código no seu e-mail.');}
 catch(error){verificationNotice(error.message,true);}finally{lock(false);}
});
$('inviteVerifyForm').addEventListener('submit',async event=>{
 event.preventDefault();const form=event.currentTarget;if(busy||!account?.email||account.email_verified===true||!fields.validateForm(form)||!form.reportValidity())return;
 const email=account.email,code=fields.otpValue($('inviteCode').value,{required:true});lock(true);verificationNotice('Confirmando e-mail…');
 try{await api('/auth/verify',{email,code});$('inviteCode').value='';fields.clearError($('inviteCode'));await refreshAccount();
  if(account?.email===email&&account.email_verified===true)notice('E-mail confirmado. Confira o convite e clique em Aceitar e solicitar acesso.');
  else notice('Não foi possível confirmar a conta atual. Confira seu acesso e tente novamente.',true);
 }catch(error){verificationNotice(error.message,true);}finally{lock(false);}
});
$('inviteAccept').addEventListener('click',async()=>{
 if(busy||account?.email_verified!==true)return;lock(true);
 try{const d=await api('/api/v1/team-invitations/accept',{token});notice(d.status==='approved'?'Seu acesso já foi aprovado. Entre no Orion para continuar.':'Aceite registrado. Aguarde a aprovação do proprietário.');$('inviteAccept').hidden=true;$('inviteVerifyForm').hidden=true;clearToken();history.replaceState(null,'','/convite');}
 catch(error){notice(error.message,true);}finally{lock(false);}
});
(async()=>{
 if(!/^[A-Za-z0-9_-]{43}$/.test(token)){notice('Este link não contém um convite válido. Peça um novo link ao proprietário.',true);return;}
 try{const d=await api('/api/v1/team-invitations/preview',{token});invitation=d;$('inviteBusiness').textContent=d.business_name;$('inviteRole').textContent=d.role==='editor'?'Editor — gestão dos dados operacionais':'Leitor — consulta dos dados da empresa';$('inviteExpiry').textContent=new Date(d.expires_at).toLocaleString('pt-BR');$('inviteDetails').hidden=false;notice(d.email_bound?'Use a conta com o e-mail indicado no convite.':'Entre com sua conta pessoal para solicitar acesso.');await refreshAccount();
  if(d.status==='approved'){notice('Convite aprovado. Seu acesso está disponível no Orion.');clearToken();}
 }catch(error){notice(error.message,true);clearToken();}
})();
})();
