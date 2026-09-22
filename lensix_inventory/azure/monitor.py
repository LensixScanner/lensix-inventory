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

Edges: monitor_log_profile -> storage_account (exports_to), when the
profile has an export storage account configured (`storage_account_id` —
already embedded in the profile's own list() response). No edge for the
profile's own `service_bus_rule_id` — that's a Service Bus
AUTHORIZATION RULE sub-resource id, not the namespace servicebus.py
persists, and this legacy (largely superseded by diagnostic settings)
field is rare enough not to be worth the extra path-parsing to derive the
parent namespace id. Emitted as read — the storage_account endpoint is
owned by storage.py's own gather(), a separate module/container (see
network.py's own docstring for this general pattern).

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
        added = writer.add_resource(
            resource_type='monitor_log_profile',
            region='global',
            resource_id=profile.id,
            resource_name=profile.name,
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            storage_account_id = raw.get('storage_account_id')
            if storage_account_id:
                writer.add_edge(from_type='monitor_log_profile', from_id=profile.id, to_type='storage_account', to_id=storage_account_id, relationship='exports_to')
