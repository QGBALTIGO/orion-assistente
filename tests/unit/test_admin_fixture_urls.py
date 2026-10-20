"""Distinct owner/runtime/support passwords; parser only, no database access."""
import pytest
from sqlalchemy.engine import make_url
from tests.admin_test_scope import role_url

SOURCE='postgresql+psycopg://postgres:synthetic-owner-only@127.0.0.1:55439/orion_ops_fixture_'+('a'*32)


@pytest.mark.parametrize('role',['app_runtime','app_support'])
@pytest.mark.parametrize('driver',['postgresql','postgresql+asyncpg'])
def test_configured_role_password_host_and_tls_survive_database_switch(role,driver):
    configured=f'postgresql+asyncpg://{role}:synthetic-{role}-only@localhost:55439/isolated_main?sslmode=disable'
    result=role_url(SOURCE,configured,role,drivername=driver)
    assert result.username==role and result.password==f'synthetic-{role}-only'
    assert result.password!=make_url(SOURCE).password
    assert result.host=='localhost' and result.port==55439 and result.query=={'sslmode':'disable'}
    assert result.drivername==driver and result.database==make_url(SOURCE).database


def test_local_trust_without_a_role_password_never_inherits_owner_password():
    result=role_url(SOURCE,'postgresql://app_support@127.0.0.1:55439/isolated_main','app_support')
    assert result.password is None and make_url(SOURCE).password is not None


@pytest.mark.parametrize('configured',[
    None,'','postgresql://postgres:synthetic-owner-only@127.0.0.1:55439/isolated_main',
    'postgresql://app_runtime:synthetic@127.0.0.1:55439/isolated_main',
    'postgresql://app_support:synthetic@production.example:55439/isolated_main',
    'postgresql://app_support:synthetic@127.0.0.1:5432/isolated_main',
    'postgresql://app_support:synthetic@127.0.0.1:55439/isolated_main?user=postgres',
    'postgresql://app_support:synthetic@127.0.0.1:55439/isolated_main?sslmode=disable&sslmode=require',
])
def test_no_missing_configuration_owner_role_or_endpoint_overrides(configured):
    with pytest.raises(ValueError,match='invalid_disposable_role_configuration'):
        role_url(SOURCE,configured,'app_support')


@pytest.mark.parametrize('source',[
    'postgresql://postgres:synthetic@127.0.0.1:55439/orion_communication_test_v3',
    'postgresql://postgres:synthetic@production.example:55439/orion_ops_fixture_'+('a'*32),
])
def test_source_must_be_this_disposable_fixture_not_the_original(source):
    with pytest.raises(ValueError,match='invalid_disposable_role_configuration'):
        role_url(source,'postgresql://app_support:synthetic@127.0.0.1:55439/main','app_support')
