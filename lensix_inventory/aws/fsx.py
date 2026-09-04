"""FSx gathering — one raw record per file system (Windows File Server,
Lustre, NetApp ONTAP, OpenZFS — one shared `fsx` client/API across all
four). Mirrors efs.py's own shape exactly: describe_file_systems already
returns everything needed (StorageCapacity, StorageType, encryption)
in one paginated call, no extra fan-out — evaluation is left server-side.

StorageCapacity (GiB) is a plain describe field, not a CloudWatch metric —
same "already in the raw record" collection philosophy as everything
else here, consulted here specifically because it fills a real, prior
gap: FSx wasn't gathered by this tool at all before this module existed.
Sub-filesystem volumes (ONTAP/OpenZFS's own describe_volumes, each with
its own storage capacity) are deliberately out of scope for now, same as
efs.py not descending into individual EFS access points — one resource
per file system is enough for a first pass; volumes can be added later
without changing this shape.
"""

import boto3


def get_file_systems(region):
    fsx = boto3.client('fsx', region_name=region)
    file_systems = []
    for page in fsx.get_paginator('describe_file_systems').paginate():
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
            resource_type='fsx_file_system',
            region=region,
            resource_id=fs.get('ResourceARN') or fs['FileSystemId'],
            resource_name=_fs_name(fs),
            scope_id=fs.get('VpcId'),
            raw=fs,
            tags=fs.get('Tags'),
        )
