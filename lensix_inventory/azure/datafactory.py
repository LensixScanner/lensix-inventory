"""Azure Data Factory gathering.

`factories.list()` already returns everything needed for public-network-
access evaluation — that evaluation itself is left server-side.
"""

from azure.mgmt.datafactory import DataFactoryManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_factories(credential, subscription_id):
    client = DataFactoryManagementClient(credential, subscription_id)
    return list(client.factories.list())


def gather(credential, subscription_id, writer):
    for factory in get_factories(credential, subscription_id):
        raw = _as_dict(factory)
        writer.add_resource(
            resource_type='data_factory',
            region=factory.location or 'global',
            resource_id=factory.id,
            resource_name=factory.name,
            scope_id=_resource_group(factory.id),
            raw=raw,
            tags=raw.get('tags'),
        )
