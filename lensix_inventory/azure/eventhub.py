"""Azure Event Hub gathering.

`namespaces.list()` already returns everything needed for encryption,
public-network-access, and local-auth evaluation (encryption,
public_network_access, disable_local_auth) — that evaluation itself is
left server-side.
"""

from azure.mgmt.eventhub import EventHubManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_namespaces(credential, subscription_id):
    client = EventHubManagementClient(credential, subscription_id)
    return list(client.namespaces.list())


def gather(credential, subscription_id, writer):
    for ns in get_namespaces(credential, subscription_id):
        writer.add_resource(
            resource_type='eventhub_namespace',
            region=ns.location or 'global',
            resource_id=ns.id,
            resource_name=ns.name,
            scope_id=_resource_group(ns.id),
            raw=_as_dict(ns),
        )
