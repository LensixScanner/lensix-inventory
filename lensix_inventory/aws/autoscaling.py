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

One fan-out IS done here, per ASG: resolving whether the launch template
or launch configuration this ASG actually launches instances from assigns
a public IP. This can't be read off the ASG's own raw record — it lives
one level down, on the launch template/configuration itself — and it
can't reuse ec2.py's own launch_template resources either, since those
only ever fetch each template's `$Default` version, while an ASG can
reference `$Latest` or a specific pinned version number that differs from
the default. The result is attached to the ASG's raw record as
`_LaunchTemplatePublicIp` (True/False/None — see
_launches_with_public_ip's own docstring for what None means) so
scanner-light's asg_publicip check can evaluate the ASG once, instead of
scanner-light re-deriving the same fact from every individual instance's
own live PublicIpAddress (see ec2_checks.py's ec2_public_ip check, which
skips ASG members precisely because this check now covers them at the
group level).
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


def _launch_template_spec(asg):
    """The {LaunchTemplateId|LaunchTemplateName, Version} dict an ASG
    references, whether set directly (`LaunchTemplate`) or via
    `MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification` —
    same shape either way. None if this ASG uses a launch configuration
    instead (or, in principle, neither)."""
    lt = asg.get('LaunchTemplate')
    if lt:
        return lt
    mip = asg.get('MixedInstancesPolicy') or {}
    return (mip.get('LaunchTemplate') or {}).get('LaunchTemplateSpecification')


def _network_interfaces_associate_public_ip(network_interfaces):
    """True if any network interface in a launch template version's data
    explicitly associates a public IP; False if at least one explicitly
    disables it and none enable it; None if no network interface entry
    sets this field at all. That last case is genuinely indeterminate
    from the template alone — AWS falls back to the subnet's own
    MapPublicIpOnLaunch default, which this module doesn't gather (a
    second cross-service call this check doesn't need enough to justify
    yet) — so it's reported as unknown rather than guessed."""
    saw_explicit = False
    for ni in (network_interfaces or []):
        value = ni.get('AssociatePublicIpAddress')
        if value is None:
            continue
        saw_explicit = True
        if value:
            return True
    return False if saw_explicit else None


def _launches_with_public_ip(asg, region):
    """True/False/None for whether this ASG's launch template or launch
    configuration assigns instances a public IP — None when it can't be
    determined (no launch template/configuration referenced at all, or
    the referenced version/name no longer exists). Any lookup FAILURE
    (permission denied, throttling, ...) raises instead of returning None
    — silently returning None there would be indistinguishable from a
    legitimate "no launch template" ASG, hiding a real problem from
    gather()'s own error reporting (see its own try/except below). Builds
    its own client per call, same convention as ec2.py's own
    get_launch_template_default_version — no live call happens for an ASG
    that references neither a launch template nor a launch configuration
    (in principle possible, though AWS requires one of the two in
    practice)."""
    lc_name = asg.get('LaunchConfigurationName')
    if lc_name:
        asg_client = boto3.client('autoscaling', region_name=region, config=_BOTO_CFG)
        lcs = asg_client.describe_launch_configurations(
            LaunchConfigurationNames=[lc_name],
        )['LaunchConfigurations']
        return lcs[0].get('AssociatePublicIpAddress') if lcs else None

    lt = _launch_template_spec(asg)
    if not lt:
        return None
    ec2_client = boto3.client('ec2', region_name=region, config=_BOTO_CFG)
    kwargs = {'Versions': [str(lt.get('Version') or '$Default')]}
    if lt.get('LaunchTemplateId'):
        kwargs['LaunchTemplateId'] = lt['LaunchTemplateId']
    else:
        kwargs['LaunchTemplateName'] = lt['LaunchTemplateName']
    versions = ec2_client.describe_launch_template_versions(**kwargs)['LaunchTemplateVersions']
    if not versions:
        return None
    network_interfaces = versions[0].get('LaunchTemplateData', {}).get('NetworkInterfaces', [])
    return _network_interfaces_associate_public_ip(network_interfaces)


def gather(region, writer):
    for asg in get_asgs(region):
        try:
            asg['_LaunchTemplatePublicIp'] = _launches_with_public_ip(asg, region)
        except Exception as e:
            # Recorded via writer.add_error (-> scan_errors, same as every
            # other gather failure in this codebase) rather than swallowed
            # — a previous version of this function ate this exception
            # silently, which would have made a real permissions/API
            # problem indistinguishable from "no launch template" with
            # zero trace anywhere. One ASG's lookup failing still doesn't
            # abort the whole region's gather.
            asg['_LaunchTemplatePublicIp'] = None
            writer.add_error(region, 'autoscaling launch template/configuration lookup',
                              f"{asg.get('AutoScalingGroupName', asg.get('AutoScalingGroupARN'))}: {e}")
        writer.add_resource(
            resource_type='autoscaling_group',
            region=region,
            resource_id=asg['AutoScalingGroupARN'],
            resource_name=asg['AutoScalingGroupName'],
            raw=asg,
        )
