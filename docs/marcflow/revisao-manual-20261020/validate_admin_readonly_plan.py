"""Read-only static check. Never execute the hook, import the app, or connect."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

folder = Path(__file__).resolve().parent
repo = folder.parent / 'orion-marcflow-20261020'
if not (repo / '.git').exists():
    repo = folder.parents[2]
plan = json.loads((folder / 'native-admin-lease-readonly-prepared.json').read_text(encoding='utf-8-sig'))
candidate = plan['candidate_commit']
assert re.fullmatch('[0-9a-f]{40}', candidate)
assert subprocess.check_output(['git', 'rev-parse', candidate], cwd=repo, text=True).strip() == candidate
words = shlex.split(plan['hook'])
assert len(words) == 3 and words[:2] == ['python', '-c'] and words[2] == plan['code']
tree = ast.parse(words[2])
runs = []
for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == 'subprocess'
            and node.func.attr == 'run'):
        continue
    argv = node.args[0]
    assert isinstance(argv, ast.List) and len(argv.elts) == 3
    assert ast.unparse(argv.elts[0]) == 'sys.executable'
    assert ast.literal_eval(argv.elts[1]) == '-m'
    assert any(kw.arg == 'check' and ast.literal_eval(kw.value) is True for kw in node.keywords)
    runs.append(ast.literal_eval(argv.elts[2]))
assert runs == ['scripts.wait_for_admin_identity_schema', 'scripts.one_time_admin_demo_setup', 'scripts.probe_qr_bridge']
assert plan['service'] == 'API only' and plan['no_business_writes'] is True
assert plan['worker_or_qr_redeploy'] is False and plan['approved_old_hash_not_reused'] is True
assert plan['variables'] == {
    'ORION_RUN_ADMIN_IDENTITY_MIGRATION': '0',
    'ORION_RUN_ADMIN_DEMO_SETUP': '1',
    'ORION_ADMIN_DEMO_SETUP_RUN_ID': 'dec65625-cd92-4dfb-a2da-51842420fdeb',
    'ORION_ADMIN_DEMO_SETUP_PHASE': 'plan',
    'ORION_ADMIN_DEMO_TENANT_ID': '82552b25-d0c8-5338-8c42-49f11de59e45',
    'ORION_ADMIN_DEMO_APPROVED_PLAN_SHA256': '',
    'PANEL_LEGACY_ADMIN_IDENTITY_ENABLED': '0',
}
expected = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'expected' for target in node.targets))
lock = subprocess.check_output(['git', 'show', candidate + ':requirements.lock'], cwd=repo, text=True)
pins = dict(line.strip().split('==') for line in lock.splitlines() if line.strip() and not line.lstrip().startswith('#'))
normalize = lambda name: name.lower().replace('_', '-')
pins = {normalize(name): value for name, value in pins.items()}
assert len(expected) == 18
assert all(pins[normalize(name)] == version for name, version in expected.items())
migration = subprocess.check_output(['git', 'show', candidate + ':alembic/versions/0030_legacy_admin_identity.py'], cwd=repo)
assert hashlib.sha256(migration).hexdigest() == '09c070122ef84c0af272de1b9ec853585ba209d6f7ebaff782fe770c8eaa1a96'
result = {
    'status': 'passed', 'candidate_commit': candidate,
    'scope': 'Static preparation only; no hook, application, database, or network execution',
    'ordered_fail_closed_modules': runs, 'runtime_versions_checked_against_candidate_lock': len(expected),
    'ddl_operator_included': False, 'native_admin_enabled': False, 'business_writes': False,
    'worker_qr_or_volume_changes': False, 'approved_old_hash_reused': False,
    'plan_file_sha256': hashlib.sha256((folder / 'native-admin-lease-readonly-prepared.json').read_bytes()).hexdigest(),
}
(folder / 'native-admin-lease-readonly-validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result))
