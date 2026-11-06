"""Pinned egress and synthetic tag discovery; no network or real recipients."""
import asyncio
import json
import logging
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest

from app import subscription_chatbot_http as tags


@pytest.fixture
def endpoint(monkeypatch):
    calls, checks = [], []
    result = {"status": 200, "body": b'{"reply":"private response value","username":"synthetic"}',
              "headers": {"content-type": "application/json"}}

    async def resolve(host, port):
        assert host == "api.example.com" and port == 443
        checks.append("dns")
        return "93.184.216.34"

    async def authorize():
        checks.append("authorize")

    def dispatch(request):
        calls.append(request)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "api.example.com"
        assert request.extensions["sni_hostname"] == "api.example.com"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(result["status"], content=result["body"], headers=result["headers"])

    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert "verify" not in kwargs or kwargs["verify"] is True
        return real_client(transport=httpx.MockTransport(dispatch), **kwargs)

    monkeypatch.setattr(tags, "_resolve", resolve)
    monkeypatch.setattr(tags.httpx, "AsyncClient", client)
    return calls, checks, result, authorize


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_sends_only_fixed_synthetic_context_and_returns_names(endpoint, method):
    calls, checks, _, authorize = endpoint
    output = await tags.read_url_tags("https://api.example.com/tags?fixed=1", method,
                                      request_id=uuid4(), authorize=authorize)
    assert output == ["reply", "username"]
    assert checks == ["dns", "authorize", "authorize"] and len(calls) == 1
    request = calls[0]
    if method == "POST":
        assert json.loads(request.content) == tags.SYNTHETIC_CONTEXT
    else:
        for key, value in tags.SYNTHETIC_CONTEXT.items():
            assert request.url.params[key] == str(value)
        assert request.content == b"" and request.url.params["fixed"] == "1"
    assert "private response value" not in str(output)


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 500])
async def test_no_redirect_or_retry_of_post(endpoint, status):
    calls, _, result, authorize = endpoint
    result["status"] = status
    result["headers"]["location"] = "https://127.0.0.1/private"
    with pytest.raises(HTTPException):
        await tags.read_url_tags("https://api.example.com/tags", "POST", request_id=uuid4(), authorize=authorize)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b'null', b'{"x":NaN}', b'{"x":"\xff"}', b'{broken'])
async def test_invalid_json_utf8_or_scalar_root_is_not_a_tag_result(endpoint, body):
    _, _, result, authorize = endpoint
    result["body"] = body
    with pytest.raises(HTTPException) as error:
        await tags.read_url_tags("https://api.example.com/tags", "GET", request_id=uuid4(), authorize=authorize)
    assert error.value.status_code == 422


async def test_declared_and_actual_size_are_bounded(endpoint, monkeypatch):
    _, _, result, authorize = endpoint
    monkeypatch.setattr(tags, "MAX_BYTES", 10)
    with pytest.raises(HTTPException) as error:
        await tags.read_url_tags("https://api.example.com/tags", "GET", request_id=uuid4(), authorize=authorize)
    assert error.value.status_code == 413
    result["headers"]["content-length"] = "1"
    with pytest.raises(HTTPException) as error:
        await tags.read_url_tags("https://api.example.com/tags", "GET", request_id=uuid4(), authorize=authorize)
    assert error.value.status_code == 413


@pytest.mark.parametrize("at", [1, 2])
async def test_revoked_owner_niche_or_revision_never_releases_keys(endpoint, at):
    calls, _, _, _ = endpoint
    count = 0

    async def revoked():
        nonlocal count
        count += 1
        if count == at:
            raise HTTPException(409, "synthetic_revoked")

    with pytest.raises(HTTPException) as error:
        await tags.read_url_tags("https://api.example.com/tags", "POST", request_id=uuid4(), authorize=revoked)
    assert error.value.status_code == 409 and len(calls) == at - 1


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private", "https://localhost/x", "https://a.internal/x",
    "file:///etc/passwd", "https://user:password@example.com", "https://api.example.com/#fragment",
    "https://api.example.com\\@127.0.0.1/x", "https://api.example.com/\nheader",
])
async def test_bad_urls_fail_before_dns_or_http(endpoint, url):
    calls, checks, _, authorize = endpoint
    with pytest.raises(HTTPException) as error:
        await tags.read_url_tags(url, "GET", request_id=uuid4(), authorize=authorize)
    assert error.value.status_code == 422 and calls == [] and checks == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "100.64.0.1",
                                     "::1", "::ffff:127.0.0.1", "2002:7f00:1::", "ff02::1", "3fff::1"])
def test_public_policy_refuses_metadata_loopback_transition_and_reserved(address):
    assert tags._public_address(address) is False


