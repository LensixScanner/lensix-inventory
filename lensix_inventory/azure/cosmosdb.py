"""Azure Cosmos DB gathering.

`database_accounts.list()` already returns everything needed for public-
network-access evaluation — that evaluation itself is left server-side.
Threat-protection status needs a per-account sub-call —
`SecurityCenter.advanced_threat_protection.get(resource_id=...)` — a plain
get call, so it's included too and merged into each account's raw record
as `_AdvancedThreatProtection`.

Edges: cosmosdb_account -> subnet (in_subnet), one per
virtual_network_rules[] entry — already embedded in the account's own
list() response (unlike sql.py's own VNet rules, no extra API call is
needed here; VirtualNetworkRule.id here IS the subnet's own ARM id
directly, not a nested SubResource). private_endpoint_connections[] has
no persisted target to join to (nothing in this codebase gathers
`private_endpoint` as its own resource type), so that's skipped, same
reasoning as appservice.py's own app_service_plan note. Emitted as read —
the subnet endpoint is owned by network.py's own gather(), a separate
module/container (see network.py's own docstring for this pattern).
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

        added = writer.add_resource(
            resource_type='cosmosdb_account',
            region=account.location or 'global',
            resource_id=account.id,
            resource_name=account.name,
            scope_id=_resource_group(account.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            for vnet_rule in (raw.get('virtual_network_rules') or []):
                subnet_id = vnet_rule.get('id')
                if subnet_id:
                    writer.add_edge(from_type='cosmosdb_account', from_id=account.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
