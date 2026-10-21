"""Read-only central/session/assets proof. No authentication or send requests."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, httpx, json, re, subprocess, sys

here = Path(__file__).resolve().parent
repo = here.parent / 'orion-marcflow-20261020'
phase, revision = sys.argv[1:3]
assert phase in ('E', 'F') and re.fullmatch('[0-9a-f]{40}', revision)
result = {'verified_at_utc': datetime.now(timezone.utc).isoformat(), 'phase': phase,
          'scope': 'GET only, no login/provider/send calls', 'assets_compared_to': revision,
          'health': [], 'assets': []}
base = 'https://orion-assistente-production.up.railway.app'
with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
    for path in ('/health', '/health/whatsapp', '/health/central'):
        response = client.get(base + path)
        raw = response.json()
        allowed = ('status', 'bridge', 'legacy_ready', 'session_preserved')
        body = {key: raw[key] for key in allowed if key in raw}
        assert response.status_code == 200 and body.get('status') == 'ok', path
        if path == '/health/central':
            assert body.get('legacy_ready') is True and body.get('session_preserved') is True
        result['health'].append({'path': path, 'status': response.status_code, 'body': body})
    for filename in ('orion-suite.js', 'orion-channels.js', 'orion-conversations.js'):
        expected = subprocess.check_output(['git', 'show', revision + ':app/static/' + filename], cwd=repo)
        response = client.get(base + '/panel-assets/' + filename)
        assert response.status_code == 200 and response.content == expected, filename
        digest = lambda content: hashlib.sha256(content).hexdigest()
        result['assets'].append({'path': '/panel-assets/' + filename, 'status': response.status_code,
                                'expected_sha256': digest(expected), 'actual_sha256': digest(response.content),
                                'matches_commit': True})
result['adapter_ready_connected_and_same_session_proof'] = True
output = here / f'public-after-native-central-{phase}.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'phase': phase, 'health_checks_passed': len(result['health']), 'assets_matched': len(result['assets']), 'output': str(output)}))
