"""Virtual network gathering — VNets, their subnets, and their peering
connections.

Only the data-fetching calls are included here (virtual_networks.list_all,
virtual_network_peerings.list) — missing-DDoS-protection, single-subnet,
and active-peering evaluation is left server-side. Subnets are gathered
from each VNet's own `raw['subnets']` embedded list (from `as_dict()`) —
no separate subnets.list() call exists on NetworkManagementClient; a VNet's
subnets are only ever enumerable through the parent VNet itself.

Edges: subnet -> virtual_network (in_vnet); vnet_peering -> virtual_network
(peers_with) for both the local VNet and, when resolvable, the remote one
(remote_virtual_network.id, case-resolved against this same
subscription's own vnets — see the resolution map built in gather());
subnet -> nsg (associated_with_nsg), when the subnet has one. The nsg
endpoint is owned by nsg.py's own gather() (a separate module/container —
see the Scan Job Lifecycle section of lensix-web-light's CLAUDE.md), so
unlike the vnet_peering case above there's no same-run nsg list to
case-resolve subnet.network_security_group.id against; emitted as read,
same tolerance persist_resource_edges() already documents for any
unresolved endpoint.

Requires: azure-mgmt-network.
"""

from ._util import resource_group as _resource_group, normalize_id as _normalize_id

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

    # A peering's own remote_virtual_network.id can come back cased
    # differently than the same VNet's own vnet.id from this same
    # list_all() call (see _util.normalize_id's own docstring for the
    # established precedent) — resolve same-subscription peering targets
    # through this map so the edge's to_id always matches the target
    # virtual_network's own persisted resource_id casing. A peering to a
    # VNet outside this subscription (peerings can cross subscriptions)
    # won't resolve here; the edge is still emitted with the raw id,
    # which persist_resource_edges() tolerates as an unresolved endpoint.
    vnet_id_by_normalized = {_normalize_id(v.id): v.id for v in vnets}

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
            subnet_id = subnet.get('id')
            added = writer.add_resource(
                resource_type='subnet',
                region=region,
                resource_id=subnet_id,
                resource_name=subnet.get('name'),
                scope_id=vnet.id,
                raw=subnet,
                tags=vnet_tags,
            )
            if added:
                writer.add_edge(from_type='subnet', from_id=subnet_id, to_type='virtual_network', to_id=vnet.id, relationship='in_vnet')
                nsg_id = (subnet.get('network_security_group') or {}).get('id')
                if nsg_id:
                    writer.add_edge(from_type='subnet', from_id=subnet_id, to_type='nsg', to_id=nsg_id, relationship='associated_with_nsg')

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
            peering_raw = peering.as_dict()
            added = writer.add_resource(
                resource_type='vnet_peering',
                region=region,
                resource_id=peering.id,
                resource_name=peering.name,
                scope_id=rg,
                raw=peering_raw,
                tags=vnet_tags,
            )
            if added:
                writer.add_edge(from_type='vnet_peering', from_id=peering.id, to_type='virtual_network', to_id=vnet.id, relationship='peers_with')
                remote_id = (peering_raw.get('remote_virtual_network') or {}).get('id')
                if remote_id:
                    resolved_remote_id = vnet_id_by_normalized.get(_normalize_id(remote_id), remote_id)
                    writer.add_edge(from_type='vnet_peering', from_id=peering.id, to_type='virtual_network', to_id=resolved_remote_id, relationship='peers_with')
