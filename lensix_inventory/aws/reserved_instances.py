"""Reserved Instance / reserved-node gathering — what the account has
actually purchased, across every AWS service that sells them (EC2, RDS,
ElastiCache, Redshift, Elasticsearch/OpenSearch). One raw record per
reservation, feeding lensix-cost-light's commitments input (alongside
savingsplans.py's Savings Plan holdings) so it can price resources against
what's already committed instead of assuming on-demand rates everywhere.

This is holdings, not the published rate catalog — lensix-cost-light's own
aws/savingsplans.py and aws/commitments.py own that side (AWS's public
Price List Bulk API, and applying RIs/SPs to priced resources); this
module only answers "what does this account already own."

Bundled into one module rather than five, even though each fetch hits a
different boto3 client — a reservation isn't really "an EC2 resource" or
"an RDS resource" the way ec2.py/rds.py/elasticache.py/redshift.py/
elasticsearch.py otherwise mean it; it's its own billing-construct
resource family that happens to be sold per-service, and none of those
files' own docstrings claim reservations as part of their scope. Each
service's fetch is isolated in its own try/except in gather() below
(mirroring redshift.py's AWS Backup lookup) so one disabled/denied
service doesn't block collection from the other four.

Field/pagination shapes below were confirmed directly against the
installed botocore service models before writing this (not from memory):
  - EC2 (describe_reserved_instances): NOT paginated (no botocore
    paginator registered, no NextToken/nextToken in its output shape at
    all) — returns everything in one call. Records carry their own
    `Tags` (EC2-family {'Key','Value'} list); the other four don't
    support tagging on reservations at all, so tags=None for those.
  - RDS/ElastiCache/Redshift/ES (describe_reserved_*): all have a
    registered botocore paginator, Marker-based — used the same way
    rds.py/redshift.py already use get_paginator() elsewhere in this
    repo for other calls on these same clients.
  - The `es` client name (not `opensearch`) matches this repo's existing
    elasticsearch.py — OpenSearch domains are still reached through the
    legacy-named Elasticsearch Service API in boto3.
"""

import boto3


def get_ec2_reserved_instances(region):
    ec2 = boto3.client('ec2', region_name=region)
    return ec2.describe_reserved_instances().get('ReservedInstances', [])


def get_rds_reserved_instances(region):
    rds = boto3.client('rds', region_name=region)
    result = []
    for page in rds.get_paginator('describe_reserved_db_instances').paginate():
        result.extend(page.get('ReservedDBInstances', []))
    return result


def get_elasticache_reserved_nodes(region):
    ec = boto3.client('elasticache', region_name=region)
    result = []
    for page in ec.get_paginator('describe_reserved_cache_nodes').paginate():
        result.extend(page.get('ReservedCacheNodes', []))
    return result


def get_redshift_reserved_nodes(region):
    rs = boto3.client('redshift', region_name=region)
    result = []
    for page in rs.get_paginator('describe_reserved_nodes').paginate():
        result.extend(page.get('ReservedNodes', []))
    return result


def get_elasticsearch_reserved_instances(region):
    es = boto3.client('es', region_name=region)
    result = []
    for page in es.get_paginator('describe_reserved_elasticsearch_instances').paginate():
        result.extend(page.get('ReservedElasticsearchInstances', []))
    return result


# (resource_type, fetcher, id field, ARN field (or None), name field)
_SOURCES = [
    ('ec2_reserved_instance', get_ec2_reserved_instances,
     'ReservedInstancesId', None, 'ReservedInstancesId'),
    ('rds_reserved_instance', get_rds_reserved_instances,
     'ReservedDBInstanceId', 'ReservedDBInstanceArn', 'ReservedDBInstanceId'),
    ('elasticache_reserved_instance', get_elasticache_reserved_nodes,
     'ReservedCacheNodeId', 'ReservationARN', 'ReservedCacheNodeId'),
    ('redshift_reserved_node', get_redshift_reserved_nodes,
     'ReservedNodeId', None, 'ReservedNodeId'),
    # ReservationName is a customer-chosen label set at purchase time —
    # often blank, hence the `or resource_id` fallback below (same
    # fallback every source gets, kept explicit here since this is the
    # one row where the "name" field can genuinely be empty rather than
    # just equal to the id).
    ('elasticsearch_reserved_instance', get_elasticsearch_reserved_instances,
     'ReservedElasticsearchInstanceId', None, 'ReservationName'),
]


def gather(region, writer):
    for resource_type, fetch_fn, id_field, arn_field, name_field in _SOURCES:
        try:
            records = fetch_fn(region)
        except Exception as e:
            writer.add_error(region=region, source=f'reserved_instances ({resource_type})', message=e)
            continue
        for record in records:
            resource_id = record.get(id_field, '')
            arn = record.get(arn_field) if arn_field else None
            writer.add_resource(
                resource_type=resource_type,
                region=region,
                resource_id=arn or resource_id,
                resource_name=record.get(name_field) or resource_id,
                raw=dict(record),
                tags=record.get('Tags') if resource_type == 'ec2_reserved_instance' else None,
            )
