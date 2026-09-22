"""Azure Application Gateway gathering.

`application_gateways.list_all()` already returns everything needed for
WAF-configuration, HTTP-listener, and SSL-policy evaluation
(web_application_firewall_configuration, http_listeners, ssl_policy) —
that evaluation itself is left server-side.

Edges: application_gateway -> subnet (in_subnet), one per
gateway_ip_configurations[] entry (the dedicated subnet the gateway
itself is deployed into — distinct from a backend's own subnet);
application_gateway -> network_interface (routes_to_nic), one per backend
pool's own backend_ip_configurations[].id, same NIC-ip-config-id
stripping technique as lb.py's own routes_to_nic edge (see its docstring
for the full reasoning — the single highest-value edge in that module,
same here). backend_address_pools[].backend_addresses (FQDN/plain-IP
targets, not NIC references) has no persisted target to join to, so
that's skipped. Both edges are emitted as read: their endpoints (subnet,
network_interface) are each owned by a different module/container, so
there's no same-run id list to case-resolve against (see network.py's
own docstring for this pattern).
"""

from azure.mgmt.network import NetworkManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_gateways(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.application_gateways.list_all())


def gather(credential, subscription_id, writer):
    for gw in get_gateways(credential, subscription_id):
        raw = _as_dict(gw)
        added = writer.add_resource(
            resource_type='application_gateway',
            region=gw.location or 'global',
            resource_id=gw.id,
            resource_name=gw.name,
            scope_id=_resource_group(gw.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for gw_ip_config in (raw.get('gateway_ip_configurations') or []):
                subnet_id = (gw_ip_config.get('subnet') or {}).get('id')
                if subnet_id:
                    writer.add_edge(from_type='application_gateway', from_id=gw.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')

            for pool in (raw.get('backend_address_pools') or []):
                for ip_config in (pool.get('backend_ip_configurations') or []):
                    ip_config_id = ip_config.get('id')
                    if ip_config_id and '/ipConfigurations/' in ip_config_id:
                        nic_id = ip_config_id.split('/ipConfigurations/')[0]
                        writer.add_edge(from_type='application_gateway', from_id=gw.id, to_type='network_interface', to_id=nic_id, relationship='routes_to_nic')
