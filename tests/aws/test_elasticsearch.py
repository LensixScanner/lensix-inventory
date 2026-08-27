"""Unit tests for lensix_inventory.aws.elasticsearch — Elasticsearch/OpenSearch domains."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.elasticsearch as m


def _es_client(names, detail_by_name=None, detail_error_names=None):
    client = MagicMock()
    client.list_domain_names.return_value = {'DomainNames': [{'DomainName': n} for n in names]}
    detail_by_name = detail_by_name or {}
    detail_error_names = detail_error_names or set()

    def _describe(DomainName):
        if DomainName in detail_error_names:
            raise RuntimeError('boom')
        return {'DomainStatus': detail_by_name[DomainName]}
    client.describe_elasticsearch_domain.side_effect = _describe
    return client


class TestGather:
    def test_adds_one_resource_per_domain(self):
        w = MagicMock()
        domain = {'ARN': 'arn:aws:es:us-east-1:1:domain/logs', 'VPCOptions': {'VPCId': 'vpc-1'}}
        client = _es_client(['logs'], detail_by_name={'logs': domain})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='elasticsearch_domain', region='us-east-1',
            resource_id='arn:aws:es:us-east-1:1:domain/logs', resource_name='logs',
            scope_id='vpc-1', raw=domain,
        )

    def test_no_vpc_options_means_no_scope_id(self):
        w = MagicMock()
        domain = {'ARN': 'arn:public'}
        client = _es_client(['logs'], detail_by_name={'logs': domain})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['scope_id'] is None

    def test_a_describe_failure_for_one_domain_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'ARN': 'arn:good'}
        client = _es_client(['bad', 'good'], detail_by_name={'good': good}, detail_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'elasticsearch_domain:bad'
        w.add_resource.assert_called_once()

    def test_no_domains_gathers_nothing(self):
        w = MagicMock()
        client = _es_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
