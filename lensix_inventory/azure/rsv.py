"""Recovery Services Vault gathering.

Only the data-fetching calls are included here
(vaults.list_by_subscription, diagnostic_settings.list) — missing-
customer-managed-key/BYOK and missing-diagnostic-settings evaluation is
left server-side.

Requires: azure-mgmt-recoveryservices, azure-mgmt-monitor.
"""

from ._util import resource_group as _resource_group

def get_vaults(credential, subscription_id):
    from azure.mgmt.recoveryservices import RecoveryServicesClient
    rsv_client = RecoveryServicesClient(credential, subscription_id)
    return list(rsv_client.vaults.list_by_subscription())


def get_diagnostic_settings(monitor_client, resource_uri):
    try:
        return [s.as_dict() for s in monitor_client.diagnostic_settings.list(resource_uri)]
    except Exception:
        return []


def gather(credential, subscription_id, writer):
    from azure.mgmt.monitor import MonitorManagementClient

    monitor_client = MonitorManagementClient(credential, subscription_id)

    try:
        vaults = get_vaults(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='rsv:vaults', message=e)
        return

    for vault in vaults:
        region = vault.location or 'global'
        raw = vault.as_dict()
        raw['_DiagnosticSettings'] = get_diagnostic_settings(monitor_client, vault.id)
        writer.add_resource(
            resource_type='recovery_services_vault',
            region=region,
            resource_id=vault.id,
            resource_name=vault.name,
            scope_id=_resource_group(vault.id),
            raw=raw,
        )
