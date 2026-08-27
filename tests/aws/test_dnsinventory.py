"""Unit tests for lensix_inventory.aws.dnsinventory — DNS record sets
within each public Route 53 hosted zone."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.dnsinventory as m


def _r53_client(record_sets_by_zone=None, error_zones=None, truncated_zone=None):
    client = MagicMock()
    record_sets_by_zone = record_sets_by_zone or {}
    error_zones = error_zones or set()

    def _list(HostedZoneId, **kwargs):
        if HostedZoneId in error_zones:
            raise RuntimeError('boom')
        if HostedZoneId == truncated_zone and 'StartRecordName' not in kwargs:
            return {'ResourceRecordSets': record_sets_by_zone[HostedZoneId][:1], 'IsTruncated': True,
                    'NextRecordName': 'x', 'NextRecordType': 'A'}
        if HostedZoneId == truncated_zone:
            return {'ResourceRecordSets': record_sets_by_zone[HostedZoneId][1:]}
        return {'ResourceRecordSets': record_sets_by_zone.get(HostedZoneId, [])}
    client.list_resource_record_sets.side_effect = _list
    return client


class TestNormalizeHostname:
    def test_strips_trailing_dot_and_lowercases(self):
        assert m._normalize_hostname('Example.COM.') == 'example.com'

    def test_converts_wildcard_escape_sequence(self):
        assert m._normalize_hostname(r'\052.example.com.') == '*.example.com'


class TestGetRecordSets:
    def test_returns_all_records_for_a_zone(self):
        client = _r53_client(record_sets_by_zone={'/hostedzone/Z1': [{'Name': 'a.example.com.', 'Type': 'A'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            records = m.get_record_sets('/hostedzone/Z1')
        assert records == [{'Name': 'a.example.com.', 'Type': 'A'}]

    def test_paginates_when_truncated(self):
        client = _r53_client(
            record_sets_by_zone={'/hostedzone/Z1': [{'Name': 'a.example.com.', 'Type': 'A'}, {'Name': 'b.example.com.', 'Type': 'A'}]},
            truncated_zone='/hostedzone/Z1',
        )
        with patch.object(m.boto3, 'client', return_value=client):
            records = m.get_record_sets('/hostedzone/Z1')
        assert len(records) == 2


class TestGather:
    def test_adds_one_resource_per_relevant_record(self):
        w = MagicMock()
        zone = ('/hostedzone/Z1', 'example.com.')
        record = {'Name': 'www.example.com.', 'Type': 'A'}
        client = _r53_client(record_sets_by_zone={'/hostedzone/Z1': [record]})
        with patch.object(m.boto3, 'client', return_value=client), \
             patch.object(m, 'get_public_zones', return_value=[zone]):
            m.gather(w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 'route53_record'
        assert kwargs['resource_id'] == 'Z1:www.example.com:A'
        assert kwargs['resource_name'] == 'www.example.com'
        assert kwargs['scope_id'] == 'Z1'

    def test_irrelevant_record_types_are_skipped(self):
        w = MagicMock()
        zone = ('/hostedzone/Z1', 'example.com.')
        record = {'Name': 'example.com.', 'Type': 'MX'}
        client = _r53_client(record_sets_by_zone={'/hostedzone/Z1': [record]})
        with patch.object(m.boto3, 'client', return_value=client), \
             patch.object(m, 'get_public_zones', return_value=[zone]):
            m.gather(w)
        w.add_resource.assert_not_called()

    def test_weighted_records_include_the_set_identifier_in_the_resource_id(self):
        w = MagicMock()
        zone = ('/hostedzone/Z1', 'example.com.')
        record = {'Name': 'www.example.com.', 'Type': 'CNAME', 'SetIdentifier': 'us-east'}
        client = _r53_client(record_sets_by_zone={'/hostedzone/Z1': [record]})
        with patch.object(m.boto3, 'client', return_value=client), \
             patch.object(m, 'get_public_zones', return_value=[zone]):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'Z1:www.example.com:CNAME:us-east'

    def test_a_zone_fetch_failure_does_not_abort_the_others(self):
        w = MagicMock()
        good_record = {'Name': 'good.example.com.', 'Type': 'A'}
        client = _r53_client(
            record_sets_by_zone={'/hostedzone/Z2': [good_record]},
            error_zones={'/hostedzone/Z1'},
        )
        with patch.object(m.boto3, 'client', return_value=client), \
             patch.object(m, 'get_public_zones', return_value=[('/hostedzone/Z1', 'bad.com.'), ('/hostedzone/Z2', 'good.com.')]):
            m.gather(w)
        assert any(c.kwargs['source'] == 'route53_zone:Z1' for c in w.add_error.call_args_list)
        w.add_resource.assert_called_once()

    def test_no_zones_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m, 'get_public_zones', return_value=[]):
            m.gather(w)
        w.add_resource.assert_not_called()
