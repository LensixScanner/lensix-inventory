"""Azure SQL Database gathering — SQL servers and their firewall rules.

Only the data-fetching calls are included here (servers.list,
server_security_alert_policies.get, server_blob_auditing_policies.get,
firewall_rules.list_by_server) — minimum-TLS-version, public-network-
access, missing-alert-emails, auditing-disabled, threat-protection-
disabled, and public-firewall-rule evaluation is left server-side.

The security alert policy and blob auditing policy are per-server
sub-API calls (much like S3's per-bucket fan-out in `s3.py`) merged into
each server's raw record as `_SecurityAlertPolicy`/`_AuditingPolicy` rather
than evaluated inline. Firewall rules get their own resource type
(`sql_server_firewall_rule`) since public-firewall-rule findings are
reported against individual rules by their own ARM id, not just against
the parent server.

Requires: azure-mgmt-sql.
"""

from ._util import resource_group as _resource_group

def get_servers(credential, subscription_id):
    from azure.mgmt.sql import SqlManagementClient
    sql_client = SqlManagementClient(credential, subscription_id)
    return list(sql_client.servers.list())


def get_security_alert_policy(credential, subscription_id, rg, server_name):
    from azure.mgmt.sql import SqlManagementClient
    sql_client = SqlManagementClient(credential, subscription_id)
    try:
        return sql_client.server_security_alert_policies.get(rg, server_name).as_dict()
    except Exception:
        return None


def get_auditing_policy(credential, subscription_id, rg, server_name):
    from azure.mgmt.sql import SqlManagementClient
    sql_client = SqlManagementClient(credential, subscription_id)
    try:
        return sql_client.server_blob_auditing_policies.get(rg, server_name).as_dict()
    except Exception:
        return None


def get_firewall_rules(credential, subscription_id, rg, server_name):
    from azure.mgmt.sql import SqlManagementClient
    sql_client = SqlManagementClient(credential, subscription_id)
    return list(sql_client.firewall_rules.list_by_server(rg, server_name))


def gather(credential, subscription_id, writer):
    try:
        servers = get_servers(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='sql:servers', message=e)
        return

    for server in servers:
        region = server.location or 'global'
        rg = _resource_group(server.id)

        raw = server.as_dict()
        raw['_SecurityAlertPolicy'] = get_security_alert_policy(credential, subscription_id, rg, server.name)
        raw['_AuditingPolicy'] = get_auditing_policy(credential, subscription_id, rg, server.name)
        server_tags = raw.get('tags')
        writer.add_resource(
            resource_type='sql_server',
            region=region,
            resource_id=server.id,
            resource_name=server.name,
            scope_id=rg,
            raw=raw,
            tags=server_tags,
        )

        try:
            rules = get_firewall_rules(credential, subscription_id, rg, server.name)
        except Exception as e:
            writer.add_error(region=region, source=f'sql:firewall_rules:{server.name}', message=e)
            continue

        for rule in rules:
            # FirewallRule has no `tags` field of its own (the SDK model
            # rejects it entirely) — it inherits the parent server's own
            # tags instead, same pattern as azure.network's vnet_peering.
            writer.add_resource(
                resource_type='sql_server_firewall_rule',
                region=region,
                resource_id=rule.id,
                resource_name=rule.name,
                scope_id=rg,
                raw=rule.as_dict(),
                tags=server_tags,
            )
