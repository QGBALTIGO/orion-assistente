"""Static validation only: never imports application, connects, or executes the hook."""
from pathlib import Path
import ast, hashlib, json, shlex

folder=Path(__file__).resolve().parent
plan=json.loads((folder/'native-admin-phase1-prepared.json').read_text())
repo=folder.parent/'orion-marcflow-20261020'
words=shlex.split(plan['api']['pre_deploy_command'])
assert words[:2]==['python','-c'] and len(words)==3
assert words[2]==plan['api']['pre_deploy_code']
tree=ast.parse(words[2])
runs=[]
for n in ast.walk(tree):
    if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='subprocess' and n.func.attr=='run':
        args=n.args[0]
        assert isinstance(args,ast.List) and len(args.elts)==3
        assert ast.unparse(args.elts[0])=='sys.executable'
        assert ast.literal_eval(args.elts[1])=='-m'
        assert any(k.arg=='check' and ast.literal_eval(k.value) is True for k in n.keywords)
        runs.append(ast.literal_eval(args.elts[2]))
assert runs==['scripts.one_time_admin_identity_migration','scripts.wait_for_admin_identity_schema','scripts.one_time_admin_demo_setup','scripts.probe_qr_bridge']
variables=plan['api']['variables']
assert variables['ORION_ADMIN_DEMO_SETUP_PHASE']=='plan'
assert variables['ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256']==''
assert variables['PANEL_LEGACY_ADMIN_IDENTITY_ENABLED']=='0'
assert variables['ORION_ADMIN_DEMO_TENANT_ID']=='82552b25-d0c8-5338-8c42-49f11de59e45'
assert not ({'QR_AI_ENABLED','QR_CHANNELS_ENABLED','INBOX_TOOLS_ENABLED','QR_AI_TENANT_ID','QR_AI_ALLOWED_SENDERS','QR_BRIDGE_TOKEN','INTEGRATION_FERNET_KEY'}&variables.keys())
assert plan['qr_service_changed'] is False and plan['volumes_changed'] is False
assert plan['worker']['staged_in_phase1'] is False
expected=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='expected' for t in n.targets))
pins={}
for line in (repo/'requirements.lock').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        name,version=line.strip().split('==')
        pins[name.lower().replace('_','-')]=version
for name,version in expected.items():assert pins[name.lower().replace('_','-')]==version,name
migration=(repo/'alembic/versions/0030_legacy_admin_identity.py').read_bytes().replace(b'\r\n',b'\n')
assert hashlib.sha256(migration).hexdigest()=='09c070122ef84c0af272de1b9ec853585ba209d6f7ebaff782fe770c8eaa1a96'
result={'status':'passed','scope':'static preparation; no hook/application/network execution','command_roundtrip':True,'fail_closed_subprocess_order':runs,'runtime_versions_checked_against_lock':len(expected),'structural_migration_sha256':hashlib.sha256(migration).hexdigest(),'business_changes_authorized_or_applied':False,'worker_staged_in_phase1':False}
(folder/'native-admin-phase1-prepared-validation.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))

