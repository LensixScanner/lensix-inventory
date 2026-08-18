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
        writer.add_resource(
            resource_type='docdb_cluster',
            region=region,
            resource_id=cluster['DBClusterArn'],
            resource_name=cluster['DBClusterIdentifier'],
            raw=cluster,
        )
