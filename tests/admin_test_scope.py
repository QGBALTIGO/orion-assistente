"""Keep configured role credentials when targeting a disposable admin fixture."""
import re

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def role_url(source_url,configured_url,expected_role,*,drivername='postgresql+asyncpg'):
    """Change only driver/database; never borrow the owner's password or role."""
    try:
        source=make_url(source_url);role=make_url(configured_url)
        local={'localhost','127.0.0.1','::1'}
        drivers={'postgres','postgresql','postgresql+psycopg','postgresql+asyncpg'}
        tls={'ssl','sslmode','sslrootcert','sslcert','sslkey','sslpassword'}
        if (expected_role not in {'app_runtime','app_support'} or role.username!=expected_role
            or source.host not in local or role.host not in local
            or (source.port or 5432)!=(role.port or 5432)
            or source.drivername not in drivers or role.drivername not in drivers
            or drivername not in {'postgresql','postgresql+asyncpg'}
            or re.fullmatch('orion_ops_fixture_[0-9a-f]{32}',source.database or '') is None
            or set(role.query)-tls or any(not isinstance(value,str) for value in role.query.values())
            or role.username==source.username):
            raise ValueError()
        return role.set(database=source.database,drivername=drivername)
    except (TypeError,ValueError,AttributeError,ArgumentError):
        raise ValueError('invalid_disposable_role_configuration') from None
