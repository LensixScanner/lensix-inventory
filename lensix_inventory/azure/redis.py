"""Azure Cache for Redis gathering.

Only the data-fetching call is included here (redis.list) — minimum-TLS-
version and non-SSL-port-enabled evaluation is left server-side.
`minimum_tls_version` and `enable_non_ssl_port` are both already present on
the full `RedisResource.as_dict()` payload.

Requires: azure-mgmt-redis.
"""

from ._util import resource_group as _resource_group

def get_caches(credential, subscription_id):
    from azure.mgmt.redis import RedisManagementClient
    redis_client = RedisManagementClient(credential, subscription_id)
    return list(redis_client.redis.list())


def gather(credential, subscription_id, writer):
    try:
        caches = get_caches(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='redis:caches', message=e)
        return

    for cache in caches:
        writer.add_resource(
            resource_type='redis_cache',
            region=cache.location or 'global',
            resource_id=cache.id,
            resource_name=cache.name,
            scope_id=_resource_group(cache.id),
            raw=cache.as_dict(),
        )
