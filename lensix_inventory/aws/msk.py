"""MSK (Managed Streaming for Kafka) gathering — clusters.

Clusters are listed (list_clusters_v2, falling back to the older
list_clusters if the v2 API isn't available) then described individually
(describe_cluster_v2) — the describe_cluster_v2 result becomes the raw
`msk_cluster` record as-is.
"""

import boto3


def get_clusters(region):
    kafka = boto3.client('kafka', region_name=region)
    clusters = []
    try:
        paginator = kafka.get_paginator('list_clusters_v2')
        for page in paginator.paginate(ClusterTypeFilter='ALL'):
            clusters.extend(page.get('ClusterInfoList', []))
    except Exception:
        kwargs = {}
        while True:
            resp = kafka.list_clusters(**kwargs)
            clusters.extend(resp.get('ClusterInfoList', []))
            token = resp.get('NextToken')
            if not token:
                break
            kwargs['NextToken'] = token
    return clusters


def describe_cluster(region, arn):
    kafka = boto3.client('kafka', region_name=region)
    return kafka.describe_cluster_v2(ClusterArn=arn)['ClusterInfo']


def gather(region, writer):
    for summary in get_clusters(region):
        arn = summary.get('ClusterArn', '')
        name = summary.get('ClusterName', arn)
        try:
            cluster = describe_cluster(region, arn)
        except Exception as e:
            writer.add_error(region=region, source=f'msk_cluster:{arn}', message=e)
            cluster = summary

        recorded = writer.add_resource(
            resource_type='msk_cluster',
            region=region,
            resource_id=arn,
            resource_name=name,
            raw=cluster,
            # MSK's own Tags field is already a flat {key: value} map, not
            # a list of {'Key','Value'} pairs — no extra call needed.
            tags=cluster.get('Tags'),
        )
        if not recorded:
            continue
        # No VpcId is ever exposed directly; subnet edges reach it
        # transitively via vpc.py's own subnet -> vpc edge. Provisioned
        # and Serverless clusters carry this data in different shapes (a
        # serverless cluster can have more than one VPC config).
        prov = cluster.get('Provisioned') or {}
        broker_info = prov.get('BrokerNodeGroupInfo') or {}
        for subnet_id in broker_info.get('ClientSubnets', []):
            writer.add_edge(from_type='msk_cluster', from_id=arn, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
        for sg_id in broker_info.get('SecurityGroups', []):
            writer.add_edge(from_type='msk_cluster', from_id=arn, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
        for vpc_config in (cluster.get('Serverless') or {}).get('VpcConfigs', []):
            for subnet_id in vpc_config.get('SubnetIds', []):
                writer.add_edge(from_type='msk_cluster', from_id=arn, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
            for sg_id in vpc_config.get('SecurityGroupIds', []):
                writer.add_edge(from_type='msk_cluster', from_id=arn, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
