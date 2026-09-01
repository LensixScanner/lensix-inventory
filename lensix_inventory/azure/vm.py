"""Virtual machine gathering — VMs, managed disks, snapshots, and VM Scale
Sets.

Only the data-fetching calls are included here (virtual_machines.list_all,
disks.list, snapshots.list, virtual_machine_scale_sets.list_all) — disk/
BYOK encryption, AD-auth/boot-diagnostics/backup/auto-update/guest-
diagnostics extension presence, availability set/zone membership,
password-auth-enabled, missing-NSG, unattached-disk, snapshot-age, and
scale-set auto-repair/auto-upgrade/empty/single-AZ evaluation is left
server-side.

Disks are gathered once via the account-wide `disks.list()` call (like
AWS's `ebs_volume` in `ebs.py`) rather than a per-VM `disks.get()` call for
the OS disk and each data disk — one full listing already contains every
disk's `disk_state`, `encryption_settings_collection`, and `encryption`
fields, and each VM's `storage_profile` still carries the disk name/
managed-disk-id references needed to correlate a VM to its disks
server-side.

NICs are NOT gathered here — `defender.py` already gathers every NIC in
the subscription in one `network_interfaces.list_all()` call (needed for
its own IP-forwarding evaluation), so this module would otherwise
duplicate the same `network_interface` resource type under the same
resource_id. Lensix can join a VM to its NICs server-side via each VM's
own `network_profile.network_interfaces[].id`.

**Secrets exception**: `os_profile.custom_data` (VM user-data, often
base64-encoded cloud-init) — and, for scale sets, the equivalent shared
`virtual_machine_profile.os_profile.custom_data` — is exactly the kind of
free-text field that can carry hardcoded credentials, same treatment as
`aws/ec2.py`'s `get_userdata_secret_hits`. It's decoded, scanned locally
with `common.secrets.scan_text_for_secrets`, and only the matched rule
names are kept as `secret_scan_hits`; the decoded (or raw, if decoding
fails) text is discarded immediately and never placed in `raw`.

The scrub happens on the *typed model object*, before `.as_dict()` is ever
called, not by reaching into `.as_dict()`'s output afterward: this SDK
generation's `.as_dict()` nests most fields under a top-level `properties`
key matching the ARM wire shape — `properties.osProfile.customData` — not
the flat `os_profile`/`custom_data` names a lookup on the *output* dict
would assume, while attribute access on the *object* itself
(`vm.os_profile`) works fine regardless. Assigning `None` to the attribute
is what actually clears it before serialization — a dict-style `.pop()` on
`os_profile` does not reliably propagate through these models.

Whether `custom_data` was set at all is also flagged as a separate signal,
even with zero secret matches (worth manual review) — preserved here as
`_has_custom_data`, a plain top-level boolean on the record (not nested
under `properties.osProfile`, for the same serialization-shape reason the
scrub itself can't rely on that path).

Three more governance-object awareness signals, each stamped on the raw
record so scanner-light's vm_import.py checks can consult real external
state instead of guessing from static config alone:

`_ProtectedByAzureBackup` (True/False/None) — every VM's own ARM resource
ID is checked against the union of every already-gathered Recovery
Services vault's protected-item list (rsv.py's own
get_protected_vm_resource_ids(), one backup_protected_items.list() call
per vault — bounded by vault count, not VM count) BEFORE the VM loop
below, then stamped on each VM. None means the whole Backup-service lookup
failed or there were no vaults to check against — isolated in its own
try/except so a Backup-service failure never aborts VM gather itself, same
discipline as every try/except in this module.

`_HasMaintenanceConfigurationAssignment` (True/False, left UNSET
entirely otherwise) — unlike every other governance-object lookup in this
module, azure-mgmt-maintenance exposes no subscription- or region-wide
"list all assignments" call; `configuration_assignments.list()` is
inherently a genuine per-VM live call. To bound the fan-out, it's made
ONLY for a VM that has already failed the static
`enable_automatic_updates is False` check (see
`_has_maintenance_configuration_assignment`'s own docstring for the full
reasoning) — a compliant VM never triggers this call at all.

`_HasScheduledAutoscale` (True/False/None) — every VMSS's own ARM
resource ID is checked against the set of Autoscale settings that have at
least one schedule-based (`recurrence`-bearing) profile, fetched ONCE
subscription-wide via `MonitorManagementClient.autoscale_settings.
list_by_subscription()` (zero required params) BEFORE the VMSS loop
below, then stamped on each VMSS. None means the lookup failed —
isolated in its own try/except, same "indeterminate, don't guess"
discipline as `_ProtectedByAzureBackup` and AWS's own `_HasScheduledAction`
(`lensix_inventory/aws/autoscaling.py`).

Requires: azure-mgmt-compute, azure-mgmt-recoveryservices,
azure-mgmt-recoveryservicesbackup, azure-mgmt-maintenance,
azure-mgmt-monitor.
"""

import base64

from ..common.secrets import scan_text_for_secrets
from ._util import resource_group as _resource_group
from . import rsv as _rsv

