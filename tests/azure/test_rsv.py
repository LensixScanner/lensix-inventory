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
        rsv_client.vaults.list_by_subscription_id.return_value = [vault]
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
        rsv_client.vaults.list_by_subscription_id.return_value = [vault]
        monitor_client = MagicMock()
        monitor_client.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription_id.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=MagicMock()):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'rsv:vaults'
        w.add_resource.assert_not_called()

    def test_no_vaults_gathers_nothing(self):
        w = MagicMock()
        rsv_client = MagicMock()
        rsv_client.vaults.list_by_subscription_id.return_value = []
        with patch('azure.mgmt.recoveryservices.RecoveryServicesClient', return_value=rsv_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=MagicMock()):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


def test_get_vaults_calls_a_real_method_on_the_installed_sdk():
    """Regression test: every test above mocks RecoveryServicesClient with a
    bare MagicMock(), which happily accepts ANY attribute name — including
    one that doesn't exist on the real SDK class at all. get_vaults()
    previously called vaults.list_by_subscription(), which raises
    AttributeError against a real azure-mgmt-recoveryservices client (the
    real method is list_by_subscription_id()) — every mocked test above
    kept passing throughout, since a MagicMock can't catch this class of
    bug. This test checks against the REAL installed SDK class instead of
    a mock, so a similar drift can't hide the same way again."""
    from azure.mgmt.recoveryservices.operations import VaultsOperations
    assert hasattr(VaultsOperations, 'list_by_subscription_id')


def _protected_item(source_resource_id):
    item = MagicMock()
    item.properties.source_resource_id = source_resource_id
    return item


class TestGetProtectedVmResourceIds:
    def test_unions_protected_ids_across_every_vault(self):
        vault1 = _vault(rid='/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.RecoveryServices/vaults/v1', name='v1')
        vault2 = _vault(rid='/subscriptions/s1/resourceGroups/rg2/providers/Microsoft.RecoveryServices/vaults/v2', name='v2')
        backup_client = MagicMock()

        def _list(vault_name, resource_group_name, **kw):
            if vault_name == 'v1':
                return [_protected_item('/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/VM1')]
            return [_protected_item('/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/VM2')]
        backup_client.backup_protected_items.list.side_effect = _list
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            ids = m.get_protected_vm_resource_ids('cred', 'sub-1', [vault1, vault2])
        assert ids == {
            '/subscriptions/s1/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm1',
            '/subscriptions/s1/resourcegroups/rg/providers/microsoft.compute/virtualmachines/vm2',
        }

    def test_calls_list_once_per_vault_with_its_own_name_and_resource_group(self):
        vault = _vault(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.RecoveryServices/vaults/v1', name='v1')
        backup_client = MagicMock()
        backup_client.backup_protected_items.list.return_value = []
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            m.get_protected_vm_resource_ids('cred', 'sub-1', [vault])
        backup_client.backup_protected_items.list.assert_called_once_with('v1', 'my-rg')

    def test_no_vaults_returns_empty_set(self):
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=MagicMock()):
            assert m.get_protected_vm_resource_ids('cred', 'sub-1', []) == set()

    def test_item_without_source_resource_id_is_skipped(self):
        vault = _vault()
        item = MagicMock()
        item.properties.source_resource_id = None
        backup_client = MagicMock()
        backup_client.backup_protected_items.list.return_value = [item]
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            assert m.get_protected_vm_resource_ids('cred', 'sub-1', [vault]) == set()

    def test_item_with_no_properties_at_all_is_skipped(self):
        vault = _vault()
        item = MagicMock()
        item.properties = None
        backup_client = MagicMock()
        backup_client.backup_protected_items.list.return_value = [item]
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            assert m.get_protected_vm_resource_ids('cred', 'sub-1', [vault]) == set()

    def test_vault_with_unparseable_resource_group_is_skipped_not_erroring(self):
        vault = _vault(rid='not-a-real-arm-id', name='v1')
        backup_client = MagicMock()
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            assert m.get_protected_vm_resource_ids('cred', 'sub-1', [vault]) == set()
        backup_client.backup_protected_items.list.assert_not_called()

    def test_lookup_error_propagates(self):
        # Raises rather than swallowing — vm.py's own gather() is the
        # error boundary (see TestGatherProtectedByAzureBackup in
        # tests/azure/test_vm.py), not this function.
        vault = _vault()
        backup_client = MagicMock()
        backup_client.backup_protected_items.list.side_effect = RuntimeError('AccessDenied')
        with patch('azure.mgmt.recoveryservicesbackup.RecoveryServicesBackupClient', return_value=backup_client):
            try:
                m.get_protected_vm_resource_ids('cred', 'sub-1', [vault])
                assert False, 'expected RuntimeError'
            except RuntimeError:
                pass
