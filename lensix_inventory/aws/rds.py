"""RDS gathering — DB instances and DB clusters.

describe_db_instances / describe_db_clusters already return everything most
evaluation needs in one shot; the exception is TLS-enforcement, which
(like S3's per-bucket sub-API calls) needs its own describe_db_parameters
call per instance to read the force_ssl/require_secure_transport
parameter — that raw parameter value is folded into each instance's
record here (`_SSLParameter`) the same way s3.py merges its sub-calls.

`rds_instance`, `rds_cluster`, and `rds_snapshot` (manual snapshots only —
automated snapshots aren't independently actionable the way a customer-
created manual one is) are gathered as resources.

7-day CloudWatch `DatabaseConnections` datapoints ARE gathered now
(get_db_connections_7d), merged into each 'available' instance's
raw['_ConnectionDatapoints'] — a point-in-time snapshot of a rolling
window, same "reasonably fresh, not live" treatment ec2.py's own
_Metrics gets (see its docstring). Only fetched for 'available' instances
— a stopped/creating/etc. instance has nothing meaningful in CloudWatch.

The current-published major-version list per engine (describe_db_engine_
versions) IS gathered now too (get_latest_major_versions), merged into
each instance's raw['_LatestMajorVersions'] — fetched once per distinct
engine actually present among this region's instances, not once per
instance, since it's a per-engine (not per-instance) fact. Still NOT
replicated: `get_vcpus`/`extended_support_cost_per_hour` (pricing-table
lookups against Lensix's own `instance_sizes` table, not an AWS API call
at all).

Whether each instance is centrally protected by AWS Backup (one region-
wide backup.get_protected_resource_arns() call, isolated in its own
try/except — see gather()'s own comment) is merged in as
raw['_ProtectedByAwsBackup'], keyed by the instance's own DBInstanceArn
(already present on the raw describe_db_instances record — not the short
DBInstanceIdentifier used as resource_id). Unlike the None-on-failure
"unknown" fields above, a lookup failure here stamps False on every
instance rather than None — this field only ever suppresses a finding
that would otherwise fire (rds_backupretention/rds_noautomatedbackups),
so failing toward False means failing toward still firing, not toward
silently hiding a real problem. See lensix_inventory.aws.backup's own
docstring for the cross-service rationale.
"""

from datetime import datetime, timedelta, timezone

import boto3

from .backup import get_protected_resource_arns

CONNECTIONS_LOOKBACK_DAYS = 7


def get_latest_major_versions(region, engine):
    rds = boto3.client('rds', region_name=region)
    versions = []
    for page in rds.get_paginator('describe_db_engine_versions').paginate(Engine=engine):
        versions.extend(page['DBEngineVersions'])
    return sorted({int(v['EngineVersion'].split('.')[0]) for v in versions})


def get_db_connections_7d(region, identifier):
    cw  = boto3.client('cloudwatch', region_name=region)
    end = datetime.now(timezone.utc)
    resp = cw.get_metric_statistics(
        Namespace='AWS/RDS',
        MetricName='DatabaseConnections',
        Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': identifier}],
        StartTime=end - timedelta(days=CONNECTIONS_LOOKBACK_DAYS),
        EndTime=end,
        Period=86400,
        Statistics=['Maximum'],
    )
    return resp['Datapoints']


def get_db_instances(region):
    rds = boto3.client('rds', region_name=region)
    instances = []
    for page in rds.get_paginator('describe_db_instances').paginate():
        instances.extend(page['DBInstances'])
    return instances


def get_db_clusters(region):
    rds = boto3.client('rds', region_name=region)
    clusters = []
    for page in rds.get_paginator('describe_db_clusters').paginate():
        clusters.extend(page['DBClusters'])
    return clusters


def get_manual_db_snapshots(region):
    rds = boto3.client('rds', region_name=region)
    snapshots = []
    for page in rds.get_paginator('describe_db_snapshots').paginate(SnapshotType='manual'):
        snapshots.extend(page['DBSnapshots'])
    return snapshots


