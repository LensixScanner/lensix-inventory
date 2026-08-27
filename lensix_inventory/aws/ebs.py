"""EBS gathering — volumes, snapshots, AMIs, and the region's default-
encryption setting.

`get_volumes`/`get_snapshots` (describe_volumes/describe_snapshots) and
`ec2.describe_images(Owners=['self'])` (AMIs) are the pure fetchers;
unused-volume, encryption, customer-managed-key, snapshot-age, and
unencrypted-AMI evaluation is left server-side.

Two things worth calling out:
  - Public snapshot restorability is a genuine extra fetch (a second
    describe_snapshots call with `RestorableByUserIds=['all']` — that data
    isn't in the plain describe_snapshots response), so it's kept and
    merged into each snapshot's raw record as `_Public`.
  - Public AMI status is NOT fetched separately — DescribeImages' `Public`
    field is already present on every image returned by the main
    `Owners=['self']` call this module already makes, so no second call is
    needed.

Per-volume backup recency and AMI-in-use correlation against EC2 instances
and launch templates are NOT computed here — both are correlation against
data this tool already gathers independently (every `ebs_snapshot` record
already carries its own `VolumeId`; every `ec2_instance` record gathered by
ec2.py already carries its own `ImageId`), or, for launch templates, data no
module in this tool's current scope gathers yet (a follow-up, not silently
dropped). Per the "gather each resource type independently" principle (see
README), Lensix can recompute "volume has no recent backup" / "AMI unused"
server-side.
"""

import boto3


def get_volumes(region):
    ec2 = boto3.client('ec2', region_name=region)
    vols = []
    for page in ec2.get_paginator('describe_volumes').paginate():
        vols.extend(page['Volumes'])
    return vols


def get_snapshots(region):
    ec2 = boto3.client('ec2', region_name=region)
    snaps = []
    for page in ec2.get_paginator('describe_snapshots').paginate(OwnerIds=['self']):
        snaps.extend(page['Snapshots'])
    return snaps


def get_public_snapshot_ids(region):
    ec2 = boto3.client('ec2', region_name=region)
    public = set()
    try:
        for page in ec2.get_paginator('describe_snapshots').paginate(OwnerIds=['self'], RestorableByUserIds=['all']):
            for snap in page['Snapshots']:
                public.add(snap['SnapshotId'])
    except Exception:
        pass
    return public


def get_amis(region):
    ec2 = boto3.client('ec2', region_name=region)
    return ec2.describe_images(Owners=['self'])['Images']


def get_ebs_encryption_by_default(region):
    ec2 = boto3.client('ec2', region_name=region)
    try:
        return ec2.get_ebs_encryption_by_default()
    except Exception:
        return None


def _tag_name(tags, fallback):
    for tag in tags or []:
        if tag['Key'] == 'Name':
            return tag['Value']
    return fallback


def gather(region, writer):
    # Region settings, volumes, snapshots, and AMIs are four independent
    # fetches — isolate them so a failure fetching one doesn't prevent
    # the others from being gathered.
    enc_default = get_ebs_encryption_by_default(region)
    if enc_default is not None:
        writer.add_resource(
            resource_type='ebs_region_settings', region=region, resource_id=region,
            resource_name=region, raw=enc_default,
        )

    try:
        for vol in get_volumes(region):
            writer.add_resource(
                resource_type='ebs_volume', region=region, resource_id=vol['VolumeId'],
                resource_name=_tag_name(vol.get('Tags'), vol['VolumeId']), raw=vol,
            )
    except Exception as e:
        writer.add_error(region=region, source='ebs (volumes)', message=e)

    try:
        public_snap_ids = get_public_snapshot_ids(region)
        for snap in get_snapshots(region):
            raw = dict(snap)
            raw['_Public'] = snap['SnapshotId'] in public_snap_ids
            writer.add_resource(
                resource_type='ebs_snapshot', region=region, resource_id=snap['SnapshotId'],
                resource_name=_tag_name(snap.get('Tags'), snap['SnapshotId']), raw=raw,
            )
    except Exception as e:
        writer.add_error(region=region, source='ebs (snapshots)', message=e)

    try:
        for ami in get_amis(region):
            writer.add_resource(
                resource_type='ebs_ami', region=region, resource_id=ami['ImageId'],
                resource_name=ami.get('Name', ami['ImageId']), raw=ami,
            )
    except Exception as e:
        writer.add_error(region=region, source='ebs (amis)', message=e)
