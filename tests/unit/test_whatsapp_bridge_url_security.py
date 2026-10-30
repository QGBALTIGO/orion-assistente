"""Security contract for bridge private URL and QR token destination."""
import pytest
from fastapi import HTTPException

from app.qr_api import _config


@pytest.fixture(autouse=True)
def secret(monkeypatch):
    monkeypatch.setenv('QR_BRIDGE_TOKEN','synthetic-sealed-internal-key-'+'x'*40)


@pytest.mark.parametrize('host',[
  'http://orion-qr-bridge.railway.internal:3000',
  'http://orion-qr-bridge.railway.internal:3000/'
])
def test_only_exact_private_bridge_origin_is_accepted(monkeypatch,host):
    monkeypatch.setenv('QR_BRIDGE_URL',host)
    url,token=_config()
    assert url=='http://orion-qr-bridge.railway.internal:3000'
    assert len(token)>=32


@pytest.mark.parametrize('bad_url',[
    '',
    'http://evil.example/test.railway.internal:3000',
    'http://evil.example//orion-qr-bridge.railway.internal:3000',
    'http://evil.example/.railway.internal:3000',
    'http://orion-qr-bridge.railway.internal:3000/redirect',
    'http://orion-qr-bridge.railway.internal:3000/internal/status',
    'http://orion-qr-bridge.railway.internal:3000?to=evil',
    'http://orion-qr-bridge.railway.internal:3000#fragment',
    'http://evil@orion-qr-bridge.railway.internal:3000',
    'http://orion-qr-bridge.railway.internal:3000@evil.example',
    'http://orion-qr-bridge.railway.internal:3001',
    'http://orion-qr-bridge.railway.internal:3000:3000',
    'https://orion-qr-bridge.railway.internal:3000',
    'http://orion-qr-bridge.railway.internal.evil.example:3000',
    'http://orion-qr-bridge.railway.internal:3000\\@evil.example',
    'http://.railway.internal:3000',
    'http://localhost:3000',
    'http://127.0.0.1:3000',
    'http://orion-qr-bridge.railway.internal:3000/%2f%2fevil.example',
])
def test_malicious_or_ambiguous_bridge_url_is_rejected_before_token_use(monkeypatch,bad_url):
    monkeypatch.setenv('QR_BRIDGE_URL',bad_url)
    with pytest.raises(HTTPException) as exc:
        _config()
    assert exc.value.status_code==503


def test_missing_bridge_secret_fails_closed(monkeypatch):
    monkeypatch.setenv('QR_BRIDGE_URL','http://orion-qr-bridge.railway.internal:3000')
    monkeypatch.setenv('QR_BRIDGE_TOKEN','too-short')
    with pytest.raises(HTTPException):
        _config()
