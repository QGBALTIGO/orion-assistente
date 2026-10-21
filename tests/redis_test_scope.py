"""Designated test Redis only; never accept arbitrary infrastructure URLs."""
import os
from urllib.parse import urlsplit

LOOPBACK=frozenset({'localhost','127.0.0.1','::1'})
MANDATORY_COMPOSE_FLAGS=('REQUIRE_POSTGRES_TESTS','REQUIRE_REDIS_TESTS','ORION_REQUIRE_INTAKE_REDIS_TEST')


def communication_redis_url(value,environment=None):
    environment=os.environ if environment is None else environment
    message='Communication tests require designated isolated Redis database15'
    if not isinstance(value,str) or not value or any(ord(c)<33 for c in value):
        raise ValueError(message)
    try:
        parsed=urlsplit(value)
        port=parsed.port if parsed.port is not None else 6379
        valid=(parsed.scheme=='redis' and parsed.path=='/15' and
            parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment and
            1<=port<=65535)
    except ValueError:
        raise ValueError(message) from None
    if not valid:raise ValueError(message)
    if parsed.hostname in LOOPBACK:return value
    # Only compose.yaml's tools/test service supplies this opt-in. Runtime API
    # and workers do not receive it. All existing mandatory-test flags and the
    # configured Redis must agree before the exact private service alias works.
    if (parsed.hostname=='redis' and parsed.port==6379 and
            environment.get('ORION_COMPOSE_TEST_INFRA')=='1' and
            all(environment.get(name)=='1' for name in MANDATORY_COMPOSE_FLAGS) and
            environment.get('REDIS_URL')==value):
        return value
    raise ValueError(message)
