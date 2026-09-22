"""Network Watcher gathering — watchers and their NSG flow logs.

Only the data-fetching calls are included here (network_watchers.list_all,
flow_logs.list) — flow-log retention-threshold evaluation is left
server-side.

Edges: flow_log -> storage_account (exports_to) via `storage_id`
(already embedded, no extra call); flow_log -> {nsg|virtual_network|
subnet} (monitors) via `target_resource_id` — flow logs can target any
of the three depending on API version/scope, so the target TYPE is
sniffed from its own ARM path segment (".../networkSecurityGroups/...",
".../virtualNetworks/.../subnets/...", or ".../virtualNetworks/..."
alone) rather than assumed to always be an NSG. Both emitted as read —
each endpoint is owned by a different module's own gather() (storage.py,
nsg.py, network.py), separate modules/containers (see network.py's own
docstring for this general pattern).

Requires: azure-mgmt-network.
"""

import re

from ._util import resource_group as _resource_group

_TARGET_TYPE_PATTERNS = (
    (re.compile(r'/subnets/[^/]+$', re.IGNORECASE), 'subnet'),
    (re.compile(r'/networkSecurityGroups/[^/]+$', re.IGNORECASE), 'nsg'),
    (re.compile(r'/virtualNetworks/[^/]+$', re.IGNORECASE), 'virtual_network'),
)


def _target_resource_type(target_resource_id):
    for pattern, resource_type in _TARGET_TYPE_PATTERNS:
        if pattern.search(target_resource_id):
            return resource_type
    return None

def get_network_watchers(credential, subscription_id):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.network_watchers.list_all())


def get_flow_logs(credential, subscription_id, rg, watcher_name):
    from azure.mgmt.network import NetworkManagementClient
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.flow_logs.list(rg, watcher_name))


def gather(credential, subscription_id, writer):
    try:
        watchers = get_network_watchers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='networkwatcher:watchers', message=e)
        return

    for watcher in watchers:
        region = watcher.location or 'global'
        rg = _resource_group(watcher.id)
        watcher_raw = watcher.as_dict()
        writer.add_resource(
            resource_type='network_watcher',
            region=region,
            resource_id=watcher.id,
            resource_name=watcher.name,
            scope_id=rg,
            raw=watcher_raw,
            tags=watcher_raw.get('tags'),
        )

        try:
            flow_logs = get_flow_logs(credential, subscription_id, rg, watcher.name)
        except Exception as e:
            writer.add_error(region=region, source=f'networkwatcher:flow_logs:{watcher.name}', message=e)
            continue

        for fl in flow_logs:
            fl_raw = fl.as_dict()
            added = writer.add_resource(
                resource_type='flow_log',
                region=region,
                resource_id=fl.id,
                resource_name=fl.name,
                scope_id=rg,
                raw=fl_raw,
                tags=fl_raw.get('tags'),
            )
            if added:
                storage_id = fl_raw.get('storage_id')
                if storage_id:
                    writer.add_edge(from_type='flow_log', from_id=fl.id, to_type='storage_account', to_id=storage_id, relationship='exports_to')
                target_resource_id = fl_raw.get('target_resource_id')
                if target_resource_id:
                    target_type = _target_resource_type(target_resource_id)
                    if target_type:
                        writer.add_edge(from_type='flow_log', from_id=fl.id, to_type=target_type, to_id=target_resource_id, relationship='monitors')
