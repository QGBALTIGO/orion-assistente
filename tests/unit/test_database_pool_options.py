"""Bounded shared pools: construct real engines, never open a connection."""
import pytest
from sqlalchemy.pool import NullPool

from app import catalog_api as catalog, database


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in ("ORION_DB_POOL_SIZE", "ORION_DB_MAX_OVERFLOW", "PYTEST_CURRENT_TEST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://app_runtime:synthetic-only@db.invalid/orion")
    monkeypatch.setenv("SUPPORT_DATABASE_URL", "postgres://app_support:synthetic-only@db.invalid/orion")
    database.get_engine.cache_clear()
    catalog.support_session_factory.cache_clear()
    yield
    database.get_engine.cache_clear()
    catalog.support_session_factory.cache_clear()


def test_defaults_preserve_the_existing_pool_limits_and_pre_ping(monkeypatch):
    # Pytest refreshes this marker between fixture setup and the test call.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert database.engine_pool_options() == {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}
    assert catalog.engine_pool_options is database.engine_pool_options


@pytest.mark.parametrize("size,overflow", [(1, 0), (5, 5), (10, 0), (20, 20)])
def test_explicit_configuration_is_bounded_and_shared(monkeypatch, size, overflow):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ORION_DB_POOL_SIZE", str(size))
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", str(overflow))
    expected = {"pool_pre_ping": True, "pool_size": size, "max_overflow": overflow}
    assert database.engine_pool_options() == catalog.engine_pool_options() == expected
    if (size, overflow) in {(5, 5), (10, 0)}:
        assert 3 * 2 * (expected["pool_size"] + expected["max_overflow"]) == 60


@pytest.mark.parametrize("name,value", [
    ("ORION_DB_POOL_SIZE", "0"), ("ORION_DB_POOL_SIZE", "21"),
    ("ORION_DB_MAX_OVERFLOW", "21"), ("ORION_DB_MAX_OVERFLOW", "-1"),
    *[(name, value) for name in ("ORION_DB_POOL_SIZE", "ORION_DB_MAX_OVERFLOW")
      for value in ("", " ", " 5", "5 ", "+5", "5.0", "1e1", "true", "٥", "５", "5\n", "1" * 5000)],
])
@pytest.mark.parametrize("pytest_mode", [False, True])
def test_invalid_configuration_fails_without_echoing_any_value(monkeypatch, name, value, pytest_mode):
    monkeypatch.setenv(name, value)
    if pytest_mode:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic-test")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    with pytest.raises(RuntimeError) as error:
        database.engine_pool_options()
    assert str(error.value) == "Configuração dos pools PostgreSQL inválida"
    assert error.value.__cause__ is None


@pytest.mark.parametrize("configured", [False, True])
def test_pytest_uses_null_pool_without_queue_pool_only_options(monkeypatch, configured):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic-test")
    if configured:
        monkeypatch.setenv("ORION_DB_POOL_SIZE", "5")
        monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", "5")
    assert database.engine_pool_options() == {"pool_pre_ping": True, "poolclass": NullPool}


@pytest.mark.asyncio
@pytest.mark.parametrize("size,overflow,pytest_mode", [(None, None, False), (5, 5, False), (10, 0, False), (1, 0, False), (20, 20, False), (5, 5, True)])
async def test_both_real_factories_accept_options_and_keep_roles_and_session_contract(monkeypatch, size, overflow, pytest_mode):
    if size is not None:
        monkeypatch.setenv("ORION_DB_POOL_SIZE", str(size))
        monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", str(overflow))
    if pytest_mode:
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic-test")
    else:
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    runtime = database.get_engine()
    support_factory = catalog.support_session_factory()
    support = support_factory.kw["bind"]
    try:
        assert runtime.url.drivername == support.url.drivername == "postgresql+asyncpg"
        assert runtime.url.username == "app_runtime" and support.url.username == "app_support"
        assert database.get_session_factory().kw == {"bind": runtime, "autoflush": True, "expire_on_commit": False}
        assert support_factory.kw == {"bind": support, "autoflush": True, "expire_on_commit": False}
        for engine in (runtime, support):
            assert engine.pool._pre_ping is True
            if pytest_mode:
                assert isinstance(engine.pool, NullPool)
            else:
                assert engine.pool.size() == (5 if size is None else size)
                assert engine.pool._max_overflow == (10 if overflow is None else overflow)
        assert database.get_engine() is runtime
        assert catalog.support_session_factory() is support_factory
    finally:
        await runtime.dispose()
        await support.dispose()


@pytest.mark.parametrize("factory,variable,role", [
    (database.get_engine, "APP_DATABASE_URL", "app_migrator"),
    (database.get_engine, "APP_DATABASE_URL", "app_support"),
    (database.get_engine, "APP_DATABASE_URL", "postgres"),
    (catalog.support_session_factory, "SUPPORT_DATABASE_URL", "app_runtime"),
    (catalog.support_session_factory, "SUPPORT_DATABASE_URL", "app_migrator"),
    (catalog.support_session_factory, "SUPPORT_DATABASE_URL", "postgres"),
])
def test_pool_configuration_never_relaxes_database_role_validation(monkeypatch, factory, variable, role):
    monkeypatch.setenv(variable, f"postgresql+asyncpg://{role}:synthetic-only@db.invalid/orion")
    monkeypatch.setenv("ORION_DB_POOL_SIZE", "5")
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", "5")
    def forbidden(*args, **kwargs):
        pytest.fail("A rejected database role reached engine construction")
    monkeypatch.setattr(database, "create_async_engine", forbidden)
    monkeypatch.setattr(catalog, "create_async_engine", forbidden)
    with pytest.raises(RuntimeError) as error:
        factory()
    assert "synthetic-only" not in str(error.value) and "db.invalid" not in str(error.value)


@pytest.mark.parametrize("factory,variable", [
    (database.get_engine, "APP_DATABASE_URL"),
    (catalog.support_session_factory, "SUPPORT_DATABASE_URL"),
])
def test_invalid_pool_options_fail_before_engine_creation_in_either_factory(monkeypatch, factory, variable):
    monkeypatch.setenv("ORION_DB_MAX_OVERFLOW", "-1")
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid pool configuration reached engine construction")
    monkeypatch.setattr(database, "create_async_engine", forbidden)
    monkeypatch.setattr(catalog, "create_async_engine", forbidden)
    with pytest.raises(RuntimeError, match="Configuração dos pools PostgreSQL inválida"):
        factory()
