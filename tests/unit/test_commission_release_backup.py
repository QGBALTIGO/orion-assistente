"""Release preflight: backup integrity required before any production DDL."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import backup_and_migrate_commissions as release


def test_connection_requires_reviewed_host_name_and_password(monkeypatch):
    monkeypatch.setenv("MIGRATION_DATABASE_URL",
                       "postgresql+psycopg://app_migrator:synthetic@private.invalid:5432/orion")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_HOST","different.invalid")
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_NAME","orion")
    with pytest.raises(ValueError,match="scope_mismatch"):
        release.connection_values()
    monkeypatch.setenv("ORION_EXPECTED_DATABASE_HOST","private.invalid")
    raw,url=release.connection_values()
    assert url.username=="app_migrator"
    assert url.database=="orion"
    assert "synthetic" in raw


def test_storage_scope_requires_explicit_private_bucket(monkeypatch):
    variables={
        "ORION_BACKUP_S3_ENDPOINT":"https://backup.invalid",
        "ORION_BACKUP_S3_BUCKET":"private-a",
        "ORION_BACKUP_S3_REGION":"iad",
        "ORION_BACKUP_S3_ACCESS_KEY_ID":"fake",
        "ORION_BACKUP_S3_SECRET_ACCESS_KEY":"synthetic",
        "ORION_BACKUP_EXPECTED_BUCKET":"different-b",
    }
    for key,value in variables.items():monkeypatch.setenv(key,value)
    with pytest.raises(ValueError,match="bucket_mismatch"):
        release.storage_values()
    monkeypatch.setenv("ORION_BACKUP_EXPECTED_BUCKET","private-a")
    assert release.storage_values()["style"] in {"path","virtual"}


@pytest.mark.parametrize("preserve_ownership", [False, True])
def test_backup_checks_archive_and_remote_digest_before_ddl(tmp_path,monkeypatch,preserve_ownership):
    from hashlib import sha256
    data=b"synthetic-sql-backup-for-tests-only"*7
    client=SimpleNamespace()
    expected={}
    def upload(filename,bucket,key,ExtraArgs):
        assert bucket=="synthetic-private"
        assert Path(filename).read_bytes()==data
        expected.update(key=key,sha=ExtraArgs["Metadata"]["sha256"],bucket=bucket)
    def head(Bucket,Key):
        assert Bucket==expected["bucket"] and Key==expected["key"]
        return {"ContentLength":len(data),"Metadata":{"sha256":sha256(data).hexdigest()}}
    client.upload_file=upload;client.head_object=head
    class StubBody:
        def iter_chunks(self,chunk_size):yield data
        def close(self):pass
    client.get_object=lambda Bucket,Key:{"Body":StubBody()}
    import boto3
    monkeypatch.setattr(boto3,"client",lambda *_args,**_kwargs:client)
    def run(args,**kwargs):
        if args[0]=="pg_dump":
            expected=["pg_dump","--host","127.0.0.1","--port","5432","--username","app_migrator",
                      "--dbname","synthetic","--format=custom"]
            if not preserve_ownership:
                expected.extend(["--no-owner","--no-acl"])
            expected.extend(["--file",str(tmp_path/"backup.dump"),"--lock-wait-timeout=10000"])
            assert args==expected
            Path(args[args.index("--file")+1]).write_bytes(data)
        assert kwargs["stderr"] is not None
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(release.subprocess,"run",run)
    url=SimpleNamespace(host="127.0.0.1",port=5432,username="app_migrator",
                        database="synthetic",password="private-only")
    settings={"endpoint":"https://storage.invalid","bucket":"synthetic-private",
              "region":"iad","access":"fake","secret":"fake","style":"path"}
    key=release.perform_backup(url,settings,tmp_path/"backup.dump",preserve_ownership=preserve_ownership)
    assert key.startswith("production/commission-0033/pre-schema-0032-")
    assert expected["sha"]==sha256(data).hexdigest()


def test_never_applies_migration_when_backup_fails(monkeypatch):
    url=SimpleNamespace()
    monkeypatch.setattr(release,"connection_values",lambda:("safe",url))
    monkeypatch.setattr(release,"migrated_version",lambda _raw:release.PREVIOUS)
    monkeypatch.setattr(release,"storage_values",lambda:{"bucket":"synthetic"})
    monkeypatch.setattr(release,"perform_backup",
                        lambda *_args:(_ for _ in ()).throw(RuntimeError("archive unsafe")))
    invoked=[]
    monkeypatch.setattr(release,"apply_commission_migration",lambda:invoked.append(True))
    assert release.main()==3
    assert invoked==[]
