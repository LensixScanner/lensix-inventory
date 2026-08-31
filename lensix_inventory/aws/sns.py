"""SNS gathering — topics.

Topics are listed (list_topics) then their attributes fetched individually
(get_topic_attributes) — those attributes become the raw `sns_topic`
record as-is.

Cross-account/public-policy evaluation is finding-time policy-document
parsing over the same `Policy` attribute already present in the raw record
— no additional gathering needed; Lensix parses the uploaded policy JSON
server-side.
"""

import boto3


def get_topics(region):
    sns = boto3.client('sns', region_name=region)
    topics = []
    for page in sns.get_paginator('list_topics').paginate():
        topics.extend(page.get('Topics', []))
    return topics


def get_topic_attributes(region, arn):
    sns = boto3.client('sns', region_name=region)
    return sns.get_topic_attributes(TopicArn=arn)['Attributes']


def get_topic_tags(region, arn):
    """SNS tags aren't part of get_topic_attributes' response — its own
    separate, unpaginated call (SNS caps a topic at 50 tags, so no
    NextToken here, unlike DynamoDB/DAX's own tag calls). Returns [] on
    failure."""
    sns = boto3.client('sns', region_name=region)
    try:
        return sns.list_tags_for_resource(ResourceArn=arn).get('Tags', [])
    except Exception:
        return []


def gather(region, writer):
    for topic in get_topics(region):
        arn = topic['TopicArn']
        name = arn.split(':')[-1]
        try:
            attrs = get_topic_attributes(region, arn)
        except Exception as e:
            writer.add_error(region=region, source=f'sns_topic:{arn}', message=e)
            continue
        writer.add_resource(
            resource_type='sns_topic',
            region=region,
            resource_id=arn,
            resource_name=name,
            raw=attrs,
            tags=get_topic_tags(region, arn),
        )
