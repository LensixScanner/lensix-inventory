"""Unit tests for lensix_inventory.azure.datalake — Data Lake Store accounts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.datalake as m


def _account(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DataLakeStore/accounts/a1', name='a1',
             vnet_rule_subnet_ids=None):
    acct = MagicMock()
    acct.location = location
    acct.id = rid
    acct.name = name
    raw = {'id': rid, 'name': name}
    if vnet_rule_subnet_ids is not None:
        raw['virtual_network_rules'] = [{'subnet_id': sid} for sid in vnet_rule_subnet_ids]
    acct.as_dict.return_value = raw
    return acct


class TestGather:
    def test_adds_one_resource_per_account(self):
        w = MagicMock()
        account = _account()
        client = MagicMock()
        client.accounts.list.return_value = [account]
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='data_lake_store', region='eastus', resource_id=account.id,
            resource_name='a1', scope_id='my-rg', raw={'id': account.id, 'name': 'a1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        account = _account()
        account.as_dict.return_value = {'id': account.id, 'name': 'a1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.accounts.list.return_value = [account]
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_accounts_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.accounts.list.return_value = []
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_vnet_rule_gets_an_in_subnet_edge(self):
        w = MagicMock()
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        client = MagicMock()
        client.accounts.list.return_value = [account]
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='data_lake_store', from_id=account.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_no_vnet_rules_gets_no_edges(self):
        w = MagicMock()
        account = _account()
        client = MagicMock()
        client.accounts.list.return_value = [account]
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_account_gets_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        client = MagicMock()
        client.accounts.list.return_value = [account]
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
