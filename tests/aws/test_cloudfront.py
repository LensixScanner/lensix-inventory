"""Unit tests for lensix_inventory.aws.cloudfront — CloudFront distributions."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.cloudfront as m


def _cf_client(dists, config_by_id=None, config_error_ids=None, tags_by_arn=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'DistributionList': {'Items': dists}}]
    config_by_id = config_by_id or {}
    config_error_ids = config_error_ids or set()

    def _get_dist(Id):
        if Id in config_error_ids:
            raise RuntimeError('boom')
        return {'Distribution': {'DistributionConfig': config_by_id[Id]}}
    client.get_distribution.side_effect = _get_dist
    tags_by_arn = tags_by_arn or {}
    client.list_tags_for_resource.side_effect = lambda Resource: {'Tags': {'Items': tags_by_arn.get(Resource, [])}}
    return client


class TestGather:
    def test_adds_one_resource_with_the_config_merged_in(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        config = {'Enabled': True}
        client = _cf_client([dist], config_by_id={'E123': config})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_type'] == 'cloudfront_distribution'
        assert kwargs['region'] == 'global'
        assert kwargs['resource_id'] == 'E123'
        assert kwargs['resource_name'] == 'd123.cloudfront.net'
        assert kwargs['raw']['_DistributionConfig'] == config
        assert kwargs['raw']['Id'] == 'E123'

    def test_distribution_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net', 'ARN': 'arn:aws:cloudfront::1:distribution/E123'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _cf_client([dist], config_by_id={'E123': {}}, tags_by_arn={'arn:aws:cloudfront::1:distribution/E123': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_falls_back_to_the_id_when_domain_name_missing(self):
        w = MagicMock()
        dist = {'Id': 'E123'}
        client = _cf_client([dist], config_by_id={'E123': {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'E123'

    def test_a_config_fetch_failure_still_records_the_distribution(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        client = _cf_client([dist], config_error_ids={'E123'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'cloudfront_distribution:E123'
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert '_DistributionConfig' not in kwargs['raw']

    def test_the_original_distribution_dict_is_not_mutated(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        client = _cf_client([dist], config_by_id={'E123': {'Enabled': True}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        assert '_DistributionConfig' not in dist

    def test_no_distributions_gathers_nothing(self):
        w = MagicMock()
        client = _cf_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_not_called()
