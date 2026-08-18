"""SQS gathering — queues.

Queues are listed (list_queues) then their attributes fetched individually
(get_queue_attributes with AttributeNames=['All']) — those attributes
become the raw `sqs_queue` record as-is. A queue is only recorded once its
attributes have been successfully fetched (needed to resolve the
QueueArn).

Policy-document evaluation (wildcard principals, cross-account access) is
finding-time parsing over the same `Policy` attribute already present in
the raw record — no additional gathering needed.
"""

import boto3


def get_queues(region):
    sqs = boto3.client('sqs', region_name=region)
    urls = []
    for page in sqs.get_paginator('list_queues').paginate():
        urls.extend(page.get('QueueUrls', []))
    return urls


def get_queue_attributes(region, url):
    sqs = boto3.client('sqs', region_name=region)
    return sqs.get_queue_attributes(QueueUrl=url, AttributeNames=['All'])['Attributes']


def gather(region, writer):
    for url in get_queues(region):
        name = url.split('/')[-1]
        try:
            attrs = get_queue_attributes(region, url)
        except Exception as e:
            writer.add_error(region=region, source=f'sqs_queue:{url}', message=e)
            continue

        writer.add_resource(
            resource_type='sqs_queue',
            region=region,
            resource_id=attrs.get('QueueArn', url),
            resource_name=name,
            raw=attrs,
        )
