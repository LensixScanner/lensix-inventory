"""Unit tests for lensix_inventory.azure.storage — Storage accounts and
blob containers.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (the service-properties sub-fetches' own isolation),
which was untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.storage as m


def _account(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Storage/storageAccounts/sa1', name='sa1',
             vnet_rule_subnet_ids=None):
    account = MagicMock()
    account.location = location
    account.id = rid
    account.name = name
    raw = {'id': rid, 'name': name}
    if vnet_rule_subnet_ids is not None:
        raw['network_rule_set'] = {'virtual_network_rules': [{'virtual_network_resource_id': sid} for sid in vnet_rule_subnet_ids]}
    account.as_dict.return_value = raw
    return account


def _storage_client(accounts, containers=None):
    client = MagicMock()
    client.storage_accounts.list.return_value = accounts
    client.blob_containers.list.return_value = containers or []
    client.blob_services.get_service_properties.side_effect = Exception('n/a')
    client.queue_services.get_service_properties.side_effect = Exception('n/a')
    client.file_services.get_service_properties.side_effect = Exception('n/a')
    return client


class TestGather:
    def test_adds_one_resource_per_account(self):
        w = MagicMock()
        account = _account()
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['storage_account'].kwargs['resource_id'] == account.id
        assert calls['storage_account'].kwargs['tags'] is None

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        account = _account()
        account.as_dict.return_value = {'id': account.id, 'name': 'sa1', 'tags': {'lensix-suppress': 'true'}}
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['storage_account'].kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_accounts_gathers_nothing(self):
        w = MagicMock()
        client = _storage_client([])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_vnet_rule_gets_an_in_subnet_edge(self):
        w = MagicMock()
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='storage_account', from_id=account.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_multiple_vnet_rules_each_get_their_own_edge(self):
        w = MagicMock()
        account = _account(vnet_rule_subnet_ids=['subnet-1', 'subnet-2'])
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        to_ids = {c.kwargs['to_id'] for c in w.add_edge.call_args_list}
        assert to_ids == {'subnet-1', 'subnet-2'}

    def test_no_vnet_rules_gets_no_edges(self):
        w = MagicMock()
        account = _account()
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_account_gets_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        account = _account(vnet_rule_subnet_ids=['subnet-1'])
        client = _storage_client([account])
        with patch('azure.mgmt.storage.StorageManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
