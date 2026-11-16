"""Next deploy after 0056: no historical backup/migration may rerun."""
import pytest
from scripts import backup_and_migrate_registration as registration
from scripts import backup_and_migrate_cakto as cakto
from scripts import backup_and_migrate_short_links as short_links
from scripts import backup_and_migrate_combo_duration as combo


def test_predeploy_accepts_only_read_only_verified_one_click_schema(monkeypatch):
    monkeypatch.setattr(registration,'connection_values',lambda:('synthetic-url','synthetic-url'))
    monkeypatch.setattr(registration,'migrated_version',lambda _: '0056_one_click_appointment_links')
    monkeypatch.setattr(short_links,'schema_ready',lambda _:True)
    monkeypatch.setattr(combo,'schema_ready',lambda _:True)
    monkeypatch.setattr(registration,'one_click_schema_ready',lambda _:True)
    def forbidden(*args,**kwargs):
        raise AssertionError('Historical migration or backup must never execute')
    monkeypatch.setattr(registration,'main',forbidden)
    monkeypatch.setattr(combo,'main',forbidden)
    monkeypatch.setattr(short_links,'main',forbidden)
    assert registration.predeploy_main()==0


@pytest.mark.parametrize('missing', ['combo','short','one_click'])
def test_predeploy_refuses_missing_0056_contract(monkeypatch, missing):
    monkeypatch.setattr(registration,'connection_values',lambda:('synthetic-url','synthetic-url'))
    monkeypatch.setattr(registration,'migrated_version',lambda _: '0056_one_click_appointment_links')
    monkeypatch.setattr(short_links,'schema_ready',lambda _:missing!='short')
    monkeypatch.setattr(combo,'schema_ready',lambda _:missing!='combo')
    monkeypatch.setattr(registration,'one_click_schema_ready',lambda _:missing!='one_click')
    assert registration.predeploy_main()==3


def test_old_cakto_migrator_recognizes_new_revision_without_replaying_ddl(monkeypatch):
    monkeypatch.setattr(cakto,'connection_values',lambda:('synthetic-url','synthetic-url'))
    monkeypatch.setattr(cakto,'migrated_version',lambda _: '0056_one_click_appointment_links')
    monkeypatch.setattr(cakto,'schema_ready',lambda _: True)
    def forbidden(*args,**kwargs):
        raise AssertionError('Old Cakto migration must not execute')
    monkeypatch.setattr(cakto,'perform_backup',forbidden)
    monkeypatch.setattr(cakto,'subprocess',type('NoSubprocess',(),{'run':staticmethod(forbidden)}))
    assert cakto.main()==0
