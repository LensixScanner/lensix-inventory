"""Unit tests for lensix_inventory.azure.vm — VMs, managed disks,
snapshots, and VM Scale Sets.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring for all four
resource types, plus a light check that tag wiring didn't disturb the
existing os_profile.custom_data secret-scrub behavior (scrubbed before
.as_dict() is called — see the module's own docstring for why).
"""

import base64
from unittest.mock import MagicMock, patch

import lensix_inventory.azure.vm as m


def _vm(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1',
        name='vm1', tags=None, custom_data=None, windows_auto_update=None, nic_ids=None):
    vm = MagicMock()
    vm.location = location
    vm.id = rid
    vm.name = name
    vm.os_profile = MagicMock(custom_data=custom_data) if custom_data is not None else None
    raw = {'id': rid, 'name': name, 'tags': tags}
    if windows_auto_update is not None:
        raw['os_profile'] = {'windows_configuration': {'enable_automatic_updates': windows_auto_update}}
    if nic_ids is not None:
        raw['network_profile'] = {'network_interfaces': [{'id': nic_id} for nic_id in nic_ids]}
    vm.as_dict.return_value = raw
    return vm


def _disk(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/disks/d1',
          name='d1', tags=None):
    disk = MagicMock()
    disk.location = location
    disk.id = rid
    disk.name = name
    disk.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return disk


def _snapshot(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/snapshots/snap1',
              name='snap1', tags=None):
    snap = MagicMock()
    snap.location = location
    snap.id = rid
    snap.name = name
    snap.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return snap


def _vmss(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss1',
          name='vmss1', tags=None):
    vmss = MagicMock()
    vmss.location = location
    vmss.id = rid
    vmss.name = name
    vmss.virtual_machine_profile = None
    vmss.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return vmss


def _empty_client():
    client = MagicMock()
    client.virtual_machines.list_all.return_value = []
    client.disks.list.return_value = []
    client.snapshots.list.return_value = []
    client.virtual_machine_scale_sets.list_all.return_value = []
    return client


class TestGather:
    def test_adds_one_resource_per_type(self):
        w = MagicMock()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [_vm()]
        client.disks.list.return_value = [_disk()]
        client.snapshots.list.return_value = [_snapshot()]
        client.virtual_machine_scale_sets.list_all.return_value = [_vmss()]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 4
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['vm', 'disk', 'snapshot', 'vmss']
        for call in w.add_resource.call_args_list:
            assert call.kwargs['tags'] is None

    def test_tags_are_passed_through_independently_for_each_resource_type(self):
        w = MagicMock()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [_vm(tags={'lensix-suppress': 'true'})]
        client.disks.list.return_value = [_disk(tags={'lensix-suppress-checks': 'vm_unattacheddisk'})]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        vm_call, disk_call = w.add_resource.call_args_list
        assert vm_call.kwargs['tags'] == {'lensix-suppress': 'true'}
        assert disk_call.kwargs['tags'] == {'lensix-suppress-checks': 'vm_unattacheddisk'}

    def test_custom_data_is_still_scrubbed_before_as_dict_with_tags_wired(self):
        # Regression guard: tag wiring must not disturb the pre-existing
        # scrub-before-as_dict() ordering documented in the module's own
        # docstring.
        w = MagicMock()
        encoded = base64.b64encode(b'plain cloud-init text').decode()
        vm = _vm(custom_data=encoded, tags={'env': 'prod'})
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert vm.os_profile.custom_data is None
        call = w.add_resource.call_args_list[0]
        assert call.kwargs['tags'] == {'env': 'prod'}
        assert call.kwargs['raw']['_has_custom_data'] is True

    def test_vm_list_failure_is_recorded_and_gather_continues_to_other_types(self):
        w = MagicMock()
        client = _empty_client()
        client.virtual_machines.list_all.side_effect = RuntimeError('boom')
        client.disks.list.return_value = [_disk()]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert any(c.kwargs['source'] == 'vm:virtual_machines' for c in w.add_error.call_args_list)
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['disk']

    def test_no_resources_gathers_nothing(self):
        w = MagicMock()
        client = _empty_client()
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherNicEdges:
    def test_a_vm_gets_an_attached_to_nic_edge_per_network_interface(self):
        w = MagicMock()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [_vm(nic_ids=['nic-1', 'nic-2'])]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        edge_calls = w.add_edge.call_args_list
        assert len(edge_calls) == 2
        assert edge_calls[0].kwargs == {
            'from_type': 'vm', 'from_id': '/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1',
            'to_type': 'network_interface', 'to_id': 'nic-1', 'relationship': 'attached_to_nic',
        }
        assert edge_calls[1].kwargs['to_id'] == 'nic-2'

    def test_a_vm_with_no_network_profile_gets_no_edges(self):
        w = MagicMock()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [_vm()]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_vm_gets_no_nic_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [_vm(nic_ids=['nic-1'])]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()


