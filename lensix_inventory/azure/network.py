"""Virtual network gathering — VNets and their peering connections.

Only the data-fetching calls are included here (virtual_networks.list_all,
virtual_network_peerings.list) — missing-DDoS-protection, single-subnet,
and active-peering evaluation is left server-side. A `subnet` resource type
isn't in this module's scope yet — see the VNet's own `raw['subnets']`
embedded list from `as_dict()` in the meantime.

Requires: azure-mgmt-network.
"""

from ._util import resource_group as _resource_group

def get_virtual_networks(credential, subscription_id):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.virtual_networks.list_all())


def get_peerings(credential, subscription_id, rg, vnet_name):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.virtual_network_peerings.list(rg, vnet_name))


def gather(credential, subscription_id, writer):
    try:
        vnets = get_virtual_networks(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='network:virtual_networks', message=e)
        return

    for vnet in vnets:
        region = vnet.location or 'global'
        rg = _resource_group(vnet.id)
        writer.add_resource(
            resource_type='virtual_network',
            region=region,
            resource_id=vnet.id,
            resource_name=vnet.name,
            scope_id=rg,
            raw=vnet.as_dict(),
        )

        try:
            peerings = get_peerings(credential, subscription_id, rg, vnet.name)
        except Exception as e:
            writer.add_error(region=region, source=f'network:peerings:{vnet.name}', message=e)
            continue

        for peering in peerings:
            writer.add_resource(
                resource_type='vnet_peering',
                region=region,
                resource_id=peering.id,
                resource_name=peering.name,
                scope_id=rg,
                raw=peering.as_dict(),
            )
