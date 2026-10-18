"""Runner-only unit proof; no browser, database or provider is involved."""
import json
import zipfile


def test_trace_artifact_omits_login_payload_and_opaque_session_values(tmp_path, trace_sanitizer):
    raw, safe = tmp_path / "private.zip", tmp_path / "safe.zip"
    password, cookie, csrf = "Synthetic#E2E-2026-Only", "A" * 43, "B" * 43
    event = {"type":"before","method":"fill","params":{"value":password},
             "request":{"headers":[{"name":"Cookie","value":"__Host-orion_session=" + cookie}],
                        "postData":{"text":json.dumps({"password":password})}},
             "response":{"headers":[{"name":"Set-Cookie","value":cookie}]}}
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("test.trace", json.dumps(event))
        archive.writestr("resources/auth.json", json.dumps({"csrf":csrf,"ok":True}))
        archive.writestr("test.stacks", "Fill: " + password + "; token=" + cookie)
    trace_sanitizer(raw, safe)
    with zipfile.ZipFile(safe) as archive:
        texts = [archive.read(name).decode() for name in archive.namelist()]
    for secret in (password, cookie, csrf):
        assert all(secret not in value for value in texts)
    assert json.loads(texts[0])["request"]["headers"] == []
    assert json.loads(texts[1])["ok"] is True