async def test_mixed_dns_answers_are_all_validated_before_pinning(monkeypatch):
    loop = asyncio.get_running_loop()

    async def mixed(*args, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(loop, "getaddrinfo", mixed)
    with pytest.raises(HTTPException) as error:
        await tags._resolve("api.example.com", 443)
    assert error.value.status_code == 422


@pytest.mark.parametrize("status", [200, 500])
async def test_private_url_never_enters_httpx_info_logs_and_filter_is_restored(endpoint, caplog, status):
    _, _, result, authorize = endpoint
    caplog.set_level(logging.INFO, logger="httpx")
    result["status"] = status
    request = tags.read_url_tags("https://api.example.com/private-path-secret?token=synthetic-url-secret", "GET",
                                 request_id=uuid4(), authorize=authorize)
    if status == 200:
        assert await request == ["reply", "username"]
    else:
        with pytest.raises(HTTPException):
            await request
    logging.getLogger("httpx").info("unrelated task remains observable")
    assert "synthetic-url-secret" not in caplog.text
    assert "private-path-secret" not in caplog.text
    assert "unrelated task remains observable" in caplog.text


async def test_private_request_log_filter_does_not_hide_concurrent_httpx_or_httpcore_logs(monkeypatch, caplog):
    entered, release = asyncio.Event(), asyncio.Event()
    caplog.set_level(logging.DEBUG, logger="httpx")
    caplog.set_level(logging.DEBUG, logger="httpcore.http11")
    real_client = httpx.AsyncClient

    async def resolve(*args):
        return "93.184.216.34"

    async def authorize():
        pass

    async def dispatch(request):
        logging.getLogger("httpcore.http11").debug("private response headers: %s", "synthetic-response-secret")
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"reply": "synthetic"})

    monkeypatch.setattr(tags, "_resolve", resolve)
    monkeypatch.setattr(tags.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(dispatch), **kwargs))
    task = asyncio.create_task(tags.read_url_tags("https://api.example.com/private?token=synthetic-url-secret", "POST",
                                                 request_id=uuid4(), authorize=authorize))
    await asyncio.wait_for(entered.wait(), timeout=1)
    logging.getLogger("httpx").info("other concurrent request visible")
    logging.getLogger("httpcore.http11").debug("other concurrent transport visible")
    release.set()
    assert await task == ["reply"]
    assert "synthetic-url-secret" not in caplog.text and "synthetic-response-secret" not in caplog.text
    assert "other concurrent request visible" in caplog.text and "other concurrent transport visible" in caplog.text


@pytest.mark.parametrize("body,expected", [
    ({"data":{"name":"Não expor valor","active":False},"items":[{"price":0}]},
     ["data.name","data.active","items[0].price"]),
    ([{"reply":"Não expor resposta"}], ["[0].reply"]),
    ({"nome completo":"Dado privado","pay-url":"https://example.com/private"},
     ['["nome completo"]','["pay-url"]']),
    ([], []),
])
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_nested_paths_and_array_root_from_real_discovery_helper(endpoint, body, expected, method):
    calls, _, result, authorize = endpoint
    result["body"] = json.dumps(body).encode()
    actual=await tags.read_url_tags("https://api.example.com/tags",method,request_id=uuid4(),authorize=authorize)
    assert actual == expected and len(calls) == 1
    assert "Não expor" not in json.dumps(actual)


@pytest.mark.parametrize("status,mime,code", [
    (401,"application/json","CHATBOT_URL_AUTH"),
    (403,"application/json","CHATBOT_URL_AUTH"),
    (405,"application/json","CHATBOT_URL_METHOD"),
    (302,"application/json","CHATBOT_URL_REDIRECT"),
    (200,"text/html","CHATBOT_URL_CONTENT_TYPE"),
    (500,"application/json","CHATBOT_URL_RESPONSE"),
])
async def test_discovery_reports_safe_specific_failure_without_external_body(endpoint,status,mime,code):
    calls,_,result,authorize=endpoint
    result.update(status=status,body=b'provider secret sentinel',headers={"content-type":mime})
    with pytest.raises(HTTPException) as exc:
        await tags.read_url_tags("https://api.example.com/tags","POST",request_id=uuid4(),authorize=authorize)
    assert exc.value.detail["code"] == code
    assert "sentinel" not in str(exc.value.detail)
    assert len(calls) == 1


async def test_url_tag_discovery_includes_root_data_and_reply_without_leaking_values(endpoint):
    calls, checks, result, authorize = endpoint
    result["body"] = json.dumps({
        "dns": "https://example.invalid",
        "username": "synthetic-user",
        "connections": 1,
        "reply": "Mensagem de teste privada",
        "data": [{"message": "Mensagem de teste privada"}],
    }, ensure_ascii=False).encode()
    output = await tags.read_url_tags(
        "https://api.example.com/tags", "GET", request_id=uuid4(), authorize=authorize)
    assert output == ["dns", "username", "connections", "reply", "data[0].message", "data"]
    assert "Mensagem de teste privada" not in repr(output)
    assert checks == ["dns", "authorize", "authorize"] and len(calls) == 1
