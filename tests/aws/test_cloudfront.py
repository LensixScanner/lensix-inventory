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


class TestExtractBucketName:
    def test_matches_the_regional_form(self):
        assert m._extract_bucket_name('mybucket.s3.us-east-1.amazonaws.com') == 'mybucket'

    def test_matches_the_legacy_global_form(self):
        assert m._extract_bucket_name('mybucket.s3.amazonaws.com') == 'mybucket'

    def test_matches_the_website_hyphen_form(self):
        assert m._extract_bucket_name('mybucket.s3-website-us-east-1.amazonaws.com') == 'mybucket'

    def test_no_match_for_a_non_s3_domain(self):
        assert m._extract_bucket_name('example.com') is None


class TestIsS3Origin:
    def test_true_for_an_s3_origin_config(self):
        assert m._is_s3_origin({'S3OriginConfig': {}}) is True

    def test_false_for_a_custom_origin_config(self):
        assert m._is_s3_origin({'CustomOriginConfig': {}}) is False

    def test_false_when_both_are_somehow_present(self):
        assert m._is_s3_origin({'S3OriginConfig': {}, 'CustomOriginConfig': {}}) is False


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

    def test_an_s3_origin_produces_a_routes_to_edge(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        config = {'Origins': {'Items': [{'DomainName': 'mybucket.s3.amazonaws.com', 'S3OriginConfig': {}}]}}
        client = _cf_client([dist], config_by_id={'E123': config})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_called_once_with(
            from_type='cloudfront_distribution', from_id='E123',
            to_type='s3_bucket', to_id='mybucket', relationship='routes_to',
        )

    def test_a_custom_origin_produces_no_edge(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        config = {'Origins': {'Items': [{'DomainName': 'example.com', 'CustomOriginConfig': {}}]}}
        client = _cf_client([dist], config_by_id={'E123': config})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_no_origins_produces_no_edge(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        client = _cf_client([dist], config_by_id={'E123': {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_a_config_fetch_failure_produces_no_edge(self):
        w = MagicMock()
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        client = _cf_client([dist], config_error_ids={'E123'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_distribution_produces_no_edge(self):
        w = MagicMock()
        w.add_resource.return_value = False
        dist = {'Id': 'E123', 'DomainName': 'd123.cloudfront.net'}
        config = {'Origins': {'Items': [{'DomainName': 'mybucket.s3.amazonaws.com', 'S3OriginConfig': {}}]}}
        client = _cf_client([dist], config_by_id={'E123': config})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_edge.assert_not_called()

    def test_no_distributions_gathers_nothing(self):
        w = MagicMock()
        client = _cf_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather(w)
        w.add_resource.assert_not_called()
