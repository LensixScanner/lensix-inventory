"""Unit tests for lensix_inventory.azure.authorization — custom role definitions."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.authorization as m


def _role_def(role_type='CustomRole', role_name='My Custom Role',
              rid='/subscriptions/s1/providers/Microsoft.Authorization/roleDefinitions/rd1', name='rd1'):
    rd = MagicMock()
    rd.role_type = role_type
    rd.role_name = role_name
    rd.id = rid
    rd.name = name
    rd.as_dict.return_value = {'id': rid, 'name': name}
    return rd


class TestGetCustomRoleDefinitions:
    def test_filters_to_customrole_only(self):
        custom = _role_def(role_type='CustomRole')
        builtin = _role_def(role_type='BuiltInRole')
        client = MagicMock()
        client.role_definitions.list.return_value = [custom, builtin]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client):
            defs = m.get_custom_role_definitions('cred', 'sub-1')
        assert defs == [custom]
        client.role_definitions.list.assert_called_once_with('/subscriptions/sub-1')


def _lock(lock_id, *, level='CanNotDelete', name=None):
    lock = MagicMock()
    lock.id = lock_id
    lock.name = name or lock_id.rsplit('/', 1)[-1]
    lock.level = level
    lock.as_dict.return_value = {'id': lock_id, 'name': lock.name}
    return lock


def _lock_client(locks=None):
    client = MagicMock()
    client.management_locks.list_at_subscription_level.return_value = locks or []
    return client


class TestGetManagementLocks:
    def test_returns_every_lock_from_the_subscription_level_list_call(self):
        lock = _lock('/subscriptions/s1/providers/Microsoft.Authorization/locks/dont-delete')
        with patch.object(m, 'ManagementLockClient', return_value=_lock_client([lock])):
            locks = m.get_management_locks('cred', 'sub-1')
        assert locks == [lock]


class TestLockScope:
    def test_strips_the_lock_suffix_from_a_resource_level_lock(self):
        lock_id = '/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1/providers/Microsoft.Authorization/locks/dont-delete'
        assert m.lock_scope(lock_id) == '/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1'

    def test_strips_the_lock_suffix_from_a_resource_group_level_lock(self):
        lock_id = '/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Authorization/locks/dont-delete'
        assert m.lock_scope(lock_id) == '/subscriptions/s1/resourceGroups/rg1'

    def test_strips_the_lock_suffix_from_a_subscription_level_lock(self):
        lock_id = '/subscriptions/s1/providers/Microsoft.Authorization/locks/dont-delete'
        assert m.lock_scope(lock_id) == '/subscriptions/s1'

    def test_is_case_insensitive_about_the_provider_segment(self):
        lock_id = '/subscriptions/s1/resourceGroups/rg1/Providers/microsoft.authorization/Locks/dont-delete'
        assert m.lock_scope(lock_id) == '/subscriptions/s1/resourceGroups/rg1'

    def test_returns_none_for_an_unrecognized_shape(self):
        assert m.lock_scope('/subscriptions/s1/resourceGroups/rg1') is None
        assert m.lock_scope('') is None
        assert m.lock_scope(None) is None


class TestGather:
    def test_adds_one_resource_per_custom_role_named_from_role_name(self):
        w = MagicMock()
        role_def = _role_def(role_name='My Custom Role')
        client = MagicMock()
        client.role_definitions.list.return_value = [role_def]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client()):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='role_definition', region='global', resource_id=role_def.id,
            resource_name='My Custom Role', scope_id=None, raw={'id': role_def.id, 'name': 'rd1'},
        )

    def test_falls_back_to_the_technical_name_without_a_role_name(self):
        w = MagicMock()
        role_def = _role_def(role_name=None)
        client = MagicMock()
        client.role_definitions.list.return_value = [role_def]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client()):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'rd1'

    def test_no_custom_roles_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.role_definitions.list.return_value = [_role_def(role_type='BuiltInRole')]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client()):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()

    def test_adds_one_resource_per_management_lock(self):
        w = MagicMock()
        client = MagicMock()
        client.role_definitions.list.return_value = []
        lock = _lock('/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Authorization/locks/dont-delete')
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client([lock])):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='authorization_lock', region='global', resource_id=lock.id,
            resource_name=lock.name, scope_id='rg1', raw={'id': lock.id, 'name': lock.name},
        )

    def test_emits_an_applies_to_edge_for_a_resource_group_scoped_lock(self):
        w = MagicMock()
        w.add_resource.return_value = True
        client = MagicMock()
        client.role_definitions.list.return_value = []
        lock = _lock('/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Authorization/locks/dont-delete')
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client([lock])):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='authorization_lock', from_id=lock.id,
            to_type='resource_group', to_id='/subscriptions/s1/resourceGroups/rg1',
            relationship='applies_to',
        )

    def test_no_edge_for_a_subscription_scoped_lock(self):
        w = MagicMock()
        w.add_resource.return_value = True
        client = MagicMock()
        client.role_definitions.list.return_value = []
        lock = _lock('/subscriptions/s1/providers/Microsoft.Authorization/locks/dont-delete')
        with patch.object(m, 'AuthorizationManagementClient', return_value=client), \
             patch.object(m, 'ManagementLockClient', return_value=_lock_client([lock])):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
