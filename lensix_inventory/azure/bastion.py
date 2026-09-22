"""Azure Bastion gathering.

"Is there no Bastion host in this subscription at all" is a subscription-
wide "is the list empty" test — pure finding evaluation over the fetched
list, not gathering — so only the fetch (`bastion_hosts.list_all()`) is
included here.

Edges: bastion_host -> subnet (in_subnet), one per
ip_configurations[].subnet.id (the dedicated AzureBastionSubnet Bastion
requires); bastion_host -> virtual_network (in_vnet), via the host's own
top-level virtual_network.id. Both emitted as read — the subnet/
virtual_network endpoints are owned by network.py's own gather(), a
separate module/container (see its own docstring for this pattern).
"""

from azure.mgmt.network import NetworkManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_bastion_hosts(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.bastion_hosts.list_all())


def gather(credential, subscription_id, writer):
    for host in get_bastion_hosts(credential, subscription_id):
        raw = _as_dict(host)
        added = writer.add_resource(
            resource_type='bastion_host',
            region=host.location or 'global',
            resource_id=host.id,
            resource_name=host.name,
            scope_id=_resource_group(host.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for ip_config in (raw.get('ip_configurations') or []):
                subnet_id = (ip_config.get('subnet') or {}).get('id')
                if subnet_id:
                    writer.add_edge(from_type='bastion_host', from_id=host.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
            vnet_id = (raw.get('virtual_network') or {}).get('id')
            if vnet_id:
                writer.add_edge(from_type='bastion_host', from_id=host.id, to_type='virtual_network', to_id=vnet_id, relationship='in_vnet')
