"""EFS gathering — one raw record per file system.

`get_file_systems` (describe_file_systems) already returns everything
needed for encryption and customer-managed-key evaluation (Encrypted,
KmsKeyId) in one call — no extra fan-out needed. That evaluation itself is
left server-side.
"""

import boto3


def get_file_systems(region):
    efs = boto3.client('efs', region_name=region)
    file_systems = []
    for page in efs.get_paginator('describe_file_systems').paginate():
        file_systems.extend(page['FileSystems'])
    return file_systems


def _fs_name(fs):
    for tag in fs.get('Tags', []):
        if tag['Key'] == 'Name':
            return tag['Value']
    return fs['FileSystemId']


def gather(region, writer):
    for fs in get_file_systems(region):
        writer.add_resource(
            resource_type='efs_filesystem',
            region=region,
            resource_id=fs['FileSystemId'],
            resource_name=_fs_name(fs),
            raw=fs,
            tags=fs.get('Tags'),
        )
