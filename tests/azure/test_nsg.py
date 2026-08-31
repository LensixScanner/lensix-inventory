"""Unit tests for lensix_inventory.azure.nsg — Network Security Groups.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.nsg as m


def _nsg(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/networkSecurityGroups/n1', name='n1'):
    nsg = MagicMock()
    nsg.location = location
    nsg.id = rid
    nsg.name = name
    nsg.as_dict.return_value = {'id': rid, 'name': name}
    return nsg


class TestGather:
    def test_adds_one_resource_per_nsg(self):
        w = MagicMock()
        nsg = _nsg()
        network = MagicMock()
        network.network_security_groups.list_all.return_value = [nsg]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='nsg', region='eastus', resource_id=nsg.id,
            resource_name='n1', scope_id='my-rg',
            raw={'id': nsg.id, 'name': 'n1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        nsg = _nsg()
        nsg.as_dict.return_value = {'id': nsg.id, 'name': 'n1', 'tags': {'lensix-suppress': 'true'}}
        network = MagicMock()
        network.network_security_groups.list_all.return_value = [nsg]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        network = MagicMock()
        network.network_security_groups.list_all.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'nsg:network_security_groups'
        w.add_resource.assert_not_called()

    def test_no_nsgs_gathers_nothing(self):
        w = MagicMock()
        network = MagicMock()
        network.network_security_groups.list_all.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