def get_ssl_parameter(region, instance):
    """Returns the raw force_ssl/require_secure_transport parameter (or None
    if not found/not applicable), not a computed true/false determination."""
    engine = instance.get('Engine', '')
    pg_name = (instance.get('DBParameterGroups') or [{}])[0].get('DBParameterGroupName')
    if not pg_name:
        return None
    param_name = 'rds.force_ssl' if 'postgres' in engine else 'require_secure_transport'
    try:
        rds = boto3.client('rds', region_name=region)
        resp = rds.describe_db_parameters(
            DBParameterGroupName=pg_name,
            Filters=[{'Name': 'parameter-name', 'Values': [param_name]}],
        )
        for p in resp.get('Parameters', []):
            if p['ParameterName'] == param_name:
                return p
    except Exception:
        return None
    return None


def gather(region, writer):
    # Instances and clusters are independent describe calls — isolate
    # the clusters fetch so its failure doesn't discard the
    # already-gathered instances.
    instances = get_db_instances(region)

    # Latest-major-version lookup is per-engine, not per-instance — fetch
    # each distinct engine actually present exactly once, isolated so one
    # engine's failure doesn't blank out the others.
    majors_by_engine = {}
    for engine in {i.get('Engine') for i in instances if i.get('Engine')}:
        try:
            majors_by_engine[engine] = get_latest_major_versions(region, engine)
        except Exception as e:
            majors_by_engine[engine] = None
            writer.add_error(region=region, source=f'rds (latest engine versions:{engine})', message=e)

    # AWS Backup protection is a region-wide fact, not a per-instance one
    # — one paginated call answers it for every instance at once. Isolated
    # so a failure here degrades every instance's _ProtectedByAwsBackup to
    # False (still fires the static check) rather than aborting the
    # instance gather.
    try:
        protected_arns = get_protected_resource_arns(region)
    except Exception as e:
        protected_arns = None
        writer.add_error(region=region, source='rds (aws backup protected resources)', message=e)

    for instance in instances:
        iid = instance['DBInstanceIdentifier']
        raw = dict(instance)
        raw['_SSLParameter'] = get_ssl_parameter(region, instance)
        raw['_LatestMajorVersions'] = majors_by_engine.get(instance.get('Engine'))
        raw['_ProtectedByAwsBackup'] = (
            False if protected_arns is None else instance.get('DBInstanceArn') in protected_arns
        )
        if instance.get('DBInstanceStatus') == 'available':
            try:
                raw['_ConnectionDatapoints'] = get_db_connections_7d(region, iid)
            except Exception as e:
                raw['_ConnectionDatapoints'] = None
                writer.add_error(region=region, source=f'rds (connections:{iid})', message=e)
        else:
            raw['_ConnectionDatapoints'] = None
        writer.add_resource(
            resource_type='rds_instance',
            region=region,
            resource_id=iid,
            resource_name=iid,
            # DBCluster's DBSubnetGroup is just a name (no VpcId without an
            # extra describe_db_subnet_groups call) — instances embed the
            # full subnet group object, so only instances get scope_id.
            scope_id=instance.get('DBSubnetGroup', {}).get('VpcId'),
            raw=raw,
            # RDS uses TagList, not Tags, across every describe_db_* API —
            # a genuine RDS API naming quirk, not a typo (same kind of
            # inconsistency as EC2 ENIs' own TagSet).
            tags=instance.get('TagList'),
        )

    try:
        for cluster in get_db_clusters(region):
            cid = cluster['DBClusterIdentifier']
            writer.add_resource(
                resource_type='rds_cluster',
                region=region,
                resource_id=cid,
                resource_name=cid,
                raw=cluster,
                tags=cluster.get('TagList'),
            )
    except Exception as e:
        writer.add_error(region=region, source='rds (clusters)', message=e)

    try:
        for snap in get_manual_db_snapshots(region):
            snap_id = snap['DBSnapshotIdentifier']
            writer.add_resource(
                resource_type='rds_snapshot',
                region=region,
                resource_id=snap_id,
                resource_name=snap_id,
                raw=snap,
                tags=snap.get('TagList'),
            )
    except Exception as e:
        writer.add_error(region=region, source='rds (snapshots)', message=e)
