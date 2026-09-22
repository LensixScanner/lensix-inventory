"""Azure Data Lake Store gathering.

`accounts.list()` already returns everything needed for encryption
evaluation (encryption_config, encryption_state) — that evaluation itself
is left server-side. Gathered here as `data_lake_store` resources.

Edges: data_lake_store -> subnet (in_subnet), one per
virtual_network_rules[] entry — already embedded in the account's own
list() response, no extra call needed. Emitted as read — the subnet
endpoint is owned by network.py's own gather(), a separate module/
container (see its own docstring for this pattern).
"""

from azure.mgmt.datalake.store import DataLakeStoreAccountManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_accounts(credential, subscription_id):
    client = DataLakeStoreAccountManagementClient(credential, subscription_id)
    return list(client.accounts.list())


def gather(credential, subscription_id, writer):
    for account in get_accounts(credential, subscription_id):
        raw = _as_dict(account)
        added = writer.add_resource(
            resource_type='data_lake_store',
            region=account.location or 'global',
            resource_id=account.id,
            resource_name=account.name,
            scope_id=_resource_group(account.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for vnet_rule in (raw.get('virtual_network_rules') or []):
                subnet_id = vnet_rule.get('subnet_id')
                if subnet_id:
                    writer.add_edge(from_type='data_lake_store', from_id=account.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
