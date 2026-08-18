"""Azure Bastion gathering.

"Is there no Bastion host in this subscription at all" is a subscription-
wide "is the list empty" test — pure finding evaluation over the fetched
list, not gathering — so only the fetch (`bastion_hosts.list_all()`) is
included here.
"""

from azure.mgmt.network import NetworkManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_bastion_hosts(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.bastion_hosts.list_all())


def gather(credential, subscription_id, writer):
    for host in get_bastion_hosts(credential, subscription_id):
        writer.add_resource(
            resource_type='bastion_host',
            region=host.location or 'global',
            resource_id=host.id,
            resource_name=host.name,
            scope_id=_resource_group(host.id),
            raw=_as_dict(host),
        )
