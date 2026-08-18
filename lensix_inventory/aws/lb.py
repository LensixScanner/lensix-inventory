"""Load balancer gathering — Classic ELBs and modern ALB/NLBs.

Two resource types: `classic_load_balancer` and `load_balancer` (ALB/NLB).
Target groups aren't a separate resource type — instead (mirroring s3.py's
fused per-bucket fan-out) each modern load balancer's raw record folds in
every relevant sub-API call against that same load balancer: attributes,
listeners (used for HTTP-listener/TLS-policy/security-policy/no-HTTPS
evaluation), target groups with their own attributes and target health
(used for unused/nonredundant/unhealthy/deregistration-delay evaluation),
and — for ALBs only — the attached WAF Web ACL, if any. Classic LBs get the
equivalent fold-in of attributes and instance health.
"""

import boto3


def get_classic_lbs(region):
    elb = boto3.client('elb', region_name=region)
    lbs = []
    for page in elb.get_paginator('describe_load_balancers').paginate():
        lbs.extend(page['LoadBalancerDescriptions'])
    return lbs


def get_classic_attributes(region, name):
    elb = boto3.client('elb', region_name=region)
    try:
        return elb.describe_load_balancer_attributes(LoadBalancerName=name)['LoadBalancerAttributes']
    except Exception:
        return {}


def get_classic_instance_health(region, name):
    elb = boto3.client('elb', region_name=region)
    try:
        return elb.describe_instance_health(LoadBalancerName=name)['InstanceStates']
    except Exception:
        return []


def get_modern_lbs(region):
    elbv2 = boto3.client('elbv2', region_name=region)
    lbs = []
    for page in elbv2.get_paginator('describe_load_balancers').paginate():
        lbs.extend(page['LoadBalancers'])
    return lbs


def get_lb_attributes(region, arn):
    elbv2 = boto3.client('elbv2', region_name=region)
    try:
        resp = elbv2.describe_load_balancer_attributes(LoadBalancerArn=arn)
        return {a['Key']: a['Value'] for a in resp['Attributes']}
    except Exception:
        return {}


def get_listeners(region, lb_arn):
    elbv2 = boto3.client('elbv2', region_name=region)
    try:
        return elbv2.describe_listeners(LoadBalancerArn=lb_arn)['Listeners']
    except Exception:
        return []


def get_target_groups(region, lb_arn):
    elbv2 = boto3.client('elbv2', region_name=region)
    tgs = []
    for page in elbv2.get_paginator('describe_target_groups').paginate(LoadBalancerArn=lb_arn):
        tgs.extend(page['TargetGroups'])
    return tgs


def get_target_group_attributes(region, tg_arn):
    elbv2 = boto3.client('elbv2', region_name=region)
    try:
        resp = elbv2.describe_target_group_attributes(TargetGroupArn=tg_arn)
        return {a['Key']: a['Value'] for a in resp['Attributes']}
    except Exception:
        return {}


def get_target_health(region, tg_arn):
    elbv2 = boto3.client('elbv2', region_name=region)
    try:
        return elbv2.describe_target_health(TargetGroupArn=tg_arn)['TargetHealthDescriptions']
    except Exception:
        return []


def get_web_acl(region, resource_arn):
    """Best-effort — no WAF web ACL attached is the expected common case,
    not an error (a WAFNonexistentItemException means exactly that)."""
    wafv2 = boto3.client('wafv2', region_name=region)
    try:
        resp = wafv2.get_web_acl_for_resource(ResourceArn=resource_arn)
        return resp.get('WebACL')
    except wafv2.exceptions.WAFNonexistentItemException:
        return None
    except Exception:
        return None


def gather(region, writer):
    for lb in get_classic_lbs(region):
        name = lb['LoadBalancerName']
        raw = dict(lb)
        raw['_Attributes'] = get_classic_attributes(region, name)
        raw['_InstanceHealth'] = get_classic_instance_health(region, name) if lb.get('Instances') else []
        writer.add_resource(
            resource_type='classic_load_balancer',
            region=region,
            resource_id=name,
            resource_name=name,
            scope_id=lb.get('VPCId'),
            raw=raw,
        )

    for lb in get_modern_lbs(region):
        name = lb['LoadBalancerName']
        arn = lb['LoadBalancerArn']
        tgs = get_target_groups(region, arn)
        tg_records = []
        for tg in tgs:
            tg_arn = tg['TargetGroupArn']
            tg_records.append({
                **tg,
                '_Attributes': get_target_group_attributes(region, tg_arn),
                '_TargetHealthDescriptions': get_target_health(region, tg_arn),
            })

        raw = dict(lb)
        raw['_Attributes'] = get_lb_attributes(region, arn)
        raw['_Listeners'] = get_listeners(region, arn)
        raw['_TargetGroups'] = tg_records
        if lb.get('Type') == 'application':
            raw['_WebACL'] = get_web_acl(region, arn)

        writer.add_resource(
            resource_type='load_balancer',
            region=region,
            resource_id=arn,
            resource_name=name,
            scope_id=lb.get('VpcId'),
            raw=raw,
        )
