"""Build, qualify and publish the three pinned subscription audience overlays.

This utility never talks to Railway. It copies only manifest-listed source files
onto the exact reviewed production images, with no dependency installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit


SERVICES = {
    "api": {
        "base_image": "ghcr.io/qgbaltigo/orion-assistente-api@sha256:ccd592fbf7f1cde2a322da36f58c8237426eab717da5edf8cb955dd196bb1087",
        "root": "/srv/app", "owner": "appuser:appuser", "runtime": "python",
    },
    "worker": {
        "base_image": "ghcr.io/qgbaltigo/orion-assistente-api@sha256:8848dca7a0db8cc88c90eca4f57fefdc4a8c1e04ce37aac137d0fada3b74b568",
        "root": "/srv/app", "owner": "appuser:appuser", "runtime": "python",
    },
    "bridge": {
        "base_image": "ghcr.io/qgbaltigo/orion-assistente-qr-bridge@sha256:e374f21ad9d3bd8e0c68c13a60f7c73a7334439833561e0c447165b08dce6d45",
        "root": "/srv/qr", "owner": "0:0", "runtime": "node",
    },
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
DEFAULT_MANIFEST = "scripts/subscription_audience_release_manifest.json"
RUNTIME_CONFIG = ("Cmd", "Entrypoint", "Env", "User", "WorkingDir", "ExposedPorts",
                  "Volumes", "Healthcheck", "StopSignal", "Shell")
DATABASE_ENV = ("APP_DATABASE_URL", "MIGRATION_DATABASE_URL", "SUPPORT_DATABASE_URL",
                "SUPPORT_API_TOKEN", "REDIS_URL", "REQUIRE_POSTGRES_TESTS", "REQUIRE_REDIS_TESTS")
SAFE_ENV = {
    "CAKTO_BILLING_LIVE_ENABLED": "0", "ORION_DIRECT_PAYMENTS_ENABLED": "0",
    "META_LIVE_SEND": "0", "QR_AI_ENABLED": "0", "INBOX_SEND_ENABLED": "0",
    "MARKETING_EXECUTION_ENABLED": "0", "ORION_NOTIFY_SELF_ON_LINK": "0",
    "QR_BRIDGE_URL": "", "PYTHONDONTWRITEBYTECODE": "1",
}

# Both inspectors reject symlinked files/parents and distinguish absent files
# from unreadable files. Neither imports the application or prints file data.
AUDIT_PY = r"""
import hashlib,json,pathlib,stat,sys
for item in json.load(sys.stdin):
    path=pathlib.Path(item['target'])
    for parent in reversed((path,*path.parents)):
        if parent.is_symlink(): raise SystemExit('symlink: '+str(parent))
    if item['sha256'] is None:
        if path.exists(): raise SystemExit('expected absent: '+str(path))
    else:
        if not stat.S_ISREG(path.stat().st_mode): raise SystemExit('not regular: '+str(path))
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise SystemExit('hash mismatch: '+str(path))
print('Verified manifest file boundaries')
"""
AUDIT_JS = r"""
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const items=JSON.parse(fs.readFileSync(0,'utf8'));
for(const item of items){
  let cursor='/';
  for(const part of item.target.split('/').filter(Boolean)){
    cursor=path.join(cursor,part);
    try{if(fs.lstatSync(cursor).isSymbolicLink())throw new Error('symlink: '+cursor)}
    catch(error){if(error.code!=='ENOENT')throw error;}
  }
  let stat;
  try{stat=fs.lstatSync(item.target)}catch(error){if(error.code!=='ENOENT')throw error;}
  if(item.sha256===null){if(stat)throw new Error('expected absent: '+item.target);}
  else{
    if(!stat?.isFile())throw new Error('not regular: '+item.target);
    const actual=crypto.createHash('sha256').update(fs.readFileSync(item.target)).digest('hex');
    if(actual!==item.sha256)throw new Error('hash mismatch: '+item.target);
  }
}
console.log('Verified manifest file boundaries');
"""
PYTEST_RUNNER = r"""
import ipaddress,socket,sys,pytest
original_connect=socket.socket.connect
original_connect_ex=socket.socket.connect_ex
original_getaddrinfo=socket.getaddrinfo
def allow(address):
    host=address[0] if isinstance(address,tuple) else address
    if host=='localhost': return
    try: permitted=ipaddress.ip_address(host).is_loopback
    except (ValueError,TypeError): permitted=False
    if not permitted: raise AssertionError('External network forbidden in release qualification')
def connect(sock,address):
    allow(address)
    return original_connect(sock,address)
def connect_ex(sock,address):
    allow(address)
    return original_connect_ex(sock,address)
