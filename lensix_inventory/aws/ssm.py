"""SSM gathering — Parameter Store parameters.

This module covers only Parameter Store (describe_parameters), producing
`ssm_parameter` records. Secrets Manager secrets are deliberately not
re-scanned here — that would write the same `secretsmanager_secret`
resource twice into the inventory file, once from this tool's
secretsmanager.py and once here.

Note: parameter *values* are never fetched (list/describe_parameters only
returns metadata, not GetParameter/GetParameters values), so there's
nothing here that needs the secret-redaction treatment used elsewhere in
this tool (see common/secrets.py) — the "sensitive-sounding name but
stored as plain String" check is itself just a name-pattern match over
metadata already present in the raw record, no value ever touched.
"""

import boto3


def get_parameters(region):
    client = boto3.client('ssm', region_name=region)
    params = []
    for page in client.get_paginator('describe_parameters').paginate():
        params.extend(page.get('Parameters', []))
    return params


def get_parameter_tags(region, name):
    """describe_parameters doesn't include tags — SSM's own separate,
    paginated list_tags_for_resource call, keyed by parameter name (not
    an ARN). Returns [] on failure."""
    client = boto3.client('ssm', region_name=region)
    tags = []
    try:
        kwargs = {'ResourceType': 'Parameter', 'ResourceId': name}
        while True:
            resp = client.list_tags_for_resource(**kwargs)
            tags.extend(resp.get('TagList', []))
            next_token = resp.get('NextToken')
            if not next_token:
                break
            kwargs['NextToken'] = next_token
    except Exception:
        return []
    return tags


def gather(region, writer):
    for param in get_parameters(region):
        name = param.get('Name', '')
        writer.add_resource(
            resource_type='ssm_parameter',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=param,
            tags=get_parameter_tags(region, name),
        )
