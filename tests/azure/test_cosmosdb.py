"""Unit tests for lensix_inventory.azure.cosmosdb — Cosmos DB accounts.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (get_advanced_threat_protection's own isolation),
which was untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.cosmosdb as m


def _account(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DocumentDB/databaseAccounts/a1', name='a1',
             vnet_rule_subnet_ids=None):
    account = MagicMock()
    account.location = location
    account.id = rid
    account.name = name
    raw = {'id': rid, 'name': name}
    if vnet_rule_subnet_ids is not None:
        raw['virtual_network_rules'] = [{'id': sid} for sid in vnet_rule_subnet_ids]
    account.as_dict.return_value = raw
    return account


class TestGather:
    def test_adds_one_resource_per_account(self):
        w = MagicMock()
        account = _account()
        cosmos_client = MagicMock()
        cosmos_client.database_accounts.list.return_value = [account]
        sc_client = MagicMock()
        sc_client.advanced_threat_protection.get.side_effect = Exception('not configured')
        with patch.object(m, 'CosmosDBManagementClient', return_value=cosmos_client), \
             patch.object(m, 'SecurityCenter', return_value=sc_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='cosmosdb_account', region='eastus', resource_id=account.id,
            resource_name='a1', scope_id='my-rg',
            raw={'id': account.id, 'name': 'a1', '_AdvancedThreatProtection': None},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        account = _account()
        account.as_dict.return_value = {'id': account.id, 'name': 'a1', 'tags': {'lensix-suppress': 'true'}}
        cosmos_client = MagicMock()
        cosmos_client.database_accounts.list.return_value = [account]
        sc_client = MagicMock()
        sc_client.advanced_threat_protection.get.side_effect = Exception('not configured')
        with patch.object(m, 'CosmosDBManagementClient', return_value=cosmos_client), \
             patch.object(m, 'SecurityCenter', return_value=sc_client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_accounts_gathers_nothing(self):
        w = MagicMock()
        cosmos_client = MagicMock()
        cosmos_client.database_accounts.list.return_value = []
        with patch.object(m, 'CosmosDBManagementClient', return_value=cosmos_client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, account, w=None):
        w = w or MagicMock()
        cosmos_client = MagicMock()
        cosmos_client.database_accounts.list.return_value = [account]
        sc_client = MagicMock()
        sc_client.advanced_threat_protection.get.side_effect = Exception('not configured')
        with patch.object(m, 'CosmosDBManagementClient', return_value=cosmos_client), \
             patch.object(m, 'SecurityCenter', return_value=sc_client):
            m.gather('cred', 'sub-1', w)
        return w

    def test_a_vnet_rule_gets_an_in_subnet_edge(self):
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        w = self._gather(account)
        w.add_edge.assert_called_once_with(
            from_type='cosmosdb_account', from_id=account.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_multiple_vnet_rules_each_get_their_own_edge(self):
        account = _account(vnet_rule_subnet_ids=['subnet-1', 'subnet-2'])
        w = self._gather(account)
        to_ids = {c.kwargs['to_id'] for c in w.add_edge.call_args_list}
        assert to_ids == {'subnet-1', 'subnet-2'}

    def test_no_vnet_rules_gets_no_edges(self):
        account = _account()
        w = self._gather(account)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_account_gets_no_edges(self):
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(account, w=w)
        w.add_edge.assert_not_called()
