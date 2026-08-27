"""Unit tests for lensix_inventory.azure.activitylog — Activity Log Alerts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.activitylog as m


def _alert(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Insights/activityLogAlerts/a1', name='a1'):
    alert = MagicMock()
    alert.id = rid
    alert.name = name
    alert.as_dict.return_value = {'id': rid, 'name': name}
    return alert


class TestGather:
    def test_adds_one_resource_per_alert_always_scoped_to_global(self):
        w = MagicMock()
        alert = _alert()
        client = MagicMock()
        client.activity_log_alerts.list_by_subscription_id.return_value = [alert]
        with patch.object(m, 'MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='activity_log_alert', region='global', resource_id=alert.id,
            resource_name='a1', scope_id='my-rg', raw={'id': alert.id, 'name': 'a1'},
        )

    def test_no_alerts_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.activity_log_alerts.list_by_subscription_id.return_value = []
        with patch.object(m, 'MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
