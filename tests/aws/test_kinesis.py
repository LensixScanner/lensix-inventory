"""Unit tests for lensix_inventory.aws.kinesis — data streams and Firehose delivery streams."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.kinesis as m


def _client(stream_names=None, summary_by_name=None, summary_error_names=None,
            streams_raise=False, firehose_names=None, firehose_detail_by_name=None,
            firehose_error_names=None, firehose_raise=False, incoming_records=None,
            stream_tags_by_name=None, firehose_tags_by_name=None):
    # list_tags_for_stream/list_tags_for_delivery_stream both need an
    # explicit, real {'Tags': [...], 'HasMoreTags': False} response — an
    # unconfigured MagicMock's own .get('HasMoreTags') is always truthy,
    # which would make get_stream_tags()/get_firehose_tags()'s own
    # pagination loop spin forever (same trap noted in test_dynamodb.py's
    # own _client()).
    client = MagicMock()
    stream_tags_by_name = stream_tags_by_name or {}
    firehose_tags_by_name = firehose_tags_by_name or {}
    client.list_tags_for_stream.side_effect = lambda StreamName, **kw: {
        'Tags': stream_tags_by_name.get(StreamName, []), 'HasMoreTags': False}
    client.list_tags_for_delivery_stream.side_effect = lambda DeliveryStreamName, **kw: {
        'Tags': firehose_tags_by_name.get(DeliveryStreamName, []), 'HasMoreTags': False}
    if incoming_records is not None:
        client.get_metric_statistics.return_value = {'Datapoints': incoming_records}
    stream_names = stream_names or []
    summary_by_name = summary_by_name or {}
    summary_error_names = summary_error_names or set()

    if streams_raise:
        client.list_streams.side_effect = RuntimeError('boom')
    else:
        client.list_streams.return_value = {'StreamNames': stream_names, 'HasMoreStreams': False}

    def _summary(StreamName):
        if StreamName in summary_error_names:
            raise RuntimeError('boom')
        return {'StreamDescriptionSummary': summary_by_name[StreamName]}
    client.describe_stream_summary.side_effect = _summary

    firehose_names = firehose_names or []
    firehose_detail_by_name = firehose_detail_by_name or {}
    firehose_error_names = firehose_error_names or set()

    if firehose_raise:
        client.list_delivery_streams.side_effect = RuntimeError('boom')
    else:
        client.list_delivery_streams.return_value = {'DeliveryStreamNames': firehose_names, 'HasMoreDeliveryStreams': False}

    def _fh_detail(DeliveryStreamName):
        if DeliveryStreamName in firehose_error_names:
            raise RuntimeError('boom')
        return {'DeliveryStreamDescription': firehose_detail_by_name[DeliveryStreamName]}
    client.describe_delivery_stream.side_effect = _fh_detail
    return client


class TestGather:
    def test_adds_one_resource_per_data_stream(self):
        w = MagicMock()
        summary = {'StreamARN': 'arn:1', 'StreamStatus': 'ACTIVE'}
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary}, incoming_records=[])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_stream'].kwargs['resource_id'] == 's1'
        raw = calls['kinesis_stream'].kwargs['raw']
        assert raw['StreamARN'] == 'arn:1' and raw['StreamStatus'] == 'ACTIVE'

    def test_an_active_stream_gets_incoming_records_datapoints_merged_in(self):
        w = MagicMock()
        summary = {'StreamStatus': 'ACTIVE'}
        datapoints = [{'Sum': 0}] * 7
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary}, incoming_records=datapoints)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_stream'].kwargs['raw']['_IncomingRecordsDatapoints'] == datapoints

    def test_a_non_active_stream_gets_no_incoming_records_fetch_at_all(self):
        w = MagicMock()
        summary = {'StreamStatus': 'CREATING'}
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary})
        with patch.object(m, 'get_incoming_records_7d') as get_records, \
             patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        get_records.assert_not_called()
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_stream'].kwargs['raw']['_IncomingRecordsDatapoints'] is None

    def test_an_incoming_records_fetch_failure_records_none_and_an_error(self):
        w = MagicMock()
        summary = {'StreamStatus': 'ACTIVE'}
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary})
        with patch.object(m, 'get_incoming_records_7d', side_effect=RuntimeError('boom')), \
             patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'kinesis (incoming records:s1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_stream'].kwargs['raw']['_IncomingRecordsDatapoints'] is None

    def test_stream_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        summary = {'StreamStatus': 'ACTIVE'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary}, incoming_records=[],
                          stream_tags_by_name={'s1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_stream'].kwargs['tags'] == tags

    def test_the_original_summary_dict_is_not_mutated(self):
        w = MagicMock()
        summary = {'StreamStatus': 'ACTIVE'}
        client = _client(stream_names=['s1'], summary_by_name={'s1': summary}, incoming_records=[])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_IncomingRecordsDatapoints' not in summary

    def test_a_stream_summary_failure_does_not_abort_the_others(self):
        w = MagicMock()
        client = _client(stream_names=['bad', 'good'], summary_by_name={'good': {}}, summary_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'kinesis_stream:bad' for c in w.add_error.call_args_list)
        stream_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'kinesis_stream']
        assert len(stream_calls) == 1

    def test_a_data_streams_service_failure_does_not_prevent_firehose_from_being_gathered(self):
        w = MagicMock()
        client = _client(streams_raise=True, firehose_names=['fh1'], firehose_detail_by_name={'fh1': {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'kinesis (data streams)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'kinesis_firehose_stream' in calls

    def test_adds_one_resource_per_firehose_stream(self):
        w = MagicMock()
        detail = {'DeliveryStreamStatus': 'ACTIVE'}
        client = _client(firehose_names=['fh1'], firehose_detail_by_name={'fh1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_firehose_stream'].kwargs['resource_id'] == 'fh1'

    def test_firehose_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        detail = {'DeliveryStreamStatus': 'ACTIVE'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(firehose_names=['fh1'], firehose_detail_by_name={'fh1': detail},
                          firehose_tags_by_name={'fh1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['kinesis_firehose_stream'].kwargs['tags'] == tags

    def test_a_firehose_detail_failure_does_not_abort_the_others(self):
        w = MagicMock()
        client = _client(firehose_names=['bad', 'good'], firehose_detail_by_name={'good': {}}, firehose_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'kinesis_firehose_stream:bad' for c in w.add_error.call_args_list)
        firehose_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'kinesis_firehose_stream']
        assert len(firehose_calls) == 1

    def test_a_firehose_service_failure_does_not_prevent_data_streams_from_being_gathered(self):
        w = MagicMock()
        client = _client(stream_names=['s1'], summary_by_name={'s1': {}}, firehose_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'kinesis (firehose streams)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'kinesis_stream' in calls


class TestGetStreamTags:
    def test_paginates_via_exclusive_start_tag_key(self):
        client = MagicMock()
        client.list_tags_for_stream.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'HasMoreTags': True},
            {'Tags': [{'Key': 'b', 'Value': '2'}], 'HasMoreTags': False},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            tags = m.get_stream_tags('us-east-1', 's1')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]

    def test_a_failure_returns_an_empty_list(self):
        client = MagicMock()
        client.list_tags_for_stream.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_stream_tags('us-east-1', 's1') == []


class TestGetFirehoseTags:
    def test_paginates_via_exclusive_start_tag_key(self):
        client = MagicMock()
        client.list_tags_for_delivery_stream.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'HasMoreTags': True},
            {'Tags': [{'Key': 'b', 'Value': '2'}], 'HasMoreTags': False},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            tags = m.get_firehose_tags('us-east-1', 'fh1')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]

    def test_a_failure_returns_an_empty_list(self):
        client = MagicMock()
        client.list_tags_for_delivery_stream.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_firehose_tags('us-east-1', 'fh1') == []


class TestGetIncomingRecords7d:
    def test_returns_the_datapoints_scoped_to_this_stream(self):
        datapoints = [{'Sum': 1.0}, {'Sum': 0.0}]
        client = MagicMock()
        client.get_metric_statistics.return_value = {'Datapoints': datapoints}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_incoming_records_7d('us-east-1', 's1') == datapoints
        call_kwargs = client.get_metric_statistics.call_args.kwargs
        assert call_kwargs['Dimensions'] == [{'Name': 'StreamName', 'Value': 's1'}]
        assert call_kwargs['MetricName'] == 'IncomingRecords'
