"""DocumentDB gathering — one raw record per cluster.

`get_docdb_clusters` (describe_db_clusters, filtered to Engine == 'docdb')
already returns everything needed for log-export, encryption, and
customer-managed-key evaluation (EnabledCloudwatchLogsExports,
StorageEncrypted, KmsKeyId) in one call — no extra fan-out needed. That
evaluation itself is left server-side.
"""

import boto3


def get_docdb_clusters(region):
    rds = boto3.client('rds', region_name=region)
    clusters = []
    for page in rds.get_paginator('describe_db_clusters').paginate():
        for cluster in page['DBClusters']:
            if cluster.get('Engine') == 'docdb':
                clusters.append(cluster)
    return clusters


def gather(region, writer):
    for cluster in get_docdb_clusters(region):
        cid = cluster['DBClusterArn']
        recorded = writer.add_resource(
            resource_type='docdb_cluster',
            region=region,
            resource_id=cid,
            resource_name=cluster['DBClusterIdentifier'],
            raw=cluster,
            tags=cluster.get('TagList'),
        )
        if not recorded:
            continue
        # No free VPC edge here: describe_db_clusters' own DBSubnetGroup
        # field is just the subnet group's name, not the object shape
        # (with a VpcId) describe_db_instances returns -- and DocumentDB
        # is only gathered at the cluster level, so that VpcId isn't
        # available without a new API call (out of scope for this pass).
        for sg in cluster.get('VpcSecurityGroups', []):
            if sg.get('VpcSecurityGroupId'):
                writer.add_edge(from_type='docdb_cluster', from_id=cid, to_type='security_group', to_id=sg['VpcSecurityGroupId'], relationship='member_of_sg')
