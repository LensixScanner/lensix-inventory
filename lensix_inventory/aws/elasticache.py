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


def gather(region, writer):
    for rg in get_replication_groups(region):
        rg_id = rg['ReplicationGroupId']
        writer.add_resource(
            resource_type='elasticache_replication_group',
            region=region,
            resource_id=rg.get('ARN', rg_id),
            resource_name=rg_id,
            raw=rg,
        )

    for cluster in get_cache_clusters(region):
        cluster_id = cluster['CacheClusterId']
        writer.add_resource(
            resource_type='elasticache_cluster',
            region=region,
            resource_id=cluster.get('ARN', cluster_id),
            resource_name=cluster_id,
            raw=cluster,
        )
