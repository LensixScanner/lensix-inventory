"""Azure Database for MySQL gathering — Single Server and Flexible Server.

Only the data-fetching calls are included here (servers.list,
server_administrators.list, configurations.list_by_server, for both the
Single Server and Flexible Server APIs) — SSL enforcement, missing-AD-
admin, geo-redundant-backup, storage-autogrowth, audit-log/log-retention/
connection-throttling, and public-access evaluation is left server-side.

Server configuration parameters (`configurations.list_by_server`) is a
per-server sub-API fan-out much like S3's per-bucket calls in `s3.py` —
only a handful of named parameters (audit_log_enabled, log_retention_days,
max_connect_errors) out of the ~100+ returned actually matter for
evaluation, but for gathering purposes the full parameter set is merged
into the server's raw record as `_Configurations` rather than cherry-
picking, so Lensix can evaluate any config-based check server-side without
a second round trip. AAD admins are similarly merged in as
`_Administrators`.

Edges: mysql_server -> subnet (in_subnet), one per VNet service-endpoint
rule (`virtual_network_rules.list_by_server` — a genuinely new API call,
mirroring sql.py's own identical pattern, not derived from data already
gathered); mysql_flexible_server -> subnet (in_subnet), when the server
has VNet integration configured (`network.delegated_subnet_resource_id`
— already embedded in the flexible server's own list() response, unlike
Single Server, no extra call needed). Both emitted as read — the subnet
endpoint is owned by network.py's own gather(), a separate module/
container (see its own docstring for this pattern).

Requires: azure-mgmt-rdbms.
"""

from ._util import resource_group as _resource_group

def get_servers(credential, subscription_id):
    from azure.mgmt.rdbms.mysql import MySQLManagementClient
    mysql_client = MySQLManagementClient(credential, subscription_id)
    return list(mysql_client.servers.list())


def get_vnet_rules(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.mysql import MySQLManagementClient
    mysql_client = MySQLManagementClient(credential, subscription_id)
    return list(mysql_client.virtual_network_rules.list_by_server(rg, server_name))


def get_administrators(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.mysql import MySQLManagementClient
    mysql_client = MySQLManagementClient(credential, subscription_id)
    try:
        return [a.as_dict() for a in mysql_client.server_administrators.list(rg, server_name)]
    except Exception:
        return []


def get_configurations(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.mysql import MySQLManagementClient
    mysql_client = MySQLManagementClient(credential, subscription_id)
    try:
        return {c.name: (c.value or '') for c in mysql_client.configurations.list_by_server(rg, server_name)}
    except Exception:
        return {}


def get_flexible_servers(credential, subscription_id):
    try:
        from azure.mgmt.rdbms.mysql_flexibleservers import (
            MySQLManagementClient as FlexMySQLClient,
        )
    except ImportError:
        return []
    flex_client = FlexMySQLClient(credential, subscription_id)
    return list(flex_client.servers.list())


def get_flexible_configurations(credential, subscription_id, rg, server_name):
    try:
        from azure.mgmt.rdbms.mysql_flexibleservers import (
            MySQLManagementClient as FlexMySQLClient,
        )
    except ImportError:
        return {}
    flex_client = FlexMySQLClient(credential, subscription_id)
    try:
        return {c.name: (c.value or '') for c in flex_client.configurations.list_by_server(rg, server_name)}
    except Exception:
        return {}


def gather(credential, subscription_id, writer):
    try:
        servers = get_servers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='mysql:servers', message=e)
        servers = []

    for server in servers:
        region = server.location or 'global'
        rg = _resource_group(server.id)
        raw = server.as_dict()
        raw['_Administrators'] = get_administrators(credential, subscription_id, rg, server.name)
        raw['_Configurations'] = get_configurations(credential, subscription_id, rg, server.name)
        added = writer.add_resource(
            resource_type='mysql_server',
            region=region,
            resource_id=server.id,
            resource_name=server.name,
            scope_id=rg,
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            try:
                for vnet_rule in get_vnet_rules(credential, subscription_id, rg, server.name):
                    subnet_id = vnet_rule.virtual_network_subnet_id
                    if subnet_id:
                        writer.add_edge(from_type='mysql_server', from_id=server.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
            except Exception as e:
                writer.add_error(region=region, source=f'mysql:vnet_rules:{server.name}', message=e)

    try:
        flex_servers = get_flexible_servers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='mysql:flexible_servers', message=e)
        flex_servers = []

    for server in flex_servers:
        region = server.location or 'global'
        rg = _resource_group(server.id)
        raw = server.as_dict()
        raw['_Configurations'] = get_flexible_configurations(credential, subscription_id, rg, server.name)
        added = writer.add_resource(
            resource_type='mysql_flexible_server',
            region=region,
            resource_id=server.id,
            resource_name=server.name,
            scope_id=rg,
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            subnet_id = (raw.get('network') or {}).get('delegated_subnet_resource_id')
            if subnet_id:
                writer.add_edge(from_type='mysql_flexible_server', from_id=server.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