def getaddrinfo(host,*args,**kwargs):
    if host is not None: allow((host,0))
    return original_getaddrinfo(host,*args,**kwargs)
socket.socket.connect=connect
socket.socket.connect_ex=connect_ex
socket.getaddrinfo=getaddrinfo
class NoSkippedIntegration:
    skipped=False
    def pytest_runtest_logreport(self,report):
        if report.skipped: self.skipped=True
    def pytest_collectreport(self,report):
        if report.skipped: self.skipped=True
    def pytest_sessionfinish(self,session,exitstatus):
        if self.skipped and sys.argv[1]=='integration': session.exitstatus=1
raise SystemExit(pytest.main(['-q','-ra','-p','no:cacheprovider',*sys.argv[2:]],
                            plugins=[NoSkippedIntegration()]))
"""


class ReleaseError(ValueError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_file(root, value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", value):
        raise ReleaseError("Invalid source path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or str(pure) != value:
        raise ReleaseError("Source must be a canonical relative path")
    current = root
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            raise ReleaseError("Symlink source is forbidden: " + value)
    if not current.is_file():
        raise ReleaseError("Missing regular source: " + value)
    return current


def load_manifest(root, path):
    manifest = json.loads(path.read_text())
    if set(manifest) != {"schema_version", "services"} or manifest["schema_version"] != 1:
        raise ReleaseError("Expected manifest schema_version 1 and services")
    if not isinstance(manifest["services"], dict) or set(manifest["services"]) != set(SERVICES):
        raise ReleaseError("Manifest must declare exactly api, worker and bridge")
    for name, service in manifest["services"].items():
        if set(service) != {"base_image", "files", "tests"}:
            raise ReleaseError("Service requires base_image, files and tests: " + name)
        if service["base_image"] != SERVICES[name]["base_image"]:
            raise ReleaseError("Unreviewed base image: " + name)
        if not isinstance(service["files"], list) or not service["files"]:
            raise ReleaseError("Empty overlay: " + name)
        seen = set()
        for entry in service["files"]:
            if not isinstance(entry, dict) or set(entry) != {"source", "target", "base_sha256"}:
                raise ReleaseError("File requires source, target and base_sha256")
            relative = entry["source"]
            source_file(root, relative)
            if name == "bridge":
                if not relative.startswith("qr-bridge/src/") or not relative.endswith(".mjs"):
                    raise ReleaseError("Bridge overlay may contain only src/*.mjs")
                target = "/srv/qr/" + relative.removeprefix("qr-bridge/")
            else:
                if not relative.startswith(("app/", "scripts/", "alembic/")):
                    raise ReleaseError("API/worker source outside application directories")
                target = "/srv/app/" + relative
            if entry["target"] != target or target in seen:
                raise ReleaseError("Duplicate or incorrectly mapped target")
            seen.add(target)
            value = entry["base_sha256"]
            if value is not None and (not isinstance(value, str) or not HEX64.fullmatch(value)):
                raise ReleaseError("base_sha256 must be a SHA256 or null for an absent file")
        tests = service["tests"]
        if not isinstance(tests, dict) or set(tests) != {"unit", "integration"}:
            raise ReleaseError("tests requires unit and integration lists")
        if not tests["unit"] or (name == "api" and not tests["integration"]):
            raise ReleaseError("Each service needs unit tests and API needs integration tests")
        for phase, paths in tests.items():
            if not isinstance(paths, list) or len(paths) != len(set(paths)):
                raise ReleaseError("Tests must be distinct explicit file paths")
            if name == "bridge" and phase == "integration" and paths:
                raise ReleaseError("Bridge uses the isolated Node unit gate")
            prefix = "qr-bridge/test/" if name == "bridge" else "tests/" + phase + "/"
            suffix = ".test.mjs" if name == "bridge" else ".py"
            for relative in paths:
                source_file(root, relative)
                if not relative.startswith(prefix) or not relative.endswith(suffix):
                    raise ReleaseError("Unexpected test location: " + relative)
    return manifest


def run(command, *, capture=False, input_text=None, cwd=None):
    result = subprocess.run(command, check=True, text=True, input=input_text,
                            stdout=subprocess.PIPE if capture else None, cwd=cwd)
    return result.stdout.strip() if capture else None


def inspect(image):
    return json.loads(run(["docker", "image", "inspect", image], capture=True))[0]


def audit_files(name, image, entries, hash_field):
    definition = SERVICES[name]
    script = AUDIT_JS if definition["runtime"] == "node" else AUDIT_PY
    flag = "-e" if definition["runtime"] == "node" else "-c"
    expected = [{"target": row["target"], "sha256": row[hash_field]} for row in entries]
    run(["docker", "run", "--rm", "-i", "--platform", "linux/amd64", "--network", "none",
         "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
         "--entrypoint", definition["runtime"], image, flag, script], input_text=json.dumps(expected))


def assert_inherited(base, candidate):
    for field in RUNTIME_CONFIG:
        if base["Config"].get(field) != candidate["Config"].get(field):
            raise ReleaseError("Runtime config unexpectedly changed: " + field)
    old = base["RootFS"]["Layers"]
    if candidate["RootFS"]["Layers"][:len(old)] != old:
        raise ReleaseError("Candidate does not inherit the complete production filesystem")


def assert_checkout(root, revision):
    if not HEX40.fullmatch(revision):
        raise ReleaseError("A full Git commit SHA is required")
    if run(["git", "rev-parse", "HEAD"], capture=True, cwd=root) != revision:
        raise ReleaseError("Git HEAD does not match the requested revision")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], capture=True, cwd=root):
        raise ReleaseError("Release requires a clean committed checkout")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def build(root, manifest_path, manifest, revision, release_id, output):
    assert_checkout(root, revision)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", release_id):
        raise ReleaseError("Invalid unique release ID")
    output.mkdir(parents=True, exist_ok=True)
    if (output / "build.json").exists():
        raise ReleaseError("Release output already contains a build")
    record = {"schema_version": 1, "revision": revision, "release_id": release_id,
              "manifest_sha256": sha256(manifest_path), "services": {}}
    for name, definition in SERVICES.items():
        service = manifest["services"][name]
        base = service["base_image"]
        files = [dict(entry, sha256=sha256(root / entry["source"])) for entry in service["files"]]
        run(["docker", "pull", "--platform", "linux/amd64", base])
        audit_files(name, base, files, "base_sha256")
        tag = base.split("@")[0] + f":subscription-audience-{name}-{revision}-{release_id}"
        context = output / name
        context.mkdir()
        lines = ["FROM " + base,
                 'LABEL org.opencontainers.image.revision="' + revision + '"',
                 'LABEL io.orion.release="subscription-audience"']
        # One layer avoids exhausting overlayfs depth on the inherited images.
        # The payload is assembled from the manifest, never the repository tree.
        payload = context / "payload"
        payload.mkdir(mode=0o755)
        for entry in files:
            relative = PurePosixPath(entry["target"]).relative_to(definition["root"])
            local = payload.joinpath(*relative.parts)
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / entry["source"], local)
            local.chmod(0o644)
        for directory in (payload, *(path for path in payload.rglob("*") if path.is_dir())):
            directory.chmod(0o755)
        lines.append("COPY --chown=" + definition["owner"] + " " +
                     json.dumps(["payload/", definition["root"] + "/"]))
        (context / "Dockerfile").write_text("\n".join(lines) + "\n")
        run(["docker", "build", "--platform", "linux/amd64", "--pull=false", "--no-cache",
             "--tag", tag, str(context)])
        candidate = inspect(tag)
        assert_inherited(inspect(base), candidate)
        audit_files(name, tag, files, "sha256")
        record["services"][name] = {"base_image": base, "tag": tag, "image_id": candidate["Id"],
                                    "files": files, "qualified": {"unit": False, "integration": False}}
    write_json(output / "build.json", record)


def checked_record(root, manifest_path, manifest, revision, output):
    assert_checkout(root, revision)
    record = json.loads((output / "build.json").read_text())
    if record["revision"] != revision or record["manifest_sha256"] != sha256(manifest_path):
        raise ReleaseError("Build manifest/revision changed")
    for name, service in manifest["services"].items():
        saved = record["services"][name]
        expected = [dict(entry, sha256=sha256(root / entry["source"])) for entry in service["files"]]
        if saved["base_image"] != service["base_image"] or saved["files"] != expected:
            raise ReleaseError("Source changed after candidate build: " + name)
        if inspect(saved["tag"])["Id"] != saved["image_id"]:
            raise ReleaseError("Candidate image changed after build: " + name)
    return record


def container_command(root, name, tag, *, integration=False):
    definition = SERVICES[name]
    command = ["docker", "run", "--rm", "--platform", "linux/amd64", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--network", "host" if integration else "none",
               "--entrypoint", definition["runtime"], "--workdir", definition["root"]]
    for key, value in SAFE_ENV.items():
        command += ["--env", key + "=" + value]
    if integration:
        validate_disposable_environment()
        for key in DATABASE_ENV:
            if not os.environ.get(key):
                raise ReleaseError("Missing disposable integration setting: " + key)
            command += ["--env", key]
    if name == "bridge":
        command += ["--mount", f"type=bind,source={root / 'qr-bridge/test'},target=/srv/qr/test,readonly",
                    "--mount", f"type=bind,source={root / 'tests/fixtures'},target=/srv/tests/fixtures,readonly"]
    else:
        command += ["--mount", f"type=bind,source={root / 'tests'},target=/srv/app/tests,readonly"]
    return [*command, tag]


def validate_disposable_environment():
    databases = []
    for key, role in (("APP_DATABASE_URL", "app_runtime"),
                      ("MIGRATION_DATABASE_URL", "app_migrator"),
                      ("SUPPORT_DATABASE_URL", "app_support")):
        parsed = urlsplit(os.environ.get(key, ""))
        if (parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username != role or not parsed.password
                or parsed.path != "/orion_subscription_audience_ci"):
            raise ReleaseError("Only role-separated disposable release databases are allowed: " + key)
        databases.append((parsed.hostname, parsed.port, parsed.path))
    if len(set(databases)) != 1:
        raise ReleaseError("Disposable database scopes do not match")
    redis = urlsplit(os.environ.get("REDIS_URL", ""))
    if redis.scheme != "redis" or redis.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ReleaseError("Only disposable loopback Redis is allowed")
    if any(os.environ.get(key) != "1" for key in ("REQUIRE_POSTGRES_TESTS", "REQUIRE_REDIS_TESTS")):
        raise ReleaseError("Integration gates must require PostgreSQL and Redis")


def qualify(root, manifest_path, manifest, revision, output, name, phase):
    record = checked_record(root, manifest_path, manifest, revision, output)
    paths = manifest["services"][name]["tests"][phase]
    if paths:
        command = container_command(root, name, record["services"][name]["tag"], integration=phase == "integration")
        if name == "bridge":
            command += ["--test", *[path.removeprefix("qr-bridge/") for path in paths]]
        else:
            command += ["-c", PYTEST_RUNNER, phase, *paths]
        run(command)
    record["services"][name]["qualified"][phase] = True
    write_json(output / "build.json", record)


def migrate(root, manifest_path, manifest, revision, output):
    record = checked_record(root, manifest_path, manifest, revision, output)
    command = container_command(root, "api", record["services"]["api"]["tag"], integration=True)
    run([*command, "-m", "alembic", "upgrade", "head"])


def publish(root, manifest_path, manifest, revision, output):
    record = checked_record(root, manifest_path, manifest, revision, output)
    for name in SERVICES:
        if record["services"][name]["qualified"] != {"unit": True, "integration": True}:
            raise ReleaseError("Candidate has not passed all declared gates: " + name)
    for name, service in record["services"].items():
        # The source and config were checked before qualification; repeat the
        # byte and ancestry guards immediately before sending the artifact.
        audit_files(name, service["tag"], service["files"], "sha256")
        assert_inherited(inspect(service["base_image"]), inspect(service["tag"]))
        run(["docker", "push", service["tag"]])
        repository = service["base_image"].split("@")[0]
        digests = [value for value in inspect(service["tag"]).get("RepoDigests", [])
                   if value.startswith(repository + "@sha256:") and HEX64.fullmatch(value.split(":")[-1])]
        if len(set(digests)) != 1:
            raise ReleaseError("Cannot resolve an unambiguous published digest: " + name)
        service["image"] = digests[0]
        service["digest"] = digests[0].split("@", 1)[1]
    write_json(output / "subscription-audience-release.json", record)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("validate", "build", "test", "migrate", "publish"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, default=Path(DEFAULT_MANIFEST))
    parser.add_argument("--revision")
    parser.add_argument("--release-id")
    parser.add_argument("--output", type=Path, default=Path("/tmp/orion-subscription-audience-release"))
    parser.add_argument("--service", choices=tuple(SERVICES))
    parser.add_argument("--phase", choices=("unit", "integration"))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    try:
        manifest = load_manifest(root, path)
        if args.action == "validate":
            print("Validated three pinned overlays and explicit test lists")
            return 0
        if not args.revision:
            raise ReleaseError("--revision is required")
        if args.action == "build":
            if not args.release_id:
                raise ReleaseError("--release-id is required")
            build(root, path, manifest, args.revision, args.release_id, args.output)
        elif args.action == "test":
            if not args.service or not args.phase:
                raise ReleaseError("test requires --service and --phase")
            qualify(root, path, manifest, args.revision, args.output, args.service, args.phase)
        elif args.action == "migrate":
            migrate(root, path, manifest, args.revision, args.output)
        else:
            publish(root, path, manifest, args.revision, args.output)
    except (ReleaseError, OSError, ValueError, subprocess.CalledProcessError) as error:
        # Commands here contain no credentials; environment values are never
        # interpolated into command strings or diagnostic output.
        print("Release stopped: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
