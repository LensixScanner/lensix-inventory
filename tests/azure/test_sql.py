"""Unit tests for lensix_inventory.azure.sql — SQL servers and their
firewall rules.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. FirewallRule
has no `tags` field of its own (confirmed — the SDK model rejects it
entirely), the same shape as azure.network's vnet_peering: each firewall
rule inherits the parent server's own tags at gather time instead.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.sql as m


def _server(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Sql/servers/s1',
            name='s1', tags=None):
    server = MagicMock()
    server.location = location
    server.id = rid
    server.name = name
    server.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return server


def _rule(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Sql/servers/s1/firewallRules/AllowAll', name='AllowAll'):
    rule = MagicMock()
    rule.id = rid
    rule.name = name
    rule.as_dict.return_value = {'id': rid, 'name': name}
    return rule


def _vnet_rule(subnet_id='/subscriptions/s1/.../subnets/sql-subnet'):
    rule = MagicMock()
    rule.virtual_network_subnet_id = subnet_id
    return rule


class TestGather:
    def test_adds_one_resource_per_server_and_rule(self):
        w = MagicMock()
        server = _server()
        rule = _rule()
        sql_client = MagicMock()
        sql_client.servers.list.return_value = [server]
        sql_client.server_security_alert_policies.get.side_effect = Exception('none')
        sql_client.server_blob_auditing_policies.get.side_effect = Exception('none')
        sql_client.firewall_rules.list_by_server.return_value = [rule]
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 2
        server_call, rule_call = w.add_resource.call_args_list
        assert server_call.kwargs['resource_type'] == 'sql_server'
        assert server_call.kwargs['tags'] is None
        assert rule_call.kwargs['resource_type'] == 'sql_server_firewall_rule'
        assert rule_call.kwargs['tags'] is None

    def test_firewall_rule_inherits_the_parent_servers_own_tags(self):
        w = MagicMock()
        server = _server(tags={'lensix-suppress-checks': 'sql_publicfirewall'})
        rule = _rule()
        sql_client = MagicMock()
        sql_client.servers.list.return_value = [server]
        sql_client.server_security_alert_policies.get.side_effect = Exception('none')
        sql_client.server_blob_auditing_policies.get.side_effect = Exception('none')
        sql_client.firewall_rules.list_by_server.return_value = [rule]
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        server_call, rule_call = w.add_resource.call_args_list
        assert server_call.kwargs['tags'] == {'lensix-suppress-checks': 'sql_publicfirewall'}
        assert rule_call.kwargs['tags'] == {'lensix-suppress-checks': 'sql_publicfirewall'}

    def test_fully_suppressing_the_server_leaves_its_rule_tagged_the_same_way(self):
        w = MagicMock()
        server = _server(tags={'lensix-suppress': 'true'})
        rule = _rule()
        sql_client = MagicMock()
        sql_client.servers.list.return_value = [server]
        sql_client.server_security_alert_policies.get.side_effect = Exception('none')
        sql_client.server_blob_auditing_policies.get.side_effect = Exception('none')
        sql_client.firewall_rules.list_by_server.return_value = [rule]
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        server_call, rule_call = w.add_resource.call_args_list
        assert server_call.kwargs['tags'] == {'lensix-suppress': 'true'}
        assert rule_call.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_server_list_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        sql_client = MagicMock()
        sql_client.servers.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'sql:servers'
        w.add_resource.assert_not_called()

    def test_no_servers_gathers_nothing(self):
        w = MagicMock()
        sql_client = MagicMock()
        sql_client.servers.list.return_value = []
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherVnetRuleEdges:
    def _sql_client(self, server, vnet_rules=None):
        sql_client = MagicMock()
        sql_client.servers.list.return_value = [server]
        sql_client.server_security_alert_policies.get.side_effect = Exception('none')
        sql_client.server_blob_auditing_policies.get.side_effect = Exception('none')
        sql_client.firewall_rules.list_by_server.return_value = []
        sql_client.virtual_network_rules.list_by_server.return_value = vnet_rules or []
        return sql_client

    def test_a_vnet_rule_gets_an_in_subnet_edge(self):
        w = MagicMock()
        server = _server()
        sql_client = self._sql_client(server, [_vnet_rule(subnet_id='subnet-1')])
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='sql_server', from_id=server.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_multiple_vnet_rules_each_get_their_own_edge(self):
        w = MagicMock()
        server = _server()
        sql_client = self._sql_client(server, [_vnet_rule(subnet_id='subnet-1'), _vnet_rule(subnet_id='subnet-2')])
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        to_ids = {c.kwargs['to_id'] for c in w.add_edge.call_args_list}
        assert to_ids == {'subnet-1', 'subnet-2'}

    def test_no_vnet_rules_gets_no_edges(self):
        w = MagicMock()
        server = _server()
        sql_client = self._sql_client(server, [])
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_vnet_rules_list_failure_is_recorded_and_gather_continues(self):
        w = MagicMock()
        server = _server()
        sql_client = self._sql_client(server)
        sql_client.virtual_network_rules.list_by_server.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        assert any(c.kwargs['source'] == 'sql:vnet_rules:s1' for c in w.add_error.call_args_list)
        w.add_edge.assert_not_called()
        # The server resource itself, and its (empty) firewall-rule fetch,
        # still succeed — one VNet-rule fetch failure doesn't abort gather().
        assert w.add_resource.call_count == 1

    def test_a_fully_suppressed_server_gets_no_vnet_rule_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        server = _server()
        sql_client = self._sql_client(server, [_vnet_rule(subnet_id='subnet-1')])
        with patch('azure.mgmt.sql.SqlManagementClient', return_value=sql_client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
