"""Unit tests for lensix_inventory.azure.resources — resource groups and
their management locks.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. ResourceGroup
carries tags natively; ManagementLockObject has no `tags` field on its own
SDK model at all (confirmed — the SDK discards it with a warning if
passed), a control-plane object in the same architectural class as
authorization's role_definition/policy's policy_assignment.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.resources as m


def _rg(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg', name='my-rg', tags=None):
    rg = MagicMock()
    rg.location = location
    rg.id = rid
    rg.name = name
    rg.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return rg


def _lock(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Authorization/locks/lock1', name='lock1'):
    lock = MagicMock()
    lock.id = rid
    lock.name = name
    lock.as_dict.return_value = {'id': rid, 'name': name}
    return lock


class TestGather:
    def test_adds_one_resource_per_rg_and_lock(self):
        w = MagicMock()
        rg = _rg()
        lock = _lock()
        resource_client = MagicMock()
        resource_client.resource_groups.list.return_value = [rg]
        locks_client = MagicMock()
        locks_client.management_locks.list_at_resource_group_level.return_value = [lock]
        with patch('azure.mgmt.resource.ResourceManagementClient', return_value=resource_client), \
             patch('azure.mgmt.resource.locks.ManagementLockClient', return_value=locks_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 2
        rg_call, lock_call = w.add_resource.call_args_list
        assert rg_call.kwargs['resource_type'] == 'resource_group'
        assert rg_call.kwargs['tags'] is None
        assert lock_call.kwargs['resource_type'] == 'management_lock'
        assert 'tags' not in lock_call.kwargs

    def test_tags_are_passed_through_for_the_resource_group(self):
        w = MagicMock()
        rg = _rg(tags={'lensix-suppress': 'true'})
        resource_client = MagicMock()
        resource_client.resource_groups.list.return_value = [rg]
        locks_client = MagicMock()
        locks_client.management_locks.list_at_resource_group_level.return_value = []
        with patch('azure.mgmt.resource.ResourceManagementClient', return_value=resource_client), \
             patch('azure.mgmt.resource.locks.ManagementLockClient', return_value=locks_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args_list[0].kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_rg_list_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        resource_client = MagicMock()
        resource_client.resource_groups.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.resource.ResourceManagementClient', return_value=resource_client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'resources:resource_groups'
        w.add_resource.assert_not_called()

    def test_lock_list_failure_for_one_rg_does_not_abort_the_others(self):
        w = MagicMock()
        bad = _rg(rid='/subscriptions/s1/resourceGroups/bad', name='bad')
        good = _rg(rid='/subscriptions/s1/resourceGroups/good', name='good')
        good_lock = _lock()
        resource_client = MagicMock()
        resource_client.resource_groups.list.return_value = [bad, good]
        locks_client = MagicMock()

        def _list(rg_name):
            if rg_name == 'bad':
                raise RuntimeError('boom')
            return [good_lock]
        locks_client.management_locks.list_at_resource_group_level.side_effect = _list
        with patch('azure.mgmt.resource.ResourceManagementClient', return_value=resource_client), \
             patch('azure.mgmt.resource.locks.ManagementLockClient', return_value=locks_client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'resources:locks:bad'
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['resource_group', 'resource_group', 'management_lock']

    def test_no_resource_groups_gathers_nothing(self):
        w = MagicMock()
        resource_client = MagicMock()
        resource_client.resource_groups.list.return_value = []
        with patch('azure.mgmt.resource.ResourceManagementClient', return_value=resource_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
