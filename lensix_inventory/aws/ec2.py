"""EC2 gathering — instances, network interfaces.

Only the data-fetching calls are included here (get_instances, the ENI
describe call, per-instance termination-protection and user-data attribute
lookups) — pass/fail evaluation is left server-side. Findings are
determined server-side by Lensix from the uploaded raw data, with one
exception: EC2 user-data is scanned for secrets locally (see
common/secrets.py) and only the match results are uploaded, never the raw
(often base64-encoded, sometimes secret-bearing) user-data text itself.

EBS volumes are deliberately NOT gathered here — ebs.py is the dedicated,
richer home for `ebs_volume` (it also merges in public-snapshot and
default-encryption context), so this module doesn't duplicate it.

CloudWatch-metrics-based checks (oversized/undersized/unused instance) are
intentionally NOT replicated here — they're time-windowed telemetry, not
inventory, and a static snapshot can't represent "avg CPU over 7 days" in
any way that stays meaningful after upload.
"""

import base64

import boto3

from ..common.secrets import scan_text_for_secrets


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
    ec2 = boto3.client('ec2', region_name=region)
    try:
        resp = ec2.describe_instance_attribute(InstanceId=instance_id, Attribute='disableApiTermination')
        return resp['DisableApiTermination']['Value']
    except Exception:
        return None


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


def gather(region, writer):
    """Gathers all EC2-owned resource types for one region into `writer`."""
    instances = get_instances(region)
    running_ids = [i['InstanceId'] for i in instances if i['State']['Name'] == 'running']
    statuses = get_instance_statuses(region, running_ids)

    for inst in instances:
        iid = inst['InstanceId']
        raw = dict(inst)
        raw['_SystemStatus'] = statuses.get(iid)
        raw['_DisableApiTermination'] = get_termination_protection(region, iid)

        secret_hits = get_userdata_secret_hits(region, iid) if inst['State']['Name'] == 'running' else []

        writer.add_resource(
            resource_type='ec2_instance',
            region=region,
            resource_id=iid,
            resource_name=_tag_name(inst.get('Tags'), iid),
            scope_id=inst.get('VpcId'),
            raw=raw,
            secret_scan_hits=secret_hits,
        )

    for eni in get_network_interfaces(region):
        eni_id = eni['NetworkInterfaceId']
        writer.add_resource(
            resource_type='elastic_network_interface',
            region=region,
            resource_id=eni_id,
            resource_name=eni.get('Description') or eni_id,
            scope_id=eni.get('VpcId'),
            raw=eni,
        )
