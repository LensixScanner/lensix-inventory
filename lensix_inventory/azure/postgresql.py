"""Azure Database for PostgreSQL gathering — Single Server and Flexible
Server.

Only the data-fetching calls are included here (servers.list,
server_administrators.list / administrators.list_by_server,
configurations.list_by_server, for both the Single Server and Flexible
Server APIs) — SSL enforcement, missing-AD-admin, geo-redundant-backup,
storage-autogrowth, checkpoint/connection/disconnection/duration logging,
log-retention, and connection-throttling evaluation is left server-side.

As with `mysql.py`, the full configuration parameter set is merged into
each server's raw record as `_Configurations` (rather than cherry-picking
the handful of named parameters evaluation actually needs) so Lensix can
evaluate any config-based check server-side without a second round trip.
AAD admins are similarly merged in as `_Administrators`.

Edges: postgresql_server -> subnet (in_subnet), one per VNet service-
endpoint rule (`virtual_network_rules.list_by_server` — a genuinely new
API call, mirroring mysql.py's/sql.py's own identical pattern, not
derived from data already gathered); postgresql_flexible_server -> subnet
(in_subnet), when the server has VNet integration configured
(`network.delegated_subnet_resource_id` — already embedded in the
flexible server's own list() response, no extra call needed). Both
emitted as read — the subnet endpoint is owned by network.py's own
gather(), a separate module/container (see its own docstring for this
pattern).

Requires: azure-mgmt-rdbms.
"""

from ._util import resource_group as _resource_group

def get_servers(credential, subscription_id):
    from azure.mgmt.rdbms.postgresql import PostgreSQLManagementClient
    pg_client = PostgreSQLManagementClient(credential, subscription_id)
    return list(pg_client.servers.list())


def get_vnet_rules(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.postgresql import PostgreSQLManagementClient
    pg_client = PostgreSQLManagementClient(credential, subscription_id)
    return list(pg_client.virtual_network_rules.list_by_server(rg, server_name))


def get_administrators(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.postgresql import PostgreSQLManagementClient
    pg_client = PostgreSQLManagementClient(credential, subscription_id)
    try:
        return [a.as_dict() for a in pg_client.server_administrators.list(rg, server_name)]
    except Exception:
        return []


def get_configurations(credential, subscription_id, rg, server_name):
    from azure.mgmt.rdbms.postgresql import PostgreSQLManagementClient
    pg_client = PostgreSQLManagementClient(credential, subscription_id)
    try:
        return {c.name: (c.value or '') for c in pg_client.configurations.list_by_server(rg, server_name)}
    except Exception:
        return {}


def get_flexible_servers(credential, subscription_id):
    try:
        from azure.mgmt.rdbms.postgresql_flexibleservers import (
            PostgreSQLManagementClient as FlexPGClient,
        )
    except ImportError:
        return []
    flex_client = FlexPGClient(credential, subscription_id)
    return list(flex_client.servers.list())


def get_flexible_administrators(credential, subscription_id, rg, server_name):
    try:
        from azure.mgmt.rdbms.postgresql_flexibleservers import (
            PostgreSQLManagementClient as FlexPGClient,
        )
    except ImportError:
        return []
    flex_client = FlexPGClient(credential, subscription_id)
    try:
        return [a.as_dict() for a in flex_client.administrators.list_by_server(rg, server_name)]
    except Exception:
        return []


def get_flexible_configurations(credential, subscription_id, rg, server_name):
    try:
        from azure.mgmt.rdbms.postgresql_flexibleservers import (
            PostgreSQLManagementClient as FlexPGClient,
        )
    except ImportError:
        return {}
    flex_client = FlexPGClient(credential, subscription_id)
    try:
        return {c.name: (c.value or '') for c in flex_client.configurations.list_by_server(rg, server_name)}
    except Exception:
        return {}


def gather(credential, subscription_id, writer):
    try:
        servers = get_servers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='postgresql:servers', message=e)
        servers = []

    for server in servers:
        region = server.location or 'global'
        rg = _resource_group(server.id)
        raw = server.as_dict()
        raw['_Administrators'] = get_administrators(credential, subscription_id, rg, server.name)
        raw['_Configurations'] = get_configurations(credential, subscription_id, rg, server.name)
        added = writer.add_resource(
            resource_type='postgresql_server',
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
                        writer.add_edge(from_type='postgresql_server', from_id=server.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
            except Exception as e:
                writer.add_error(region=region, source=f'postgresql:vnet_rules:{server.name}', message=e)

    try:
        flex_servers = get_flexible_servers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='postgresql:flexible_servers', message=e)
        flex_servers = []

    for server in flex_servers:
        region = server.location or 'global'
        rg = _resource_group(server.id)
        raw = server.as_dict()
        raw['_Administrators'] = get_flexible_administrators(credential, subscription_id, rg, server.name)
        raw['_Configurations'] = get_flexible_configurations(credential, subscription_id, rg, server.name)
        added = writer.add_resource(
            resource_type='postgresql_flexible_server',
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
                writer.add_edge(from_type='postgresql_flexible_server', from_id=server.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
