"""Vercel must publish only fixed security routes, never stale frontend files."""
import hashlib
import json

import pytest
from scripts import prepare_vercel_orion as bundle


@pytest.fixture
def source_tree(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    for source, _ in bundle.SOURCE_ALLOWLIST:
        target = checkout / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((bundle.SOURCE_ROOT / source).read_bytes())
    for private in (".env", "docs/private.html", "backups/db.sql", "public/secret.txt",
                    "app/static/orion-suite.js", "landing/index.html"):
        target = checkout / private
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SYNTHETIC_SECRET_MUST_NOT_BE_COPIED", encoding="utf-8")
    monkeypatch.setattr(bundle, "SOURCE_ROOT", checkout)
    return checkout


def test_bundle_contains_only_policy_files_and_private_manifest(source_tree, tmp_path):
    output = tmp_path / "facade"
    result = bundle.prepare_bundle(output)
    expected = {"public/403.html", "public/404.html", "public/google-origin.json",
                "vercel.json", "bundle-manifest.json"}
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert actual == expected
    assert result == json.loads((output / "bundle-manifest.json").read_text(encoding="utf-8"))
    assert result["mode"] == "live_railway_proxy" and result["format_version"] == 2
    assert result["manifest_public"] is False and result["network_requests"] == 0
    assert result["runtime_execution"] is False and result["production_changes"] is False
    for record in result["files"]:
        content = (output / record["output"]).read_bytes()
        original = (source_tree / record["source"]).read_bytes()
        assert content == original
        assert record["source_sha256"] == hashlib.sha256(original).hexdigest()
        assert record["source_sha256_lf"] == hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
        assert record["output_sha256"] == hashlib.sha256(content).hexdigest()
        assert record["output_bytes"] == len(content)
        assert b"SYNTHETIC_SECRET" not in content
        assert record["transformation"] == "byte_identical_copy"


def test_no_static_frontend_can_be_bundled(source_tree, tmp_path):
    output = tmp_path / "facade"
    bundle.prepare_bundle(output)
    assert not (output / "public/index.html").exists()
    assert not (output / "public/orion-suite-vercel.js").exists()
    config = json.loads((output / "vercel.json").read_text())
    assert all(r.get("dest") not in ("/index.html", "/orion-suite-vercel.js") for r in config["routes"])
    assert any(r.get("src") == "/" and r.get("dest") == bundle.UPSTREAM_ORIGIN + "/" for r in config["routes"])


def test_manifest_is_deterministic_and_does_not_record_environment(source_tree, tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", "SYNTHETIC_PRIVATE_VALUE")
    first, second = tmp_path / "first", tmp_path / "second"
    bundle.prepare_bundle(first)
    bundle.prepare_bundle(second)
    assert (first / "bundle-manifest.json").read_bytes() == (second / "bundle-manifest.json").read_bytes()
    assert b"SYNTHETIC_PRIVATE_VALUE" not in (first / "bundle-manifest.json").read_bytes()
    assert str(source_tree).encode() not in (first / "bundle-manifest.json").read_bytes()


@pytest.mark.parametrize("relative", ["deploy/vercel-orion/403.html", "deploy/vercel-orion/vercel.json"])
def test_missing_allowlisted_source_fails_closed(source_tree, tmp_path, relative):
    (source_tree / relative).unlink()
    with pytest.raises(ValueError, match="Missing allowlisted source"):
        bundle.prepare_bundle(tmp_path / "facade")
    assert not (tmp_path / "facade").exists()


def test_unapproved_external_rewrite_is_refused(source_tree, tmp_path):
    source = source_tree / "deploy/vercel-orion/vercel.json"
    data = json.loads(source.read_text())
    data["routes"].append({"src": "/forbidden", "dest": "https://attacker.invalid/"})
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="not allowlisted"):
        bundle.prepare_bundle(tmp_path / "facade")
    assert not (tmp_path / "facade").exists()


@pytest.mark.parametrize("relative", [".", "generated", "public/facade"])
def test_output_cannot_be_written_into_checkout(source_tree, relative):
    with pytest.raises(ValueError, match="outside the repository"):
        bundle.prepare_bundle(source_tree / relative)
    assert not (source_tree / "bundle-manifest.json").exists()


def test_existing_external_output_is_preserved(source_tree, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "preserved.txt"
    marker.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="already exists"):
        bundle.prepare_bundle(output)
    assert marker.read_bytes() == b"preserve"


def test_symlink_cannot_copy_private_file(source_tree, tmp_path):
    source = source_tree / "deploy/vercel-orion/403.html"
    source.unlink()
    try:
        source.symlink_to(source_tree / ".env")
    except OSError:
        pytest.skip("Symlinks unavailable on platform")
    with pytest.raises(ValueError, match="regular checkout file"):
        bundle.prepare_bundle(tmp_path / "facade")


def test_cli_requires_explicit_output(capsys):
    with pytest.raises(SystemExit) as exc:
        bundle.main([])
    assert exc.value.code == 2
    assert "--output" in capsys.readouterr().err


def test_cli_reports_policy_only_package(source_tree, tmp_path, capsys):
    output = tmp_path / "facade"
    assert bundle.main(["--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "output": str(output.resolve()), "files": 4,
        "manifest": "bundle-manifest.json", "network_requests": 0}
