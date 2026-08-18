"""EMR gathering — one raw record per cluster (active states only, merged
with its named security configuration if it has one).

`get_clusters` (list_clusters, filtered to WAITING/RUNNING/BOOTSTRAPPING)
and `get_cluster_detail` (describe_cluster) are the two pure fetchers.
`describe_security_configuration` IS raw per-resource data (the cluster's
own referenced encryption config isn't inlined in describe_cluster's
response, only its name), so it's merged into the cluster's raw record as
`_SecurityConfig` (None if the cluster has no security configuration
attached) — the same fused-fetch pattern as s3.py; encryption, TLS, and
disk-encryption evaluation is left server-side.
"""

import json

import boto3


def get_clusters(region):
    emr = boto3.client('emr', region_name=region)
    clusters = []
    for page in emr.get_paginator('list_clusters').paginate(ClusterStates=['WAITING', 'RUNNING', 'BOOTSTRAPPING']):
        clusters.extend(page.get('Clusters', []))
    return clusters


def get_cluster_detail(region, cluster_id):
    emr = boto3.client('emr', region_name=region)
    return emr.describe_cluster(ClusterId=cluster_id)['Cluster']


def get_security_config(region, name):
    emr = boto3.client('emr', region_name=region)
    resp = emr.describe_security_configuration(Name=name)
    return json.loads(resp['SecurityConfiguration'])


def gather(region, writer):
    for summary in get_clusters(region):
        cluster_id = summary['Id']
        try:
            cluster = get_cluster_detail(region, cluster_id)
        except Exception as e:
            writer.add_error(region=region, source=f'emr_cluster:{cluster_id}', message=e)
            continue

        name = cluster.get('Name', cluster_id)
        raw = dict(cluster)
        sec_config_name = cluster.get('SecurityConfiguration')
        if sec_config_name:
            try:
                raw['_SecurityConfig'] = get_security_config(region, sec_config_name)
            except Exception as e:
                writer.add_error(region=region, source=f'emr_cluster:{cluster_id}', message=e)
                raw['_SecurityConfig'] = None
        else:
            raw['_SecurityConfig'] = None

        writer.add_resource(
            resource_type='emr_cluster',
            region=region,
            resource_id=cluster_id,
            resource_name=name,
            raw=raw,
        )
