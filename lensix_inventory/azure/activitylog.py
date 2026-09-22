"""Azure Activity Log Alert gathering.

One call — `activity_log_alerts.list_by_subscription_id()` — covers every
alert-coverage check (each just pattern-matches alert conditions against
specific operation names). Only the fetch is included here; the condition-
matching itself is finding evaluation and stays server-side.

Edges: activity_log_alert -> resource_group (applies_to), one per scope
entry in `scopes[]` that's exactly a resource-group-level ARM path (see
_util.is_resource_group_scope's own docstring — same helper, same
reasoning as policy.py's own identical edge; subscription-level scopes,
the overwhelmingly common case for this alert type, and resource-level
scopes are both skipped). No edge for `actions[].action_group_id` — Azure
Monitor Action Groups aren't gathered as their own resource type
anywhere in this codebase, same "no persisted target" reasoning as
appservice.py's own app_service_plan note.
"""

from azure.mgmt.monitor import MonitorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict, is_resource_group_scope


def get_activity_log_alerts(credential, subscription_id):
    monitor_client = MonitorManagementClient(credential, subscription_id)
    return list(monitor_client.activity_log_alerts.list_by_subscription_id())


def gather(credential, subscription_id, writer):
    for alert in get_activity_log_alerts(credential, subscription_id):
        raw = _as_dict(alert)
        added = writer.add_resource(
            resource_type='activity_log_alert',
            region='global',
            resource_id=alert.id,
            resource_name=alert.name,
            scope_id=_resource_group(alert.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for scope in (raw.get('scopes') or []):
                if is_resource_group_scope(scope):
                    writer.add_edge(from_type='activity_log_alert', from_id=alert.id, to_type='resource_group', to_id=scope, relationship='applies_to')
