"""Execute pytest with real local infrastructure and external networking denied.

No credentials are loaded here. The caller must provision disposable databases;
ordinary pytest remains unchanged. This runner cannot reach a real provider.
"""
import json
import os
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
LOCAL={None,'localhost','127.0.0.1','::1',b'localhost',b'127.0.0.1',b'::1'}
for key,value in os.environ.items():
    if key.endswith(('DATABASE_URL','TEST_OWNER_URL','REDIS_URL')) and value:
        if urlsplit(value.replace('postgresql+asyncpg:','postgresql:').replace('postgresql+psycopg:','postgresql:')).hostname not in LOCAL:
            raise RuntimeError('Local disposable infrastructure required: '+key)

connect,connect_ex,dns=socket.socket.connect,socket.socket.connect_ex,socket.getaddrinfo
blocked=[]
def allowed(address):
    if isinstance(address,tuple) and address[0] not in LOCAL:
        blocked.append(str(address[0]));raise RuntimeError('External network denied in isolated test')
def guarded_connect(self,address):allowed(address);return connect(self,address)
def guarded_connect_ex(self,address):allowed(address);return connect_ex(self,address)
def guarded_dns(host,*args,**kwargs):
    if host not in LOCAL:
        blocked.append(str(host));raise RuntimeError('External DNS denied in isolated test')
    return dns(host,*args,**kwargs)
socket.socket.connect=guarded_connect
socket.socket.connect_ex=guarded_connect_ex
socket.getaddrinfo=guarded_dns
import pytest
code=pytest.main(sys.argv[1:])
if os.getenv('ORION_TEST_NETWORK_AUDIT'):
    Path(os.environ['ORION_TEST_NETWORK_AUDIT']).write_text(json.dumps({'exit_code':int(code),'blocked':blocked,'external_network':'denied'}),encoding='utf-8')
raise SystemExit(code)
