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

        arn = broker.get('BrokerArn', broker_id)
        recorded = writer.add_resource(
            resource_type='mq_broker',
            region=region,
            resource_id=arn,
            resource_name=broker_name,
            raw=broker,
            # MQ's own Tags field is already a flat {key: value} map.
            tags=broker.get('Tags'),
        )
        if not recorded:
            continue
        # No VpcId is ever exposed directly; subnet edges reach it
        # transitively via vpc.py's own subnet -> vpc edge.
        for subnet_id in broker.get('SubnetIds', []):
            writer.add_edge(from_type='mq_broker', from_id=arn, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
        for sg_id in broker.get('SecurityGroups', []):
            writer.add_edge(from_type='mq_broker', from_id=arn, to_type='security_group', to_id=sg_id, relationship='member_of_sg')
