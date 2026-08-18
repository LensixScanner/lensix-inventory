"""Azure Data Lake Store gathering.

`accounts.list()` already returns everything needed for encryption
evaluation (encryption_config, encryption_state) — that evaluation itself
is left server-side. Gathered here as `data_lake_store` resources.
"""

from azure.mgmt.datalake.store import DataLakeStoreAccountManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_accounts(credential, subscription_id):
    client = DataLakeStoreAccountManagementClient(credential, subscription_id)
    return list(client.accounts.list())


def gather(credential, subscription_id, writer):
    for account in get_accounts(credential, subscription_id):
        writer.add_resource(
            resource_type='data_lake_store',
            region=account.location or 'global',
            resource_id=account.id,
            resource_name=account.name,
            scope_id=_resource_group(account.id),
            raw=_as_dict(account),
        )
