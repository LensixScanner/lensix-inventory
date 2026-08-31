"""Azure Container Registry gathering.

`registries.list()` already returns everything needed for admin-user,
public-network-access, anonymous-pull, and encryption evaluation — that
evaluation itself is left server-side.
"""

from azure.mgmt.containerregistry import ContainerRegistryManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_registries(credential, subscription_id):
    acr = ContainerRegistryManagementClient(credential, subscription_id)
    return list(acr.registries.list())


def gather(credential, subscription_id, writer):
    for registry in get_registries(credential, subscription_id):
        raw = _as_dict(registry)
        writer.add_resource(
            resource_type='container_registry',
            region=registry.location or 'global',
            resource_id=registry.id,
            resource_name=registry.name,
            scope_id=_resource_group(registry.id),
            raw=raw,
            tags=raw.get('tags'),
        )
