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
        writer.add_resource(
            resource_type='load_balancer',
            region=lb.location or 'global',
            resource_id=lb.id,
            resource_name=lb.name,
            scope_id=_resource_group(lb.id),
            raw=raw,
        )
