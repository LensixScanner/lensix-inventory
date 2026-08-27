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


class TestGather:
    def test_adds_one_resource_per_custom_role_named_from_role_name(self):
        w = MagicMock()
        role_def = _role_def(role_name='My Custom Role')
        client = MagicMock()
        client.role_definitions.list.return_value = [role_def]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client):
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
        with patch.object(m, 'AuthorizationManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'rd1'

    def test_no_custom_roles_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.role_definitions.list.return_value = [_role_def(role_type='BuiltInRole')]
        with patch.object(m, 'AuthorizationManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
