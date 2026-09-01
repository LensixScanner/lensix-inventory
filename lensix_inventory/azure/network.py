"""Virtual network gathering — VNets, their subnets, and their peering
connections.

Only the data-fetching calls are included here (virtual_networks.list_all,
virtual_network_peerings.list) — missing-DDoS-protection, single-subnet,
and active-peering evaluation is left server-side. Subnets are gathered
from each VNet's own `raw['subnets']` embedded list (from `as_dict()`) —
no separate subnets.list() call exists on NetworkManagementClient; a VNet's
subnets are only ever enumerable through the parent VNet itself.

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
        vnet_raw = vnet.as_dict()
        vnet_tags = vnet_raw.get('tags')
        writer.add_resource(
            resource_type='virtual_network',
            region=region,
            resource_id=vnet.id,
            resource_name=vnet.name,
            scope_id=rg,
            raw=vnet_raw,
            tags=vnet_tags,
        )

        # Subnets have no `tags` field of their own in the Azure API (same
        # gap as vnet_peering above) and no list operation of their own
        # here — they inherit the parent VNet's tags, same rationale as
        # peerings: a lensix-suppress-checks tag on the VNet also covers
        # per-subnet checks (e.g. missing-NSG-association) evaluated
        # against this data later.
        for subnet in (vnet_raw.get('subnets') or []):
            writer.add_resource(
                resource_type='subnet',
                region=region,
                resource_id=subnet.get('id'),
                resource_name=subnet.get('name'),
                scope_id=vnet.id,
                raw=subnet,
                tags=vnet_tags,
            )

        try:
            peerings = get_peerings(credential, subscription_id, rg, vnet.name)
        except Exception as e:
            writer.add_error(region=region, source=f'network:peerings:{vnet.name}', message=e)
            continue

        for peering in peerings:
            # VirtualNetworkPeering has no `tags` field of its own (the SDK
            # model rejects it entirely) — it inherits the parent VNet's own
            # tags instead, so lensix-suppress/lensix-suppress-checks on the
            # VNet also suppresses (fully, or just network_unknownpeering
            # for) each of its peerings. A fully-suppressed VNet's peerings
            # are therefore never gathered either, same as the VNet itself.
            writer.add_resource(
                resource_type='vnet_peering',
                region=region,
                resource_id=peering.id,
                resource_name=peering.name,
                scope_id=rg,
                raw=peering.as_dict(),
                tags=vnet_tags,
            )