def get_virtual_machines(credential, subscription_id):
    from azure.mgmt.compute import ComputeManagementClient
    compute_client = ComputeManagementClient(credential, subscription_id)
    return list(compute_client.virtual_machines.list_all())


def get_disks(credential, subscription_id):
    from azure.mgmt.compute import ComputeManagementClient
    compute_client = ComputeManagementClient(credential, subscription_id)
    return list(compute_client.disks.list())


def get_snapshots(credential, subscription_id):
    from azure.mgmt.compute import ComputeManagementClient
    compute_client = ComputeManagementClient(credential, subscription_id)
    return list(compute_client.snapshots.list())


def get_scale_sets(credential, subscription_id):
    from azure.mgmt.compute import ComputeManagementClient
    compute_client = ComputeManagementClient(credential, subscription_id)
    return list(compute_client.virtual_machine_scale_sets.list_all())


def get_autoscale_settings(credential, subscription_id):
    """Every AutoscaleSettingResource in the subscription — one
    `MonitorManagementClient.autoscale_settings.list_by_subscription()`
    call (zero required params — verified against the installed
    azure-mgmt-monitor 6.0.2; uses the version-router
    `MonitorManagementClient.autoscale_settings` property rather than
    importing a specific `v2022_10_01` submodule directly, for
    forward-compatibility), not one call per VMSS — mirrors this
    codebase's own AWS `get_scheduled_actions` precedent
    (`lensix_inventory/aws/autoscaling.py`) exactly. Raises on failure —
    gather()'s own try/except isolates this, same discipline as every
    other cross-module lookup in this module."""
    from azure.mgmt.monitor import MonitorManagementClient
    monitor_client = MonitorManagementClient(credential, subscription_id)
    return list(monitor_client.autoscale_settings.list_by_subscription())


def _has_maintenance_configuration_assignment(credential, subscription_id, vm):
    """Whether ANY Maintenance Configuration is assigned to this specific
    VM, via `MaintenanceManagementClient.configuration_assignments.list(
    resource_group_name, provider_name, resource_type, resource_name)` —
    verified against the installed azure-mgmt-maintenance 2.1.0 as the
    ONLY way to check maintenance-configuration coverage: unlike every
    other governance-object lookup in this codebase (AWS Backup's
    list_protected_resources, this module's own get_autoscale_settings),
    no subscription- or region-wide "list all assignments" call exists in
    this SDK. That makes this a genuine per-VM live call rather than a
    per-management-object one.

    Cost mitigation (see gather()'s own call site): only ever invoked for
    a VM that has ALREADY failed the static `enable_automatic_updates is
    False` check — a compliant VM needs no second-guessing at all, so
    this bounds the fan-out to the (usually small) set of VMs the check
    would otherwise flag, not the whole fleet.

    Raises on failure rather than swallowing it — gather()'s own
    try/except per VM isolates this, and deliberately treats a failure
    here as "unknown, so don't suppress the finding" (see
    check_auto_update's own comment in scanner-light's vm_import.py for
    why this is the opposite fail-direction from the schedule-aware
    checks in this same batch: a missing/failed Update-Manager lookup is
    not itself evidence a VM IS covered, so it must not hide a real
    finding)."""
    from azure.mgmt.maintenance import MaintenanceManagementClient
    maintenance_client = MaintenanceManagementClient(credential, subscription_id)
    resource_group_name = _resource_group(vm.id)
    assignments = list(maintenance_client.configuration_assignments.list(
        resource_group_name, 'Microsoft.Compute', 'virtualMachines', vm.name,
    ))
    return bool(assignments)


def _redact_os_profile_userdata(os_profile):
    """Decodes os_profile.custom_data, scans it locally for secrets, then
    clears the attribute on the live object (so a subsequent .as_dict()
    call never serializes it, regardless of where that ends up nesting it)
    — the decoded text itself is discarded immediately and never
    returned/uploaded. Returns (secret_scan_hits, has_custom_data). Shared
    by both the per-VM os_profile and a scale set's shared
    virtual_machine_profile.os_profile — same field, same treatment."""
    if os_profile is None or os_profile.custom_data is None:
        return [], False
    try:
        decoded = base64.b64decode(os_profile.custom_data).decode('utf-8', errors='replace')
    except Exception:
        decoded = os_profile.custom_data
    hits = scan_text_for_secrets(decoded)
    os_profile.custom_data = None  # not .pop() — see module docstring
    return hits, True


