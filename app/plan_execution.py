"""Bind background generation to the same plan authority as authenticated APIs."""
from functools import wraps


def requires_plan(feature):
    """The first argument is the server-resolved tenant, never model input."""
    def decorate(function):
        @wraps(function)
        async def guarded(tenant, *args, **kwargs):
            from app.plan_permissions import require_tenant_feature, bind_feature, reset_feature

            await require_tenant_feature(tenant, feature)
            token = bind_feature(tenant, feature)
            try:
                result = await function(tenant, *args, **kwargs)
                # A provider call may outlive a temporary grant. Domain writes
                # are guarded again in their transactions; stale output must
                # also be stopped before it becomes a new outbound response.
                await require_tenant_feature(tenant, feature)
                return result
            finally:
                reset_feature(token)
        return guarded
    return decorate
