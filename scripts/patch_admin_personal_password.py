"""Wire the authenticated self-service admin password feature into the published Orion API."""
from pathlib import Path
import sys

def once(s,old,new):
    if s.count(old)!=1:
        raise RuntimeError("Unexpected integration anchor: "+old[:130])
    return s.replace(old,new,1)

def patch(root):
    root=Path(root)
    p=root/"app/panel_auth.py"
    s=p.read_text()
    s=once(s,'''        legacy_admin = valid_user and _password_valid(payload.password)
        account = None
        if legacy_admin:
            from app.legacy_admin_identity import login_identity
            account = await login_identity()
        else:''','''        legacy_admin = False
        account = None
        if valid_user:
            from app.legacy_admin_password import authenticate
            legacy_admin, account = await authenticate(payload.password, _password_valid)
        else:''')
    p.write_text(s)
    p=root/"app/account_auth.py"
    s=p.read_text()
    s=once(s,'''    if not _password_valid(payload.legacy_password):
        raise HTTPException(403,"Autorização administrativa recusada")''','''    from app.legacy_admin_password import verify_authorization
    if not await verify_authorization(payload.legacy_password, _password_valid):
        raise HTTPException(403,"Autorização administrativa recusada")''')
    s+='''
\n@router.post("/admin/password", include_in_schema=False)
async def admin_own_password(request: Request, data: dict = Depends(panel_actor)):
    from app.legacy_admin_password import change
    return await change(request, data, _password_valid)
'''
    p.write_text(s)
    p=root/"app/static/orion-admin-center.js"
    s=p.read_text()
    s=once(s,'onMaintenanceStart:null,epoch:', 'onMaintenanceStart:null,onPasswordChanged:null,epoch:')
    s=once(s,'btn("Demonstrações","demos",','btn("Alterar minha senha","own-password",{cls:"os-button os-outline os-small",icon:"shield",text:"Alterar senha"})+btn("Demonstrações","demos",')
    s=once(s,'  function closeDialog(){','''  function closeDialog(){
    if(state.modalType==="own-password"&&!state.busy)$("osDialog")?.querySelector('[data-admin-form="own-password"]')?.reset();''')
    s=once(s,'  async function submit(event){','''  function showOwnPassword(){
    openDialog("own-password","Alterar minha senha",
      '<p class="os-help">Confirme sua senha atual. Após salvar, entre novamente com a nova senha.</p>'+
      '<form class="os-form" data-admin-form="own-password" autocomplete="on">'+
      '<div class="os-field"><label for="adminCurrentPassword">Senha atual</label><input id="adminCurrentPassword" name="current_password" type="password" autocomplete="current-password" required maxlength="200"></div>'+
      '<div class="os-field"><label for="adminNewPassword">Nova senha</label><input id="adminNewPassword" name="new_password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"><small>Use pelo menos 8 caracteres. Recomendamos uma frase-senha longa e única.</small></div>'+
      '<div class="os-field"><label for="adminConfirmPassword">Confirmar nova senha</label><input id="adminConfirmPassword" name="confirm_password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></div>'+
      '<div class="os-form-message" role="alert"></div><div class="os-form-actions">'+
      '<button type="button" class="os-button os-outline" data-admin-control="close">Cancelar</button>'+
      '<button type="submit" class="os-button">Alterar senha</button></div></form>',{});
  }
  async function saveOwnPassword(form){
    const values=Object.fromEntries(new FormData(form));
    if(values.new_password!==values.confirm_password){feedback("As novas senhas não coincidem.",true);return;}
    if(values.current_password===values.new_password){feedback("Escolha uma senha diferente da atual.",true);return;}
    const epoch=state.epoch;state.busy=true;form.setAttribute("aria-busy","true");
    form.querySelectorAll("button,input").forEach(el=>el.disabled=true);
    try{
      const result=await state.request("/auth/admin/password","POST",values);
      if(epoch!==state.epoch)return;
      if(result?.ok!==true||result?.requires_login!==true)throw Error("Alteração ainda não confirmada. Entre novamente para conferir.");
      const completed=state.onPasswordChanged;
      form.reset();state.busy=false;closeDialog();
      if(completed)completed(result.message);else window.location.assign("/painel?aba=member");
    }catch(error){
      if(epoch!==state.epoch)return;
      form.reset();state.busy=false;form.setAttribute("aria-busy","false");
      form.querySelectorAll("button,input").forEach(el=>el.disabled=false);
      feedback(error.message||"Não foi possível confirmar a alteração. Entre novamente para conferir.",true);
    }
  }
  async function submit(event){''')
    s=once(s,"    if(form.dataset.adminForm==='plan'){reviewPlan();return;}","    if(form.dataset.adminForm==='own-password'){await saveOwnPassword(form);return;}\n    if(form.dataset.adminForm==='plan'){reviewPlan();return;}")
    s=once(s,'    if(key==="demos"){','    if(key==="own-password"){showOwnPassword();return;}\n    if(key==="demos"){')
    s=once(s,'const context={request:state.request,role:state.role,onMaintenanceStart:state.onMaintenanceStart};','const context={request:state.request,role:state.role,onMaintenanceStart:state.onMaintenanceStart,onPasswordChanged:state.onPasswordChanged};')
    s=once(s,'function open({request,role,onMaintenanceStart})','function open({request,role,onMaintenanceStart,onPasswordChanged})')
    s=once(s,'state.request=request;state.role=role;state.onMaintenanceStart=onMaintenanceStart;','state.request=request;state.role=role;state.onMaintenanceStart=onMaintenanceStart;state.onPasswordChanged=onPasswordChanged;')
    s=once(s,'    state.request=null;state.role=null;','    state.request=null;state.role=null;state.onPasswordChanged=null;')
    s=once(s,'        if(!state.owned)return;\n        state.owned=false;state.busy=false;state.selected=null;','        if(!state.owned)return;\n        if(state.modalType==="own-password")dialog.querySelector(\'[data-admin-form="own-password"]\')?.reset();\n        state.owned=false;state.busy=false;state.selected=null;')
    p.write_text(s)
    p=root/"app/static/panel.html"
    s=p.read_text()
    s=once(s,'onMaintenanceStart:()=>window.location.assign("/painel?aba=dashboard"),','onMaintenanceStart:()=>window.location.assign("/painel?aba=dashboard"),\n    onPasswordChanged:message=>{showLogin();messageAuth(message||"Senha alterada. Entre novamente.");},')
    p.write_text(s)
    # Keep the existing Railway predeploy entrypoint; its successor is an
    # ordinary backed-up schema migration, not a credential operation.
    p=root/"scripts/backup_and_migrate_plan_cycle.py"
    s=p.read_text()
    s=once(s, 'if __name__ == "__main__":\n    raise SystemExit(main())', 'if __name__ == "__main__":\n    from scripts.backup_and_migrate_admin_personal_password import main as successor\n    raise SystemExit(successor())')
    p.write_text(s)

if __name__=="__main__":
    patch(Path(sys.argv[1] if len(sys.argv)>1 else "."))
