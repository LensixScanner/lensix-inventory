"""SageMaker gathering — notebook instances.

Notebook instances are listed (list_notebook_instances) then described
individually (describe_notebook_instance) — the describe result becomes
the raw `sagemaker_notebook` record as-is.
"""

import boto3
from botocore.config import Config

_BOTO_CFG = Config(connect_timeout=5, read_timeout=15, retries={'max_attempts': 2})


def get_notebook_instances(region):
    client = boto3.client('sagemaker', region_name=region, config=_BOTO_CFG)
    notebooks = []
    for page in client.get_paginator('list_notebook_instances').paginate():
        notebooks.extend(page.get('NotebookInstances', []))
    return notebooks


def describe_notebook_instance(region, name):
    client = boto3.client('sagemaker', region_name=region, config=_BOTO_CFG)
    return client.describe_notebook_instance(NotebookInstanceName=name)


def get_notebook_tags(region, arn):
    """describe_notebook_instance doesn't include tags — SageMaker's own
    separate, paginated list_tags call, keyed by ARN. Returns [] on
    failure."""
    client = boto3.client('sagemaker', region_name=region, config=_BOTO_CFG)
    tags = []
    try:
        kwargs = {'ResourceArn': arn}
        while True:
            resp = client.list_tags(**kwargs)
            tags.extend(resp.get('Tags', []))
            next_token = resp.get('NextToken')
            if not next_token:
                break
            kwargs['NextToken'] = next_token
    except Exception:
        return []
    return tags


def gather(region, writer):
    for nb in get_notebook_instances(region):
        name = nb['NotebookInstanceName']
        arn = nb['NotebookInstanceArn']
        try:
            detail = describe_notebook_instance(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'sagemaker_notebook:{arn}', message=e)
            continue
        writer.add_resource(
            resource_type='sagemaker_notebook',
            region=region,
            resource_id=arn,
            resource_name=name,
            raw=detail,
            tags=get_notebook_tags(region, arn),
        )
