"""EFS gathering — one raw record per file system.

`get_file_systems` (describe_file_systems) already returns everything
needed for encryption and customer-managed-key evaluation (Encrypted,
KmsKeyId) in one call — no extra fan-out needed. That evaluation itself is
left server-side.

Mount targets (and each mount target's own security groups) are a second
fan-out, fused into each file system's raw record as `_MountTargets` —
same pattern as redshift.py's `_LoggingStatus`/s3.py's per-bucket
sub-fetches. describe_mount_targets already returns each mount target's
own VpcId/SubnetId directly; only the security-group list needs its own
extra call per mount target (describe_mount_target_security_groups).
Isolated per-file-system/per-mount-target via writer.add_error() so one
failure doesn't lose every other file system's data.
"""

import boto3


def get_file_systems(region):
    efs = boto3.client('efs', region_name=region)
    file_systems = []
    for page in efs.get_paginator('describe_file_systems').paginate():
        file_systems.extend(page['FileSystems'])
    return file_systems


def get_mount_targets(region, file_system_id):
    efs = boto3.client('efs', region_name=region)
    mount_targets = []
    for page in efs.get_paginator('describe_mount_targets').paginate(FileSystemId=file_system_id):
        mount_targets.extend(page.get('MountTargets', []))
    return mount_targets


def get_mount_target_security_groups(region, mount_target_id):
    efs = boto3.client('efs', region_name=region)
    return efs.describe_mount_target_security_groups(MountTargetId=mount_target_id).get('SecurityGroups', [])


def _fs_name(fs):
    for tag in fs.get('Tags', []):
        if tag['Key'] == 'Name':
            return tag['Value']
    return fs['FileSystemId']


def gather(region, writer):
    for fs in get_file_systems(region):
        fs_id = fs['FileSystemId']
        raw = dict(fs)
        try:
            mount_targets = get_mount_targets(region, fs_id)
            for mt in mount_targets:
                mt_id = mt.get('MountTargetId')
                if not mt_id:
                    continue
                try:
                    mt['SecurityGroups'] = get_mount_target_security_groups(region, mt_id)
                except Exception as e:
                    writer.add_error(region=region, source=f'efs_filesystem:{fs_id} (mount target {mt_id} security groups)', message=e)
            raw['_MountTargets'] = mount_targets
        except Exception as e:
            writer.add_error(region=region, source=f'efs_filesystem:{fs_id} (mount targets)', message=e)
            raw['_MountTargets'] = []

        recorded = writer.add_resource(
            resource_type='efs_filesystem',
            region=region,
            resource_id=fs_id,
            resource_name=_fs_name(fs),
            raw=raw,
            tags=fs.get('Tags'),
        )
        if not recorded:
            continue
        vpc_ids = set()
        for mt in raw['_MountTargets']:
            if mt.get('VpcId'):
                vpc_ids.add(mt['VpcId'])
            if mt.get('SubnetId'):
                writer.add_edge(from_type='efs_filesystem', from_id=fs_id, to_type='subnet', to_id=mt['SubnetId'], relationship='in_subnet')
            for sg_id in mt.get('SecurityGroups', []):
                writer.add_edge(from_type='efs_filesystem', from_id=fs_id, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
        for vpc_id in vpc_ids:
            writer.add_edge(from_type='efs_filesystem', from_id=fs_id, to_type='vpc', to_id=vpc_id, relationship='in_vpc')
