"""Load balancer gathering.

Only the data-fetching calls are included here (load_balancers.list_all,
diagnostic_settings.list) — public-frontend, port-80-without-HTTPS, and
empty-backend-pool evaluation is left server-side. Frontend IP
configurations, load-balancing rules, and backend address pools are all
already embedded in the full `LoadBalancer.as_dict()` payload, so Lensix
can recompute all of that from the raw record without any extra per-pool
gathering here.

Diagnostic settings (used for missing-diagnostics evaluation) are merged in
here as `_DiagnosticSettings` rather than re-listed by `monitor.py`, so
this module's own per-LB fan-out call is the only place that data is
fetched.

Edges: load_balancer -> subnet (in_subnet), one per internal frontend
(frontend_ip_configurations[].subnet.id — a public frontend's own
public_ip_address.id has no persisted target anywhere in this codebase,
so it's skipped, same "no resource type to join to" reasoning as
appservice.py's own app_service_plan note); load_balancer -> virtual_network
(in_vnet), one per backend pool's own virtual_network.id when set;
load_balancer -> network_interface (routes_to_nic) — the single highest-
value edge here, one per backend pool's own backend_ip_configurations[].id,
which is a NIC's *ip configuration* sub-id
(.../networkInterfaces/nic1/ipConfigurations/ipconfig1), not the NIC's own
id — stripped down to the parent NIC id (everything before
'/ipConfigurations/') so it actually resolves against defender.py's own
persisted network_interface resource_ids. Chained with vm.py's own
vm -> network_interface edge, this is what turns an isolated load-balancer
finding into "this LB actually reaches these N real VMs" — the same
highest-value edge AWS's own load_balancer -> ec2_instance/lambda_function
edges are. All three are emitted as read: their endpoints (subnet, vnet,
network_interface) are each owned by a different module/container, so
there's no same-run id list to case-resolve against (see network.py's own
docstring for this pattern).

Requires: azure-mgmt-network, azure-mgmt-monitor.
"""

from ._util import resource_group as _resource_group

def get_load_balancers(credential, subscription_id):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.load_balancers.list_all())


def get_diagnostic_settings(monitor_client, resource_uri):
    try:
        return [s.as_dict() for s in monitor_client.diagnostic_settings.list(resource_uri=resource_uri)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    from azure.mgmt.monitor import MonitorManagementClient

    monitor_client = MonitorManagementClient(credential, subscription_id)

    try:
        lbs = get_load_balancers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='lb:load_balancers', message=e)
        return

    for lb in lbs:
        raw = lb.as_dict()
        raw['_DiagnosticSettings'] = get_diagnostic_settings(monitor_client, lb.id)
        added = writer.add_resource(
            resource_type='load_balancer',
            region=lb.location or 'global',
            resource_id=lb.id,
            resource_name=lb.name,
            scope_id=_resource_group(lb.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for frontend in (raw.get('frontend_ip_configurations') or []):
                subnet_id = (frontend.get('subnet') or {}).get('id')
                if subnet_id:
                    writer.add_edge(from_type='load_balancer', from_id=lb.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')

            for pool in (raw.get('backend_address_pools') or []):
                vnet_id = (pool.get('virtual_network') or {}).get('id')
                if vnet_id:
                    writer.add_edge(from_type='load_balancer', from_id=lb.id, to_type='virtual_network', to_id=vnet_id, relationship='in_vnet')
                for ip_config in (pool.get('backend_ip_configurations') or []):
                    ip_config_id = ip_config.get('id')
                    if ip_config_id and '/ipConfigurations/' in ip_config_id:
                        nic_id = ip_config_id.split('/ipConfigurations/')[0]
                        writer.add_edge(from_type='load_balancer', from_id=lb.id, to_type='network_interface', to_id=nic_id, relationship='routes_to_nic')
