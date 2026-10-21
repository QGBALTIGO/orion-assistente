"""Copy reviewed local evidence; does not run operators or access production."""
from pathlib import Path
import hashlib, json, shutil

source = Path(__file__).resolve().parent
target = source.parent / 'orion-marcflow-20261020/docs/marcflow/revisao-manual-20261020'
assert target.is_dir()
names = {
    'railway-native-central-C-results.json', 'railway-native-central-D-results.json',
    'public-after-native-central-D.json',
    'native-central-C-prepared.json', 'native-central-C-prepared-validation.json', 'native-central-C-staged.json',
    'native-central-D-prepared.json', 'native-central-D-staged.json',
    'native-central-E-prepared.json', 'native-central-E-guard-evidence.json', 'native-central-E-staged.json',
    'native-central-F-prepared.json', 'native-central-F-static-proof.json', 'native-central-F-worker-current-config.json',
    'native-central-F-review-and-masking-test.json', 'native-central-F-masking-isolated.log',
    'runtime-168872a-equivalence-and-D-checklist.json', 'post-rollout-docs-checklist.json',
    'ci-168872a-metadata.json', 'ci-168872a-final-evidence.json', 'ci-168872a-constraints-proof.json',
    'ci-168872a-backend.log', 'ci-168872a-qr.log', 'ci-168872a-asr.log', 'ci-168872a-media.log',
    'update_central_rollout_receipt.py',
    'copy_central_rollout_evidence.py', 'probe_central_rollout.py',
    'native-admin-fresh-login-readonly.json',
    'native-admin-central-readonly.png', 'native-admin-central-readonly-top.png',
}
guard = json.loads((source / 'native-central-E-guard-evidence.json').read_text(encoding='utf-8-sig'))
for name, digest in guard['artifacts_sha256'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
    names.add(name)
for phase in ('E', 'F'):
    for name in (f'railway-native-central-{phase}-results.json', f'public-after-native-central-{phase}.json', f'native-central-{phase}-staged.json'):
        if (source / name).is_file(): names.add(name)
if (source / 'final-central-docs-review.json').is_file(): names.add('final-central-docs-review.json')
ui = json.loads((source / 'native-admin-fresh-login-readonly.json').read_text(encoding='utf-8-sig'))
for name, digest in ui['screenshots_sha256'].items():
    assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, name
for name in sorted(names):
    assert Path(name).name == name and (source / name).is_file(), name
    shutil.copyfile(source / name, target / name)
    assert (source / name).read_bytes() == (target / name).read_bytes(), name
print(json.dumps({'raw_artifacts_copied': len(names), 'no_runtime_or_production_execution': True}))
