"""Unit tests for lensix_inventory.azure.activitylog — Activity Log Alerts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.activitylog as m


def _alert(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Insights/activityLogAlerts/a1', name='a1', scopes=None):
    alert = MagicMock()
    alert.id = rid
    alert.name = name
    raw = {'id': rid, 'name': name}
    if scopes is not None:
        raw['scopes'] = scopes
    alert.as_dict.return_value = raw
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
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        alert = _alert()
        alert.as_dict.return_value = {'id': alert.id, 'name': 'a1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.activity_log_alerts.list_by_subscription_id.return_value = [alert]
        with patch.object(m, 'MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_alerts_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.activity_log_alerts.list_by_subscription_id.return_value = []
        with patch.object(m, 'MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, alert, w=None):
        w = w or MagicMock()
        client = MagicMock()
        client.activity_log_alerts.list_by_subscription_id.return_value = [alert]
        with patch.object(m, 'MonitorManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        return w

    def test_a_resource_group_scoped_alert_gets_an_applies_to_edge(self):
        alert = _alert(scopes=['/subscriptions/s1/resourceGroups/rg1'])
        w = self._gather(alert)
        w.add_edge.assert_called_once_with(
            from_type='activity_log_alert', from_id=alert.id, to_type='resource_group',
            to_id='/subscriptions/s1/resourceGroups/rg1', relationship='applies_to',
        )

    def test_multiple_resource_group_scopes_each_get_their_own_edge(self):
        alert = _alert(scopes=['/subscriptions/s1/resourceGroups/rg1', '/subscriptions/s1/resourceGroups/rg2'])
        w = self._gather(alert)
        to_ids = {c.kwargs['to_id'] for c in w.add_edge.call_args_list}
        assert to_ids == {'/subscriptions/s1/resourceGroups/rg1', '/subscriptions/s1/resourceGroups/rg2'}

    def test_a_subscription_scoped_alert_gets_no_edge(self):
        alert = _alert(scopes=['/subscriptions/s1'])
        w = self._gather(alert)
        w.add_edge.assert_not_called()

    def test_no_scopes_gets_no_edge(self):
        alert = _alert()
        w = self._gather(alert)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_alert_gets_no_edge(self):
        alert = _alert(scopes=['/subscriptions/s1/resourceGroups/rg1'])
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(alert, w=w)
        w.add_edge.assert_not_called()
