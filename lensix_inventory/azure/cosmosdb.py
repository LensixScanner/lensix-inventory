"""Azure Cosmos DB gathering.

`database_accounts.list()` already returns everything needed for public-
network-access evaluation — that evaluation itself is left server-side.
Threat-protection status needs a per-account sub-call —
`SecurityCenter.advanced_threat_protection.get(resource_id=...)` — a plain
get call, so it's included too and merged into each account's raw record
as `_AdvancedThreatProtection`.
"""

from azure.mgmt.cosmosdb import CosmosDBManagementClient
from azure.mgmt.security import SecurityCenter
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_accounts(credential, subscription_id):
    cosmos = CosmosDBManagementClient(credential, subscription_id)
    return list(cosmos.database_accounts.list())


def get_advanced_threat_protection(credential, subscription_id, resource_id):
    sc = SecurityCenter(credential, subscription_id)
    try:
        return _as_dict(sc.advanced_threat_protection.get(resource_id=resource_id))
    except Exception:
        return None


def gather(credential, subscription_id, writer):
    for account in get_accounts(credential, subscription_id):
        raw = _as_dict(account)
        raw['_AdvancedThreatProtection'] = get_advanced_threat_protection(
            credential, subscription_id, account.id
        )

        writer.add_resource(
            resource_type='cosmosdb_account',
            region=account.location or 'global',
            resource_id=account.id,
            resource_name=account.name,
            scope_id=_resource_group(account.id),
            raw=raw,
        )
