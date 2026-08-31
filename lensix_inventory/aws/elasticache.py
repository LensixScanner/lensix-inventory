"""ElastiCache gathering — replication groups and cache clusters.

`get_replication_groups` (describe_replication_groups) and
`get_cache_clusters` (describe_cache_clusters) already return everything
needed for at-rest-encryption, in-transit-encryption, and backup-retention
evaluation (AtRestEncryptionEnabled, TransitEncryptionEnabled,
SnapshotRetentionLimit, Engine) in one call each — no extra fan-out needed.
That evaluation itself is left server-side.
"""

import boto3


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
    # Replication groups and cache clusters are independent describe
    # calls — isolate them so a failure fetching one doesn't prevent the
    # other from being gathered.
    try:
        for rg in get_replication_groups(region):
            rg_id = rg['ReplicationGroupId']
            rg_arn = rg.get('ARN', rg_id)
            writer.add_resource(
                resource_type='elasticache_replication_group',
                region=region,
                resource_id=rg_arn,
                resource_name=rg_id,
                raw=rg,
                tags=get_tags(region, rg_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='elasticache (replication groups)', message=e)

    try:
        for cluster in get_cache_clusters(region):
            cluster_id = cluster['CacheClusterId']
            cluster_arn = cluster.get('ARN', cluster_id)
            writer.add_resource(
                resource_type='elasticache_cluster',
                region=region,
                resource_id=cluster_arn,
                resource_name=cluster_id,
                raw=cluster,
                tags=get_tags(region, cluster_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='elasticache (cache clusters)', message=e)
