"""EKS gathering — one raw record per cluster.

`get_cluster_names`/`get_cluster` (list_clusters + describe_cluster)
already returns everything needed for logging, secrets-encryption, public-
endpoint, public-CIDR, and outdated-version evaluation (logging,
encryptionConfig, resourcesVpcConfig, version) in one call — no extra
fan-out needed. That evaluation itself is left server-side.
"""

import boto3


def get_cluster_names(region):
    eks = boto3.client('eks', region_name=region)
    names = []
    for page in eks.get_paginator('list_clusters').paginate():
        names.extend(page.get('clusters', []))
    return names


def get_cluster(region, name):
    eks = boto3.client('eks', region_name=region)
    return eks.describe_cluster(name=name)['cluster']


def gather(region, writer):
    for name in get_cluster_names(region):
        try:
            cluster = get_cluster(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'eks_cluster:{name}', message=e)
            continue
        writer.add_resource(
            resource_type='eks_cluster',
            region=region,
            resource_id=cluster.get('arn', name),
            resource_name=name,
            scope_id=cluster.get('resourcesVpcConfig', {}).get('vpcId'),
            raw=cluster,
        )
