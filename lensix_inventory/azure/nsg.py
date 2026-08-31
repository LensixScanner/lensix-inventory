"""Network security group gathering.

Only the data-fetching calls are included here
(network_security_groups.list_all, diagnostic_settings.list) — open-port
evaluation (scanning `nsg.security_rules` for public inbound access to
specific ports) and the subscription-level "no Network Watcher configured"
check are left server-side. Every rule the open-port evaluation needs
(direction, access, source address prefix, destination port range/ranges)
is already embedded in the full `NetworkSecurityGroup.as_dict()` payload's
`security_rules` list, so no separate rule-fetching call is needed (unlike
AWS's `sg.py`, where rules require a second API call).

The "no Network Watcher configured" check only needs to know whether any
`network_watcher` resource exists at all, which `networkwatcher.py` already
gathers independently, so Lensix can recompute that from the presence/
absence of that resource type in the uploaded inventory.

Diagnostic settings are merged in here as `_DiagnosticSettings` rather than
re-listed by `monitor.py` — see the note in `monitor.py`'s docstring.

Requires: azure-mgmt-network, azure-mgmt-monitor.
"""

from ._util import resource_group as _resource_group

def get_network_security_groups(credential, subscription_id):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.network_security_groups.list_all())


def get_diagnostic_settings(monitor_client, resource_uri):
    try:
        return [s.as_dict() for s in monitor_client.diagnostic_settings.list(resource_uri=resource_uri)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    from azure.mgmt.monitor import MonitorManagementClient

    monitor_client = MonitorManagementClient(credential, subscription_id)

    try:
        nsgs = get_network_security_groups(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='nsg:network_security_groups', message=e)
        return

    for nsg in nsgs:
        region = nsg.location or 'global'
        raw = nsg.as_dict()
        raw['_DiagnosticSettings'] = get_diagnostic_settings(monitor_client, nsg.id)
        writer.add_resource(
            resource_type='nsg',
            region=region,
            resource_id=nsg.id,
            resource_name=nsg.name,
            scope_id=_resource_group(nsg.id),
            raw=raw,
            tags=raw.get('tags'),
        )
