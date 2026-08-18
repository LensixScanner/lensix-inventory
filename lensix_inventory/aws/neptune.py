"""Neptune gathering — DB clusters.

describe_db_clusters (filtered to Engine == 'neptune', since RDS/Neptune/
DocumentDB share the same API) already returns everything evaluation
needs in one shot — a clean fetch/evaluate split; the fetch result becomes
the raw `neptune_cluster` record as-is.
"""

import boto3


def get_clusters(region):
    neptune = boto3.client('neptune', region_name=region)
    clusters = []
    for page in neptune.get_paginator('describe_db_clusters').paginate():
        for cluster in page.get('DBClusters', []):
            if cluster.get('Engine', '') == 'neptune':
                clusters.append(cluster)
    return clusters


def gather(region, writer):
    for cluster in get_clusters(region):
        arn = cluster.get('DBClusterArn', '')
        name = cluster.get('DBClusterIdentifier', arn)
        writer.add_resource(
            resource_type='neptune_cluster',
            region=region,
            resource_id=arn,
            resource_name=name,
            raw=cluster,
        )
