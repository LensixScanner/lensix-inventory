"""Kinesis gathering — data streams and Firehose delivery streams.

Data streams are listed by name (list_streams) then described individually
(describe_stream_summary) for the full record; Firehose delivery streams
the same way (list_delivery_streams + describe_delivery_stream).

Incoming-record volume over a 7-day CloudWatch lookback is intentionally
not gathered — it's time-windowed telemetry, not inventory, and a static
snapshot can't represent "no records in 7 days" in a way that stays
meaningful after upload.
"""

import boto3


def get_streams(region):
    kin = boto3.client('kinesis', region_name=region)
    names = []
    kwargs = {}
    while True:
        resp = kin.list_streams(**kwargs)
        names.extend(resp.get('StreamNames', []))
        if not resp.get('HasMoreStreams'):
            break
        kwargs['ExclusiveStartStreamName'] = names[-1]
    return names


def get_stream_summary(region, name):
    kin = boto3.client('kinesis', region_name=region)
    resp = kin.describe_stream_summary(StreamName=name)
    return resp['StreamDescriptionSummary']


def get_firehose_streams(region):
    fh = boto3.client('firehose', region_name=region)
    names = []
    kwargs = {}
    while True:
        resp = fh.list_delivery_streams(**kwargs)
        names.extend(resp.get('DeliveryStreamNames', []))
        if not resp.get('HasMoreDeliveryStreams'):
            break
        kwargs['ExclusiveStartDeliveryStreamName'] = names[-1]
    return names


def get_firehose_detail(region, name):
    fh = boto3.client('firehose', region_name=region)
    return fh.describe_delivery_stream(DeliveryStreamName=name)['DeliveryStreamDescription']


def gather(region, writer):
    for name in get_streams(region):
        try:
            summary = get_stream_summary(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'kinesis_stream:{name}', message=e)
            continue
        writer.add_resource(
            resource_type='kinesis_stream',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=summary,
        )

    for name in get_firehose_streams(region):
        try:
            detail = get_firehose_detail(region, name)
        except Exception as e:
            writer.add_error(region=region, source=f'kinesis_firehose_stream:{name}', message=e)
            continue
        writer.add_resource(
            resource_type='kinesis_firehose_stream',
            region=region,
            resource_id=name,
            resource_name=name,
            raw=detail,
        )
