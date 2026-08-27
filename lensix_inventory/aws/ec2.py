"""EC2 gathering — instances, network interfaces, launch templates.

Only the data-fetching calls are included here (get_instances, the ENI
describe call, per-instance termination-protection, user-data attribute,
and CloudWatch-metrics lookups, launch templates + their default version)
— pass/fail evaluation is left server-side. Findings are determined
server-side by Lensix from the uploaded raw data, with one exception: EC2
user-data is scanned for secrets locally (see common/secrets.py) and only
the match results are uploaded, never the raw (often base64-encoded,
sometimes secret-bearing) user-data text itself.

EBS volumes are deliberately NOT gathered here — ebs.py is the dedicated,
richer home for `ebs_volume` (it also merges in public-snapshot and
default-encryption context), so this module doesn't duplicate it.

7-day CPU/network CloudWatch metrics (get_metrics) ARE gathered here now,
merged into each running instance's raw['_Metrics'] — this is a point-in-
time snapshot of a rolling 7-day window, not a live "as of right now"
figure, so the checks that read it (ec2_oversized/undersized/unused) treat
it as any other already-gathered field: parsed as-is, no live call of
their own, "reasonably fresh" as of whenever this gather ran. Only fetched
for running instances (CloudWatch has nothing meaningful for stopped/
terminated ones) — how long an instance needs to have been running for the
7-day average to be meaningful is a check-layer decision (see
aws/uploadchecks/ec2_import.py's check_oversized/check_undersized/
check_unused in lensix-scanner-light), not a gather-time one.
"""

import base64
from datetime import datetime, timedelta, timezone

import boto3

from ..common.secrets import scan_text_for_secrets

METRICS_LOOKBACK_DAYS = 7


def get_metrics(region, instance_id):
    """Fetch CPU and network metrics for an instance in a single CloudWatch call."""
    cw = boto3.client('cloudwatch', region_name=region)
    end_time   = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=METRICS_LOOKBACK_DAYS)
    period     = METRICS_LOOKBACK_DAYS * 86400
    dims       = [{'Name': 'InstanceId', 'Value': instance_id}]

    resp = cw.get_metric_data(
        MetricDataQueries=[
            {'Id': 'cpu_avg', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUUtilization', 'Dimensions': dims}, 'Period': period, 'Stat': 'Average'}},
            {'Id': 'cpu_max', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'CPUUtilization', 'Dimensions': dims}, 'Period': period, 'Stat': 'Maximum'}},
            {'Id': 'net_in',  'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'NetworkIn',       'Dimensions': dims}, 'Period': period, 'Stat': 'Average'}, 'ReturnData': False},
            {'Id': 'net_out', 'MetricStat': {'Metric': {'Namespace': 'AWS/EC2', 'MetricName': 'NetworkOut',      'Dimensions': dims}, 'Period': period, 'Stat': 'Average'}, 'ReturnData': False},
            {'Id': 'net_avg', 'Expression': 'SUM([net_in, net_out])'},
        ],
        StartTime=start_time,
        EndTime=end_time,
    )

    result = {}
    for r in resp['MetricDataResults']:
        result[r['Id']] = r['Values'][0] if r['Values'] else None
    return {
        'avg_cpu':     result.get('cpu_avg'),
        'max_cpu':     result.get('cpu_max'),
        'avg_network': result.get('net_avg'),
    }


def _tag_name(tags, fallback):
    return next((t['Value'] for t in (tags or []) if t['Key'] == 'Name'), fallback)


def get_instances(region):
    ec2 = boto3.client('ec2', region_name=region)
    paginator = ec2.get_paginator('describe_instances')
    instances = []
    for page in paginator.paginate():
        for reservation in page['Reservations']:
            instances.extend(reservation['Instances'])
    return instances


def get_instance_statuses(region, instance_ids):
    if not instance_ids:
        return {}
    ec2 = boto3.client('ec2', region_name=region)
    statuses = {}
    paginator = ec2.get_paginator('describe_instance_status')
    for page in paginator.paginate(InstanceIds=instance_ids, IncludeAllInstances=True):
        for s in page['InstanceStatuses']:
            statuses[s['InstanceId']] = s['SystemStatus']['Status']
    return statuses


