"""Auto Scaling group gathering — one raw record per group.

`get_asgs` (describe_auto_scaling_groups) already returns everything needed
for single-AZ, missing-ELB-health-check, empty-group, and
suspended-process evaluation in one call — no extra fan-out.

Verifying that a referenced target group still exists is NOT done here —
it's the same "is this reference still valid" cross-service correlation
sg.py's docstring describes skipping: the ASG's raw record already carries
its own `TargetGroupARNs`, and the target groups themselves are inventory
owned by the ELB/ALB module, not this one. Lensix can recompute "references
a nonexistent target group" server-side from the union of both.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


def get_asgs(region):
    asg_client = boto3.client('autoscaling', region_name=region, config=_BOTO_CFG)
    asgs = []
    for page in asg_client.get_paginator('describe_auto_scaling_groups').paginate(PaginationConfig={'PageSize': 100}):
        asgs.extend(page['AutoScalingGroups'])
    return asgs


def gather(region, writer):
    for asg in get_asgs(region):
        writer.add_resource(
            resource_type='autoscaling_group',
            region=region,
            resource_id=asg['AutoScalingGroupARN'],
            resource_name=asg['AutoScalingGroupName'],
            raw=asg,
        )
