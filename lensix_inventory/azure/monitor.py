"""Azure Monitor gathering — subscription-level activity log profiles.

Only the data-fetching call is included here (log_profiles.list) —
missing-profile, retention-period, missing-category, and missing-storage-
account evaluation is left server-side.

Checking "does this resource have any diagnostic settings" for NSGs, load
balancers, and Key Vaults is intentionally NOT duplicated here as a
separate re-listing of each resource type. Instead, `nsg.py`, `lb.py`, and
`keyvault.py` each merge their own resource's `diagnostic_settings.list()`
result in as `_DiagnosticSettings` when gathering that resource type, so
the data is fetched exactly once per resource rather than twice. CDN
diagnostic-settings data isn't gathered anywhere yet — a gap to close if/
when a `cdn.py` diagnostic fetch is added. Public-access signals for
storage accounts (`allow_blob_public_access`, `network_rule_set`) are
already present in `storage.py`'s raw `StorageAccount.as_dict()` record, so
no separate fetch is needed here either.

Despite the module name, everything here is resource/config listing, not
metric time-series querying — no time-windowed metrics calls to skip.

Requires: azure-mgmt-monitor.
"""


def get_log_profiles(credential, subscription_id):
    from azure.mgmt.monitor import MonitorManagementClient
    monitor_client = MonitorManagementClient(credential, subscription_id)
    return list(monitor_client.log_profiles.list())


def gather(credential, subscription_id, writer):
    try:
        profiles = get_log_profiles(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='monitor:log_profiles', message=e)
        return

    for profile in profiles:
        raw = profile.as_dict()
        writer.add_resource(
            resource_type='monitor_log_profile',
            region='global',
            resource_id=profile.id,
            resource_name=profile.name,
            raw=raw,
            tags=raw.get('tags'),
        )
