"""The one-time customer repair refuses all unreviewed customer FK shapes."""
import pytest
from scripts import repair_customer_identity_20261027 as repair


def catalog(tables):
    return [{"namespace":"public","table_name":table,"columns":["tenant_id","customer_id"]} for table in sorted(tables)]


@pytest.mark.parametrize("tables", [
    repair.FK_TABLES,
    repair.FK_TABLES | repair.NEW_FK_TABLES,
])
def test_only_reviewed_foreign_key_sets_are_allowed(monkeypatch, tables):
    monkeypatch.setattr(repair, "rows", lambda *args, **kwargs: catalog(tables))
    assert repair.verify_foreign_keys(object()) == tables


@pytest.mark.parametrize("tables", [
    repair.FK_TABLES | {"appointment_access"},
    repair.FK_TABLES | repair.NEW_FK_TABLES | {"new_customer_reference"},
    repair.FK_TABLES - {"appointments"},
])
def test_unreviewed_added_or_missing_reference_fails_closed(monkeypatch, tables):
    monkeypatch.setattr(repair, "rows", lambda *args, **kwargs: catalog(tables))
    with pytest.raises(ValueError, match="customer_foreign_keys_changed"):
        repair.verify_foreign_keys(object())


def test_wrong_reference_columns_refused(monkeypatch):
    altered = catalog(repair.FK_TABLES | repair.NEW_FK_TABLES)
    altered[0]["columns"] = ["customer_id"]
    monkeypatch.setattr(repair, "rows", lambda *args, **kwargs: altered)
    with pytest.raises(ValueError, match="customer_foreign_keys_changed"):
        repair.verify_foreign_keys(object())
