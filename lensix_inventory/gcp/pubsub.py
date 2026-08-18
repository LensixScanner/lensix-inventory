"""Pub/Sub gathering — topics and subscriptions.

Both list calls already return everything needed for missing-CMEK (topics)
and missing-dead-letter-topic (subscriptions) evaluation in one shot
(`kmsKeyName` on topics, `deadLetterPolicy` on subscriptions) — no fan-out
sub-API calls needed. That evaluation itself is left server-side.
"""

from googleapiclient import discovery


def get_topics(pubsub, project_id):
    topics = []
    project_path = f'projects/{project_id}'
    request = pubsub.projects().topics().list(project=project_path)
    while request is not None:
        resp = request.execute()
        topics.extend(resp.get('topics', []))
        page_token = resp.get('nextPageToken')
        request = pubsub.projects().topics().list(project=project_path, pageToken=page_token) if page_token else None
    return topics


def get_subscriptions(pubsub, project_id):
    subs = []
    project_path = f'projects/{project_id}'
    request = pubsub.projects().subscriptions().list(project=project_path)
    while request is not None:
        resp = request.execute()
        subs.extend(resp.get('subscriptions', []))
        page_token = resp.get('nextPageToken')
        request = pubsub.projects().subscriptions().list(project=project_path, pageToken=page_token) if page_token else None
    return subs


def gather(project_id, credentials, writer):
    pubsub = discovery.build('pubsub', 'v1', credentials=credentials)

    try:
        for topic in get_topics(pubsub, project_id):
            topic_name = topic.get('name', '')
            writer.add_resource(
                resource_type='pubsub_topic',
                region='global',
                resource_id=topic_name,
                resource_name=topic_name.split('/')[-1],
                raw=topic,
            )
    except Exception as e:
        writer.add_error(region='global', source='pubsub_topic', message=e)

    try:
        for sub in get_subscriptions(pubsub, project_id):
            sub_name = sub.get('name', '')
            writer.add_resource(
                resource_type='pubsub_subscription',
                region='global',
                resource_id=sub_name,
                resource_name=sub_name.split('/')[-1],
                raw=sub,
            )
    except Exception as e:
        writer.add_error(region='global', source='pubsub_subscription', message=e)
