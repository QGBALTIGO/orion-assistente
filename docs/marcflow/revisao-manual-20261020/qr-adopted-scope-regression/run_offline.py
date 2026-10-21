"""Runs existing unit tests with production configuration removed and network denied."""
from pathlib import Path
import os, sys, socket, json, datetime

ROOT = Path(r'C:\Users\kayky\Documents\Playground\orion-marcflow-20261020')
OUT = Path(__file__).resolve().parent
for key in list(os.environ):
    if any(x in key.upper() for x in ('TOKEN','SECRET','PASSWORD','API_KEY','DATABASE_URL','REDIS_URL')) or key.startswith(('QR_', 'GROQ_', 'META_', 'AUTOMATION_', 'INBOX_', 'MARKETING_', 'GOOGLE_', 'BUSINESS_PROFILES_', 'COMMERCE_', 'ORION_', 'APP_', 'PANEL_', 'WHATSAPP_')):
        os.environ.pop(key, None)
os.environ.update(PYTHONDONTWRITEBYTECODE='1', QR_AI_ENABLED='0', QR_CHANNELS_ENABLED='0',
    INBOX_CONTROL_ENABLED='0', INBOX_SEND_ENABLED='0', META_LIVE_ENABLED='0', AUTOMATION_EXECUTION_ENABLED='0')
sys.dont_write_bytecode = True
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
blocked = []
def check(address):
    if isinstance(address, tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
        blocked.append(str(address[0]))
        raise RuntimeError('audit_external_network_denied')
def connect(self,address):
    check(address)
    return original_connect(self,address)
def connect_ex(self,address):
    check(address)
    return original_connect_ex(self,address)
def dns(host,*args,**kwargs):
    if host not in (None,'127.0.0.1','::1','localhost',b'localhost'):
        blocked.append(str(host))
        raise RuntimeError('audit_external_dns_denied')
    return original_dns(host,*args,**kwargs)
socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns
import pytest
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
code = pytest.main(['tests/unit','-m','not integration','-q','-p','no:cacheprovider',f'--junitxml={OUT / "backend-junit.xml"}',*sys.argv[1:]])
(OUT/'backend-run.json').write_text(json.dumps({'started_at':started,'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'exit_code':int(code),'external_network_blocked_attempts':blocked,'scope':'existing tests/unit; mock providers and fake databases; no live model or real integration'},ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(code)
