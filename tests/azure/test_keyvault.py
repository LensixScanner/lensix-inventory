"""Unit tests for lensix_inventory.azure.keyvault — Key Vaults, keys, secrets.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, not full
pre-existing behavior (get_keys/get_secrets' own isolation), which was
untested prior to this file too.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.keyvault as m


def _vault(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.KeyVault/vaults/v1', name='v1'):
    vault = MagicMock()
    vault.location = location
    vault.id = rid
    vault.name = name
    vault.properties.vault_uri = 'https://v1.vault.azure.net/'
    vault.as_dict.return_value = {'id': rid, 'name': name}
    return vault


def _clients():
    kv_mgmt = MagicMock()
    key_client = MagicMock()
    key_client.list_properties_of_keys.return_value = []
    secret_client = MagicMock()
    secret_client.list_properties_of_secrets.return_value = []
    monitor = MagicMock()
    monitor.diagnostic_settings.list.return_value = []
    return kv_mgmt, key_client, secret_client, monitor


class TestGather:
    def test_adds_one_resource_per_vault(self):
        w = MagicMock()
        vault = _vault()
        kv_mgmt, key_client, secret_client, monitor = _clients()
        kv_mgmt.vaults.list.return_value = [vault]
        with patch('azure.mgmt.keyvault.KeyVaultManagementClient', return_value=kv_mgmt), \
             patch('azure.keyvault.keys.KeyClient', return_value=key_client), \
             patch('azure.keyvault.secrets.SecretClient', return_value=secret_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='key_vault', region='eastus', resource_id=vault.id,
            resource_name='v1', scope_id='my-rg',
            raw={'id': vault.id, 'name': 'v1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        vault = _vault()
        vault.as_dict.return_value = {'id': vault.id, 'name': 'v1', 'tags': {'lensix-suppress': 'true'}}
        kv_mgmt, key_client, secret_client, monitor = _clients()
        kv_mgmt.vaults.list.return_value = [vault]
        with patch('azure.mgmt.keyvault.KeyVaultManagementClient', return_value=kv_mgmt), \
             patch('azure.keyvault.keys.KeyClient', return_value=key_client), \
             patch('azure.keyvault.secrets.SecretClient', return_value=secret_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_key_and_secret_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        vault = _vault()
        kv_mgmt, key_client, secret_client, monitor = _clients()
        kv_mgmt.vaults.list.return_value = [vault]
        key = MagicMock(id='key-id', name='k1', enabled=True, created_on=None, updated_on=None,
                         expires_on=None, not_before=None, tags={'lensix-suppress': 'true'})
        secret = MagicMock(id='secret-id', name='s1', enabled=True, created_on=None, updated_on=None,
                            expires_on=None, not_before=None, content_type=None, tags={'lensix-suppress-checks': 'x'})
        key_client.list_properties_of_keys.return_value = [key]
        secret_client.list_properties_of_secrets.return_value = [secret]
        with patch('azure.mgmt.keyvault.KeyVaultManagementClient', return_value=kv_mgmt), \
             patch('azure.keyvault.keys.KeyClient', return_value=key_client), \
             patch('azure.keyvault.secrets.SecretClient', return_value=secret_client), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        calls_by_type = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls_by_type['keyvault_key'].kwargs['tags'] == {'lensix-suppress': 'true'}
        assert calls_by_type['keyvault_secret'].kwargs['tags'] == {'lensix-suppress-checks': 'x'}

    def test_no_vaults_gathers_nothing(self):
        w = MagicMock()
        kv_mgmt, key_client, secret_client, monitor = _clients()
        kv_mgmt.vaults.list.return_value = []
        with patch('azure.mgmt.keyvault.KeyVaultManagementClient', return_value=kv_mgmt):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
