"""Recovery Services Vault gathering.

Only the data-fetching calls are included here
(vaults.list_by_subscription_id, diagnostic_settings.list) — missing-
customer-managed-key/BYOK and missing-diagnostic-settings evaluation is
left server-side.

Requires: azure-mgmt-recoveryservices, azure-mgmt-monitor.
"""

from ._util import resource_group as _resource_group

def get_vaults(credential, subscription_id):
    """Pre-existing bug fixed here: `VaultsOperations` (azure-mgmt-
    recoveryservices 3.1.0, the version pinned by this project) exposes
    `list_by_subscription_id()`, not `list_by_subscription()` — confirmed
    directly against the installed SDK (`dir(VaultsOperations)`). The old
    name doesn't exist on the real client at all, so every call here would
    raise AttributeError against a live Azure subscription — meaning
    rsv_nobyok/rsv_nologging (which this gather already fed) and the newer
    vm_nobackup Azure-Backup-vault-policy check (which now also depends on
    this same vault list, see vm.py's own gather()) have never actually
    been able to enumerate a single real vault. Existing tests mocked the
    client directly, so this never surfaced there either."""
    from azure.mgmt.recoveryservices import RecoveryServicesClient
    rsv_client = RecoveryServicesClient(credential, subscription_id)
    return list(rsv_client.vaults.list_by_subscription_id())


def get_diagnostic_settings(monitor_client, resource_uri):
    try:
        return [s.as_dict() for s in monitor_client.diagnostic_settings.list(resource_uri)]
    except Exception:
        return []


def get_protected_vm_resource_ids(credential, subscription_id, vaults):
    """Every VM's own ARM resource ID (lowercased, for case-insensitive
    comparison) that is protected by ANY of the given already-gathered
    Recovery Services vaults — one
    RecoveryServicesBackupClient(...).backup_protected_items.list(
    vault_name, resource_group_name) call per vault (verified signature:
    list(vault_name, resource_group_name, *, filter=None, **kwargs) ->
    ItemPaged[ProtectedItemResource], confirmed against the installed
    azure-mgmt-recoveryservicesbackup 11.0.0), unioned into one set. Cost
    is bounded by vault count, not VM count — one extra live call per
    vault, matching this codebase's established "per-management-object,
    not per-leaf-resource" cost discipline (e.g. autoscaling.py's own
    get_scheduled_actions in the AWS side of this project).

    Reads each protected item's `.properties.source_resource_id`
    (verified field on the ProtectedItem base class — and its
    AzureIaaSComputeVMProtectedItem subclass in particular — via the
    installed SDK's own model source: "ARM ID of the resource to be
    backed up"). Items for other workload types this same vault might
    also protect (SQL, file shares, ...) simply carry a
    source_resource_id that will never match a VM's own id, so no
    filtering by item/workload type is needed — set membership by ARN
    alone is correct, same principle as AWS Backup's own
    list_protected_resources() precedent in this codebase.

    A vault whose own resource group can't be parsed from its ARM id is
    skipped (nothing to call list() with). Raises on any per-vault lookup
    failure rather than swallowing it — vm.py's own gather() isolates the
    whole call in its own try/except, same discipline as every other
    cross-module lookup in this codebase; a partial per-vault failure
    here would otherwise silently under-report protection for every VM,
    indistinguishable from a genuinely unprotected one."""
    from azure.mgmt.recoveryservicesbackup import RecoveryServicesBackupClient
    backup_client = RecoveryServicesBackupClient(credential, subscription_id)
    protected_ids = set()
    for vault in vaults:
        resource_group_name = _resource_group(vault.id)
        if not resource_group_name:
            continue
        for item in backup_client.backup_protected_items.list(vault.name, resource_group_name):
            props = getattr(item, 'properties', None)
            source_resource_id = getattr(props, 'source_resource_id', None) if props else None
            if source_resource_id:
                protected_ids.add(source_resource_id.lower())
    return protected_ids


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
            tags=raw.get('tags'),
        )
