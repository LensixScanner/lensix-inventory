"""Unit tests for lensix_inventory.azure.mysql — Single Server and
Flexible Server.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring for both
server kinds. get_flexible_servers()'s own ImportError fallback (SDK not
installed) is exercised separately from tag support.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.mysql as m


def _server(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DBforMySQL/servers/s1',
            name='s1', tags=None, network=None):
    server = MagicMock()
    server.location = location
    server.id = rid
    server.name = name
    raw = {'id': rid, 'name': name, 'tags': tags}
    if network is not None:
        raw['network'] = network
    server.as_dict.return_value = raw
    return server


def _vnet_rule(subnet_id='/subscriptions/s1/.../subnets/mysql-subnet'):
    rule = MagicMock()
    rule.virtual_network_subnet_id = subnet_id
    return rule


class TestGather:
    def test_adds_one_resource_per_server(self):
        w = MagicMock()
        server = _server()
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = [server]
        mysql_client.server_administrators.list.return_value = []
        mysql_client.configurations.list_by_server.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once()
        call = w.add_resource.call_args
        assert call.kwargs['resource_type'] == 'mysql_server'
        assert call.kwargs['tags'] is None

    def test_tags_are_passed_through_for_a_single_server(self):
        w = MagicMock()
        server = _server(tags={'lensix-suppress': 'true'})
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = [server]
        mysql_client.server_administrators.list.return_value = []
        mysql_client.configurations.list_by_server.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_tags_are_passed_through_for_a_flexible_server(self):
        w = MagicMock()
        flex_server = _server(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DBforMySQL/flexibleServers/fs1',
                               name='fs1', tags={'lensix-suppress-checks': 'mysql_publicaccessenabled'})
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = []
        flex_client = MagicMock()
        flex_client.servers.list.return_value = [flex_server]
        flex_client.configurations.list_by_server.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=flex_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once()
        call = w.add_resource.call_args
        assert call.kwargs['resource_type'] == 'mysql_flexible_server'
        assert call.kwargs['tags'] == {'lensix-suppress-checks': 'mysql_publicaccessenabled'}

    def test_server_list_failure_is_recorded_and_gather_continues_to_flexible_servers(self):
        w = MagicMock()
        mysql_client = MagicMock()
        mysql_client.servers.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'mysql:servers'
        w.add_resource.assert_not_called()

    def test_no_servers_gathers_nothing(self):
        w = MagicMock()
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_single_servers_vnet_rule_gets_an_in_subnet_edge(self):
        w = MagicMock()
        server = _server()
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = [server]
        mysql_client.server_administrators.list.return_value = []
        mysql_client.configurations.list_by_server.return_value = []
        mysql_client.virtual_network_rules.list_by_server.return_value = [_vnet_rule(subnet_id='subnet-1')]
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='mysql_server', from_id=server.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_a_single_servers_vnet_rules_list_failure_is_recorded_and_gather_continues(self):
        w = MagicMock()
        server = _server()
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = [server]
        mysql_client.server_administrators.list.return_value = []
        mysql_client.configurations.list_by_server.return_value = []
        mysql_client.virtual_network_rules.list_by_server.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=MagicMock(servers=MagicMock(list=MagicMock(return_value=[])))):
            m.gather('cred', 'sub-1', w)
        assert any(c.kwargs['source'] == 'mysql:vnet_rules:s1' for c in w.add_error.call_args_list)
        w.add_edge.assert_not_called()
        assert w.add_resource.call_count == 1

    def test_a_flexible_servers_delegated_subnet_gets_an_in_subnet_edge(self):
        w = MagicMock()
        flex_server = _server(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DBforMySQL/flexibleServers/fs1',
                               name='fs1', network={'delegated_subnet_resource_id': 'subnet-1'})
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = []
        flex_client = MagicMock()
        flex_client.servers.list.return_value = [flex_server]
        flex_client.configurations.list_by_server.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=flex_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='mysql_flexible_server', from_id=flex_server.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_a_flexible_server_with_no_vnet_integration_gets_no_edge(self):
        w = MagicMock()
        flex_server = _server(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DBforMySQL/flexibleServers/fs1', name='fs1')
        mysql_client = MagicMock()
        mysql_client.servers.list.return_value = []
        flex_client = MagicMock()
        flex_client.servers.list.return_value = [flex_server]
        flex_client.configurations.list_by_server.return_value = []
        with patch('azure.mgmt.rdbms.mysql.MySQLManagementClient', return_value=mysql_client), \
             patch('azure.mgmt.rdbms.mysql_flexibleservers.MySQLManagementClient', return_value=flex_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
