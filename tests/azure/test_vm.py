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
        name='vm1', tags=None, custom_data=None):
    vm = MagicMock()
    vm.location = location
    vm.id = rid
    vm.name = name
    vm.os_profile = MagicMock(custom_data=custom_data) if custom_data is not None else None
    vm.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
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
