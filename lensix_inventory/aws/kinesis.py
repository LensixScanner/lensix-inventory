"""Kinesis gathering — data streams and Firehose delivery streams.

Data streams are listed by name (list_streams) then described individually
(describe_stream_summary) for the full record; Firehose delivery streams
the same way (list_delivery_streams + describe_delivery_stream).

Incoming-record volume over a 7-day CloudWatch lookback IS gathered now
(get_incoming_records_7d), merged into each ACTIVE stream's
raw['_IncomingRecordsDatapoints'] — a point-in-time snapshot of a rolling
window, same "reasonably fresh, not live" treatment ec2.py's own
_Metrics gets (see its docstring). Only fetched for ACTIVE streams —
kinesis_abandoned's own status gate means a non-ACTIVE stream never reads
this field anyway.
"""

from datetime import datetime, timedelta, timezone

import boto3

INCOMING_RECORDS_LOOKBACK_DAYS = 7


def get_incoming_records_7d(region, stream_name):
    cw   = boto3.client('cloudwatch', region_name=region)
    end  = datetime.now(timezone.utc)
    resp = cw.get_metric_statistics(
        Namespace='AWS/Kinesis',
        MetricName='IncomingRecords',
        Dimensions=[{'Name': 'StreamName', 'Value': stream_name}],
        StartTime=end - timedelta(days=INCOMING_RECORDS_LOOKBACK_DAYS),
        EndTime=end,
        Period=INCOMING_RECORDS_LOOKBACK_DAYS * 86400,
        Statistics=['Sum'],
    )
    return resp['Datapoints']


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
    # Data streams and Firehose delivery streams are independent list
    # calls — isolate them so a failure fetching one doesn't prevent the
    # other from being gathered.
    try:
        for name in get_streams(region):
            try:
                summary = get_stream_summary(region, name)
            except Exception as e:
                writer.add_error(region=region, source=f'kinesis_stream:{name}', message=e)
                continue
            raw = dict(summary)
            if summary.get('StreamStatus') == 'ACTIVE':
                try:
                    raw['_IncomingRecordsDatapoints'] = get_incoming_records_7d(region, name)
                except Exception as e:
                    raw['_IncomingRecordsDatapoints'] = None
                    writer.add_error(region=region, source=f'kinesis (incoming records:{name})', message=e)
            else:
                raw['_IncomingRecordsDatapoints'] = None
            writer.add_resource(
                resource_type='kinesis_stream',
                region=region,
                resource_id=name,
                resource_name=name,
                raw=raw,
            )
    except Exception as e:
        writer.add_error(region=region, source='kinesis (data streams)', message=e)

    try:
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
    except Exception as e:
        writer.add_error(region=region, source='kinesis (firehose streams)', message=e)
