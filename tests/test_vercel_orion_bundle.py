"""The facade packager must never publish repository or authentication data."""
import hashlib
import json
from pathlib import Path

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
                    "deploy/vercel-orion/not-allowed.txt"):
        target = checkout / private
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SYNTHETIC_SECRET_MUST_NOT_BE_COPIED", encoding="utf-8")
    monkeypatch.setattr(bundle, "SOURCE_ROOT", checkout)
    return checkout


def test_bundle_contains_only_six_reviewed_outputs_and_private_manifest(source_tree, tmp_path):
    output = tmp_path / "facade"
    result = bundle.prepare_bundle(output)
    expected = {"public/index.html", "public/orion-suite-vercel.js", "public/403.html",
                "public/404.html", "public/google-origin.json", "vercel.json", "bundle-manifest.json"}
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert actual == expected
    assert result == json.loads((output / "bundle-manifest.json").read_text(encoding="utf-8"))
    assert result["manifest_public"] is False
    assert result["network_requests"] == 0
    assert result["runtime_execution"] is False
    assert result["production_changes"] is False
    for record in result["files"]:
        content = (output / record["output"]).read_bytes()
        original = (source_tree / record["source"]).read_bytes()
        assert record["source_sha256"] == hashlib.sha256(original).hexdigest()
        assert record["source_sha256_lf"] == hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
        assert record["output_sha256"] == hashlib.sha256(content).hexdigest()
        assert record["output_bytes"] == len(content)
        assert b"SYNTHETIC_SECRET" not in content
        if record["transformation"] == "byte_identical_copy":
            assert content == original


def test_landing_changes_only_the_three_metadata_values(source_tree, tmp_path):
    output = tmp_path / "facade"
    original = (source_tree / "landing/index.html").read_bytes()
    bundle.prepare_bundle(output)
    content = (output / "public/index.html").read_bytes()
    assert content.count(bundle.PUBLIC_ORIGIN.encode()) == 3
    assert content.replace(bundle.PUBLIC_ORIGIN.encode(), bundle.UPSTREAM_ORIGIN.encode()) == original
    for asset in (b'/landing-assets/orion-landing.css', b'/brand/orion-symbol.svg'):
        assert asset in content
    assert b"__SALES_CONTACT_LINK__" not in content


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_suite_only_inserts_connect_dialog_after_existing_capability_guard(source_tree, tmp_path, newline):
    source = source_tree / "app/static/orion-suite.js"
    original = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
    source.write_bytes(original)
    output = tmp_path / "facade"
    bundle.prepare_bundle(output)
    content = (output / "public/orion-suite-vercel.js").read_bytes()
    insertion = bundle.GOOGLE_CONNECT_INSERTION.encode().replace(b"\n", newline)
    guard = bundle.GOOGLE_ACTION_GUARD.encode().replace(b"\n", newline)
    assert content.count(insertion) == 1
    assert content.replace(insertion, b"", 1) == original
    assert guard + insertion in content
    # Encoded tenant input cannot change the fixed origin or create attributes;
    # HTML escaping is still applied to the entire constructed href.
    assert b"/panel?aba=agenda&empresa='+encodeURIComponent(state.tenantId)" in insertion
    assert b"href=\"'+esc(originalPanel)+'\"" in insertion
    assert bundle.UPSTREAM_ORIGIN.encode() in insertion
    assert bundle.PUBLIC_ORIGIN.encode() not in insertion
    assert b"return;" in insertion
    for forbidden in (b"request(", b"authorization_url", b"location.assign", b"<form", b"data-os-form"):
        assert forbidden not in insertion
    # Retry, revoke, form submission and all other code remain byte identical.
    assert content.count(b"async function submitGoogleAction(form,d,values)") == 1


def test_manifest_is_deterministic_and_does_not_record_environment(source_tree, tmp_path, monkeypatch):
    monkeypatch.setenv("PANEL_ADMIN_PASSWORD_HASH", "SYNTHETIC_PRIVATE_VALUE")
    first, second = tmp_path / "first", tmp_path / "second"
    bundle.prepare_bundle(first)
    bundle.prepare_bundle(second)
    assert (first / "bundle-manifest.json").read_bytes() == (second / "bundle-manifest.json").read_bytes()
    assert b"SYNTHETIC_PRIVATE_VALUE" not in (first / "bundle-manifest.json").read_bytes()
    assert str(source_tree).encode() not in (first / "bundle-manifest.json").read_bytes()


@pytest.mark.parametrize("relative", ["landing/index.html", "app/static/orion-suite.js"])
def test_unreviewed_source_changes_fail_before_output_is_created(source_tree, tmp_path, relative):
    source = source_tree / relative
    source.write_bytes(source.read_bytes() + b"\n<!-- unreviewed -->")
    output = tmp_path / "facade"
    with pytest.raises(ValueError, match="Reviewed source hash mismatch"):
        bundle.prepare_bundle(output)
    assert not output.exists()


@pytest.mark.parametrize("relative", ["deploy/vercel-orion/403.html", "deploy/vercel-orion/vercel.json"])
def test_missing_allowlisted_source_fails_closed(source_tree, tmp_path, relative):
    (source_tree / relative).unlink()
    output = tmp_path / "facade"
    with pytest.raises(ValueError, match="Missing allowlisted source"):
        bundle.prepare_bundle(output)
    assert not output.exists()


@pytest.mark.parametrize("relative", [".", "generated", "public/facade"])
def test_output_cannot_be_written_into_checkout(source_tree, relative):
    with pytest.raises(ValueError, match="outside the repository"):
        bundle.prepare_bundle(source_tree / relative)
    assert not (source_tree / "bundle-manifest.json").exists()


def test_existing_external_output_is_never_overwritten(source_tree, tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "preserved.txt"
    marker.write_bytes(b"preserve existing data")
    with pytest.raises(ValueError, match="already exists"):
        bundle.prepare_bundle(output)
    assert list(output.iterdir()) == [marker]
    assert marker.read_bytes() == b"preserve existing data"


def test_symlink_source_cannot_copy_a_private_file(source_tree, tmp_path):
    source = source_tree / "deploy/vercel-orion/403.html"
    source.unlink()
    try:
        source.symlink_to(source_tree / ".env")
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable on this platform: {exc.winerror}")
    with pytest.raises(ValueError, match="regular checkout file"):
        bundle.prepare_bundle(tmp_path / "facade")
    assert not (tmp_path / "facade").exists()


@pytest.mark.parametrize("invalid", [b"__SALES_CONTACT_LINK__", b'<link rel="canonical" href="wrong">'])
def test_landing_shape_checks_reject_unresolved_or_missing_metadata(invalid):
    with pytest.raises(ValueError):
        bundle._landing(invalid)


def test_google_shape_checks_reject_missing_or_duplicate_guards():
    guard = bundle.GOOGLE_ACTION_GUARD.encode()
    for content in (b"function changed(){}", guard + guard):
        with pytest.raises(ValueError, match="exactly one"):
            bundle._suite(content)


def test_cli_requires_explicit_output(capsys):
    with pytest.raises(SystemExit) as exc:
        bundle.main([])
    assert exc.value.code == 2
    assert "--output" in capsys.readouterr().err


def test_cli_reports_package_without_deploying(source_tree, tmp_path, capsys):
    output = tmp_path / "facade"
    assert bundle.main(["--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {"output": str(output.resolve()), "files": 6,
                      "manifest": "bundle-manifest.json", "network_requests": 0}
