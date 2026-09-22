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

from . import _util, vpc


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

    # privateNetwork's own documented example ('/projects/myProject/
    # global/networks/default') is a partial resource link, not
    # guaranteed byte-identical to the referenced vpc_network's own
    # selfLink (used as its resource_id) — resolved via vpc.py's own
    # exported name -> selfLink map instead (see compute.py's own gather()
    # comment for the full rationale). An accepted, disclosed duplicate of
    # the same list call vpc.py's own gather() already makes elsewhere in
    # the same scan, isolated so a resolution failure doesn't block
    # instance gathering itself.
    compute = discovery.build('compute', 'v1', credentials=credentials)
    try:
        network_id_by_name = vpc.get_network_selflink_by_name(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='sql_instance (network resolution)', message=e)
        network_id_by_name = {}

    for instance in instances:
        name = instance.get('name', '')
        region = instance.get('region', 'global')
        # Only present when the instance uses a private IP; public-only
        # instances have no VPC association.
        network = instance.get('settings', {}).get('ipConfiguration', {}).get('privateNetwork')

        recorded = writer.add_resource(
            resource_type='sql_instance',
            region=region,
            resource_id=name,
            resource_name=name,
            scope_id=_util.extract_network_name(network),
            raw=instance,
            # Cloud SQL's tags-equivalent field is settings.userLabels, not
            # a top-level `labels` key — same userLabels naming quirk as
            # Cloud Monitoring's AlertPolicy (see logging.py).
            tags=instance.get('settings', {}).get('userLabels'),
        )
        if not recorded:
            continue
        if network:
            network_id = network_id_by_name.get(_util.extract_network_name(network))
            if network_id:
                writer.add_edge(from_type='sql_instance', from_id=name, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
        # diskEncryptionConfiguration.kmsKeyName is always the
        # fully-qualified KMS resource name (same convention as every
        # other GCP CMEK field, confirmed against the real discovery
        # document schema) — matches kms_crypto_key's own resource_id
        # exactly, no name-based resolution needed.
        kms_key = (instance.get('diskEncryptionConfiguration') or {}).get('kmsKeyName')
        if kms_key:
            writer.add_edge(from_type='sql_instance', from_id=name, to_type='kms_crypto_key', to_id=kms_key, relationship='uses_cmek')
