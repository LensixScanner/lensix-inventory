"""WorkSpaces gathering — workspaces, workspaces_ip_group, workspaces_directory.

describe_workspaces already returns everything disk-encryption evaluation
needs in one shot; the fetch result becomes the raw `workspace` record
as-is.

IP access groups (describe_ip_groups) and directories
(describe_workspace_directories) are gathered too, as their own resource
types, so IP-access and access-control evaluation can run against
already-gathered data instead of a live call. Each of the three fetches is
isolated in its own try/except so one failing doesn't prevent the other
two from being gathered.
"""

import boto3


def get_workspaces(region):
    client = boto3.client('workspaces', region_name=region)
    items = []
    for page in client.get_paginator('describe_workspaces').paginate():
        items.extend(page.get('Workspaces', []))
    return items


def get_ip_groups(region):
    client = boto3.client('workspaces', region_name=region)
    items = []
    for page in client.get_paginator('describe_ip_groups').paginate():
        items.extend(page.get('Result', []))
    return items


def get_directories(region):
    client = boto3.client('workspaces', region_name=region)
    items = []
    for page in client.get_paginator('describe_workspace_directories').paginate():
        items.extend(page.get('Directories', []))
    return items


def gather(region, writer):
    try:
        workspaces = get_workspaces(region)
    except Exception as e:
        writer.add_error(region=region, source='workspaces', message=e)
        workspaces = []

    for ws in workspaces:
        ws_id = ws['WorkspaceId']
        writer.add_resource(
            resource_type='workspace',
            region=region,
            resource_id=ws_id,
            resource_name=ws.get('ComputerName', ws_id),
            raw=ws,
        )

    try:
        ip_groups = get_ip_groups(region)
    except Exception as e:
        writer.add_error(region=region, source='workspaces:ip_groups', message=e)
        ip_groups = []

    for group in ip_groups:
        group_id = group.get('groupId', '')
        writer.add_resource(
            resource_type='workspaces_ip_group',
            region=region,
            resource_id=group_id,
            resource_name=group.get('groupName', group_id),
            raw=group,
        )

    try:
        directories = get_directories(region)
    except Exception as e:
        writer.add_error(region=region, source='workspaces:directories', message=e)
        directories = []

    for directory in directories:
        directory_id = directory.get('DirectoryId', '')
        writer.add_resource(
            resource_type='workspaces_directory',
            region=region,
            resource_id=directory_id,
            resource_name=directory.get('DirectoryName', directory_id),
            raw=directory,
        )
