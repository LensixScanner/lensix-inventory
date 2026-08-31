"""Network Watcher gathering — watchers and their NSG flow logs.

Only the data-fetching calls are included here (network_watchers.list_all,
flow_logs.list) — flow-log retention-threshold evaluation is left
server-side.

Requires: azure-mgmt-network.
"""

from ._util import resource_group as _resource_group

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
            writer.add_resource(
                resource_type='flow_log',
                region=region,
                resource_id=fl.id,
                resource_name=fl.name,
                scope_id=rg,
                raw=fl_raw,
                tags=fl_raw.get('tags'),
            )
