"""Amazon MQ gathering — brokers.

Brokers are listed (list_brokers) then described individually
(describe_broker) — the describe_broker result becomes the raw
`mq_broker` record as-is.
"""

import boto3


def get_brokers(region):
    mq = boto3.client('mq', region_name=region)
    brokers = []
    kwargs = {}
    while True:
        resp = mq.list_brokers(**kwargs)
        brokers.extend(resp.get('BrokerSummaries', []))
        next_token = resp.get('NextToken')
        if not next_token:
            break
        kwargs['NextToken'] = next_token
    return brokers


def describe_broker(region, broker_id):
    mq = boto3.client('mq', region_name=region)
    return mq.describe_broker(BrokerId=broker_id)


def gather(region, writer):
    for summary in get_brokers(region):
        broker_id = summary['BrokerId']
        broker_name = summary['BrokerName']
        try:
            broker = describe_broker(region, broker_id)
        except Exception as e:
            writer.add_error(region=region, source=f'mq_broker:{broker_id}', message=e)
            continue

        writer.add_resource(
            resource_type='mq_broker',
            region=region,
            resource_id=broker.get('BrokerArn', broker_id),
            resource_name=broker_name,
            raw=broker,
        )