class TestGatherProtectedByAzureBackup:
    """gather()'s _ProtectedByAzureBackup stamping — Workstream 3."""

    def test_true_when_vm_id_is_in_the_protected_set(self):
        w = MagicMock()
        vm = _vm(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/VM1')
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._rsv, 'get_vaults', return_value=['vault1']), \
             patch.object(m._rsv, 'get_protected_vm_resource_ids',
                           return_value={vm.id.lower()}):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByAzureBackup'] is True

    def test_false_when_vm_id_is_not_in_the_protected_set(self):
        w = MagicMock()
        vm = _vm()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._rsv, 'get_vaults', return_value=['vault1']), \
             patch.object(m._rsv, 'get_protected_vm_resource_ids', return_value=set()):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByAzureBackup'] is False

    def test_none_when_the_backup_lookup_fails(self):
        # A Backup-service failure must not abort VM gather itself —
        # still gathers the VM, but with the field left indeterminate.
        w = MagicMock()
        vm = _vm()
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._rsv, 'get_vaults', side_effect=RuntimeError('AccessDenied')):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByAzureBackup'] is None
        assert any(c.kwargs['source'] == 'vm:backup_protected_items' for c in w.add_error.call_args_list)

    def test_stamped_on_every_vm_from_one_shared_lookup(self):
        # Bounded by vault count, not VM count — the lookup itself is
        # made once for the whole loop, not once per VM.
        w = MagicMock()
        vm1 = _vm(name='vm1', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1')
        vm2 = _vm(name='vm2', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm2')
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm1, vm2]
        get_protected = MagicMock(return_value={vm1.id.lower()})
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._rsv, 'get_vaults', return_value=['vault1']), \
             patch.object(m._rsv, 'get_protected_vm_resource_ids', get_protected):
            m.gather('cred', 'sub-1', w)
        get_protected.assert_called_once()
        raws = [c.kwargs['raw'] for c in w.add_resource.call_args_list]
        assert raws[0]['_ProtectedByAzureBackup'] is True
        assert raws[1]['_ProtectedByAzureBackup'] is False

    def test_the_gather_writer_is_forwarded_so_protects_edges_can_be_emitted(self):
        # get_protected_vm_resource_ids() itself only emits edges when
        # given a writer (see its own tests in test_rsv.py) — this just
        # confirms gather() actually passes its OWN writer through,
        # rather than silently omitting it.
        w = MagicMock()
        client = _empty_client()
        get_protected = MagicMock(return_value=set())
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._rsv, 'get_vaults', return_value=['vault1']), \
             patch.object(m._rsv, 'get_protected_vm_resource_ids', get_protected):
            m.gather('cred', 'sub-1', w)
        assert get_protected.call_args.kwargs['writer'] is w


def _lock_obj(lock_id, level='CanNotDelete'):
    lock = MagicMock()
    lock.id = lock_id
    lock.level = level
    return lock


class TestGatherProtectedByDeletionLock:
    """gather()'s _ProtectedByDeletionLock stamping — Azure's equivalent
    of AWS's disableApiTermination / GCP's deletionProtection is a
    CanNotDelete/ReadOnly management lock on the VM itself, its resource
    group, or the subscription."""

    VM_ID = '/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1'

    def test_true_for_a_lock_directly_on_the_vm(self):
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj(f'{self.VM_ID}/providers/Microsoft.Authorization/locks/dont-delete')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is True

    def test_true_for_a_lock_on_the_resource_group(self):
        # Note: the lock's scope is built from subscription_id ('sub-1',
        # the gather() argument below), NOT from the 's1' embedded in
        # VM_ID's own resource path — vm.py computes rg_scope/sub_scope
        # from its own subscription_id parameter, so this test's lock has
        # to match that, same as the real gather() call always would.
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj('/subscriptions/sub-1/resourceGroups/my-rg/providers/Microsoft.Authorization/locks/dont-delete')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is True

    def test_true_for_a_lock_on_the_subscription(self):
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj('/subscriptions/sub-1/providers/Microsoft.Authorization/locks/dont-delete')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is True

    def test_false_for_a_lock_on_an_unrelated_resource_group(self):
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj('/subscriptions/s1/resourceGroups/other-rg/providers/Microsoft.Authorization/locks/dont-delete')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is False

    def test_false_when_no_locks_exist(self):
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is False

    def test_false_for_a_readonly_level_other_than_candelete_or_readonly(self):
        # NotSpecified is a valid wire value but doesn't actually protect
        # anything — only CanNotDelete/ReadOnly block deletion.
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj(f'{self.VM_ID}/providers/Microsoft.Authorization/locks/noop', level='NotSpecified')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is False

    def test_none_when_the_lock_lookup_fails(self):
        w = MagicMock()
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', side_effect=RuntimeError('AccessDenied')):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_ProtectedByDeletionLock'] is None
        assert any(c.kwargs['source'] == 'vm:management_locks' for c in w.add_error.call_args_list)


