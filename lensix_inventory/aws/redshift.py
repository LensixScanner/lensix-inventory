"""Redshift gathering — clusters.

describe_clusters returns most of what evaluation needs directly, but
logging and TLS status each need their own sub-API call per cluster the
same way S3's checks do — describe_logging_status, and
describe_cluster_parameters per parameter group — so both are folded into
each cluster's raw record here (`_LoggingStatus`, `_SSLParameters`)
instead of being re-derived as booleans, matching s3.py's fused fan-out
pattern.

Whether each cluster is centrally protected by AWS Backup (one region-
wide backup.get_protected_resource_arns() call, isolated in its own
try/except — see gather()'s own comment) is folded in as
raw['_ProtectedByAwsBackup'], keyed by the cluster's own
ClusterNamespaceArn (already used as resource_id). A lookup failure
stamps False rather than None on every cluster — this field only ever
suppresses a finding that would otherwise fire
(redshift_nosnapshotretention), so failing toward False means failing
toward still firing rather than hiding a real problem. See
lensix_inventory.aws.backup's own docstring for the cross-service
rationale.
"""

import boto3

from .backup import get_protected_resource_arns


def get_clusters(region):
    rs = boto3.client('redshift', region_name=region)
    clusters = []
    for page in rs.get_paginator('describe_clusters').paginate():
        clusters.extend(page.get('Clusters', []))
    return clusters


def get_logging_status(region, cluster_id):
    rs = boto3.client('redshift', region_name=region)
    try:
        return rs.describe_logging_status(ClusterIdentifier=cluster_id)
    except Exception:
        return None


def get_ssl_parameters(region, param_group_name):
    """Returns the raw require_ssl parameter (or None if not found), not a
    computed true/false determination."""
    rs = boto3.client('redshift', region_name=region)
    try:
        for page in rs.get_paginator('describe_cluster_parameters').paginate(ParameterGroupName=param_group_name):
            for param in page.get('Parameters', []):
                if param.get('ParameterName') == 'require_ssl':
                    return param
    except Exception:
        return None
    return None


def gather(region, writer):
    # AWS Backup protection is a region-wide fact, not a per-cluster one —
    # one paginated call answers it for every cluster at once. Isolated so
    # a failure here degrades every cluster's _ProtectedByAwsBackup to
    # False (still fires the static check) rather than aborting the
    # cluster gather.
    try:
        protected_arns = get_protected_resource_arns(region)
    except Exception as e:
        protected_arns = None
        writer.add_error(region=region, source='redshift (aws backup protected resources)', message=e)

    for cluster in get_clusters(region):
        cid = cluster.get('ClusterIdentifier', '')
        resource_id = cluster.get('ClusterNamespaceArn') or cid

        raw = dict(cluster)
        raw['_LoggingStatus'] = get_logging_status(region, cid)

        ssl_params = {}
        for pg in cluster.get('ClusterParameterGroups', []):
            pg_name = pg.get('ParameterGroupName', '')
            if pg_name:
                ssl_params[pg_name] = get_ssl_parameters(region, pg_name)
        raw['_SSLParameters'] = ssl_params
        raw['_ProtectedByAwsBackup'] = (
            False if protected_arns is None else cluster.get('ClusterNamespaceArn') in protected_arns
        )

        writer.add_resource(
            resource_type='redshift_cluster',
            region=region,
            resource_id=resource_id,
            resource_name=cid,
            scope_id=cluster.get('VpcId'),
            raw=raw,
            tags=cluster.get('Tags'),
        )
