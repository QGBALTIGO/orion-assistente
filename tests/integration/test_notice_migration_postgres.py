"""Rehearsal of the sealed additive migration in a randomly named loopback database."""
from types import SimpleNamespace
from pathlib import Path
from alembic import command, op
from alembic.config import Config
from sqlalchemy import text
import pytest
from scripts import one_time_notice_migration as ops
from test_business_migration_ops_postgres import migration_source  # noqa: F401

pytestmark=pytest.mark.integration

@pytest.fixture
def notice_source(migration_source):
    engine, previous=migration_source
    config=Config(str(ops.ROOT/'alembic.ini'))
    config.set_main_option('script_location',str(ops.ROOT/'alembic'))
    command.upgrade(config,ops.SOURCE)
    return SimpleNamespace(engine=engine,config=config,settings=ops.Settings(previous.url,previous.url.host,previous.url.database,True))


def snapshot(source):
    with source.engine.begin() as conn:
        conn.exec_driver_sql('SET TRANSACTION READ ONLY')
        return {'catalog':ops.existing_catalog(conn), 'revision':ops.revision(conn),
                'rows':{name:conn.exec_driver_sql(f'SELECT count(*) FROM public."{name}"').scalar_one()
                        for name in sorted(ops.TABLES)}}


def test_notice_target_matches_the_reviewed_pg16_seal(notice_source):
    command.upgrade(notice_source.config,ops.TARGET)
    with notice_source.engine.begin() as conn:
        conn.exec_driver_sql('SET LOCAL search_path=pg_catalog,public')
        definition=ops.catalog(conn)['appointment_notices']
        observed=ops.fingerprint(definition)
        assert observed==ops.TARGET_SCHEMA_SHA256,'NOTICE_SCHEMA_SEAL='+observed
        assert definition['rls'] and definition['force_rls']
        assert definition['owner']=='app_migrator'


def test_notice_additive_upgrade_preserves_existing_tables_and_data(notice_source):
    before=snapshot(notice_source)
    result=ops.execute(notice_source.settings)
    after=snapshot(notice_source)
    assert result['status']=='applied' and after['revision']==ops.TARGET
    assert after['catalog']==before['catalog'] and after['rows']==before['rows']
    with notice_source.engine.begin() as conn:
        assert conn.exec_driver_sql('SELECT count(*) FROM appointment_notices').scalar_one()==0
    assert ops.execute(notice_source.settings)['status']=='already_applied'
    assert snapshot(notice_source)==after


def test_notice_wrong_source_is_rejected_without_writes(notice_source):
    with notice_source.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num='0018_sales_requests'")
    before=snapshot(notice_source)
    with pytest.raises(ops.Stop,match='unexpected_source_schema'):
        ops.execute(notice_source.settings)
    assert snapshot(notice_source)==before


def test_notice_ddl_and_revision_roll_back_together(notice_source,monkeypatch):
    before=snapshot(notice_source);original=ops.existing_catalog;calls=0
    def fail_final(conn,*,projected=False):
        nonlocal calls
        result=original(conn,projected=projected)
        if projected:
            calls+=1
            if calls==2:raise ops.Stop('synthetic_final_check_failed')
        return result
    monkeypatch.setattr(ops,'existing_catalog',fail_final)
    with pytest.raises(ops.Stop,match='synthetic_final_check_failed'):
        ops.execute(notice_source.settings)
    assert calls==2 and snapshot(notice_source)==before
    with notice_source.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT to_regclass('public.appointment_notices')").scalar_one() is None


def test_notice_unrelated_ddl_is_rolled_back(notice_source,monkeypatch):
    original=ops.reviewed_migration();before=snapshot(notice_source)
    def bad_upgrade():
        original.upgrade();op.execute('ALTER TABLE customers ADD COLUMN unreviewed_notice_change text')
    monkeypatch.setattr(ops,'reviewed_migration',lambda:SimpleNamespace(upgrade=bad_upgrade))
    with pytest.raises(ops.Stop,match='existing_catalog_changed'):
        ops.execute(notice_source.settings)
    assert snapshot(notice_source)==before


def test_notice_runtime_read_only_gate_detects_only_committed_schema(notice_source,monkeypatch):
    import asyncio,os
    from sqlalchemy.engine import make_url
    from scripts import wait_for_agenda_schema as base
    from scripts import wait_for_notice_schema as gate
    url=make_url(os.environ['APP_DATABASE_URL']).set(host=notice_source.settings.url.host,
          port=notice_source.settings.url.port,database=notice_source.settings.url.database)
    monkeypatch.setenv('APP_DATABASE_URL',url.render_as_string(hide_password=False))
    settings=base.Settings.from_environment()
    assert asyncio.run(gate.probe(settings,3)) is False
    ops.execute(notice_source.settings)
    assert asyncio.run(gate.probe(settings,3)) is True
