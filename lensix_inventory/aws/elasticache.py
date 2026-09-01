"""ElastiCache gathering — replication groups and cache clusters.

`get_replication_groups` (describe_replication_groups) and
`get_cache_clusters` (describe_cache_clusters) already return everything
needed for at-rest-encryption, in-transit-encryption, and backup-retention
evaluation (AtRestEncryptionEnabled, TransitEncryptionEnabled,
SnapshotRetentionLimit, Engine) in one call each — no extra fan-out needed.
That evaluation itself is left server-side.

Whether each replication group / standalone cache cluster is centrally
protected by AWS Backup (one region-wide backup.get_protected_resource_
arns() call, isolated in its own try/except — see gather()'s own comment)
is folded in as raw['_ProtectedByAwsBackup'], keyed by the record's own
ARN (already used as resource_id for both resource types). A lookup
failure stamps False rather than None on every record — this field only
ever suppresses a finding that would otherwise fire
(elasticache_nobackup), so failing toward False means failing toward
still firing rather than hiding a real problem. See
lensix_inventory.aws.backup's own docstring for the cross-service
rationale.
"""

import boto3

from .backup import get_protected_resource_arns


def get_replication_groups(region):
    ec = boto3.client('elasticache', region_name=region)
    groups = []
    kwargs = {}
    while True:
        resp = ec.describe_replication_groups(**kwargs)
        groups.extend(resp.get('ReplicationGroups', []))
        marker = resp.get('Marker')
        if not marker:
            break
        kwargs['Marker'] = marker
    return groups


def get_cache_clusters(region):
    ec = boto3.client('elasticache', region_name=region)
    clusters = []
    kwargs = {}
    while True:
        resp = ec.describe_cache_clusters(**kwargs)
        clusters.extend(resp.get('CacheClusters', []))
        marker = resp.get('Marker')
        if not marker:
            break
        kwargs['Marker'] = marker
    return clusters


def get_tags(region, arn):
    """Neither describe_replication_groups nor describe_cache_clusters
    includes tags — ElastiCache needs its own separate
    list_tags_for_resource call, keyed by ARN despite the parameter's
    own name (ResourceName). Response key is TagList (matching RDS's own
    convention), not Tags. Returns [] on failure."""
    ec = boto3.client('elasticache', region_name=region)
    try:
        return ec.list_tags_for_resource(ResourceName=arn).get('TagList', [])
    except Exception:
        return []


def gather(region, writer):
    # AWS Backup protection is a region-wide fact, not a per-resource one
    # — one paginated call answers it for both replication groups and
    # standalone clusters at once. Isolated so a failure here degrades
    # every record's _ProtectedByAwsBackup to False (still fires the
    # static check) rather than aborting either resource-type's gather.
    try:
        protected_arns = get_protected_resource_arns(region)
    except Exception as e:
        protected_arns = None
        writer.add_error(region=region, source='elasticache (aws backup protected resources)', message=e)

    # Replication groups and cache clusters are independent describe
    # calls — isolate them so a failure fetching one doesn't prevent the
    # other from being gathered.
    try:
        for rg in get_replication_groups(region):
            rg_id = rg['ReplicationGroupId']
            rg_arn = rg.get('ARN', rg_id)
            raw = dict(rg)
            raw['_ProtectedByAwsBackup'] = (
                False if protected_arns is None else rg.get('ARN') in protected_arns
            )
            writer.add_resource(
                resource_type='elasticache_replication_group',
                region=region,
                resource_id=rg_arn,
                resource_name=rg_id,
                raw=raw,
                tags=get_tags(region, rg_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='elasticache (replication groups)', message=e)

    try:
        for cluster in get_cache_clusters(region):
            cluster_id = cluster['CacheClusterId']
            cluster_arn = cluster.get('ARN', cluster_id)
            raw = dict(cluster)
            raw['_ProtectedByAwsBackup'] = (
                False if protected_arns is None else cluster.get('ARN') in protected_arns
            )
            writer.add_resource(
                resource_type='elasticache_cluster',
                region=region,
                resource_id=cluster_arn,
                resource_name=cluster_id,
                raw=raw,
                tags=get_tags(region, cluster_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='elasticache (cache clusters)', message=e)
