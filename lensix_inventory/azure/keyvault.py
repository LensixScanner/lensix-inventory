"""Key Vault gathering — vaults, keys, secrets (properties only, never values),
and diagnostic settings.

Only the data-fetching calls are included here (vaults.list,
list_properties_of_keys, list_properties_of_secrets,
diagnostic_settings.list) — soft-delete/purge-protection state, network ACL
default-action, key/secret expiry, content-type presence, and diagnostics
presence evaluation is left server-side.

Key/secret VALUES are never read: `list_properties_of_keys`/
`list_properties_of_secrets` only ever return metadata (id, enabled,
timestamps, tags, content-type) — the cryptographic material and secret
value require a separate get-by-version call this tool never makes, so
there's nothing here that needs the secrets.py redaction treatment.

Requires: azure-mgmt-keyvault, azure-mgmt-monitor, azure-keyvault-keys,
azure-keyvault-secrets, azure-core.
"""

from ._util import resource_group as _resource_group

def get_vaults(credential, subscription_id):
    from azure.mgmt.keyvault import KeyVaultManagementClient
    kv_mgmt_client = KeyVaultManagementClient(credential, subscription_id)
    return list(kv_mgmt_client.vaults.list())


def get_keys(vault, credential):
    """Key metadata only (id, enabled, timestamps, tags) — never key material."""
    from azure.keyvault.keys import KeyClient
    key_client = KeyClient(vault_url=vault.properties.vault_uri, credential=credential)
    keys = []
    for k in key_client.list_properties_of_keys():
        keys.append({
            'id': k.id,
            'name': k.name,
            'enabled': k.enabled,
            'created_on': k.created_on,
            'updated_on': k.updated_on,
            'expires_on': k.expires_on,
            'not_before': k.not_before,
            'recovery_level': getattr(k, 'recovery_level', None),
            'tags': k.tags,
        })
    return keys


def get_secrets(vault, credential):
    """Secret metadata only (id, enabled, timestamps, content_type, tags) —
    never the secret value, which list_properties_of_secrets never returns
    anyway (a separate get-by-version call would be needed for that, and
    this tool never makes it)."""
    from azure.keyvault.secrets import SecretClient
    secret_client = SecretClient(vault_url=vault.properties.vault_uri, credential=credential)
    secrets = []
    for s in secret_client.list_properties_of_secrets():
        secrets.append({
            'id': s.id,
            'name': s.name,
            'enabled': s.enabled,
            'created_on': s.created_on,
            'updated_on': s.updated_on,
            'expires_on': s.expires_on,
            'not_before': s.not_before,
            'content_type': s.content_type,
            'tags': s.tags,
        })
    return secrets


def get_diagnostic_settings(monitor_client, resource_uri):
    try:
        return [s.as_dict() for s in monitor_client.diagnostic_settings.list(resource_uri=resource_uri)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    from azure.mgmt.monitor import MonitorManagementClient

    monitor_client = MonitorManagementClient(credential, subscription_id)

    try:
        vaults = get_vaults(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='keyvault:vaults', message=e)
        return

    for vault in vaults:
        region = vault.location or 'global'
        rg = _resource_group(vault.id)
        raw = vault.as_dict()
        raw['_DiagnosticSettings'] = get_diagnostic_settings(monitor_client, vault.id)
        writer.add_resource(
            resource_type='key_vault',
            region=region,
            resource_id=vault.id,
            resource_name=vault.name,
            scope_id=rg,
            raw=raw,
        )

        try:
            for key in get_keys(vault, credential):
                writer.add_resource(
                    resource_type='keyvault_key',
                    region=region,
                    resource_id=key['id'],
                    resource_name=key['name'],
                    scope_id=rg,
                    raw=key,
                )
        except Exception as e:
            writer.add_error(region=region, source=f'keyvault:keys:{vault.name}', message=e)

        try:
            for secret in get_secrets(vault, credential):
                writer.add_resource(
                    resource_type='keyvault_secret',
                    region=region,
                    resource_id=secret['id'],
                    resource_name=secret['name'],
                    scope_id=rg,
                    raw=secret,
                )
        except Exception as e:
            writer.add_error(region=region, source=f'keyvault:secrets:{vault.name}', message=e)
