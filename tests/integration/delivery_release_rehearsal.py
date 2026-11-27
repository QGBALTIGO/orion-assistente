"""Rehearse the packaged pre-deploy with a disposable database and local S3 stand-in."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from scripts import backup_and_migrate_delivery as delivery
from scripts.backup_and_migrate_commissions import connection_values


class MemoryArchive:
    def __init__(self):
        self.body = b""
        self.meta = {}
        self.uploads = 0
        self.reads = 0
        self.corrupt_readback = False

    def upload_file(self, filename, bucket, key, ExtraArgs):
        assert bucket == "delivery-release-ci"
        assert key.startswith(f"production/orion-delivery/pre-schema-{delivery.BASE_REVISION}-")
        self.body = Path(filename).read_bytes()
        assert self.body.startswith(b"PGDMP")
        self.meta = ExtraArgs["Metadata"]
        self.uploads += 1

    def head_object(self, **_):
        return {"ContentLength": len(self.body), "Metadata": self.meta}

    def get_object(self, **_):
        self.reads += 1
        return {"Body": self}

    def iter_chunks(self, chunk_size):
        output = self.body
        if self.corrupt_readback:
            output = bytes([output[0] ^ 1]) + output[1:]
        for offset in range(0, len(output), chunk_size):
            yield output[offset : offset + chunk_size]

    def close(self):
        pass


def rehearse():
    raw, url = connection_values()
    assert url.host == "127.0.0.1" and url.database == "orion_delivery_release_ci"
    assert os.environ["ORION_BACKUP_S3_ENDPOINT"] == "https://delivery-backup.invalid"
    assert delivery.migrated_version(raw) == delivery.BASE_REVISION
    archive = MemoryArchive()
    with patch("boto3.client", return_value=archive):
        assert delivery.main(audit=True) == 0
        assert archive.uploads == 0
        archive.corrupt_readback = True
        assert delivery.main() == 3
        assert delivery.migrated_version(raw) == delivery.BASE_REVISION
        archive.corrupt_readback = False
        assert delivery.main() == 0
        assert delivery.migrated_version(raw) == delivery.approved_chain()[-1]
        assert delivery.main() == 0
        assert archive.uploads == archive.reads == 2
    print("orion_delivery_release_rehearsal_passed", flush=True)


if __name__ == "__main__":
    rehearse()
