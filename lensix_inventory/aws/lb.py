"""Load balancer gathering — Classic ELBs and modern ALB/NLBs.

Three resource types: `classic_load_balancer`, `load_balancer` (ALB/NLB),
and `target_group`. Each modern load balancer's raw record folds in every
relevant sub-API call against that same load balancer: attributes,
listeners (used for HTTP-listener/TLS-policy/security-policy/no-HTTPS
evaluation), its own target groups with their own attributes and target
health nested under `_TargetGroups` (used for unused/nonredundant/
unhealthy evaluation scoped to that one load balancer), and — for ALBs
only — the attached WAF Web ACL, if any. Classic LBs get the equivalent
fold-in of attributes and instance health.

`target_group` is ALSO gathered as its own top-level, region-wide resource
type (describe_target_groups() with no LoadBalancerArn filter) — unlike
the nested `_TargetGroups`, this catches target groups that exist but
aren't attached to any load balancer at all, and lets any check needing
"does target group X still exist" (e.g. an Auto Scaling group's
TargetGroupARNs, or a listener rule's forward action) correlate against
the full region-wide set instead of needing a live call of its own. This
duplicates a `describe_target_group_attributes`/`describe_target_health`
call for target groups that ARE attached to a load balancer (once here,
once via that load balancer's own `_TargetGroups` fold-in) — an accepted,
disclosed inefficiency in exchange for not having to restructure the
already-established per-load-balancer fold-in shape.
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


def get_classic_lb_tags(region, name):
    """Classic ELB tags aren't in describe_load_balancers' response — its
    own separate describe_tags call, one LB per call (matching this
    module's existing one-call-per-resource convention for attributes/
    instance health, rather than describe_tags' own up-to-20-per-call
    batching). Returns [] on failure."""
    elb = boto3.client('elb', region_name=region)
    try:
        descs = elb.describe_tags(LoadBalancerNames=[name])['TagDescriptions']
        return descs[0]['Tags'] if descs else []
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


def get_all_target_groups(region):
    """Every target group in the region, regardless of whether it's
    attached to a load balancer — unlike get_target_groups() above, which
    is scoped to one load balancer's own attached target groups."""
    elbv2 = boto3.client('elbv2', region_name=region)
    tgs = []
    for page in elbv2.get_paginator('describe_target_groups').paginate():
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


def get_elbv2_tags(region, arn):
    """Covers both modern load balancers and target groups — ELBv2's own
    describe_tags takes ResourceArns for either type. Not in
    describe_load_balancers'/describe_target_groups' own response.
    Returns [] on failure."""
    elbv2 = boto3.client('elbv2', region_name=region)
    try:
        descs = elbv2.describe_tags(ResourceArns=[arn])['TagDescriptions']
        return descs[0]['Tags'] if descs else []
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


def _add_target_edges(writer, from_type, from_id, target_health_descriptions):
    """Shared by target_group -> {ec2_instance,lambda_function} (modern
    ELBv2) and classic_load_balancer -> ec2_instance (classic ELB, no
    target-group indirection) -- a target's Target.Id/InstanceId is an
    EC2 instance id, a Lambda function ARN, or an IP address (IP-type
    target groups aren't a resource Lensix tracks, so those produce no
    edge)."""
    for t in target_health_descriptions:
        target_id = (t.get('Target') or {}).get('Id') or t.get('InstanceId')
        if not target_id:
            continue
        if target_id.startswith('i-'):
            writer.add_edge(from_type=from_type, from_id=from_id, to_type='ec2_instance', to_id=target_id, relationship='routes_to_instance')
        elif target_id.startswith('arn:aws:lambda:'):
            writer.add_edge(from_type=from_type, from_id=from_id, to_type='lambda_function', to_id=target_id, relationship='routes_to_function')


def gather(region, writer):
    # Classic ELBs and modern ALB/NLBs are independent describe calls —
    # isolate them so one's failure doesn't discard the other.
    try:
        classic_lbs = get_classic_lbs(region)
    except Exception as e:
        writer.add_error(region=region, source='lb (classic)', message=e)
        classic_lbs = []
    for lb in classic_lbs:
        name = lb['LoadBalancerName']
        raw = dict(lb)
        raw['_Attributes'] = get_classic_attributes(region, name)
        raw['_InstanceHealth'] = get_classic_instance_health(region, name) if lb.get('Instances') else []
        recorded = writer.add_resource(
            resource_type='classic_load_balancer',
            region=region,
            resource_id=name,
            resource_name=name,
            scope_id=lb.get('VPCId'),
            raw=raw,
            tags=get_classic_lb_tags(region, name),
        )
        if not recorded:
            continue
        if lb.get('VPCId'):
            writer.add_edge(from_type='classic_load_balancer', from_id=name, to_type='vpc', to_id=lb['VPCId'], relationship='in_vpc')
        for sg_id in lb.get('SecurityGroups', []):
            writer.add_edge(from_type='classic_load_balancer', from_id=name, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
        # No target-group indirection for classic ELB -- it registers
        # instances directly.
        _add_target_edges(writer, 'classic_load_balancer', name, raw['_InstanceHealth'])

    try:
        modern_lbs = get_modern_lbs(region)
    except Exception as e:
        writer.add_error(region=region, source='lb (modern)', message=e)
        modern_lbs = []
    for lb in modern_lbs:
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

        recorded = writer.add_resource(
            resource_type='load_balancer',
            region=region,
            resource_id=arn,
            resource_name=name,
            scope_id=lb.get('VpcId'),
            raw=raw,
            tags=get_elbv2_tags(region, arn),
        )
        if not recorded:
            continue
        if lb.get('VpcId'):
            writer.add_edge(from_type='load_balancer', from_id=arn, to_type='vpc', to_id=lb['VpcId'], relationship='in_vpc')
        for sg_id in lb.get('SecurityGroups', []):
            writer.add_edge(from_type='load_balancer', from_id=arn, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
        for tg in tg_records:
            if tg.get('TargetGroupArn'):
                writer.add_edge(from_type='load_balancer', from_id=arn, to_type='target_group', to_id=tg['TargetGroupArn'], relationship='routes_to')

    try:
        all_tgs = get_all_target_groups(region)
    except Exception as e:
        writer.add_error(region=region, source='lb (target groups)', message=e)
        all_tgs = []
    for tg in all_tgs:
        tg_arn = tg['TargetGroupArn']
        raw = dict(tg)
        raw['_Attributes'] = get_target_group_attributes(region, tg_arn)
        raw['_TargetHealthDescriptions'] = get_target_health(region, tg_arn)
        recorded = writer.add_resource(
            resource_type='target_group',
            region=region,
            resource_id=tg_arn,
            resource_name=tg.get('TargetGroupName', tg_arn),
            raw=raw,
            tags=get_elbv2_tags(region, tg_arn),
        )
        if recorded:
            _add_target_edges(writer, 'target_group', tg_arn, raw['_TargetHealthDescriptions'])
