"""WorkSpaces gathering — workspaces.

describe_workspaces already returns everything disk-encryption evaluation
needs in one shot; the fetch result becomes the raw `workspace` record
as-is.

IP access groups and directories are used for IP-access and access-control
evaluation, but neither has a persisted resource shape of its own — they
only ever feed a finding, never a standalone resource — so there's nothing
to gather for them here.
"""

import boto3
import botocore.exceptions


def get_workspaces(region):
    client = boto3.client('workspaces', region_name=region)
    items = []
    for page in client.get_paginator('describe_workspaces').paginate():
        items.extend(page.get('Workspaces', []))
    return items


def gather(region, writer):
    try:
        workspaces = get_workspaces(region)
    except botocore.exceptions.ClientError as e:
        writer.add_error(region=region, source='workspaces', message=e)
        return
    except Exception as e:
        writer.add_error(region=region, source='workspaces', message=e)
        return

    for ws in workspaces:
        ws_id = ws['WorkspaceId']
        writer.add_resource(
            resource_type='workspace',
            region=region,
            resource_id=ws_id,
            resource_name=ws.get('ComputerName', ws_id),
            raw=ws,
        )
