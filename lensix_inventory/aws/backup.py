"""AWS Backup — cross-service protected-resource membership.

AWS Backup can centrally protect resources across many AWS services (RDS,
Redshift, ElastiCache, EFS, DynamoDB, EBS, and more) via backup plans and
on-demand backups. A resource under such a plan already has its backup
posture governed centrally, independent of whatever the service's own
built-in backup/snapshot-retention setting says — a per-service check that
only looks at (e.g.) RDS's own BackupRetentionPeriod can be flatly *wrong*
when it ignores this, the same governance-object lesson this session's
other backup/schedule-aware checks (autoscaling.py's own
get_scheduled_actions) already apply.

get_protected_resource_arns is called independently by rds.py's,
redshift.py's, and elasticache.py's own gather() — each an accepted
3x-redundant per-region call across three separately-deployed scanner
modules, the same trade-off already made for other cross-module lookups.
"""

import boto3


def get_protected_resource_arns(region):
    """Every resource AWS Backup protects in this region, across every
    resource type it supports — one region-wide, paginated
    list_protected_resources() call answers "is this resource centrally
    backed up" for RDS/Redshift/ElastiCache/etc. all at once; membership
    is checked by ARN, so no per-service ResourceType filtering is
    needed. Raises on failure — each of rds.py/redshift.py/elasticache.py's
    own gather() isolates this in its own try/except, same discipline as
    every other cross-module lookup this session (see autoscaling.py's
    own get_scheduled_actions)."""
    client = boto3.client('backup', region_name=region)
    arns = set()
    for page in client.get_paginator('list_protected_resources').paginate():
        arns.update(r['ResourceArn'] for r in page['Results'])
    return arns
