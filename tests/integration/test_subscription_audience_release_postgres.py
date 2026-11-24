"""Check the publication contract against the fully migrated disposable DB."""
import os

import pytest

from scripts import backup_and_migrate_subscription_audience as release


pytestmark = pytest.mark.integration


def test_audience_gate_validates_new_and_preserved_schema_contracts():
    raw = os.environ.get("MIGRATION_DATABASE_URL")
    if not raw:
        if os.environ.get("REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("The required PostgreSQL test database is unavailable")
        pytest.skip("Disposable PostgreSQL required")
    assert release.audience_contract(raw) is True
    assert release.schema_ready(raw) is True
