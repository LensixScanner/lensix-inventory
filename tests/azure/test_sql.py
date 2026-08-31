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
