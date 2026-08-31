"""DynamoDB gathering — tables (merged with continuous-backups/PITR status)
and DAX clusters.

`get_tables`/`get_table_detail` (list_tables + describe_table) and
`get_dax_clusters` (describe_clusters) are the pure fetchers.
`describe_continuous_backups` IS a pure per-resource fetch (point-in-time-
recovery status isn't in describe_table's response), so it's merged into
each table's raw record the same fused-fetch pattern as s3.py;
PITR/encryption/customer-managed-key evaluation is left server-side.
"""

import boto3


def get_tables(region):
    ddb = boto3.client('dynamodb', region_name=region)
    names = []
    for page in ddb.get_paginator('list_tables').paginate():
        names.extend(page.get('TableNames', []))
    return names


def get_table_detail(region, name):
    ddb = boto3.client('dynamodb', region_name=region)
    return ddb.describe_table(TableName=name)['Table']


def get_continuous_backups(region, name):
    ddb = boto3.client('dynamodb', region_name=region)
    try:
        return ddb.describe_continuous_backups(TableName=name).get('ContinuousBackupsDescription')
    except Exception:
        return None


def get_table_tags(region, table_arn):
    """DynamoDB tags aren't in describe_table's response — a separate,
    paginated list_tags_of_resource call, keyed by ARN. Returns the raw
    Tags list ([{'Key','Value'}, ...]), or [] on failure."""
    ddb = boto3.client('dynamodb', region_name=region)
    tags = []
    try:
        kwargs = {'ResourceArn': table_arn}
        while True:
            resp = ddb.list_tags_of_resource(**kwargs)
            tags.extend(resp.get('Tags', []))
            token = resp.get('NextToken')
            if not token:
                break
            kwargs['NextToken'] = token
    except Exception:
        return []
    return tags


def get_dax_cluster_tags(region, cluster_arn):
    """DAX tags likewise aren't in describe_clusters' response — its own
    separate, paginated list_tags call, keyed by ARN."""
    dax = boto3.client('dax', region_name=region)
    tags = []
    try:
        kwargs = {'ResourceName': cluster_arn}
        while True:
            resp = dax.list_tags(**kwargs)
            tags.extend(resp.get('Tags', []))
            token = resp.get('NextToken')
            if not token:
                break
            kwargs['NextToken'] = token
    except Exception:
        return []
    return tags


def get_dax_clusters(region):
    dax = boto3.client('dax', region_name=region)
    clusters = []
    kwargs = {}
    while True:
        resp = dax.describe_clusters(**kwargs)
        clusters.extend(resp.get('Clusters', []))
        token = resp.get('NextToken')
        if not token:
            break
        kwargs['NextToken'] = token
    return clusters


def gather(region, writer):
    # Tables and DAX clusters are independent list calls — isolate them
    # so a failure fetching one doesn't prevent the other from being
    # gathered.
    try:
        for name in get_tables(region):
            try:
                table = get_table_detail(region, name)
            except Exception as e:
                writer.add_error(region=region, source=f'dynamodb_table:{name}', message=e)
                continue
            table['_ContinuousBackups'] = get_continuous_backups(region, name)
            table_arn = table.get('TableArn', name)
            writer.add_resource(
                resource_type='dynamodb_table',
                region=region,
                resource_id=table_arn,
                resource_name=name,
                raw=table,
                tags=get_table_tags(region, table_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='dynamodb (tables)', message=e)

    try:
        for cluster in get_dax_clusters(region):
            cluster_arn = cluster.get('ClusterArn', cluster.get('ClusterName', ''))
            writer.add_resource(
                resource_type='dax_cluster',
                region=region,
                resource_id=cluster_arn,
                resource_name=cluster.get('ClusterName', ''),
                raw=cluster,
                tags=get_dax_cluster_tags(region, cluster_arn),
            )
    except Exception as e:
        writer.add_error(region=region, source='dynamodb (dax clusters)', message=e)