def get_termination_protection(region, instance_id):
    """Raises on failure (unlike get_userdata_secret_hits below) — gather()
    catches this per-instance and records a writer error, leaving
    `_DisableApiTermination` as None. That None is meaningfully different
    from a real False: consumers should treat it as "unknown, don't
    evaluate" rather than conflating it with "termination protection is
    off"."""
    ec2 = boto3.client('ec2', region_name=region)
    resp = ec2.describe_instance_attribute(InstanceId=instance_id, Attribute='disableApiTermination')
    return resp['DisableApiTermination']['Value']


def get_userdata_secret_hits(region, instance_id):
    """Fetches user-data, scans it locally for secrets, and returns ONLY the
    matched rule names — the decoded user-data text itself is discarded
    immediately and never returned/uploaded."""
    try:
        ec2 = boto3.client('ec2', region_name=region)
        resp = ec2.describe_instance_attribute(InstanceId=instance_id, Attribute='userData')
        encoded = resp.get('UserData', {}).get('Value')
        if not encoded:
            return []
        text = base64.b64decode(encoded).decode('utf-8', errors='replace')
        return scan_text_for_secrets(text)
    except Exception:
        return []


def get_network_interfaces(region):
    ec2 = boto3.client('ec2', region_name=region)
    enis = []
    paginator = ec2.get_paginator('describe_network_interfaces')
    for page in paginator.paginate():
        enis.extend(page['NetworkInterfaces'])
    return enis


def get_launch_templates(region):
    ec2 = boto3.client('ec2', region_name=region)
    lts = []
    for page in ec2.get_paginator('describe_launch_templates').paginate():
        lts.extend(page['LaunchTemplates'])
    return lts


def get_launch_template_default_version(region, lt_id):
    ec2 = boto3.client('ec2', region_name=region)
    versions = ec2.describe_launch_template_versions(
        LaunchTemplateId=lt_id, Versions=['$Default'],
    )['LaunchTemplateVersions']
    return versions[0] if versions else None


def gather(region, writer):
    """Gathers all EC2-owned resource types for one region into `writer`."""
    # Instances and network interfaces are independent describe calls —
    # isolate them so one's failure doesn't discard the other.
    instances = get_instances(region)
    running_ids = [i['InstanceId'] for i in instances if i['State']['Name'] == 'running']
    statuses = get_instance_statuses(region, running_ids)

    for inst in instances:
        iid = inst['InstanceId']
        raw = dict(inst)
        raw['_SystemStatus'] = statuses.get(iid)
        try:
            raw['_DisableApiTermination'] = get_termination_protection(region, iid)
        except Exception as e:
            raw['_DisableApiTermination'] = None
            writer.add_error(region=region, source=f'ec2 (termination protection:{iid})', message=e)

        is_running = inst['State']['Name'] == 'running'
        secret_hits = get_userdata_secret_hits(region, iid) if is_running else []

        if is_running:
            try:
                raw['_Metrics'] = get_metrics(region, iid)
            except Exception as e:
                raw['_Metrics'] = None
                writer.add_error(region=region, source=f'ec2 (metrics:{iid})', message=e)
        else:
            raw['_Metrics'] = None

        writer.add_resource(
            resource_type='ec2_instance',
            region=region,
            resource_id=iid,
            resource_name=_tag_name(inst.get('Tags'), iid),
            scope_id=inst.get('VpcId'),
            raw=raw,
            secret_scan_hits=secret_hits,
        )

    try:
        enis = get_network_interfaces(region)
    except Exception as e:
        writer.add_error(region=region, source='ec2 (network interfaces)', message=e)
        enis = []
    for eni in enis:
        eni_id = eni['NetworkInterfaceId']
        writer.add_resource(
            resource_type='elastic_network_interface',
            region=region,
            resource_id=eni_id,
            resource_name=eni.get('Description') or eni_id,
            scope_id=eni.get('VpcId'),
            raw=eni,
        )

    try:
        launch_templates = get_launch_templates(region)
    except Exception as e:
        writer.add_error(region=region, source='ec2 (launch templates)', message=e)
        launch_templates = []
    for lt in launch_templates:
        lt_id = lt['LaunchTemplateId']
        raw = dict(lt)
        try:
            raw['_DefaultVersion'] = get_launch_template_default_version(region, lt_id)
        except Exception as e:
            raw['_DefaultVersion'] = None
            writer.add_error(region=region, source=f'ec2 (launch template version:{lt_id})', message=e)
        writer.add_resource(
            resource_type='launch_template',
            region=region,
            resource_id=lt_id,
            resource_name=lt.get('LaunchTemplateName', lt_id),
            raw=raw,
        )
