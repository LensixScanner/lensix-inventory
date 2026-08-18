"""Cloud SQL gathering — one raw record per instance.

The instances().list() call already returns everything every engine-
specific check needs in one shot (settings.ipConfiguration, settings.
backupConfiguration, settings.availabilityType, settings.storageAutoResize,
diskEncryptionConfiguration, settings.databaseFlags, serverCaCert, ...) —
no fan-out sub-API calls needed. SSL enforcement, public IP/access,
automated backups, failover, storage autoresize, CMEK, per-engine
database-flag evaluation (MySQL/PostgreSQL/SQL Server), and TLS
certificate expiry evaluation is left server-side, including engine-
specific dispatch (databaseVersion prefix MYSQL/POSTGRES/SQLSERVER), which
is finding-selection logic, not gathering.
"""

from googleapiclient import discovery

from . import _util


def get_instances(sqladmin, project_id):
    instances = []
    request = sqladmin.instances().list(project=project_id)
    while request is not None:
        resp = request.execute()
        instances.extend(resp.get('items', []))
        request = sqladmin.instances().list_next(previous_request=request, previous_response=resp)
    return instances


def gather(project_id, credentials, writer):
    sqladmin = discovery.build('sqladmin', 'v1', credentials=credentials)

    try:
        instances = get_instances(sqladmin, project_id)
    except Exception as e:
        writer.add_error(region='global', source='sql_instance', message=e)
        return

    for instance in instances:
        name = instance.get('name', '')
        region = instance.get('region', 'global')
        # Only present when the instance uses a private IP; public-only
        # instances have no VPC association.
        network = instance.get('settings', {}).get('ipConfiguration', {}).get('privateNetwork')

        writer.add_resource(
            resource_type='sql_instance',
            region=region,
            resource_id=name,
            resource_name=name,
            scope_id=_util.extract_network_name(network),
            raw=instance,
        )
