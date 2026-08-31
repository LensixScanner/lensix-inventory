"""Unit tests for lensix_inventory.azure.rsv — Recovery Services Vaults.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (get_diagnostic_settings' own isolation), which was
untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.rsv as m


def _vault(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.RecoveryServices/vaults/v1', name='v1'):
    vault = MagicMock()
    vault.location = location
    vault.id = rid
    vault.name = name
    vault.as_dict.return_value = {'id': rid, 'name': name}
    return vault


class TestGather:
    def test_adds_one_resource_per_vault(self):
        w = MagicMock()
        vault = _vault()
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription.return_value = [vault]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='recovery_services_vault', region='eastus', resource_id=vault.id,
            resource_name='v1', scope_id='my-rg',
            raw={'id': vault.id, 'name': 'v1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        vault = _vault()
        vault.as_dict.return_value = {'id': vault.id, 'name': 'v1', 'tags': {'lensix-suppress': 'true'}}
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription.return_value = [vault]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=MagicMock()):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'rsv:vaults'
        w.add_resource.assert_not_called()

    def test_no_vaults_gathers_nothing(self):
        w = MagicMock()
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription.return_value = []
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=MagicMock()):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
