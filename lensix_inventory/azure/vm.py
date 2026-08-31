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

Requires: azure-mgmt-compute.
"""

import base64

from ..common.secrets import scan_text_for_secrets
from ._util import resource_group as _resource_group

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

    for vm in vms:
        region = vm.location or 'global'

        # Must scrub before calling .as_dict() — see module docstring for why.
        hits, has_custom_data = _redact_os_profile_userdata(vm.os_profile)
        raw = vm.as_dict()
        raw['_has_custom_data'] = has_custom_data

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

    try:
        for vmss in get_scale_sets(credential, subscription_id):
            profile = vmss.virtual_machine_profile
            os_profile = profile.os_profile if profile is not None else None

            # Must scrub before calling .as_dict() — see module docstring for why.
            hits, has_custom_data = _redact_os_profile_userdata(os_profile)
            raw = vmss.as_dict()
            raw['_has_custom_data'] = has_custom_data

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