def gather(credential, subscription_id, writer):
    try:
        vms = get_virtual_machines(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='vm:virtual_machines', message=e)
        vms = []

    # One subscription-wide Backup-vault/protected-item lookup for the
    # WHOLE vm loop below, not one per VM — bounded by vault count, not
    # VM count (see rsv.get_protected_vm_resource_ids' own docstring).
    # Isolated in its own try/except: a Backup-service failure (or a
    # subscription with zero vaults at all — that's not a failure, just
    # an empty set) must not abort VM gather itself. None means "unknown"
    # rather than "not protected" — every VM's own _ProtectedByAzureBackup
    # becomes None too, matching the "don't guess" discipline used
    # throughout this codebase for a failed governance-object lookup.
    try:
        vaults = _rsv.get_vaults(credential, subscription_id)
        protected_vm_ids = _rsv.get_protected_vm_resource_ids(credential, subscription_id, vaults)
    except Exception as e:
        writer.add_error(region='global', source='vm:backup_protected_items', message=e)
        protected_vm_ids = None

    for vm in vms:
        region = vm.location or 'global'

        # Must scrub before calling .as_dict() — see module docstring for why.
        hits, has_custom_data = _redact_os_profile_userdata(vm.os_profile)
        raw = vm.as_dict()
        raw['_has_custom_data'] = has_custom_data
        raw['_ProtectedByAzureBackup'] = (
            None if protected_vm_ids is None else (vm.id or '').lower() in protected_vm_ids
        )

        # Maintenance Configuration assignment: a genuine per-VM live
        # call (see _has_maintenance_configuration_assignment's own
        # docstring for why no cheaper subscription-wide lookup exists)
        # — made ONLY for a VM already failing the static
        # enable_automatic_updates check, to bound the fan-out to the
        # (usually small) set of VMs the check would otherwise flag.
        # Left entirely UNSET (not even None) for every other VM — zero
        # extra cost for the compliant majority. Isolated in its own
        # try/except per VM, same discipline as every other per-VM
        # lookup in this codebase — one VM's lookup failure must not
        # abort the rest of gather().
        wc = ((raw.get('os_profile') or {}).get('windows_configuration') or {})
        if wc.get('enable_automatic_updates') is False:
            try:
                raw['_HasMaintenanceConfigurationAssignment'] = _has_maintenance_configuration_assignment(
                    credential, subscription_id, vm,
                )
            except Exception as e:
                raw['_HasMaintenanceConfigurationAssignment'] = None
                writer.add_error(region=region, source='vm:maintenance_configuration_assignment',
                                  message=f"{vm.name}: {e}")

        writer.add_resource(
            resource_type='vm',
            region=region,
            resource_id=vm.id,
            resource_name=vm.name,
            scope_id=_resource_group(vm.id),
            raw=raw,
            secret_scan_hits=hits,
            tags=raw.get('tags'),
        )
        # NICs referenced by vm.network_profile are NOT fetched here — see
        # module docstring: defender.py already gathers every NIC in the
        # subscription via one network_interfaces.list_all() call, which
        # `vm_nonsg` and friends can join against server-side via each VM's
        # own network_profile.network_interfaces[].id.

    try:
        for disk in get_disks(credential, subscription_id):
            disk_raw = disk.as_dict()
            writer.add_resource(
                resource_type='disk',
                region=disk.location or 'global',
                resource_id=disk.id,
                resource_name=disk.name,
                scope_id=_resource_group(disk.id),
                raw=disk_raw,
                tags=disk_raw.get('tags'),
            )
    except Exception as e:
        writer.add_error(region='global', source='vm:disks', message=e)

    try:
        for snap in get_snapshots(credential, subscription_id):
            snap_raw = snap.as_dict()
            writer.add_resource(
                resource_type='snapshot',
                region=snap.location or 'global',
                resource_id=snap.id,
                resource_name=snap.name,
                scope_id=_resource_group(snap.id),
                raw=snap_raw,
                tags=snap_raw.get('tags'),
            )
    except Exception as e:
        writer.add_error(region='global', source='vm:snapshots', message=e)

    # One subscription-wide Autoscale-settings lookup for the WHOLE vmss
    # loop below, not one per VMSS — mirrors _HasScheduledAction's own
    # AWS structure exactly (lensix_inventory/aws/autoscaling.py).
    # Isolated in its own try/except: failure means "indeterminate" for
    # every VMSS (None), not "no schedule" (False) — same "don't guess"
    # discipline as _ProtectedByAzureBackup above.
    try:
        scheduled_vmss_ids = {
            s.target_resource_uri.lower()
            for s in get_autoscale_settings(credential, subscription_id)
            if s.target_resource_uri and any(p.recurrence for p in (s.profiles or []))
        }
    except Exception as e:
        writer.add_error(region='global', source='vm:autoscale_settings', message=e)
        scheduled_vmss_ids = None

    try:
        for vmss in get_scale_sets(credential, subscription_id):
            profile = vmss.virtual_machine_profile
            os_profile = profile.os_profile if profile is not None else None

            # Must scrub before calling .as_dict() — see module docstring for why.
            hits, has_custom_data = _redact_os_profile_userdata(os_profile)
            raw = vmss.as_dict()
            raw['_has_custom_data'] = has_custom_data
            raw['_HasScheduledAutoscale'] = (
                None if scheduled_vmss_ids is None else (vmss.id or '').lower() in scheduled_vmss_ids
            )

            writer.add_resource(
                resource_type='vmss',
                region=vmss.location or 'global',
                resource_id=vmss.id,
                resource_name=vmss.name,
                scope_id=_resource_group(vmss.id),
                raw=raw,
                secret_scan_hits=hits,
                tags=raw.get('tags'),
            )
    except Exception as e:
        writer.add_error(region='global', source='vm:scale_sets', message=e)
