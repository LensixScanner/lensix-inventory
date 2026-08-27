"""Unit tests for lensix_inventory.azure.datalake — Data Lake Store accounts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.datalake as m


def _account(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DataLakeStore/accounts/a1', name='a1'):
    acct = MagicMock()
    acct.location = location
    acct.id = rid
    acct.name = name
    acct.as_dict.return_value = {'id': rid, 'name': name}
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
        )

    def test_no_accounts_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.accounts.list.return_value = []
        with patch.object(m, 'DataLakeStoreAccountManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
