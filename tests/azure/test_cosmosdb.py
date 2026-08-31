"""Unit tests for lensix_inventory.azure.cosmosdb — Cosmos DB accounts.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (get_advanced_threat_protection's own isolation),
which was untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.cosmosdb as m


def _account(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DocumentDB/databaseAccounts/a1', name='a1'):
    account = MagicMock()
    account.location = location
    account.id = rid
    account.name = name
    account.as_dict.return_value = {'id': rid, 'name': name}
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
