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
            recorded = writer.add_resource(
                resource_type='pubsub_topic',
                region='global',
                resource_id=topic_name,
                resource_name=topic_name.split('/')[-1],
                raw=topic,
                tags=topic.get('labels'),
            )
            if recorded:
                # kmsKeyName is always the fully-qualified KMS resource
                # name (confirmed against the real discovery document
                # schema, same convention as every other GCP CMEK field)
                # — matches kms_crypto_key's own resource_id exactly, no
                # name-based resolution needed.
                kms_key = topic.get('kmsKeyName')
                if kms_key:
                    writer.add_edge(from_type='pubsub_topic', from_id=topic_name, to_type='kms_crypto_key', to_id=kms_key, relationship='uses_cmek')
    except Exception as e:
        writer.add_error(region='global', source='pubsub_topic', message=e)

    try:
        for sub in get_subscriptions(pubsub, project_id):
            sub_name = sub.get('name', '')
            recorded = writer.add_resource(
                resource_type='pubsub_subscription',
                region='global',
                resource_id=sub_name,
                resource_name=sub_name.split('/')[-1],
                raw=sub,
                tags=sub.get('labels'),
            )
            if recorded:
                # Subscription.topic is always the fully-qualified
                # 'projects/{project}/topics/{topic}' form (confirmed
                # against the real discovery document schema) — matches
                # pubsub_topic's own resource_id exactly, except for the
                # documented '_deleted-topic_' sentinel when the topic no
                # longer exists, which is never a real resource_id and
                # must not become a dangling edge.
                topic_ref = sub.get('topic')
                if topic_ref and topic_ref != '_deleted-topic_':
                    writer.add_edge(from_type='pubsub_subscription', from_id=sub_name, to_type='pubsub_topic', to_id=topic_ref, relationship='subscribes_to')
    except Exception as e:
        writer.add_error(region='global', source='pubsub_subscription', message=e)
