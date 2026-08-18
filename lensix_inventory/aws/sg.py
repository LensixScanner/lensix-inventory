"""Security group gathering.

Determining "is this security group referenced anywhere" (for an unused-SG
finding) means cross-referencing against many other AWS services (ENIs,
Lambda, ECS, RDS, EKS, CloudFormation, ...). None of that fan-out needs
gathering here: every one of those other resource types
either already gets its own gather module in this tool (EC2 ENIs, Lambda
functions with VpcConfig, ...) or will as this tool grows, and each of
those raw records already carries its own security-group-ID references
(e.g. an ENI's `Groups`, a Lambda function's `VpcConfig.SecurityGroupIds`).
Lensix can recompute "in use anywhere" server-side from the union of all
uploaded resources — this tool just needs to gather the security groups
and their rules themselves.
"""

import boto3


def get_security_groups(region):
    ec2 = boto3.client('ec2', region_name=region)
    sgs = []
    for page in ec2.get_paginator('describe_security_groups').paginate():
        sgs.extend(page['SecurityGroups'])
    return sgs


def get_security_group_rules(region):
    ec2 = boto3.client('ec2', region_name=region)
    rules = []
    for page in ec2.get_paginator('describe_security_group_rules').paginate():
        rules.extend(page['SecurityGroupRules'])
    return rules


def gather(region, writer):
    rules_by_group = {}
    for rule in get_security_group_rules(region):
        rules_by_group.setdefault(rule['GroupId'], []).append(rule)

    for sg in get_security_groups(region):
        group_id = sg['GroupId']
        raw = dict(sg)
        raw['_Rules'] = rules_by_group.get(group_id, [])
        writer.add_resource(
            resource_type='security_group',
            region=region,
            resource_id=group_id,
            resource_name=sg.get('GroupName', group_id),
            scope_id=sg.get('VpcId'),
            raw=raw,
        )
