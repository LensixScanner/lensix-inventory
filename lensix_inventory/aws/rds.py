"""RDS gathering — DB instances and DB clusters.

describe_db_instances / describe_db_clusters already return everything most
evaluation needs in one shot; the exception is TLS-enforcement, which
(like S3's per-bucket sub-API calls) needs its own describe_db_parameters
call per instance to read the force_ssl/require_secure_transport
parameter — that raw parameter value is folded into each instance's
record here (`_SSLParameter`) the same way s3.py merges its sub-calls.

Only `rds_instance` and `rds_cluster` are gathered as resources — manual
snapshot encryption checking isn't a persisted resource, so there's no
resource shape to represent for `rds_snapshot`.

Not replicated (time-windowed telemetry or check-only helpers, not
inventory): `get_db_connections_7d` (CloudWatch DatabaseConnections over 7
days, used by the "unused instance" check), `get_latest_major_versions`
(used only to compute "how many majors behind," a finding-time
computation over data this tool doesn't need to gather since Lensix can
recompute it server-side from `EngineVersion` plus its own knowledge of
current engine versions), `get_vcpus`/`extended_support_cost_per_hour`
(pricing-table lookups against Lensix's own `instance_sizes` table, not
an AWS API call at all).
"""

import boto3


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
    instances = get_db_instances(region)
    for instance in instances:
        iid = instance['DBInstanceIdentifier']
        raw = dict(instance)
        raw['_SSLParameter'] = get_ssl_parameter(region, instance)
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
        )

    for cluster in get_db_clusters(region):
        cid = cluster['DBClusterIdentifier']
        writer.add_resource(
            resource_type='rds_cluster',
            region=region,
            resource_id=cid,
            resource_name=cid,
            raw=cluster,
        )
