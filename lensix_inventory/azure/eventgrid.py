"""Azure Event Grid gathering.

`domains.list_by_subscription()` already returns everything needed for
public-network-access evaluation. Diagnostic settings need a per-domain
sub-call — `diagnostic_settings.list(domain.id)` — a plain list call, so
it's included too and merged into each domain's raw record as
`_DiagnosticSettings`.
"""

from azure.mgmt.eventgrid import EventGridManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_domains(credential, subscription_id):
    eg_client = EventGridManagementClient(credential, subscription_id)
    return list(eg_client.domains.list_by_subscription())


def get_diagnostic_settings(credential, subscription_id, domain_id):
    monitor_client = MonitorManagementClient(credential, subscription_id)
    try:
        return [_as_dict(s) for s in monitor_client.diagnostic_settings.list(domain_id)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    for domain in get_domains(credential, subscription_id):
        raw = _as_dict(domain)
        raw['_DiagnosticSettings'] = get_diagnostic_settings(credential, subscription_id, domain.id)

        writer.add_resource(
            resource_type='eventgrid_domain',
            region=domain.location or 'global',
            resource_id=domain.id,
            resource_name=domain.name,
            scope_id=_resource_group(domain.id),
            raw=raw,
        )
