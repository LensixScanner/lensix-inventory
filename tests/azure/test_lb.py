"""Unit tests for lensix_inventory.azure.lb — Load Balancers.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.lb as m


def _lb(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/loadBalancers/lb1', name='lb1'):
    lb = MagicMock()
    lb.location = location
    lb.id = rid
    lb.name = name
    lb.as_dict.return_value = {'id': rid, 'name': name}
    return lb


class TestGather:
    def test_adds_one_resource_per_lb(self):
        w = MagicMock()
        lb = _lb()
        network = MagicMock()
        network.load_balancers.list_all.return_value = [lb]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='load_balancer', region='eastus', resource_id=lb.id,
            resource_name='lb1', scope_id='my-rg',
            raw={'id': lb.id, 'name': 'lb1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        lb = _lb()
        lb.as_dict.return_value = {'id': lb.id, 'name': 'lb1', 'tags': {'lensix-suppress': 'true'}}
        network = MagicMock()
        network.load_balancers.list_all.return_value = [lb]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        network = MagicMock()
        network.load_balancers.list_all.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'lb:load_balancers'
        w.add_resource.assert_not_called()

    def test_no_lbs_gathers_nothing(self):
        w = MagicMock()
        network = MagicMock()
        network.load_balancers.list_all.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
