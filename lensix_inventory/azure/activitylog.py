"""Azure Activity Log Alert gathering.

One call — `activity_log_alerts.list_by_subscription_id()` — covers every
alert-coverage check (each just pattern-matches alert conditions against
specific operation names). Only the fetch is included here; the condition-
matching itself is finding evaluation and stays server-side.
"""

from azure.mgmt.monitor import MonitorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_activity_log_alerts(credential, subscription_id):
    monitor_client = MonitorManagementClient(credential, subscription_id)
    return list(monitor_client.activity_log_alerts.list_by_subscription_id())


def gather(credential, subscription_id, writer):
    for alert in get_activity_log_alerts(credential, subscription_id):
        writer.add_resource(
            resource_type='activity_log_alert',
            region='global',
            resource_id=alert.id,
            resource_name=alert.name,
            scope_id=_resource_group(alert.id),
            raw=_as_dict(alert),
        )