class TestGatherManagementLockEdges:
    VM_ID = '/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1'

    def test_emits_a_protects_edge_for_a_matching_lock(self):
        w = MagicMock()
        w.add_resource.return_value = True
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock_id = f'{self.VM_ID}/providers/Microsoft.Authorization/locks/dont-delete'
        lock = _lock_obj(lock_id)
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        edge_calls = [c for c in w.add_edge.call_args_list if c.kwargs.get('relationship') == 'protects']
        assert edge_calls == [
            ((), {'from_type': 'authorization_lock', 'from_id': lock_id, 'to_type': 'vm', 'to_id': vm.id, 'relationship': 'protects'}),
        ]

    def test_no_protects_edge_when_no_lock_matches(self):
        w = MagicMock()
        w.add_resource.return_value = True
        vm = _vm(rid=self.VM_ID)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        lock = _lock_obj('/subscriptions/s1/resourceGroups/other-rg/providers/Microsoft.Authorization/locks/dont-delete')
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m._authorization, 'get_management_locks', return_value=[lock]):
            m.gather('cred', 'sub-1', w)
        assert not any(c.kwargs.get('relationship') == 'protects' for c in w.add_edge.call_args_list)


class TestGatherMaintenanceConfigurationAssignment:
    """gather()'s _HasMaintenanceConfigurationAssignment stamping —
    Workstream 4. Only ever invoked for a VM already failing the static
    enable_automatic_updates check."""

    def test_not_called_and_field_left_unset_for_a_compliant_vm(self):
        w = MagicMock()
        vm = _vm(windows_auto_update=True)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, '_has_maintenance_configuration_assignment') as mock_lookup:
            m.gather('cred', 'sub-1', w)
        mock_lookup.assert_not_called()
        assert '_HasMaintenanceConfigurationAssignment' not in w.add_resource.call_args_list[0].kwargs['raw']

    def test_not_called_for_a_linux_vm_with_no_windows_configuration_at_all(self):
        w = MagicMock()
        vm = _vm()  # no windows_auto_update at all -> no os_profile key in raw
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, '_has_maintenance_configuration_assignment') as mock_lookup:
            m.gather('cred', 'sub-1', w)
        mock_lookup.assert_not_called()

    def test_called_and_stamped_true_for_a_noncompliant_vm(self):
        w = MagicMock()
        vm = _vm(windows_auto_update=False)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, '_has_maintenance_configuration_assignment', return_value=True) as mock_lookup:
            m.gather('cred', 'sub-1', w)
        mock_lookup.assert_called_once_with('cred', 'sub-1', vm)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_HasMaintenanceConfigurationAssignment'] is True

    def test_lookup_failure_stamps_none_and_records_an_error_but_still_gathers_the_vm(self):
        w = MagicMock()
        vm = _vm(windows_auto_update=False)
        client = _empty_client()
        client.virtual_machines.list_all.return_value = [vm]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, '_has_maintenance_configuration_assignment', side_effect=RuntimeError('AccessDenied')):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['raw']['_HasMaintenanceConfigurationAssignment'] is None
        assert any(c.kwargs['source'] == 'vm:maintenance_configuration_assignment' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()


class TestGetAutoscaleSettings:
    def test_returns_settings_from_the_subscription_wide_list(self):
        setting = MagicMock()
        client = MagicMock()
        client.autoscale_settings.list_by_subscription.return_value = [setting]
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            assert m.get_autoscale_settings('cred', 'sub-1') == [setting]

    def test_lookup_error_propagates(self):
        client = MagicMock()
        client.autoscale_settings.list_by_subscription.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.monitor.MonitorManagementClient', return_value=client):
            try:
                m.get_autoscale_settings('cred', 'sub-1')
                assert False, 'expected RuntimeError'
            except RuntimeError:
                pass


class TestHasMaintenanceConfigurationAssignment:
    def test_true_when_any_assignment_exists(self):
        client = MagicMock()
        client.configuration_assignments.list.return_value = [MagicMock()]
        vm = MagicMock(id='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1')
        vm.name = 'vm1'  # MagicMock(name=...) sets the mock's own repr, not an attribute
        with patch('azure.mgmt.maintenance.MaintenanceManagementClient', return_value=client):
            assert m._has_maintenance_configuration_assignment('cred', 'sub-1', vm) is True
        client.configuration_assignments.list.assert_called_once_with(
            'my-rg', 'Microsoft.Compute', 'virtualMachines', 'vm1',
        )

    def test_false_when_no_assignments(self):
        client = MagicMock()
        client.configuration_assignments.list.return_value = []
        vm = MagicMock(id='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1')
        vm.name = 'vm1'  # MagicMock(name=...) sets the mock's own repr, not an attribute
        with patch('azure.mgmt.maintenance.MaintenanceManagementClient', return_value=client):
            assert m._has_maintenance_configuration_assignment('cred', 'sub-1', vm) is False

    def test_lookup_error_propagates(self):
        client = MagicMock()
        client.configuration_assignments.list.side_effect = RuntimeError('boom')
        vm = MagicMock(id='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachines/vm1')
        vm.name = 'vm1'  # MagicMock(name=...) sets the mock's own repr, not an attribute
        with patch('azure.mgmt.maintenance.MaintenanceManagementClient', return_value=client):
            try:
                m._has_maintenance_configuration_assignment('cred', 'sub-1', vm)
                assert False, 'expected RuntimeError'
            except RuntimeError:
                pass


class TestGatherScheduledAutoscale:
    """gather()'s _HasScheduledAutoscale stamping — Workstream 5."""

    def _setting(self, target_resource_uri, recurrences):
        s = MagicMock()
        s.target_resource_uri = target_resource_uri
        s.profiles = [MagicMock(recurrence=r) for r in recurrences]
        return s

    def test_true_when_a_scheduled_profile_targets_this_vmss(self):
        w = MagicMock()
        vmss = _vmss(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachineScaleSets/VMSS1')
        client = _empty_client()
        client.virtual_machine_scale_sets.list_all.return_value = [vmss]
        setting = self._setting(vmss.id, [MagicMock()])  # non-None recurrence
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, 'get_autoscale_settings', return_value=[setting]):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args_list[0].kwargs['raw']
        assert raw['_HasScheduledAutoscale'] is True

    def test_false_when_no_setting_targets_this_vmss(self):
        w = MagicMock()
        vmss = _vmss()
        client = _empty_client()
        client.virtual_machine_scale_sets.list_all.return_value = [vmss]
        other = self._setting('/subscriptions/s1/.../virtualMachineScaleSets/other', [MagicMock()])
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, 'get_autoscale_settings', return_value=[other]):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args_list[0].kwargs['raw']
        assert raw['_HasScheduledAutoscale'] is False

    def test_false_when_the_setting_has_no_recurrence_profile(self):
        # An Autoscale setting can target this VMSS with a metric-based
        # (not schedule-based) profile — recurrence is None, so it must
        # not count as "scheduled."
        w = MagicMock()
        vmss = _vmss()
        client = _empty_client()
        client.virtual_machine_scale_sets.list_all.return_value = [vmss]
        setting = self._setting(vmss.id, [None])
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, 'get_autoscale_settings', return_value=[setting]):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args_list[0].kwargs['raw']
        assert raw['_HasScheduledAutoscale'] is False

    def test_none_when_the_lookup_fails(self):
        w = MagicMock()
        vmss = _vmss()
        client = _empty_client()
        client.virtual_machine_scale_sets.list_all.return_value = [vmss]
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, 'get_autoscale_settings', side_effect=RuntimeError('AccessDenied')):
            m.gather('cred', 'sub-1', w)
        raw = w.add_resource.call_args_list[0].kwargs['raw']
        assert raw['_HasScheduledAutoscale'] is None
        assert any(c.kwargs['source'] == 'vm:autoscale_settings' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()

    def test_lookup_is_one_call_for_the_whole_subscription_not_per_vmss(self):
        w = MagicMock()
        vmss1 = _vmss(name='vmss1', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss1')
        vmss2 = _vmss(name='vmss2', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Compute/virtualMachineScaleSets/vmss2')
        client = _empty_client()
        client.virtual_machine_scale_sets.list_all.return_value = [vmss1, vmss2]
        get_settings = MagicMock(return_value=[])
        with patch('azure.mgmt.compute.ComputeManagementClient', return_value=client), \
             patch.object(m, 'get_autoscale_settings', get_settings):
            m.gather('cred', 'sub-1', w)
        get_settings.assert_called_once()
